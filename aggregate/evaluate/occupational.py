"""occupational — is each statement an occupational task of this occupation, or an activity PRIMARILY performed
by one of the occupations related to it?

    res = occupational.run(ctx, lists, title, description, related)
    # related = [{"title": ..., "code": ..., "description": ...}]

One judgement per statement of every list: the occupation's description and its related occupations (with
descriptions) go in the system message, the statement in the user message. The default verdict is
occupational; false only when the activity is primarily a related occupation's work.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from string import Template

from ..engine import bare_model
from ..lib import sha256, strip_json
from . import stats

SECTION = "occupational_postings"
_CODE = re.compile(r"\s*\(?\d{2}-\d{4}\.\d{2}\)?\s*$")


def _template(ctx):
    lines = ctx.prompt("occupational_specificity").splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "\n".join(lines[i:]).strip()


def system_prompt(ctx, title, description, related, code=""):
    rel = "\n".join(f"- {r['title']} ({r.get('code') or r.get('soc', '')}): {r.get('description', '')}" for r in related) or "- (none listed)"
    return Template(_template(ctx)).safe_substitute(title=title, soc=code, description=description or "(no description)", related=rel)


def _fields(p):
    occ = p.get("occupational")
    if isinstance(occ, str):
        occ = occ.strip().lower() in {"true", "yes", "y"}
    if not isinstance(occ, bool):
        raise ValueError("occupational not boolean")
    by = p.get("primarily_performed_by") or []
    if isinstance(by, str):
        by = [by]
    return {"occupational": occ, "by": [str(a).strip() for a in by if str(a).strip()], "rationale": str(p.get("rationale") or "")[:300]}


def _titles(also, titles):
    known = {t.lower(): t for t in titles}
    out = []
    for a in also:
        a = _CODE.sub("", a).strip().rstrip(",;")
        if a:
            out.append(known.get(a.lower(), a))
    return list(dict.fromkeys(out))


def judge(ctx, sys_prompt, task):
    model, effort = ctx.model("occupational"), ctx.config.get("models.occupational.effort", "") or ""
    key = sha256("␟".join(["OCCUPATIONAL", bare_model(model), effort, sha256(sys_prompt)[:12], task]))

    def compute():
        raw = ctx.engine.chat(f"Task statement: {task}", model, system=sys_prompt, max_out=600, json_mode=True, effort=effort or None)
        try:
            return _fields(json.loads(strip_json(raw) or "{}"))
        except (ValueError, TypeError, AttributeError) as e:
            raise RuntimeError(f"occupational: unusable reply ({e})")
    return ctx.cache.memo(SECTION, key, compute)


def run(ctx, lists, title, description="", related=(), *, code="", B=None):
    B = int(ctx.config.get("evaluate.B_statements", 4000)) if B is None else B
    related = list(related)
    titles = [r["title"] for r in related]
    sp = system_prompt(ctx, title, description, related, code)
    res = {"model": ctx.model("occupational"), "related": related, "lists": {}, "per_statement": {}}
    flags = {}

    def one(t):
        try:
            return judge(ctx, sp, t)
        except Exception as e:                            # noqa: BLE001
            print(f"    occupational: {str(e)[:80]} — {t[:60]}", file=sys.stderr)
            return None
    for arm, L in lists.items():
        if not L:
            continue
        with ThreadPoolExecutor(max_workers=ctx.workers_for("evaluate")) as ex:
            v = dict(zip(L, ex.map(one, L)))
        res["per_statement"][arm] = {s: (dict(v[s], by=_titles(v[s]["by"], titles)) if v[s] else None) for s in L}
        flags[arm] = [1.0 if v[s]["occupational"] else 0.0 for s in L if v[s] is not None]
        m, se = stats.boot_statements(flags[arm], B)
        res["lists"][arm] = {"n": len(L), "n_judged": len(flags[arm]), "n_occupational": int(sum(flags[arm])), "share": m, "share_se": se}
    arms = list(flags)
    res["gaps"] = {}
    for a, b in stats.arm_pairs(arms):
        g = stats.gap_statements(flags[a], flags[b], B)
        if g:
            res["gaps"][f"{a}-{b}"] = g
    return res
