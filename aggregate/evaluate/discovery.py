"""discovery — what distinguishes the tasks one method finds and another does not? A WITHIN-LIST contrast.

    res = discovery.run(ctx, rows)
    # rows: [{"occupation": slug, "title": ..., "inv": arm, "text": statement, "rel_<arm>": bool|None, <feature>: number}]

Every statement is tagged with whether the OTHER list(s) have a related statement (`rel_<arm>`, from a
crosswalk), and features are compared WITHIN one list between its related and unrelated statements, pooled
over occupations with occupation fixed effects and a permutation test that shuffles the flag within occupation.
Features: the Y/N codes below (one model pass per statement) plus every numeric key the rows carry.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..engine import bare_model
from ..lib import sha256, strip_json
from . import stats

CODES = [
    ("coordination", "Coordination (paper)", "Passes work, information, or responsibility to or from someone else: handoffs, referrals, escalations, status updates, passing work to the next stage. Not communication that is itself the service."),
    ("teaching_development", "Teaching and professional development (paper)", "Builds the expertise of colleagues, trainees, or the worker themselves: training, mentoring, evaluating or onboarding colleagues; continuing education; safety refreshers. Not teaching clients as the core service."),
    ("oversight_qa", "Oversight and quality assurance (paper)", "Checks, approves, or corrects work, or responds when something goes wrong: reviewing or approving others' work, audits, compliance checks, safety reviews, incident response. Not revising one's own work."),
    ("new_technology", "New technology (paper)", "Depends on a tool or practice that became common in roughly the last 15 years and creates a task of its own: AI and language models, CI/CD, cloud infrastructure, email when the task is defined by it. Not long-standing tools named in passing."),
    ("specialized", "Specialized (paper, O*NET sheet)", "Specialized or role-specific work done by only a subset of incumbents, rarely, or by a senior or managerial version of the role. Not routine work most incumbents do."),
    ("dated", "Dated (paper, O*NET sheet)", "The function persists but in vocabulary that predates current practice: names an older technology or form of work organization. Not wording that is merely formal."),
    ("employer_facing", "Employer-facing expectation (new)", "Phrased as an expectation an employer writes into a job ad: compliance with policies or regulations, reporting or documentation for management, meeting targets, representing the company. Not the hands-on work itself."),
    ("tacit_invisible", "Tacit or invisible work (new)", "Backgrounded work that keeps the job running but is rarely written into a job description: informal helping, workarounds, covering for others, keeping things tidy or stocked, emotional labour, waiting or watching. Not a named deliverable."),
    ("physical_manual", "Physical or manual (new)", "Requires physical handling of materials, equipment, people or spaces as the core of the task."),
    ("client_facing", "Client-facing (new)", "Direct interaction with clients, patients, customers, students or the public is the core of the task. Not internal colleagues."),
    ("tool_specific", "Tool-specific (new)", "Names a specific tool, system, software category, instrument or document type that the task is performed with or on."),
    ("routine", "Routine and recurring (new)", "A recurring, scheduled or daily activity rather than an occasional, project-based or exceptional one."),
    ("managerial_scope", "Managerial scope (new)", "Supervising staff, managing budgets or vendors, setting strategy or policy: work above the occupation's core activities."),
    ("generic_workplace", "Generic workplace activity (new)", "An activity workers of nearly any occupation do: attending meetings, keeping the workplace safe, timesheets, following policies, communicating with colleagues in general."),
]
SECTION = "task_codes"
FEATURE_NAMES = {"beta": "Eloundou-β", "rl": "RL feasibility", "words": "Words", "emp": "Workers", "atoms": "Atoms",
                 "core": "Core task", "incumbents": "Incumbents responding", "IM": "Importance (IM, 1–5)", "RT": "Relevance (RT, %)"}


def system_prompt():
    return ("You are coding occupational task statements against a codebook. Code the statement AS WRITTEN; do not infer duties it does "
            "not name. For EACH code, apply its one-line test and answer Y or N. Codes may overlap. Reply with ONE JSON object: "
            '{"codes": {"<code>": "Y"|"N", ...}} with every code present.\n\nCODEBOOK:\n'
            + "\n".join(f"- {k} — {name}: {test}" for k, name, test in CODES))


def code_statement(ctx, text, title):
    model = ctx.model("coder")
    sysp = system_prompt()
    key = sha256("␟".join(["TASKCODES", bare_model(model), sha256(sysp)[:10], title, text]))

    def compute():
        raw = ctx.engine.chat(f"Occupation: {title}\n\nTask statement: {text}", model, system=sysp, max_out=600, json_mode=True, effort="low")
        d = json.loads(strip_json(raw) or "{}").get("codes") or {}
        if not any(k in d for k, _, _ in CODES):
            raise RuntimeError("coder returned no codes")
        return {k: str(d.get(k, "N")).strip().upper().startswith("Y") for k, _, _ in CODES}
    return ctx.cache.memo(SECTION, key, compute)


def contrast(rows, inv, flag, feature_fn, perms=2000, seed=0):
    """Within-occupation difference of a feature between flag True and False statements of `inv`."""
    R = [r for r in rows if r["inv"] == inv and r.get(flag) is not None and feature_fn(r) is not None]
    if not R:
        return None
    occs = sorted({r["occupation"] for r in R})
    by = {o: [r for r in R if r["occupation"] == o] for o in occs}
    keep = {o for o in occs if any(r[flag] for r in by[o]) and any(not r[flag] for r in by[o])}
    R = [r for r in R if r["occupation"] in keep]
    if not R:
        return None
    y = np.array([float(feature_fn(r)) for r in R])
    g = np.array([bool(r[flag]) for r in R])
    occ = np.array([r["occupation"] for r in R])
    yd = y.copy()
    for o in keep:
        m = occ == o
        yd[m] = y[m] - y[m].mean()
    obs = float(yd[g].mean() - yd[~g].mean())
    rng = np.random.default_rng(seed)
    draws = np.empty(perms)
    idx_by = {o: np.where(occ == o)[0] for o in keep}
    gp = g.copy()
    for b in range(perms):
        for o, ix in idx_by.items():
            gp[ix] = g[ix][rng.permutation(len(ix))]
        draws[b] = yd[gp].mean() - yd[~gp].mean()
    p = float((np.abs(draws) >= abs(obs)).mean())
    return {"n1": int(g.sum()), "n0": int((~g).sum()), "mean1": float(y[g].mean()), "mean0": float(y[~g].mean()),
            "diff": obs, "p": max(p, 1 / (perms + 1)), "n_occ": len(keep)}


def run(ctx, rows, *, features=None, workers=24):
    """Code every row, then contrast every numeric feature and every code within each list by each rel_ flag."""
    rows = [dict(r) for r in rows]
    perms = int(ctx.config.get("evaluate.permutations", 2000))

    def safe(r):
        try:
            return code_statement(ctx, r["text"], r.get("title", ""))
        except Exception as e:                            # noqa: BLE001
            print(f"    coder: {str(e)[:60]} — {r['text'][:50]}", file=sys.stderr)
            return None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r, c in zip(rows, ex.map(safe, rows)):
            r["codes"] = c
    ctx.cache.save()
    invs = list(dict.fromkeys(r["inv"] for r in rows))
    flags = sorted({k for r in rows for k in r if k.startswith("rel_")})
    numeric = features or sorted({k for r in rows for k, v in r.items()
                                  if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ("six",)})
    contrasts = {}
    for inv in invs:
        for flag in flags:
            if flag == f"rel_{inv}":
                continue
            feats = [(k, FEATURE_NAMES.get(k, k), (lambda k: (lambda r: r.get(k)))(k)) for k in numeric]
            feats += [(k, name, (lambda k: (lambda r: None if not r.get("codes") else float(r["codes"].get(k, False))))(k)) for k, name, _ in CODES]
            out = {}
            for k, name, fn in feats:
                c = contrast(rows, inv, flag, fn, perms)
                if c:
                    out[k] = {"name": name, **c}
            if out:
                contrasts[f"{inv}|{flag}"] = {"label": f"{inv}: {flag[4:]}-related vs not", "features": out}
    res = {"model": ctx.model("coder"), "codes": [{"key": k, "name": n, "test": t} for k, n, t in CODES], "n_rows": len(rows),
           "contrasts": contrasts, "rows": rows}
    res["markdown"] = markdown(res)
    return res


def markdown(res):
    md = ["# What distinguishes the tasks one method finds\n",
          f"{res['n_rows']} statements; codes rated by {res['model']}; within-list contrasts with occupation fixed effects and a "
          "within-occupation permutation test (p).\n", "## Hypotheses\n"]
    md += [f"- **{c['name']}** — {c['test']}" for c in res["codes"]]
    pct_keys = {c["key"] for c in res["codes"]}
    for key, c in res["contrasts"].items():
        feats = c["features"]
        v0 = next(iter(feats.values()))
        md += ["", f"## {c['label']}", f"{v0['n1']} related vs {v0['n0']} unrelated statements, {v0['n_occ']} occupations.", "",
               "| feature | related | unrelated | difference (FE) | p |", "|---|---|---|---|---|"]
        for k, v in sorted(feats.items(), key=lambda kv: kv[1]["p"]):
            pct = k in pct_keys
            f = (lambda x: f"{x * 100:.0f}%") if pct else (lambda x: f"{x:.2f}" if abs(x) < 10 else f"{x:.1f}")
            diff = f"{v['diff'] * 100:+.0f} pp" if pct else ("+" if v["diff"] >= 0 else "") + f(v["diff"])
            md.append(f"| {v['name']} | {f(v['mean1'])} | {f(v['mean0'])} | {diff} {stats.stars(v['p'])} | {v['p']:.3f} |")
    return "\n".join(md) + "\n"
