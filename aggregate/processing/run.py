#!/usr/bin/env python3
"""run — process one occupation's interviews into its task list: ingest → normalize → dedup.

  python -m tasknet.processing.run --occupation lawyers                    # interviews/lawyers/ → results/processing/lawyers/
  python -m tasknet.processing.run --occupation lawyers --from normalize   # skip ingest
  python -m tasknet.processing.run --occupation lawyers --force

Runs each stage's own CLI in turn and stops at the first failure. Ingest always runs (it only appends new
tasks); a later stage runs only when its output is missing or older than its input. --force re-runs every
selected stage, starting ingest from an empty file.

Output: results/processing/<occ>/3_deduped.csv, which tasknet.aggregation reads.
"""
import argparse
import os
import subprocess
import sys

from tasknet.lib import REPO_ROOT, interviews_dir  # $DATA_DIR/interviews, else data/interviews

STAGES = [("ingest", None, "1_ingested.csv"),          # (stage, input, output), in results/processing/<occ>/
          ("normalize", "1_ingested.csv", "2_normalized.csv"),
          ("dedup", "2_normalized.csv", "3_deduped.csv")]
IDS = [s for s, _, _ in STAGES]


def main():
    p = argparse.ArgumentParser(description="Run the processing stages for one occupation.")
    p.add_argument("--occupation", required=True, help="the occupation slug (sets $OCCUPATION)")
    p.add_argument("--data-dir", help="session-JSON dir for ingest (default: $DATA_DIR/interviews/<occupation>)")
    p.add_argument("--from", dest="start", choices=IDS, default=IDS[0], help="first stage to run")
    p.add_argument("--to", dest="end", choices=IDS, default=IDS[-1], help="last stage to run")
    p.add_argument("--force", action="store_true", help="re-run even if the output is up to date")
    args = p.parse_args()

    occ = os.environ["OCCUPATION"] = args.occupation
    proc = REPO_ROOT / "results" / "processing" / occ
    stages = STAGES[IDS.index(args.start):IDS.index(args.end) + 1]
    if not stages:
        sys.exit(f"--from {args.start} comes after --to {args.end}")

    if stages[0][0] == "ingest":
        data_dir = args.data_dir or (str(interviews_dir() / occ) if (interviews_dir() / occ).is_dir() else None)
        if not data_dir:
            sys.exit(f"ingest needs --data-dir <dir> or {interviews_dir() / occ}/; or --from normalize to skip it.")
        if args.force:
            (proc / "1_ingested.csv").unlink(missing_ok=True)

    for stage, inp, out in stages:
        inp, out = (proc / inp if inp else None), proc / out
        stale = (args.force or inp is None or not out.exists()
                 or (inp.exists() and out.stat().st_mtime < inp.stat().st_mtime))
        if not stale:
            print(f"  SKIP {stage:<10} up to date: {out.relative_to(REPO_ROOT)}")
            continue
        cmd = [sys.executable, "-m", f"tasknet.processing.{stage}"]
        if stage == "ingest":
            cmd += ["--data-dir", data_dir]
        print(f"  RUN  {stage:<10} {' '.join(['python', *cmd[1:]])}", flush=True)
        if (rc := subprocess.run(cmd).returncode):
            sys.exit(f"\nstage '{stage}' failed (exit {rc}) — stopping.")
    print(f"\nOutput: {(proc / '3_deduped.csv').relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
