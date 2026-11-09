"""The `tasknet` command.

    tasknet process <occupation> [--data-dir DIR] [--force] [--from STAGE] [--to STAGE]
    tasknet build   <occupation> [--fresh] [--max-rounds N] [--batch]
    tasknet audit   [<occupation>]
    tasknet verify  <artifact.json> ...

`--home DIR` (or $WORKSPACE) sets the workspace that holds results/, cache/ and .env; default: this checkout,
or the current directory when installed. Models, thresholds and batching are in aggregate/configs.yml; a YAML
at $TASKNET_CONFIG overlays it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .pipeline import STAGES


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tasknet", description="Turn worker interviews into an occupational task list.")
    ap.add_argument("--home", help="workspace directory (default: $WORKSPACE, else this checkout or the current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("process", help="interview sessions -> results/processing/<occ>/3_deduped.csv")
    p.add_argument("occupation")
    p.add_argument("--data-dir", help="directory of session JSONs (default: $DATA_DIR/interviews/<occ>)")
    p.add_argument("--force", action="store_true", help="re-run every selected stage, ingest from an empty file")
    p.add_argument("--from", dest="start", choices=STAGES, default=STAGES[0], help="first stage to run")
    p.add_argument("--to", dest="end", choices=STAGES, default=STAGES[-1], help="last stage to run")

    b = sub.add_parser("build", help="processed tasks -> results/aggregation/<occ>/build.json")
    b.add_argument("occupation")
    b.add_argument("--fresh", action="store_true", help="ignore an adopted final.json and build from scratch")
    b.add_argument("--max-rounds", type=int, help="cap on chain cycles (default: chain.max_cycles, 3)")
    b.add_argument("--batch", action="store_true", help="OpenAI Batch API: half price, blocks until done")

    a = sub.add_parser("audit", help="check no session task was lost in processing (no API calls)")
    a.add_argument("occupation", nargs="?")

    v = sub.add_parser("verify", help="check a list artifact's invariants (no API calls)")
    v.add_argument("artifacts", nargs="+")

    args = ap.parse_args(argv)
    if args.home:
        os.environ["WORKSPACE"] = str(Path(args.home).expanduser().resolve())
    from . import pipeline
    if args.cmd == "process":
        out = pipeline.process(args.occupation, args.data_dir, force=args.force, start=args.start, end=args.end)
        print(f"\nOutput: {out}")
        return 0
    if args.cmd == "build":
        out = pipeline.build(args.occupation, fresh=args.fresh, max_rounds=args.max_rounds, batch=args.batch)
        print(f"\nOutput: {out}")
        return 0
    if args.cmd == "audit":
        from .processing.audit import audit
        return 0 if audit(args.occupation) else 1
    if args.cmd == "verify":
        bad = 0
        for path in args.artifacts:
            problems = pipeline.verify(path)
            print(f"{'FAIL' if problems else 'ok  '}  {path}")
            for pr in problems:
                print(f"      ✗ {pr}")
            bad += bool(problems)
        return 1 if bad else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
