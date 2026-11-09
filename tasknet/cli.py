"""The `tasknet` command.

    tasknet process <occupation> [--data-dir DIR]
    tasknet build   <occupation> [--fresh] [--max-rounds N] [--batch]
    tasknet audit   [<occupation>] [--data-dir DIR]
    tasknet verify  <occupation> [--which build|final]
    tasknet onet    [--release 31_0] [--force]

`--home DIR` (or $WORKSPACE) sets the workspace that holds results/, cache/ and .env.
"""
from __future__ import annotations

import argparse
import sys

import tasknet


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tasknet", description="Turn worker interviews into an occupational task list.")
    ap.add_argument("--home", help="workspace directory (default: $WORKSPACE, else the checkout or current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("process", help="interview sessions -> results/processing/<occ>/3_deduped.csv")
    p.add_argument("occupation")
    p.add_argument("--data-dir", help="directory of session JSONs (default: $DATA_DIR/interviews/<occ>)")

    b = sub.add_parser("build", help="processed tasks -> results/aggregation/<occ>/build.json")
    b.add_argument("occupation")
    b.add_argument("--fresh", action="store_true", help="ignore an adopted final.json and build from scratch")
    b.add_argument("--max-rounds", type=int, help="cap on chain cycles (default 3)")
    b.add_argument("--batch", action="store_true", help="OpenAI Batch API: half price, blocks until done")

    a = sub.add_parser("audit", help="check no session task was lost in processing (no API calls)")
    a.add_argument("occupation", nargs="?")
    a.add_argument("--data-dir", help="the sessions processed (default: $DATA_DIR/interviews/<occ>)")

    v = sub.add_parser("verify", help="check a built list's invariants")
    v.add_argument("occupation")
    v.add_argument("--which", default="build", choices=["build", "final"])

    o = sub.add_parser("onet", help="download the O*NET task statements into data/onet/")
    o.add_argument("--release", default=None)
    o.add_argument("--force", action="store_true")

    args = ap.parse_args(argv)
    if args.home:
        tasknet.workspace(args.home)
    if args.cmd == "process":
        tasknet.process(args.occupation, args.data_dir)
        return 0
    if args.cmd == "build":
        tasknet.build(args.occupation, fresh=args.fresh, max_rounds=args.max_rounds, batch=args.batch)
        return 0
    if args.cmd == "audit":
        return 0 if tasknet.audit(args.occupation, data_dir=args.data_dir) else 1
    if args.cmd == "verify":
        fails = tasknet.verify(args.occupation, args.which)
        for f in fails:
            print(f"  ✗ {f}")
        print("verify " + ("FAILED" if fails else "PASSED"))
        return 1 if fails else 0
    if args.cmd == "onet":
        from . import onet
        onet.main(([f"--release={args.release}"] if args.release else []) + (["--force"] if args.force else []))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
