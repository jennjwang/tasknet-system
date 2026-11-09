"""exposure — two AI-exposure rubrics rated once per statement, averaged per list.

    res = exposure.run(ctx, lists, title, crosswalk={"reference": frame})

  eloundou   prompts/evaluation/exposure/exposure_eloundou.txt: E0 / E1 / E2; beta = E1 + 0.5 * E2, on 0-1
  rl         prompts/evaluation/exposure/exposure_rl.txt: a physical gate, then D1..D8 on 1-10;
             score = (mean(D) - 1) / 9 * 100, 0 when the gate fails
Level = the unweighted mean over a list's statements, SE by resampling statements. With a crosswalk frame per
other list, the gap ours - other is split into omitting the other's unrelated tasks, adding new statements,
and shared activities.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

from ..engine import bare_model
from ..lib import sha256, strip_json
from . import stats

RL_DIMS = [f"d{i}" for i in range(1, 9)]
SECTION = "exposure_postings"
METRICS = ("eloundou", "rl")


def system_prompt(ctx, metric):
    lines = ctx.prompt(f"exposure_{metric}").splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:]).strip()


def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {v!r}")
    if not (lo <= x <= hi):
        raise ValueError(f"out of range: {x}")
    return x


def _fields(metric, p):
    if metric == "eloundou":
        label = str(p.get("label", "")).strip().upper()
        if label not in {"E0", "E1", "E2", "E3"}:
            raise ValueError(f"bad label {label!r}")
        e1, e2 = float(label == "E1"), float(label in {"E2", "E3"})
        return {"label": label, "beta": e1 + 0.5 * e2, "rationale": str(p.get("rationale") or "")[:300]}
    pf = p.get("physical_feasibility") or {}
    gate = pf.get("pass")
    if not isinstance(gate, bool):
        raise ValueError("physical_feasibility.pass not boolean")
    if not gate:
        return {"gate": False, "score": 0.0, "dims": {}, "rationale": str(pf.get("justification") or "")[:300]}
    dims = p.get("dimensions")
    if not isinstance(dims, dict):
        raise ValueError("dimensions missing")
    by_key = {str(k).strip().lower()[:2]: v for k, v in dims.items()}
    vals = {d: _num(by_key[d].get("score") if isinstance(by_key.get(d), dict) else by_key.get(d), 1, 10) for d in RL_DIMS}
    return {"gate": True, "score": (sum(vals.values()) / len(vals) - 1) / 9 * 100, "dims": vals, "rationale": str(pf.get("justification") or "")[:300]}


def rate(ctx, metric, task, title):
    model, effort = ctx.model("exposure"), ctx.config.get("models.exposure.effort", "") or ""
    sys_prompt = system_prompt(ctx, metric)
    key = sha256("␟".join(["EXPOSURE", metric, sha256(sys_prompt)[:10], bare_model(model), effort, title, task]))

    def compute():
        raw = ctx.engine.chat(f"Occupation: {title}\n\nTask: {task}", model, system=sys_prompt, max_out=4000, json_mode=True, effort=effort or None)
        try:
            return _fields(metric, json.loads(strip_json(raw) or "{}"))
        except (ValueError, TypeError, AttributeError) as e:
            raise RuntimeError(f"exposure {metric}: unusable reply ({e})")
    return ctx.cache.memo(SECTION, key, compute)


def run(ctx, lists, title, *, crosswalk=None, B=None):
    """`crosswalk`: {arm: frame} from crosswalk.run(ours, lists[arm]) for the decomposition (optional)."""
    B = int(ctx.config.get("evaluate.B_statements", 4000)) if B is None else B
    crosswalk = crosswalk or {}
    res = {"model": ctx.model("exposure"), "lists": {}, "per_statement": {}}
    scores = {}

    def rate_all(metric, L):
        def one(t):
            try:
                return rate(ctx, metric, t, title)
            except Exception as e:                        # noqa: BLE001
                print(f"    exposure {metric}: {str(e)[:80]} — {t[:60]}", file=sys.stderr)
                return None
        with ThreadPoolExecutor(max_workers=ctx.workers_for("evaluate")) as ex:
            return dict(zip(L, ex.map(one, L)))
    for arm, L in lists.items():
        if not L:
            continue
        el, rl = rate_all("eloundou", L), rate_all("rl", L)
        scores[arm] = {s: {"beta": (el[s] or {}).get("beta"), "rl": (rl[s] or {}).get("score"),
                           "label": (el[s] or {}).get("label", ""), "gate": (rl[s] or {}).get("gate")} for s in L}
        bm, bse = stats.boot_statements([v["beta"] for v in scores[arm].values()], B)
        rm, rse = stats.boot_statements([v["rl"] for v in scores[arm].values()], B)
        res["lists"][arm] = {"n": len(L), "beta": bm, "beta_se": bse, "rl": rm, "rl_se": rse}
        res["per_statement"][arm] = scores[arm]
    arms = list(scores)
    ours = arms[0] if arms else None
    mean = lambda a: (sum(a) / len(a)) if a else float("nan")
    res["decomposition"] = {}
    for target, fr in crosswalk.items():
        if not fr or target not in scores or ours is None:
            continue
        cls, missing = fr["classes"], set(fr.get("target_missing") or [])
        for metric in ("beta", "rl"):
            o_all = [v[metric] for v in scores[target].values() if v[metric] is not None]
            o_rel = [v[metric] for s, v in scores[target].items() if v[metric] is not None and s not in missing]
            s_all = [v[metric] for v in scores[ours].values() if v[metric] is not None]
            s_rel = [v[metric] for s, v in scores[ours].items() if v[metric] is not None and cls.get(s, "new") != "new"]
            res["decomposition"][f"{target}_{metric}"] = {"gap": mean(s_all) - mean(o_all), "omit_target_only": mean(o_rel) - mean(o_all),
                                                        "add_new": mean(s_all) - mean(s_rel), "shared": mean(s_rel) - mean(o_rel)}
    res["gaps"] = {}
    for a, b in stats.arm_pairs(arms):
        for metric in ("beta", "rl"):
            g = stats.gap_statements([v[metric] for v in scores[a].values()], [v[metric] for v in scores[b].values()], B)
            if g:
                res["gaps"][f"{a}-{b}_{metric}"] = g
    return res
