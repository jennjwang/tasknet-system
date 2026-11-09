"""fusion — the split hearing: is each statement ONE duty over its members?

    build, verdicts = fusion.split(ctx, build)

The duties judge sees the statement and its atoms and names the parts and their members if it is fused. The
seen-ledger (workdir/ledgers/defuse_seen.json) replays standing verdicts BY MEMBER SET: a family whose
activities did not change keeps its verdict however the prose was reworded. A row carrying the writer's fusion
⚑ is re-opened once. `decide` partitions by the ATOMS (exact text) and applies the source floor per part —
keep/partition-failed when the atoms do not split as claimed, else a split with a basis (exclusive-sources,
shared-sources, one-supported-part, no-supported-part). `concurrence` (chain.concurrence.split, off by default)
demotes a one-signal split. `apply_splits` turns applied splits into families; every atom lands in a part or
the pile. NO HUMAN RULING GATES THIS: a ruling corrects the deliverable, never the machine.
"""
from __future__ import annotations

import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from .. import families
from ..lib import load_json, sha256
from . import blocks

REFUSAL_EXITS = {"still-rejected", "coverage-fell", "no-change"}


def fusion_flag(row, conf=0.75):
    fl = row.get("form_flag") or {}
    if fl.get("principle") == "fusion" and float(fl.get("confidence") or 0.0) >= conf:
        return fl
    return None


def reopen(row, rec, conf=0.75):
    """A GATE-REFUSED FUSION REPAIR RE-OPENS THE CASE: a row carrying the gate's ⚑ is heard fresh ONCE."""
    return bool(fusion_flag(row, conf)) and (rec or {}).get("reopened_on") != "fusion"


def form_corroboration(row, conf=0.75):
    fl = fusion_flag(row, conf)
    return [f"GATE 2 refused a fusion repair (⚑ fusion {fl.get('confidence')}): {(fl.get('trigger') or '')[:80]}"] if fl else []


def by_members(seen):
    """member-set key -> ledger record, for rows that recorded their members (the newest wins)."""
    out = {}
    for rec in seen.values():
        mem = rec.get("members")
        if mem:
            out[families.key(mem)] = rec
    return out


def lookup(c, seen, by_mem):
    """The ledger record for one family, by member set OR (pre-2026-09 rows) statement text. -> record | None."""
    rec = by_mem.get(families.key(c.get("members") or []))
    if rec is not None:
        return rec
    rec = seen.get(sha256(c["statement"]))
    if rec is not None and rec.get("members"):
        return None
    return rec


def partition(core, seen, conf=0.75):
    """-> (fresh, standing): families to judge now vs. ledger replays."""
    by_mem = by_members(seen)
    fresh, standing = [], []
    for c in core:
        rec = lookup(c, seen, by_mem)
        (fresh if rec is None or reopen(c, rec, conf) else standing).append(c)
    return fresh, standing


