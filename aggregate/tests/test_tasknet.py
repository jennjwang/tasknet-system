"""The TaskNet layer over the aggregator: ingest, the processing files, audit, verify, build's wiring. Offline:
normalize is stubbed (no model is called) and the chain is replaced where a test reaches it."""
import csv
import json

import pytest

import tasknet
import tasknet.processing
from tasknet import pipeline, workspace
from tasknet.aggregation import chain
from tasknet.processing import audit, ingest

OCC = "lawyers"


def _session(sid, tasks, added=(), code="23-1011.00", title="Lawyers"):
    return {"sessionId": sid, "occupationSelection": {"selectedCode": code, "selectedTitle": title},
            "taskItems": [{"name": t, "addedByParticipant": True} for t in added], "selectedTasks": tasks}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PREWARM", "0")
    for k in ("DATA_DIR", "LLM_CACHE_DIR", "TASKNET_CONFIG", "REFERENCE_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    d = tmp_path / "data" / "interviews" / OCC
    d.mkdir(parents=True)
    (d / "s1.json").write_text(json.dumps(_session("s1", ["Draft motions for court.", "- Advise clients\n- File pleadings with the clerk"],
                                                   added=["- Advise clients\n- File pleadings with the clerk"])))
    (d / "s2.json").write_text(json.dumps(_session("s2", ["Draft motions for court.", "Draft motions for court."])))
    # normalize stubbed: every task kept as is (the real one calls models)
    monkeypatch.setattr(tasknet.processing, "normalize", lambda ctx, atoms: tasknet.Atoms([{**r, "original": ""} for r in atoms.rows]))
    return tmp_path


def _rows(path):
    with path.open() as fh:
        return list(csv.DictReader(fh))


def test_explode_splits_a_bulleted_entry():
    assert ingest.explode("- Advise clients\n- File pleadings with the clerk") == ["Advise clients", "File pleadings with the clerk"]
    assert ingest.explode("Draft motions for court.") == ["Draft motions for court."]


def test_process_writes_the_three_files_in_the_old_columns(ws):
    out = tasknet.process(OCC)
    proc = workspace.processing_dir(OCC)
    assert out == proc / "3_deduped.csv"
    ing = _rows(proc / "1_ingested.csv")
    assert list(ing[0]) == ingest.FIELDNAMES and len(ing) == 4          # s2's repeat is one row (ingest dedups)
    assert {r["status"] for r in ing if r["session_id"] == "s1" and r["task_name"] != "Draft motions for court."} == {"added"}
    ded = _rows(out)
    assert {"session_id", "task_name", "onet_code", "onet_title", "original_task_name"} <= set(ded[0])
    assert tasknet.process(OCC) == out                                    # re-run: nothing new, stages skipped
    assert len(_rows(proc / "1_ingested.csv")) == 4


def test_audit_passes_then_catches_a_lost_task(ws, capsys):
    tasknet.process(OCC)
    assert tasknet.audit(OCC) is True
    proc = workspace.processing_dir(OCC)
    rows = _rows(proc / "2_normalized.csv")
    with (proc / "2_normalized.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows[1:])
    assert tasknet.audit(OCC) is False
    assert "B: LOST" in capsys.readouterr().out


def test_build_wires_corpus_workdir_and_adopted_list(ws, monkeypatch):
    tasknet.process(OCC)
    seen = {}

    def fake_build(ctx, standing=None, fresh=False, max_cycles=None, on_stage=None):
        seen.update(occupation=ctx.occupation, workdir=ctx.workdir, standing=standing, fresh=fresh, cycles=max_cycles,
                    n_atoms=len(ctx.atoms.tasks), cache=ctx.cache.dir)
        return {"meta": {}, "core": []}
    monkeypatch.setattr(chain, "build", fake_build)
    final = workspace.aggregation_dir(OCC) / "final.json"
    final.parent.mkdir(parents=True)
    final.write_text(json.dumps({"meta": {}, "core": [{"statement": "Draft motions.", "members": []}]}))
    tasknet.build(OCC, fresh=True, max_rounds=2)
    assert seen["occupation"] == "Lawyers" and seen["workdir"] == workspace.aggregation_dir(OCC)
    assert seen["standing"]["core"][0]["statement"] == "Draft motions." and seen["fresh"] and seen["cycles"] == 2
    assert seen["n_atoms"] == 3 and seen["cache"] == ws / "cache" / "aggregation"


def test_verify_checks_the_floor_against_the_corpus(ws):
    tasknet.process(OCC)
    art = workspace.aggregation_dir(OCC) / "build.json"
    art.parent.mkdir(parents=True)
    fam = {"statement": "Advise clients.", "members": ["Advise clients"], "sources": ["s1"], "prevalence": 1}
    art.write_text(json.dumps({"meta": {"min_sources": 2}, "core": [fam], "released": [], "declined": [],
                               "dropped_atoms": [], "thin_members": []}))
    assert any("floor" in p for p in pipeline.verify(art))


def test_cli_audit_exit_code(ws):
    from tasknet.cli import main
    tasknet.process(OCC)
    assert main(["audit", OCC]) == 0


def test_build_refuses_a_frozen_list(ws, monkeypatch):
    tasknet.process(OCC)
    f = ws / "results" / "evaluation" / "heldout_frozen.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"occupations": {OCC: {"n_held_out_at_freeze": 7}}}))
    monkeypatch.setattr(chain, "build", lambda ctx, **kw: {"meta": {}, "core": []})
    with pytest.raises(SystemExit, match="7 held-out workers"):
        tasknet.build(OCC)
    tasknet.build(OCC, spend_holdout=True)


def test_history_files_migrate_to_ledgers(tmp_path):
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "split_verdicts.json").write_text('{"k": {"verdict": "keep"}}')
    (tmp_path / "ledgers").mkdir()
    (tmp_path / "ledgers" / "sharpen_seen.json").write_text("{}")
    (tmp_path / "history" / "reword_counts.json").write_text('{"x": 1}')
    assert pipeline.migrate_history(tmp_path) == ["history/split_verdicts.json -> ledgers/defuse_seen.json"]
    assert json.loads((tmp_path / "ledgers" / "defuse_seen.json").read_text()) == {"k": {"verdict": "keep"}}
    assert (tmp_path / "ledgers" / "sharpen_seen.json").read_text() == "{}"     # an existing ledger is never overwritten


def test_budget_stops_the_run_through_a_fail_open_judge(tmp_path):
    from types import SimpleNamespace as NS
    from tasknet import Aggregator, Config
    from tasknet.engine import BudgetExceeded

    calls = []

    def create(**kw):
        calls.append(kw["model"])
        return NS(choices=[NS(message=NS(content='{"one_duty": true}'), finish_reason="stop")],
                  usage=NS(prompt_tokens=1_000_000, completion_tokens=0, prompt_tokens_details=None))
    client = NS(chat=NS(completions=NS(create=create)))
    agg = Aggregator(Config.load(prewarm={"on": False}, budget_usd=1.0), client=client, workdir=tmp_path / "w",
                     cache_dir=tmp_path / "c")
    ctx = agg.context(tasknet.Atoms([{"source": "s1", "task": "Wash dishes.", "title": "Dishwashers"}]))
    ctx.judges.duties("Wash dishes.", ["Wash dishes."])               # gpt-5.4-mini, 1M input tokens: $0.75
    ctx.judges.duties("Scrub pots.", ["Scrub pots."])                 # $1.50 now: the cap is reached
    with pytest.raises(BudgetExceeded):
        ctx.judges.duties("Stack plates.", ["Stack plates."])         # duties fails open on Exception, not on this
    assert len(calls) == 2 and agg.engine.spent() == pytest.approx(1.5)
