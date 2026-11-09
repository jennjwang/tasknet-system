"""The scale knobs, offline: blocking, member caps, k-means chunks, re-homing, and the multi-item judge calls that
write one cache entry per item under the single-call key."""
import json

import numpy as np
import pytest

from tasknet.aggregation import blocks
from conftest import atoms_from_sources


def _unit(v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


def test_same_block_only_separates_two_known_functions(ctx):
    ctx.config["blocks"]["by"] = "function"
    assert blocks.same_block(ctx, "data", "data") and blocks.same_block(ctx, "", "data") and not blocks.same_block(ctx, "data", "people")
    ctx.config["blocks"]["by"] = ""
    assert blocks.same_block(ctx, "data", "people")


def test_function_of_reads_the_atoms_meta(agg):
    ctx = agg.context(atoms_from_sources({"a": {"w1"}, "b": {"w2"}}, meta={"a": {"function": "data", "duty": "x"}, "b": {"function": "data", "duty": "y"}}))
    assert blocks.function_of(ctx, ["a", "b"]) == ""
    ctx.config["blocks"]["by"] = "function"
    assert blocks.function_of(ctx, ["a", "b"]) == "data" and blocks.duty_of(ctx, ["a", "b"]) in ("x", "y")


def test_cap_members_prefers_prevalence_then_centrality(agg, monkeypatch):
    ctx = agg.context(atoms_from_sources({"a": {"w1"}, "b": {"w1", "w2"}, "c": {"w1"}, "d": {"w3"}}))
    ctx.config["blocks"]["member_cap"] = 2
    monkeypatch.setattr(ctx.judges, "embed", lambda ts: {"a": _unit([1, 0]), "b": _unit([1, 0.1]), "c": _unit([0.9, 0.3]), "d": _unit([0, 1])})
    assert blocks.cap_members(ctx, ["a", "b", "c", "d"]) == ["b", "c"]
    ctx.config["blocks"]["member_cap"] = 0
    assert blocks.cap_members(ctx, ["a", "b", "c", "d"]) == ["a", "b", "c", "d"]


def test_kmeans_chunks_assign_nearest_and_publishable_pile(agg, monkeypatch):
    ctx = agg.context(atoms_from_sources({"x0": {"w1", "w2"}, "y0": {"w1"}}))
    vec = {f"x{i}": _unit([1, 0.01 * i]) for i in range(5)}
    vec.update({f"y{i}": _unit([0.01 * i, 1]) for i in range(5)})
    monkeypatch.setattr(ctx.judges, "embed", lambda ts: {t: vec[t] for t in ts})
    chunks = blocks.kmeans_chunks(ctx, list(vec), 5)
    assert sorted(map(len, chunks)) == [5, 5] and all(len({t[0] for t in c}) == 1 for c in chunks)
    assert blocks.assign_nearest(ctx, ["x3", "y4"], [["x0", "x1"], ["y0", "y1"]]) == {"x3": 0, "y4": 1}
    assert blocks.publishable_pile(ctx, ["x0", "y0"]) == ["x0", "y0"]
    ctx.config["blocks"]["refile_scope"] = "publishable"
    assert blocks.publishable_pile(ctx, ["x0", "y0"]) == ["x0"]


def test_covers_many_writes_one_entry_per_atom(ctx, monkeypatch):
    ctx.config["batch"]["items"] = True
    seen = []

    def fake_chat(prompt, model, **kw):
        seen.append(prompt)
        return json.dumps({"verdicts": [{"id": 1, "verdict": "yes", "deciding_rule": "none", "why": "same act"},
                                        {"id": 2, "verdict": "no", "deciding_rule": "D1", "why": "other act"},
                                        {"id": 9, "verdict": "yes"}]})
    monkeypatch.setattr(ctx.engine, "chat", fake_chat)
    n = ctx.judges.covers_many([("Wash pots.", "Clean dishware"), ("Mop floors.", "Clean dishware"), ("Wash pots.", "Clean dishware")])
    assert n == 2 and len(seen) == 1 and "1. Mop floors." in seen[0] and "LABEL: Clean dishware" in seen[0]
    monkeypatch.setattr(ctx.engine, "chat", lambda *a, **k: pytest.fail("single call made after a batched fill"))
    assert ctx.judges.covers("Mop floors.", "Clean dishware") is True
    assert ctx.judges.covers("Wash pots.", "Clean dishware") is False
    assert ctx.cache.get("covers_reasoned", ctx.judges._covers_req("Wash pots.", "Clean dishware")[1])["batched"] == 2
    assert ctx.judges.covers_many([("Wash pots.", "Clean dishware")]) == 0


def test_overlaps_many_and_misses_fall_to_single(ctx, monkeypatch):
    ctx.config["batch"]["items"] = True
    monkeypatch.setattr(ctx.engine, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(ctx.engine, "chat", lambda prompt, model, **kw: json.dumps({"verdicts": [{"id": 1, "answer": "yes"}]}))
    pairs = [("Wash pots.", "Clean dishware."), ("Mop floors.", "Sweep floors.")]
    assert ctx.judges.overlaps_many(pairs) == 1
    monkeypatch.setattr(ctx.engine, "chat", lambda prompt, model, **kw: "no")
    assert ctx.judges.overlaps("Wash pots.", "Clean dishware.") is True
    assert ctx.judges.overlaps("Mop floors.", "Sweep floors.") is False


def test_dispose_many_matches_single_key(ctx, monkeypatch):
    ctx.config["batch"]["items"] = True
    monkeypatch.setattr(ctx.engine, "prewarm", lambda *a, **k: None)
    tasks = "1. Wash dishes and pots.\n2. Mop and sweep floors."
    monkeypatch.setattr(ctx.engine, "chat", lambda prompt, model, **kw: json.dumps(
        {"verdicts": [{"id": 1, "fate": "FILE", "task": 1, "why": "instance"}, {"id": 2, "fate": "LEAVE", "task": None, "why": "fragment"}]}))
    assert ctx.judges.dispose_many([("Scrub pans.", tasks, None, ()), ("Stuff.", tasks, None, ())], draw=1) == 2
    monkeypatch.setattr(ctx.engine, "chat", lambda *a, **k: pytest.fail("single disposer call after prefill"))
    assert ctx.judges.dispose("Scrub pans.", tasks)["fate"] == "FILE" and ctx.judges.dispose("Stuff.", tasks)["fate"] == "LEAVE"


def test_batch_api_is_a_no_op_without_the_sdk_surface(ctx):
    assert not ctx.engine.has_batch_api("gpt-5.4")
    assert ctx.batch.fill("covers_reasoned", [("k", "p")] * 50, "gpt-5.4", lambda t: None) == 0
