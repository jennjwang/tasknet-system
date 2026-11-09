"""ingest, process, audit, the held-out guard and the key routing — all offline."""
import csv
import json

import pytest

import tasknet
from tasknet import env, ingest, pipeline
from conftest import OCC


def _rows(p):
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def test_ingest_reads_sessions_status_and_bullets(workspace):
    rows = ingest.read_sessions(workspace / "data" / "interviews" / OCC)
    got = {(r["source"], r["task"]): r["status"] for r in rows}
    assert got[("s-one", "Advise clients on contracts.")] == "added"
    assert got[("s-two", "Eat lunch at noon")] == "edited"
    assert got[("s-one", "Draft motions for court")] == "confirmed"
    assert ("s-one", "Review discovery requests") in got and ("s-one", "Prepare witness binders") in got
    assert sum(1 for r in rows if r["source"] == "s-two" and r["task"] == "Draft motions for court") == 1
    assert {r["occupation"] for r in rows} == {"99-9999.00"} and {r["title"] for r in rows} == {"Keytest Occupation"}


def test_process_writes_the_three_files_and_the_audit_passes(workspace, stub):
    out = tasknet.process(OCC)
    proc = out.parent
    assert [p.name for p in sorted(proc.iterdir())] == ["1_ingested.csv", "2_normalized.csv", "3_deduped.csv"]
    deduped = _rows(out)
    assert set(deduped[0]) >= {"session_id", "task_name", "onet_code", "onet_title", "status", "original_task_name"}
    assert not any("lunch" in r["task_name"] for r in deduped)                 # removed by the filter
    assert {r["task_name"] for r in deduped} >= {"Draft motions for court.", "Advise clients on contracts."}
    assert any(r["original_task_name"] == "Draft motions for court" for r in deduped)
    calls = stub.calls
    assert tasknet.audit(OCC) is True and stub.calls == calls                  # the audit makes no model call


def test_audit_catches_a_lost_row_and_a_stale_dedup(workspace, stub, capsys):
    out = tasknet.process(OCC)
    norm = out.parent / "2_normalized.csv"
    rows = _rows(norm)
    with norm.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows([r for r in rows if "Advise" not in r["task_name"]])
    assert tasknet.audit(OCC) is False
    text = capsys.readouterr().out
    assert "B: LOST" in text and "Advise clients" in text and "C: in 3_deduped with no 2_normalized source" in text


def test_audit_flags_a_session_task_never_ingested(workspace, stub, capsys):
    tasknet.process(OCC)
    s = workspace / "data" / "interviews" / OCC / "s-two.json"
    d = json.loads(s.read_text())
    d["selectedTasks"].append("File pleadings with the clerk")
    s.write_text(json.dumps(d))
    assert tasknet.audit(OCC) is False
    assert "A: never ingested" in capsys.readouterr().out


def test_build_refuses_a_frozen_occupation(workspace, monkeypatch):
    f = workspace / "results" / "evaluation" / "heldout_frozen.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"occupations": {OCC: {"n_held_out_at_freeze": 7}}}))
    with pytest.raises(SystemExit, match="7 held-out"):
        tasknet.build(OCC)
    monkeypatch.setenv("SPEND_HOLDOUT", "1")
    with pytest.raises(SystemExit, match="run `tasknet process"):                # past the guard: no atoms yet
        tasknet.build(OCC)


def test_sim_runs_have_their_own_results(workspace):
    from tasknet.paths import results_dir
    assert results_dir("processing", "lawyers-sim") == workspace / "results" / "sim" / "processing" / "lawyers"


def test_workspace_configs_and_flat_prompts_overlay_aggregate(workspace):
    (workspace / "configs.yml").write_text("models:\n  cluster: {model: gpt-5.5}\n")
    (workspace / "prompts").mkdir()
    (workspace / "prompts" / "task_dimensions.txt").write_text("MY DEFINITION")
    cfg = pipeline.config()
    assert cfg.model("cluster") == "gpt-5.5" and cfg.model("write") == "claude-sonnet-4-6"
    p = pipeline.prompts(cfg)
    assert p.load("task_dimensions") == "MY DEFINITION" and "MY DEFINITION" in p.load("one_duty")


@pytest.fixture
def keys(monkeypatch):
    for v in ("LLM_API_KEY", "LLM_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CLAUDE_API_KEY", "CLAUDE_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def test_router_never_invents_an_endpoint(keys):
    keys.setenv("CLAUDE_API_KEY", "c")
    with pytest.raises(RuntimeError, match="CLAUDE_BASE_URL"):
        env.router("claude-sonnet-4-6")
    keys.setenv("CLAUDE_BASE_URL", "https://gw.example/v1")
    assert str(env.router("claude-sonnet-4-6").base_url).rstrip("/") == "https://gw.example/v1"
    keys.setenv("OPENAI_API_KEY", "o")
    assert str(env.router("gpt-5.4").base_url).rstrip("/") == "https://api.openai.com/v1"
    keys.setenv("LLM_API_KEY", "one")
    keys.setenv("LLM_BASE_URL", "https://proxy.example/v1")
    c = env.router("claude-sonnet-4-6")
    assert c.api_key == "one" and str(c.base_url).rstrip("/") == "https://proxy.example/v1"
