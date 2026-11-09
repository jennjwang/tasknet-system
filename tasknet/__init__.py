"""TaskNet System: turn worker interviews into an occupational task list.

    import tasknet
    tasknet.process("lawyers", data_dir="interviews/lawyers")   # sessions -> results/processing/lawyers/3_deduped.csv
    tasknet.build("lawyers")                                     # tasks -> results/aggregation/lawyers/build.json
    tasks = tasknet.load_tasks("lawyers", which="build")

The same steps from a shell: `tasknet process`, `tasknet build`, `tasknet audit`, `tasknet verify`.

The aggregation itself is the `aggregate` package (github.com/S-ONET/aggregate, the git submodule at
aggregate/); this package holds what is specific to the TaskNet System: reading the Interviewer's sessions, the
workspace layout, the model keys, O*NET, the held-out guard and the processing audit. Outputs, caches and .env
live in the workspace: `home=` / $WORKSPACE, else the source checkout, else the current directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__version__ = "0.2.0"
__all__ = ["process", "build", "audit", "verify", "load_tasks", "workspace"]


def _home(home) -> None:
    if home is not None:
        os.environ["WORKSPACE"] = str(Path(home).expanduser().resolve())


def workspace(home=None) -> Path:
    """The directory results/, cache/ and .env are read from and written to."""
    _home(home)
    from .paths import workspace as ws
    return ws()


def process(occupation: str, data_dir=None, *, home=None) -> Path:
    """Interview sessions -> one row per reported task (ingest, normalize, dedup). -> 3_deduped.csv.

    `data_dir` holds one <session>.json per interview; default $DATA_DIR/interviews/<occupation>. Normalize
    calls a model; every call is cached, so a re-run pays only for new tasks."""
    _home(home)
    from .pipeline import process as run
    return run(occupation, Path(data_dir).expanduser().resolve() if data_dir else None)


def build(occupation: str, *, fresh: bool = False, max_rounds: int | None = None, batch: bool = False,
          home=None) -> Path:
    """Tasks -> a task list (cluster, synthesize, split, overlap, refile until no new task). -> build.json.

    Reads process()'s output for the same occupation. Amends an adopted final.json unless `fresh`.
    `max_rounds` caps the chain's cycles (default 3). `batch` routes the gpt calls through the OpenAI Batch
    API (half price, but blocks until each batch finishes)."""
    _home(home)
    from .pipeline import build as run
    return run(occupation, fresh=fresh, max_cycles=max_rounds, batch=batch)


def audit(occupation: str | None = None, *, data_dir=None, home=None) -> bool:
    """Check that every session task is accounted for through 3_deduped.csv (no API calls). -> passed?"""
    _home(home)
    from .auditing import audit as run
    return run(occupation, data_dir)


def verify(occupation: str, which: str = "build", *, home=None) -> list[str]:
    """aggregate's invariants over results/aggregation/<occupation>/<which>.json. -> failures ([] = sound)."""
    _home(home)
    from .pipeline import verify as run
    return run(occupation, which)


def load_tasks(occupation: str, which: str = "final", *, home=None) -> list[dict]:
    """The task statements of a built list: `which` is "final" (adopted) or "build" (the latest run).
    -> [{statement, sources, prevalence, members, ...}]"""
    from .paths import results_dir
    _home(home)
    return json.loads((results_dir("aggregation", occupation) / f"{which}.json").read_text()).get("core") or []
