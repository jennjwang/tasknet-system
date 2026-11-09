"""The split hearing: decide's four paths, the leak guard, the fusion ⚑ re-open, member-keyed replay, concurrence."""
import inspect

from tasknet.aggregation import blocks, fusion
from tasknet.lib import sha256
from conftest import atoms_from_sources

ATOM_WORKERS = {"a": {"w1", "w2"}, "b": {"w1", "w2"}, "c": {"w3"}}
FLAG = {"principle": "fusion", "confidence": 0.97, "trigger": "Do X and do Y"}


def _decide(members, outcomes):
    row = {"statement": "S.", "members": members, "prevalence": 3, "n_members": len(members), "reason": "r", "outcomes": outcomes}
    return fusion.decide(row, ATOM_WORKERS, 2)


def test_small_families_are_still_acted_on():
    v, _, basis, _ = _decide(["a", "b", "c"], [{"statement": "P1", "members": ["a", "b"]}, {"statement": "P2", "members": ["c"]}])
    assert (v, basis) == ("split", "one-supported-part")
    v, _, basis, _ = _decide(["a", "b"], [{"statement": "P1", "members": ["a"]}, {"statement": "P2", "members": ["b"]}])
    assert (v, basis) == ("split", "no-supported-part")


def test_no_member_floor_on_duties(ctx):
    assert not hasattr(ctx.judges, "DUTIES_MIN_MEMBERS") and not hasattr(fusion, "MIN_MEMBERS")


# ── every atom of a split lands somewhere ──────────────────────────────────────────────────────────

def _row(**kw):
    return {"verdict": "split", "statement": "Parent statement.", "n_members": 0, "basis": "test", "_shared_hits": {}, **kw}


def _accounted(art):
    from tasknet import families
    return {m for f in art["core"] for m in f["members"]} | set(families.pile(art))


def test_a_thin_parts_atoms_are_recorded_not_discarded(agg):
    members = ["a1", "a2", "a3", "a4"]
    ctx = agg.context(atoms_from_sources({m: {f"w{i}"} for i, m in enumerate(members)}))
    row = _row(members=members, parts=[{"statement": "Published part.", "members": ["a1", "a2"], "publishable": True},
                                       {"statement": "Thin part.", "members": ["a3"], "publishable": False},
                                       {"statement": "(not covered by any proposed part)", "members": ["a4"], "publishable": False}])
    art, log = fusion.apply_splits(ctx, [row], {})
    assert _accounted(art) == set(members) and "a3" in art["dropped_atoms"] and "a4" in art["dropped_atoms"]


def test_overlap_atoms_are_filed_not_double_counted(agg):
    members = ["a1", "a2", "a3"]
    ctx = agg.context(atoms_from_sources({m: {f"w{i}"} for i, m in enumerate(members)}))
    parts = [{"statement": "Part one.", "members": ["a1"], "publishable": True},
             {"statement": "Part two.", "members": ["a2"], "publishable": True},
             {"statement": "(described by MORE THAN ONE proposed part)", "members": ["a3"], "publishable": False}]
    art, log = fusion.apply_splits(ctx, [_row(members=members, parts=parts, _shared_hits={"a3": [0, 1]})], {})
    assert _accounted(art) == set(members) and "a3" not in (art.get("dropped_atoms") or [])
    assert any("a3" in f["members"] for f in art["core"])


def test_the_inputs_leftovers_are_carried(agg):
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}}))
    art, _ = fusion.apply_splits(ctx, [{"verdict": "keep", "statement": "Kept.", "members": ["a1"]}], {},
                                 {"declined": ["d1", "d2"], "thin_members": ["t1"]})
    assert set(art["declined"]) == {"d1", "d2"} and set(art["thin_members"]) == {"t1"} and _accounted(art) == {"a1", "d1", "d2", "t1"}


def test_apply_splits_rehomes_unshown_members(agg, monkeypatch):
    ctx = agg.context(atoms_from_sources({m: {m[0]} for m in ("p1", "p2", "q1", "q2", "u1")}))
    ctx.config["blocks"]["member_cap"] = 4
    monkeypatch.setattr(blocks, "assign_nearest", lambda ctx, orph, parts: {o: (0 if o.startswith("p") else 1) for o in orph})

    def row(shown):
        return {"statement": "Do P and Q", "members": ["p1", "p2", "q1", "q2", "u1"], "shown": shown, "n_members": 5, "prevalence": 3,
                "verdict": "split", "basis": "exclusive-sources", "fused": True,
                "parts": [{"statement": "Do P", "members": ["p1", "p2"], "publishable": True},
                          {"statement": "Do Q", "members": ["q1", "q2"], "publishable": True}], "_shared_hits": {}}
    art, log = fusion.apply_splits(ctx, [row(["p1", "p2", "q1", "q2"])], {}, {})
    assert "u1" in {f["head"]: f["members"] for f in art["core"]}["Do Q"] and log["dropped"] == []
    art2, log2 = fusion.apply_splits(ctx, [row(["p1", "p2", "q1", "q2", "u1"])], {}, {})
    assert any("u1" in d for d in log2["dropped"])


def test_flag_survives_a_pass_through_split(agg):
    ctx = agg.context(atoms_from_sources({"a": {"w1"}, "b": {"w2"}}))
    row = {"verdict": "keep", "statement": "Do X and do Y.", "members": ["a", "b"], "form_flag": FLAG, "written_for": "f", "written_with": "w", "sid": "s_1"}
    art, _ = fusion.apply_splits(ctx, [row], {})
    assert art["core"][0]["form_flag"] == FLAG and art["core"][0]["sid"] == "s_1"


