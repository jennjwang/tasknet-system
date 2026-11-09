"""The TaskNet workspace: where interviews are read from and results/ and cache/ are written, and the Aggregator
configured for one occupation.

    ws = workspace.root()                          # $WORKSPACE, else this checkout, else the current directory
    workspace.processing_dir("lawyers")            # results/processing/lawyers/
    workspace.aggregation_dir("lawyers")           # results/aggregation/lawyers/
    agg = workspace.aggregator("lawyers")          # an Aggregator over the workspace's cache and settings

Settings come from the environment and the workspace's .env (variables already set win): the API keys
(.env.example), DATA_DIR (the interviews), REFERENCE_DATA_DIR (the O*NET download), LLM_CACHE_DIR (the
verdict cache), PREWARM (0 turns off prompt-cache warm-up calls) and TASKNET_CONFIG (a YAML overlaid on
configs.yml: models, thresholds, batching).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .lib import load_env


def root() -> Path:
    """$WORKSPACE if set; else the source checkout this file sits in (the first parent holding pyproject.toml
    and aggregate/); else the current directory, which is what an installed package gets. -> Path"""
    env = os.environ.get("WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    for d in Path(__file__).resolve().parents:
        if (d / "aggregate").is_dir() and (d / "pyproject.toml").is_file():
            return d
    return Path.cwd()


def load_dotenv() -> None:
    load_env(root() / ".env")


def env_path(name: str, default: Path) -> Path:
    """$name as a path, else `default`. A relative value is taken from the workspace (where .env lives), not
    from the directory the command happens to run in. -> Path"""
    v = os.environ.get(name, "").strip()
    return root() / Path(v).expanduser() if v else default


def interviews_dir() -> Path:
    """$DATA_DIR/interviews, else data/interviews in the workspace: one folder of session JSONs per occupation."""
    return env_path("DATA_DIR", root() / "data") / "interviews"


def _results(kind: str, occ: str) -> Path:
    # simulated-worker runs (a slug ending in -sim) go under results/sim/, apart from real interviews
    if occ.endswith("-sim"):
        return root() / "results" / "sim" / kind / occ[:-len("-sim")]
    return root() / "results" / kind / occ


def processing_dir(occ: str) -> Path:
    return _results("processing", occ)


def aggregation_dir(occ: str) -> Path:
    return _results("aggregation", occ)


def cache_dir() -> Path:
    """The verdict cache: $LLM_CACHE_DIR, else cache/aggregation (normalize's cache sits beside it)."""
    return env_path("LLM_CACHE_DIR", root() / "cache" / "aggregation")


def occupation_meta(occ: str) -> dict:
    """The O*NET-SOC code and title for an occupation slug. -> {occ, soc, title}

    The SOC comes from results/crosswalk/<occ>/scoped.json if it exists, else from matching the slug against
    the slugified O*NET titles. Never raises: without a match the SOC is empty and the title the de-slugged name."""
    soc = ""
    scoped = root() / "results" / "crosswalk" / occ / "scoped.json"
    if scoped.exists():
        try:
            soc = (json.loads(scoped.read_text()).get("soc") or "").strip()
        except Exception:                                  # noqa: BLE001
            soc = ""
    title = ""
    try:
        from . import onet_db
        slug = lambda t: re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t.lower())).strip("-")
        if not soc:
            soc = next((s for s, t in onet_db.socs().items() if slug(t) == occ), "")
        title = onet_db.title(soc) if soc else ""
    except Exception:                                      # noqa: BLE001 - no O*NET file: the slug is still readable
        pass
    return {"occ": occ, "soc": soc, "title": title or occ.replace("-", " ").title()}


def config(**overrides):
    """configs.yml, then $TASKNET_CONFIG, then `overrides`; PREWARM=0 turns the warm-up calls off."""
    from .config import Config
    path = os.environ.get("TASKNET_CONFIG", "").strip() or None
    if os.environ.get("PREWARM", "").strip() == "0":
        overrides = {"prewarm": {"on": False}, **overrides}
    return Config.load(env_path("TASKNET_CONFIG", Path(path)) if path else None, overrides=overrides or None)


def aggregator(occ: str, soc: str = "", **overrides):
    """An Aggregator for one occupation: its results/aggregation/<occ>/ workdir, the workspace's verdict cache,
    and O*NET's statements for the occupation's SOC major group as the umbrella-verb reference (none without
    the O*NET file, in which case every caution verb reads as an umbrella)."""
    from . import Aggregator, onet_db
    load_dotenv()
    soc = soc or occupation_meta(occ)["soc"]
    try:
        reference = onet_db.statements_in_group(soc[:2]) if soc else []
    except FileNotFoundError as e:
        print(f"  (umbrella attestation unavailable: {e})")
        reference = []
    return Aggregator(config(**overrides), workdir=aggregation_dir(occ), cache_dir=cache_dir(),
                      reference_statements=reference)
