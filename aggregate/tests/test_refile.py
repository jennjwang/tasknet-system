"""refile: who reaches the ballot, FILE / UNFILED / LEAVE, the floor as a placement rule, concurrence on a move."""
from tasknet import families
from tasknet.aggregation import refile
from conftest import atoms_from_sources, flat_embed

CORE = [{"statement": "Draft pleadings and motions for filing with the court.", "members": ["Draft a motion to dismiss."]},
        {"statement": "Advise clients on legal options and risks.", "members": ["Advise a client on settlement risk."]}]
ORPHAN = "Order office supplies for the practice."


def _stub(ctx, monkeypatch, yes=()):
    seen, ok = [], set(yes)

    def fake(atom, label, model=None):
        seen.append((atom, label))
        return (atom, label) in ok
    monkeypatch.setattr(ctx.judges, "covers", fake)
    monkeypatch.setattr(ctx.judges, "embed", flat_embed)
    return seen


def test_unfiled_atom_reaches_the_ballot_and_is_reported_unowned(ctx, monkeypatch):
    seen = _stub(ctx, monkeypatch)
    missing, off_home = refile.excluded(ctx, CORE, unfiled=[ORPHAN])
    assert {L for a, L in seen if a == ORPHAN} == {c["statement"] for c in CORE}
    assert ORPHAN in missing and off_home[ORPHAN] == (None, [])


def test_covered_orphan_is_not_missing(ctx, monkeypatch):
    _stub(ctx, monkeypatch, yes=[(ORPHAN, CORE[1]["statement"])])
    missing, off_home = refile.excluded(ctx, CORE, unfiled=[ORPHAN])
    assert ORPHAN not in missing and off_home[ORPHAN] == (None, [CORE[1]["statement"]])


def test_filed_atom_whose_own_label_covers_it_is_not_scanned_further(ctx, monkeypatch):
    a = CORE[0]["members"][0]
    seen = _stub(ctx, monkeypatch, yes=[(a, CORE[0]["statement"])])
    missing, off_home = refile.excluded(ctx, CORE, unfiled=[])
    assert a not in off_home and a not in missing and [L for x, L in seen if x == a] == [CORE[0]["statement"]]


def test_leftovers_subtract_claimed_atoms():
    build = {"declined": ["a1", "a2"], "thin_members": [], "dropped_atoms": ["a3"], "released": []}
    carried = {k: sorted(set(v) - {"a1", "kept"}) for k, v in {**families.leftovers(build), "released": ["a3"]}.items()}
    assert carried["declined"] == ["a2"] and carried["dropped_atoms"] == ["a3"]


# ── place ──────────────────────────────────────────────────────────────────────────────────────────

def _art():
    return {"meta": {"n_atoms": 8},
            "core": [{"statement": "Do alpha work.", "head": "Do alpha work.", "members": ["a1", "a2"], "sources": ["w1", "w2"],
                      "prevalence": 2, "written_for": "stampF", "written_with": "stampW"},
                     {"statement": "Do beta work.", "head": "Do beta work.", "members": ["b1", "b2"], "sources": ["w3", "w4"],
                      "prevalence": 2, "written_for": "stampB", "written_with": "stampW"}],
            "released": ["p1", "p2", "p3", "p4"]}


