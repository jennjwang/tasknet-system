"""cluster.propose reconciles the artifact with the corpus before it draws, applies the floor, and the chunked
draw unions families split across chunks."""
from tasknet import families
from tasknet.aggregation import blocks, cluster
from conftest import atoms_from_sources


def _art():
    return {"meta": {"n_atoms": 4},
            "core": [{"sid": "s_a", "head": "A", "members": ["a1", "a2"], "sources": ["w1", "w2"], "prevalence": 2},
                     {"sid": "s_b", "head": "B", "members": ["b1"], "sources": ["w3"], "prevalence": 1}],
            "released": ["p1"]}


def _accounted(art):
    filed = {m for f in art["core"] for m in f["members"]}
    pile = set(families.pile(art))
    return len(filed) + len(pile) == art["meta"]["n_atoms"] and not (filed & pile)


def _ctx(agg, sources):
    return agg.context(atoms_from_sources(sources))


def test_new_corpus_atoms_enter_the_pile(agg, monkeypatch):
    ctx = _ctx(agg, {"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "p1": {"w4"}, "n1": {"w5"}, "n2": {"w6"}})
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: [])
    art = cluster.propose(ctx, _art())
    assert set(art["released"]) == {"p1", "n1", "n2"}
    assert art["meta"]["n_atoms"] == 6 and art["draw_pile_log"]["corpus_new"] == 2 and _accounted(art)


def test_vanished_members_leave_their_family(agg, monkeypatch):
    ctx = _ctx(agg, {"a1": {"w1"}, "a2": {"w2"}, "p1": {"w4"}})
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: [])
    art = cluster.propose(ctx, _art())
    assert [f["head"] for f in art["core"]] == ["A"] and art["draw_pile_log"]["corpus_gone"] == ["b1"]
    assert art["meta"]["n_atoms"] == 3 and _accounted(art)


def test_unchanged_corpus_is_a_no_op(agg, monkeypatch):
    ctx = _ctx(agg, {"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "p1": {"w4"}})
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: [])
    art = cluster.propose(ctx, _art())
    assert art["released"] == ["p1"] and art["meta"]["n_atoms"] == 4
    assert art["draw_pile_log"]["corpus_new"] == 0 and art["draw_pile_log"]["corpus_gone"] == []


_CORPUS = {"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "b2": {"w4"}, "p1": {"w5"}, "p2": {"w6"}, "p3": {"w7"}, "p4": {"w8"}}


def _art8():
    return {"meta": {"n_atoms": 8},
            "core": [{"sid": "s1", "statement": "Do alpha work.", "head": "Do alpha work.", "members": ["a1", "a2"], "sources": ["w1", "w2"], "prevalence": 2},
                     {"sid": "s2", "statement": "Do beta work.", "head": "Do beta work.", "members": ["b1", "b2"], "sources": ["w3", "w4"], "prevalence": 2}],
            "released": ["p1", "p2", "p3", "p4"]}


def test_cluster_over_pile_founds_an_ordinary_family(agg, monkeypatch):
    ctx = _ctx(agg, _CORPUS)
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: [{"head": "Run gamma drills", "members": ["p1", "p2"]}])
    art = cluster.propose(ctx, _art8())
    cand = [f for f in art["core"] if f["head"] == "Run gamma drills"]
    assert len(cand) == 1 and sorted(cand[0]["members"]) == ["p1", "p2"] and not cand[0].get("written_for") and cand[0]["sid"]
    assert _accounted(art)


def test_cluster_over_pile_applies_the_floor(agg, monkeypatch):
    ctx = _ctx(agg, {**_CORPUS, "p1": {"w5"}, "p2": {"w5"}})
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: [{"head": "Run gamma drills", "members": ["p1", "p2"]}])
    art = cluster.propose(ctx, _art8())
    assert len(art["core"]) == 2 and {"p1", "p2"} <= set(art["released"])


def test_cluster_over_an_empty_core_is_a_fresh_build(agg, monkeypatch):
    ctx = _ctx(agg, {"p1": {"w5"}, "p2": {"w6"}, "p3": {"w7"}})
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: [{"head": "Run gamma drills", "members": ["p1", "p2"]}])
    art = cluster.propose(ctx, {"meta": {"n_atoms": 3}, "core": [], "released": ["p1", "p2", "p3"]})
    assert [f["head"] for f in art["core"]] == ["Run gamma drills"] and art["released"] == ["p3"]


def test_seed_holds_every_atom_in_the_pile(ctx):
    art = cluster.seed(ctx)
    assert art["core"] == [] and set(art["released"]) == set(ctx.atoms.tasks) and art["meta"]["n_atoms"] == 3
    assert art["meta"]["title"] == "Keytest Occupation" and art["meta"]["code"] == "99-9999.00"


def test_group_chunked_unions_across_chunks(agg, monkeypatch):
    ctx = _ctx(agg, {k: {"w"} for k in ["a1", "a2", "b1", "a3", "b2", "c1"]})
    ctx.config["blocks"]["cluster_chunk"] = 3
    monkeypatch.setattr(blocks, "kmeans_chunks", lambda ctx, labels, chunk, seed=0: [labels[:3], labels[3:]])
    calls = []

    def fake_group(ctx, labels):
        calls.append(sorted(labels))
        labels = sorted(labels)
        if labels == ["a1", "a2", "b1"]:
            return [{"head": "wash dishes", "members": ["a1", "a2"]}, {"head": "mop floors", "members": ["b1"]}]
        if labels == ["a3", "b2", "c1"]:
            return [{"head": "clean dishware", "members": ["a3"]}, {"head": "mop the floor", "members": ["b2"]}]
        return [{"head": "Wash dishes", "members": ["clean dishware", "wash dishes"]},
                {"head": "Mop floors", "members": ["mop floors", "mop the floor"]}]
    monkeypatch.setattr(cluster, "group", fake_group)
    got = cluster.group_chunked(ctx, ["a1", "a2", "b1", "a3", "b2", "c1"])
    assert len(calls) == 3
    assert {g["head"]: g["members"] for g in got} == {"Wash dishes": ["a1", "a2", "a3"], "Mop floors": ["b1", "b2"]}
    ctx.config["blocks"]["cluster_chunk"] = 0
    assert cluster.group_chunked(ctx, ["a1", "a2"]) == fake_group(ctx, ["a1", "a2"])
