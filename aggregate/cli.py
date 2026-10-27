"""The `tasknet` command.

    tasknet process <occupation> [--data-dir DIR] [--force] [--from STAGE] [--to STAGE]
    tasknet build   <occupation> [--fresh] [--max-rounds N] [--batch]
    tasknet audit   [<occupation>]
    tasknet verify  <artifact.json> ...

`--home DIR` (or $WORKSPACE) sets the workspace that holds results/, cache/ and .env; default: the
current directory. Options not listed here are passed through to the underlying stage CLI.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _exec(module: str, args: list[str]) -> int:
    return subprocess.run([sys.executable, "-m", module, *args]).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tasknet", description="Turn worker interviews into an occupational task list.")
    ap.add_argument("--home", help="workspace directory (default: $WORKSPACE, else the current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("process", help="interview sessions -> results/processing/<occ>/3_deduped.csv")
    p.add_argument("occupation")
    p.add_argument("--data-dir", help="directory of session JSONs (default: $DATA_DIR/interviews/<occ>)")

    b = sub.add_parser("build", help="processed tasks -> results/aggregation/<occ>/build.json")
    b.add_argument("occupation")
    b.add_argument("--fresh", action="store_true", help="ignore an adopted final.json and build from scratch")
    b.add_argument("--max-rounds", type=int, help="cap on chain rounds (default 3)")
    b.add_argument("--batch", action="store_true", help="OpenAI Batch API: half price, blocks until done")

    a = sub.add_parser("audit", help="check no session task was lost in processing (no API calls)")
    a.add_argument("occupation", nargs="?")

    v = sub.add_parser("verify", help="check a list artifact's invariants")
    v.add_argument("artifacts", nargs="+")

    args, rest = ap.parse_known_args(argv)
    if args.home:
        os.environ["WORKSPACE"] = str(Path(args.home).expanduser().resolve())

    if args.cmd == "process":
        extra = ["--data-dir", str(Path(args.data_dir).expanduser().resolve())] if args.data_dir else []
        return _exec("tasknet.processing.run", ["--occupation", args.occupation, *extra, *rest])
    if args.cmd == "build":
        if rest:
            ap.error(f"unrecognized arguments: {' '.join(rest)}")
        if args.fresh:
            os.environ["FRESH"] = "1"
        if args.max_rounds is not None:
            os.environ["MAX_ROUNDS"] = str(args.max_rounds)
        if args.batch:
            os.environ["BATCH"] = "1"
        return _exec("tasknet.aggregation.chain", [args.occupation])
    if args.cmd == "audit":
        return _exec("tasknet.processing.audit", [*(["--occupation", args.occupation] if args.occupation else []), *rest])
    if args.cmd == "verify":
        return _exec("tasknet.aggregation.verify", [str(Path(p).expanduser().resolve()) for p in args.artifacts])
    return 2


if __name__ == "__main__":
    sys.exit(main())
