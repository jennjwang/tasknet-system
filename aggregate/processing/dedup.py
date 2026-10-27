#!/usr/bin/env python3
"""dedup — drop exact-duplicate tasks within each participant (processing step 3).

    python -m tasknet.processing.dedup
    python -m tasknet.processing.dedup --dry-run

Input:  results/processing/<occ>/2_normalized.csv.
Output: results/processing/<occ>/3_deduped.csv, the same columns, which tasknet.aggregation reads.

A row is dropped if the same participant (session_id) already has the same task_name: a task listed twice,
or two tasks normalized to the same statement. Different participants with the same task stay separate rows,
and near-duplicate wordings are kept (similarity matching merged distinct tasks, like accounts receivable vs
payable).
"""

from __future__ import annotations

import argparse
import csv
import sys

from tasknet.lib import results_dir  # results/processing/<occ>/ for $OCCUPATION

RESULTS_DIR = results_dir("processing")
INPUT_CSV = RESULTS_DIR / "2_normalized.csv"
OUTPUT_CSV = RESULTS_DIR / "3_deduped.csv"


def dedupe(rows: list[dict]) -> list[dict]:
    """Keep the first row of each (session_id, task_name), ignoring surrounding spaces. -> rows"""
    seen: set[tuple[str, str]] = set()
    kept: list[dict] = []
    for r in rows:
        key = (r["session_id"], r["task_name"].strip())
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return kept


def main() -> None:
    """Deduplicate 2_normalized.csv into 3_deduped.csv."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print counts but do not write the output file")
    args = parser.parse_args()

    if not INPUT_CSV.exists():
        print(f"ERROR: {INPUT_CSV} not found.", file=sys.stderr)
        sys.exit(1)

    with INPUT_CSV.open() as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys()) if rows else []

    kept = dedupe(rows)
    removed = len(rows) - len(kept)
    print(f"Loaded {len(rows)} tasks; removed {removed} exact duplicates → {len(kept)} kept")

    if args.dry_run:
        print("(dry-run: no files written)")
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    print(f"\nWrote {len(kept)} tasks to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
