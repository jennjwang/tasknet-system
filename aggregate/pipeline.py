"""The TaskNet pipeline for one occupation: interview sessions -> processed tasks -> a task list.

    process("lawyers", data_dir="path/to/sessions")   # ingest -> normalize -> dedup: results/processing/lawyers/
    build("lawyers")                                   # the chain: results/aggregation/lawyers/build.json
    load_tasks("lawyers", which="build")

Every stage reads and writes the workspace (workspace.py). processing writes 1_ingested.csv, 2_normalized.csv and
3_deduped.csv (columns session_id, onet_code, onet_title, status, task_name[, original_task_name]); a stage
runs only when its output is missing or older than its input, or with force. build amends an adopted
final.json unless fresh; adopting a build is an explicit copy of build.json to final.json.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import workspace
from .atoms import Atoms

STAGES = ["ingest", "normalize", "dedup"]
FILES = {"ingest": "1_ingested.csv", "normalize": "2_normalized.csv", "dedup": "3_deduped.csv"}


def _stale(out: Path, inp: Path | None, force: bool) -> bool:
    return force or inp is None or not out.exists() or (inp.exists() and out.stat().st_mtime < inp.stat().st_mtime)


def process(occupation: str, data_dir=None, *, force: bool = False, start: str = "ingest", end: str = "dedup") -> Path:
    """Interview sessions -> one row per reported task. -> results/processing/<occ>/3_deduped.csv

    `data_dir` holds one <session>.json per interview; default $DATA_DIR/interviews/<occupation>. Ingest always
    runs (it appends only new rows); normalize calls models (every call cached, so a re-run pays only for new
    tasks); dedup drops a repeated task within one session."""
    from .processing import dedup, ingest, normalize
    workspace.load_dotenv()
    proc = workspace.processing_dir(occupation)
    stages = STAGES[STAGES.index(start):STAGES.index(end) + 1]
    if not stages:
        raise SystemExit(f"--from {start} comes after --to {end}")
    if "ingest" in stages:
        src = Path(data_dir).expanduser().resolve() if data_dir else workspace.interviews_dir() / occupation
        if not src.is_dir():
            raise SystemExit(f"ingest needs --data-dir <dir> or {src}/; or --from normalize to skip it.")
        if force:
            (proc / FILES["ingest"]).unlink(missing_ok=True)
        print("  RUN  ingest")
        ingest.ingest(occupation, src)
    if "normalize" in stages:
        inp, out = proc / FILES["ingest"], proc / FILES["normalize"]
        if _stale(out, inp, force):
            print("  RUN  normalize")
            agg = workspace.aggregator(occupation)
            normalize(agg.context(), Atoms.read(inp)).write(out, legacy=True)
            agg.save()
            print(agg.usage())
        else:
            print(f"  SKIP normalize   up to date: {out}")
    if "dedup" in stages:
        inp, out = proc / FILES["normalize"], proc / FILES["dedup"]
        if _stale(out, inp, force):
            print("  RUN  dedup")
            dedup(Atoms.read(inp)).write(out, legacy=True)
        else:
            print(f"  SKIP dedup       up to date: {out}")
    return proc / FILES["dedup"]


def frozen_holdout(occupation: str) -> int:
    """How many held-out workers a rebuild would absorb (0 if this occupation's list is not frozen). The
    published lists are frozen (results/evaluation/heldout_frozen.json, or $HELDOUT_FROZEN) so the evaluation's
    held-out sample stays held out."""
    path = workspace.env_path("HELDOUT_FROZEN", workspace.root() / "results" / "evaluation" / "heldout_frozen.json")
    try:
        o = json.loads(path.read_text())["occupations"].get(occupation)
        return int(o["n_held_out_at_freeze"]) if o else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


LEDGERS = {"split_verdicts.json": "defuse_seen.json", "reword_counts.json": "sharpen_seen.json"}


def migrate_history(workdir: Path) -> list[str]:
    """Copy an earlier release's history/ files to the ledgers/ names this one reads (same records), when the
    ledger does not exist yet. -> the files copied"""
    done = []
    for old, new in LEDGERS.items():
        src, dst = workdir / "history" / old, workdir / "ledgers" / new
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            done.append(f"history/{old} -> ledgers/{new}")
    return done


def build(occupation: str, *, fresh: bool = False, max_rounds: int | None = None, batch: bool = False,
          spend_holdout: bool = False) -> Path:
    """Processed tasks -> a task list (cluster, write, split, merge, refile, until no new task). -> build.json

    Reads process()'s output. Amends an adopted final.json unless `fresh`. `batch` routes the gpt judges
    through the OpenAI Batch API (half price, but blocks until each batch finishes). Refuses a frozen list
    unless `spend_holdout` (or SPEND_HOLDOUT=1)."""
    workspace.load_dotenv()
    spend = spend_holdout or os.environ.get("SPEND_HOLDOUT", "0") == "1"
    if not spend and (n := frozen_holdout(occupation)) > 0:
        raise SystemExit(f"REFUSING: {occupation}'s list is frozen; rebuilding would absorb its {n} held-out workers.\n"
                         "  re-run with SPEND_HOLDOUT=1 to proceed anyway.")
    corpus = workspace.processing_dir(occupation) / FILES["dedup"]
    if not corpus.exists():
        raise SystemExit(f"{corpus} not found — run `tasknet process {occupation}` first.")
    atoms = Atoms.read(corpus)
    agg = workspace.aggregator(occupation, soc=atoms.code, **({"batch": {"api": True}} if batch else {}))
    for line in migrate_history(agg.workdir):
        print(f"  migrated {line}")
    final = agg.workdir / "final.json"
    standing = json.loads(final.read_text()) if final.exists() else None
    try:
        agg.build(atoms, standing=standing, fresh=fresh, max_cycles=max_rounds)
    finally:
        agg.save()
        print(agg.usage())
    return agg.workdir / "build.json"


def verify(artifact, occupation: str | None = None) -> list[str]:
    """A list artifact's invariants, against its occupation's processed tasks when they exist. -> [failure]"""
    from .aggregation import verify as v
    art = json.loads(Path(artifact).read_text())
    occ = occupation or Path(artifact).resolve().parent.name
    corpus = workspace.processing_dir(occ) / FILES["dedup"]
    sources = Atoms.read(corpus).sources if corpus.exists() else None
    return v.check(art, sources, int(art.get("meta", {}).get("min_sources") or workspace.config().min_sources))


def load_tasks(occupation: str, which: str = "final") -> list[dict]:
    """The task statements of a built list: `which` is "final" (adopted) or "build" (the latest run).
    -> [{statement, sources, prevalence, members, ...}]"""
    path = workspace.aggregation_dir(occupation) / f"{which}.json"
    return json.loads(path.read_text()).get("core") or []
