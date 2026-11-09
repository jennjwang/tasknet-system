"""Config overlay, prompt lookup and sets, the cache contract, Atoms column mapping, DocSet."""
import json

import pytest

from tasknet import Atoms, Config, DocSet, Prompts
from tasknet.cache import Cache, NoCache


def test_config_overlays_file_then_overrides(tmp_path):
    (tmp_path / "c.yml").write_text("min_sources: 3\nmodels:\n  cluster: {model: gpt-5.5}\n")
    cfg = Config.load(tmp_path / "c.yml", overrides={"chain": {"max_cycles": 1}}, seed=7)
    assert cfg.min_sources == 3 and cfg.models.cluster.model == "gpt-5.5" and cfg.models.cluster.effort == "medium"
    assert cfg.chain.max_cycles == 1 and cfg.seed == 7 and cfg.get("batch.items") is False
    assert cfg.model("embed") == "text-embedding-3-large" and cfg.effort("filter") is None


def test_prompt_set_resolves_variant_then_falls_back(tmp_path):
    (tmp_path / "synthesize_alt.txt").write_text("ALT $items")
    p = Prompts.default(extra=[tmp_path], prompt_set="alt")
    assert p.path("synthesize").name == "synthesize_alt.txt"
    assert p.path("cluster").name == "cluster.txt"
    assert Prompts.default().path("synthesize").name == "synthesize.txt"


def test_prompt_includes_and_extra_dirs(tmp_path):
    (tmp_path / "mine.txt").write_text("MINE {{include:task_definitions}} $x")
    p = Prompts.default(extra=[tmp_path])
    out = p.load("mine", x="filled")
    assert out.startswith("MINE ") and "filled" in out and "{{" not in out
    with pytest.raises(FileNotFoundError):
        p.path("no_such_prompt")


def test_cache_memo_nocache_and_union(tmp_path):
    c = Cache(tmp_path, flush_every=1000, register_atexit=False)
    assert c.memo("s", "k", lambda: 1) == 1 and c.memo("s", "k", lambda: 2) == 1
    assert c.memo("s", "open", lambda: NoCache("fallback")) == "fallback" and c.get("s", "open") is None
    assert c.memo("s", "absent", None, only_cached=True) is None
    c.save()
    other = Cache(tmp_path, register_atexit=False)
    other.put("s", "k2", 2)
    other.save()
    assert json.loads((tmp_path / "s.json").read_text()) == {"k": 1, "k2": 2}
    assert Cache.pair_key("s", "gpt-4o", "T", "x", "y") == Cache.key("T", "x", "y")      # the historical omission


def test_atoms_accepts_legacy_columns_and_writes_canonical(tmp_path):
    a = Atoms([{"session_id": "s1", "onet_code": "1", "onet_title": "T", "status": "added", "task_name": "Do x.", "original_task_name": ""},
               {"session_id": "s2", "onet_code": "1", "onet_title": "T", "status": "added", "task_name": "Do x.", "original_task_name": ""}])
    assert a.tasks == ["Do x."] and a.sources["Do x."] == frozenset({"s1", "s2"}) and a.title == "T" and a.code == "1"
    a.write(tmp_path / "a.csv")
    assert (tmp_path / "a.csv").read_text().splitlines()[0] == "source,task,occupation,title,status,original"
    a.write(tmp_path / "b.csv", legacy=True)
    assert "session_id" in (tmp_path / "b.csv").read_text().splitlines()[0]
    assert Atoms.read(tmp_path / "b.csv").records() == a.records()


def test_docset_splits_tasks_and_covariates(tmp_path):
    d = DocSet([{"id": "d1", "split": "heldout", "atoms": ["A", "A", "B"], "meta": {"salary": 10, "title": "x"}},
                {"id": "d2", "split": "construction", "atoms": ["C"], "meta": {"salary": 20, "year": 2024}}], slug="s")
    assert [t["text"] for t in d.tasks("heldout")] == ["A", "B"] and d.tasks("construction") == [{"source": "d2", "text": "C"}]
    assert d.covariates() == ["salary", "year"]
    d.write(tmp_path / "docs.json")
    assert len(DocSet.read(tmp_path / "docs.json").construction) == 1
