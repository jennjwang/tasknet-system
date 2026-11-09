"""overlap — the merge / sharpen hearing over pairs of published statements.

    build, report = overlap.resolve(ctx, build, standing=None)

Pairs above the embedding prefilter (chain.overlap_tau) go to the overlaps judge (does an act appear on BOTH
statements). That tier ROUTES — `yes` edges form components heard by merge_or_separate (MERGE with a basis /
SEPARATE / KEEP), which answers the merge question on the MEMBERS. A MERGE then passes strain evidence
(ledgers/sharpen_seen: reworded N× and still colliding) and merge concurrence (a second resolver draw must
merge the same set; chain.concurrence.merge, on by default). `apply_merges` writes the union's statement with
the writer from the resolver's seed. SEPARATE verdicts go to sharpen: fresh statements take the rewrite;
standing statements are never machine-rewritten — the proposal is docketed with a concurrence triage flag.
"""
from __future__ import annotations

import collections
import itertools
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from .. import families
from ..lib import load_json, sha256
from . import blocks, synthesize

_STRAIN_STOP = {"with", "and", "for", "the", "from", "that", "their", "client", "clients", "case", "cases",
                "matter", "matters", "active", "legal", "work", "during", "other", "such", "including"}


def near_pairs(ctx, stmts, tau=None, funcs=None):
    """Index pairs above the cosine prefilter -> [(i, j, cos)]; `funcs` keeps a pair inside one block."""
    import numpy as np
    tau = float(ctx.config.get("chain.overlap_tau", 0.44)) if tau is None else tau
    V = ctx.judges.embed(stmts)
    E = np.array([V[s] for s in stmts])
    C = E @ E.T
    return [(i, j, float(C[i, j])) for i, j in itertools.combinations(range(len(stmts)), 2)
            if C[i, j] >= tau and (not funcs or blocks.same_block(ctx, funcs[i], funcs[j]))]


def components(pairs, cap=0, dropped=None):
    """Connected components over [(a, b)] or [(a, b, cos)] by union-find -> [[member, ...]].

    With `cap` > 0 no component grows past `cap` members: pairs join closest-first (highest cos) and a pair that
    would join two components into one too big is left out (appended to `dropped`). Below the cap the result is
    the plain connected components. Without a cap, one umbrella statement on a postings build chained 49
    statements into a single resolver call, which listed one twice and proposed SEPARATE for 45 of them."""
    parent, size = {}, {}

    def find(x):
        parent.setdefault(x, x)
        size.setdefault(x, 1)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    order = sorted(pairs, key=lambda t: -t[2]) if cap and pairs and len(pairs[0]) > 2 else list(pairs)
    for a, b, *_ in order:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        if cap and size[ra] + size[rb] > cap:
            if dropped is not None:
                dropped.append((a, b))
            continue
        parent[ra] = rb
        size[rb] += size[ra]
    out = collections.defaultdict(list)
    for x in list(parent):
        out[find(x)].append(x)
    return [sorted(v) for v in out.values()]


def judge_pairs(ctx, core, tau=None):
    """Run the overlaps judge over every prefiltered pair -> [{a, b, cos, label, labels_collide, why}]."""
    stmts = [c["statement"] for c in core]
    funcs = [blocks.function_of(ctx, c.get("members") or []) for c in core] if blocks.block_by(ctx) else None
    pairs = near_pairs(ctx, stmts, tau, funcs)
    print(f"  {len(stmts)} statements · {len(pairs)} pairs at cosine >= "
          f"{float(ctx.config.get('chain.overlap_tau', 0.44)) if tau is None else tau}", file=sys.stderr)

    def one(t):
        i, j, cos = t
        a, b = stmts[i], stmts[j]
        collide = ctx.judges.overlaps(a, b)
        return {"a": a, "b": b, "cos": round(cos, 3), "label": "yes" if collide else "no",
                "labels_collide": bool(collide), "why": f"an act appears in both labels at cosine {cos:.2f}" if collide else ""}
    ctx.judges.overlaps_many([(stmts[i], stmts[j]) for i, j, _ in pairs])
    with ThreadPoolExecutor(ctx.workers_for("overlap")) as ex:
        return list(ex.map(one, pairs))


# ── the strain ledger ──────────────────────────────────────────────────────────────────────────────

def _pairkey(members_a, members_b):
    return sha256("|".join(sorted((families.key(members_a), families.key(members_b)))))


