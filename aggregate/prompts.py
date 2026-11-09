"""Prompts — bare-name lookup over the package's prompt tree plus any extra trees, with prompt sets and includes.

    p = Prompts.default()                                   # the package's prompts/
    p = Prompts.default(extra=[Path("my/prompts")], prompt_set="fja")
    p.load("synthesize", occupation="Lawyers")             # $placeholder substitution (unknown left intact)
    p.path("coverage"), p.hash("one_duty")

A `{{include:name}}` directive splices another prompt in by the same bare-name lookup (cycle-guarded). With a
prompt set, `<name>_<set>` is tried first and `<name>` is the fallback, so a run can swap the writer's doctrine
without any call site naming the variant; every cache key hashes the template it was rendered from.
"""
from __future__ import annotations

import re
from pathlib import Path
from string import Template

from .lib import sha256

PACKAGE_PROMPTS = Path(__file__).resolve().parent / "prompts"
_INCLUDE = re.compile(r"\{\{\s*include:\s*([A-Za-z0-9_]+)\s*\}\}")


class Prompts:
    def __init__(self, dirs, prompt_set: str = ""):
        self.dirs = [Path(d) for d in dirs]
        self.prompt_set = (prompt_set or "").strip()
        self._paths: dict[str, Path] = {}

    @classmethod
    def default(cls, extra=(), prompt_set: str = "") -> "Prompts":
        return cls([PACKAGE_PROMPTS, *extra], prompt_set)

    def path(self, name: str) -> Path:
        """Resolve a bare prompt name to its .txt. A flat <dir>/<name>.txt wins; else every tree is searched by
        bare name and the match must be unique across all of them."""
        if self.prompt_set and not name.endswith(f"_{self.prompt_set}"):
            try:
                return self.path(f"{name}_{self.prompt_set}")
            except FileNotFoundError:
                pass
        if name in self._paths:
            return self._paths[name]
        for d in self.dirs:
            direct = d / f"{name}.txt"
            if direct.exists():
                self._paths[name] = direct
                return direct
        matches = [m for d in self.dirs for m in sorted(d.rglob(f"{name}.txt"))]
        if len(matches) > 1:
            raise ValueError(f"prompt '{name}' is ambiguous: {[str(m) for m in matches]}")
        if not matches:
            raise FileNotFoundError(f"prompt '{name}.txt' not found under {[str(d) for d in self.dirs]}")
        self._paths[name] = matches[0]
        return matches[0]

    def template(self, name: str) -> str:
        """The template text with includes expanded and no substitution."""
        def expand(nm, seen):
            if nm in seen:
                raise ValueError(f"circular prompt include: {nm} (chain: {sorted(seen)})")
            text = self.path(nm).read_text()
            return _INCLUDE.sub(lambda m: expand(m.group(1), seen | {nm}), text)
        return expand(name, frozenset())

    def load(self, name: str, **kwargs) -> str:
        text = self.template(name)
        return Template(text).safe_substitute(**kwargs) if kwargs else text

    def hash(self, name: str, **kwargs) -> str:
        return sha256(self.load(name, **kwargs))
