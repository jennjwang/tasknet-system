"""audit — check that no interview task was silently lost in processing. No model calls.

    tasknet audit                    # every occupation under results/processing/
    tasknet audit lawyers

Only that every task reached the next file or was removed by the filter, never whether a decision was right.
Three checks per occupation:

  A  sessions → 1_ingested      every selectedTasks entry (one per line, as ingest splits it) is in 1_ingested.csv
  B  1_ingested → 2_normalized  every ingested row is either traced in 2_normalized.csv (by original_task_name,
                                else task_name) or has a filter verdict that removed it (or all its split pieces);
                                and normalize, replayed from its caches alone, reproduces 2_normalized.csv
  C  2_normalized → 3_deduped   re-running dedup gives exactly 3_deduped.csv

B reads normalize's caches for the currently configured models and prompts. A B failure right after changing
one of them means 2_normalized.csv is out of date (re-run `tasknet process`), not that tasks were lost.
"""
from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path

from aggregate import Atoms
from aggregate.processing import dedup, lines
from aggregate.processing import normalize as normalize_rows

from .ingest import session_files
from .paths import interviews_dir, results_dir, workspace

# the module, not the function aggregate.processing re-exports under its name: its caches (_Pass) and the
# helpers normalize itself decides with (_hash, kept, wants_split)
_nz = importlib.import_module("aggregate.processing.normalize")
MAX_EXAMPLES = 5


class _NoCalls:
    """A client that refuses every call: the replay must come from the caches alone."""
    def __getattr__(self, name):
        raise RuntimeError("cache miss")


