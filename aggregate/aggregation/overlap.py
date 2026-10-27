#!/usr/bin/env python3
"""overlap — merge statements that describe the same work, reword ones that only read alike (step 3 of each round).

    python -m tasknet.aggregation.overlap [--in build.json] [--out verdicts/overlap.json] [--apply handoffs/merged.json] [--sharpen build.json]

Input:  a build artifact with statements.
Output: verdicts/overlap.json (every decision); with --apply, handoffs/merged.json with merges applied; with
        --sharpen, the artifact with merges applied and SEPARATE statements reworded.

  1. Pairs of statements whose embeddings are close (cosine >= TAU) go to the label judge
     (judges.overlap_labels): does any act appear in both? A "yes" is a collision.
  2. Colliding statements form groups (connected components). resolve_group decides each group in one call,
     looking at the reports and workers: MERGE some statements, or SEPARATE / KEEP each one. A merge needs a
     second resolver draw that agrees (merge_concurrence).
  3. A MERGE becomes one cluster; synthesize writes its statement. A SEPARATE statement is reworded (sharpened)
     so it no longer reads like its neighbours. Adopted (adopted) statements are never reworded; the proposed
     rewrite is printed for a reviewer.
Reword counts: history/reword_counts.json counts, for each pair of clusters (by member sets), how many times
they were reworded apart. A pair that collides again after being reworded is shown to the resolver with its
count (the prompt calls it STRAIN) as evidence the two may be one task.
"""
import argparse
import collections
import itertools
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from tasknet import engine
from tasknet import judges
from tasknet.aggregation import cluster, identity, synthesize

# ── settings ───────────────────────────────────────────────────────────────────────────────────────
# Which pairs are judged: only those whose statement embeddings have at least this cosine.
TAU = float(os.environ.get("OVERLAP_TAU", "0.44"))
# The resolver, and its guards: a merge applies only if a second resolver draw merges the same statements
# (CONCURRENCE_MERGE=0 turns that off), and a merge of more than MERGE_CAP statements is withheld.
RESOLVE_MODEL = os.environ.get("RESOLVE_MODEL", "gpt-5.4")
CONCURRENCE = os.environ.get("CONCURRENCE_MERGE", "1") != "0"
MERGE_CAP = int(os.environ.get("OVERLAP_MERGE_CAP", "4"))
# The sharpener writes published statements, so it uses the synthesize writer's model.
SHARPEN_MODEL = os.environ.get("SHARPEN_MODEL", "claude-sonnet-4-6")
SHARPEN_EFFORT = os.environ.get("SHARPEN_EFFORT", "medium")
PARALLEL_CALLS = int(os.environ.get("PARALLEL_CALLS", 12))        # model calls run at once (threads)

SEP = "\u241f"                                                   # separates cache-key fields


def near_pairs(stmts):
    """The index pairs of `stmts` whose embeddings have cosine >= TAU. -> [(i, j, cos)]"""
    import numpy as np
    V = judges.embed(stmts)
    E = np.array([V[s] for s in stmts])
    C = E @ E.T
    return [(i, j, float(C[i, j])) for i, j in itertools.combinations(range(len(stmts)), 2) if C[i, j] >= TAU]


def groups(pairs):
    """Group the items in `pairs` [(a, b), ...] into connected components. -> [[item, ...], ...]"""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    out = collections.defaultdict(list)
    for x in list(parent):
        out[find(x)].append(x)
    return [sorted(v) for v in out.values()]


def judge_pairs(core):
    """Ask the label judge about every close pair of statements. -> [{a, b, cos, label, labels_collide, why}]"""
    stmts = [c["statement"] for c in core]
    pairs = near_pairs(stmts)
    print(f"  {len(stmts)} statements · {len(pairs)} pairs at cosine >= {TAU}", file=sys.stderr)

    def one(t):
        i, j, cos = t
        a, b = stmts[i], stmts[j]
        v = judges.overlap_labels(a, b)
        collide = v != "no"
        why = f"an act appears in both labels at cosine {cos:.2f}" if collide else ""
        return {"a": a, "b": b, "cos": round(cos, 3), "label": v,
                "labels_collide": bool(collide), "why": why}

    judges.overlap_prefill(                       # Batch API pre-fill, when BATCH is on
        [(stmts[i], stmts[j]) for i, j, _ in pairs])
    with ThreadPoolExecutor(PARALLEL_CALLS) as ex:
        return list(ex.map(one, pairs))


