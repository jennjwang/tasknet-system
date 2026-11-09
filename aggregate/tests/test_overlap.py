"""The merge / sharpen hearing: merge concurrence, pass-through stamps, the SEPARATE+KEEP gate, strain, dockets."""
import json

from tasknet import families
from tasknet.aggregation import overlap, synthesize
from conftest import atoms_from_sources

A = "Draft trial motions and briefs for filing with the court."
B = "Draft appellate briefs and petitions for review."
MEM = {A: ["a1", "a2"], B: ["b1", "b2"]}
ROWS = [{"a": A, "b": B, "cos": 0.81, "label": "yes", "labels_collide": True, "why": "an act appears in both labels"}]
CORE = [{"statement": A, "members": MEM[A]}, {"statement": B, "members": MEM[B]}]
REWRITE = {A: "Draft and file motions and briefs in trial-court proceedings."}


def _merge_group(members, relation="EQUIVALENT"):
    return {"members": list(members), "decision": "MERGE", "relation": relation, "proposed": "p", "divider": "", "basis": "b"}


def test_every_merge_pays_the_second_draw():
    calls = []
    out = overlap.merge_concurrence([_merge_group(["A", "B"])], lambda: calls.append(1) or [_merge_group(["A", "B"])])
    assert out[0]["decision"] == "MERGE" and calls == [1]
    sep = [{"members": [m], "decision": "SEPARATE", "relation": "", "proposed": "", "divider": "d", "basis": ""} for m in "AB"]
    out = overlap.merge_concurrence([_merge_group(["A", "B"])], lambda: sep)
    assert out[0]["decision"] == "KEEP" and "no-concurrence" in out[0]["contested"]
    calls.clear()
    out = overlap.merge_concurrence([_merge_group(["A", "B"], "CONTAINMENT")], lambda: calls.append(1) or [_merge_group(["A", "B"], "CONTAINMENT")])
    assert out[0]["decision"] == "MERGE" and calls
    g = [sep[0]]
    assert overlap.merge_concurrence(g, lambda: (_ for _ in ()).throw(AssertionError)) == g


def test_pass_through_rows_keep_continuity_stamps(agg):
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}, "a2": {"w2"}}))
    core = [{"statement": "Do alpha work.", "head": "Do alpha work.", "members": ["a1", "a2"], "sources": ["w1", "w2"],
             "prevalence": 2, "written_for": "stampF", "principles_with": "h1"}]
    fams, log, lost = overlap.apply_merges(ctx, core, [], [])
    assert fams[0]["written_for"] == "stampF" and fams[0]["principles_with"] == "h1" and fams[0]["prevalence"] == 2


# ── the SEPARATE + KEEP gate ───────────────────────────────────────────────────────────────────────

def _harness(agg, monkeypatch, verdicts):
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "b2": {"w4"}}))
    seen = {}
    monkeypatch.setattr(overlap, "resolve_component", lambda ctx, nodes, edges, m_of: verdicts)

    def _sharpen(nodes, edges, m_of, frozen=()):
        seen.update(nodes=list(nodes), edges=list(edges), frozen=list(frozen))
        return {k: [v] for k, v in REWRITE.items()}
    monkeypatch.setattr(ctx.judges, "sharpen", _sharpen)
    monkeypatch.setattr(synthesize, "rejects", lambda c, s, a: ("", {}))
    r = overlap.route(ctx, ROWS, by_mem=MEM)
    out, log = overlap.apply_sharpens(ctx, [dict(c) for c in CORE], r["sharpen"])
    return ctx, {c["statement"] for c in out}, log, r, seen


def test_two_separates_are_rewritten(agg, monkeypatch):
    _, stmts, log, r, seen = _harness(agg, monkeypatch, [{"members": [A], "decision": "SEPARATE"}, {"members": [B], "decision": "SEPARATE"}])
    assert r["sharpen"] and REWRITE[A] in stmts and A not in stmts and [e["before"] for e in log] == [A] and seen["frozen"] == []


def test_separate_and_keep_reaches_the_writer_and_records_strain(agg, monkeypatch):
    ctx, stmts, log, r, seen = _harness(agg, monkeypatch, [{"members": [A], "decision": "SEPARATE"}, {"members": [B], "decision": "KEEP"}])
    assert r["sharpen"][0]["members"] == [A] and r["sharpen"][0]["each"][0]["siblings"] == [B]
    assert seen["frozen"] == [B] and seen["nodes"] == [A, B] and [set(e) for e in seen["edges"]] == [{A, B}]
    assert REWRITE[A] in stmts and A not in stmts and B in stmts and [e["before"] for e in log] == [A]
    led = json.loads(ctx.ledger("sharpen_seen.json").read_text())
    (rec,) = led.values()
    assert rec["count"] == 1 and set(rec["labels"]) == {A, B}


# ── strain ─────────────────────────────────────────────────────────────────────────────────────────

