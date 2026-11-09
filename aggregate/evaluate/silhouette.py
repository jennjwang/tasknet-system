"""silhouette — do held-out tasks form tighter groups under one list's statements than another's?

    res = silhouette.run(ctx, lists, docs, pool)     # pool: the build's own atoms (the source pool)

Every task (the pool plus each held-out document's tasks) is placed under exactly one statement of each list
by the placement judge (the paper's r10 few-shot prompt, cache section "round1", keys preserved; batched here
with one cache entry per task). For each held-out document the dataset is pool + its tasks; the silhouette of
each of its tasks is (b - a) / max(a, b) on cosine distance; the document's score is the mean; lists are
compared on the within-document difference. The random baseline reassigns labels, preserving group sizes.
"""
from __future__ import annotations

import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..lib import sha256, strip_json
from . import stats

R10_VARIANT = "r10_fewshot_catchall"
R10_INSTR = ('You are filing one piece of work under the single statement that is its HOME.\nThe home is the statement whose activity this work IS an instance, sub-type, or step of.\nJudge the ACT and its OBJECT, not shared words or shared subject matter.\nA statement that names a CLASS OF GENERAL DUTIES rather than an act -- "perform\nadministrative and management functions", "perform other duties as assigned" -- is the\nhome ONLY when no statement in the list names the actual activity. If any statement names\nthe act, choose it, even if the general statement would also cover the work.\n\nWORKED EXAMPLES.\n  work: Run monthly data pipelines and validate the output data.\n  home: Build data ingestion and processing pipelines to move, clean, chunk, and index\n        content for search and retrieval systems.\n  work: Document mileage, time, amount of gas remaining, and destinations for client outings.\n  home: Prepare and maintain records of client progress and services performed, reporting\n        changes in client condition to manager or supervisor.\n  work: Answer teammates\' questions about system behavior and implementation details.\n  home: Advise engineers, product managers, and technical leaders on design choices,\n        implementation approaches, and engineering trade-offs.\n  work: Coordinate accommodations and testing logistics for students during standardized\n        assessments.\n  home: Administer standardized ability and achievement tests, and interpret results to\n        determine student strengths and needs.\n  work: Monitor clients\' safety and remove fall hazards in the home.\n  home: Observe clients throughout care shifts for changes in condition, mobility,\n        behavior, or safety.\nYou MUST pick exactly one number. There is no \'none\' option -- if nothing fits well,\npick the closest.\nReply as JSON: {"pick": <number>}')
assert sha256(R10_INSTR)[:12] == "bc3821508bfb", "the r10 placement prompt drifted"


def _listing(stmts):
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(stmts))


def placement_key(ctx, task, stmts):
    return sha256("␟".join(["ROUND", R10_VARIANT, ctx.model("placement"), sha256(R10_INSTR), task, sha256("␞".join(stmts))]))


def _pick_single(ctx, task, stmts):
    d = json.loads(strip_json(ctx.engine.chat(f"{R10_INSTR}\n\nTHE WORK:\n{task}\n\nSTATEMENTS:\n{_listing(stmts)}",
                                              ctx.model("placement"), max_out=200, json_mode=True)) or "{}")
    p = d.get("pick")
    p = int(p) if str(p).strip().lstrip("-").isdigit() else None
    if not (p and 1 <= p <= len(stmts)):
        raise RuntimeError("bad pick")
    return {"p": p}


def _place_batch(ctx, chunk, stmts):
    body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
    prompt = (f"{R10_INSTR}\n\nFOR THIS CALL YOU FILE SEVERAL PIECES OF WORK, EACH ON ITS OWN, under the same statement "
              f"list; one piece's home never depends on another's. Instead of one pick, reply as JSON:\n"
              f'{{"picks": [{{"id": <work number>, "pick": <statement number>}}, ...]}} — every work number exactly once.'
              f"\n\nTHE WORK:\n{body}\n\nSTATEMENTS:\n{_listing(stmts)}")
    try:
        d = json.loads(strip_json(ctx.engine.chat(prompt, ctx.model("placement"), max_out=min(8000, 40 * len(chunk) + 200),
                                                  json_mode=True)) or "{}")
    except Exception as e:                                # noqa: BLE001
        print(f"    placement batch failed ({type(e).__name__}); {len(chunk)} tasks fall to single calls", file=sys.stderr)
        return 0
    n = 0
    for v in (d.get("picks") if isinstance(d, dict) else None) or []:
        try:
            t = chunk[int(v.get("id")) - 1]
            p = int(v.get("pick"))
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        if 1 <= p <= len(stmts):
            ctx.cache.put("round1", placement_key(ctx, t, stmts), {"p": p, "batched": len(chunk)})
            n += 1
    return n