def _pair_key(members_a, members_b):
    """A key for a pair of clusters (by member sets), the same whichever order they come in."""
    return engine.sha256("|".join(sorted((identity.member_key(members_a),
                                          identity.member_key(members_b)))))


def _reword_counts():
    """history/reword_counts.json and its contents: {pair key: {"count": times reworded apart, "labels": the
    last wording}}. -> (path, dict)"""
    p = identity.history_path("reword_counts.json")
    return p, (json.loads(p.read_text()) if p.exists() else {})


def add_reword_counts(edges, members_of):
    """Add one to each pair's reword count: they were reworded apart this build."""
    p, counts = _reword_counts()
    for a, b in edges:
        k = _pair_key(members_of[a], members_of[b])
        rec = counts.get(k) or {"count": 0, "labels": []}
        rec["count"] += 1
        rec["labels"] = [a, b]
        counts[k] = rec
    p.write_text(json.dumps(counts, indent=1))


# words ignored when deciding whether a pair's wording has changed since its last reword
_REWORD_STOP = {"with", "and", "for", "the", "from", "that", "their", "client", "clients", "case", "cases",
                "matter", "matters", "active", "legal", "work", "during", "other", "such", "including"}


def reword_evidence(edges, members_of, idx):
    """The lines to show the resolver, one per colliding pair already reworded apart, and the matching
    cache-key tokens. -> (lines, tokens). The "STRAIN" wording is part of the prompt and the key: keep it.

    A pair's count is how many times it was reworded apart; if both statements have been substantially
    reworded since the last time, one is taken off, so the new wording gets a fresh chance."""
    def words(text):                                    # content words, to tell a real rewording
        return {w for w in re.sub(r"[^a-z ]", " ", text.lower()).split() if len(w) > 4 and w not in _REWORD_STOP}

    def sim(x, y):
        if x == y:
            return 1.0
        wx, wy = words(x), words(y)
        if not wx or not wy:
            return 1.0                                  # nothing to compare: no discount rather than a spurious one
        return len(wx & wy) / min(len(wx), len(wy))

    counts = _reword_counts()[1]
    lines, tokens = [], []
    for a, b in edges:
        rec = counts.get(_pair_key(members_of[a], members_of[b])) or {}
        n = rec.get("count", 0)
        if n and rec.get("labels"):
            la, lb = rec["labels"][0], rec["labels"][-1]
            if max(min(sim(a, la), sim(b, lb)), min(sim(a, lb), sim(b, la))) < 0.5:
                n -= 1
        if not n:
            continue
        lines.append(f"{idx[a]}-{idx[b]} STRAIN: this pair was already reworded to separate "
                     f"{n} time(s) and still reads as one duty — the occupation's vocabulary has so "
                     f"far refused to separate them. Weigh this, in proportion to the count, as "
                     f"evidence the two are ONE entry at the published level, unless a hard "
                     f"structural divider holds.")
        tokens.append(f"STRAIN:{idx[a]}-{idx[b]}:{n}")
    return lines, tokens


