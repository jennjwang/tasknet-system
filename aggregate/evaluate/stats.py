"""The two bootstraps the evaluations use, Holm's correction, and significance stars."""
from __future__ import annotations

import numpy as np


def boot_sources(per_source, B=10000, seed=0):
    """Paired cluster bootstrap over sources: `per_source` = {source: array of per-task differences}.
    -> {est, se, ci, p, n}. SE = sd of the draws; p recentred, floored at 1/(B+1)."""
    w = list(per_source)
    n = len(w)
    if n == 0:
        return {"est": float("nan"), "se": float("nan"), "ci": [float("nan")] * 2, "p": float("nan"), "n": 0}
    rng = np.random.default_rng(seed)
    arrs = [np.asarray(per_source[k], float) for k in w]
    obs = float(np.concatenate(arrs).mean())
    if n == 1:
        return {"est": obs, "se": float("nan"), "ci": [obs, obs], "p": float("nan"), "n": 1}
    sizes = np.array([len(a) for a in arrs])
    sums = np.array([a.sum() for a in arrs])
    idx = rng.integers(0, n, (B, n))
    dr = sums[idx].sum(axis=1) / sizes[idx].sum(axis=1)
    lo, hi = np.percentile(dr, [2.5, 97.5])
    p = float((np.abs(dr - obs) >= abs(obs)).mean())
    return {"est": obs, "se": float(dr.std(ddof=1)), "ci": [float(lo), float(hi)], "p": max(p, 1.0 / (B + 1)), "n": n}


def boot_source_means(per_source, B=10000, seed=0):
    """Bootstrap over sources where each source contributes ONE value."""
    return boot_sources({k: [v] for k, v in per_source.items()}, B, seed)


def boot_statements(values, B=4000, seed=0):
    """Mean and bootstrap SE of a statement-level score (statements resampled within a list)."""
    v = np.asarray([x for x in values if x is not None and not (isinstance(x, float) and np.isnan(x))], float)
    if len(v) == 0:
        return float("nan"), float("nan")
    if len(v) == 1:
        return float(v[0]), float("nan")
    rng = np.random.default_rng(seed)
    dr = v[rng.integers(0, len(v), (B, len(v)))].mean(axis=1)
    return float(v.mean()), float(dr.std(ddof=1))


def gap_statements(a, b, B=4000, seed=0):
    """Statement bootstrap of mean(a) - mean(b), both lists resampled -> {est, se, p} or None."""
    a = np.asarray([x for x in a if x is not None], float)
    b = np.asarray([x for x in b if x is not None], float)
    if len(a) < 2 or len(b) < 2:
        return None
    rng = np.random.default_rng(seed)
    dr = a[rng.integers(0, len(a), (B, len(a)))].mean(axis=1) - b[rng.integers(0, len(b), (B, len(b)))].mean(axis=1)
    obs = float(a.mean() - b.mean())
    return {"est": obs, "se": float(dr.std(ddof=1)), "p": max(float((np.abs(dr - obs) >= abs(obs)).mean()), 1 / (B + 1))}


def holm(pvals):
    """{key: p} -> {key: Holm-adjusted p}."""
    items = sorted((p, k) for k, p in pvals.items() if p == p)
    out, run, m = {}, 0.0, len(items)
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def stars(p):
    if p != p:
        return ""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


def arm_pairs(arms):
    """The (a, b) pairs a gap is reported for: ours (the first arm) against each other, then the rest a < b."""
    ours = arms[0]
    return [(ours, b) for b in arms[1:]] + [(a, b) for a in arms[1:] for b in arms[1:] if a < b]


def by_source_tasks(tasks):
    """[{source, text}] -> {source: [distinct texts]} in order."""
    out = {}
    for t in tasks:
        out.setdefault(t["source"], [])
        if t["text"] not in out[t["source"]]:
            out[t["source"]].append(t["text"])
    return out
