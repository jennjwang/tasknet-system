"""OpenAI Batch API helpers: pre-fill a judge's verdict cache at half price before a stage asks live.

fill() takes the (cache key, prompt) pairs a judge would ask, submits the ones not yet cached, waits, and writes
each reply under the same key, so the stage's own live calls then hit the cache. Each request's `custom_id` is
its cache key, so a batch can be collected by any later process. The caller supplies the parse function: it
returns the value to cache, or None to skip a reply, so a broken reply is never cached and stays a live call.

Every submitted batch is recorded in a pending list (cache/pending_batches.json) until it is collected, and
fill() first drains any finished batches there, so a billed batch is never abandoned.
"""
import contextlib
try:
    import fcntl                                    # POSIX only; without it the lock fails open
except ImportError:
    fcntl = None
import json
import re
import os
import tempfile
import time
from pathlib import Path

from tasknet import engine

CHUNK = int(os.environ.get("BATCH_CHUNK", "50000"))     # requests per batch file (the API cap)
POLL = int(os.environ.get("BATCH_POLL", "30"))           # seconds between status checks
DONE = ("completed", "failed", "expired", "cancelled")


# ── The pending list ───────────────────────────────────────────────────────────────────────────────────
# A submitted batch is billed whether or not anyone collects it, so every submission is written down and every
# successful collect erases it; drain() harvests finished batches before anything new is submitted. Entries are
# keyed by (tag, model), not by build: a batch submitted for a different statement set still holds verdicts
# under correct content keys, so collecting it is free. The list lives with the cache: regenerable state.
_PENDING = engine.ROOT / "cache" / "pending_batches.json"