def _read(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def session_tasks(data_dir: Path) -> list[tuple[str, str]]:
    """Every task ingest should have read from `data_dir`. -> [(session_id, task)]"""
    out = []
    for p in session_files(data_dir):
        d = json.loads(p.read_text())
        if "sessionId" not in d:
            continue
        for raw in d.get("selectedTasks") or []:
            if raw.strip():
                out += [(d["sessionId"], t) for t in lines(raw.strip())]
    return out


def _filter_verdicts(ctx):
    """The split and filter caches as normalize itself would read them. -> (split cache, filter cache)"""
    split = _nz._Pass(ctx, "split", "split", ctx.model("normalize"), ctx.effort("normalize"), 4096).cache
    filt = _nz._Pass(ctx, "filter", "filter", ctx.model("filter"), ctx.effort("filter"), 16000).cache
    return split, filt


def audit_occ(occ: str, data_dir=None) -> tuple[list[str], dict]:
    """Run checks A, B and C for one occupation. -> (failures, counts); each failure is one printable line."""
    from .pipeline import aggregator
    proc = results_dir("processing", occ)
    fails: list[str] = []
    stats: dict = {"occ": occ}
    ingested = _read(proc / "1_ingested.csv")
    normalized = _read(proc / "2_normalized.csv") if (proc / "2_normalized.csv").exists() else None
    deduped = _read(proc / "3_deduped.csv") if (proc / "3_deduped.csv").exists() else None
    stats["ingested"] = len(ingested)

    # ── A: every session task reached 1_ingested.csv ──────────────────────────────────────────────
    sessions = Path(data_dir) if data_dir else interviews_dir() / occ
    have = {(r["session_id"], r["task_name"]) for r in ingested}
    if not sessions.is_dir():
        fails.append(f"A: no session dir found ({sessions}) — cannot verify ingest")
    else:
        expected = session_tasks(sessions)
        missing = [k for k in expected if k not in have]
        stats["sessions"], stats["session_tasks"] = len({s for s, _ in expected}), len(expected)
        fails += [f"A: never ingested  [{sid[:8]}] {t[:80]!r}" for sid, t in missing[:MAX_EXAMPLES]]
        if len(missing) > MAX_EXAMPLES:
            fails.append(f"A: ... and {len(missing) - MAX_EXAMPLES} more never-ingested tasks")

    # ── B: every ingested row is accounted for in 2_normalized.csv ────────────────────────────────
    if normalized is None:
        fails.append("B: 2_normalized.csv missing — run `tasknet process`")
        return fails, stats
    stats["normalized"] = len(normalized)
    ctx = aggregator(client=_NoCalls()).context()        # replays from the caches only
    split, filt = _filter_verdicts(ctx)
    traced = {(r["session_id"], r["original_task_name"] or r["task_name"]) for r in normalized}
    removed = lost = 0
    for r in Atoms(ingested).rows:
        if (r["source"], r["task"]) in traced:
            continue
        pieces = (split.get(_nz._hash(r["task"])) if _nz.wants_split(r) else None) or [r["task"]]
        if all(_nz._hash(p) in filt and not _nz.kept(filt[_nz._hash(p)]) for p in pieces):
            removed += 1
            continue
        lost += 1
        if lost <= MAX_EXAMPLES:
            fails.append(f"B: LOST (not in 2_normalized, no filter verdict removing it)  [{r['source'][:8]}] "
                         f"{r['task'][:70]!r}")
    if lost > MAX_EXAMPLES:
        fails.append(f"B: ... and {lost - MAX_EXAMPLES} more lost rows")
    stats["removed"] = removed
    try:
        replay = [(r["source"], r["task"]) for r in normalize_rows(ctx, Atoms(ingested)).rows]
    except RuntimeError:
        fails.append("B: normalize's caches do not cover 1_ingested.csv (models or prompts changed?) — "
                     "re-run `tasknet process`")
        replay = None
    if replay is not None:
        actual = [(r["session_id"], r["task_name"]) for r in normalized]
        if replay != actual:
            extra = [k for k in actual if k not in set(replay)]
            gone = [k for k in replay if k not in set(actual)]
            fails += [f"B: in 2_normalized with no source in a replay  [{s[:8]}] {t[:70]!r}" for s, t in extra[:MAX_EXAMPLES]]
            fails += [f"B: a replay writes it but 2_normalized lacks it  [{s[:8]}] {t[:70]!r}" for s, t in gone[:MAX_EXAMPLES]]
            if not extra and not gone:
                fails.append("B: 2_normalized row ORDER differs from a replay (stale file?)")

    # ── C: re-running dedup gives exactly 3_deduped.csv ───────────────────────────────────────────
    if deduped is None:
        fails.append("C: 3_deduped.csv missing — run `tasknet process`")
        return fails, stats
    replay = [(r["source"], r["task"].strip()) for r in dedup(Atoms(normalized)).rows]
    actual = [(r["session_id"], r["task_name"].strip()) for r in deduped]
    if replay != actual:
        gone = [k for k in replay if k not in set(actual)]
        extra = [k for k in actual if k not in set(replay)]
        fails += [f"C: in 2_normalized but not 3_deduped  [{s[:8]}] {t[:70]!r}" for s, t in gone[:MAX_EXAMPLES]]
        fails += [f"C: in 3_deduped with no 2_normalized source  [{s[:8]}] {t[:70]!r}" for s, t in extra[:MAX_EXAMPLES]]
        if not gone and not extra:
            fails.append("C: 3_deduped row ORDER differs from a dedup replay (stale file?)")
    stats["dups"], stats["deduped"] = len(normalized) - len(replay), len(deduped)
    return fails, stats


def audit(occupation: str | None = None, data_dir=None) -> bool:
    """Audit one occupation or all of them, printing PASS/FAIL per occupation. -> passed?"""
    proc = workspace() / "results" / "processing"
    occs = [occupation] if occupation else sorted(
        d.name for d in (proc.iterdir() if proc.is_dir() else []) if (d / "1_ingested.csv").exists())
    if not occs:
        print("nothing to audit: no results/processing/<occ>/1_ingested.csv found", file=sys.stderr)
        return False
    ok = True
    for occ in occs:
        fails, s = audit_occ(occ, data_dir)
        ok = ok and not fails
        print(f"{'FAIL' if fails else 'PASS'}  {occ}\n      "
              + "  ".join(f"{k} {s.get(k, '?')}" for k in ("sessions", "session_tasks", "ingested", "removed",
                                                             "normalized", "dups", "deduped")))
        for f in fails:
            print(f"      ✗ {f}")
    print("\naudit " + ("PASSED — every session task is accounted for through 3_deduped.csv." if ok else
                        "FAILED — at least one task's fate is unaccounted for (or an output file is stale)."))
    return ok