# ── the ⚑ re-opens a standing verdict once; replay keys on members ─────────────────────────────────

def _flagged(stmt="Do X and do Y.", flag=FLAG):
    r = {"statement": stmt, "members": ["a", "b"]}
    if flag:
        r["form_flag"] = flag
    return r


def test_flagged_row_in_the_ledger_is_heard_fresh_once():
    h = sha256("Do X and do Y.")
    fresh, standing = fusion.partition([_flagged()], {h: {"verdict": "keep"}})
    assert len(fresh) == 1 and not standing
    fresh, standing = fusion.partition([_flagged()], {h: {"verdict": "keep", "reopened_on": "fusion"}})
    assert not fresh and len(standing) == 1


def test_unflagged_or_weak_flags_do_not_reopen():
    h = sha256("Do X and do Y.")
    assert fusion.partition([_flagged(flag=None)], {h: {}})[1]
    assert fusion.partition([_flagged(flag={**FLAG, "confidence": 0.5})], {h: {}})[1]
    assert fusion.partition([_flagged(flag={**FLAG, "principle": "vocabulary"})], {h: {}})[1]


def test_a_reworded_family_replays_on_its_members_and_a_changed_set_is_fresh():
    rec = {"verdict": "keep", "fused": False, "members": ["a", "b"], "statement": "Old wording."}
    seen = {sha256("Old wording."): rec}
    row = {"statement": "Completely different wording, same work.", "members": ["b", "a"]}
    assert fusion.partition([row], seen) == ([], [row])
    row = {"statement": "Old wording.", "members": ["a", "b", "c"]}
    assert fusion.partition([row], seen)[0] == [row]
    row = {"statement": "New wording.", "members": ["a", "b"], "form_flag": {"principle": "fusion", "confidence": 0.9}}
    assert fusion.partition([row], seen)[0] == [row]


def test_by_members_replays_the_newest_row_for_a_member_set():
    seen = {"h1": {"statement": "Old wording.", "members": ["a", "b"], "verdict": "keep"},
            "h2": {"statement": "New wording.", "members": ["a", "b"], "verdict": "split"}}
    assert list(fusion.by_members(seen).values())[0]["statement"] == "New wording."


def test_ledger_rows_record_the_statement_and_members_they_judged():
    src = inspect.getsource(fusion.hear)
    i = src.index('seen[sha256(r["statement"])] = {')
    assert '"statement"' in src[i:i + 400] and '"members"' in src[i:i + 400]


# ── concurrence ────────────────────────────────────────────────────────────────────────────────────

def _split_row(basis="shared-sources", corroboration=(), standing=False):
    return {"verdict": "split", "statement": "Parent statement.", "basis": basis, "fused": True, "corroboration": list(corroboration),
            "standing": standing, "members": ["a1", "a2", "a3", "a4"], "n_members": 4, "prevalence": 4, "parts": [], "feedback": "",
            "reason": "", "outcomes": [], "_shared_hits": {}}


def _on(ctx):
    ctx.config["chain"]["concurrence"]["split"] = True
    return ctx


def test_second_signals_skip_the_second_draw(ctx):
    _on(ctx)
    calls = []
    draw = lambda r: calls.append(r) or {"one_duty": False}
    for rows in ([_split_row(basis="exclusive-sources")], [_split_row(corroboration=["GATE 2 repair refused"])], [_split_row(standing=True)]):
        fusion.concurrence(ctx, rows, draw)
        assert rows[0]["verdict"] == "split"
    assert not calls
    rows = [{**_split_row(), "verdict": "keep", "fused": False}]
    fusion.concurrence(ctx, rows, draw)
    assert not calls


def test_lone_split_needs_a_concurring_second_draw(ctx):
    _on(ctx)
    rows = [_split_row()]
    fusion.concurrence(ctx, rows, lambda r: {"one_duty": False})
    assert rows[0]["verdict"] == "split" and "concurrence" in rows[0]["basis"]
    rows = [_split_row()]
    fusion.concurrence(ctx, rows, lambda r: {"one_duty": True})
    assert rows[0]["verdict"] == "keep" and rows[0]["basis"] == "no-concurrence" and rows[0]["feedback"]


def test_the_split_gate_is_off_by_default(ctx):
    rows = [_split_row()]
    fusion.concurrence(ctx, rows, lambda r: {"one_duty": True})
    assert rows[0]["verdict"] == "split"


def test_the_second_draw_asks_for_draw_two(ctx, monkeypatch):
    _on(ctx)
    seen = []
    monkeypatch.setattr(ctx.judges, "duties", lambda s, m, draw=1: seen.append(draw) or {"one_duty": True})
    fusion.concurrence(ctx, [_split_row()])
    assert seen == [2]
    assert ctx.judges.duties_key("S.", ["a", "b"]) != ctx.judges.duties_key("S.", ["a", "b"], draw=2)


def test_refusal_is_corroboration():
    assert fusion.form_corroboration(_flagged())[0].startswith("GATE 2 refused a fusion repair")
    stmt = "Do X and do Y."
    trace = [{"stage": "quality_gate", "draft": stmt, "exit": "0-skipped", "principle": None},
             {"stage": "quality_gate", "draft": stmt, "exit": "accepted", "principle": "verb"}]
    assert fusion.corroboration(stmt, trace) == []
    trace.append({"stage": "quality_gate", "draft": stmt, "exit": "still-rejected", "principle": "fusion"})
    assert len(fusion.corroboration(stmt, trace)) == 1
