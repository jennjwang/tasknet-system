#!/usr/bin/env python3
"""coverage — re-check where every report is filed, and refile what it can from the pool (step 4 of each round).

    python -m tasknet.aggregation.coverage [--in build.json] [--out verdicts/coverage.json] [--apply build.json]

Input:  a build artifact with statements.
Output: verdicts/coverage.json, listing the "off-home" reports (their own statement no longer covers them) and
        the "missing" ones (no statement covers them); and, with --apply, the artifact with reports moved.

With --apply, the disposer (prompts/aggregation/dispose.txt) sees every published statement and decides
each off-home report and each pool report: FILE it under a statement, or leave/release it to the pool. A report only
leaves its cluster when the coverage check and the disposer both say so, and a move needs two agreeing
disposer draws. Clusters then below the worker floor are dissolved into the pool. This stage never creates a
cluster; the pool it leaves is what cluster groups next round.
"""
import argparse
import collections
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from tasknet import engine
from tasknet import judges
from tasknet.aggregation import cluster, identity

PARALLEL_CALLS = int(os.environ.get("PARALLEL_CALLS", 12))        # model calls run at once (threads)
TOPK = int(os.environ.get("COVER_TOPK", "0"))     # statements to try per report; 0 = len(list) / 6, min 3
MODEL_DISPOSE = os.environ.get("DISPOSE_MODEL", "gpt-5.4-mini")       # the disposer


def _judge_coverage(jobs):
    """Ask the coverage judge (judges.covers) about each (report, statement) pair in `jobs`.
    -> {report: [statements that cover it]}"""
    jobs = sorted(set(jobs))
    if len(jobs) > 500:
        print(f"  coverage scan: {len(jobs):,} (report, label) pairs", file=sys.stderr, flush=True)
    judges.covers_prefill(jobs)
    with ThreadPoolExecutor(max_workers=judges.PARALLEL_CALLS) as ex:
        hits = list(ex.map(lambda p: bool(judges.covers(*p)), jobs))
    out = collections.defaultdict(list)
    for (a, L), ok in zip(jobs, hits):
        if ok:
            out[a].append(L)
    return out


def uncovered(core, unfiled=()):
    """Find the reports whose own statement doesn't cover them. -> (missing, off_home)

    `unfiled` (the pool) counts as having no statement. `off_home` maps each such report to (its statement or
    None, [other statements that do cover it]); `missing` lists those that nothing covers. A report that fails
    its own statement is tried against its TOPK nearest statements by embedding."""
    labels = [c["statement"] for c in core]
    owner = {m: c["statement"] for c in core for m in (c.get("members") or [])}
    for a in unfiled:
        owner.setdefault(a, None)
    reports = sorted(owner)
    own_ok = _judge_coverage([(a, owner[a]) for a in reports if owner.get(a) in labels])
    failed = [a for a in reports if owner[a] not in own_ok.get(a, [])]
    # second pass: each failed report against its nearest statements, skipping its own (it already said no)
    near = []
    fs, ls = sorted(set(failed)), sorted(set(labels))
    if fs and ls:
        k = TOPK if TOPK > 0 else max(3, math.ceil(len(ls) / 6))
        V = judges.embed(fs + ls)
        for a in fs:
            ranked = sorted(ls, key=lambda x: -float(V[a] @ V[x]))[:k]
            own = owner.get(a)
            if own and own in ls and own not in ranked:
                ranked.append(own)
            near += [(a, L) for L in ranked]
    rest = _judge_coverage([(a, L) for a, L in near if L != owner.get(a)])
    missing, off_home = [], {}
    for a in failed:
        hits = [L for L in rest.get(a, []) if L != owner.get(a)]
        off_home[a] = (owner.get(a), hits)
        if not hits:
            missing.append(a)
    return missing, off_home


