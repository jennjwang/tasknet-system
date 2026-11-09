"""pipeline — the TaskNet System's two model-calling steps over aggregate.

    process(occ)   sessions -> results/processing/<occ>/{1_ingested,2_normalized,3_deduped}.csv
    build(occ)     3_deduped.csv -> results/aggregation/<occ>/build.json (never final.json: adopt by copying)

aggregate (the git submodule at aggregate/) does the work: aggregate.processing.normalize (split, filter,
rewrite, debrand), aggregate.processing.dedup, and Aggregator.build (the chain). This module adds the
interview study's own parts: where the sessions and outputs live, the TaskNet keys (tasknet.env), the O*NET
reference statements (tasknet.onet), the held-out freeze guard, and a configs.yml overlay in the workspace.
The CSVs keep their legacy column names (session_id, task_name, onet_code, onet_title, original_task_name).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from aggregate import Aggregator, Atoms, Config, Prompts
from aggregate.processing import dedup, normalize

from . import env, onet
from .ingest import read_sessions
from .paths import cache_dir, env_path, interviews_dir, load_env, results_dir, workspace


def config(**overrides) -> Config:
    """aggregate's defaults, then the workspace's configs.yml (git-ignored) if there is one, then `overrides`."""
    p = workspace() / "configs.yml"
    return Config.load(p if p.exists() else None, overrides=overrides or None)


def prompts(cfg: Config) -> Prompts:
    """aggregate's prompts, then the workspace's prompts/ (if any) and the config's prompt_dirs (relative to the
    workspace). A file placed FLAT in one of those, e.g. prompts/task_dimensions.txt, replaces the packaged prompt
    of that name, which sits in a subdirectory; that is how the task definition is changed without editing the
    submodule."""
    extra = [workspace() / "prompts"] if (workspace() / "prompts").is_dir() else []
    extra += [workspace() / d for d in (cfg.get("prompt_dirs") or [])]
    return Prompts.default(extra=extra, prompt_set=cfg.get("prompt_set", "") or "")


def aggregator(workdir=None, *, client=None, **overrides) -> Aggregator:
    """An Aggregator over the workspace's config, prompts and cache, with the TaskNet keys (tasknet.env);
    `client` replaces every model client (the audit passes one that refuses calls)."""
    load_env()
    cfg = config(**overrides)
    return Aggregator(cfg, router=None if client else env.router, client=client, workdir=workdir,
                      cache_dir=cache_dir(), prompts=prompts(cfg))


# ── process ───────────────────────────────────────────────────────────────────────────────────────

def process(occupation: str, data_dir=None) -> Path:
    """Interview sessions -> one row per reported task. -> the path of 3_deduped.csv.
    `data_dir` holds one <session>.json per interview; default $DATA_DIR/interviews/<occupation>."""
    src = Path(data_dir) if data_dir else interviews_dir() / occupation
    if not src.is_dir():
        raise SystemExit(f"no session directory {src} (set DATA_DIR, or pass --data-dir)")
    out = results_dir("processing", occupation)
    rows = read_sessions(src)
    if not rows:
        raise SystemExit(f"no tasks in any session under {src}")
    print(f"{occupation}: {len(rows)} raw tasks from {len({r['source'] for r in rows})} sessions in {src}")
    Atoms(rows).write(out / "1_ingested.csv", legacy=True)
    agg = aggregator()
    normalized = normalize(agg.context(), rows)
    normalized.write(out / "2_normalized.csv", legacy=True)
    atoms = dedup(normalized)
    atoms.write(out / "3_deduped.csv", legacy=True)
    agg.save()
    print(f"→ {out / '3_deduped.csv'}  ({len(atoms)} distinct tasks, {len(atoms.rows)} rows)")
    print(agg.usage())
    return out / "3_deduped.csv"


# ── build ─────────────────────────────────────────────────────────────────────────────────────────

def frozen_heldout(occupation: str) -> int:
    """How many held-out workers a rebuild would absorb (0 if this occupation's list is not frozen). The
    published lists are frozen so the evaluation's held-out sample stays held out."""
    p = env_path("HELDOUT_FROZEN", workspace() / "results" / "evaluation" / "heldout_frozen.json")
    try:
        o = json.loads(p.read_text())["occupations"].get(occupation)
        return int(o["n_held_out_at_freeze"]) if o else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def build(occupation: str, *, fresh: bool = False, max_cycles: int | None = None, batch: bool = False) -> Path:
    """3_deduped.csv -> the task list. -> the path of build.json. AMENDS an adopted final.json unless `fresh`.
    `batch` sends the gpt fan-outs through the OpenAI Batch API (half price, blocks until each batch is done)."""
    if os.environ.get("SPEND_HOLDOUT", "0") != "1" and (n := frozen_heldout(occupation)) > 0:
        raise SystemExit(f"REFUSING: {occupation}'s list is frozen; rebuilding would absorb its {n} held-out "
                         "workers. Re-run with SPEND_HOLDOUT=1 to proceed anyway.")
    atoms_csv = results_dir("processing", occupation) / "3_deduped.csv"
    if not atoms_csv.exists():
        raise SystemExit(f"{atoms_csv} not found — run `tasknet process {occupation}` first")
    workdir = results_dir("aggregation", occupation)
    atoms = Atoms.read(atoms_csv)
    standing = json.loads((workdir / "final.json").read_text()) if (workdir / "final.json").exists() else None
    try:
        reference = onet.group_statements(atoms.code)
    except FileNotFoundError as e:                        # O*NET not fetched: the umbrella check runs unattested
        print(f"  (umbrella-verb attestation unavailable: {e})", file=sys.stderr)
        reference = []
    agg = aggregator(workdir=workdir, **({"batch": {"api": True}} if batch else {}))
    built = agg.build(atoms, standing=standing, fresh=fresh, max_cycles=max_cycles, reference_statements=reference)
    agg.save()
    print(f"→ {workdir / 'build.json'}  ({len(built['core'])} statements; diff against final.json, adopt by copying)")
    print(agg.usage())
    return workdir / "build.json"


def verify(occupation: str, which: str = "build") -> list[str]:
    """aggregate's invariants over a built list. -> the failures ([] = sound)."""
    atoms = Atoms.read(results_dir("processing", occupation) / "3_deduped.csv")
    art = json.loads((results_dir("aggregation", occupation) / f"{which}.json").read_text())
    return aggregator().verify(art, atoms)
