"""ingest — append each interview's final task list to 1_ingested.csv (processing step 1).

    from tasknet.processing import ingest
    ingest.ingest("lawyers", "path/to/sessions")          # -> rows appended

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

import csv
import glob
import json
import re
from pathlib import Path

from ..workspace import processing_dir

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


def sessions(data_dir) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(Path(data_dir) / "**" / "*.json"), recursive=True))]


def session_rows(path) -> list[dict]:
    """One session JSON -> its rows, in selectedTasks order (duplicates kept; `ingest` drops them)."""
    d = json.loads(Path(path).read_text())
    sid = d["sessionId"]
    occ = d.get("occupationSelection") or {}
    added_names = {t["name"].strip() for t in d.get("taskItems", []) if t.get("addedByParticipant") and t.get("name")}
    edited_names = {t["name"].strip() for t in d.get("taskItems", []) if t.get("status") == "edited" and t.get("name")}
    out = []
    for raw in d.get("selectedTasks", []):
        raw = raw.strip()
        if not raw:
            continue
        # status is decided on the whole entry; the tasks split from it share it
        status = "added" if raw in added_names else "edited" if raw in edited_names else "confirmed"
        out += [{"session_id": sid, "onet_code": occ.get("selectedCode") or "", "onet_title": occ.get("selectedTitle") or "",
                 "status": status, "task_name": t} for t in explode(raw)]
    return out


def ingest(occ: str, data_dir, dry_run: bool = False) -> int:
    """Append every session's new tasks under `data_dir` to results/processing/<occ>/1_ingested.csv. -> rows added"""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise SystemExit(f"--data-dir not found: {data_dir}")
    paths = sessions(data_dir)
    if not paths:
        raise SystemExit(f"no *.json sessions under {data_dir}")
    out = processing_dir(occ) / "1_ingested.csv"
    existing: set[tuple[str, str]] = set()
    if out.exists():
        with out.open() as f:
            existing = {(r["session_id"], r["task_name"]) for r in csv.DictReader(f)}
    new_rows = []
    for path in paths:
        added = 0
        for r in session_rows(path):
            if (r["session_id"], r["task_name"]) in existing:
                continue
            existing.add((r["session_id"], r["task_name"]))
            new_rows.append(r)
            added += 1
        if added:
            print(f"  {path.stem[:8]:<20} +{added} tasks")
    if not new_rows:
        print("  ingest: nothing to add.")
        return 0
    if dry_run:
        print(f"  ingest: {len(new_rows)} rows to add (dry run, not writing)")
        return len(new_rows)
    write_header = not out.exists()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    print(f"  ingest: appended {len(new_rows)} rows to {out}")
    return len(new_rows)
