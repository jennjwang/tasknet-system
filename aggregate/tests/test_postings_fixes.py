"""Fixes found building task lists from job postings (ported from tasknet-system 608b16a): the cluster ceiling,
capped overlap groups, a lenient resolver parse, the one-duty output cap, failed verdicts kept out of the ledger,
and the one-duty check again after overlap."""
import json

from tasknet.aggregation import chain, cluster, fusion, overlap
from conftest import atoms_from_sources
from test_chain import W, _stub


def test_ladder_never_raises_past_the_ceiling():
    assert cluster._ladder(32000, "medium") == [(32000, "medium"), (96000, "medium"), (96000, "low")]
    assert cluster._ladder(60000, "medium", 128000) == [(60000, "medium"), (128000, "medium"), (128000, "low")]
    assert cluster._ladder(60000, "low", 128000) == [(60000, "low"), (128000, "low")]
    assert cluster._ladder(200000, "low", 128000) == [(200000, "low"), (200000, "low")]   # never below max_out


def test_components_cap_joins_closest_first():
    chainlinks = [(f"s{i}", f"s{i + 1}", 0.9 - i * 0.01) for i in range(14)]          # one 15-statement chain
    dropped = []
    comps = overlap.components(chainlinks, cap=10, dropped=dropped)
    assert max(len(c) for c in comps) <= 10 and len(dropped) == 1
    assert sorted(m for c in comps for m in c) == sorted({x for a, b, _ in chainlinks for x in (a, b)})
    assert "s0" in next(c for c in comps if "s1" in c)                                 # the closest pair joined
    small = [("a", "b", 0.5), ("b", "c", 0.6), ("x", "y", 0.7)]
    assert sorted(overlap.components(small, cap=10)) == sorted(overlap.components([(a, b) for a, b, _ in small]))


def _resolver_reply(ctx, monkeypatch, groups):
    monkeypatch.setattr(ctx.engine, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(ctx.engine, "chat", lambda *a, **k: json.dumps({"groups": groups}))
    nodes = ["Draft motions for court.", "File pleadings with the clerk.", "Advise clients on contracts."]
    edges = [(nodes[0], nodes[1]), (nodes[1], nodes[2])]
    return nodes, ctx.judges.merge_or_separate(nodes, edges, {n: [n] for n in nodes})


def test_resolver_repeat_keeps_first_decision(ctx, monkeypatch):
    nodes, out = _resolver_reply(ctx, monkeypatch, [{"ids": [1], "decision": "KEEP"}, {"ids": [2], "decision": "KEEP"},
                                                    {"ids": [3], "decision": "SEPARATE"}, {"ids": [1], "decision": "SEPARATE"}])
    assert out is not None and [g["members"] for g in out] == [[n] for n in nodes]
    assert out[0]["decision"] == "KEEP"


def test_resolver_omission_is_kept(ctx, monkeypatch):
    nodes, out = _resolver_reply(ctx, monkeypatch, [{"ids": [1], "decision": "SEPARATE"}])
    assert out is not None and {g["members"][0]: g["decision"] for g in out} == {nodes[0]: "SEPARATE", nodes[1]: "KEEP",
                                                                                 nodes[2]: "KEEP"}


def test_resolver_unknown_id_still_rejects(ctx, monkeypatch):
    _, out = _resolver_reply(ctx, monkeypatch, [{"ids": [9], "decision": "KEEP"}])
    assert out is None


def test_duties_output_cap_grows_with_members(ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(ctx.engine, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(ctx.engine, "chat", lambda *a, **k: seen.append(k["max_out"]) or '{"one_duty": true}')
    ctx.judges.duties("Do the work.", [f"atom {i}" for i in range(100)])
    assert seen == [2000 + 30 * 100]


def test_failed_duties_verdict_is_not_saved(ctx, monkeypatch):
    monkeypatch.setattr(ctx.judges, "duties", lambda s, m, draw=1: {"one_duty": True, "outcomes": [], "reason": "", "error": "boom"})
    monkeypatch.setattr(ctx.judges, "duties_many", lambda rows: 0)
    build = {"meta": {}, "core": [{"statement": "Draft motions.", "members": ["Draft motions for court."], "prevalence": 2}]}
    rows = fusion.hear(ctx, build)
    assert rows[0]["verdict"] == "keep" and rows[0]["failed"] == "boom"
    assert json.loads(ctx.ledger("defuse_seen.json").read_text()) == {}


def test_merged_statements_get_the_one_duty_check(agg, monkeypatch, tmp_path):
    ctx = agg.context(atoms_from_sources(W), workdir=tmp_path / "w")
    _stub(ctx, monkeypatch)
    asked = []
    monkeypatch.setattr(ctx.judges, "duties", lambda stmt, members, draw=1: asked.append(stmt) or
                        {"one_duty": True, "outcomes": [], "reason": ""})

    def merge_all(ctx, build, standing=None, tau=None):
        mem = sorted(m for c in build["core"] for m in c["members"])
        src = sorted({s for m in mem for s in W[m]})
        fam = {"sid": "s_merged", "head": "Do merged work.", "statement": "Do merged work.", "members": mem,
               "sources": src, "prevalence": len(src)}
        return {"meta": build["meta"], "core": [fam], **{k: v for k, v in build.items() if k not in ("meta", "core")}}, {}
    monkeypatch.setattr(overlap, "resolve", merge_all)
    chain.build(ctx, max_cycles=1)
    assert asked.count("Do merged work.") == 1 and asked.count("Do alpha work.") == 1     # only the merge is new
    assert (tmp_path / "w" / "verdicts" / "cycle1_fused_merged.json").exists()
    assert (tmp_path / "w" / "stages" / "recheck.json").exists()
