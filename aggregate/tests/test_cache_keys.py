"""THE KEY GUARD. Every live judge's cache key, for fixed inputs, must equal what the previous TaskNet code
produced (golden_keys_default.json: the pipeline judges recorded from tasknet-release@da1baf4, the evaluation
judges from the aggregate library they came with). A drifted key reads as a cache miss and silently re-bills
the paid verdict cache."""
import json
from pathlib import Path

import pytest

from tasknet.aggregation import cluster, synthesize
from tasknet.lib import sha256
from tasknet import families

HERE = Path(__file__).resolve().parent
A, B = "Draft motions for court.", "Advise clients on contracts."
C = "File pleadings with the clerk."
S1, S2 = "Prepare legal filings for the court.", "Advise clients on contract terms."


class Recorded(ValueError):
    pass


def _intercept(ctx):
    calls = []

    def memo(section, key, compute, *, only_cached=False):
        calls.append([section, key])
        raise Recorded(section)
    ctx.cache.memo = memo
    return calls


def _probe(fn):
    try:
        fn()
    except Exception:
        pass


@pytest.fixture
def golden(ctx):
    return json.loads((HERE / "golden_keys_default.json").read_text()), ctx


def test_occupation_matches(golden):
    g, ctx = golden
    assert ctx.occupation == g["occupation"]


def test_embedding_and_covers_and_overlap_keys(golden):
    g, ctx = golden
    j = ctx.judges
    assert ctx.embeddings.key(A) == g["embed_key"]
    assert j._covers_req(A, S1)[1] == g["covers"]
    assert j._covers_req(A, S1, "gpt-5.4")[1] == g["covers_model_override"]
    assert j._covers_req(A, S1)[2] == g["covers_prompt"]
    assert j._overlap_req(S2, S1)[0] == g["overlap_d1"]
    assert j._overlap_req(S2, S1, 2)[0] == g["overlap_d2"]
    assert j._overlap_req(S2, S1)[1] == g["overlap_prompt"]


def test_duties_and_dispose_keys(golden):
    g, ctx = golden
    j = ctx.judges
    assert j.duties_tag == g["oneduty_tag"]
    assert j.duties_key(S1, [B, A]) == g["one_duty_d1"]
    assert j.duties_key(S1, [B, A], draw=2) == g["one_duty_d2"]
    assert ctx.prompt("one_duty", occupation=ctx.occupation, statement=S1, members=j._members_block([B, A])) == g["one_duty_prompt"]
    tasks = f"1. {S1}\n2. {S2}"
    assert j.dispose_key(A, tasks, S1, [A, B, C]) == g["dispose_d1"]
    assert j.dispose_key(A, tasks, S1, [A, B, C], draw=2) == g["dispose_d2"]
    assert j._dispose_text(A, tasks, S1, [A, B, C])[3] == g["dispose_prompt"]
    assert j.dispose_key(C, tasks, None, ()) == g["dispose_pile_d1"]


def test_cluster_and_writer_keys(golden):
    g, ctx = golden
    calls = _intercept(ctx)
    _probe(lambda: cluster.group(ctx, [B, A, C]))
    assert calls[-1] == g["cluster_draw"]
    calls.clear()
    _probe(lambda: synthesize.statement(ctx, "Draft court filings", [A, C], siblings=["Advise clients", "Draft court filings"]))
    assert calls[-1] == g["relabel_head"]
    tmpl = ctx.prompt("synthesize", occupation=ctx.occupation)
    assert sha256(tmpl) == g["writer_prompt_hash"]
    synthesize.covered = lambda ctx_, stmt, atoms: [False, True]       # the coverage judge, stubbed
    df = synthesize.Draft(ctx, tmpl, synthesize._tag_items(ctx, [A, C]), "- Advise clients")
    calls.clear()
    _probe(lambda: synthesize._cover_repair(ctx, S1, [A, C], df))
    assert calls[-1] == g["cover_repair"]
    calls.clear()
    _probe(lambda: synthesize._gate_once(ctx, S1, [A, C], df, 1, rnd=1,
                                         verdict=("writing_principles §1.4 — umbrella", {"principle": "offender_head", "confidence": 1.0})))
    assert calls[-1] == g["quality_gate_r1"]
    calls.clear()
    _probe(lambda: synthesize._gate_once(ctx, S1, [A, C], df, 1, rnd=2, verdict=("why2", {"principle": "verb", "confidence": 0.9})))
    assert calls[-1] == g["quality_gate_r2"]


