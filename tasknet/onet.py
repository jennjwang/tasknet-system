"""onet — the O*NET task statements, downloaded (not stored in the repo: O*NET is CC BY 4.0, National Center for
O*NET Development) and read for the TaskNet System's own needs: occupation titles for the validation inputs, and
the published statements of a SOC major group, which aggregate's umbrella-verb check attests against.

    python -m tasknet.onet                       # download task_statements.csv into data/onet/
    python -m tasknet.onet --release 31_0 --dest some/dir

Reads $REFERENCE_DATA_DIR/task_statements.csv, else data/onet/ in the workspace.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path

from .paths import env_path, workspace

ONET_RELEASE = "31_0"
ONET_DL = "https://www.onetcenter.org/dl_files/database/db_{rel}_csv/{name}.csv"
NAMES = ["task_statements"]


def onet_dir() -> Path:
    return env_path("REFERENCE_DATA_DIR", workspace() / "data" / "onet")


@lru_cache(maxsize=1)
def _rows() -> list[dict]:
    path = onet_dir() / "task_statements.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing — run `python -m tasknet.onet` (or `make onet`)")
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def socs() -> dict[str, str]:
    """{soc: title} over every occupation in the export."""
    out = {}
    for r in _rows():
        out.setdefault(r["O*NET-SOC Code"], r["Title"])
    return out


def title(soc: str) -> str:
    return socs().get(soc, soc)


def statements(soc: str) -> list[str]:
    return list(dict.fromkeys(r["Task"] for r in _rows() if r["O*NET-SOC Code"] == soc))


def group_statements(soc: str) -> list[str]:
    """Every published statement in the SOC's major group (its first two digits): aggregate's
    `reference_statements` for the umbrella-verb check."""
    prefix = (soc or "")[:2]
    return [r["Task"] for r in _rows() if prefix and r["O*NET-SOC Code"].startswith(prefix)]


def slugify(t: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t.lower())).strip("-")


def occupation_meta(occ: str) -> dict:
    """{occ, soc, title} for a slug: the SOC from results/crosswalk/<occ>/scoped.json if present, else by matching
    the slug against the slugified O*NET titles. Never raises: without a match, an empty SOC and the de-slugged name."""
    soc = ""
    scoped = workspace() / "results" / "crosswalk" / occ / "scoped.json"
    if scoped.exists():
        try:
            soc = (json.loads(scoped.read_text()).get("soc") or "").strip()
        except Exception:                                 # noqa: BLE001
            soc = ""
    t = ""
    try:
        if not soc:
            soc = next((s for s, tt in socs().items() if slugify(tt) == occ), "")
        t = title(soc) if soc else ""
    except Exception:                                     # noqa: BLE001 - no export on disk; the slug still reads
        t = ""
    return {"occ": occ, "soc": soc, "title": t or occ.replace("-", " ").title()}


# ── download ──────────────────────────────────────────────────────────────────────────────────────

def fetch(names=NAMES, release=ONET_RELEASE, dest=None, force=False) -> None:
    dest = Path(dest) if dest else onet_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        out = dest / f"{name}.csv"
        if out.exists() and not force:
            print(f"  {out} exists, skipping (--force to refresh)")
            continue
        url = ONET_DL.format(rel=release, name=name)
        print(f"  {url} -> {out}")
        _download(url, out)
        print(f"    {out.stat().st_size / 1e6:.1f} MB")


def _download(url, out: Path) -> None:
    """Fetch to a temp file beside `out` and rename it into place only once complete, so a failed download
    leaves no partial file for a later run to skip as present."""
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".part", dir=out.parent)
    try:
        with os.fdopen(fd, "wb") as fh, urllib.request.urlopen(url) as resp:
            shutil.copyfileobj(resp, fh)
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Download the O*NET task statements into data/onet/.")
    ap.add_argument("--release", default=ONET_RELEASE)
    ap.add_argument("--dest", default=None, help="default: $REFERENCE_DATA_DIR, else data/onet in the workspace")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    fetch(NAMES, a.release, a.dest, a.force)


if __name__ == "__main__":
    main()
