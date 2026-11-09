"""audit — check that no interview task was silently lost in processing.

    tasknet audit                    # every occupation under results/processing/
    tasknet audit lawyers

No model calls, and no judgement of whether a decision was right: only that every task reached the next file
or was removed by the filter. Three checks per occupation, each replaying the stage's own code over its cached
results:

  A  sessions → 1_ingested      every selectedTasks entry (after ingest.explode) is in 1_ingested.csv
  B  1_ingested → 2_normalized  every piece of every row (after the split) is either in 2_normalized.csv
                                (traced by original_task_name) or removed by the filter; no extra rows
  C  2_normalized → 3_deduped   re-running dedup gives exactly 3_deduped.csv

Check B reads the caches for the currently configured models and prompts. Many B mismatches after changing
one of them mean 2_normalized.csv is out of date (re-run normalize), not that tasks were lost.
"""
from __future__ import annotations

from collections import Counter

from .. import workspace
from ..atoms import Atoms
from . import dedup as _dedup
from . import ingest
from .normalize import _Pass, _hash, coalesce, is_target, kept, valid_split, wants_split

MAX_EXAMPLES = 5   # examples printed per kind of failure, per occupation


def session_tasks(data_dir) -> list[tuple[str, str]]:
    """Every task ingest should have read from `data_dir`. -> [(session_id, task)]"""
    return [(r["session_id"], r["task_name"]) for p in ingest.sessions(data_dir) for r in ingest.session_rows(p)]


def _split_row(row, split_cache) -> list[str] | None:
    """The tasks one ingested row becomes, by the split cache (as normalize applies it). -> pieces, or None if whole"""
    pieces = split_cache.get(_hash(row["task"])) if wants_split(row) else None
    if not pieces or not valid_split(pieces):
        return None
    merged = coalesce(row["task"], pieces)
    return None if (len(merged) == 1 and merged[0] == row["task"]) else merged


def live_pieces(ctx, rows) -> list[dict]:
    """Every task the filter saw (after the split), with whether it was kept, from the current caches.
    -> [{source, origin, text, kept}]; `origin` is the ingested row's text."""
    split = _Pass(ctx, "split", "split", ctx.model("normalize"), ctx.effort("normalize"), 4096).cache
    filt = _Pass(ctx, "filter", "filter", ctx.model("filter"), ctx.effort("filter"), 16000).cache
    out = []
    for r in rows:
        if not is_target(r):
            out.append({"source": r["source"], "origin": r["task"], "text": r["task"], "kept": True})
            continue
        for p in _split_row(r, split) or [r["task"]]:
            out.append({"source": r["source"], "origin": r["task"], "text": p, "kept": kept(filt.get(_hash(p)))})
    return out


