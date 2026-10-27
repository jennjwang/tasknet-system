#!/usr/bin/env python3
"""split — split any cluster whose statement holds two duties (the end of each round's synthesis step).

    python -m tasknet.aggregation.split [--in build.json] [--out verdicts/split.json] [--apply handoffs/split.json]

Input:  a build artifact with statements.
Output: verdicts/split.json (KEEP or SPLIT per cluster) and, with --apply, a clusters artifact with the splits
        applied; every report lands in a part or back in the pool. synthesize then writes statements for the
        new parts.

For each cluster the one_duty judge (prompts/aggregation/consolidate/one_duty.txt) sees the statement and its
reports and, if it reads as two duties ("fused"), proposes the parts and their members. decide_split() only accepts a
split if those parts really partition the reports and each published part clears the worker floor.

Past verdicts: every verdict is saved in history/split_verdicts.json (keyed by a hash of the statement, with
its members), and a cluster whose member set already has a verdict reuses it instead of being judged again,
even after its statement was reworded. The model cache alone would not do that: it is keyed on the exact
statement text. A cluster whose statement the writer flagged as fused (its `form_flag`, shown as ⚑) is judged
again once, and `reopened_on` on its saved verdict stops that happening every build.
"""
import argparse
import collections
import json
import os
from concurrent.futures import ThreadPoolExecutor
from tasknet import engine
from tasknet import judges
from tasknet.aggregation import cluster, identity

PARALLEL_CALLS = int(os.environ.get("PARALLEL_CALLS", 8))        # model calls run at once (threads)
REOPEN_CONF = float(os.environ.get("WRITING_GATE_CONF", "0.75"))   # same bar synthesize uses for a flag


def fusion_flag(row):
    """The cluster's fusion flag from synthesize's writing check (`form_flag`, ⚑), if it is confident
    enough to act on; else None."""
    fl = row.get("form_flag") or {}
    if fl.get("principle") == "fusion" and float(fl.get("confidence") or 0.0) >= REOPEN_CONF:
        return fl
    return None


def fresh_or_replayed(core, past):
    """Sort `core` by its past verdicts. -> (fresh, replayed): clusters to judge now, and (cluster, past
    verdict) pairs whose verdict is reused.

    A past verdict is matched by member set first (the verdict is about the activities, not the wording; the
    newest wins), else by statement text, but only for old rows that recorded no members. A cluster with a
    past verdict is still judged again, once, if synthesize flagged its statement as fused (`reopened_on` on
    the saved verdict stops that repeating every build)."""
    by_mem = {}
    for rec in past.values():                       # insertion order = age, oldest first
        if rec.get("members"):
            by_mem[identity.member_key(rec["members"])] = rec
    fresh, replayed = [], []
    for c in core:
        rec = by_mem.get(identity.member_key(c.get("members") or []))
        if rec is None:
            rec = past.get(engine.sha256(c["statement"]))   # `is None`, not falsy: a verdict may be {}
            if rec is not None and rec.get("members"):
                rec = None                              # same wording but different activities: judge again
        if rec is None or (fusion_flag(c) and rec.get("reopened_on") != "fusion"):
            fresh.append(c)
        else:
            replayed.append((c, rec))
    return fresh, replayed


