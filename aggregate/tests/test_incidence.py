"""incidence.status quotes from the cache and the ledger; run(partial=True) builds the matrix from whatever is judged."""
from tasknet import DocSet
from tasknet.evaluate import incidence

from conftest import orth_embed

LISTS = {"ours": ["Draft motions for filing.", "Advise clients on settling."], "reference": ["Negotiate settlements."]}
DOCS = DocSet([{"id": "h1", "split": "heldout", "atoms": ["Draft motions.", "Advise on settlement."], "meta": {"salary": 90}},
               {"id": "h2", "split": "heldout", "atoms": ["Negotiate with counsel."], "meta": {"salary": 70}},
               {"id": "c1", "split": "construction", "atoms": ["Draft motions."], "meta": {}}], slug="lawyers")


def test_status_counts_pairs_and_unjudged(no_live_calls, monkeypatch):
    ctx = no_live_calls
    monkeypatch.setattr(ctx.judges, "embed", orth_embed)
    st = incidence.status(ctx, LISTS, DOCS)
    assert st["docs"] == {"heldout": 2, "construction": 1} and st["lists"] == {"ours": 2, "reference": 1}
    assert st["pairs"] > 0 and st["judged"] == 0 and st["unjudged"] == st["pairs"] and st["pending"] == []
    assert 1 <= st["requests"] <= st["pairs"] and st["model"] == ctx.model("pooled")
    a, L = "Draft motions.", LISTS["ours"][0]
    ctx.cache.memo(incidence.SECTION, incidence.pooled_key(ctx, a, L), lambda: {"verdict": "yes"})
    st = incidence.status(ctx, LISTS, DOCS)
    assert st["judged"] == 1 and st["unjudged"] == st["pairs"] - 1


def test_run_gates_unless_partial(no_live_calls, monkeypatch):
    ctx = no_live_calls
    monkeypatch.setattr(ctx.judges, "embed", orth_embed)
    ctx.config["batch"]["min"] = 0                       # nothing is judged live
    res = incidence.run(ctx, LISTS, DOCS)
    assert res["available"] is False and "not judged" in res["why"]
    res = incidence.run(ctx, LISTS, DOCS, partial=True)
    assert res["available"] is True
    assert res["preliminary"]["unjudged"] == res["preliminary"]["pairs"] == res["judge"]["pairs"] > 0
    assert res["uncached_pairs"] == {"total": res["judge"]["pairs"], "treated_as": "not covering"}
