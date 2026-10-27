#!/usr/bin/env python3
"""ingest — append each interview's final task list to 1_ingested.csv (processing step 1).

    python -m tasknet.processing.ingest --data-dir $DATA_DIR/interviews/lawyers
    python -m tasknet.processing.ingest --data-dir $DATA_DIR/interviews/lawyers --dry-run

Input:  a folder of session JSONs (one per interview, so one per participant).
Output: results/processing/<occ>/1_ingested.csv, one row per task:
        session_id, onet_code, onet_title, status, task_name.

The tasks are each session's `selectedTasks`: the list the participant confirmed at the end of the interview.
The occupation they chose (`occupationSelection`) becomes onet_code / onet_title. `status` records where each
task came from, matched against `taskItems`:
  added      the participant typed it in
  edited     the participant changed a suggested task
  confirmed  a suggested task, accepted unchanged
A typed entry holding a bulleted list is split into one task per line (`explode`).

Only new (session_id, task_name) pairs are appended, so re-running is safe.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from pathlib import Path

from tasknet.lib import REPO_ROOT, results_dir  # results/processing/<occ>/ for $OCCUPATION

ALL_TASKS = results_dir("processing") / "1_ingested.csv"

FIELDNAMES = ["session_id", "onet_code", "onet_title", "status", "task_name"]

# a bullet or number at the start of a line: "- x", "• x", "1. x", "a) x"
_MARKER = re.compile(r"^\s*(?:[-•*·▪●–—]|\(?\d{1,2}[.)]|[a-z][.)])\s+")


def explode(text: str) -> list[str]:
    """Split one selectedTasks entry into the tasks it holds. -> [str], never empty

    The interview's add-a-task box accepts several lines, so one entry can be a whole bulleted list. With more
    than one line, each line with 2+ words (after removing its bullet or number) is a task, and a heading
    line ending in ":" is dropped. A one-line entry comes back unchanged."""
    lines = [ln.strip() for ln in text.splitlines()]
    if len([ln for ln in lines if ln]) <= 1:
        return [text.strip()]
    out = []
    for ln in lines:
        ln = _MARKER.sub("", ln).strip()
        if len(ln.split()) < 2 or ln.endswith(":"):
            continue
        out.append(ln)
    return out or [" ".join(text.split())]


def main() -> None:
    """Read every session under --data-dir and append its new tasks."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", required=True,
                        help="directory of session JSONs to ingest (e.g. $DATA_DIR/interviews/<occupation>)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = REPO_ROOT / data_dir
    if not data_dir.is_dir():
        sys.exit(f"--data-dir not found: {data_dir}")
    sessions = sorted(glob.glob(str(data_dir / "**" / "*.json"), recursive=True))
    if not sessions:
        sys.exit(f"no *.json sessions under {data_dir}")

    existing: set[tuple[str, str]] = set()
    if ALL_TASKS.exists():
        with ALL_TASKS.open() as f:
            existing = {(r["session_id"], r["task_name"]) for r in csv.DictReader(f)}

    new_rows: list[dict] = []
    for path in sessions:
        with open(path) as f:
            d = json.load(f)
        sid = d["sessionId"]
        occ = d.get("occupationSelection") or {}
        onet_code  = occ.get("selectedCode") or ""
        onet_title = occ.get("selectedTitle") or ""
        added_names  = {t["name"].strip() for t in d.get("taskItems", [])
                        if t.get("addedByParticipant") and t.get("name")}
        edited_names = {t["name"].strip() for t in d.get("taskItems", [])
                        if t.get("status") == "edited" and t.get("name")}
        added = 0
        for raw in d.get("selectedTasks", []):
            raw = raw.strip()
            if not raw:
                continue
            # status is decided on the whole entry; the tasks split from it share it
            status = ("added" if raw in added_names else "edited" if raw in edited_names else "confirmed")
            pieces = explode(raw)
            if len(pieces) > 1:
                print(f"  {sid[:8]:<20} split a {len(pieces)}-line entry: {pieces[0][:50]!r} ...")
            for task_name in pieces:
                if (sid, task_name) in existing:
                    continue
                new_rows.append({
                    "session_id": sid,
                    "onet_code":  onet_code,
                    "onet_title": onet_title,
                    "status":     status,
                    "task_name":  task_name,
                })
                existing.add((sid, task_name))
                added += 1
        if added:
            print(f"  {sid[:8]:<20} +{added} tasks")

    if not new_rows:
        print("Nothing to add.")
        return

    print(f"\n{len(new_rows)} rows to add.")
    if args.dry_run:
        print("(dry-run — not writing)")
        return

    write_header = not ALL_TASKS.exists()
    ALL_TASKS.parent.mkdir(parents=True, exist_ok=True)
    with ALL_TASKS.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    print(f"Appended {len(new_rows)} rows to {ALL_TASKS}")


if __name__ == "__main__":
    main()
