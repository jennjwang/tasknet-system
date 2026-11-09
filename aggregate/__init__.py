"""TaskNet System: turn worker interviews into an occupational task list, and evaluate the list.

    import tasknet
    tasknet.process("lawyers", data_dir="interviews/lawyers")   # sessions -> results/processing/lawyers/3_deduped.csv
    tasknet.build("lawyers")                                     # -> results/aggregation/lawyers/build.json
    tasks = tasknet.load_tasks("lawyers", which="build")

The same steps from a shell: `tasknet process`, `tasknet build`, `tasknet audit`, `tasknet verify`. Outputs,
caches and .env live in the workspace (tasknet.workspace). Underneath is the aggregator, usable on its own with
task rows from any source:

    from tasknet import Aggregator, Config, Atoms, DocSet

    agg = Aggregator(Config.load("configs.yml"), client=openai.OpenAI(), workdir="results/lawyers")
    atoms = agg.process(rows)                 # raw task texts -> atoms (normalize + dedup)
    build = agg.build(atoms)                  # the chain -> build.json under the workdir; invariants checked
    agg.evaluate.coverage(lists, heldout)     # held-out evaluation, every data input passed in
    agg.judges.covers(atom, statement)        # the judges as an instrument
"""
from __future__ import annotations

from pathlib import Path

from .atoms import Atoms
from .cache import Cache
from .config import Config
from .context import Context
from .engine import Engine
from .evaluate import statements_of as statements
from .evaluate.docs import DocSet
from .prompts import Prompts

__version__ = "0.1.0"
__all__ = ["Aggregator", "Config", "Engine", "Prompts", "Atoms", "DocSet", "Context", "Cache", "statements",
           "process", "build", "audit", "load_tasks", "workspace"]


def process(occupation, data_dir=None, **kw):
    """Interview sessions -> results/processing/<occ>/3_deduped.csv (tasknet.pipeline.process)."""
    from .pipeline import process as _process
    return _process(occupation, data_dir, **kw)


def build(occupation, **kw):
    """Processed tasks -> results/aggregation/<occ>/build.json (tasknet.pipeline.build)."""
    from .pipeline import build as _build
    return _build(occupation, **kw)


def audit(occupation=None) -> bool:
    """Check every session task is accounted for through 3_deduped.csv (no API calls). -> passed?"""
    from .processing.audit import audit as _audit
    return _audit(occupation)


def load_tasks(occupation, which="final"):
    """The statements of a built list: "final" (adopted) or "build" (the latest run)."""
    from .pipeline import load_tasks as _load
    return _load(occupation, which)


class Aggregator:
    def __init__(self, config=None, *, client=None, router=None, engine=None, workdir=None, cache_dir=None,
                 prompts=None, reference_statements=None, occupation=None):
        """`config`: a Config, a path to a YAML, or None for the defaults. `workdir` is where a build's artifacts
        and ledgers go and the default root of the cache (config.cache_dir, relative). `reference_statements` is a plain list
        of well-formed task statements (an external inventory of the occupation's group) the writer's
        umbrella-verb check attests caution verbs against; without it every caution verb is treated as an umbrella."""
        self.config = config if isinstance(config, Config) else Config.load(config)
        self.workdir = Path(workdir) if workdir else None
        self.engine = engine or Engine(self.config, client=client, router=router)
        cache_root = Path(cache_dir) if cache_dir else (self.workdir or Path(".")) / self.config.get("cache_dir", "cache/aggregation")
        self.cache = Cache(cache_root)
        self.prompts = prompts or Prompts.default(extra=[Path(p) for p in (self.config.get("prompt_dirs") or [])],
                                                  prompt_set=self.config.get("prompt_set", "") or "")
        self.reference_statements = list(reference_statements or [])
        self.occupation = occupation
        self._root = Context(self.config, self.engine, self.prompts, self.cache, workdir=self.workdir,
                             occupation=occupation, reference_statements=reference_statements)
        self._last_atoms = None

    # ── contexts ──────────────────────────────────────────────────────────────────────────────────
    def context(self, atoms=None, workdir=None, occupation=None, reference_statements=None) -> Context:
        if atoms is None and workdir is None and occupation is None and reference_statements is None:
            return self._root
        return self._root.with_atoms(atoms, workdir=workdir, occupation=occupation, reference_statements=reference_statements)

    @property
    def judges(self):
        return self._root.judges if self._last_atoms is None else self.context(self._last_atoms).judges

    @property
    def evaluate(self):
        from .evaluate import Evaluate
        return Evaluate(self)

    # ── processing ────────────────────────────────────────────────────────────────────────────────
    def process(self, rows, **kw) -> Atoms:
        """Raw task rows -> atoms: normalize (split, filter, rewrite, debrand) then dedup."""
        from .processing import dedup, normalize
        return dedup(normalize(self._root, rows, **kw))

    def condense(self, atoms, **kw):
        """Key + near-duplicate merge over FJA-field atoms -> (Atoms, mapping)."""
        from .processing import condense
        return condense(self.context(atoms), atoms, **kw)

    # ── the chain ─────────────────────────────────────────────────────────────────────────────────
    def build(self, atoms, *, standing=None, fresh=False, max_cycles=None, workdir=None, occupation=None,
              reference_statements=None, on_stage=None) -> dict:
        """Build the task list for `atoms` (an Atoms or a path to its CSV). `standing` is an adopted list
        (final.json's dict) to amend, or the continuity registry when `fresh`."""
        from .aggregation import chain
        if not isinstance(atoms, Atoms):
            atoms = Atoms.read(atoms)
        self._last_atoms = atoms
        ctx = self.context(atoms, workdir=workdir, occupation=occupation, reference_statements=reference_statements)
        return chain.build(ctx, standing=standing, fresh=fresh, max_cycles=max_cycles, on_stage=on_stage)

    def verify(self, build, atoms=None) -> list[str]:
        from .aggregation import verify
        atoms = atoms if atoms is not None else self._last_atoms
        return verify.invariants(self.context(atoms), build)

    # ── accounting ────────────────────────────────────────────────────────────────────────────────
    def usage(self) -> str:
        return self.engine.usage_report()

    def save(self) -> None:
        self.cache.save()
