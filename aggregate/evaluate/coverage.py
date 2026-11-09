"""coverage — the share of held-out tasks the coverage judge finds described by at least one statement, for
each list, with a paired source bootstrap.

    res = coverage.run(ctx, lists, docs)       # lists = {"ours": [...], "reference": [...]}, docs a DocSet

Candidates per task: the `evaluate.coverage.topk` most similar statements by embedding cosine plus every
statement at cosine >= tau; a task is covered if any candidate says yes (ctx.judges.covers). Standard errors
and the recentred p come from resampling held-out sources (documents).
"""
from __future__ import annotations

import collections

import numpy as np

from . import stats


def candidates(tasks, labels, V, topk=5, tau=0.30):
    """{task: [labels in cosine order]} — top `topk` plus every label at cosine >= tau."""
    L = np.array([V[s] for s in labels])
    out = {}
    for t in tasks:
        sims = L @ V[t]
        order = np.argsort(-sims)
        keep = [int(i) for i in order[:topk]] + [int(i) for i in order[topk:] if sims[i] >= tau]
        out[t] = [labels[i] for i in keep]
    return out


def judge(ctx, texts, lists, topk=None, tau=None):
    """(cand {arm: {text: [labels]}}, yes {(text, label): bool}) for held-out texts against every list."""
    topk = int(ctx.config.get("evaluate.coverage.topk", 5)) if topk is None else topk
    tau = float(ctx.config.get("evaluate.coverage.tau", 0.30)) if tau is None else tau
    V = ctx.judges.embed(list(texts) + [s for L in lists.values() for s in L])
    cand = {arm: candidates(texts, L, V, topk, tau) for arm, L in lists.items() if L}
    pairs = sorted({(t, L) for arm in cand for t, Ls in cand[arm].items() for L in Ls})
    yes = ctx.judges.covers_all(pairs)
    return cand, yes


def run(ctx, lists, docs, *, split="heldout", B=None, topk=None, tau=None):
    """-> {available, n_sources, n_tasks, k, cov, gaps, per_task, judge}."""
    B = int(ctx.config.get("evaluate.coverage.B", 10000)) if B is None else B
    tasks = docs.tasks(split) if hasattr(docs, "tasks") else list(docs)
    if not tasks:
        return {"available": False, "why": f"no {split} documents"}
    lists = {a: list(L) for a, L in lists.items() if L}
    texts = list(dict.fromkeys(t["text"] for t in tasks))
    cand, yes = judge(ctx, texts, lists, topk, tau)
    arms = list(lists)
    covered = {arm: {t: any(yes[(t, L)] for L in cand[arm][t]) for t in texts} for arm in arms}

    def gap(a, b):
        by_w = collections.defaultdict(list)
        for t in tasks:
            by_w[t["source"]].append(float(covered[a][t["text"]]) - float(covered[b][t["text"]]))
        return stats.boot_sources(by_w, B)
    return {"available": True, "n_sources": len({t["source"] for t in tasks}), "n_tasks": len(tasks),
            "k": {a: len(L) for a, L in lists.items()},
            "cov": {a: float(np.mean([covered[a][t["text"]] for t in tasks])) for a in arms},
            "gaps": {f"{a}-{b}": gap(a, b) for a, b in stats.arm_pairs(arms)},
            "per_task": [{"source": t["source"], "text": t["text"], **{a: covered[a][t["text"]] for a in arms},
                          "n_cover": {a: sum(yes[(t["text"], L)] for L in cand[a][t["text"]]) for a in arms}} for t in tasks],
            "judge": {"model": ctx.model("covers"), "topk": topk or ctx.config.get("evaluate.coverage.topk"),
                      "tau": tau or ctx.config.get("evaluate.coverage.tau"), "B": B}}