def resolve_group(nodes, edges, members_of):
    """Decide one group of colliding statements in a single resolver call (prompts/.../resolve_group.txt).
    -> [{"members", "decision", "relation", "proposed", "divider", "basis", ...}] covering each statement in
    `nodes` exactly once, or None if the reply can't be parsed or doesn't cover them cleanly.

    `edges` are the colliding pairs and `members_of` maps each statement to its reports. The resolver also sees,
    for each pair, how many workers the two share, and how often it was reworded apart. MERGE needs two or more statements and a
    relation (EQUIVALENT or CONTAINMENT); SEPARATE (reword it) and KEEP (leave it) cover one statement each.

    The resolver's verdicts vary a lot between draws on the same input, and prompt edits have not reduced
    that; the second draw (CONCURRENCE) is the guard. The prompt's opening overstates what the label
    judge confirmed, but correcting it changed verdicts without improving them, so it stays for now."""
    idx = {t: i for i, t in enumerate(nodes, 1)}
    tmpl = engine.load_prompt("resolve_group", occupation=engine.occupation())
    tasks = "\n".join(f"{idx[t]} — {t} — " + "; ".join(sorted(members_of[t])) for t in nodes)
    AW = cluster.report_workers()
    wk = {t: set().union(*[AW.get(m, set()) for m in members_of[t]]) for t in nodes}

    def _wline(a, b):                                   # the resolver's line about a pair's shared workers
        sh, small = len(wk[a] & wk[b]), max(1, min(len(wk[a]), len(wk[b])))
        verdict = ("DIFFERENT people" if sh <= 1 else
                   f"SAME people — {sh}/{small} of the smaller task's workers also hold the other")
        return f"{idx[a]}-{idx[b]}: {sh} shared worker(s) of {len(wk[a])} and {len(wk[b])} — {verdict}"
    reword_lines, reword_tokens = reword_evidence(edges, members_of, idx)
    workers_txt = "\n".join([_wline(a, b) for a, b in edges] + reword_lines) or "(none)"
    clash = ", ".join(f"{idx[a]}-{idx[b]}" for a, b in edges)
    # $redundant renders empty and the "" in the key holds its place: the judge that filled it is gone, and
    # this keeps the prompt and key identical to what every cached verdict was drawn against. Do not tidy.
    prompt = (tmpl.replace("$clash", clash).replace("$tasks", tasks).replace("$redundant", "")
              .replace("$workers", workers_txt))
    key = engine.sha256(SEP.join(["RESOLVEGROUP", RESOLVE_MODEL, engine.sha256(tmpl), clash, "",
                                  "MEMWINDOW=all"] + reword_tokens
                                 + list(nodes)
                                 + [engine.sha256(SEP.join(sorted(members_of[t]))) for t in nodes]))

    instr = judges.prompt_prefix(tmpl, "$clash", "$tasks", "$redundant", "$workers")   # cacheable prefix

    def compute():
        engine.prewarm(instr, RESOLVE_MODEL)
        return json.loads(engine.strip_json(
            engine.chat(prompt, RESOLVE_MODEL, max_out=4000, cache_prefix=instr)))

    try:
        d = engine.memo("mece_resolve_group", key, compute)
    except (ValueError, KeyError, TypeError) as e:
        print(f"    resolve_group: unparseable reply ({e}) — component left alone", file=sys.stderr)
        return None

    gs = d.get("groups") if isinstance(d, dict) else None
    if not isinstance(gs, list):
        return None
    rev = {str(i): t for t, i in idx.items()}
    out, seen = [], set()
    for g in gs:
        ids = g.get("ids") if isinstance(g, dict) else None
        if not isinstance(ids, list) or not ids:
            return None
        mem = [rev.get(str(i)) for i in ids]
        if any(m is None for m in mem) or any(m in seen for m in mem):
            return None                             # an unknown number, or one statement in two groups
        seen.update(mem)
        dec = str(g.get("decision") or "").upper()
        rel = str(g.get("relation") or "").upper()
        prop = str(g.get("proposed") or g.get("statement") or "").strip()
        if dec == "MERGE" and len(mem) == 1:
            dec = "SEPARATE"                        # a one-statement "merge" is really a rewrite
        if dec == "MERGE":
            if rel not in ("EQUIVALENT", "CONTAINMENT"):
                return None                         # a merge needs a real relation
        elif dec not in ("SEPARATE", "KEEP") or len(mem) != 1:
            return None
        out.append({"members": mem, "decision": dec, "relation": rel, "proposed": prop,
                    "divider": str(g.get("divider") or "")[:200],
                    "basis": str(g.get("basis") or "")[:400]})
    if seen != set(nodes):
        return None                                 # some statement was left out
    merges = [g for g in out if g["decision"] == "MERGE"]
    if CONCURRENCE and merges:
        # a merge applies only if an independent second draw (its own cache entry) merges the same statements
        try:
            d2 = engine.memo("mece_resolve_group", engine.sha256(key + "|CONC2"), compute)
        except (ValueError, KeyError, TypeError):
            d2 = None
        merged2 = set()
        for g2 in (d2.get("groups") if isinstance(d2, dict) else None) or []:
            mem2 = [rev.get(str(i)) for i in ((g2.get("ids") if isinstance(g2, dict) else None) or [])]
            if mem2 and all(mem2) and str(g2.get("decision") or "").upper() == "MERGE":
                merged2.add(frozenset(mem2))
        for g in merges:
            if frozenset(g["members"]) in merged2:
                g["basis"] = ((g.get("basis") or "") + " [+concurrence: an independent second draw merges "
                              "the same group]").strip()
            else:
                g["decision"], g["relation"] = "KEEP", ""
    return out