def test_strain_keys_on_member_sets_and_discounts_rewrites(ctx):
    m = {"Old label A": ["a1", "a2"], "Old label B": ["b1", "b2"]}
    overlap.record_sharpen_edges(ctx, [("Old label A", "Old label B")], m)
    assert overlap.strain_count(ctx, ["a2", "a1"], ["b2", "b1"]) == 1
    overlap.record_sharpen_edges(ctx, [("Old label A", "Old label B")], m)
    assert overlap.strain_count(ctx, ["a1", "a2"], ["b1", "b2"]) == 2
    assert overlap.strain_count(ctx, ["a1", "a2"], ["c1", "c2"]) == 0
    lines, tokens = overlap.strain_evidence(ctx, [("A", "B"), ("A", "C")], {"A": ["a1", "a2"], "B": ["b1", "b2"], "C": ["c1"]}, {"A": 1, "B": 2, "C": 3})
    assert len(lines) == 1 and "1-2" in lines[0] and "2 time" in lines[0] and tokens == ["STRAIN:1-2:2"]
    m2 = {"Old wording about filing papers": ["x1"], "Old wording about drafting text": ["y1"]}
    overlap.record_sharpen_edges(ctx, [tuple(m2)], m2)
    assert overlap.strain_count(ctx, ["x1"], ["y1"], *m2) == 1
    assert overlap.strain_count(ctx, ["x1"], ["y1"], "Completely different sentence one", "Completely different sentence two") == 0


# ── standing prose is docketed, never rewritten ────────────────────────────────────────────────────

def _adopted(*fams):
    return families.registry({"core": [{"statement": s, "members": m} for s, m in fams]})


def _sg(a="Standing A.", b="Fresh B."):
    core = [{"statement": a, "members": ["a1", "a2"]}, {"statement": b, "members": ["b1", "b2"]}]
    sg = [{"members": [a, b], "each": [{"statement": a, "complaints": [{"partner": b}]}]}]
    return core, sg


def test_sharpen_docket_holds_rewrites_of_standing_statements(ctx, monkeypatch):
    monkeypatch.setattr(overlap, "sharpen_component", lambda c, n, e, m, frozen=(): {"Standing A.": "Rewritten A.", "Fresh B.": "Rewritten B."})
    monkeypatch.setattr(ctx.judges, "overlaps", lambda a, b, draw=1: False)
    core, sg = _sg()
    out, log = overlap.apply_sharpens(ctx, core, sg, _adopted(("Standing A.", ["a1", "a2"])))
    stmts = {f["statement"] for f in out}
    assert "Standing A." in stmts and "Rewritten B." in stmts
    assert ctx.docket[0]["statement"] == "Standing A." and ctx.docket[0]["proposed"] == "Rewritten A."


def test_sharpen_concurrence_is_triage_never_application(ctx, monkeypatch):
    monkeypatch.setattr(overlap, "sharpen_component", lambda c, n, e, m, frozen=(): {"Standing A.": "Rewritten A."})
    monkeypatch.setattr(ctx.judges, "overlaps", lambda a, b, draw=1: not ("Rewritten" in a or "Rewritten" in b))
    core, sg = _sg()
    out, log = overlap.apply_sharpens(ctx, core, sg, _adopted(("Standing A.", ["a1", "a2"])))
    assert "Standing A." in {f["statement"] for f in out} and not log
    assert ctx.docket[0]["concurrence"] is True and ctx.docket[0]["why"].startswith("resolves")
    ctx.docket.clear()
    monkeypatch.setattr(ctx.judges, "overlaps", lambda a, b, draw=1: True)
    out, log = overlap.apply_sharpens(ctx, core, sg, _adopted(("Standing A.", ["a1", "a2"])))
    assert not log and ctx.docket[0]["concurrence"] is False and ctx.docket[0]["why"].startswith("no-concurrence")


def test_no_registry_means_every_statement_is_fresh(ctx, monkeypatch):
    monkeypatch.setattr(overlap, "sharpen_component", lambda c, n, e, m, frozen=(): {"Standing A.": "Rewritten A.", "Fresh B.": "Rewritten B."})
    core, sg = _sg()
    core[0]["written_for"] = families.key(["a1", "a2"])
    out, log = overlap.apply_sharpens(ctx, core, sg)
    assert {f["statement"] for f in out} == {"Rewritten A.", "Rewritten B."} and len(log) == 2 and not ctx.docket


def test_adopted_prose_already_rewritten_is_no_longer_standing(ctx, monkeypatch):
    monkeypatch.setattr(overlap, "sharpen_component", lambda c, n, e, m, frozen=(): {"Reworded A.": "Rewritten A."})
    core, sg = _sg(a="Reworded A.")
    out, log = overlap.apply_sharpens(ctx, core, sg, _adopted(("Standing A.", ["a1", "a2"])))
    assert "Rewritten A." in {f["statement"] for f in out} and len(log) == 1


def test_strain_is_a_ledger_of_rewordings_not_hearings(ctx, monkeypatch):
    path = ctx.ledger("sharpen_seen.json")
    core = [{"statement": "Label A.", "members": ["a1", "a2"]}, {"statement": "Label B.", "members": ["b1", "b2"]}]
    sg = [{"members": ["Label A.", "Label B."], "each": [{"statement": "Label A.", "complaints": [{"partner": "Label B."}]},
                                                       {"statement": "Label B.", "complaints": [{"partner": "Label A."}]}]}]
    monkeypatch.setattr(overlap, "sharpen_component", lambda c, n, e, m, frozen=(): None)
    out, log = overlap.apply_sharpens(ctx, core, sg)
    assert log == [] and not path.exists()
    monkeypatch.setattr(overlap, "sharpen_component", lambda c, n, e, m, frozen=(): {"Label A.": "Reworded A."})
    monkeypatch.setattr(ctx.judges, "overlaps", lambda a, b, draw=1: False)
    overlap.apply_sharpens(ctx, core, sg, _adopted(("Label A.", ["a1", "a2"])))
    assert not path.exists()                                       # docketed: nothing was reworded
    overlap.apply_sharpens(ctx, core, sg)
    (rec,) = json.loads(path.read_text()).values()
    assert rec["count"] == 1