def _ledger(ctx):
    return load_json(ctx.ledger("sharpen_seen.json"), {}) or {}


def record_sharpen_edges(ctx, edges, members_of):
    """Record that sharpen APPLIED a rewrite on each of these pairs."""
    p = ctx.ledger("sharpen_seen.json")
    led = _ledger(ctx)
    for a, b in edges:
        k = _pairkey(members_of[a], members_of[b])
        rec = led.get(k) or {"count": 0, "labels": []}
        rec["count"] += 1
        rec["labels"] = [a, b]
        led[k] = rec
    p.write_text(json.dumps(led, indent=1))


def _content_words(text):
    return {w for w in re.sub(r"[^a-z ]", " ", text.lower()).split() if len(w) > 4 and w not in _STRAIN_STOP}


def strain_count(ctx, members_a, members_b, label_a=None, label_b=None):
    """Ledgered rewording failures for this member-pair, discounted by one after a material rewrite."""
    rec = _ledger(ctx).get(_pairkey(members_a, members_b))
    if not rec:
        return 0
    n = rec.get("count", 0)
    if n and label_a and label_b and rec.get("labels"):
        def sim(x, y):
            if x == y:
                return 1.0
            wx, wy = _content_words(x), _content_words(y)
            if not wx or not wy:
                return 1.0
            return len(wx & wy) / min(len(wx), len(wy))
        la, lb = rec["labels"][0], rec["labels"][-1]
        best = max(min(sim(label_a, la), sim(label_b, lb)), min(sim(label_a, lb), sim(label_b, la)))
        if best < 0.5:
            n -= 1
    return n


def strain_evidence(ctx, edges, members_of, idx):
    """-> (evidence lines for the resolver's worker block, cache-key tokens)."""
    lines, tokens = [], []
    for a, b in edges:
        n = strain_count(ctx, members_of[a], members_of[b], a, b)
        if not n:
            continue
        lines.append(f"{idx[a]}-{idx[b]} STRAIN: this pair was already reworded to separate "
                     f"{n} time(s) and still reads as one duty — the occupation's vocabulary has so "
                     f"far refused to separate them. Weigh this, in proportion to the count, as "
                     f"evidence the two are ONE entry at the published level, unless a hard "
                     f"structural divider holds.")
        tokens.append(f"STRAIN:{idx[a]}-{idx[b]}:{n}")
    return lines, tokens


# ── resolution ─────────────────────────────────────────────────────────────────────────────────────

def merge_concurrence(groups, second):
    """A MERGE applies only when an independent second resolver draw merges the same member set."""
    if not any(g["decision"] == "MERGE" for g in groups):
        return groups
    merged2 = {frozenset(g2["members"]) for g2 in (second() or []) if g2.get("decision") == "MERGE"}
    for g in groups:
        if g["decision"] != "MERGE":
            continue
        if frozenset(g["members"]) in merged2:
            g["basis"] = ((g.get("basis") or "") + " [+concurrence: an independent second draw merges the same group]").strip()
            continue
        g["decision"], g["relation"] = "KEEP", ""
        g["contested"] = ((g.get("contested") or "") + " no-concurrence: MERGE on one resolver draw; the second draw "
                          "did not merge this group and no confirmed same-work edge covers it").strip()
    return groups


def resolve_component(ctx, nodes, edges, members_of):
    """One component through merge_or_separate, with strain evidence and merge concurrence -> groups or None."""
    idx = {t: i for i, t in enumerate(nodes, 1)}
    strain = strain_evidence(ctx, edges, members_of, idx)
    out = ctx.judges.merge_or_separate(nodes, edges, members_of, strain=strain)
    if not out:
        return None
    if ctx.config.get("chain.concurrence.merge", True):
        out = merge_concurrence(out, lambda: ctx.judges.merge_or_separate(nodes, edges, members_of, strain=strain, draw=2) or [])
    return out