def decide_split(row, report_workers):
    """Turn one_duty's "fused" verdict into KEEP or SPLIT. -> (verdict, parts, basis)

    Each proposed part's members are matched to the cluster's reports. The cluster is kept if the parts don't
    really partition the reports (no parts, no members, or too many reports claimed by several parts). Otherwise
    it splits, and `basis` says how well the parts are supported by workers:
      exclusive-workers   two or more parts each have MIN_WORKERS workers who do only that part
      shared-workers      two or more parts clear the floor, but their workers overlap
      one-supported-part  only one part clears the floor; the rest of its reports go to the pool
      no-supported-part   no part clears the floor; all its reports go to the pool"""
    def text(o):                                        # a part is a dict, or a bare string in old verdicts
        return o.get("statement") if isinstance(o, dict) else str(o)
    rawouts = [o for o in row["outcomes"] if text(o).strip()]
    if len(rawouts) < 2:
        return "keep", [], "no-parts-proposed"
    if not any(isinstance(o, dict) and o.get("members") for o in rawouts):
        return "keep", [], "no-membership"

    def _canon(t):                                      # compare reports ignoring case, spacing, final period
        return " ".join(str(t).split()).casefold().rstrip(".")
    by_canon = {_canon(a): a for a in row["members"]}
    claims = {}                                         # report -> [indices of the parts that list it]
    for i, o in enumerate(rawouts):
        for m in (o.get("members") or [] if isinstance(o, dict) else []):
            a = by_canon.get(_canon(m))                 # members that match no report are ignored
            if a is not None and i not in claims.setdefault(a, []):
                claims[a].append(i)

    # each report goes to the one part that claims it; reports claimed by several parts, or by none, are set aside
    assign = {i: [] for i in range(len(rawouts))}
    unplaced, shared, shared_hits = [], [], {}
    for report in row["members"]:
        hs = claims.get(report, [])
        if len(hs) == 1:
            assign[hs[0]].append(report)
        elif hs:
            shared.append(report)
            shared_hits[report] = hs
        else:
            unplaced.append(report)
    row["_shared_hits"] = shared_hits

    parts = []
    for i, o in enumerate(rawouts):
        workers = set().union(*(report_workers.get(a, set()) for a in assign[i]))
        parts.append({"statement": text(o), "members": sorted(assign[i]), "n_members": len(assign[i]),
                      "prevalence": len(workers), "publishable": len(workers) >= cluster.MIN_WORKERS,
                      "_workers": workers})
    for label, extra in (("(described by MORE THAN ONE proposed part — the parts overlap)", shared),
                         ("(not covered by any proposed part)", unplaced)):
        if extra:
            parts.append({"statement": label, "members": sorted(extra), "n_members": len(extra),
                          "prevalence": None, "publishable": False, "_workers": set()})
    for part in parts:                                  # workers who appear in this part and no other
        others = set().union(*[q["_workers"] for q in parts if q is not part]) if len(parts) > 1 else set()
        part["exclusive"] = len(part["_workers"] - others)
        if not part["statement"].startswith("(") and part["n_members"] < 2:
            part["publishable"] = False       # a one-report part never publishes, however many workers it has
            part["one_outlier"] = True

    with_reports = [p for p in parts if not p["statement"].startswith("(") and p["n_members"] > 0]
    if len(shared) * 2 >= len(row["members"]) or len(with_reports) < 2:
        return "keep", parts, "partition-failed"       # the reports don't divide the way the verdict claims
    real = [p for p in parts if p["publishable"]]
    if len(real) >= 2:
        strong = [p for p in real if p["exclusive"] >= cluster.MIN_WORKERS]
        return "split", parts, ("exclusive-workers" if len(strong) >= 2 else "shared-workers")
    return "split", parts, ("one-supported-part" if real else "no-supported-part")


def _verdict_row(c):
    """The fields every verdict row copies from its cluster. `sid` must be included, or apply_splits gives
    the kept cluster no id and the split parts no parent."""
    return {"sid": c.get("sid"), "statement": c["statement"], "members": sorted(c.get("members") or []),
            "written_for": c.get("written_for"), "written_with": c.get("written_with"),
            "n_members": len(c.get("members") or []), "prevalence": c.get("prevalence")}


def apply_splits(rows, source_meta, source_pool=None, source_name="build.json"):
    """Build the clusters artifact with the SPLIT verdicts applied. -> (artifact, log)

    A kept cluster passes through unchanged. A split cluster is replaced by its publishable parts; reports
    claimed by several parts go to the largest of them, and reports in no publishable part are added to
    `dropped_atoms` (part of the pool). `source_pool` is the input's carried keys."""
    fams, log = [], {"split": [], "dropped": []}
    AW = cluster.report_workers()

    def _cluster_row(head, members, stamps=None, born_of=None):
        """One cluster row. A kept cluster (`stamps` = its verdict row) keeps its sid, its "already written"
        stamps (written_for / written_with) and its flag. A split part (`born_of` = the row it was cut from)
        gets a new sid and no stamps, so synthesize writes its statement fresh."""
        mem = sorted(set(members))
        w = sorted({x for m in mem for x in AW.get(m, set())})
        f = {"head": head, "members": mem, "workers": w, "prevalence": len(w)}
        if born_of is not None:
            f["sid"], f["sid_split_from"] = identity.new_cluster_id(), born_of.get("sid")
        elif stamps and stamps.get("sid"):
            f["sid"] = stamps["sid"]
        if stamps and stamps.get("written_for") and stamps.get("written_with"):
            f["written_for"], f["written_with"] = stamps["written_for"], stamps["written_with"]
        if stamps and stamps.get("form_flag"):
            f["form_flag"] = stamps["form_flag"]        # keep the flag, so the cluster is judged again
        return f

    for r in rows:
        if r["verdict"] != "split":
            fams.append(_cluster_row(r["statement"], r["members"], stamps=r))
            continue
        pubs = [p for p in r["parts"] if p.get("publishable")]
        sh = r.get("_shared_hits") or {}
        idx = {id(p): i for i, p in enumerate(r["parts"])}
        for report, his in sh.items():                    # a report claimed by several parts joins the largest
            cand = [p for p in pubs if idx[id(p)] in his]
            if cand:
                max(cand, key=lambda p: (len(p["members"]), -idx[id(p)]))["members"].append(report)
        entry = {"was": r["statement"], "n_members": r["n_members"], "basis": r["basis"], "parts": []}
        for p in pubs:
            f = _cluster_row(p["statement"], p["members"], born_of=r)
            f["split_of"] = r["statement"]              # provenance: the fused statement it was cut from
            fams.append(f)
            entry["parts"].append({"head": f["head"], "n_members": len(f["members"]),
                                   "prevalence": f["prevalence"]})
        filed_here = {m for pp in pubs for m in pp["members"]}
        orph = sorted(set(r["members"]) - filed_here)
        entry["dropped"] = orph
        log["dropped"].extend(f"[{r['statement'][:40]}...] {m}" for m in sorted(orph))
        log["split"].append(entry)
    dropped = sorted({m for e in log["split"] for m in e.get("dropped") or ()})
    left = source_pool or {}
    return {"meta": {**source_meta, "defused_from": source_name,
                     "n_split": len(log["split"]), "n_dropped": len(log["dropped"])},
            "core": fams,
            **left, "dropped_atoms": sorted(set(dropped) | set(left.get("dropped_atoms", ()))),
            "dropped_log": log["dropped"]}, log


