"""The model judges the aggregation steps share (`tasknet.judges`).

    embed(stmts)                      -> {stmt: unit vector}   embeddings, the cheap screen before any model call
    overlap_labels(a, b, draw=1)      -> 'yes' | 'no'          does any act appear in both statements?
    covers(report, label)             -> bool                  does `report` fall under `label`?
    one_duty(stmt, members, draw=1)   -> {one_duty, outcomes, reason}   is this cluster two duties?
    writing(stmt, title, own_tasks)   -> {defect, principle, trigger, confidence}   the main writing defect

Each judge builds its request in one `_<judge>_req` function, which its Batch API pre-fill (`<judge>_prefill`,
tasknet.batch) also uses, so the two always write and read the same cache key. `writing` has no pre-fill.

Cache keys are part of the interface: every judge caches on (model, prompt text, arguments), so editing a
prompt or changing a model default makes every stored verdict miss and be paid for again. Moving a prompt file
is free (keys hash its content, not its path). The cache section names and key tags below ("overlap_verify",
"OVLPVERIFY", "covers_reasoned", "COVREASONED", "OD1", "WO4") are frozen: renaming one orphans its cache.
"""
import functools
import json
import os
import sqlite3

from tasknet import batch
from tasknet import engine

# Judge calls run at once (threads). They are latency-bound, so wall-clock time ~ calls / PARALLEL_CALLS.
PARALLEL_CALLS = int(os.environ.get("PARALLEL_CALLS", 96))


def prompt_prefix(tmpl, *slots):
    """The part of a prompt template before its first slot: identical on every call, so it can be sent as a
    cacheable prefix (`cache_prefix`, `engine.prewarm`). -> str"""
    at = [tmpl.find(x) for x in slots if tmpl.find(x) >= 0]
    return tmpl[:min(at)] if at else tmpl


# ── embeddings ─────────────────────────────────────────────────────────────────────────────────────────

EMB_MODEL = "text-embedding-3-large"
EMB_BATCH = 256                                         # texts per embedding API call
# a key-value store (key = hash of model + text), so a run reads only the vectors it needs; the directory
# name is frozen and follows LLM_CACHE_DIR
EMB_DB_PATH = engine.CACHE_DIR / "stage2_prefilter" / "embeddings.sqlite"
_EMB_MEM = {}                                           # this run's vectors: key -> unit vector


@functools.cache
def _emb_db_ready():
    """Create the store and turn on WAL mode (concurrent readers, one writer at a time), once per process."""
    EMB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(EMB_DB_PATH, timeout=60)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, v BLOB)")
        c.commit()
    finally:
        c.close()
    return True