def route(ctx, rows, by_mem=None):
    """The collisions, then ONE joint decision per component -> {"merge", "sharpen", "withheld", "mixed", "partitions"}."""
    by_mem = by_mem or {}
    merge_cap = int(ctx.config.get("chain.merge_cap", 4))
    colliding = [(r["a"], r["b"]) for r in rows if r["labels_collide"]]
    why = collections.defaultdict(dict)
    for r in rows:
        if r["labels_collide"]:
            why[r["a"]][r["b"]] = r["why"]
            why[r["b"]][r["a"]] = r["why"]
    mg, mixed, parts_log, specs = [], [], [], []
    group_cap = int(ctx.config.get("chain.overlap_group_cap", 10) or 0)
    capped = []
    for c in components([(r["a"], r["b"], r["cos"]) for r in rows if r["labels_collide"]], group_cap, capped):
        cs = set(c)
        inner = [(a, b) for (a, b) in colliding if a in cs and b in cs]
        part = resolve_component(ctx, c, inner, {n: blocks.cap_members(ctx, by_mem.get(n, [])) for n in c})
        if not part:
            mixed.append({"members": sorted(c), "merge_edges": [], "sharpen_edges": [list(e) for e in inner],
                          "why": "merge_or_separate returned no usable partition"})
            continue
        parts_log.append({"component": sorted(c), "edges": [list(e) for e in inner], "same_work_edges": [], "groups": part})
        sep = [g["members"][0] for g in part if g["decision"] == "SEPARATE"]
        mg += [sorted(g["members"]) for g in part if g["decision"] == "MERGE"]
        if sep:
            keep = [g["members"][0] for g in part if g["decision"] != "SEPARATE"]
            specs.append((sorted(sep), sorted(keep)))
    if capped:
        print(f"  {len(capped)} colliding pair(s) left out to keep each group to {group_cap} statements", file=sys.stderr)
    withheld = [g for g in mg if len(g) > merge_cap]
    mg = [g for g in mg if len(g) <= merge_cap]
    sg = [{"members": ms,
           "each": [{"statement": m, "atoms": sorted(set(by_mem.get(m, []))),
                     "siblings": [x for x in ms if x != m] + list(peers),
                     "complaints": [{"partner": p, "why": w} for p, w in sorted(why.get(m, {}).items()) if p in set(ms) | set(peers)]}
                    for m in ms]}
          for ms, peers in specs]
    return {"merge": mg, "sharpen": sg, "withheld": withheld, "mixed": mixed, "partitions": parts_log,
            "capped": [list(e) for e in capped]}


def apply_merges(ctx, core, mg, partitions=()):
    """Combine each merge group into one family (its statement written from the resolver's seed) -> (fams, log, lost)."""
    by = {c["statement"]: c for c in core}
    W = ctx.atoms.sources

    def cov(stmt, atoms):
        return sum(synthesize.covered(ctx, stmt, atoms))
    merged, log, lost, fams = set(), [], [], []
    for g in mg:
        mem = sorted({m for s in g for m in (by[s].get("members") or [])})
        before = sum(cov(s, sorted(set(by[s].get("members") or []))) for s in g)
        seed = next((x["proposed"] for pt in partitions for x in pt.get("groups", [])
                     if x.get("decision") == "MERGE" and set(x.get("members") or []) == set(g) and x.get("proposed")), g[0])
        stmt = synthesize.statement(ctx, seed, mem, [s for s in by if s not in g]) or g[0]
        after = cov(stmt, mem)
        if after < before:
            lost.append({"from": g, "statement": stmt, "cov_before": before, "cov_after": after, "n_atoms": len(mem)})
        w = sorted({x for m in mem for x in W.get(m, ())})
        fams.append({"sid": families.new_sid(), "sid_merged_from": [by[s].get("sid") for s in g if by.get(s)],
                     "head": stmt, "statement": stmt, "members": mem, "sources": w, "prevalence": len(w),
                     "n_atoms": len(mem), "merged_from": list(g)})
        merged.update(g)
        log.append({"into": stmt, "from": g, "n_members": len(mem), "prevalence": len(w), "cov_before": before, "cov_after": after})
    for c in core:
        if c["statement"] in merged:
            continue
        fams.append({**c, "head": c["statement"], "statement": c["statement"], "members": sorted(set(c.get("members") or [])),
                     "sources": sorted(set(c.get("sources") or [])), "prevalence": len(set(c.get("sources") or []))})
    families.recount(ctx, fams)
    return fams, log, lost


def sharpen_concurrence(ctx, stmt, new, partners):
    """A sharpen of ADJUDICATED prose counts only when the rewrite RESOLVES a collision on a second draw."""
    for p in partners:
        if ctx.judges.overlaps(stmt, p, draw=2) and not ctx.judges.overlaps(new, p):
            return p
    return None