def _dispose(report, tasks, home=None, siblings=()):
    """Ask the disposer where `report` belongs, given the numbered statement list `tasks`. -> {"fate", "task",
    ...}. `home` is the statement it is filed under now and `siblings` the other reports there (None and ()
    for a pool report).

    Any change needs agreement: if the first answer would move or release the report (or file a pool report),
    a second, independent answer (its own cache entry) must say the same, or the report stays where it is.
    Errors also leave it in place."""
    where = (f'This activity is currently filed under: "{home}"' if home
             else "This activity is in the pile — it is filed under no task at all, so there is nothing "
                  "to leave it in and FILE must earn its place on the statement's own words.")
    sib = [m for m in siblings if m != report][:12]
    sib_txt = ("The other activities already filed under that task:\n"
               + "\n".join(f"  - {m}" for m in sib)) if sib else "(no other activities are filed there)"
    instr = engine.load_prompt("dispose", occupation=engine.occupation(), tasks=tasks,
                               atom="", home="", home_members="")
    prompt = engine.load_prompt("dispose", occupation=engine.occupation(), tasks=tasks, atom=report,
                                home=where, home_members=sib_txt)
    home_n = next((int(n) for n, _, rest in (ln.partition(". ") for ln in tasks.splitlines())
                   if rest.strip() == (home or "").strip()), None)     # its statement's number, if filed

    def ask(draw):
        engine.prewarm(instr, MODEL_DISPOSE)            # cache the prompt's fixed part with the provider
        key = engine.sha256("DISPOSE1" + MODEL_DISPOSE + engine.sha256(prompt)
                            + (f"|D{draw}" if draw > 1 else ""))
        raw = engine.memo("found_dispose", key,
                          lambda: engine.chat(prompt, MODEL_DISPOSE, max_out=300, json_mode=True,
                                              cache_prefix=instr))
        return json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))

    try:
        first = ask(1)
    except Exception as e:                              # noqa: BLE001 - an unanswered report stays where it is
        return {"fate": "ERR", "why": str(e)[:60]}
    fate = str(first.get("fate", "")).upper()
    moves = (bool(home) if fate in ("UNFILED", "LEAVE", "RELEASE")       # only a filed report can be released
             else first.get("task") != home_n if fate.startswith("FILE") else False)
    if not moves:
        return first
    try:
        second = ask(2)
    except Exception as e:                              # noqa: BLE001
        return {"fate": "ERR", "why": f"second draw: {str(e)[:44]}"}
    if fate == str(second.get("fate", "")).upper() and first.get("task") == second.get("task"):
        return first
    if home:
        return {"fate": "FILE", "task": home_n, "why": "no-concurrence: draws disagreed"}
    return {"fate": "LEAVE", "why": "no-concurrence: draws disagreed"}