def audit_occ(occ: str, ctx) -> tuple[list[str], dict]:
    """Run checks A, B and C for one occupation. -> (failures, counts); each failure is one printable line."""
    proc = workspace.processing_dir(occ)
    fails: list[str] = []
    stats: dict = {"occ": occ}
    read = lambda name: Atoms.read(proc / name).rows if (proc / name).exists() else None
    ingested = read("1_ingested.csv") or []
    normalized, deduped = read("2_normalized.csv"), read("3_deduped.csv")

    # ── A: every session task reached 1_ingested.csv ─────────────────────────────
    sessions = workspace.interviews_dir() / occ
    have = {(r["source"], r["task"]) for r in ingested}
    if not sessions.is_dir():
        fails.append(f"A: no session dir found ({sessions}) — cannot verify ingest")
    else:
        expected = session_tasks(sessions)
        missing = [k for k in expected if k not in have]
        stats["sessions"] = len({s for s, _ in expected})
        stats["session_tasks"] = len(expected)
        for sid, t in missing[:MAX_EXAMPLES]:
            fails.append(f"A: never ingested  [{sid[:8]}] {t[:80]!r}")
        if len(missing) > MAX_EXAMPLES:
            fails.append(f"A: ... and {len(missing) - MAX_EXAMPLES} more never-ingested tasks")
    stats["ingested"] = len(ingested)

    # ── B: every ingested row is accounted for in 2_normalized.csv ───────────────
    if normalized is None:
        fails.append("B: 2_normalized.csv missing — run normalize")
        return fails, stats
    pieces = live_pieces(ctx, ingested)
    stats["live_pieces"] = len(pieces)
    stats["removed"] = sum(1 for p in pieces if not p["kept"])
    # output rows per ingested row, traced back by original_task_name (the text as typed, which for a split
    # row is the whole entry), or task_name when it was not changed
    observed = Counter((r["source"], r["original"] or r["task"]) for r in normalized)
    by_origin = Counter((p["source"], p["origin"]) for p in pieces if p["kept"])
    b_mismatch = 0
    for r in {(r["source"], r["task"]): r for r in ingested}.values():
        key = (r["source"], r["task"])
        exp, obs = by_origin.get(key, 0), observed.pop(key, 0)
        if exp != obs:
            b_mismatch += 1
            if b_mismatch <= MAX_EXAMPLES:
                what = "LOST (no fate recorded)" if obs < exp else "EXTRA rows"
                fails.append(f"B: {what}  [{r['source'][:8]}] expected {exp} kept, found {obs}: {r['task'][:70]!r}")
    if b_mismatch > MAX_EXAMPLES:
        fails.append(f"B: ... and {b_mismatch - MAX_EXAMPLES} more count mismatches")
    if b_mismatch > 0.2 * max(1, len(ingested)):
        fails.append("B: mismatch rate >20% — 2_normalized.csv is likely STALE against the current "
                     "model/prompt caches; re-run normalize before reading these as losses")
    for sid, prov in list(observed)[:MAX_EXAMPLES]:
        fails.append(f"B: ORPHAN output row (no ingested source)  [{sid[:8]}] {prov[:70]!r}")
    if len(observed) > MAX_EXAMPLES:
        fails.append(f"B: ... and {len(observed) - MAX_EXAMPLES} more orphans")
    stats["normalized"] = len(normalized)

    # ── C: re-running dedup gives exactly 3_deduped.csv ──────────────────────────
    if deduped is None:
        fails.append("C: 3_deduped.csv missing — run dedup")
        return fails, stats
    replay = [(r["source"], r["task"].strip()) for r in _dedup(Atoms(normalized)).rows]
    actual = [(r["source"], r["task"].strip()) for r in deduped]
    if replay != actual:
        gone = [k for k in replay if k not in set(actual)]
        extra = [k for k in actual if k not in set(replay)]
        for sid, t in gone[:MAX_EXAMPLES]:
            fails.append(f"C: in 2_normalized but not 3_deduped  [{sid[:8]}] {t[:70]!r}")
        for sid, t in extra[:MAX_EXAMPLES]:
            fails.append(f"C: in 3_deduped with no 2_normalized source  [{sid[:8]}] {t[:70]!r}")
        if not gone and not extra:
            fails.append("C: 3_deduped row ORDER differs from a dedup replay (stale file?)")
    stats["dups"] = len(normalized) - len(replay)
    stats["deduped"] = len(deduped)
    return fails, stats


def audit(occupation: str | None = None) -> bool:
    """Audit one occupation or all of them, printing PASS/FAIL per occupation. -> passed?"""
    workspace.load_dotenv()
    proc = workspace.root() / "results" / "processing"
    occs = [occupation] if occupation else sorted(d.name for d in proc.glob("*") if (d / "1_ingested.csv").exists())
    if not occs:
        print("nothing to audit: no results/processing/<occ>/1_ingested.csv found")
        return False
    ctx = workspace.aggregator(occs[0]).context()
    ok = True
    for occ in occs:
        fails, s = audit_occ(occ, ctx)
        ok = ok and not fails
        counts = (f"sessions {s.get('sessions', '?'):>3}  session_tasks {s.get('session_tasks', '?'):>4}  "
                  f"ingested {s.get('ingested', '?'):>4}  live {s.get('live_pieces', '?'):>4}  "
                  f"removed {s.get('removed', '?'):>3}  normalized {s.get('normalized', '?'):>4}  "
                  f"dups {s.get('dups', '?'):>2}  deduped {s.get('deduped', '?'):>4}")
        print(f"{'FAIL' if fails else 'PASS'}  {occ}\n      {counts}")
        for f in fails:
            print(f"      ✗ {f}")
    print("\naudit PASSED — every session task is accounted for through 3_deduped.csv." if ok else
          "\naudit FAILED — at least one task's fate is unaccounted for (or an output file is stale).")
    return ok
