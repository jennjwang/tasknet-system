"""TaskNet System: turn worker interviews into an occupational task list.

    import tasknet
    tasknet.process("lawyers", data_dir="interviews/lawyers")   # sessions -> results/processing/lawyers/3_deduped.csv
    tasknet.build("lawyers")                                     # reports -> results/aggregation/lawyers/build.json
    tasks = tasknet.load_tasks("lawyers", which="build")

The same steps from a shell: `tasknet process`, `tasknet build`, `tasknet audit`, `tasknet verify`.

Outputs, caches and `.env` live in the workspace: `home=` / $WORKSPACE, else the current directory (or the
source checkout, when running from one). Every stage runs as its own process, as the stage CLIs do, because
each reads its settings (occupation, models, cache paths) once at import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

__version__ = "0.1.0"
__all__ = ["process", "build", "audit", "load_tasks", "workspace"]


def _env(home, **extra) -> dict:
    env = dict(os.environ)
    if home is not None:
        env["WORKSPACE"] = str(Path(home).expanduser().resolve())
    env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def _run(module: str, args: list[str], env: dict, check: bool = True) -> int:
    r = subprocess.run([sys.executable, "-m", module, *args], env=env)
    if check and r.returncode:
        raise RuntimeError(f"{module} failed (exit {r.returncode})")
    return r.returncode


def workspace(home=None) -> Path:
    """The directory results/, cache/ and .env are read from and written to."""
    if home is not None:
        return Path(home).expanduser().resolve()
    from tasknet.lib import REPO_ROOT
    return REPO_ROOT


def process(occupation: str, data_dir=None, *, force: bool = False, home=None) -> Path:
    """Interview sessions -> one row per reported task (ingest, normalize, dedup). -> 3_deduped.csv.

    `data_dir` holds one <session>.json per interview; default $DATA_DIR/interviews/<occupation>.
    Normalize calls an LLM (OPENAI_API_KEY); every call is cached, so a re-run pays only for new tasks."""
    args = ["--occupation", occupation]
    if data_dir is not None:
        args += ["--data-dir", str(Path(data_dir).expanduser().resolve())]
    if force:
        args.append("--force")
    _run("tasknet.processing.run", args, _env(home))
    return workspace(home) / "results" / "processing" / occupation / "3_deduped.csv"


def build(occupation: str, *, fresh: bool = False, max_rounds: int | None = None, batch: bool = False,
          home=None) -> Path:
    """Reports -> a task list (cluster, synthesize, overlap, coverage until no new cluster). -> build.json.

    Reads process()'s output for the same occupation. Amends an adopted final.json unless `fresh`. `batch`
    routes the gpt calls through the OpenAI Batch API (half price, but blocks until each batch finishes)."""
    env = _env(home, FRESH="1" if fresh else None, MAX_ROUNDS=max_rounds,
               BATCH="1" if batch else None)
    _run("tasknet.aggregation.chain", [occupation], env)
    return workspace(home) / "results" / "aggregation" / occupation / "build.json"


def audit(occupation: str | None = None, *, home=None) -> bool:
    """Check that every session task is accounted for through 3_deduped.csv (no API calls). -> passed?"""
    return _run("tasknet.processing.audit", ["--occupation", occupation] if occupation else [],
                _env(home), check=False) == 0


def load_tasks(occupation: str, which: str = "final", *, home=None) -> list[dict]:
    """The task statements of a built list: `which` is "final" (adopted) or "build" (the latest run).
    -> [{statement, workers, prevalence, members, ...}]"""
    path = workspace(home) / "results" / "aggregation" / occupation / f"{which}.json"
    return json.loads(path.read_text()).get("core") or []