def decide(row, atom_sources, min_sources=2):
    """KEEP or SPLIT a fused verdict, by whether the proposed parts actually partition the members and publish
    under the source floor. -> (verdict, parts, basis, feedback)."""
    rawouts = [o for o in row["outcomes"] if (o.get("statement") if isinstance(o, dict) else str(o)).strip()]
    outs = [(o.get("statement") if isinstance(o, dict) else str(o)) for o in rawouts]
    if len(outs) < 2:
        return "keep", [], "no-parts-proposed", (
            "The reviewer found this fused but proposed no separable parts, so there is no evidence about "
            f"where the seam is. Grounds: {row['reason']}")
    assign = {i: [] for i in range(len(outs))}
    unplaced, shared = [], []
    if not any(isinstance(o, dict) and o.get("members") for o in rawouts):
        return "keep", [], "no-membership", (
            "The reviewer found this fused but assigned no activities to its parts, so there is no evidence "
            f"about where the seam is. Grounds: {row['reason']}")

    def _canon(t):
        return " ".join(str(t).split()).casefold().rstrip(".")
    by_canon = {_canon(a): a for a in row["members"]}
    claims, ghosts = {}, 0
    for i, o in enumerate(rawouts):
        for m in (o.get("members") or [] if isinstance(o, dict) else []):
            a = by_canon.get(_canon(m))
            if a is None:
                ghosts += 1
            else:
                idx = claims.setdefault(a, [])
                if i not in idx:
                    idx.append(i)
    if ghosts:
        print(f"      ({ghosts} proposed member(s) did not join verbatim and were ignored)", file=sys.stderr)
    shared_hits = {}
    for atom in row["members"]:
        hs = claims.get(atom, [])
        if len(hs) == 1:
            assign[hs[0]].append(atom)
        elif hs:
            shared.append(atom)
            shared_hits[atom] = hs
        else:
            unplaced.append(atom)
    row["_shared_hits"] = shared_hits
    parts = []
    for i, o in enumerate(outs):
        atoms = assign[i]
        sources = set()
        for a in atoms:
            sources |= set(atom_sources.get(a, set()))
        parts.append({"statement": o, "members": sorted(atoms), "n_members": len(atoms), "prevalence": len(sources),
                      "publishable": len(sources) >= min_sources, "_sources": sources})
    for label, extra in (("(described by MORE THAN ONE proposed part — the parts overlap)", shared),
                         ("(not covered by any proposed part)", unplaced)):
        if extra:
            parts.append({"statement": label, "members": sorted(extra), "n_members": len(extra), "prevalence": None,
                          "publishable": False, "_sources": set()})
    for part in parts:
        others = set().union(*[q["_sources"] for q in parts if q is not part]) if len(parts) > 1 else set()
        part["exclusive"] = len(part["_sources"] - others)
    for part in parts:
        if not part["statement"].startswith("(") and part["n_members"] < 2:
            part["publishable"] = False
            part["one_outlier"] = True
    real = [p for p in parts if p.get("publishable")]
    thin = [p for p in parts if not p.get("publishable") and not p["statement"].startswith("(")]
    named = [p for p in parts if not p["statement"].startswith("(")]

    def ev(p):
        return f"{p['n_members']} atoms / {p['prevalence'] or 0} sources"
    with_atoms = [p for p in named if p["n_members"] > 0]
    if len(shared) * 2 >= len(row["members"]) or len(with_atoms) < 2:
        return "keep", parts, "partition-failed", (
            f"one_duty proposed {len(named)} parts, but {len(shared)} of {len(row['members'])} activities are "
            f"described by more than one of them and only {len(with_atoms)} part(s) hold any activity "
            f"uniquely. The members do not partition the way the verdict claims, so it carries no evidence.")
    if len(real) >= 2:
        strong = [p for p in real if p["exclusive"] >= min_sources]
        if len(strong) >= 2:
            return "split", parts, "exclusive-sources", ""
        fb = ("Two or more parts clear the source floor, but no part has "
              f"{min_sources} sources that do it and not the others — "
              + "; ".join(f'"{p["statement"][:70]}" ({ev(p)}, {p["exclusive"]} exclusive)' for p in real)
              + f". The family has {row['prevalence']} sources in total. Splits on this basis audit as sound as "
                f"exclusive-sources ones (22/25 vs 23/25). Reviewer's grounds: {row['reason']}")
        return "split", parts, "shared-sources", fb
    if len(real) == 1:
        fb = (f"Of the {row['n_members']} activities filed here, only {real[0]['n_members']} have the source "
              f"evidence to publish ({real[0]['prevalence']} sources). "
              + "".join(f'The part "{p["statement"][:90]}" rests on {ev(p)} — below the floor of '
                        f'{min_sources} sources, so its activities are released to the pile. ' for p in thin)
              + "The supported activities become the family.")
        return "split", parts, "one-supported-part", fb
    fb = ("No proposed part of this statement clears the source floor — "
          + "; ".join(f'"{p["statement"][:80]}" ({ev(p)})' for p in thin)
          + f". The family rests on {row['prevalence']} sources and no duty inside it does, so its activities "
            f"are released to the pile rather than published under a statement none of them supports.")
    return "split", parts, "no-supported-part", fb


def corroboration(statement, trace):
    """Independent signals that this family is fused, from the writer's own trace."""
    out = []
    for t in trace:
        if t.get("stage") != "quality_gate" or (t.get("kept") or t.get("draft")) != statement:
            continue
        pr, ex = t.get("principle"), t.get("exit")
        if ex in REFUSAL_EXITS:
            out.append(f"GATE 2 repair refused: {ex} on {pr}")
        if ex == "accepted" and pr == "clauses":
            out.append("GATE 2 promoted a method clause — check it did not make a verb list")
    return out


