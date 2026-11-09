"""condense — collapse statements that render the same work into one atom with the union of their sources.

    atoms, mapping = processing.condense(ctx, atoms, tau=0.92, cap=50)

Two merges, both deterministic given the embeddings: a KEY merge on (function, verb lemma, object head noun)
for atoms carrying FJA fields, then a NEAR-DUPLICATE merge within one source function at embedding cosine >=
tau, capped at `cap` members per component. The representative text of a component is the most prevalent
member's (most distinct sources), the shortest among equals. `mapping` records every merged original.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..atoms import FJA, Atoms

_STOP = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "by", "all", "new", "other"}


def head_noun(obj: str) -> str:
    words = [w for w in re.findall(r"[a-z][a-z\-]+", (obj or "").lower()) if w not in _STOP]
    if not words:
        return ""
    w = words[-1]
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ses", "xes", "ches", "shes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def lemma_verb(v: str) -> str:
    v = (v or "").lower().strip()
    for suf, rep in (("ies", "y"), ("es", ""), ("s", "")):
        if v.endswith(suf) and len(v) > len(suf) + 2:
            return v[:-len(suf)] + rep
    return v


class _UnionFind:
    def __init__(self, items):
        self.p = {x: x for x in items}
        self.size = {x: 1 for x in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b, cap=None):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if cap and self.size[ra] + self.size[rb] > cap:
            return False
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        self.size[ra] += self.size[rb]
        return True


def condense(ctx, atoms, tau=0.92, cap=50, embed=None):
    """-> (Atoms, mapping [{original, representative, reason}]). `embed` overrides ctx.judges.embed (tests)."""
    import numpy as np
    rows = atoms.rows if isinstance(atoms, Atoms) else list(atoms)
    by_text = defaultdict(lambda: {"sessions": set(), "meta": []})
    for r in rows:
        t = (r.get("task") or "").strip()
        if t:
            by_text[t]["sessions"].add(r["source"])
            by_text[t]["meta"].append(r)
    texts = sorted(by_text)
    uf = _UnionFind(texts)
    reasons = {}
    by_key = defaultdict(list)
    for t in texts:
        m = by_text[t]["meta"][0]
        key = (m.get("function", ""), lemma_verb(m.get("verb", "")), head_noun(m.get("object", "")))
        if all(key):
            by_key[key].append(t)
    for key, group in by_key.items():
        for t in group[1:]:
            if uf.union(group[0], t, cap):
                reasons[t] = f"key {key[0]}/{key[1]}/{key[2]}"
    if tau < 1.0 and len(texts) > 1:
        embed = embed or (lambda ts: ctx.judges.embed(ts))
        V = embed(texts)
        blocks_ = defaultdict(list)
        for t in texts:
            blocks_[by_text[t]["meta"][0].get("function", "")].append(t)
        pairs = []
        for fn, grp in blocks_.items():
            if len(grp) < 2:
                continue
            E = np.array([V[t] for t in grp])
            C = E @ E.T
            ii, jj = np.where(np.triu(C, 1) >= tau)
            pairs += [(float(C[i, j]), grp[i], grp[j]) for i, j in zip(ii, jj)]
        for cos, a, b in sorted(pairs, key=lambda x: -x[0]):
            if uf.find(a) != uf.find(b) and uf.union(a, b, cap):
                reasons.setdefault(b, f"cos {cos:.3f} ~ {a[:60]}")
    comps = defaultdict(list)
    for t in texts:
        comps[uf.find(t)].append(t)
    out_rows, mapping = [], []
    for members in comps.values():
        rep = sorted(members, key=lambda t: (-len(by_text[t]["sessions"]), len(t), t))[0]
        sessions = set().union(*(by_text[t]["sessions"] for t in members))
        metas = [m for t in members for m in by_text[t]["meta"]]
        maj = lambda k: (Counter(m.get(k, "") for m in metas if m.get(k)).most_common(1) or [("", 0)])[0][0]
        base = metas[0]
        for t in members:
            if t != rep:
                mapping.append({"original": t, "representative": rep, "reason": reasons.get(t, reasons.get(rep, ""))})
        for sid in sorted(sessions):
            out_rows.append({"source": sid, "occupation": base.get("occupation", ""), "title": base.get("title", ""),
                             "status": "condensed", "task": rep,
                             "original": "" if len(members) == 1 else f"{len(members)} statements",
                             **{k: maj(k) for k in FJA}})
    out_rows.sort(key=lambda r: (r["task"], r["source"]))
    return Atoms(out_rows), mapping
