#!/usr/bin/env python3
"""audit — check that no interview task was silently lost in processing.

    python -m tasknet.processing.audit                       # every occupation under results/processing/
    python -m tasknet.processing.audit --occupation lawyers

No model calls, and no judgement of whether a decision was right: only that every task reached the next file
or was removed by the filter. Exits non-zero if any check fails. Three checks per occupation, each replaying
the stage's own code over its cached results:

  A  sessions → 1_ingested      every selectedTasks entry (after ingest.explode) is in 1_ingested.csv
  B  1_ingested → 2_normalized  every piece of every row (after the split) is either in 2_normalized.csv
                                (traced by original_task_name) or removed by the filter; no extra rows
  C  2_normalized → 3_deduped   re-running dedup gives exactly 3_deduped.csv

Check B reads the caches for the currently configured models and prompts. Many B mismatches after changing
one of them mean 2_normalized.csv is out of date (re-run normalize), not that tasks were lost.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter
from pathlib import Path

from tasknet.lib import REPO_ROOT as ROOT, interviews_dir
from tasknet.processing import dedup       # dedupe(), replayed by check C
from tasknet.processing import ingest      # explode(), replayed by check A
from tasknet.processing import normalize   # split_row(), kept() and the current cache paths, for check B

MAX_EXAMPLES = 5   # examples printed per kind of failure, per occupation


def _read(path: Path) -> list[dict]:
    """The rows of a CSV file."""
    with path.open() as f:
        return list(csv.DictReader(f))


def session_tasks(data_dir: Path) -> list[tuple[str, str]]:
    """Every task ingest should have read from `data_dir`. -> [(session_id, task)]"""
    out = []
    for path in sorted(glob.glob(str(data_dir / "**" / "*.json"), recursive=True)):
        with open(path) as f:
            d = json.load(f)
        for raw in d.get("selectedTasks", []):
            raw = raw.strip()
            if not raw:
                continue
            for piece in ingest.explode(raw):
                out.append((d["sessionId"], piece))
    return out


def live_pieces(occ: str) -> list[dict]:
    """Every task the filter saw for one occupation (after the split), with whether it was kept, from the
    current caches. -> [{session_id, source, text, kept}], `source` being the ingested row it came from.
    Also used to sample tasks for checking the filter by hand: it includes removed tasks, not just kept ones."""
    split_cache = normalize.load_cache(normalize.SPLIT_CACHE_PATH)
    filter_cache = normalize.load_cache(normalize.FILTER_CACHE_PATH)
    out = []
    for r in _read(ROOT / "results" / "processing" / occ / "1_ingested.csv"):
        for p in normalize.split_row(r, split_cache) or [r["task_name"]]:
            out.append({"session_id": r["session_id"], "source": r["task_name"], "text": p,
                        "kept": normalize.kept(filter_cache.get(normalize._hash(p)))})
    return out


def audit_occ(occ: str) -> tuple[list[str], dict]:
    """Run checks A, B and C for one occupation. -> (failures, counts); each failure is one printable line."""
    proc = ROOT / "results" / "processing" / occ
    fails: list[str] = []
    stats: dict = {"occ": occ}

    ingested = _read(proc / "1_ingested.csv")
    normalized = _read(proc / "2_normalized.csv") if (proc / "2_normalized.csv").exists() else None
    deduped = _read(proc / "3_deduped.csv") if (proc / "3_deduped.csv").exists() else None

    # ── A: every session task reached 1_ingested.csv ─────────────────────────────
    sessions = interviews_dir() / occ           # the folder run.py ingests by default
    have = {(r["session_id"], r["task_name"]) for r in ingested}
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
    pieces = live_pieces(occ)
    stats["live_pieces"] = len(pieces)
    stats["removed"] = sum(1 for p in pieces if not p["kept"])
    # count the output rows per ingested row, traced back by original_task_name (the text as typed, which
    # for a split row is the whole entry), or task_name when it was not changed
    observed = Counter((r["session_id"], r["original_task_name"] or r["task_name"])
                       for r in normalized)
    by_source = Counter()
    for p in pieces:
        if p["kept"]:
            by_source[(p["session_id"], p["source"])] += 1
    sources = {(r["session_id"], r["task_name"]): r for r in ingested}   # ingest keeps these unique
    b_mismatch = 0
    for key, r in sources.items():
        exp, obs = by_source.get(key, 0), observed.pop(key, 0)
        if exp != obs:
            b_mismatch += 1
            if b_mismatch <= MAX_EXAMPLES:
                what = "LOST (no fate recorded)" if obs < exp else "EXTRA rows"
                fails.append(f"B: {what}  [{r['session_id'][:8]}] expected {exp} kept, found {obs}: "
                             f"{r['task_name'][:70]!r}")
    if b_mismatch > MAX_EXAMPLES:
        fails.append(f"B: ... and {b_mismatch - MAX_EXAMPLES} more count mismatches")
    if b_mismatch > 0.2 * max(1, len(sources)):
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
    replay = [(r["session_id"], r["task_name"].strip()) for r in dedup.dedupe(normalized)]
    actual = [(r["session_id"], r["task_name"].strip()) for r in deduped]
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


def main() -> None:
    """Audit one occupation or all of them, printing PASS/FAIL per occupation."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--occupation", help="audit one occupation (default: every dir under results/processing/)")
    args = p.parse_args()

    proc = ROOT / "results" / "processing"
    occs = [args.occupation] if args.occupation else sorted(
        d.name for d in proc.iterdir() if (d / "1_ingested.csv").exists())
    if not occs:
        sys.exit("nothing to audit: no results/processing/<occ>/1_ingested.csv found")

    any_fail = False
    for occ in occs:
        fails, s = audit_occ(occ)
        flag = "FAIL" if fails else "PASS"
        any_fail = any_fail or bool(fails)
        counts = (f"sessions {s.get('sessions', '?'):>3}  session_tasks {s.get('session_tasks', '?'):>4}  "
                  f"ingested {s.get('ingested', '?'):>4}  live {s.get('live_pieces', '?'):>4}  "
                  f"removed {s.get('removed', '?'):>3}  normalized {s.get('normalized', '?'):>4}  "
                  f"dups {s.get('dups', '?'):>2}  deduped {s.get('deduped', '?'):>4}")
        print(f"{flag}  {occ}\n      {counts}")
        for f in fails:
            print(f"      ✗ {f}")
    print()
    if any_fail:
        sys.exit("audit FAILED — at least one task's fate is unaccounted for (or an output file is stale).")
    print("audit PASSED — every session task is accounted for through 3_deduped.csv.")


if __name__ == "__main__":
    main()
