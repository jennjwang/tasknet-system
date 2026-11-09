"""equal_length — the lists compared at the SAME number of statements.

  coverage    for a task that c of a list's K statements cover, the expected coverage under a random subset of
              m statements is 1 - C(K-c, m) / C(K, m); m = the shortest list's length. Uses the judged covering
              counts already in a coverage result (no new calls); gaps bootstrapped over sources.
  silhouette  R random size-m subsets of each longer list; every task placed by cosine argmax among the subset's
              statements (judge-free, the same for every arm); the random baseline reassigns labels among the
              SAME m groups; a source's score is the mean over draws.

    res = equal_length.run(ctx, coverage=cov, docs=docs, lists=lists, pool=pool)
"""
from __future__ import annotations

import collections
import math

import numpy as np

from . import silhouette, stats


def coverage_equal(cov, B=10000):
    """A coverage result -> {m, k, cov: {arm}, gaps} or None."""
    if not cov or not cov.get("available"):
        return None
    K = cov["k"]
    arms = list(K)
    m = min(K.values())
    per_task = cov.get("per_task") or []
    if not per_task or "n_cover" not in per_task[0]:
        return None

    def p_sub(k, c, mm):
        if c <= 0:
            return 0.0
        if k - c < mm:
            return 1.0
        return 1.0 - math.comb(k - c, mm) / math.comb(k, mm)
    exp = {a: [p_sub(K[a], t["n_cover"][a], m) for t in per_task] for a in arms}
    by_w = {a: collections.defaultdict(list) for a in arms}
    for a in arms:
        for t, v in zip(per_task, exp[a]):
            by_w[a][t["source"]].append(v)
    gaps = {f"{a}-{b}": stats.boot_sources({w: np.array(by_w[a][w]) - np.array(by_w[b][w]) for w in by_w[a]}, B)
            for a, b in stats.arm_pairs(arms)}
    return {"m": m, "k": K, "cov": {a: float(np.mean(exp[a])) for a in arms}, "gaps": gaps,
            "n_sources": cov["n_sources"], "n_tasks": cov["n_tasks"]}


def silhouette_equal(by_w, pool, lists, V, B=10000, seed=0, draws=30, null_draws=10):
    rng = np.random.default_rng(seed)
    arms = list(lists)
    m = min(len(L) for L in lists.values())
    S = {a: np.array([V[s] for s in L]) for a, L in lists.items()}
    per_w, null_w = {a: {} for a in arms}, {a: [] for a in arms}
    for w, wt in by_w.items():
        texts = pool + wt
        X = np.array([V[t] for t in texts])
        X = X / np.linalg.norm(X, axis=1, keepdims=True).clip(1e-9)
        D = 1 - X @ X.T
        rws = list(range(len(pool), len(texts)))
        for a in arms:
            K = len(lists[a])
            vals, nulls = [], []
            for _ in range(draws if K > m else 1):
                sub = rng.choice(K, size=m, replace=False) if K > m else np.arange(K)
                lab = (X @ S[a][sub].T).argmax(axis=1)
                groups = set(lab.tolist())
                if len(groups) < 2:
                    continue
                vals.append(silhouette.sil(D, lab, rws, groups))
                cnt = np.bincount(lab)
                bp = np.array([c for c, n_ in zip(np.flatnonzero(cnt), cnt[cnt > 0]) for _ in range(int(n_))])
                nulls.append(float(np.mean([silhouette.sil(D, (pp := rng.permutation(bp)), rws, set(pp.tolist())) for _ in range(null_draws)])))
            if vals:
                per_w[a][w] = float(np.mean(vals))
                null_w[a].append(float(np.mean(nulls)))
    common_w = [w for w in by_w if all(w in per_w[a] for a in arms)]
    gaps = {f"{a}-{b}": stats.boot_source_means({w: per_w[a][w] - per_w[b][w] for w in common_w}, B) for a, b in stats.arm_pairs(arms)}
    return {"m": m, "k": {a: len(L) for a, L in lists.items()}, "placement": "cosine argmax within the subset",
            "draws": draws, "null_draws": null_draws, "n_sources": len(common_w),
            "sil": {a: float(np.mean([per_w[a][w] for w in common_w])) if common_w else float("nan") for a in arms},
            "null": {a: float(np.mean(null_w[a])) if null_w[a] else float("nan") for a in arms}, "gaps": gaps}


def run(ctx, *, coverage=None, docs=None, lists=None, pool=None, split="heldout", B=10000, seed=0):
    """-> {"coverage": ..., "silhouette": ...}; either half is None when its inputs are absent."""
    draws = int(ctx.config.get("evaluate.equal_length.draws", 30))
    null_draws = int(ctx.config.get("evaluate.equal_length.null_draws", 10))
    out = {"coverage": coverage_equal(coverage, B) if coverage else None, "silhouette": None}
    tasks = (docs.tasks(split) if hasattr(docs, "tasks") else list(docs or []))
    if tasks and lists and pool is not None:
        lists = {a: list(L) for a, L in lists.items() if L}
        by_w = stats.by_source_tasks(tasks)
        allw = list(dict.fromkeys(t for v in by_w.values() for t in v))
        pool = [a for a in dict.fromkeys(pool) if a not in set(allw)]
        V = ctx.judges.embed(pool + allw + [s for L in lists.values() for s in L])
        out["silhouette"] = silhouette_equal(by_w, pool, lists, V, B, seed, draws, null_draws)
    return out