_W = {"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "b2": {"w4"}, "p1": {"w5"}, "p2": {"w6"}, "p3": {"w7"}, "p4": {"w8"}}


def _setup(agg, monkeypatch, fates, off_home=None, sources=_W):
    ctx = agg.context(atoms_from_sources(sources))
    monkeypatch.setattr(refile, "excluded", lambda ctx, core, unfiled=(): ([], off_home or {}))
    monkeypatch.setattr(ctx.judges, "dispose", lambda atom, tasks, home=None, siblings=(), draw=1: fates[atom])
    return ctx


def _accounted(art):
    filed = {m for f in art["core"] for m in f["members"]}
    return filed, set(families.pile(art))


LEAVE = {p: {"fate": "LEAVE", "task": None} for p in ("p1", "p2", "p3", "p4")}


def test_pile_atom_filed_joins_family_and_clears_stamp(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, {**LEAVE, "p1": {"fate": "FILE", "task": 1}})
    art, _ = refile.place(ctx, _art())
    fam = art["core"][0]
    assert "p1" in fam["members"] and "written_for" not in fam
    filed, pile = _accounted(art)
    assert len(filed) + len(pile) == 8 and not filed & pile


def test_unfiled_and_leave_stay_in_the_pile(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, {"p1": {"fate": "UNFILED"}, "p2": {"fate": "UNFILED"}, "p3": {"fate": "LEAVE"}, "p4": {"fate": "LEAVE"}})
    art, _ = refile.place(ctx, _art())
    assert len(art["core"]) == 2 and set(art["released"]) == {"p1", "p2", "p3", "p4"}


def test_off_home_atom_moves_only_where_the_disposer_files_it(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, {**LEAVE, "a2": {"fate": "FILE", "task": 2}}, off_home={"a2": ("Do alpha work.", ["Do beta work."])})
    art, _ = refile.place(ctx, _art())
    beta = next(f for f in art["core"] if f["statement"] == "Do beta work.")
    assert "a2" in beta["members"] and "written_for" not in beta and art["refile_log"]["moved"] == 1
    assert not any(f["statement"] == "Do alpha work." for f in art["core"]) and "a1" in art["released"]


def test_off_home_atom_kept_when_disposer_files_it_home(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, {**LEAVE, "a2": {"fate": "FILE", "task": 1}}, off_home={"a2": ("Do alpha work.", [])})
    art, _ = refile.place(ctx, _art())
    assert "a2" in art["core"][0]["members"] and art["core"][0].get("written_for") == "stampF"


def test_off_home_atom_released_only_when_both_judges_agree(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, {**LEAVE, "a2": {"fate": "UNFILED"}}, off_home={"a2": ("Do alpha work.", [])})
    art, _ = refile.place(ctx, _art())
    assert "a2" not in art["core"][0]["members"] and "a2" in art["released"]
    filed, pile = _accounted(art)
    assert len(filed) + len(pile) == 8


def test_bad_file_target_leaves_the_atom_put(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, {**LEAVE, "p1": {"fate": "FILE", "task": 99}})
    art, _ = refile.place(ctx, _art())
    filed, pile = _accounted(art)
    assert "p1" in pile and len(filed) + len(pile) == 8


def test_family_below_the_floor_is_demoted_to_the_pile(agg, monkeypatch):
    ctx = _setup(agg, monkeypatch, LEAVE, sources={**_W, "a2": {"w1"}})
    art, _ = refile.place(ctx, _art())
    assert [f["statement"] for f in art["core"]] == ["Do beta work."]
    assert {"a1", "a2"} <= set(art["released"]) and art["refile_log"]["demoted"] == ["Do alpha work."]


def test_disagreeing_draws_leave_a_filed_atom_where_it_is(agg, monkeypatch):
    calls = []

    def two_minds(atom, tasks, home=None, siblings=(), draw=1):
        calls.append(draw)
        return {"fate": "UNFILED", "task": None} if draw == 1 else {"fate": "FILE", "task": 1}
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "b2": {"w4"}}))
    monkeypatch.setattr(refile, "excluded", lambda ctx, core, unfiled=(): ([], {"a2": ("Do alpha work.", [])}))
    monkeypatch.setattr(ctx.judges, "dispose", two_minds)
    art, _ = refile.place(ctx, {"core": [{"statement": "Do alpha work.", "members": ["a1", "a2"]},
                                         {"statement": "Do beta work.", "members": ["b1", "b2"]}], "released": []})
    assert "a2" in art["core"][0]["members"] and calls == [1, 2]


def test_a_keep_is_not_paid_for_twice(agg, monkeypatch):
    calls = []
    ctx = agg.context(atoms_from_sources({"a1": {"w1"}, "a2": {"w2"}}))
    monkeypatch.setattr(refile, "excluded", lambda ctx, core, unfiled=(): ([], {"a2": ("Do alpha work.", [])}))
    monkeypatch.setattr(ctx.judges, "dispose", lambda atom, tasks, home=None, siblings=(), draw=1: calls.append(draw) or {"fate": "FILE", "task": 1})
    refile.place(ctx, {"core": [{"statement": "Do alpha work.", "members": ["a1", "a2"]}], "released": []})
    assert calls == [1]
