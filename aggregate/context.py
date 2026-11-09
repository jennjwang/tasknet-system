"""Context — the one object every stage and judge receives: config, engine, prompts, cache, the atoms of the
occupation being built, the workdir its ledgers and artifacts live in, an optional list of reference statements
(well-formed statements of related occupations, for the writer's umbrella-verb check), and the run's scratch state.

Nothing in the package is ambient: a stage is `stage(ctx, build)`, a judge is `ctx.judges.covers(atom, stmt)`.
"""
from __future__ import annotations

from pathlib import Path

from .cache import Cache, Embeddings
from .engine import Engine
from .prompts import Prompts

UNSPECIFIED = "an unspecified occupation"


class Context:
    def __init__(self, config, engine: Engine, prompts: Prompts, cache: Cache, *, atoms=None, workdir=None,
                 occupation: str | None = None, reference_statements=None, embeddings: Embeddings | None = None):
        self.config = config
        self.engine = engine
        self.prompts = prompts
        self.cache = cache
        self.atoms = atoms
        self.workdir = Path(workdir) if workdir else None
        self._occupation = occupation
        self.reference_statements = list(reference_statements or [])
        self.embeddings = embeddings or Embeddings(cache.dir / "stage2_prefilter", config.model("embed"), engine)
        self._judges = None
        self._batch = None
        # per-run scratch: the writer's decision trace, its fusion flags, sharpen's alternatives, the docket
        self.trace: list = []
        self.form_flags: dict = {}
        self.sharpen_alternatives: dict = {}
        self.docket: list = []

    # ── derived ───────────────────────────────────────────────────────────────────────────────────
    @property
    def occupation(self) -> str:
        """The occupation string prompts are rendered with: the atoms' O*NET title, or the placeholder."""
        return self._occupation or (self.atoms.title if self.atoms is not None else "") or UNSPECIFIED

    @property
    def code(self) -> str:
        """The occupation's code as the source gave it (an O*NET-SOC code, say); "" when unknown."""
        return self.atoms.code if self.atoms is not None else ""

    @property
    def min_sources(self) -> int:
        return int(self.config.get("min_sources", 2))

    @property
    def judges(self):
        if self._judges is None:
            from .judges import Judges
            self._judges = Judges(self)
        return self._judges

    @property
    def batch(self):
        if self._batch is None:
            from .batch import Batch
            self._batch = Batch(self)
        return self._batch

    def prompt(self, name, **kw) -> str:
        return self.prompts.load(name, **kw)

    def model(self, name) -> str:
        return self.config.model(name)

    def effort(self, name):
        return self.config.effort(name)

    def workers_for(self, kind: str) -> int:
        return int((self.config.get("workers") or {}).get(kind, 8))

    def with_atoms(self, atoms, workdir=None, occupation=None, reference_statements=None) -> "Context":
        """A fresh per-build context sharing the engine, prompts and cache."""
        return Context(self.config, self.engine, self.prompts, self.cache, atoms=atoms,
                       workdir=workdir or self.workdir, occupation=occupation or self._occupation,
                       reference_statements=reference_statements if reference_statements is not None else self.reference_statements,
                       embeddings=self.embeddings)

    # ── paths ─────────────────────────────────────────────────────────────────────────────────────
    def path(self, role: str, name: str) -> Path:
        """workdir/<role>/<name>, created on demand (verdicts/, handoffs/, stages/, ledgers/)."""
        if self.workdir is None:
            raise RuntimeError("this context has no workdir")
        d = self.workdir / role
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def ledger(self, name: str) -> Path:
        return self.path("ledgers", name)
