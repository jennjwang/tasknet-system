"""The whole chain in-process over a stub corpus: every judge stubbed, every invariant checked, every artifact
written where chain.build says it writes it."""
import json

from tasknet.aggregation import chain, cluster, synthesize
from conftest import atoms_from_sources, orth_embed

W = {"a1": {"w1"}, "a2": {"w2"}, "b1": {"w3"}, "b2": {"w4"}, "lone": {"w5"}}


FIRST = [[{"head": "Alpha", "members": ["a1", "a2"]}, {"head": "Beta", "members": ["b1", "b2"]}], [], []]


def _stub(ctx, monkeypatch, draws=None):
    draws = iter(draws if draws is not None else FIRST)
    monkeypatch.setattr(cluster, "group", lambda ctx, pile: next(draws))
    monkeypatch.setattr(synthesize, "statement", lambda ctx, head, atoms, siblings=(), standing=None: standing or f"Do {head.lower()} work.")
    j = ctx.judges
    monkeypatch.setattr(j, "embed", orth_embed)
    monkeypatch.setattr(j, "covers", lambda atom, stmt, model=None: True)
    monkeypatch.setattr(j, "duties", lambda stmt, members, draw=1: {"one_duty": True, "outcomes": [], "reason": ""})
    monkeypatch.setattr(j, "overlaps", lambda a, b, draw=1: False)
    monkeypatch.setattr(j, "dispose", lambda atom, tasks, home=None, siblings=(), draw=1: {"fate": "LEAVE", "task": None})
    monkeypatch.setattr(j, "writing_defects", lambda stmt, atoms, exclude=(): {"defect": False, "principle": "none", "trigger": "", "confidence": 0.0})


def test_fresh_build_runs_to_completion(agg, monkeypatch, tmp_path):
    ctx = agg.context(atoms_from_sources(W), workdir=tmp_path / "w")
    _stub(ctx, monkeypatch)
    build = chain.build(ctx)
    stmts = sorted(f["statement"] for f in build["core"])
    assert stmts == ["Do alpha work.", "Do beta work."] and build["released"] == ["lone"]
    assert build["meta"]["n_atoms"] == 5 and build["meta"]["mode"] == "FRESH" and agg.verify(build, ctx.atoms) == []
    assert all(f["written_for"] and f["sid"] for f in build["core"])
    assert (tmp_path / "w" / "build.json").exists() and (tmp_path / "w" / "build.done").exists()
    assert (tmp_path / "w" / "stages" / "refile.json").exists() and (tmp_path / "w" / "verdicts" / "cycle1_fused.json").exists()
    assert json.loads((tmp_path / "w" / "ledgers" / "defuse_seen.json").read_text())
    assert len(build["draw_log"]) == 2                     # cycle 2's empty draw stopped the loop


def test_amend_build_keeps_adopted_statements(agg, monkeypatch, tmp_path):
    ctx = agg.context(atoms_from_sources(W), workdir=tmp_path / "w")
    _stub(ctx, monkeypatch)
    final = chain.build(ctx)
    for f in final["core"]:
        f["statement"] = f["statement"].replace("work", "WORK (adopted)")
    ctx2 = agg.context(atoms_from_sources(W), workdir=tmp_path / "w2")
    _stub(ctx2, monkeypatch, draws=[[], []])
    build = chain.build(ctx2, standing=final)
    assert build["meta"]["mode"] == "AMEND"
    assert sorted(f["statement"] for f in build["core"]) == sorted(f["statement"] for f in final["core"])


def test_aggregator_build_entry_point(agg, monkeypatch, tmp_path):
    atoms = atoms_from_sources(W)
    atoms.write(tmp_path / "atoms.csv")
    ctx = agg.context(atoms, workdir=tmp_path / "w")
    _stub(ctx, monkeypatch)
    monkeypatch.setattr(agg, "context", lambda *a, **k: ctx)
    build = agg.build(tmp_path / "atoms.csv")
    assert len(build["core"]) == 2 and agg.verify(build) == []
