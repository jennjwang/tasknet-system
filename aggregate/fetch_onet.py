#!/usr/bin/env python3
"""Download the O*NET task statements the pipeline reads into data/onet/ (not stored in the repo: O*NET is
CC BY 4.0, National Center for O*NET Development — cite it, do not redistribute it under this repo's terms).

    python aggregate/fetch_onet.py                    # task_statements.csv (what onet_db.py reads)
    python aggregate/fetch_onet.py --release 31_0 --dest some/dir

The file is the O*NET Center's own CSV release, byte-for-byte:
    https://www.onetcenter.org/dl_files/database/db_<release>_csv/<name>.csv
"""
import argparse
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

try:  # where the pipeline reads them ($REFERENCE_DATA_DIR, else the workspace's data/onet)
    from tasknet.onet_db import ONET_DIR
except ImportError:  # tasknet not installed: this checkout's data/onet
    ONET_DIR = Path(__file__).resolve().parent.parent / "data" / "onet"
ONET_RELEASE = "31_0"
ONET_DL = "https://www.onetcenter.org/dl_files/database/db_{rel}_csv/{name}.csv"
NAMES = ["task_statements"]


def fetch(names, release, dest: Path, force=False):
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


def _download(url, out: Path):
    """Fetch `url` to a temp file beside `out` and rename it into place only once complete, so a download that
    fails midway leaves no partial file for a later run to skip as already present."""
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".part", dir=out.parent)
    try:
        with os.fdopen(fd, "wb") as fh, urllib.request.urlopen(url) as resp:
            shutil.copyfileobj(resp, fh)
        os.chmod(tmp, 0o644)                      # mkstemp makes it owner-only; a download is a plain data file
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--release", default=ONET_RELEASE)
    ap.add_argument("--dest", default=str(ONET_DIR))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    fetch(NAMES, a.release, Path(a.dest), a.force)


if __name__ == "__main__":
    main()
