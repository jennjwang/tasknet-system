"""The writer's skip, the regate rule on adjudicated prose, umbrella lead verbs, the atom list, the fusion flag."""
from tasknet import families
from tasknet.aggregation import synthesize
from tasknet.lib import sha256
from conftest import atoms_from_sources

TEMPLATE = "TEMPLATE $items $siblings"


def _fam(**kw):
    return {"head": "h", "members": ["a1", "a2"], "sources": ["w1", "w2"], "prevalence": 2, **kw}


def _run(ctx, monkeypatch, fams):
    calls = []
    monkeypatch.setattr(ctx.prompts, "load", lambda *a, **k: TEMPLATE)
    monkeypatch.setattr(ctx.engine, "prewarm", lambda *a, **k: None)
    monkeypatch.setattr(synthesize, "statement", lambda *a, **k: calls.append(1) or "REWRITTEN.")
    return synthesize.write_all(ctx, fams), len(calls)


def test_unchanged_members_and_prompt_are_not_rewritten(ctx, monkeypatch):
    f = _fam(statement="Original.", written_for=families.key(["a1", "a2"]), written_with=sha256(TEMPLATE))
    out, n = _run(ctx, monkeypatch, [f])
    assert n == 0 and out[0]["statement"] == "Original."


def test_changed_members_or_prompt_are_rewritten(ctx, monkeypatch):
    f = _fam(members=["a1", "a2", "a3"], statement="Original.", written_for=families.key(["a1", "a2"]), written_with=sha256(TEMPLATE))
    assert _run(ctx, monkeypatch, [f])[1] == 1
    f = _fam(statement="Original.", written_for=families.key(["a1", "a2"]), written_with=sha256("OLDER"))
    assert _run(ctx, monkeypatch, [f])[1] == 1


def test_unstamped_artifact_is_rewritten_then_stamped(ctx, monkeypatch):
    out, n = _run(ctx, monkeypatch, [_fam(statement="Original.")])
    assert n == 1 and out[0]["written_for"] == families.key(["a1", "a2"]) and out[0]["written_with"] == sha256(TEMPLATE)


# ── regating adjudicated prose ─────────────────────────────────────────────────────────────────────

def _gate(ctx, monkeypatch, flag, standing, repaired="REPAIRED STATEMENT."):
    calls = {"rejects": 0}

    def rejects(ctx_, stmt, atoms):
        calls["rejects"] += 1
        return ("why", flag) if calls["rejects"] == 1 else ("", {})
    monkeypatch.setattr(synthesize, "rejects", rejects)
    monkeypatch.setattr(synthesize, "_gate_once", lambda *a, **k: repaired)
    monkeypatch.setattr(synthesize, "covered", lambda c, s, a: [True] * len(a))
    ctx.docket.clear()
    out = synthesize._quality_gate(ctx, "Standing statement.", ["a1"], None, 1, standing=standing)
    return out, ctx.docket


def test_judge_flag_on_standing_prose_is_docketed_and_kept(ctx, monkeypatch):
    out, docket = _gate(ctx, monkeypatch, {"defect": True, "principle": "verb", "confidence": 0.9}, standing=True)
    assert out == "Standing statement." and docket[0]["principle"] == "verb" and "standing" in docket[0]["why"]


def test_every_principle_is_docketed_not_repaired(ctx, monkeypatch):
    for p in ["verb", "vocabulary", "acronym", "fusion", "objects", "clauses", "counterpart", "face_valid", "gerund_stack", "offender_head", "too_long"]:
        out, docket = _gate(ctx, monkeypatch, {"defect": True, "principle": p, "confidence": 1.0}, standing=True)
        assert out == "Standing statement." and docket[-1]["principle"] == p


def test_gerund_false_positive_cannot_rewrite_a_voted_statement(ctx, monkeypatch):
    voted = "Assist clients with bathing, grooming, and dressing during daily personal care routines."
    assert len(synthesize.gerund_clauses(voted)) >= 2
    monkeypatch.setattr(synthesize, "rejects", lambda c, s, a: ("gerunds", {"defect": True, "principle": "gerund_stack", "confidence": 1.0, "trigger": "x"}))
    monkeypatch.setattr(synthesize, "_gate_once", lambda *a, **k: "REWRITTEN, detail dropped.")
    monkeypatch.setattr(synthesize, "covered", lambda c, s, a: [True] * len(a))
    assert synthesize._quality_gate(ctx, voted, ["a1"], None, 1, standing=True) == voted
    assert ctx.docket[0]["principle"] == "gerund_stack"


def test_fresh_draft_is_repaired_normally(ctx, monkeypatch):
    out, docket = _gate(ctx, monkeypatch, {"defect": True, "principle": "verb", "confidence": 0.9}, standing=False)
    assert out == "REPAIRED STATEMENT." and not docket


# ── umbrella lead verbs ────────────────────────────────────────────────────────────────────────────

def _quiet(monkeypatch):
    monkeypatch.setattr(synthesize, "_form", lambda c, s, a: {})
    monkeypatch.setattr(synthesize, "gerund_clauses", lambda s: [])


def test_do_synonyms_and_ensure_are_umbrellas(ctx, monkeypatch):
    _quiet(monkeypatch)
    for stmt in ("Ensure compliance with filing deadlines.", "Use spreadsheets to track hours.",
                 "Utilize diagnostic tools on incoming units.", "Execute test plans for releases."):
        assert synthesize.rejects(ctx, stmt, ["a1"])[1].get("principle") == "offender_head", stmt


def test_perform_passes_when_the_reference_attests_the_pairing(agg, atoms, monkeypatch):
    _quiet(monkeypatch)
    ctx = agg.context(atoms, reference_statements=["Perform bedside procedures on patients."])
    why, w = synthesize.rejects(ctx, "Perform bedside procedures, such as laceration repair.", ["a1"])
    assert not why
    assert synthesize.rejects(ctx, "Perform routine office chores.", ["a1"])[1].get("principle") == "offender_head"


def test_no_reference_means_no_exception_and_an_umbrella(ctx, monkeypatch):
    _quiet(monkeypatch)
    assert synthesize.rejects(ctx, "Perform bedside procedures.", ["a1"])[1].get("principle") == "offender_head"


# ── the atom list and the flag ─────────────────────────────────────────────────────────────────────

def test_tag_items_accepts_a_list(agg):
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}, "a2": {"w2"}}))
    assert synthesize._tag_items(ctx, ["a2", "a1"]).splitlines() == ["- a1  [W1]", "- a2  [W2]"]


def test_statement_reaches_the_writer_with_list_members(agg, monkeypatch):
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}}))
    monkeypatch.setattr(ctx.prompts, "load", lambda *a, **k: TEMPLATE)
    monkeypatch.setattr(synthesize, "covered", lambda c, s, a: [True] * len(a))
    monkeypatch.setattr(synthesize, "_quality_gate", lambda c, stmt, *a, **k: stmt)
    assert synthesize.statement(ctx, "head", ["a1"], standing="Adopted statement.") == "Adopted statement."


def test_stamp_flag_is_current_for_the_statement_on_the_row(ctx):
    flag = {"principle": "fusion", "confidence": 0.97, "trigger": "Do X and do Y"}
    ctx.form_flags["Do X and do Y."] = flag
    assert synthesize.stamp_flag(ctx, {"form_flag": {"stale": True}}, "Do X.").get("form_flag") is None
    assert synthesize.stamp_flag(ctx, {}, "Do X and do Y.")["form_flag"] == flag