@contextlib.contextmanager
def _pending_lock():
    """Hold an exclusive file lock across a read-modify-write of the pending list.

    Without it, two processes calling record()/forget() at once keep only the last write, and the other's
    batch is orphaned. Fails open: if flock is unavailable the body still runs, so the pending list never stops a
    build."""
    lock = _PENDING.with_suffix(".lock")
    fh = None
    try:
        _PENDING.parent.mkdir(parents=True, exist_ok=True)
        fh = lock.open("a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()


def _read_pending():
    """The pending rows. -> list ([] if missing or unreadable)"""
    try:
        return json.loads(_PENDING.read_text())
    except Exception:
        return []


def _write_pending(rows):
    """Replace the pending list with `rows` (temp file + rename). Never raises. -> None"""
    try:
        _PENDING.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PENDING.with_suffix(f".{os.getpid()}.tmp")      # PID: two writers must not share a tmp file
        tmp.write_text(json.dumps(rows, indent=2))
        tmp.replace(_PENDING)
    except Exception:
        pass                      # a pending list that cannot be written must not stop a build


def record(ids, model, tag, n=0):
    """Record batch `ids` as submitted and not yet collected. -> None"""
    with _pending_lock():
        rows = [r for r in _read_pending() if r.get("id") not in set(ids)]
        rows += [{"id": i, "model": model, "tag": tag, "requests": n,
                  "occ": os.environ.get("OCCUPATION", ""), "at": time.strftime("%Y-%m-%dT%H:%M:%S")} for i in ids]
        _write_pending(rows)


def forget(batch_id):
    """Remove a batch from the pending list. Takes one id or an iterable of ids. -> None"""
    drop = {batch_id} if isinstance(batch_id, str) else set(batch_id)
    with _pending_lock():
        _write_pending([r for r in _read_pending() if r.get("id") not in drop])


def pending(tag=None, model=None):
    """The pending rows, optionally filtered by tag and model. -> list"""
    return [r for r in _read_pending()
            if (tag is None or r.get("tag") == tag) and (model is None or r.get("model") == model)]


def body_parser(parse):
    """Wrap a judge's reply-text parser into the response-body parser collect() expects.
    -> f(response_body) -> value | None

    drain() and fill() both apply it, so callers always pass the judge's own text parser."""
    return lambda b: parse(((b.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def drain(tag, model, section, parse):
    """Collect every finished batch in the pending list for (tag, model) into `section`. -> verdicts written

    `parse` is the judge's own text parser. Never raises. A batch still running stays in the pending list; one that
    was cancelled, failed or expired without output is dropped from it."""
    parse = body_parser(parse)
    total = 0
    for r in pending(tag, model):
        try:
            b = engine.client(model).batches.retrieve(r["id"])
            # a batch that will never complete is forgotten rather than retried on every later drain
            if b.status in ("cancelled", "cancelling", "failed", "expired"):
                print(f"  pending: {r['id']} ended {b.status}, dropping it")
                forget(r["id"])
                continue
            if b.status != "completed" or not getattr(b, "output_file_id", None):
                print(f"  pending: {r['id']} is {b.status}, leaving it")
                continue
            wrote, _ = collect(r["id"], model, section, parse)
            total += wrote
            print(f"  pending: recovered {wrote:,} verdicts from {r['id']} (submitted {r.get('at','?')})")
        except Exception as e:                              # noqa: BLE001
            print(f"  pending: could not drain {r['id']} ({type(e).__name__})")
    return total


# Batches name the model's dated snapshot. For some models the Batch API resolves a bare alias to a "-batch"
# snapshot that does not exist and fails every request with a 404; a dated snapshot avoids that. Snapshots are
# found with models.list(), falling back to the bare name.
_SNAP = {}


def batch_model(model):
    """The model name to put in a batch body. -> the newest dated snapshot if one is listed, else the bare name"""
    bare = engine._bare(model)
    if bare not in _SNAP:
        try:
            pat = re.compile(rf"^{re.escape(bare)}-\d{{4}}-\d{{2}}-\d{{2}}$")
            hits = sorted(m.id for m in engine.client(model).models.list() if pat.match(m.id))
            _SNAP[bare] = hits[-1] if hits else bare
        except Exception:
            _SNAP[bare] = bare
    return _SNAP[bare]


def submit(requests, model, tag):
    """Submit [(custom_id, body)] requests, one batch per CHUNK, and record each in the pending list. -> [batch_id]"""
    client = engine.client(model)
    ids = []
    for i in range(0, len(requests), CHUNK):
        block = requests[i:i + CHUNK]
        bm = batch_model(model)
        lines = [json.dumps({"custom_id": cid, "method": "POST", "url": "/v1/chat/completions",
                             "body": {**body, "model": bm}})
                 for cid, body in block]
        # The input file is scratch, so it goes to the temp dir, not results/. The PID in the name keeps two
        # concurrent processes from overwriting each other's file before upload.
        path = Path(tempfile.gettempdir()) / f"onet_batch_{tag}_{os.getpid()}_{i // CHUNK}.jsonl"
        path.write_text("\n".join(lines))
        with path.open("rb") as fh:
            up = client.files.create(file=fh, purpose="batch")
        b = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions",
                                  completion_window="24h",
                                  metadata={"purpose": f"onet {tag} warm", "model": model})
        ids.append(b.id)
        print(f"  submitted {b.id}  ({len(block):,} requests, {path.name})")
        record([b.id], model, tag, len(block))      # billed from here on, so it goes in the pending list NOW
    return ids


def wait(ids, model, deadline=None):
    """Poll until every batch reaches a terminal state. -> the final batch objects

    Raises TimeoutError after `deadline` seconds (default BATCH_WAIT_MAX, 30 minutes), since the
    completion window is up to 24h; fill() then leaves the batches in the pending list and the stage runs live."""
    client = engine.client(model)
    limit = float(os.environ.get("BATCH_WAIT_MAX", "1800")) if deadline is None else float(deadline)
    t0 = time.time()
    while True:
        st = [client.batches.retrieve(i) for i in ids]
        if all(s.status in DONE for s in st):
            return st
        if time.time() - t0 > limit:
            raise TimeoutError(f"batch wait exceeded {limit:.0f}s; statuses {[s.status for s in st]}")
        print(f"    waiting… {[(s.status, getattr(getattr(s, 'request_counts', None), 'completed', '?')) for s in st]}",
              flush=True)
        time.sleep(POLL)


def _shout_if_all_failed(b, model, client):
    """Print a loud warning, with the first error message, if a batch failed every request. -> None

    That is a configuration error (model or request body), and every call will then be paid live."""
    rc = getattr(b, "request_counts", None)
    if not rc or not rc.total or rc.completed:
        return
    why = ""
    try:
        if getattr(b, "error_file_id", None):
            first = client.files.content(b.error_file_id).text.splitlines()[0]
            why = ((json.loads(first).get("response") or {}).get("body") or {}).get("error", {}).get("message", "")
    except Exception:
        pass
    print(f"  !! BATCH FULLY FAILED on {model}: {rc.failed}/{rc.total} requests. {why}")
    print("  !! nothing was warmed; these calls will be paid LIVE. Fix the model or the request body.")


def collect(batch_id, model, section, parse):
    """Write one batch's results into cache `section` and remove it from the pending list. -> (written, skipped)

    `parse(response_body)` returns the value to cache, or None to skip the row."""
    client = engine.client(model)
    b = client.batches.retrieve(batch_id)
    # Harvest whatever finished: an expired batch still has the results it completed before the window closed.
    if not getattr(b, "output_file_id", None):
        print(f"  {batch_id} status={b.status}, no output file ({getattr(b, 'request_counts', None)})")
        _shout_if_all_failed(b, model, client)
        return 0, 0
    if b.status != "completed":
        print(f"  {batch_id} status={b.status} — harvesting partial output ({getattr(b, 'request_counts', None)})")
    text = client.files.content(b.output_file_id).text
    with engine._cache_lock:
        sec = engine._load_section(section)
    wrote = skip = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        resp = rec.get("response") or {}
        if rec.get("error") or resp.get("status_code") != 200:
            skip += 1
            continue
        try:
            value = parse(resp.get("body") or {})
        except Exception:
            value = None
        if value is None:
            skip += 1
            continue
        sec[rec.get("custom_id")] = value
        engine._unsaved[0] += 1
        wrote += 1
    engine._save_cache()
    # Forget the batch only after the verdicts are saved, so a failed save leaves it claimable.
    forget(batch_id)
    return wrote, skip


# ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
MIN = int(os.environ.get("BATCH_MIN", "40"))     # below this, the round trip is not worth the wait
ON = os.environ.get("BATCH", "1") != "0"


def fill(section, jobs, model, parse, *, max_out=2000, json_mode=False, effort=None, tag=""):
    """Pre-fill cache `section` for the (key, prompt) `jobs` not already cached, via the Batch API. -> n filled

    Runs before a stage's fan-out; the stage's own calls then hit the cache. `jobs` must use the same keys and
    prompts the judge's live call builds (the judges share a `_req`-style builder for this), or the batch is
    wasted. `parse` is the judge's own reply-text parser; None skips a reply.

    It always drains the pending list first, even when batching is off. It submits nothing for a Claude model (the
    gateway cannot batch), under BATCH=0, or when fewer than BATCH_MIN jobs miss. If the wait
    times out or the Batch API fails, the stage just runs those calls live: batching is never load-bearing."""
    tag = tag or section
    # Drain before the off-switch: BATCH=0 stops new submissions, not collecting what is already paid for.
    try:
        already = drain(tag, model, section, parse)
    except Exception as e:                                  # noqa: BLE001
        print(f"  batch: drain failed ({type(e).__name__}), continuing")
        already = 0
    if not ON or engine._bare(model).startswith("claude"):
        return already
    body_parse = body_parser(parse)
    total = already

    seen, misses = set(), []
    for key, prompt in jobs:
        if key in seen:
            continue
        seen.add(key)
        if engine.memo(section, key, None, only_cached=True) is None:
            misses.append((key, prompt))
    if len(misses) < MIN:
        return total

    kw = engine.chat_kwargs(model, max_out, json_mode=json_mode, effort=effort)
    # custom_id is the cache key, so any later process can collect the batch.
    reqs = [(key, {"messages": [{"role": "user", "content": p}], **kw}) for key, p in misses]
    try:
        ids = submit(reqs, model, tag)
    except Exception as e:                                  # noqa: BLE001 - fall back to live calls
        print(f"  batch: submit failed ({type(e).__name__}: {str(e)[:80]}); paying live now.")
        return total
    if not ids:
        return total
    try:
        wait(ids, model)
    except TimeoutError as e:
        print(f"  batch: {e}\n  batch: leaving them in the pending list; the next run drains them. Paying live now.")
        return total
    except Exception as e:                                  # noqa: BLE001 - submitted ids stay in the pending list
        print(f"  batch: polling failed ({type(e).__name__}: {str(e)[:80]}); the batches stay in the pending list "
              "and the next run drains them. Paying live now.")
        return total
    for i in ids:
        try:
            wrote, skipped = collect(i, model, section, body_parse)
            total += wrote
            print(f"  batch {section}: filled {wrote:,}" + (f", skipped {skipped:,}" if skipped else ""))
        except Exception as e:                              # noqa: BLE001
            print(f"  batch: collect of {i} failed ({type(e).__name__}); it stays in the pending list")
    return total
