"""The OpenAI Batch API plumbing, once — chunk, upload, submit, poll, harvest into the verdict cache.

`Batch(ctx).fill(section, jobs, model, parse)` runs BEFORE a stage's fan-out: it submits the cache misses and
writes the answers under the same keys the stage's own code reads, so the stage then finds every call a hit.
`custom_id` IS the memo key, which is what lets a harvest survive the process that submitted it and lets
`drain` collect a batch nobody remembers requesting. The caller owns the key and the parse: `parse(text)`
returns the value to cache or None to SKIP (a fail-open verdict must never be cached as an answer).

NEVER LOAD-BEARING: nothing is submitted on a client without the Batch API, on a Claude model, with batch.api
off, below batch.min misses, or on any exception; the stage then pays live. A submitted batch is billed
whether anyone collects it, so every submission goes in a ledger (cache/pending_batches.json) that `fill`
drains first — including when submitting is switched off.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from .engine import bare_model

DONE = ("completed", "failed", "expired", "cancelled")


class Batch:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cfg = ctx.config
        self.ledger_path = Path(ctx.cache.dir).parent / "pending_batches.json"
        self._snap: dict[str, str] = {}

    # ── the pending ledger ─────────────────────────────────────────────────────────────────────────
    @contextlib.contextmanager
    def _lock(self):
        lock = self.ledger_path.with_suffix(".lock")
        fh = None
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            fh = lock.open("a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except Exception:                                 # noqa: BLE001 - a ledger that cannot lock must not stop a build
            fh = None
        try:
            yield
        finally:
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()

    def _read(self):
        try:
            return json.loads(self.ledger_path.read_text())
        except Exception:                                 # noqa: BLE001
            return []

    def _write(self, rows):
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ledger_path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(rows, indent=2))
            tmp.replace(self.ledger_path)
        except Exception:                                 # noqa: BLE001
            pass

    def record(self, ids, model, tag, n=0, occ=""):
        with self._lock():
            rows = [r for r in self._read() if r.get("id") not in set(ids)]
            rows += [{"id": i, "model": model, "tag": tag, "requests": n, "occ": occ or self.ctx.occupation,
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")} for i in ids]
            self._write(rows)

    def forget(self, batch_id):
        drop = {batch_id} if isinstance(batch_id, str) else set(batch_id)
        with self._lock():
            self._write([r for r in self._read() if r.get("id") not in drop])

    def pending(self, tag=None, model=None):
        return [r for r in self._read() if (tag is None or r.get("tag") == tag) and (model is None or r.get("model") == model)]

    # ── plumbing ──────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def body_parser(parse):
        """Wrap a judge's TEXT parser into the BODY parser collect() expects."""
        return lambda b: parse(((b.get("choices") or [{}])[0].get("message") or {}).get("content") or "")

    def batch_model(self, model):
        """The dated snapshot if one is visible (a bare alias resolves to a '-batch' variant that may not exist)."""
        bare = bare_model(model)
        if bare not in self._snap:
            try:
                pat = re.compile(rf"^{re.escape(bare)}-\d{{4}}-\d{{2}}-\d{{2}}$")
                hits = sorted(m.id for m in self.ctx.engine.client(model).models.list() if pat.match(m.id))
                self._snap[bare] = hits[-1] if hits else bare
            except Exception:                             # noqa: BLE001
                self._snap[bare] = bare
        return self._snap[bare]

    def submit(self, requests, model, tag):
        """[(custom_id, body)] -> [batch_id]. One batch per batch.chunk requests. logprobs are stripped."""
        client = self.ctx.engine.client(model)
        chunk = int(self.cfg.get("batch.chunk", 50000))
        ids = []
        for i in range(0, len(requests), chunk):
            block = requests[i:i + chunk]
            bm = self.batch_model(model)
            lines = [json.dumps({"custom_id": cid, "method": "POST", "url": "/v1/chat/completions",
                                 "body": {k: v for k, v in {**body, "model": bm}.items() if k != "logprobs"}})
                     for cid, body in block]
            path = Path(tempfile.gettempdir()) / f"aggregate_batch_{tag}_{os.getpid()}_{i // chunk}.jsonl"
            path.write_text("\n".join(lines))
            with path.open("rb") as fh:
                up = client.files.create(file=fh, purpose="batch")
            b = client.batches.create(input_file_id=up.id, endpoint="/v1/chat/completions", completion_window="24h",
                                      metadata={"purpose": f"aggregate {tag}", "model": model})
            ids.append(b.id)
            print(f"  submitted {b.id}  ({len(block):,} requests, {path.name})")
            self.record([b.id], model, tag, len(block))
        return ids

    def wait(self, ids, model, deadline=None):
        client = self.ctx.engine.client(model)
        limit = float(self.cfg.get("batch.wait_max_s", 1800)) if deadline is None else float(deadline)
        poll = int(self.cfg.get("batch.poll_s", 30))
        t0 = time.time()
        while True:
            st = [client.batches.retrieve(i) for i in ids]
            if all(s.status in DONE for s in st):
                return st
            if time.time() - t0 > limit:
                raise TimeoutError(f"batch wait exceeded {limit:.0f}s; statuses {[s.status for s in st]}")
            print(f"    waiting… {[(s.status, getattr(getattr(s, 'request_counts', None), 'completed', '?')) for s in st]}", flush=True)
            time.sleep(poll)

    def _shout_if_all_failed(self, b, model, client):
        rc = getattr(b, "request_counts", None)
        if not rc or not rc.total or rc.completed:
            return
        why = ""
        try:
            if getattr(b, "error_file_id", None):
                first = client.files.content(b.error_file_id).text.splitlines()[0]
                why = ((json.loads(first).get("response") or {}).get("body") or {}).get("error", {}).get("message", "")
        except Exception:                                 # noqa: BLE001
            pass
        print(f"  !! BATCH FULLY FAILED on {model}: {rc.failed}/{rc.total} requests. {why}\n"
              f"  !! nothing was warmed; these calls will be paid LIVE. Fix the model or the request body.")

    def collect(self, batch_id, model, section, parse):
        """Harvest one finished batch into `section`. `parse(body) -> value | None`. -> (written, skipped)."""
        client = self.ctx.engine.client(model)
        b = client.batches.retrieve(batch_id)
        if not getattr(b, "output_file_id", None):
            print(f"  {batch_id} status={b.status}, no output file ({getattr(b, 'request_counts', None)})")
            self._shout_if_all_failed(b, model, client)
            return 0, 0
        if b.status != "completed":
            print(f"  {batch_id} status={b.status} — harvesting partial output ({getattr(b, 'request_counts', None)})")
        text = client.files.content(b.output_file_id).text
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
            except Exception:                             # noqa: BLE001
                value = None
            if value is None:
                skip += 1
                continue
            self.ctx.cache.put(section, rec.get("custom_id"), value)
            wrote += 1
        self.ctx.cache.save()
        self.forget(batch_id)                             # only after the verdicts are saved
        return wrote, skip

    def drain(self, tag, model, section, parse):
        """Collect every already-paid-for batch for (tag, model) before submitting more. Never raises."""
        parse = self.body_parser(parse)
        total = 0
        for r in self.pending(tag, model):
            try:
                b = self.ctx.engine.client(model).batches.retrieve(r["id"])
                if b.status == "cancelling":
                    print(f"  ledger: {r['id']} is cancelling, leaving it until it has settled")
                    continue
                if b.status in ("cancelled", "failed", "expired"):
                    if getattr(b, "output_file_id", None):
                        wrote, _ = self.collect(r["id"], model, section, parse)
                        total += wrote
                        print(f"  ledger: recovered {wrote:,} verdicts from {b.status} {r['id']}")
                    else:
                        print(f"  ledger: {r['id']} ended {b.status} with no output, dropping it")
                        self.forget(r["id"])
                    continue
                if b.status != "completed":
                    print(f"  ledger: {r['id']} is {b.status}, leaving it")
                    continue
                if not getattr(b, "output_file_id", None):
                    rc = getattr(b, "request_counts", None)
                    print(f"  ledger: {r['id']} completed with no output (completed={getattr(rc, 'completed', '?')}, "
                          f"failed={getattr(rc, 'failed', '?')}), dropping it")
                    self.forget(r["id"])
                    continue
                wrote, _ = self.collect(r["id"], model, section, parse)
                total += wrote
                print(f"  ledger: recovered {wrote:,} verdicts from {r['id']} (submitted {r.get('at', '?')})")
            except Exception as e:                        # noqa: BLE001
                print(f"  ledger: could not drain {r['id']} ({type(e).__name__})")
        return total

    # ── the prefill ───────────────────────────────────────────────────────────────────────────────
    def available(self, model) -> bool:
        return bool(self.cfg.get("batch.api")) and not bare_model(model).startswith("claude") and self.ctx.engine.has_batch_api(model)

    def fill(self, section, jobs, model, parse, *, max_out=2000, json_mode=False, effort=None, tag=""):
        """Pre-fill `section` for the (key, prompt) jobs not already cached. -> n filled. Drains first, always."""
        tag = tag or section
        try:
            already = self.drain(tag, model, section, parse) if self.ctx.engine.has_batch_api(model) else 0
        except Exception as e:                            # noqa: BLE001
            print(f"  batch: drain failed ({type(e).__name__}), continuing")
            already = 0
        if not self.available(model):
            return already
        body_parse = self.body_parser(parse)
        total = already
        seen, misses = set(), []
        for key, prompt in jobs:
            if key in seen:
                continue
            seen.add(key)
            if self.ctx.cache.get(section, key) is None:
                misses.append((key, prompt))
        if len(misses) < int(self.cfg.get("batch.min", 40)):
            return total
        kw = self.ctx.engine.chat_kwargs(model, max_out, json_mode=json_mode, effort=effort)
        reqs = [(key, {"messages": [{"role": "user", "content": p}], **kw}) for key, p in misses]
        try:
            ids = self.submit(reqs, model, tag)
        except Exception as e:                            # noqa: BLE001
            print(f"  batch: submit failed ({type(e).__name__}); paying live")
            return total
        if not ids:
            return total
        try:
            self.wait(ids, model)
        except TimeoutError as e:
            print(f"  batch: {e}\n  batch: leaving them in the ledger; the next run drains them. Paying live now.")
            return total
        for i in ids:
            try:
                wrote, skipped = self.collect(i, model, section, body_parse)
                total += wrote
                print(f"  batch {section}: filled {wrote:,}" + (f", skipped {skipped:,}" if skipped else ""))
            except Exception as e:                        # noqa: BLE001
                print(f"  batch: collect of {i} failed ({type(e).__name__}); it stays in the ledger")
        return total
