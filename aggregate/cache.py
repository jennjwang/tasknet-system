"""The verdict cache and the embedding store.

Every judge memoises on a content key — model, prompt hash, inputs — so the same statements get the same answer
across rounds AND runs; without it the stochastic model breaks the set-function purity the chain relies on. One
JSON file per SECTION under `dir`, read on first touch, persisted every `flush_every` new entries, on `save()`
and at exit; a save is a UNION with what is on disk (append-only by construction: a key encodes what produced
it, so two processes agreeing on a key agree on its value). A failed call is never cached: wrap a fail-open
value in `NoCache` and the next run retries.

THE KEY RECIPES ARE AN INTERFACE. `Cache.key` and `Cache.pair_key` reproduce the historical `_pkey` /
`cached_key` byte for byte; the ~300 MB of paid verdicts behind them hit only while they stay so.
"""
from __future__ import annotations

import atexit
import json
import sqlite3
import threading
from pathlib import Path

from .lib import atomic_write, sha256, sha256_short


class NoCache:
    """Returned by a compute() to say "use this value, but do NOT store it"."""

    def __init__(self, value):
        self.value = value


class Cache:
    def __init__(self, dir, flush_every: int = 250, register_atexit: bool = True):
        self.dir = Path(dir)
        self.flush_every = flush_every
        self._sections: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._unsaved = 0
        if register_atexit:
            atexit.register(lambda: self.save() if self._unsaved else None)

    # ── keys ───────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def key(*parts) -> str:
        return sha256_short(" § ".join(parts))

    @staticmethod
    def pair_key(section, model, tag, sx, sy) -> str:
        """The key of a (model, tag)-namespaced pair verdict. gpt-4o is omitted from the key for history."""
        return Cache.key(*(([] if model == "gpt-4o" else [model]) + [tag]), sx, sy)

    # ── sections ───────────────────────────────────────────────────────────────────────────────────
    def path(self, section: str) -> Path:
        return self.dir / f"{section}.json"

    def _load(self, section: str) -> dict:
        """MUST be called holding the lock."""
        if section not in self._sections:
            p = self.path(section)
            try:
                self._sections[section] = json.loads(p.read_text()) if p.exists() else {}
            except (OSError, ValueError):
                self._sections[section] = {}
        return self._sections[section]

    def section(self, section: str) -> dict:
        """The live dict of a section (for bulk readers such as a batch harvest); writes through `put`."""
        with self._lock:
            return self._load(section)

    def get(self, section: str, key: str):
        """The stored value, or None — WITHOUT ever computing it."""
        with self._lock:
            return self._load(section).get(key)

    def put(self, section: str, key: str, value) -> None:
        with self._lock:
            self._load(section)[key] = value
            self._unsaved += 1
            due = self._unsaved >= self.flush_every
            if due:
                self._unsaved = 0
        if due:
            self.save()

    def memo(self, section: str, key: str, compute, *, only_cached: bool = False):
        """Content-keyed get-or-compute. A stored value is never None, so None means absent. compute() may
        return NoCache(value) to use a value without storing it; `only_cached` returns None instead of computing."""
        hit = self.get(section, key)
        if hit is not None:
            return hit
        if only_cached:
            return None
        val = compute()
        if isinstance(val, NoCache):
            return val.value
        self.put(section, key, val)
        return val

    def cached(self, section, model, tag, sx, sy, compute):
        return self.memo(section, self.pair_key(section, model, tag, sx, sy), compute)

    def save(self) -> None:
        with self._lock:
            for section, data in self._sections.items():
                if data:
                    p = self.path(section)
                    atomic_write(p, self._union_with_disk(p, data))
            self._unsaved = 0

    @staticmethod
    def _union_with_disk(p: Path, data: dict) -> dict:
        try:
            disk = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError):
            return data
        if not isinstance(disk, dict) or not disk:
            return data
        disk.update(data)
        return disk


class Embeddings:
    """Content-keyed embedding store: a SQLite KV file (WAL, many readers, serialised writers) plus a per-process
    memo of UNIT vectors bounded by the keys used this run. `embed(texts)` SELECTs only the keys it needs and asks
    the model for the misses in batches of 256."""

    BATCH = 256

    def __init__(self, dir, model: str, engine):
        self.dir = Path(dir)
        self.model = model
        self.engine = engine
        self.db = str(self.dir / "embeddings.sqlite")
        self._mem: dict[str, object] = {}
        self._init_lock = threading.Lock()
        self._ready = False

    def key(self, text: str) -> str:
        return sha256(self.model + "␟" + text)

    def _conn(self):
        if not self._ready:
            with self._init_lock:
                if not self._ready:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    c = sqlite3.connect(self.db, timeout=60)
                    c.execute("PRAGMA journal_mode=WAL")
                    c.execute("PRAGMA synchronous=NORMAL")
                    c.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")
                    c.commit()
                    c.close()
                    self._ready = True
        conn = sqlite3.connect(self.db, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def __call__(self, texts):
        import numpy as np
        texts = list(texts)
        keys = {s: self.key(s) for s in texts}
        unit = lambda a: a / (np.linalg.norm(a) or 1.0)
        need = sorted({keys[s] for s in texts if keys[s] not in self._mem})
        if need:
            conn = self._conn()
            try:
                for i in range(0, len(need), 900):
                    chunk = need[i:i + 900]
                    rows = conn.execute("SELECT k, v FROM emb WHERE k IN (%s)" % ",".join("?" * len(chunk)), chunk)
                    for k, v in rows:
                        self._mem[k] = unit(np.frombuffer(v, dtype=np.float32).astype(np.float64))
                miss = sorted({s for s in texts if keys[s] not in self._mem})
                insert = []
                for i in range(0, len(miss), self.BATCH):
                    chunk = miss[i:i + self.BATCH]
                    self.engine.check_budget()
                    resp = self.engine.with_retries(
                        lambda: self.engine.client(self.model).embeddings.create(model=self.model, input=chunk),
                        label=self.model)
                    for s, d in zip(chunk, resp.data):
                        vec = np.asarray(d.embedding, dtype=np.float32)
                        insert.append((keys[s], vec.tobytes()))
                        self._mem[keys[s]] = unit(vec.astype(np.float64))
                if insert:
                    conn.executemany("INSERT OR IGNORE INTO emb (k, v) VALUES (?, ?)", insert)
                    conn.commit()
            finally:
                conn.close()
        return {s: self._mem[keys[s]] for s in texts}