def sharpen_component(ctx, nodes, edges, members_of, frozen=()):
    """The judge's rewrite proposals for a component, gated by the writer's form judge -> {old: new} or None."""
    h = ctx.judges.sharpen(nodes, edges, members_of, frozen)
    if not h:
        return None
    out = {}
    for node, cands in h.items():
        passers = []
        for c in (synthesize.one_line(x) for x in cands):
            if c == node or not c:
                continue
            why, _w = synthesize.rejects(ctx, c, members_of.get(node) or [])
            if not why:
                passers.append(c)
        if passers:
            out[node] = passers[0]
            ctx.sharpen_alternatives[node] = passers
    return out or None


def apply_sharpens(ctx, core, sg, standing_reg=None):
    """Reword each sharpen group's statements in ONE call per group -> (core, log). A family is standing only
    when `standing_reg` holds its member set AND its statement is still the adopted text."""
    by = {c["statement"]: c for c in core}
    out, log = [], []
    for g in sg:
        movable = [m for m in g["members"] if m in by]
        frozen = [x for x in dict.fromkeys(sib for e in g["each"] for sib in (e.get("siblings") or []))
                  if x in by and x not in movable]
        nodes = movable + frozen
        edges = sorted({tuple(sorted((e["statement"], c["partner"]))) for e in g["each"] for c in e["complaints"] if c["partner"] in nodes})
        if not movable or not edges:
            continue
        m_of = {n: blocks.cap_members(ctx, by[n].get("members") or []) for n in nodes}
        h = sharpen_component(ctx, nodes, edges, m_of, frozen=frozen) or {}
        docket, applied = [], set()
        for old, new in h.items():
            if old not in by or old == new:
                continue
            f = by[old]
            adopted = families.match(f.get("members") or [], standing_reg) if standing_reg else None
            if bool(adopted) and adopted == f["statement"]:
                partners = sorted({b if a == old else a for a, b in edges if old in (a, b)})
                concur = sharpen_concurrence(ctx, old, new, partners)
                docket.append({"kind": "sharpen", "statement": old, "proposed": new, "group": movable, "concurrence": bool(concur),
                               "why": (f"resolves the collision with: {concur[:80]}" if concur else
                                       "no-concurrence: the rewrite does not resolve a confirmed collision")})
                continue
            log.append({"before": old, "after": new, "group": movable,
                        "alternatives": [c for c in ctx.sharpen_alternatives.get(old, []) if c != new]})
            by[old] = synthesize.stamp_flag(ctx, {**f, "statement": new, "sharpened_from": old}, new)
            applied.add(old)
        if applied:
            record_sharpen_edges(ctx, [e for e in edges if e[0] in applied or e[1] in applied], m_of)
        if docket:
            ctx.docket.extend(docket)
            for e in docket:
                print(f"  DOCKET sharpen{' [concurrence]' if e.get('concurrence') else ''} {e['statement'][:64]}\n"
                      f"            -> {e['proposed'][:64]}", file=sys.stderr)
    for c in core:
        out.append(by.get(c["statement"], c))
    return out, log


def resolve(ctx, build, standing=None, tau=None):
    """The whole hearing: judge pairs, route, apply merges, apply sharpens. -> (build, report)."""
    core = build["core"]
    rows = judge_pairs(ctx, core, tau)
    r = route(ctx, rows, {c["statement"]: (c.get("members") or []) for c in core})
    fams, mlog, lost = apply_merges(ctx, core, r["merge"], r["partitions"])
    if mlog:
        print(f"  APPLIED {len(mlog)} merges: {len(core)} families -> {len(fams)}")
    newcore, slog = apply_sharpens(ctx, fams, r["sharpen"], standing)
    if slog:
        print(f"  SHARPENED {len(slog)} statements across {len(r['sharpen'])} group(s)")
    out = {"meta": {**build.get("meta", {}), "label_model": ctx.model("overlaps"), "resolve_model": ctx.model("merge"),
                    "sharpen_model": ctx.model("sharpen"), "n_merged": len(mlog), "n_sharpened": len(slog)},
           "core": newcore, "sharpen_log": slog, "coverage_loss": lost, **families.leftovers(build)}
    report = {"merge": r["merge"], "sharpen": r["sharpen"], "withheld": r["withheld"], "mixed": r["mixed"],
              "partitions": r["partitions"], "capped": r.get("capped", []), "pairs": rows, "merges_applied": mlog, "sharpens_applied": slog}
    return out, report