def main():
    engine.set_stage("split")                       # use this stage's own API key, if one is set
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", default="build.json", help="a statements artifact")
    ap.add_argument("--out", default="verdicts/split.json", help="filename under results/aggregation/<occ>/")
    ap.add_argument("--apply", metavar="FILE", default=None,
                    help="write a clusters artifact with the SPLIT verdicts applied, e.g. handoffs/split.json "
                         "(the input is untouched); then synthesize --in that file writes the new statements")
    args = ap.parse_args()

    build = identity.read_artifact(args.inp, "run synthesize.py first.")
    core = build["core"]
    report_workers = cluster.report_workers()

    past_path = identity.history_path("split_verdicts.json")
    past = json.loads(past_path.read_text()) if past_path.exists() else {}
    if not isinstance(past, dict):
        past = {}
    fresh, replayed = fresh_or_replayed(core, past)
    n_reopen = sum(1 for c in fresh if engine.sha256(c["statement"]) in past)
    print(f"  {len(fresh)} to judge, {len(replayed)} reuse a past verdict"
          + (f", {n_reopen} re-opened by a ⚑ fusion flag" if n_reopen else ""))

    def run(c):                                     # judge one cluster
        v = judges.one_duty(c["statement"], c.get("members") or [])
        return {**_verdict_row(c), "fused": v["one_duty"] is False, "outcomes": v["outcomes"], "reason": v["reason"],
                "form_flag": c.get("form_flag"), "reopened": engine.sha256(c["statement"]) in past}

    judges.one_duty_prefill(                        # Batch API pre-fill, when BATCH is on
        [(c["statement"], c.get("members") or []) for c in fresh])
    with ThreadPoolExecutor(PARALLEL_CALLS) as ex:
        rows = list(ex.map(run, fresh))
    for r in rows:
        r["verdict"], r["parts"], r["basis"] = decide_split(r, report_workers) if r["fused"] else ("keep", [], "")
    # clusters with a past verdict replay it, with no model call; `_shared_hits` must come too, because
    # apply_splits reassigns shared reports through it
    for c, rec in replayed:
        rows.append({**_verdict_row(c), "fused": rec["fused"], "outcomes": rec["outcomes"], "reason": rec["reason"],
                     "verdict": rec["verdict"], "parts": rec["parts"], "basis": rec["basis"],
                     "_shared_hits": rec.get("_shared_hits") or {}, "standing": True})

    counts = collections.Counter(r["verdict"] for r in rows)
    print(f"{engine.occupation()}: {len(rows)} clusters · "
          + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"   ({judges.ONEDUTY_MODEL})")
    for r in rows:
        if r["verdict"] == "split":
            print(f"  SPLIT [{r['basis']}] {r['statement'][:90]}")

    for r in rows:
        for part in r["parts"]:
            part.pop("_workers", None)                  # a set (not JSON), only needed inside decide_split()
        if not r.get("standing"):
            # save new verdicts, with their statement and members, for later rounds and builds
            rec = past[engine.sha256(r["statement"])] = {
                k: r.get(k) for k in ("fused", "outcomes", "reason", "verdict", "parts", "basis",
                                      "_shared_hits", "statement", "members")}
            if r.get("reopened") or fusion_flag(r):
                rec["reopened_on"] = "fusion"
    past_path.write_text(json.dumps(past, indent=1))

    if args.apply:
        art, log = apply_splits(rows, build.get("meta", {}), cluster.carry(build), source_name=args.inp)
        dest = identity.write_artifact(args.apply, art)
        print(f"  APPLIED {len(log['split'])} splits: {len(rows)} clusters -> {len(art['core'])} "
              f"({len(log['dropped'])} reports in no part, to the pool)  → {dest}")

    dest = identity.write_artifact(args.out, {
        "meta": {**build.get("meta", {}), "source": args.inp, "model": judges.ONEDUTY_MODEL,
                 "tag": judges.ONEDUTY_TAG, "min_workers": cluster.MIN_WORKERS},
        "split": [r for r in rows if r["verdict"] == "split"],
        "keep": [{"statement": r["statement"], "basis": r["basis"], "n_members": r["n_members"],
                  "prevalence": r["prevalence"], "parts": r["parts"], "reason": r["reason"]}
                 if r["basis"] else r["statement"]
                 for r in rows if r["verdict"] == "keep"]})
    print(f"→ {dest}")


if __name__ == "__main__":
    main()
