#!/usr/bin/env python3
"""Build the validation survey's input: one task list per occupation, keyed by O*NET-SOC.

    python validation/tools/adapters/build_inputs.py [--occ <slug> ...] [--n 0]

Reads results/aggregation/<occ>/final.json (core[].statement, prevalence) and writes
validation/tools/inputs/inventories.json. Without --occ, every occupation with a final.json is included.
A rater sees the list for the occupation they screened into.

--n 0 (the default) shows every task. --n k shows k tasks: ranked by prevalence, then k evenly spaced
across that ranking, so the sample spans common to rare tasks (a head cut keeps only the tasks nearly
everyone endorses). The sample is written to inventories.json as `sample`; with --n 0 it is absent and the
server shows `statements`.
"""
import argparse
import json
import os
import sys
from pathlib import Path

# validation/tools/, or $VALIDATION_DIR (relative to the repository root), as the server and the tools resolve it
_REPO = Path(__file__).resolve().parents[3]
VALIDATION = _REPO / os.environ["VALIDATION_DIR"] if os.environ.get("VALIDATION_DIR") else _REPO / "validation" / "tools"
INPUTS = VALIDATION / "inputs"
from tasknet.lib import REPO_ROOT as REPO, occupation_meta  # noqa: E402  — needs `pip install -e .`

CORE_FILE = "final.json"


def built_occupations():
    """Slugs that have results/aggregation/<slug>/final.json in the workspace. -> list[str]"""
    return sorted(p.parent.name for p in (REPO / "results" / "aggregation").glob(f"*/{CORE_FILE}"))


def spread(items, n):
    """N items evenly spaced across `items`, endpoints included, order preserved. -> list"""
    if n <= 0 or n >= len(items):
        return list(items)
    if n == 1:
        return [items[0]]
    step = (len(items) - 1) / (n - 1)       # > 1 because n < len(items), so no two indices round alike
    return [items[round(i * step)] for i in range(n)]


def build_occupation(slug, n):
    core_path = REPO / "results" / "aggregation" / slug / CORE_FILE
    if not core_path.exists():
        print(f"  SKIP {slug}: no {CORE_FILE}", file=sys.stderr)
        return None
    meta = occupation_meta(slug)
    if not meta.get("soc"):
        print(f"  SKIP {slug}: no O*NET-SOC resolved — it cannot be routed to from a screener", file=sys.stderr)
        return None
    core = json.loads(core_path.read_text())["core"]
    # Prevalence-ranked, most-reported first; a task with no prevalence sorts last.
    nodes = sorted(({"statement": c["statement"], "prevalence": c.get("prevalence")} for c in core),
                   key=lambda x: (-(x["prevalence"] or 0), x["statement"]))
    statements = [x["statement"] for x in nodes]
    # n <= 0: no `sample` key, and the server shows `statements`.
    sample = spread(statements, n) if n > 0 else None
    entry = {
        "soc": meta["soc"],
        "title": meta["title"] or slug,
        "slug": slug,
        "source": f"{CORE_FILE} ({len(statements)} tasks)",
        "statements": statements,
        "nodes": nodes,
    }
    if sample is not None:
        entry["sample"] = sample
    return meta["soc"], entry


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=0,
                    help="statements shown per participant; 0 (default) = every task, no subsample")
    ap.add_argument("--occ", nargs="*",
                    help="occupation slugs to include (default: every occupation with a final.json)")
    args = ap.parse_args()
    occs = args.occ or built_occupations()
    if not occs:
        raise SystemExit(f"no results/aggregation/*/{CORE_FILE} under {REPO}; pass --occ or build a list first")

    # Attention checks (hand-written statements from unrelated occupations) are carried over from the
    # existing file; nothing here generates them.
    prev = {}
    inv_path = INPUTS / "inventories.json"
    if inv_path.exists():
        prev = json.loads(inv_path.read_text())
    checks = prev.get("attentionChecks") or []
    if not checks:
        print("  WARNING no attentionChecks carried over — the study loses its quality screen", file=sys.stderr)

    occupations = {}
    for slug in occs:
        built = build_occupation(slug, args.n)
        if built:
            soc, entry = built
            occupations[soc] = entry

    if not occupations:
        raise SystemExit("no occupations built — nothing to write")

    out = {
        "_source": {
            "built_from": f"results/aggregation/<slug>/{CORE_FILE}",
            "note": "one list per occupation, selected by the participant's screened O*NET-SOC",
            "sample_n": args.n,
            "sample_rule": ("no subsample — every task is shown" if args.n <= 0 else
                            "prevalence-ranked, then N evenly spaced (see build_inputs.py::spread)"),
        },
        "occupations": occupations,
        "attentionChecks": checks,
    }
    INPUTS.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    how = f"{args.n} statements shown each" if args.n > 0 else "EVERY task shown (no subsample)"
    print(f"inventories.json: {len(occupations)} occupations, {how}")
    for soc, e in sorted(occupations.items()):
        shown = len(e.get("sample") or e["statements"])
        print(f"  {soc}  {e['title'][:44]:46s} {len(e['statements']):3d} tasks -> {shown} shown")
    print(f"  attention checks carried over: {len(checks)}")


if __name__ == "__main__":
    main()