def assign(ctx, tasks, stmts, workers=16):
    """-> {task: statement} for every task (batched prefill, then singles for the misses; None on failure)."""
    tasks = list(dict.fromkeys(tasks))
    size = int(ctx.config.get("models.placement.batch", 20))
    missing = [t for t in tasks if ctx.cache.get("round1", placement_key(ctx, t, stmts)) is None]
    chunks = [missing[i:i + size] for i in range(0, len(missing), size)]
    if chunks:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            n = sum(ex.map(lambda c: _place_batch(ctx, c, stmts), chunks))
        print(f"    placement: {n} of {len(missing)} new picks batched in {len(chunks)} call(s)", file=sys.stderr)

    def one(t):
        try:
            c = ctx.cache.memo("round1", placement_key(ctx, t, stmts), lambda: _pick_single(ctx, t, stmts))
        except RuntimeError:
            return None
        return stmts[c["p"] - 1]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(tasks, ex.map(one, tasks)))


def sil(D, lab, rows, groups):
    out = []
    for r in rows:
        c = lab[r]
        same = (lab == c)
        same[r] = False
        b = min(D[r, lab == o].mean() for o in groups if o != c)
        out.append(0.0 if not same.any() else (b - D[r, same].mean()) / max(D[r, same].mean(), b))
    return float(np.mean(out)) if out else float("nan")


def score(ctx, by_w, pool, lists, V, MAP, null_draws, seed=0):
    """Per-worker silhouettes under every list and their random baselines -> (diffs {w: {arm: s}}, lev)."""
    rng = np.random.default_rng(seed)
    lev = collections.defaultdict(list)
    diffs = {}
    for w, wt in by_w.items():
        texts = pool + wt
        X = np.array([V[t] for t in texts])
        X = X / np.linalg.norm(X, axis=1, keepdims=True).clip(1e-9)
        D = 1 - X @ X.T
        rws = list(range(len(pool), len(texts)))
        sc = {}
        for arm, L in lists.items():
            idx = {x: i for i, x in enumerate(L)}
            if any(MAP[arm].get(t) is None for t in texts):
                break
            lab = np.array([idx[MAP[arm][t]] for t in texts])
            groups = set(lab.tolist())
            if len(groups) < 2:
                break
            sc[arm] = sil(D, lab, rws, groups)
            cnt = np.bincount(lab)
            bp = np.array([c for c, n_ in zip(np.flatnonzero(cnt), cnt[cnt > 0]) for _ in range(int(n_))])
            lev[f"null_{arm}"].append(float(np.mean([sil(D, (pp := rng.permutation(bp)), rws, set(pp.tolist()))
                                                      for _ in range(null_draws)])))
        if len(sc) != len(lists):
            continue
        for a in lists:
            lev[f"sil_{a}"].append(sc[a])
        diffs[w] = sc
    return diffs, lev


def run(ctx, lists, docs, pool, *, split="heldout", B=None, null_draws=None, workers=16):
    """`pool`: the source pool of task texts (the build's atoms); held-out texts are removed from it."""
    B = int(ctx.config.get("evaluate.silhouette.B", 10000)) if B is None else B
    null_draws = int(ctx.config.get("evaluate.silhouette.null_draws", 60)) if null_draws is None else null_draws
    tasks = docs.tasks(split) if hasattr(docs, "tasks") else list(docs)
    if not tasks:
        return {"available": False, "why": f"no {split} documents"}
    lists = {a: list(L) for a, L in lists.items() if L}
    by_w = stats.by_source_tasks(tasks)
    allw = list(dict.fromkeys(t for v in by_w.values() for t in v))
    pool = [a for a in dict.fromkeys(pool) if a not in set(allw)]
    V = ctx.judges.embed(pool + allw + [s for L in lists.values() for s in L])
    MAP = {arm: assign(ctx, pool + allw, L, workers) for arm, L in lists.items()}
    diffs, lev = score(ctx, by_w, pool, lists, V, MAP, null_draws)
    arms = list(lists)
    gaps = {f"{a}-{b}": stats.boot_source_means({w: sc[a] - sc[b] for w, sc in diffs.items()}, B) for a, b in stats.arm_pairs(arms)}
    return {"available": True, "n_sources": len(diffs), "n_tasks": len(allw), "pool": len(pool),
            "k": {a: len(L) for a, L in lists.items()},
            "sil": {a: float(np.mean(lev[f"sil_{a}"])) if lev[f"sil_{a}"] else float("nan") for a in arms},
            "null": {a: float(np.mean(lev[f"null_{a}"])) if lev[f"null_{a}"] else float("nan") for a in arms},
            "gaps": gaps, "per_source": diffs,
            "placement": {"model": ctx.model("placement"), "variant": R10_VARIANT, "null_draws": null_draws, "B": B}}
