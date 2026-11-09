"""Offline fixtures: a workspace in tmp_path with two interview sessions, and a stub model client that answers
normalize's four passes deterministically (no test reaches a real model)."""
import json
import re
from types import SimpleNamespace

import pytest

OCC = "keytest-occupation"
SESSIONS = {
    "s-one": {"selectedTasks": ["Draft motions for court", "Advise clients on contracts.",
                                "- Review discovery requests\n- Prepare witness binders"],
              "taskItems": [{"name": "Advise clients on contracts.", "addedByParticipant": True},
                            {"name": "Draft motions for court", "status": "confirmed"}]},
    "s-two": {"selectedTasks": ["Draft motions for court", "Eat lunch at noon", "Draft motions for court"],
              "taskItems": [{"name": "Eat lunch at noon", "status": "edited"}]},
}


def write_sessions(d):
    d.mkdir(parents=True, exist_ok=True)
    for sid, s in SESSIONS.items():
        (d / f"{sid}.json").write_text(json.dumps({
            "sessionId": sid, "occupationSelection": {"selectedCode": "99-9999.00", "selectedTitle": "Keytest Occupation"},
            **s}))
    (d / "screener").mkdir(exist_ok=True)
    (d / "screener" / "not-a-session.json").write_text(json.dumps({"screenedOut": True}))
    return d


class StubClient:
    """Answers every normalize pass at once: keep everything except lunch, add a final period, no splits."""

    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **kw):
        self.calls += 1
        prompt = messages[-1]["content"]
        items = json.loads(re.search(r'\[\s*\{\s*"id".*\}\s*\]', prompt, re.DOTALL).group(0))
        out = [{"id": it["id"], "keep": "lunch" not in it["text"], "tasks": [],
                "normalized": it["text"].rstrip(".") + ".", "scrubbed": it["text"].rstrip(".") + "."} for it in items]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(out)))], usage=None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for v in ("DATA_DIR", "LLM_CACHE_DIR", "REFERENCE_DATA_DIR", "HELDOUT_FROZEN", "SPEND_HOLDOUT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    write_sessions(tmp_path / "data" / "interviews" / OCC)
    return tmp_path


@pytest.fixture
def stub(monkeypatch):
    from tasknet import env
    client = StubClient()
    monkeypatch.setattr(env, "router", lambda model: client)
    return client
