"""normalize — split, filter and rewrite raw task texts into canonical O*NET-form statements.

    atoms = processing.normalize(ctx, rows)      # rows: Atoms, or dicts with source / task / status

Four passes, each cached on (model, effort, prompt hash, task text) under <cache_dir>/../processing/stage2_normalize:
  Pass 0 — split:     typed NARRATIVES (>= 2 sentences or > 30 words, status added/edited) become one row per
                      task they name; guarded by valid_split + coalesce
  Pass 1 — filter:    keep / remove / rewrite (the single quality screen); a rewrite counts as a keep
  Pass 2 — normalize: rewrite survivors into a canonical task statement
  Pass 3 — brand:     a focused pass for the product / brand names the multi-rule pass misses
Every status is processed (added, edited AND confirmed). Output rows carry `original` = the text as the
source typed it when it changed.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..atoms import Atoms
from ..lib import atomic_write, load_json, sha256, strip_json

DEFAULT_BATCH = 12
_SENT = re.compile(r"[.!?](?:\s|$)")
_LEAD = re.compile(r"^\s*([A-Za-z]+)")


def _hash(text):
    return sha256(text)


def wants_split(row) -> bool:
    t = (row.get("task") or "").strip()
    return row.get("status") in ("added", "edited") and (len(_SENT.findall(t)) >= 2 or len(t.split()) > 30)


def is_target(row) -> bool:
    return row.get("status") in ("added", "edited", "confirmed")


def kept(verdict) -> bool:
    """Did the filter keep this task? A REWRITE IS A KEEP; an absent verdict is a keep."""
    if verdict is None:
        return True
    return bool(verdict.get("keep")) or bool(str(verdict.get("rewrite") or "").strip())


def valid_split(pieces) -> bool:
    """Reject a split whose pieces ALL share one leading verb (one action over an object list)."""
    if len(pieces) <= 1:
        return True
    firsts = [m.group(1).lower() for p in pieces if (m := _LEAD.match(p))]
    return not (len(firsts) == len(pieces) and len(set(firsts)) == 1)


def _sentences(text):
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]


def _content(text):
    return {w for w in re.findall(r"[a-z][a-z'\-/]+", text.lower()) if len(w) > 3}


def _home_sentence(piece, sents, pos):
    pc = _content(piece)
    best, idx = 0.0, None
    for i, s in enumerate(sents):
        score = len(pc & _content(s)) / len(pc) if pc else 0.0
        if score > best:
            best, idx = score, i
    return idx if idx is not None else f"unmatched{pos}"


def _lead_verb(piece):
    m = _LEAD.match(piece)
    return m.group(1).lower() if m else ""


def coalesce(text, pieces):
    """Merge back the pieces that share a lead verb AND came from ONE sentence of the raw entry -> [str]."""
    if len(pieces) < 2:
        return list(pieces)
    sents = _sentences(text)
    groups, order = {}, []
    for i, p in enumerate(pieces):
        key = (_lead_verb(p), _home_sentence(p, sents, i))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)
    out = []
    for key in order:
        g = groups[key]
        objs = [p.split(None, 1)[1].strip() for p in g if len(p.split(None, 1)) == 2]
        if len(g) == 1 or not key[0] or len(objs) != len(g):
            out += g
            continue
        words = [o.split() for o in objs]
        n = 0
        for tup in zip(*words):
            if len({w.lower().strip(",;") for w in tup}) != 1:
                break
            n += 1
        n = max(0, min(n, min(len(w) for w in words) - 1))
        head = (" ".join(words[0][:n]) + " ") if n else ""
        objs = [" ".join(w[n:]) for w in words]
        joined = (objs[0] if len(objs) == 1 else ", ".join(objs[:-1]) + (" and " if len(objs) == 2 else ", and ") + objs[-1])
        out.append(f"{g[0].split(None, 1)[0]} {head}{joined}")
    return out


def _pieces(raw):
    out = []
    for p in (raw or {}).get("tasks") or []:
        p = " ".join(str(p).split()).strip()
        if len(p.split()) >= 2 and p not in out:
            out.append(p)
    return out


def _by_id(results):
    out = {}
    for res in results:
        try:
            out[int(res["id"])] = res
        except (KeyError, TypeError, ValueError):
            continue
    return out


class _Pass:
    """One cached model pass over batches of {id, text}."""

    def __init__(self, ctx, name, prompt, model, effort, max_out):
        self.ctx, self.name, self.prompt, self.model, self.effort, self.max_out = ctx, name, prompt, model, effort, max_out
        tag = sha256("␟".join([model, effort or "", sha256(ctx.prompts.path(prompt).read_text())]))[:10]
        self.path = Path(ctx.cache.dir).parent / "processing" / "stage2_normalize" / f"{name}_{tag}.json"
        self.cache = load_json(self.path, {}) or {}

    def save(self):
        atomic_write(self.path, self.cache)

    def call(self, tagged):
        prompt = self.ctx.prompt(self.prompt, tasks_json=json.dumps(tagged, ensure_ascii=False))
        kw = self.ctx.engine.chat_kwargs(self.model, self.max_out, effort=self.effort or None)
        kw.pop("seed", None)
        r = self.ctx.engine.create(self.model, [{"role": "user", "content": prompt}], **kw)
        return _by_id(json.loads(strip_json(r.choices[0].message.content)))

    def run(self, rows, text_of, store, batch_size, workers, label):
        todo = [r for r in rows if _hash(r["task"]) not in self.cache]
        if not todo:
            print(f"  {label}: all {len(rows)} cached")
            return
        batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
        print(f"  {label} ({len(todo)} uncached) — {len(batches)} batch(es)")

        def one(batch):
            by_id = self.call([{"id": j, "text": text_of(r)} for j, r in enumerate(batch)])
            out = {}
            for j, r in enumerate(batch):
                v = store(r, by_id.get(j))
                if v is not None:
                    out[_hash(r["task"])] = v
            return out
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(one, b) for b in batches]):
                self.cache.update(fut.result())
                self.save()


def normalize(ctx, rows, batch_size=DEFAULT_BATCH, workers=None) -> Atoms:
    """rows (Atoms or dicts with source, task, status, ...) -> Atoms with normalized `task` and
    `original`. Non-target rows pass through unchanged."""
    rows = [dict(r) for r in (rows.rows if isinstance(rows, Atoms) else rows)]
    workers = workers or ctx.workers_for("normalize")
    nm, ne = ctx.model("normalize"), ctx.effort("normalize")
    fm, fe = ctx.model("filter"), ctx.effort("filter")
    split_p = _Pass(ctx, "split", "split", nm, ne, 4096)
    filter_p = _Pass(ctx, "filter", "filter", fm, fe, 16000)
    norm_p = _Pass(ctx, "normalize", "normalize", nm, ne, 4096)
    brand_p = _Pass(ctx, "brand", "brand", nm, ne, 4096)
    targets = [r for r in rows if is_target(r)]
    print(f"normalize: {len(rows)} tasks, {len(targets)} to process, {len(rows) - len(targets)} pass-through")
    # pass 0 — split
    split_rows = [r for r in targets if wants_split(r)]
    split_p.run(split_rows, lambda r: r["task"],
                lambda r, res: (_pieces(res) or [r["task"]]) if res is not None else None, batch_size, workers, "Pass 0 — split")
    expanded, n_split = [], 0
    for r in targets:
        pieces = split_p.cache.get(_hash(r["task"])) if wants_split(r) else None
        if pieces and not valid_split(pieces):
            print(f"  split REJECTED (one verb over an object list): {r['task'][:70]!r}")
            pieces = None
        elif pieces:
            merged = coalesce(r["task"], pieces)
            if len(merged) < len(pieces):
                print(f"  split COALESCED {len(pieces)} -> {len(merged)}: {r['task'][:60]!r}")
            pieces = merged
        if pieces and (len(pieces) > 1 or pieces[0] != r["task"]):
            n_split += 1
            expanded += [{**r, "task": p, "_raw": r["task"]} for p in pieces]
        else:
            expanded.append({**r, "_raw": r["task"]})
    if n_split:
        print(f"  → {n_split} narrative(s) became {len(expanded) - len(targets) + n_split} tasks")
    targets = expanded
    # pass 1 — filter
    filter_p.run(targets, lambda r: r["task"],
                 lambda r, res: {"keep": res["keep"], "rewrite": res.get("rewrite")} if res is not None else None,
                 batch_size, workers, "Pass 1 — filter")
    survivors = [r for r in targets if kept(filter_p.cache.get(_hash(r["task"])))]
    print(f"  → {len(targets) - len(survivors)} removed, {len(survivors)} survived")

    def normalize_input(r):
        return filter_p.cache.get(_hash(r["task"]), {}).get("rewrite") or r["task"]
    # pass 2 — normalize
    norm_p.run(survivors, normalize_input, lambda r, res: (res or {}).get("normalized") or normalize_input(r),
               batch_size, workers, "Pass 2 — normalize")

    def normalized_of(r):
        return norm_p.cache.get(_hash(r["task"]), normalize_input(r))
    # pass 3 — brand scrub
    brand_p.run(survivors, normalized_of, lambda r, res: (res or {}).get("scrubbed") or normalized_of(r),
                batch_size, workers, "Pass 3 — brand scrub")
    out_rows = [{**r, "original": ""} for r in rows if not is_target(r)]
    removed = changed = 0
    for r in targets:
        key = _hash(r["task"])
        if not kept(filter_p.cache.get(key)):
            removed += 1
            continue
        normalized = brand_p.cache.get(key) or norm_p.cache.get(key, r["task"])
        row = {k: v for k, v in r.items() if k != "_raw"}
        row["original"] = r["_raw"] if normalized != r["_raw"] else ""
        row["task"] = normalized
        changed += normalized != r["_raw"]
        out_rows.append(row)
    print(f"  normalize: {removed} removed, {changed} rewritten, {len(out_rows)} rows out", file=sys.stderr)
    return Atoms(out_rows)
