"""chain — one loop builds an occupation: cluster -> statements -> split -> statements -> merge/sharpen -> split
again on what the merges wrote (chain.recheck_merged) -> refile,
REPEATED UNTIL CLUSTER PROPOSES NO NEW TASK THAT MEETS THE CRITERIA (the source floor) — the only stopping
condition; cycle 1 always runs in full — capped at chain.max_cycles.

    build = chain.build(ctx)                                   # FRESH: an empty core, every atom in the pile
    build = chain.build(ctx, standing=final)                   # AMEND: start from the adopted list, which is also
                                                               #   the continuity registry
    build = chain.build(ctx, standing=final, fresh=True)       # fresh draw, adopted list as the registry only

Writes workdir/build.json after every stage, a copy of the list as each stage left it under stages/, each
cycle's verdicts under verdicts/, and a `build.done` marker at the end. Never touches final.json: adopting a
build is an explicit copy by the caller. Raises on a failed invariant.
"""
from __future__ import annotations

import copy
import json
import sys
import time

from .. import families
from ..lib import atomic_write
from . import cluster, fusion, overlap, refile, synthesize, verify


def _write(ctx, role, name, obj):
    if ctx.workdir is None:
        return
    atomic_write(ctx.path(role, name) if role else ctx.workdir / name, obj)


def _checked(ctx, build, stage):
    problems = verify.invariants(ctx, build)
    if problems:
        for p in problems:
            print(f"  FAIL  {stage}: {p}", file=sys.stderr)
        raise RuntimeError(f"invariants failed after {stage}: {problems[0]}")
    print(f"  ok  {stage:12} {verify.summary(build)}")
    return build


def build(ctx, standing=None, fresh=False, max_cycles=None, on_stage=None) -> dict:
    """Build the list for ctx.atoms. `standing` is an adopted artifact (final.json's dict) or None."""
    max_cycles = int(max_cycles or ctx.config.get("chain.max_cycles", 3))
    reg = families.registry(standing) if standing else None
    if standing and not fresh:
        mode, art = "AMEND", copy.deepcopy(standing)
    else:
        mode, art = "FRESH", cluster.seed(ctx)
    print(f"=== {ctx.occupation}: {mode} build" + (" (continuity from the adopted list)" if reg else ""))
    ctx.trace.clear()
    _write(ctx, None, "build.json", art)
    _checked(ctx, art, "seed")

    def snap(stage, cycle):
        _write(ctx, None, "build.json", art)
        _write(ctx, "stages", f"{stage}.json", art)
        if on_stage:
            on_stage(stage, cycle, art)
    for cycle in range(1, max_cycles + 1):
        art = cluster.propose(ctx, art)
        n_new = len(art["draw_pile_log"]["founded"])
        for h in art["draw_pile_log"]["founded"]:
            print(f"  NEW [{h[:90]}]")
        snap("cluster", cycle)
        if n_new == 0 and cycle != 1:
            print(f"  cycle {cycle}: cluster proposed no new task — done")
            break
        art = synthesize.statements(ctx, art, reg)
        snap("synthesize", cycle)
        art, fused = fusion.split(ctx, art)
        _write(ctx, "verdicts", f"cycle{cycle}_fused.json", art.pop("fusion_verdicts", {}))
        _write(ctx, "handoffs", f"cycle{cycle}_defused.json", art)
        art = synthesize.statements(ctx, art, reg)
        art, ov = overlap.resolve(ctx, art, reg)
        _write(ctx, "verdicts", f"cycle{cycle}_overlap.json", ov)
        snap("consolidate", cycle)
        if ctx.config.get("chain.recheck_merged", True):
            # the one-duty check again: overlap's merges wrote statements the check above never saw. Families
            # already judged replay their verdict from the ledger (by member set), so only merges are asked.
            art, _ = fusion.split(ctx, art, source_name="consolidate")
            _write(ctx, "verdicts", f"cycle{cycle}_fused_merged.json", art.pop("fusion_verdicts", {}))
            art = synthesize.statements(ctx, art, reg)
            snap("recheck", cycle)
        art, rf = refile.place(ctx, art)
        _write(ctx, "verdicts", f"cycle{cycle}_coverage.json", rf)
        snap("refile", cycle)
        _checked(ctx, art, f"cycle {cycle}")
        print(f"  cycle {cycle}: {n_new} new task(s) proposed")
    art["meta"]["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    art["meta"]["mode"] = mode
    art["docket"] = list(ctx.docket)
    _checked(ctx, art, "final")
    _write(ctx, None, "build.json", art)
    if ctx.workdir is not None:
        (ctx.workdir / "build.done").write_text(json.dumps({"occupation": ctx.occupation, "at": art["meta"]["built_at"],
                                                             "families": len(art["core"])}) + "\n")
    print(f"=== {ctx.occupation} {mode} BUILD DONE ({len(art['core'])} statements)")
    return art
