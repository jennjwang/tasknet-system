"""The offline test harness: a fake client, so no test can reach a model, and an Aggregator over a fixed corpus.

NO TEST CALLS A MODEL: `FakeClient.chat.completions.create` fails loudly. A test that needs a verdict stubs the
judge method on `ctx.judges` (or seeds the cache), not the provider.
"""
import csv
from pathlib import Path

import pytest

from tasknet import Aggregator, Atoms, Config

ROWS = [("s1", "99-9999.00", "Keytest Occupation", "added", "Draft motions for court.", "", "litigation", "data"),
        ("s2", "99-9999.00", "Keytest Occupation", "added", "Draft motions for court.", "", "litigation", "data"),
        ("s2", "99-9999.00", "Keytest Occupation", "added", "Advise clients on contracts.", "", "advice", "people"),
        ("s3", "99-9999.00", "Keytest Occupation", "added", "File pleadings with the clerk.", "", "litigation", "data")]
COLS = ["source", "occupation", "title", "status", "task", "original", "duty", "function"]


class _Fail:
    def __getattr__(self, name):
        raise AssertionError("a test reached the model client — stub the judge, not the provider")


class FakeClient:
    """Has the OpenAI SDK's attribute shape; every call fails."""
    chat = type("chat", (), {"completions": _Fail()})()
    embeddings = _Fail()


def make_atoms(rows=ROWS):
    return Atoms([dict(zip(COLS, r)) for r in rows])


@pytest.fixture
def atoms():
    return make_atoms()


@pytest.fixture
def agg(tmp_path):
    return Aggregator(Config.load(prewarm={"on": False}), client=FakeClient(), workdir=tmp_path / "work",
                      cache_dir=tmp_path / "cache")


@pytest.fixture
def ctx(agg, atoms):
    return agg.context(atoms)


@pytest.fixture
def no_live_calls(ctx, monkeypatch):
    """Any engine.chat / engine.create is a loud failure."""
    monkeypatch.setattr(ctx.engine, "chat", lambda *a, **k: pytest.fail("a test reached engine.chat — stub the judge"))
    monkeypatch.setattr(ctx.engine, "create", lambda *a, **k: pytest.fail("a test reached engine.create — stub the judge"))
    return ctx


def atoms_from_sources(sources, title="Keytest Occupation", code="99-9999.00", meta=None):
    """{atom: {source ids}} -> Atoms (one row per (source, atom)); `meta` adds FJA columns per atom."""
    rows = []
    for a, ws in sources.items():
        for w in sorted(ws):
            r = {"source": w, "occupation": code, "title": title, "status": "added", "task": a, "original": ""}
            if meta:
                r.update(meta.get(a, {"function": "", "duty": ""}))
            rows.append(r)
    return Atoms(rows)


class _Vec:
    """An embedding whose every cosine is 0: orders nothing, so a small label set is wholly on the ballot."""
    def __matmul__(self, other):
        return 0.0


def flat_embed(xs):
    return {x: _Vec() for x in xs}


def orth_embed(xs):
    """Real unit vectors, one axis per distinct text: cosine 1 with itself, 0 with everything else."""
    import numpy as np
    keys = sorted(set(xs))
    out = {}
    for i, x in enumerate(keys):
        v = np.zeros(max(len(keys), 2))
        v[i] = 1.0
        out[x] = v
    return {x: out[x] for x in xs}
