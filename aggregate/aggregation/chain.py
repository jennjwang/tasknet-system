#!/usr/bin/env python3
"""The chain: the paper's four steps, repeated until clustering proposes no new cluster that clears the worker
floor (round 1 always runs; at most MAX_ROUNDS).

    1 cluster      group the pool into new clusters
    2 synthesize   write a statement per cluster; split any that holds two duties (split); write the parts
    3 overlap      merge statements that describe the same work, reword partial overlaps apart
    4 coverage     check each statement still covers its reports; refile what it can, the rest stays in the pool

    python -m tasknet.aggregation.chain <occupation>        (or: tasknet build <occupation>)

It starts from the adopted final.json if there is one (AMEND: its clusters are the starting list and its
statements are kept), else, or with FRESH=1, from an empty list with every report in the pool (FRESH). It writes
build.json and never touches final.json; adopting is an explicit copy.

Each step runs as its own process, exactly as the step CLIs run by hand, because they read their settings
(OCCUPATION, models, cache paths) at import.

Settings: FRESH=1 · ADOPTED=<final_NN.json> (the adopted list to keep statements from, in a FRESH build) ·
MAX_ROUNDS (3) · BATCH (0 here; 1 sends the gpt judges through the Batch API, half price but blocking) ·
CHAIN_LOGS (default results/aggregation/<occ>/logs/<timestamp>) · SPEND_HOLDOUT=1 to rebuild a frozen list.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tasknet.lib import REPO_ROOT, env_path


def _frozen_holdout(occ: str) -> int:
    """How many held-out workers a rebuild would absorb (0 if this occupation's list is not frozen). The
    published lists are frozen so the evaluation's held-out sample stays held out."""
    path = env_path("HELDOUT_FROZEN", REPO_ROOT / "results" / "evaluation" / "heldout_frozen.json")
    try:
        o = json.loads(path.read_text())["occupations"].get(occ)
        return int(o["n_held_out_at_freeze"]) if o else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _run(module: str, *args: str, log: Path | None = None) -> str:
    """Run `python -m tasknet.aggregation.<module> args`, saving its output to `log`. -> the output.
    A failing step stops the chain."""
    r = subprocess.run([sys.executable, "-m", f"tasknet.aggregation.{module}", *args],
                       cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if log:
        log.write_text(r.stdout)
    if r.returncode and module != "verify":
        tail = "\n".join(r.stdout.splitlines()[-20:])
        raise SystemExit(f"chain: {module} failed ({r.returncode})" + (f"; log: {log}" if log else "") + f"\n{tail}")
    return r.stdout


def run_chain(occ: str) -> Path:
    """Build occupation `occ`'s list. -> the path of build.json."""
    if os.environ.get("SPEND_HOLDOUT", "0") != "1" and (n := _frozen_holdout(occ)) > 0:
        raise SystemExit(f"REFUSING: {occ}'s list is frozen; rebuilding would absorb its {n} held-out workers.\n"
                         "  re-run with SPEND_HOLDOUT=1 to proceed anyway.")
    os.environ.setdefault("BATCH", "0")
    os.environ["OCCUPATION"] = occ
    print(f"batch mode: BATCH={os.environ['BATCH']} "
          + ("(gpt fan-outs via Batch API, -50%, blocking)" if os.environ["BATCH"] == "1" else "(all calls live)"))

    R = REPO_ROOT / "results" / "aggregation" / occ
    build = R / "build.json"
    logs = Path(os.environ.get("CHAIN_LOGS") or R / "logs" / time.strftime("%Y%m%d-%H%M%S"))
    logs.mkdir(parents=True, exist_ok=True)
    print(f"chain logs -> {logs}")
    t = occ[:4]                                         # log-file prefix

    def verify():
        for ln in _run("verify", str(build)).splitlines():
            if "ok" in ln or "FAIL" in ln:
                print(ln)

    def snap(step):                                     # the list as this step left it (the last round's)
        (R / "stages").mkdir(parents=True, exist_ok=True)
        shutil.copy(build, R / "stages" / f"{step}.json")

    if (R / "final.json").exists() and os.environ.get("FRESH", "0") != "1":
        mode, std = "AMEND", ["--adopted", "final.json"]
        shutil.copy(R / "final.json", build)
    else:
        mode = "FRESH"
        std = ["--adopted", os.environ["ADOPTED"]] if os.environ.get("ADOPTED") else []
        _run("cluster", "--seed", "build.json", log=logs / f"{t}_seed.log")
    print(f"=== {occ}: {mode} build" + (f" (keeping statements from: {std[1]})" if std else ""))
    verify()

    for c in range(1, int(os.environ.get("MAX_ROUNDS", "3")) + 1):
        def log(step):
            return logs / f"{t}_r{c}_{step}.log"

        # 1 clustering; stop once a round after the first founds nothing new
        out = _run("cluster", "--over-pool", "build.json", log=log("cluster"))
        snap("cluster")
        m = re.search(r"-> (\d+) new cluster", out)
        new = int(m.group(1)) if m else 0
        if new == 0 and c != 1:
            print(f"  round {c}: cluster proposed no new task — done")
            break
        # 2 synthesis: statements, then the one-duty check (split), then statements for the parts it split off
        _run("synthesize", "--in", "build.json", "--out", "build.json", *std, log=log("synthesize"))
        _run("split", "--in", "build.json", "--out", f"verdicts/round{c}_split.json",
             "--apply", f"handoffs/round{c}_split.json", log=log("split"))
        _run("synthesize", "--in", f"handoffs/round{c}_split.json", "--out", "build.json", *std,
             log=log("synthesize_parts"))
        snap("synthesize")
        # 3 overlap resolution: merges, then rewordings
        _run("overlap", "--in", "build.json", "--out", f"verdicts/round{c}_overlap.json",
             "--apply", f"handoffs/round{c}_merged.json", "--sharpen", "build.json", *std, log=log("overlap"))
        snap("overlap")
        # 4 coverage checking: refile, and release what nothing covers
        out = _run("coverage", "--in", "build.json", "--out", f"verdicts/round{c}_coverage.json",
                   "--apply", "build.json", log=log("coverage"))
        snap("coverage")
        verify()
        refiled = "; ".join(ln.split("  →")[0] for ln in out.splitlines() if "REFILED" in ln)
        print(f"  round {c}: {new} new task(s) proposed; {refiled}")
    verify()
    print(f"=== {occ} {mode} BUILD DONE (build.json; diff against final.json, adopt by copying) ===")
    return build


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        sys.exit(__doc__)
    run_chain(sys.argv[1])


if __name__ == "__main__":
    main()