def sharpen_group(nodes, edges, members_of, frozen=(), taken=()):
    """Reword a group of colliding statements in one call (prompts/.../sharpen.txt) so no two read alike.
    -> {old statement: new statement}, or None

    `frozen` statements (resolve_group's KEEPs) are shown for contrast but may not change, so the rewrite has
    to separate the pair using only the movable side. Each proposed rewrite must pass synthesize's checks and
    must not be a statement another cluster already has (`taken`); the first that qualifies is used."""
    idx = {t: i for i, t in enumerate(nodes, 1)}
    tmpl = engine.load_prompt("sharpen", occupation=engine.occupation())
    tasks = "\n".join(f"{idx[t]} — {t} — " + "; ".join(sorted(set(members_of[t]))) for t in nodes)
    clash = ", ".join(f"{idx[a]}-{idx[b]}" for a, b in edges)
    frz = sorted(idx[t] for t in frozen if t in idx)
    frozen_txt = ("" if not frz else
                  f"TASK{'S' if len(frz) > 1 else ''} {', '.join(str(i) for i in frz)} "
                  f"{'ARE' if len(frz) > 1 else 'IS'} ALREADY PUBLISHED AND CANNOT CHANGE. Only the other "
                  "tasks may be rewritten, so the separator you choose must be one THEY can carry on their "
                  "own. If the only way to tell a pair apart is to reword a frozen task, say so by leaving "
                  "the movable one unchanged rather than writing a rewrite that does not separate them.")
    prompt = (tmpl.replace("$clash", clash).replace("$tasks", tasks).replace("$frozen", frozen_txt))
    key = engine.sha256(SEP.join(["SHARPENGROUP", SHARPEN_MODEL, engine.sha256(tmpl), clash, "MEMWINDOW=all",
                                  engine.sha256(frozen_txt)]      # keeps frozen and unfrozen calls apart
                                 + list(nodes)
                                 + [engine.sha256(SEP.join(sorted(set(members_of[t])))) for t in nodes]))

    instr = judges.prompt_prefix(tmpl, "$clash", "$tasks", "$frozen")             # cacheable prefix

    def compute():
        engine.prewarm(instr, SHARPEN_MODEL)
        out = engine.chat(prompt, SHARPEN_MODEL, max_out=4000, effort=SHARPEN_EFFORT,
                          cache_prefix=instr)
        d = json.loads(engine.strip_json(out))
        return {"sep": d.get("sep") or {}, "h": d.get("h") or {}}

    try:
        d = engine.memo("mece_sharpen_group", key, compute)
    except (ValueError, KeyError, TypeError) as e:
        print(f"    sharpen_group: unparseable reply ({e})", file=sys.stderr)
        return None
    rev = {str(i): t for t, i in idx.items()}
    h = {}
    for k, v in d["h"].items():
        if k not in rev:
            continue
        cands = [synthesize._final_line(str(c).strip()) for c in (v if isinstance(v, list) else [v])
                 if str(c).strip()]
        node = rev[k]
        if node in set(frozen):                 # frozen statements are never rewritten
            continue
        first = next((c for c in cands if c != node and c not in taken
                      and not synthesize._find_defect(c, members_of.get(node) or [])[0]), None)
        if first:
            h[node] = first
    return h or None


