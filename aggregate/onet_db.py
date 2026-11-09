"""O*NET task statements: the published task list for every O*NET-SOC occupation.

Reads data/onet/task_statements.csv (fetch it with aggregate/fetch_onet.py, or point REFERENCE_DATA_DIR at a
download elsewhere). The pipeline uses it for occupation titles (workspace.occupation_meta) and as the reference
corpus of the writer's umbrella-verb check (statements_in_group, passed to the Aggregator).
"""
from __future__ import annotations

import csv
from functools import lru_cache

from .workspace import env_path, root

# REFERENCE_DATA_DIR points at an O*NET download elsewhere; default: data/onet in the workspace.
ONET_DIR = env_path("REFERENCE_DATA_DIR", root() / "data" / "onet")
CSV_PATH = ONET_DIR / "task_statements.csv"

_MISSING = ("{path} is missing — download 'Task Statements' from https://www.onetcenter.org/database.html "
            "(CSV) and save it there, or run aggregate/fetch_onet.py.")


@lru_cache(maxsize=1)
def _by_soc() -> dict[str, list[dict]]:
    """The published statements grouped by SOC, in export order. -> {soc: [row, ...]}"""
    if not CSV_PATH.exists():
        raise FileNotFoundError(_MISSING.format(path=CSV_PATH))
    out: dict[str, list[dict]] = {}
    with CSV_PATH.open(newline="") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["O*NET-SOC Code"], []).append(r)
    return out


def title(soc: str) -> str:
    """The SOC's occupation title. -> the title, or `soc` itself if unknown"""
    rows = _by_soc().get(soc, [])
    return rows[0]["Title"] if rows else soc


def socs() -> dict[str, str]:
    """Every occupation in the export. -> {soc: title}"""
    return {soc: rows[0]["Title"] for soc, rows in _by_soc().items()}


def statements_in_group(prefix: str) -> list[str]:
    """Every task statement of the occupations whose SOC code starts with `prefix` (the 2-digit major group for
    the umbrella-verb check). -> [statement]"""
    return [r["Task"] for soc, rows in _by_soc().items() if soc.startswith(prefix) for r in rows]