def embed(stmts):
    """Embed `stmts`, reading the store first and asking the API only for what it lacks. -> {stmt: unit vector}

    Each call opens its own connection, because SQLite connections must not be shared across threads."""
    import numpy as np
    stmts = list(stmts)
    keys = {s: engine.sha256(EMB_MODEL + "␟" + s) for s in stmts}
    unit = lambda a: a / (np.linalg.norm(a) or 1.0)
    need = sorted({keys[s] for s in stmts if keys[s] not in _EMB_MEM})
    if need:
        _emb_db_ready()
        conn = sqlite3.connect(EMB_DB_PATH, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
        try:
            for i in range(0, len(need), 900):          # SQLite allows ~999 parameters per query
                chunk = need[i:i + 900]
                rows = conn.execute("SELECT k, v FROM emb WHERE k IN (%s)" % ",".join("?" * len(chunk)), chunk)
                for k, v in rows:
                    _EMB_MEM[k] = unit(np.frombuffer(v, dtype=np.float32).astype(np.float64))
            miss = sorted({s for s in stmts if keys[s] not in _EMB_MEM})
            insert = []
            for i in range(0, len(miss), EMB_BATCH):
                chunk = miss[i:i + EMB_BATCH]
                resp = engine.client(EMB_MODEL).embeddings.create(model=EMB_MODEL, input=chunk)
                for s, d in zip(chunk, resp.data):
                    vec = np.asarray(d.embedding, dtype=np.float32)
                    insert.append((keys[s], vec.tobytes()))
                    _EMB_MEM[keys[s]] = unit(vec.astype(np.float64))
            if insert:
                conn.executemany("INSERT OR IGNORE INTO emb (k, v) VALUES (?, ?)", insert)
                conn.commit()
        finally:
            conn.close()
    return {s: _EMB_MEM[keys[s]] for s in stmts}


# ── overlap_labels: does any act appear in both statements? (overlap step) ─────────────────────────────

_OVERLAP_INSTR = engine.load_prompt("overlap_labels")
# One model for every caller, so a merge never acts on a verdict from a different model than the rest; cheaper
# models called obviously distinct pairs duplicates.
OVERLAP_MODEL = os.environ.get("OVERLAP_MODEL", "gpt-5.4")


def _overlap_req(a, b, draw=1):
    """The cache key and prompt for one statement pair. -> (key, prompt). The pair is sorted so (a, b) and
    (b, a) share a verdict; draw > 1 is an independent verdict with its own key."""
    a, b = sorted([a, b])
    return (engine.sha256("␟".join(["OVLPVERIFY", OVERLAP_MODEL, engine.sha256(_OVERLAP_INSTR), a, b]
                                   + ([f"D{draw}"] if draw > 1 else []))),
            f"{_OVERLAP_INSTR}\n\n- {a}\n- {b}")


def _overlap_parse(text):
    """-> 'yes' | 'no'. A volunteered "partial" (or "redundant") counts as yes: a wrong yes costs one closer
    look, a wrong no publishes an overlap."""
    return "yes" if (text or "").strip().lower().startswith(("y", "redundant", "partial")) else "no"


def overlap_labels(a, b, draw=1):
    """Does any act appear in both statements `a` and `b`? -> 'yes' | 'no'

    It sees only the two sentences, so it routes a pair to the resolver rather than deciding a merge; whether
    the same work is published twice is decided on the reports."""
    key, prompt = _overlap_req(a, b, draw)
    engine.prewarm(_OVERLAP_INSTR, OVERLAP_MODEL)       # the long shared instruction: warm its prefix cache
    return engine.memo("overlap_verify", key, lambda: _overlap_parse(
        engine.chat(prompt, OVERLAP_MODEL, max_out=2000, cache_prefix=_OVERLAP_INSTR)))


def overlap_prefill(pairs):
    """Fill the overlap_labels cache for [(a, b)] through the Batch API. -> number filled"""
    if not pairs:
        return 0
    return batch.fill("overlap_verify", [_overlap_req(a, b) for a, b in pairs], OVERLAP_MODEL, _overlap_parse,
                      max_out=2000, tag="overlap_labels")


# ── covers: does a report fall under a statement? (synthesis, overlap and coverage steps) ──────────────

_COVERS_INSTR = engine.load_prompt("coverage")
COVERS_MODEL = os.environ.get("COVERS_MODEL") or "gpt-5.4-mini"


def _covers_req(report, label):
    """The cache key and prompt for one (report, statement) pair. -> (key, prompt). The prompt's "ATOM:" is
    the prompt's own word for a report."""
    return (engine.sha256("␟".join(["COVREASONED", COVERS_MODEL, engine.sha256(_COVERS_INSTR), report, label])),
            f"{_COVERS_INSTR}\n\nATOM: {report}\nLABEL: {label}")


def _covers_parse(text):
    """-> the reply as a dict, or None if it has no yes/no verdict (never cached)."""
    try:
        d = json.loads(engine.strip_json(text) or "{}")
    except Exception:                                   # noqa: BLE001
        return None
    return d if str(d.get("verdict", "")).strip().lower()[:1] in ("y", "n") else None


def covers(report, label):
    """Does `report` fall under `label`: an instance, sub-type or step of the statement's activity, no broader?
    -> bool

    The one definition of "covers". The model names the act, the object and the deciding rule before its
    verdict; the whole reply is cached. An unusable reply raises rather than being cached."""
    key, prompt = _covers_req(report, label)

    def compute():
        r = _covers_parse(engine.chat(prompt, COVERS_MODEL, max_out=700, json_mode=True))
        if r is None:
            raise RuntimeError("covers: unusable reply; refusing to cache a non-verdict")
        return r
    return str(engine.memo("covers_reasoned", key, compute).get("verdict", "")).strip().lower().startswith("y")


def covers_prefill(pairs):
    """Fill the covers cache for [(report, label)] through the Batch API (half price) before a step asks them
    one by one. -> number filled; 0 with BATCH=0, a Claude model, or too few misses."""
    if not pairs:
        return 0
    return batch.fill("covers_reasoned", [_covers_req(a, L) for a, L in pairs], COVERS_MODEL, _covers_parse,
                      max_out=700, json_mode=True, tag="covers_reasoned")


# ── one_duty: is a statement one duty or two? (the split, in the synthesis step) ───────────────────────
# split.decide_split acts on the parts it proposes. There is deliberately no minimum cluster size for asking:
# decide_split can narrow or dissolve a cluster as well as split it in two, which needs no two viable parts.

ONEDUTY_MODEL = os.environ.get("ONEDUTY_MODEL", "gpt-5.4-mini")
ONEDUTY_EFFORT = os.environ.get("ONEDUTY_EFFORT", "low")
# the cache tag: "OD1" (frozen) plus a hash of the prompt with placeholders, so editing the prompt starts afresh
ONEDUTY_TAG = "OD1␟" + engine.sha256(
    engine.load_prompt("one_duty", occupation="$OCC", statement="$S", members="$M"))[:12]


def _one_duty_req(statement, members, draw=1):
    """The cache key and prompt for one statement and its member reports. -> (key, prompt)"""
    block = "\n".join(f"- {m}" for m in sorted(members)) or "(no member activities)"
    tag = ONEDUTY_TAG + (f"␟D{draw}" if draw > 1 else "")
    return (engine.cached_key(ONEDUTY_MODEL, tag, statement, block),
            engine.load_prompt("one_duty", occupation=engine.occupation(), statement=statement, members=block))


def _one_duty_parse(text):
    """-> {one_duty, outcomes, reason}, or None if the reply has no one_duty bool (never cached: a live call
    fails open, a batch row falls through to a live call). Each outcome is {statement, members}; a bare string
    (the shape older cached verdicts still hold) becomes a part with no members."""
    try:
        d = json.loads(engine.strip_json(text) or "{}")
        if not isinstance(d.get("one_duty"), bool):
            return None
        outcomes = []
        for x in d.get("outcomes") or []:
            if isinstance(x, dict):
                st, mem = str(x.get("statement") or "").strip(), [str(m) for m in (x.get("members") or [])]
            else:
                st, mem = str(x).strip(), []
            if st:
                outcomes.append({"statement": st, "members": mem})
        return {"one_duty": d["one_duty"], "outcomes": outcomes, "reason": str(d.get("reason") or "")}
    except Exception:                                   # noqa: BLE001
        return None


def one_duty(statement, members, draw=1):
    """Is `statement`, with its member reports, one duty or two? -> {one_duty, outcomes, reason}

    `outcomes` are the proposed parts, {statement, members}. Fails open (one_duty True, not cached), so a
    failed call never creates a split. Verdicts are not stable across draws, so split.decide_split checks the
    parts against the members and the worker floor before acting. draw > 1 is an independent verdict."""
    key, prompt = _one_duty_req(statement, members, draw)
    # the long instruction is the same on every call: send it as a cacheable prefix, warmed first
    instr = prompt_prefix(engine.load_prompt("one_duty", occupation=engine.occupation()), "$statement", "$members")

    def compute():
        try:
            d = _one_duty_parse(engine.chat(prompt, ONEDUTY_MODEL, json_mode=True, max_out=2000,
                                            effort=ONEDUTY_EFFORT, cache_prefix=instr))
            if d is None:
                raise ValueError("no one_duty bool in the reply")
            return d
        except Exception as e:                          # noqa: BLE001 - fail open, never cache the failure
            return engine.NoCache({"one_duty": True, "outcomes": [], "reason": "", "error": str(e)[:120]})

    engine.prewarm(instr, ONEDUTY_MODEL)
    return engine.memo("one_duty", key, compute)


def one_duty_prefill(rows):
    """Fill the one_duty cache for [(statement, members)] through the Batch API. -> number filled"""
    if not rows:
        return 0
    return batch.fill("one_duty", [_one_duty_req(s, m) for s, m in rows], ONEDUTY_MODEL, _one_duty_parse,
                      max_out=2000, json_mode=True, effort=ONEDUTY_EFFORT, tag="one_duty")


# ── writing: the statement's main writing defect, if any (synthesis step) ──────────────────────────────
# The criteria are prompts/shared/writing_principles.txt; synthesize repairs the one defect named.

WRITING_MODEL = os.environ.get("WRITING_MODEL", "gpt-5.4-mini")
WRITING_EFFORT = os.environ.get("WRITING_EFFORT", "low")
WRITING_TAG = os.environ.get("WRITING_TAG", "WO4")     # frozen cache tag, combined with the prompt's hash
WRITING_PRINCIPLES = ("verb", "vocabulary", "fusion", "objects", "clauses", "counterpart",
                      "acronym", "face_valid", "none")


@functools.cache
def _writing_prompt_version():
    """A short hash of the writing prompt with nothing filled in, so editing the criteria (an included file)
    changes the cache key. -> 12 hex chars"""
    return engine.sha256(engine.load_prompt("writing_only", title="", statement="", own_tasks=""))[:12]


def writing(statement, title, own_tasks):
    """The statement's most serious writing defect, if any. `title` is the occupation and `own_tasks` the
    cluster's reports as "- ..." lines. -> {defect, principle, trigger, confidence}. Fails open (no defect,
    not cached) if the reply can't be parsed."""
    prompt = engine.load_prompt("writing_only", title=title, statement=statement, own_tasks=own_tasks)

    def compute():
        try:
            d = json.loads(engine.strip_json(
                engine.chat(prompt, WRITING_MODEL, json_mode=True, max_out=2000, effort=WRITING_EFFORT)) or "{}")
            if not isinstance(d.get("defect"), bool):
                raise ValueError("no defect bool")
            pr = str(d.get("principle", "none"))
            return {"defect": d["defect"], "principle": pr if pr in WRITING_PRINCIPLES else "none",
                    "trigger": str(d.get("trigger", ""))[:120],
                    "confidence": float(d.get("confidence") or 0.5)}
        except Exception as e:                          # noqa: BLE001 - fail open, never cache the failure
            return engine.NoCache({"defect": False, "principle": "none", "trigger": "",
                                   "confidence": 0.0, "error": str(e)[:120]})

    return engine.memo("writing_only", engine.cached_key(WRITING_MODEL, f"{WRITING_TAG}:{_writing_prompt_version()}",
                                                         statement, title), compute)