def decide_groups(rows, by_mem):
    """Turn the judged pairs into decisions: group the colliding statements and resolve each group.
    -> {"merge": statement groups to merge, "sharpen": groups to reword, "withheld": merge groups over
        MERGE_CAP, "mixed": groups the resolver couldn't decide, "partitions": every resolver decision}
    `by_mem` maps statement -> reports."""
    colliding = []
    why = collections.defaultdict(dict)           # statement -> {colliding statement: the judge's reason}
    for r in rows:
        if r["labels_collide"]:
            colliding.append((r["a"], r["b"]))
            why[r["a"]][r["b"]] = r["why"]
            why[r["b"]][r["a"]] = r["why"]

    mg, mixed, parts_log = [], [], []
    specs = []                                    # (statements to rewrite, statements to stay clear of)
    for c in groups(colliding):
        inner = [(a, b) for (a, b) in colliding if a in c and b in c]
        part = resolve_group(c, inner, {n: by_mem.get(n, []) for n in c})
        if not part:
            mixed.append({"members": sorted(c), "sharpen_edges": [list(e) for e in inner],
                          "why": "resolve_group returned no usable partition"})
            continue
        parts_log.append({"component": sorted(c), "edges": [list(e) for e in inner], "groups": part})
        sep = [g["members"][0] for g in part if g["decision"] == "SEPARATE"]
        mg += [sorted(g["members"]) for g in part if g["decision"] == "MERGE"]
        if sep:
            # the statements a SEPARATE one must be reworded away from; for a merge group that is its first
            # statement, since the merged text doesn't exist yet
            keep = [g["members"][0] for g in part if g["decision"] != "SEPARATE"]
            specs.append((sorted(sep), sorted(keep)))

    withheld = [g for g in mg if len(g) > MERGE_CAP]
    mg = [g for g in mg if len(g) <= MERGE_CAP]
    sg = [{"members": ms,
           "each": [{"statement": m,
                     "atoms": sorted(set(by_mem.get(m, []))),
                     "siblings": [x for x in ms if x != m] + list(peers),
                     "complaints": [{"partner": p, "why": w} for p, w in sorted(why.get(m, {}).items())
                                    if p in set(ms) | set(peers)]}
                    for m in ms]}
          for ms, peers in specs]
    return {"merge": mg, "sharpen": sg, "withheld": withheld,
            "mixed": mixed, "partitions": parts_log}


def merge_identical(core):
    """Merge clusters whose statements are word-for-word identical: the same text claims the same work, so no
    judge is needed (the paper: "duplicate or nested statements are merged"). -> (clusters, merged texts)

    The merged cluster takes the first one's place, all their members and a new id; its "already written"
    stamps go, so synthesize checks the statement against the new members. A list with no repeated statement
    comes back unchanged."""
    by = {}
    for c in core:
        by.setdefault(c["statement"], []).append(c)
    dups = [s for s, cs in by.items() if len(cs) > 1]
    if not dups:
        return core, []
    out, done = [], set()
    for c in core:
        s = c["statement"]
        if s not in dups:
            out.append(c)
        elif s not in done:
            cs = by[s]
            mem = sorted({m for f in cs for m in (f.get("members") or [])})
            keep = {k: v for k, v in cs[0].items() if k not in ("written_for", "written_with", "form_flag")}
            out.append({**keep, "sid": identity.new_cluster_id(), "sid_merged_from": [f.get("sid") for f in cs],
                        "head": s, "statement": s, "members": mem, "merged_from": [s] * len(cs)})
            done.add(s)
    cluster.recount_workers(out)
    return out, dups


