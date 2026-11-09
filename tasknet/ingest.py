"""ingest — the Interviewer's session JSONs -> raw task rows for aggregate (processing step 1).

    rows = read_sessions("data/interviews/lawyers")

One row per task in each session's `selectedTasks` (the list the participant confirmed at the end of the
interview): {source, occupation, title, status, task}. `source` is the session id, `occupation`/`title` the
O*NET occupation the participant chose (`occupationSelection`). `status`, matched against `taskItems`:
  added      the participant typed it in
  edited     the participant changed a suggested task
  confirmed  a suggested task, accepted unchanged
A typed entry holding a bulleted list becomes one row per line (aggregate.processing.lines). A (session, task)
pair appears once.
"""
from __future__ import annotations

import json
from pathlib import Path

from aggregate.processing import lines


def session_files(data_dir) -> list[Path]:
    """Every session JSON under `data_dir`, sorted."""
    return sorted(Path(data_dir).glob("**/*.json"))


def read_session(path) -> list[dict]:
    d = json.loads(Path(path).read_text())
    sid = d["sessionId"]
    occ = d.get("occupationSelection") or {}
    items = d.get("taskItems") or []
    added = {t["name"].strip() for t in items if t.get("addedByParticipant") and t.get("name")}
    edited = {t["name"].strip() for t in items if t.get("status") == "edited" and t.get("name")}
    rows, seen = [], set()
    for raw in d.get("selectedTasks") or []:
        raw = raw.strip()
        if not raw:
            continue
        # status is decided on the whole entry; the tasks split from it share it
        status = "added" if raw in added else "edited" if raw in edited else "confirmed"
        for task in lines(raw):
            if (sid, task) in seen:
                continue
            seen.add((sid, task))
            rows.append({"source": sid, "occupation": occ.get("selectedCode") or "",
                         "title": occ.get("selectedTitle") or "", "status": status, "task": task})
    return rows


def read_sessions(data_dir) -> list[dict]:
    """Every session under `data_dir` -> rows. A file that is not a session (no sessionId) is skipped."""
    rows = []
    for p in session_files(data_dir):
        try:
            rows += read_session(p)
        except (KeyError, ValueError) as e:
            print(f"  skipped {p.name}: not a session ({type(e).__name__}: {e})")
    return rows