def test_resolve_and_sharpen_keys(golden):
    g, ctx = golden
    calls = _intercept(ctx)
    members_of = {S1: [A, C], S2: [B]}
    _probe(lambda: ctx.judges.merge_or_separate([S1, S2], [(S1, S2)], members_of))
    assert calls[-1] == g["resolve_group"]
    calls.clear()
    _probe(lambda: ctx.judges.sharpen([S1, S2], [(S1, S2)], members_of, frozen=[S2]))
    assert calls[-1] == g["sharpen_group_frozen"]
    calls.clear()
    _probe(lambda: ctx.judges.sharpen([S1, S2], [(S1, S2)], members_of))
    assert calls[-1] == g["sharpen_group"]
    assert families.key([B, A]) == g["family_key"]
    from tasknet.aggregation import overlap
    assert overlap._pairkey([A, C], [B]) == g["pairkey"]


def test_writing_judge_key(golden):
    g, ctx = golden
    calls = _intercept(ctx)
    _probe(lambda: ctx.judges.writing_defects(S1, [A, C]))
    assert calls[-1] == ["writing_only", g["writing_only"]]
    assert ctx.prompt("writing_only", title=ctx.occupation, statement=S1, own_tasks="- " + A + "\n- " + C) == g["writing_only_prompt"]


def test_evaluation_keys(golden):
    g, ctx = golden
    calls = _intercept(ctx)
    _probe(lambda: ctx.judges.relation(S1, "Represent clients in court."))
    assert calls[-1] == g["pair_relation"]
    assert sha256(ctx.judges.relation_instr) == g["pair_relation_instr_hash"]
    from tasknet.evaluate import exposure, occupational, discovery, incidence, silhouette
    calls.clear()
    _probe(lambda: exposure.rate(ctx, "eloundou", S1, "Keytest Occupation"))
    assert calls[-1] == g["exposure_eloundou"]
    calls.clear()
    _probe(lambda: exposure.rate(ctx, "rl", S1, "Keytest Occupation"))
    assert calls[-1] == g["exposure_rl"]
    assert {m: sha256(exposure.system_prompt(ctx, m)) for m in ("eloundou", "rl")} == g["exposure_sys_hash"]
    calls.clear()
    _probe(lambda: occupational.judge(ctx, "SYSPROMPT", S1))
    assert calls[-1] == g["occupational"]
    assert occupational.system_prompt(ctx, "Keytest Occupation", "A fixed description.",
                                      [{"code": "11-1111.00", "title": "Rel One", "description": "Does rel one."}],
                                      code="99-9999.00") == g["occupational_sys_prompt"]
    calls.clear()
    _probe(lambda: discovery.code_statement(ctx, S1, "Keytest Occupation"))
    assert calls[-1] == g["task_codes"]
    assert sha256(discovery.system_prompt()) == g["discovery_sys_hash"]
    calls.clear()
    _probe(lambda: incidence.name_axis(ctx, "Keytest Occupation", [S1, S2], [A, B]))
    assert calls[-1] == g["axis_name"]
    assert silhouette.placement_key(ctx, A, [S1, S2]) == g["r10_key"]
    assert incidence.pooled_key(ctx, A, S1) == g["covers_pooled"]


def test_processing_cache_names(golden):
    g, ctx = golden
    import importlib
    nz = importlib.import_module('tasknet.processing.normalize')
    names = {n: nz._Pass(ctx, n, p, m, e, 1).path.name for n, p, m, e in
             [("filter", "filter", ctx.model("filter"), ctx.effort("filter")),
              ("normalize", "normalize", ctx.model("normalize"), ctx.effort("normalize")),
              ("brand", "brand", ctx.model("normalize"), ctx.effort("normalize")),
              ("split", "split", ctx.model("normalize"), ctx.effort("normalize"))]}
    assert names == g["normalize_cache_files"]
    assert nz._hash("I draft motions and file them.") == g["normalize_entry_key"]
