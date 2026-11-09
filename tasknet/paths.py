"""Where things live: the workspace (results/, cache/, data/, .env), the interview sessions, the stage outputs.

The workspace is $WORKSPACE if set; else the source checkout this package sits in (the first parent holding
pyproject.toml and tasknet/); else the current directory, which is what an installed package gets.
"""
from __future__ import annotations

import os
from pathlib import Path


def workspace() -> Path:
    env = os.environ.get("WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    for d in Path(__file__).resolve().parents:
        if (d / "tasknet").is_dir() and (d / "pyproject.toml").is_file():
            return d
    return Path.cwd()


def env_path(name: str, default: Path) -> Path:
    """$name as a path, else `default`. A relative value is taken from the workspace (where .env lives), not
    from the directory the command happens to run in."""
    v = os.environ.get(name, "").strip()
    return workspace() / Path(v).expanduser() if v else default


def interviews_dir() -> Path:
    """$DATA_DIR/interviews, else data/interviews in the workspace: one directory per occupation slug."""
    return env_path("DATA_DIR", workspace() / "data") / "interviews"


def results_dir(kind: str, occupation: str) -> Path:
    """results/<kind>/<occupation>/. A simulated-worker run (a slug ending in `-sim`) goes under
    results/sim/<kind>/<slug>/, keeping real interview outputs apart."""
    if occupation.endswith("-sim"):
        return workspace() / "results" / "sim" / kind / occupation[:-len("-sim")]
    return workspace() / "results" / kind / occupation


def cache_dir() -> Path:
    """The verdict cache: $LLM_CACHE_DIR, else cache/aggregation in the workspace. Normalize's caches sit beside
    it, under cache/processing/."""
    return env_path("LLM_CACHE_DIR", workspace() / "cache" / "aggregation")


def load_env() -> None:
    """Load the workspace's .env into os.environ without overriding variables already set."""
    p = workspace() / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
