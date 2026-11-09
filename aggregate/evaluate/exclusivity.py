"""exclusivity — are the statements of a list mutually exclusive? The pair-relation judge applied WITHIN each
list, on pairs at embedding cosine >= evaluate.exclusivity.tau; a pair is related when its verdict is not N.

    res = exclusivity.run(ctx, lists)
"""
from __future__ import annotations

import collections
import itertools
from concurrent.futures import ThreadPoolExecutor

from .crosswalk import REL_WORD


def within(ctx, statements, tau=None, workers=32):
    tau = float(ctx.config.get("evaluate.exclusivity.tau", 0.30)) if tau is None else tau
    V = ctx.judges.embed(statements)
    pairs = [(a, b) for a, b in itertools.combinations(statements, 2) if float(V[a] @ V[b]) >= tau]
    n_all = len(statements) * (len(statements) - 1) // 2
    with ThreadPoolExecutor(max_workers=workers) as ex:
        verdicts = dict(zip(pairs, ex.map(lambda p: ctx.judges.relation_pair(*p), pairs)))
    related = [(a, b, v["label"]) for (a, b), v in verdicts.items() if v["label"] != "none"]
    partners = collections.Counter()
    for a, b, _ in related:
        partners[a] += 1
        partners[b] += 1
    n = len(statements)
    return {"n": n, "n_pairs": n_all, "n_judged": len(pairs), "tau": tau, "related_pairs": len(related),
            "share_pairs_related": len(related) / max(1, n_all),
            "share_with_partner": sum(1 for s in statements if partners[s]) / max(1, n),
            "mean_partners": sum(partners.values()) / max(1, n),
            "by_relation": dict(collections.Counter(REL_WORD[l] for _, _, l in related)),
            "pairs": [{"a": a, "b": b, "rel": REL_WORD[l]} for a, b, l in related]}


def run(ctx, lists, *, tau=None, workers=32):
    res = {"model": ctx.model("relation")}
    for arm, L in lists.items():
        if L:
            res[arm] = within(ctx, list(L), tau, workers)
    return res