def concurrence(ctx, rows, second_draw=None):
    """Demote fresh one-signal SPLIT verdicts to KEEP `no-concurrence` (chain.concurrence.split). Mutates rows."""
    if not ctx.config.get("chain.concurrence.split", False):
        return rows
    second_draw = second_draw or (lambda r: ctx.judges.duties(r["statement"], r["members"], draw=2))
    for r in rows:
        if r.get("verdict") != "split" or r.get("standing"):
            continue
        if r.get("basis") in ("exclusive-sources", "exclusive-workers") or r.get("corroboration"):
            continue
        v = second_draw(r)
        if v.get("one_duty"):
            r["verdict"], r["basis"] = "keep", "no-concurrence"
            r["feedback"] = ("DOCKET: fused on one draw; a second independent draw found one duty and no "
                             "corroborating signal exists (exclusive-sources, refused repair, standing verdict). "
                             "A structural change needs two signals — recorded, not applied. " + (r.get("feedback") or ""))
        else:
            r["basis"] = (r.get("basis") or "") + "+concurrence"
    return rows


def apply_splits(ctx, rows, source_meta, source_leftovers=None, source_name="build.json"):
    """Turn the SPLIT verdicts into a families artifact. -> (artifact, log)."""
    fams, log = [], {"split": [], "dropped": []}
    W = ctx.atoms.sources
    for r in rows:
        def _fam(head, members, stamps=None, born_of=None):
            mem = sorted(set(members))
            w = sorted({x for m in mem for x in W.get(m, set())})
            f = {"head": head, "members": mem, "sources": w, "prevalence": len(w)}
            if born_of is not None:
                f["sid"], f["sid_split_from"] = families.new_sid(), born_of.get("sid")
            elif stamps and stamps.get("sid"):
                f["sid"] = stamps["sid"]
            if stamps and stamps.get("written_for") and stamps.get("written_with"):
                f["written_for"], f["written_with"] = stamps["written_for"], stamps["written_with"]
            if stamps and stamps.get("form_flag"):
                f["form_flag"] = stamps["form_flag"]
            return f
        if r["verdict"] != "split":
            fams.append(_fam(r["statement"], r["members"], stamps=r))
            continue
        pubs = [p for p in r["parts"] if p.get("publishable")]
        sh = r.get("_shared_hits") or {}
        idx = {id(p): i for i, p in enumerate(r["parts"])}
        for atom, his in sh.items():
            cand = [p for p in pubs if idx[id(p)] in his]
            if cand:
                max(cand, key=lambda p: (len(p["members"]), -idx[id(p)]))["members"].append(atom)
        # members the judge never saw (member_cap) are re-homed to the nearest part, not dropped
        filed_here = {m for pp in pubs for m in pp["members"]}
        unshown = set(r["members"]) - set(r.get("shown") or r["members"])
        rehome = [m for m in sorted(set(r["members"]) - filed_here) if m in unshown]
        if rehome and pubs:
            for m, i in blocks.assign_nearest(ctx, rehome, [p["members"] for p in pubs]).items():
                pubs[i]["members"].append(m)
        entry = {"was": r["statement"], "n_members": r["n_members"], "basis": r["basis"], "parts": []}
        for p in pubs:
            f = _fam(p["statement"], p["members"], born_of=r)
            f["split_of"] = r["statement"]
            fams.append(f)
            entry["parts"].append({"head": f["head"], "n_members": len(f["members"]), "prevalence": f["prevalence"]})
        filed_here = {m for pp in pubs for m in pp["members"]}
        orph = sorted(set(r["members"]) - filed_here)
        entry["dropped"] = orph
        log["dropped"].extend(f"[{r['statement'][:40]}...] {m}" for m in sorted(orph))
        log["split"].append(entry)
    dropped = sorted({m for e in log["split"] for m in e.get("dropped") or ()})
    left = source_leftovers or {}
    return {"meta": {**source_meta, "defused_from": source_name, "n_split": len(log["split"]), "n_dropped": len(log["dropped"])},
            "core": fams, **left, "dropped_atoms": sorted(set(dropped) | set(left.get("dropped_atoms", ()))),
            "dropped_log": log["dropped"]}, log