def refile(art):
    """Re-place the off-home and pool reports through the disposer. Mutates and returns `art`, with `refile_log`.

    FILE moves the report to that statement (or keeps it, if it is already there); UNFILED / LEAVE releases a
    filed report to the pool and leaves a pool report where it is. A cluster whose members change loses its
    `written_for` stamp, so synthesize checks its statement again. Clusters then below MIN_WORKERS are
    dissolved into the pool."""
    core = art["core"]
    for f in core:
        f.setdefault("statement", f.get("head"))
    filed = {m for f in core for m in f["members"]}
    pool = sorted({a for k in cluster.POOL_KEYS for a in art.get(k) or []} - filed)
    log = {"pile": len(pool), "off_home": 0, "filed": 0, "moved": 0, "released": 0, "fates": {}}
    if not core:
        art["refile_log"] = log
        return art
    _, off_home = uncovered(core, pool)
    log["off_home"] = sum(1 for a, (own, _) in off_home.items() if own)
    owner = {m: i for i, f in enumerate(core) for m in f["members"]}
    ask = sorted(set(pool) | {a for a, (own, _) in off_home.items() if own})
    tasks = "\n".join(f"{i + 1}. {f['statement']}" for i, f in enumerate(core))

    def dispose(a):                                     # the disposer's verdict for one report
        home = core[owner[a]] if a in owner else None
        return _dispose(a, tasks, home["statement"] if home else None, home["members"] if home else ())
    with ThreadPoolExecutor(PARALLEL_CALLS) as ex:
        fates = dict(zip(ask, ex.map(dispose, ask)))
    consumed, released = set(), []
    for report, v in fates.items():
        log["fates"][report] = v.get("fate")
        t = v.get("task")
        home = owner.get(report)
        if v.get("fate") == "FILE" and isinstance(t, int) and 1 <= t <= len(core):
            if home == t - 1:
                continue                                # the disposer keeps it where it is
            if home is not None:
                core[home]["members"] = sorted(set(core[home]["members"]) - {report})
                core[home].pop("written_for", None)
                log["moved"] += 1
            else:
                log["filed"] += 1
            core[t - 1]["members"] = sorted(set(core[t - 1]["members"]) | {report})
            core[t - 1].pop("written_for", None)
            consumed.add(report)
        elif home is not None and v.get("fate") in ("UNFILED", "LEAVE"):
            core[home]["members"] = sorted(set(core[home]["members"]) - {report})
            core[home].pop("written_for", None)
            released.append(report)
    for k in cluster.POOL_KEYS:
        if art.get(k):
            art[k] = sorted(set(art[k]) - consumed)
    if released:
        art["released"] = sorted(set(art.get("released") or []) | set(released))
        log["released"] = len(released)
    # the worker floor: a cluster whose reports no longer come from MIN_WORKERS workers goes back to the pool
    AW = cluster.report_workers()
    keep, demoted = [], []
    for f in core:
        if not f["members"]:
            continue                                    # every report was released: drop the cluster
        if len({w for m in f["members"] for w in (AW.get(m) or ())}) >= cluster.MIN_WORKERS:
            keep.append(f)
        else:
            demoted.append(f)
    if demoted:
        art["released"] = sorted(set(art.get("released") or []) | {m for f in demoted for m in f["members"]})
        log["demoted"] = [f.get("statement") or f.get("head") for f in demoted]
    art["core"] = keep
    cluster.recount_workers(art["core"])
    art["refile_log"] = log
    return art


def main():
    engine.set_stage("coverage")                       # use this stage's own API key, if one is set
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", default="build.json", help="a statements artifact")
    ap.add_argument("--out", default="verdicts/coverage.json")
    ap.add_argument("--apply", metavar="FILE", default=None,
                    help="REFILE: re-place off-home reports and the pool through the disposer and write the "
                         "artifact here (e.g. build.json); without it, this stage only reports")
    args = ap.parse_args()

    build = identity.read_artifact(args.inp)
    core = build["core"]

    seen = {m for c in core for m in (c.get("members") or [])}
    leftover = {k: set(build.get(k) or []) - seen for k in cluster.POOL_KEYS}
    unfiled = sorted(set().union(*leftover.values()))
    n_reports = len(seen) + len(unfiled)
    missing, off_home = uncovered(core, unfiled)
    print(f"{engine.occupation()}: {len(core)} statements · {n_reports} reports ({len(unfiled)} in the pool) · "
          f"{len(off_home)} not covered by their own statement, {len(missing)} covered by none")

    dest = identity.write_artifact(args.out, {
        "meta": {**build.get("meta", {}), "source": args.inp, "n_atoms": n_reports,
                 "judges": {"covers": judges.COVERS_MODEL, "dispose": MODEL_DISPOSE},
                 "topk": TOPK or "len/6 (min 3)"},
        "missing": missing,
        "off_home": {a: {"owner": o, "covered_by": h} for a, (o, h) in off_home.items()}})
    print(f"→ {dest}")
    if args.apply:
        art = refile(build)
        lg = art["refile_log"]
        ap_dest = identity.write_artifact(args.apply, art, indent=1)
        print(f"  REFILED: pool {lg['pile']} + off-home {lg['off_home']} -> filed {lg['filed']}, "
              f"moved {lg['moved']}, released {lg['released']}, demoted {len(lg.get('demoted') or [])}  → {ap_dest}")


if __name__ == "__main__":
    main()
