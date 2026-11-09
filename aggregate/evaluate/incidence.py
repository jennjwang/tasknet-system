"""incidence — which documents cover each statement, for every list separately, and PCA / correspondence analysis
of the statements on that incidence; plus names for the axes.

    res = incidence.run(ctx, lists, docs)            # docs: a DocSet with heldout + construction documents
    res = incidence.axis_names(ctx, res, title)      # names each PCA / CA axis off its extreme statements
    st  = incidence.status(ctx, lists, docs)         # pairs, unjudged, requests, pending batches: a quote before spending
    res = incidence.run(ctx, lists, docs, partial=True)   # the matrix from the verdicts so far, unjudged = not covering

ROWS ARE THE POOL OF AN OCCUPATION'S DOCUMENTS (held-out, then construction). A document COVERS a statement when
the pooled coverage judge says at least one of its atoms falls under it (models.pooled: the coverage rubric
with up to `group` atoms per label in one call — its own cache section, its own keys; candidate rule top-k +
cosine as coverage uses it; a pair outside the candidate set counts as not covering). Verdicts come from the
cache; `run` judges a residual below batch.min live, and `submit` / `collect` push the rest through the Batch
API. Every numeric covariate the documents carry (`meta`) is averaged per statement over its covering
documents and correlated (Spearman) with the coverage count and with each axis.

Signs are fixed: PC1 positively correlated with coverage count; every other axis so that its largest-magnitude
score is positive. A never-covered statement has no mass in CA and no coordinate (null).
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..lib import sha256, strip_json
from . import coverage

SECTION = TAG = "covers_pooled"
EST_PER_PAIR_BATCH = 0.00006
SUBMIT_CHUNK = 15000
KEEP = ("deciding_rule", "why", "verdict")
AXIS_SECTION = "axis_names"
K_MAX, K_MIN, N_MIN = 10, 4, 8


# ── the pooled judge ───────────────────────────────────────────────────────────────────────────────

def pooled_key(ctx, atom, label):
    return sha256("␟".join(["COVPOOLED", ctx.model("pooled"), sha256(ctx.prompt("coverage_batch")), atom, label]))


def _groups(ctx, pairs):
    group = int(ctx.config.get("models.pooled.group", 40))
    by = {}
    for a, L in pairs:
        by.setdefault(L, set()).add(a)
    out = []
    for L, s in sorted(by.items()):
        atoms = sorted(s)
        out += [(L, atoms[i:i + group]) for i in range(0, len(atoms), group)]
    return out


def _prompt(ctx, label, atoms):
    return ctx.prompt("coverage_batch").replace("$label", label).replace("$atoms", "\n".join(f"{i}. {a}" for i, a in enumerate(atoms, 1)))


def _body(ctx, label, atoms):
    return {"messages": [{"role": "user", "content": _prompt(ctx, label, atoms)}],
            **ctx.engine.chat_kwargs(ctx.model("pooled"), min(16000, 250 * len(atoms) + 400), json_mode=True)}


def _store(ctx, label, atoms, text):
    """Parse one group reply and cache every usable verdict under its pair key -> n written."""
    try:
        d = json.loads(strip_json(text) or "{}")
    except Exception:                                     # noqa: BLE001
        return 0
    n = 0
    for v in (d.get("verdicts") if isinstance(d, dict) else None) or []:
        try:
            atom = atoms[int(v.get("id")) - 1]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        if str(v.get("verdict", "")).strip().lower()[:1] not in ("y", "n"):
            continue
        rec = {k: v.get(k) for k in KEEP}
        rec["grouped"] = len(atoms)
        ctx.cache.put(SECTION, pooled_key(ctx, atom, label), rec)
        n += 1
    return n


def judged(ctx, pairs):
    """({(task, statement): bool} from the cache, [pairs never judged])."""
    out, missing = {}, []
    for a, L in pairs:
        v = ctx.cache.get(SECTION, pooled_key(ctx, a, L))
        if v is None:
            missing.append((a, L))
        else:
            out[(a, L)] = str(v.get("verdict", "")).strip().lower().startswith("y")
    return out, missing


def rows_of(docs):
    """({doc id: [atoms]}, ["heldout"|"construction" per row]) — held-out documents first."""
    rows, sample = {}, []
    for split in ("heldout", "construction"):
        for d in (docs.heldout if split == "heldout" else docs.construction):
            if d["id"] in rows:
                continue
            rows[d["id"]] = list(dict.fromkeys(" ".join(str(t).split()).strip() for t in (d.get("atoms") or []) if str(t).strip()))
            sample.append(split)
    return rows, sample


def pairs_for(ctx, rows, lists):
    texts = list(dict.fromkeys(t for ts in rows.values() for t in ts))
    if not texts:
        return {arm: {} for arm in lists}, []
    topk, tau = int(ctx.config.get("evaluate.coverage.topk", 5)), float(ctx.config.get("evaluate.coverage.tau", 0.30))
    V = ctx.judges.embed(texts + [s for L in lists.values() for s in L])
    cand = {arm: coverage.candidates(texts, L, V, topk, tau) for arm, L in lists.items()}
    return cand, sorted({(t, L) for arm in cand for t, Ls in cand[arm].items() for L in Ls})


# ── projections ────────────────────────────────────────────────────────────────────────────────────

def _rank(v):
    v = np.asarray(v, float)
    order = np.argsort(v, kind="mergesort")
    r = np.empty(len(v))
    r[order] = np.arange(len(v), dtype=float)
    _, inv, cnt = np.unique(v, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, r)
    return sums[inv] / cnt[inv]


def spearman(x, y):
    x = np.asarray([np.nan if v is None else v for v in x], float)
    y = np.asarray([np.nan if v is None else v for v in y], float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return float("nan")
    return float(np.corrcoef(_rank(x[ok]), _rank(y[ok]))[0, 1])


def pca2(X):
    """X: statements x documents (0/1) -> (row scores n x 2, explained [2], |corr(PC1, count)|, column coords)."""
    X = np.asarray(X, float)
    if X.shape[0] < 3 or X.shape[1] < 2:
        return None
    Xc = X - X.mean(axis=0, keepdims=True)
    tot = float((Xc ** 2).sum())
    if tot <= 1e-12:
        return None
    U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    k = min(2, len(S))
    scores, cols = U[:, :k] * S[:k], Vt[:k].T.copy()
    if k < 2:
        scores = np.hstack([scores, np.zeros((X.shape[0], 1))])
        cols = np.hstack([cols, np.zeros((X.shape[1], 1))])
        S = np.append(S, 0.0)
    prev = X.sum(axis=1)
    if np.std(prev) > 0 and np.corrcoef(scores[:, 0], prev)[0, 1] < 0:
        scores[:, 0] *= -1
        cols[:, 0] *= -1
    if scores[np.argmax(np.abs(scores[:, 1])), 1] < 0:
        scores[:, 1] *= -1
        cols[:, 1] *= -1
    ev = [float(S[0] ** 2 / tot), float(S[1] ** 2 / tot)]
    r = float(abs(np.corrcoef(scores[:, 0], prev)[0, 1])) if np.std(prev) > 0 and np.std(scores[:, 0]) > 0 else float("nan")
    return scores, ev, r, cols


def ca2(X):
    """Correspondence analysis: row principal coordinates on two dimensions, inertia shares, |corr(dim 1, count)|,
    column principal coordinates. Rows and columns with no mass get NaN."""
    X = np.asarray(X, float)
    keep = X.sum(axis=1) > 0
    cols = X.sum(axis=0) > 0
    Xk = X[keep][:, cols]
    n = Xk.sum()
    if Xk.shape[0] < 3 or Xk.shape[1] < 2 or n <= 0:
        return None
    P = Xk / n
    r, c = P.sum(axis=1), P.sum(axis=0)
    S = (P - np.outer(r, c)) / np.sqrt(np.outer(r, c))
    U, sv, Vt = np.linalg.svd(S, full_matrices=False)
    tot = float((sv ** 2).sum())
    if tot <= 1e-12 or len(sv) < 1:
        return None
    k = min(2, len(sv))
    F = (U[:, :k] * sv[:k]) / np.sqrt(r)[:, None]
    G = (Vt[:k].T * sv[:k]) / np.sqrt(c)[:, None]
    if k < 2:
        F = np.hstack([F, np.zeros((F.shape[0], 1))])
        G = np.hstack([G, np.zeros((G.shape[0], 1))])
        sv = np.append(sv, 0.0)
    for j in range(2):
        if F[np.argmax(np.abs(F[:, j])), j] < 0:
            F[:, j] *= -1
            G[:, j] *= -1
    out = np.full((X.shape[0], 2), np.nan)
    out[keep] = F
    outc = np.full((X.shape[1], 2), np.nan)
    outc[cols] = G
    prev = X[keep].sum(axis=1)
    rho = float(abs(np.corrcoef(F[:, 0], prev)[0, 1])) if np.std(prev) > 0 and np.std(F[:, 0]) > 0 else float("nan")
    return out, [float(sv[0] ** 2 / tot), float(sv[1] ** 2 / tot)], rho, outc


def _num(v):
    return None if v is None or v != v else round(float(v), 4)


def assemble(docs_order, sample, rows, lists, cand, yes, meta, covariates):
    """-> (result dict, {arm: 0/1 matrix statements x documents})."""
    idx = {p: i for i, p in enumerate(docs_order)}
    cov = {k: np.array([np.nan if (meta.get(p) or {}).get(k) is None else float(meta[p][k]) for p in docs_order]) for k in covariates}
    res = {"available": True, "n_docs": len(docs_order), "n_construction": sum(s == "construction" for s in sample),
           "n_heldout": sum(s == "heldout" for s in sample), "n_tasks": sum(len(ts) for ts in rows.values()),
           "docs": docs_order, "sample": sample, "covariates": covariates,
           "meta": {p: {k: _num(cov[k][idx[p]]) for k in covariates} for p in docs_order},
           "ranges": {k: [_num(v) for v in (np.nanpercentile(cov[k], [2, 98]) if np.isfinite(cov[k]).sum() >= 5 else [np.nan, np.nan])] for k in covariates},
           "lists": {}, "pca": {}, "ca": {}, "checks": {}}
    mats = {}
    for arm, L in lists.items():
        cset = {t: set(Ls) for t, Ls in cand[arm].items()}
        M = np.zeros((len(L), len(docs_order)))
        out = []
        for j, s in enumerate(L):
            covering = sorted(idx[p] for p, ts in rows.items() if any(s in cset.get(t, ()) and yes.get((t, s), False) for t in ts))
            M[j, covering] = 1
            rec = {"statement": s, "docs": covering, "n": len(covering), "cov": {}}
            for k in covariates:
                cs = cov[k][covering]
                rec["cov"][k] = _num(np.nanmean(cs)) if np.isfinite(cs).sum() >= 3 else None
            out.append(rec)
        res["lists"][arm] = out
        mats[arm] = M
        res["checks"][arm] = {k: _num(spearman([r["n"] for r in out], [r["cov"][k] for r in out])) for k in covariates}
    arms = list(mats)
    for r_ in range(1, len(arms) + 1):
        for combo in itertools.combinations(arms, r_):
            X = np.vstack([mats[a] for a in combo])
            ids = [f"{a}:{j}" for a in combo for j in range(mats[a].shape[0])]
            key = "+".join(combo)
            for name, fn in (("pca", pca2), ("ca", ca2)):
                p = fn(X)
                if p is None:
                    res[name][key] = {"available": False, "ids": ids}
                    continue
                scores, ev, rho, cols = p
                res[name][key] = {"available": True, "ids": ids, "x": [_num(v) for v in scores[:, 0]], "y": [_num(v) for v in scores[:, 1]],
                                  "explained": ev, "dim1_prevalence_r": _num(rho),
                                  "axis_rho": {k: [_num(spearman(cols[:, i], cov[k])) for i in range(2)] for k in covariates}}
    return res, mats


def status(ctx, lists, docs, occ=""):
    """What judging these lists on these documents would take, from the cache and the batch ledger: a quote before
    spending. Finished batches are harvested first. -> {"docs": {split: n}, "lists": {arm: k}, "pairs", "judged",
    "unjudged", "requests" (Batch API requests the unjudged would need at models.pooled.group atoms each),
    "pending" (batches for this occupation still in the ledger), "model", "group"}."""
    lists = {a: list(L) for a, L in lists.items() if L}
    rows, sample = rows_of(docs)
    model, group = ctx.model("pooled"), int(ctx.config.get("models.pooled.group", 40))
    out = {"docs": {sp: sample.count(sp) for sp in ("heldout", "construction")}, "lists": {a: len(L) for a, L in lists.items()},
           "pairs": 0, "judged": 0, "unjudged": 0, "requests": 0, "pending": [], "model": model, "group": group}
    if not rows:
        return out
    _, pairs = pairs_for(ctx, rows, lists)
    collect(ctx, quiet=True)
    yes, missing = judged(ctx, pairs)
    per_label = {}
    for _, L in missing:
        per_label[L] = per_label.get(L, 0) + 1
    out.update(pairs=len(pairs), judged=len(yes), unjudged=len(missing), requests=sum(-(-n // group) for n in per_label.values()),
               pending=[r for r in ctx.batch.pending(TAG, model) if r.get("occ") == (occ or ctx.occupation)])
    return out


def run(ctx, lists, docs, *, live=False, partial=False, workers=8):
    """The matrices and maps from the cache; a residual below batch.min pairs is judged live (`live=True` judges
    any residual live). Returns {"available": False, ...} when documents are absent or too many pairs are unjudged,
    unless `partial`: then every unjudged pair counts as not covering (nothing is judged) and the result carries
    `preliminary` with the true count."""
    lists = {a: list(L) for a, L in lists.items() if L}
    rows, sample = rows_of(docs)
    if not rows:
        return {"available": False, "why": "no documents"}
    cand, pairs = pairs_for(ctx, rows, lists)
    yes, missing = judged(ctx, pairs)
    min_batch = int(ctx.config.get("batch.min", 40))
    for attempt in range(3):
        if partial or not missing or not (len(missing) < min_batch or live):
            break
        groups = _groups(ctx, missing)
        print(f"  judging {len(missing):,} residual pairs live in {len(groups):,} call(s)" + (f" [pass {attempt + 1}]" if attempt else ""), flush=True)
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda g: _store(ctx, g[0], g[1], ctx.engine.chat(_prompt(ctx, *g), ctx.model("pooled"),
                                                                                max_out=min(16000, 250 * len(g[1]) + 400), json_mode=True)), groups))
            ctx.cache.save()
            yes, missing = judged(ctx, pairs)
        except Exception as e:                            # noqa: BLE001
            print(f"  live judging failed ({type(e).__name__}: {str(e)[:120]})", flush=True)
            break
    if missing and not partial and len(missing) > max(5, int(0.002 * len(pairs))):
        return {"available": False, "why": f"{len(missing):,} of {len(pairs):,} pooled pairs not judged yet (submit / collect first)",
                "missing": len(missing), "pairs": len(pairs)}
    meta = {d["id"]: d.get("meta") or {} for d in docs.docs}
    covariates = docs.covariates()
    res, mats = assemble(list(rows), sample, rows, lists, cand, yes, meta, covariates)
    res["uncached_pairs"] = {"total": len(missing), "treated_as": "not covering"}
    if partial:
        res["preliminary"] = {"unjudged": len(missing), "pairs": len(pairs), "built": time.strftime("%Y-%m-%d %H:%M")}
    res["judge"] = {"model": ctx.model("pooled"), "prompt": "coverage_batch", "group": int(ctx.config.get("models.pooled.group", 40)),
                    "section": SECTION, "pairs": len(pairs)}
    res["matrices"] = {a: M.astype(int).tolist() for a, M in mats.items()}
    return res


# ── the Batch API path ─────────────────────────────────────────────────────────────────────────────

def _groups_dir(ctx):
    return Path(ctx.batch.ledger_path).parent / "pooled_groups"


def submit(ctx, lists, docs, occ=""):
    """Submit the unjudged pooled pairs to the Batch API (never waits). -> batch ids."""
    lists = {a: list(L) for a, L in lists.items() if L}
    rows, _ = rows_of(docs)
    if not rows:
        return []
    _, pairs = pairs_for(ctx, rows, lists)
    collect(ctx, quiet=True)
    _, missing = judged(ctx, pairs)
    if not missing:
        print(f"  {len(pairs):,} pooled pairs, all judged -- nothing to submit")
        return []
    mine = [r for r in ctx.batch.pending(TAG, ctx.model("pooled")) if r.get("occ") == (occ or ctx.occupation)]
    if mine:
        print(f"  {len(missing):,} pairs unjudged, but {len(mine)} batch(es) for this occupation are still in the ledger -- not resubmitting")
        return []
    if len(missing) < int(ctx.config.get("batch.min", 40)):
        print(f"  {len(missing)} unjudged pairs, below batch.min; run() judges them live")
        return []
    groups = _groups(ctx, missing)
    reqs = [(sha256("␟".join([L, *atoms])), _body(ctx, L, atoms)) for L, atoms in groups]
    d = _groups_dir(ctx)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{occ or ctx.occupation}-{time.strftime('%Y%m%dT%H%M%S')}.json").write_text(
        json.dumps({cid: [L, atoms] for cid, (L, atoms) in zip((r[0] for r in reqs), groups)}))
    print(f"  submitting {len(missing):,} of {len(pairs):,} pooled pairs in {len(reqs):,} requests "
          f"(est. ${len(missing) * EST_PER_PAIR_BATCH:.2f} at Batch API prices)")
    old = ctx.config["batch"].get("chunk", 50000)
    ctx.config["batch"]["chunk"] = min(int(old), SUBMIT_CHUNK)
    try:
        return ctx.batch.submit(reqs, ctx.model("pooled"), TAG)
    finally:
        ctx.config["batch"]["chunk"] = old


def collect(ctx, quiet=False):
    """Harvest every finished pooled-covers batch into the cache -> n verdicts."""
    model = ctx.model("pooled")
    rows = ctx.batch.pending(TAG, model)
    if not rows:
        if not quiet:
            print("  no pooled covers batches in the ledger")
        return 0
    gmap = {}
    for p in sorted(_groups_dir(ctx).glob("*.json")):
        gmap.update(json.loads(p.read_text()))
    c = ctx.engine.client(model)
    total, left = 0, []
    for r in rows:
        try:
            b = c.batches.retrieve(r["id"])
        except Exception as e:                            # noqa: BLE001
            print(f"  ledger: could not retrieve {r['id']} ({type(e).__name__})")
            left.append(r)
            continue
        if b.status not in ("completed", "cancelled", "failed", "expired"):
            left.append(r)
            continue
        n = failed = unknown = 0
        if getattr(b, "output_file_id", None):
            for line in c.files.content(b.output_file_id).text.splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                resp = rec.get("response") or {}
                if rec.get("error") or resp.get("status_code") != 200:
                    failed += 1
                    continue
                g = gmap.get(rec.get("custom_id"))
                if not g:
                    unknown += 1
                    continue
                text = (((resp.get("body") or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                n += _store(ctx, g[0], g[1], text)
            ctx.cache.save()
        print(f"  ledger: {b.status} {r['id']} ({r.get('occ') or '?'}): {n:,} verdicts" + (f", {failed} failed" if failed else "")
              + (f", {unknown} without a group map" if unknown else ""))
        ctx.batch.forget(r["id"])
        total += n
    print(f"  collected {total:,} verdicts; {len(left)} batch(es) still running")
    return total


# ── axis names ─────────────────────────────────────────────────────────────────────────────────────

def name_axis(ctx, title, high, low):
    """-> the parsed reply dict, or None when the model gave nothing usable."""
    model = ctx.model("axis")
    prompt = ctx.prompt("axis_name", title=title, high="\n".join(f"- {t}" for t in high), low="\n".join(f"- {t}" for t in low))
    key = sha256("␟".join(["AXISNAME", model, prompt]))

    def compute():
        d = json.loads(strip_json(ctx.engine.chat(prompt, model, max_out=600, json_mode=True)) or "{}")
        if not isinstance(d, dict) or "interpretable" not in d:
            raise RuntimeError("axis name: unusable reply")
        return {"interpretable": bool(d.get("interpretable")), "high": str(d.get("high") or "").strip(),
                "low": str(d.get("low") or "").strip(), "axis": str(d.get("axis") or "").strip(), "why": str(d.get("why") or "").strip()}
    try:
        v = ctx.cache.memo(AXIS_SECTION, key, compute)
    except Exception as e:                                # noqa: BLE001
        print(f"    axis name: {str(e)[:80]}", file=sys.stderr)
        return None
    return v if isinstance(v, dict) else None


def axis_names(ctx, res, title):
    """Name the first two axes of every PCA / CA map in an incidence result -> {model, k, pca: {combo: [axis, axis]}, ca: {...}}."""
    if not res.get("available"):
        return {"available": False, "why": "no task map"}
    texts = {f"{arm}:{i}": r["statement"] for arm, rows in res["lists"].items() for i, r in enumerate(rows)}
    out = {"available": True, "model": ctx.model("axis"), "k": [K_MIN, K_MAX], "pca": {}, "ca": {}}
    for meth in ("pca", "ca"):
        for combo, r in (res.get(meth) or {}).items():
            if not r.get("available"):
                continue
            axes = []
            for coords in (r["x"], r["y"]):
                placed = [(v, texts[i]) for i, v in zip(r["ids"], coords) if v is not None and i in texts]
                if len(placed) < N_MIN:
                    axes.append(None)
                    continue
                placed.sort(key=lambda t: -t[0])
                k = max(K_MIN, min(K_MAX, len(placed) // 4))
                high = list(dict.fromkeys(t for _, t in placed[:k]))
                low = list(dict.fromkeys(t for _, t in placed[::-1][:k]))
                v = name_axis(ctx, title, high, low)
                axes.append({**v, "n_high": len(high), "n_low": len(low)} if v else None)
            out[meth][combo] = axes
    return out