def apply_merges(core, mg, partitions=()):
    """Combine each merge group into one cluster with a new statement (written by synthesize, starting from the
    resolver's proposal); pass the other clusters through. -> (clusters, log, lost)
    `lost` lists merges whose new statement covers fewer reports than the old ones did; they are applied anyway
    and the next coverage step picks up the rest.

    Merge groups name statements, and two clusters can share one statement text (a merge or rewrite can
    reproduce a neighbour's), so `by` maps each text to every cluster carrying it: a merge takes them all,
    rather than keeping one and losing the others' reports."""
    by = {}
    for c in core:
        by.setdefault(c["statement"], []).append(c)
    REPORT_W = cluster.report_workers()

    def covered(stmt, reports):                            # how many of `reports` the statement covers
        return sum(synthesize._coverage(stmt, reports))

    merged, log, lost, fams = set(), [], [], []
    for g in mg:
        mem = sorted({m for s in g for f in by[s] for m in (f.get("members") or [])})
        before = sum(covered(s, sorted(set(f.get("members") or []))) for s in g for f in by[s])
        seed = next((x["proposed"] for pt in partitions for x in pt.get("groups", [])
                     if x.get("decision") == "MERGE" and set(x.get("members") or []) == set(g)
                     and x.get("proposed")), g[0])
        stmt = synthesize.write_statement(seed, mem, [s for s in by if s not in g]) or g[0]
        after = covered(stmt, mem)
        if after < before:
            lost.append({"from": g, "statement": stmt, "cov_before": before, "cov_after": after,
                         "n_atoms": len(mem)})
        w = sorted({x for m in mem for x in REPORT_W.get(m, ())})
        fams.append({"sid": identity.new_cluster_id(),
                     "sid_merged_from": [f.get("sid") for s in g for f in by.get(s, [])],
                     "head": stmt, "statement": stmt, "members": mem, "workers": w, "prevalence": len(w),
                     "n_atoms": len(mem), "merged_from": list(g)})
        merged.update(g)
        log.append({"into": stmt, "from": g, "n_members": len(mem), "prevalence": len(w),
                    "cov_before": before, "cov_after": after})
    for c in core:
        if c["statement"] in merged:
            continue
        fams.append({**c, "head": c["statement"], "statement": c["statement"],
                     "members": sorted(set(c.get("members") or [])),
                     "workers": sorted(set(c.get("workers") or [])),
                     "prevalence": len(set(c.get("workers") or []))})
    cluster.recount_workers(fams)
    return fams, log, lost


def apply_sharpens(core, sg, adopted_reg=None):
    """Reword each sharpen group in one call per group. -> (clusters, log)

    A statement is adopted (adopted) when `adopted_reg` (from --adopted) matches its cluster's members and
    it is still the adopted wording; its rewrite is printed, not applied. Without a registry every statement
    can be rewritten. Applied rewrites are added to the reword counts.

    `by` (statement -> cluster) only supplies members for the prompt. The output is built from `core` itself, so
    two clusters that share a statement text each keep their own reports."""
    by = {c["statement"]: c for c in core}
    out, log, rewritten = [], [], {}
    for g in sg:
        movable = [m for m in g["members"] if m in by]
        # the group's KEEP neighbours are shown frozen, so a lone SEPARATE still has something to separate from
        frozen = [x for x in dict.fromkeys(sib for e in g["each"] for sib in (e.get("siblings") or []))
                  if x in by and x not in movable]
        nodes = movable + frozen
        edges = sorted({tuple(sorted((e["statement"], c["partner"])))
                        for e in g["each"] for c in e["complaints"] if c["partner"] in nodes})
        if not movable or not edges:
            continue
        m_of = {n: sorted(set(by[n].get("members") or [])) for n in nodes}
        taken = set(by) | set(rewritten.values())        # every statement now in the list
        h = sharpen_group(nodes, edges, m_of, frozen=frozen, taken=taken) or {}
        applied = set()
        for old, new in h.items():
            if old not in by or old == new:
                continue
            f = by[old]
            adopted = identity.adopted_statement(f.get("members") or [], adopted_reg) if adopted_reg else None
            if adopted and adopted == f["statement"]:       # an adopted statement: print the rewrite, keep it
                print(f"  NOT APPLIED (adopted) reword {old[:64]}\n            -> {new[:64]}", file=sys.stderr)
                continue
            log.append({"before": old, "after": new, "group": movable})
            by[old] = synthesize._set_fusion_flag({**f, "statement": new, "sharpened_from": old}, new)
            applied.add(old)
            rewritten[old] = new
        # only applied rewrites are counted: a declined or unapplied one changed nothing
        if applied:
            add_reword_counts([e for e in edges if e[0] in applied or e[1] in applied], m_of)
    for c in core:
        new = rewritten.get(c["statement"])
        out.append(synthesize._set_fusion_flag({**c, "statement": new, "sharpened_from": c["statement"]}, new)
                   if new else c)
    return out, log


