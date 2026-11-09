"""Config — every knob of the package, from the packaged configs.yml overlaid with a user file and overrides.

    cfg = Config.load()                              # the defaults
    cfg = Config.load("my.yml", min_sources=3)       # a file, then keyword overrides (top-level keys)
    cfg = Config.load(overrides={"models": {"cluster": {"model": "gpt-5.5"}}})
    cfg.models.cluster.model ; cfg.chain.max_cycles ; cfg.get("batch.items")

Sections are attribute-accessible; `to_dict()` gives the plain mapping. Nothing here reads the environment: API
keys live in the environment (see engine.py), everything else lives in the file.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS_PATH = Path(__file__).resolve().parent / "configs.yml"


class Section(dict):
    """A dict whose string keys are also attributes; nested dicts come back as Sections."""

    def __getattr__(self, name):
        try:
            v = self[name]
        except KeyError:
            raise AttributeError(name) from None
        return Section(v) if isinstance(v, dict) and not isinstance(v, Section) else v

    def __setattr__(self, name, value):
        self[name] = value


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config(Section):
    @classmethod
    def load(cls, path=None, overrides: dict | None = None, **kw) -> "Config":
        """The packaged defaults, then `path` (YAML), then `overrides`, then keyword overrides, each by key."""
        data = yaml.safe_load(DEFAULTS_PATH.read_text()) or {}
        if path:
            data = _merge(data, yaml.safe_load(Path(path).read_text()) or {})
        if overrides:
            data = _merge(data, overrides)
        if kw:
            data = _merge(data, kw)
        return cls(data)

    def get(self, dotted, default=None):
        cur = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def with_overrides(self, overrides: dict | None = None, **kw) -> "Config":
        return Config(_merge(_merge(self.to_dict(), overrides or {}), kw))

    def to_dict(self) -> dict:
        return copy.deepcopy(dict(self))

    def model(self, name: str) -> str:
        """The model string for a judge or writer: `models.<name>.model`, or the bare string for `embed`."""
        m = self["models"][name]
        return m["model"] if isinstance(m, dict) else m

    def effort(self, name: str):
        m = self["models"][name]
        return (m.get("effort") or None) if isinstance(m, dict) else None