def hear(ctx, build):
    """Judge every family (fresh ones live, standing ones replayed from the ledger) -> the verdict rows."""
    core, trace = build["core"], build.get("trace", [])
    conf = float(ctx.config.get("chain.gate_conf", 0.75))
    W = ctx.atoms.sources
    seen_path = ctx.ledger("defuse_seen.json")
    seen = load_json(seen_path, {})
    if not isinstance(seen, dict):
        seen = {}
    fresh, standing = partition(core, seen, conf)
    n_reopen = sum(1 for c in fresh if sha256(c["statement"]) in seen)
    if n_reopen:
        print(f"  {n_reopen} standing verdict(s) RE-OPENED: the writer's gate refused a fusion repair")
    if standing:
        print(f"  {len(standing)} famil{'y' if len(standing) == 1 else 'ies'} keep standing verdicts; {len(fresh)} to judge")

    def run(c):
        shown = blocks.cap_members(ctx, c.get("members") or [])
        v = ctx.judges.duties(c["statement"], shown)
        return {"sid": c.get("sid"), "statement": c["statement"], "members": sorted(c.get("members") or []), "shown": shown,
                "written_for": c.get("written_for"), "written_with": c.get("written_with"),
                "n_members": len(c.get("members") or []), "prevalence": c.get("prevalence"),
                "fused": v["one_duty"] is False, "outcomes": v["outcomes"], "reason": v["reason"],
                "corroboration": corroboration(c["statement"], trace) + form_corroboration(c, conf),
                "form_flag": c.get("form_flag"), "reopened": sha256(c["statement"]) in seen,
                "failed": v.get("error")}         # the judge failed open: kept this round, never saved below
    ctx.judges.duties_many([(c["statement"], blocks.cap_members(ctx, c.get("members") or [])) for c in fresh])
    with ThreadPoolExecutor(max_workers=ctx.workers_for("fusion")) as ex:
        rows = list(ex.map(run, fresh))
    for r in rows:
        r["verdict"], r["parts"], r["basis"], r["feedback"] = (
            decide(r, W, ctx.min_sources) if r["fused"] else ("keep", [], "", ""))
    by_mem = by_members(seen)
    for c in standing:
        rec = lookup(c, seen, by_mem)
        rows.append({"sid": c.get("sid"), "statement": c["statement"], "members": sorted(c.get("members") or []),
                     "written_for": c.get("written_for"), "written_with": c.get("written_with"),
                     "n_members": len(c.get("members") or []), "prevalence": c.get("prevalence"),
                     "fused": rec["fused"], "outcomes": rec["outcomes"], "reason": rec["reason"],
                     "corroboration": corroboration(c["statement"], trace),
                     "verdict": rec["verdict"], "parts": rec["parts"], "basis": rec["basis"],
                     "feedback": rec.get("feedback", ""), "_shared_hits": rec.get("_shared_hits") or {}, "standing": True})
    rows = concurrence(ctx, rows)
    for r in rows:
        for part in r["parts"]:
            part.pop("_sources", None)
    for r in rows:
        if r.get("failed"):
            print(f"  FAILED (kept, not saved; judged again next time): {r['statement'][:70]}  [{r['failed']}]",
                  file=sys.stderr)
            continue
        if not r.get("standing"):
            seen[sha256(r["statement"])] = {k: r.get(k) for k in ("fused", "outcomes", "reason", "verdict", "parts", "basis",
                                                                    "feedback", "_shared_hits", "statement", "members")}
            if r.get("reopened") or fusion_flag(r, conf):
                seen[sha256(r["statement"])]["reopened_on"] = "fusion"
    seen_path.write_text(json.dumps(seen, indent=1))
    counts = collections.Counter(r["verdict"] for r in rows)
    print(f"  fusion: {len(rows)} families · " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return rows


def split(ctx, build, source_name="build.json"):
    """The split hearing applied: -> (families artifact, verdict rows). New parts carry no statement yet."""
    rows = hear(ctx, build)
    art, log = apply_splits(ctx, rows, build.get("meta", {}), families.leftovers(build), source_name=source_name)
    if log["split"]:
        print(f"  APPLIED {len(log['split'])} splits: {len(rows)} families -> {len(art['core'])} "
              f"({len(log['dropped'])} atoms dropped as covered by no part)")
    art["fusion_verdicts"] = {"split": [r for r in rows if r["verdict"] == "split"],
                              "keep": [{"statement": r["statement"], "basis": r["basis"], "n_members": r["n_members"],
                                        "prevalence": r["prevalence"], "parts": r["parts"], "reason": r["reason"],
                                        "feedback": r["feedback"]} if r["basis"] else r["statement"]
                                       for r in rows if r["verdict"] == "keep"]}
    return art, rows