def main():
    engine.set_stage("overlap")                      # use this stage's own API key, if one is set
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", default="build.json", help="a statements artifact")
    ap.add_argument("--out", default="verdicts/overlap.json", help="filename under results/aggregation/<occ>/")
    ap.add_argument("--apply", metavar="FILE", default=None,
                    help="write a clusters artifact with the MERGE groups combined, e.g. handoffs/merged.json. "
                         "Non-destructive. Then: synthesize.py --in handoffs/merged.json")
    ap.add_argument("--sharpen", metavar="FILE", default=None,
                    help="write a statements artifact with the SHARPEN groups reworded, e.g. "
                         "build.json. One writer call per group; non-destructive. Verification is a "
                         "second overlap pass over the result, not an inline check.")
    ap.add_argument("--adopted", default=None,
                    help="an ADOPTED deliverable artifact (e.g. final.json). Sharpen proposals against a "
                         "cluster whose statement is still its adopted text are printed, never applied; "
                         "omit it and every statement is fresh, so sharpen rewrites.")
    args = ap.parse_args()

    build = identity.read_artifact(args.inp, "run synthesize.py first.")
    core, dups = merge_identical(build["core"])          # identical statements, e.g. from synthesis
    for s in dups:
        print(f"  MERGED identical statements: {s[:90]}")

    rows = judge_pairs(core)
    r = decide_groups(rows, {c["statement"]: (c.get("members") or []) for c in core})

    print(f"{engine.occupation()}: {len(core)} statements, {len(r['partitions'])} colliding group(s) resolved · "
          f"merge {len(r['merge'])} · reword {len(r['sharpen'])} · withheld (over MERGE_CAP={MERGE_CAP}) "
          f"{len(r['withheld'])} · unresolved {len(r['mixed'])}")
    for g in r["merge"]:
        print("  MERGE  " + " + ".join(x[:50] for x in g))

    dest = identity.write_artifact(args.out, {
        "meta": {**build.get("meta", {}), "source": args.inp, "tau": TAU,
                 "label_model": judges.OVERLAP_MODEL, "merge_cap": MERGE_CAP, "resolve_model": RESOLVE_MODEL},
        "merge": r["merge"], "sharpen": r["sharpen"], "withheld": r["withheld"],
        "mixed": r["mixed"], "partitions": r["partitions"], "pairs": rows})
    print(f"\n→ {dest}")

    merged_core = None
    if args.apply:
        fams, log, lost = apply_merges(core, r["merge"], r["partitions"])
        fams, dups = merge_identical(fams)               # a merge that wrote a neighbour's statement
        for s in dups:
            print(f"  MERGED identical statements: {s[:90]}")
        merged_core = fams
        ap_dest = identity.write_artifact(args.apply, {
            "meta": {**build.get("meta", {}), "merged_from": args.inp,
                     "n_merged": len(log), "n_coverage_loss": len(lost)},
            "core": fams, "coverage_loss": lost, **cluster.carry(build)})
        print(f"  APPLIED {len(log)} merges: {len(core)} clusters -> {len(fams)} ({len(lost)} cover fewer reports "
              f"than before; the coverage step refiles the rest)  → {ap_dest}")

    if args.sharpen:
        base = merged_core if merged_core is not None else core
        adopted_reg = identity.load_adopted(args.adopted) if args.adopted else None
        newcore, slog = apply_sharpens(base, r["sharpen"], adopted_reg)
        newcore, dups = merge_identical(newcore)         # backstop: rewordings never take a taken statement
        for s in dups:
            print(f"  MERGED identical statements: {s[:90]}")
        sp = identity.write_artifact(args.sharpen, {
            "meta": {**build.get("meta", {}), "sharpened_from": args.inp,
                     "sharpened_over": args.apply if merged_core is not None else args.inp,
                     "sharpen_model": SHARPEN_MODEL, "n_sharpened": len(slog)},
            "core": newcore, "sharpen_log": slog, **cluster.carry(build)})
        print(f"  SHARPENED {len(slog)} statements across {len(r['sharpen'])} group(s)  → {sp}")


if __name__ == "__main__":
    main()
