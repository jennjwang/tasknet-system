#!/usr/bin/env python3
"""normalize — split, filter and rewrite the ingested tasks with a model (processing step 2).

    python -m tasknet.processing.normalize
    python -m tasknet.processing.normalize --dry-run          # report what the caches already say, no calls
    python -m tasknet.processing.normalize --batch-size 50

Input:  results/processing/<occ>/1_ingested.csv.
Output: results/processing/<occ>/2_normalized.csv: the input columns plus original_task_name (the text as
        typed, or for a split task the whole entry it came from; empty when the text didn't change).

Four passes, each with its own prompt in prompts/processing/:
  Pass 0  split      a long typed entry (added/edited, 2+ sentences or over 30 words) becomes one row per task
                     it names; valid_split and coalesce undo splits that only list objects of one verb
  Pass 1  filter     keep or remove each task (the only quality screen); a suggested rewrite counts as a keep
  Pass 2  normalize  rewrite each kept task as an O*NET-style task statement
  Pass 3  brand      remove product and brand names that pass 2 left in
All statuses go through the passes, including confirmed tasks, which can still carry brand names.

Each pass caches its results by task text, in a file named for its model, effort and prompt, so a re-run
only pays for new tasks and changing one pass's model or prompt only re-runs that pass. The rewrites were
checked against 30 human labels (none changed the meaning); re-check a sample if the prompts change.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tasknet.lib import REPO_ROOT, PROMPTS_DIR, sha256, strip_json, load_prompt, results_dir
from tasknet import engine  # engine.client(model) picks the route and API key for every call; importing it loads .env

_hash = sha256  # cache keys are the SHA-256 of the task text
# passes 0, 2 and 3
NORMALIZE_MODEL  = os.environ.get("NORMALIZE_MODEL", "gpt-5.4-mini")
NORMALIZE_EFFORT = os.environ.get("NORMALIZE_EFFORT", "low")  # reasoning effort: none/low/medium/high/xhigh
# pass 1 uses the stronger model: gpt-4o-mini removed many ordinary tasks that gpt-5.4 kept
FILTER_MODEL     = os.environ.get("FILTER_MODEL", "gpt-5.4")
FILTER_EFFORT    = os.environ.get("FILTER_EFFORT", "")            # "" = the model's own default tier

_PROMPT_DIR = PROMPTS_DIR / "processing"


def _cache_path(name, model, effort, prompt_file):
    """The cache file for one pass, named for its model, effort and prompt, so changing any of them starts a
    new file (the old one is kept)."""
    tag = sha256("␟".join([model, effort, sha256((_PROMPT_DIR / prompt_file).read_text())]))[:10]
    return REPO_ROOT / "cache" / "processing" / "stage2_normalize" / f"{name}_{tag}.json"


FILTER_CACHE_PATH    = _cache_path("filter", FILTER_MODEL, FILTER_EFFORT, "filter.txt")
NORMALIZE_CACHE_PATH = _cache_path("normalize", NORMALIZE_MODEL, NORMALIZE_EFFORT, "normalize.txt")
BRAND_CACHE_PATH     = _cache_path("brand", NORMALIZE_MODEL, NORMALIZE_EFFORT, "brand.txt")
SPLIT_CACHE_PATH     = _cache_path("split", NORMALIZE_MODEL, NORMALIZE_EFFORT, "split.txt")

PROC_DIR   = results_dir("processing")   # results/processing[/<OCCUPATION>]
INPUT_CSV  = PROC_DIR / "1_ingested.csv"
OUTPUT_CSV = PROC_DIR / "2_normalized.csv"
DEFAULT_BATCH = 12  # tasks per call; at 40 the model starts leaving tasks unchanged and misses brand names


def load_cache(path: Path) -> dict:
    """A pass's cache, {task hash: result}, or {} if it doesn't exist yet."""
    return json.loads(path.read_text()) if path.exists() else {}


def save_cache(cache: dict, path: Path) -> None:
    """Write a pass's cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def kept(verdict) -> bool:
    """Did the filter keep this task? A suggested rewrite counts as a keep, because the model often says
    `keep: false` alongside a good rewrite that fixes the problem. No verdict yet also counts as a keep
    (the task is retried next run)."""
    if verdict is None:
        return True
    return bool(verdict.get("keep")) or bool(str(verdict.get("rewrite") or "").strip())


# ── model calls ──────────────────────────────────────────────────────────────────

def _call(prompt: str, model: str, effort: str, tasks: list[dict], client, *, reasoning_max: int,
          plain_max: int) -> list[dict]:
    """Send one batch of tasks through one prompt. -> the model's JSON list of results
    gpt-5 models reason before answering, so they get no temperature and a larger output budget
    (`reasoning_max`, which includes the reasoning); other models get temperature 0 and `plain_max`."""
    payload = [{"id": t["id"], "text": t["text"]} for t in tasks]
    if model.startswith("gpt-5"):
        kw = {"max_completion_tokens": reasoning_max}
        if effort:
            kw["reasoning_effort"] = effort
    else:
        kw = {"temperature": 0, "max_tokens": plain_max}
    msgs = [{"role": "user", "content": load_prompt(prompt, tasks_json=json.dumps(payload, ensure_ascii=False))}]
    resp = client.chat.completions.create(model=model, messages=msgs, **kw)
    return json.loads(strip_json(resp.choices[0].message.content))


def caller(prompt: str):
    """The batch call for one pass. Each returns the model's list: split [{"id", "tasks"}], filter
    [{"id", "keep", "rewrite"}], normalize [{"id", "normalized"}], brand [{"id", "scrubbed"}]."""
    if prompt == "filter":
        return lambda tasks, client: _call("filter", FILTER_MODEL, FILTER_EFFORT, tasks, client,
                                           reasoning_max=16000, plain_max=1024)
    return lambda tasks, client: _call(prompt, NORMALIZE_MODEL, NORMALIZE_EFFORT, tasks, client,
                                       reasoning_max=4096, plain_max=2048)


def _by_id(results: list[dict]) -> dict:
    """Index the model's results by the `id` each one echoes back, not by position (a reordered or missing
    result would otherwise shift every answer onto the wrong task). Results without a usable id are skipped."""
    out: dict[int, dict] = {}
    for res in results:
        try:
            out[int(res["id"])] = res
        except (KeyError, TypeError, ValueError):
            continue
    return out


def run_pass(title: str, rows: list[dict], cache: dict, path: Path, text_of, call, value_of, *,
             client, batch_size: int, parallel_calls: int) -> None:
    """Run one pass over every row not yet in `cache`, in parallel batches, saving the cache after each batch.
    `text_of(row)` is the text the model sees; `value_of(row, result or None)` is what gets cached, or None
    to leave the row uncached so the next run tries it again."""
    todo = [r for r in rows if _hash(r["task_name"]) not in cache]
    if not todo:
        if rows:
            print(f"\n{title}: all {len(rows)} cached")
        return
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]

    def one(batch):
        by_id = _by_id(call([{"id": j, "text": text_of(r)} for j, r in enumerate(batch)], client))
        out = {}
        for j, r in enumerate(batch):
            v = value_of(r, by_id.get(j))
            if v is not None:
                out[_hash(r["task_name"])] = v
        return out

    print(f"\n{title} ({len(todo)} uncached) — {len(batches)} batch(es), {parallel_calls} at a time:")
    with ThreadPoolExecutor(max_workers=parallel_calls) as ex:
        for done, fut in enumerate(as_completed([ex.submit(one, b) for b in batches]), 1):
            cache.update(fut.result())
            print(f"  {done}/{len(batches)} batches done")
            save_cache(cache, path)


# ── Pass 0: split long entries ───────────────────────────────────────────────────
# A typed answer at the end of the interview is often a paragraph naming several tasks. Pass 2 doesn't split,
# so without this pass they would stay one long task that no later stage can take apart.

_SENT = re.compile(r"[.!?](?:\s|$)")         # the end of a sentence
_LEAD = re.compile(r"^\s*([A-Za-z]+)")        # a task's first word (its verb)


def wants_split(row: dict) -> bool:
    """Should pass 0 look at this row? Only typed entries (added/edited) of 2+ sentences or over 30 words."""
    t = row["task_name"].strip()
    return (row.get("status") in ("added", "edited")
            and (len(_SENT.findall(t)) >= 2 or len(t.split()) > 30))


def _pieces(raw: dict) -> list[str]:
    """The tasks in the model's split of one entry: tidied, 2+ words each, no repeats. Empty means keep the
    entry whole."""
    out = []
    for p in (raw or {}).get("tasks") or []:
        p = " ".join(str(p).split()).strip()
        if len(p.split()) >= 2 and p not in out:
            out.append(p)
    return out


def _lead_verb(piece: str) -> str:
    """The task's first word, lowercased ("" if none)."""
    m = _LEAD.match(piece)
    return m.group(1).lower() if m else ""


def valid_split(pieces: list[str]) -> bool:
    """Reject a split whose tasks all start with the same verb: that is one action over a list of objects
    ("Document symptoms" / "Document medications"), which should stay one task. Checked when the cache is
    read, so an over-split already in the cache is rejected without a new call."""
    firsts = [_lead_verb(p) for p in pieces]
    return len(pieces) <= 1 or "" in firsts or len(set(firsts)) > 1


def _sentences(text: str) -> list[str]:
    """`text` split into sentences and lines."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]


def _content(text: str) -> set:
    """The words of `text` longer than 3 letters, lowercased."""
    return {w for w in re.findall(r"[a-z][a-z'\-/]+", text.lower()) if len(w) > 3}


def _home_sentence(piece: str, sents: list[str], pos: int):
    """Which sentence of the original entry this task came from (the one sharing most of its words); a task
    matching none gets a key of its own."""
    pc = _content(piece)
    best, idx = 0.0, None
    for i, s in enumerate(sents):
        score = len(pc & _content(s)) / len(pc) if pc else 0.0
        if score > best:
            best, idx = score, i
    return idx if idx is not None else f"unmatched{pos}"


def coalesce(text: str, pieces: list[str]) -> list[str]:
    """Merge split tasks that start with the same verb AND came from the same sentence. -> [str]

    Catches what `valid_split` misses when only some tasks share a verb ("... document their symptoms. Also
    document medications, allergies, and prior care" gave four "Document ..." tasks). Same-verb tasks from
    different sentences are different duties and stay separate. Words the objects share are written once."""
    if len(pieces) < 2:
        return list(pieces)
    sents = _sentences(text)
    groups: dict = {}
    for i, p in enumerate(pieces):
        groups.setdefault((_lead_verb(p), _home_sentence(p, sents, i)), []).append(p)
    out = []
    for key, g in groups.items():
        objs = [p.split(None, 1)[1].strip() for p in g if len(p.split(None, 1)) == 2]
        if len(g) == 1 or not key[0] or len(objs) != len(g):
            out += g
            continue
        words = [o.split() for o in objs]
        n = 0                                           # how many leading words every object shares
        for tup in zip(*words):
            if len({w.lower().strip(",;") for w in tup}) != 1:
                break
            n += 1
        n = max(0, min(n, min(len(w) for w in words) - 1))
        head = (" ".join(words[0][:n]) + " ") if n else ""
        objs = [" ".join(w[n:]) for w in words]
        joined = (objs[0] if len(objs) == 1 else
                  ", ".join(objs[:-1]) + (" and " if len(objs) == 2 else ", and ") + objs[-1])
        out.append(f"{g[0].split(None, 1)[0]} {head}{joined}")
    return out


def split_row(row: dict, split_cache: dict, log: bool = False) -> list[str] | None:
    """The tasks one ingested row becomes, according to the split cache. -> [tasks], or None if the row stays
    whole (not a split candidate, not in the cache yet, rejected, or split into just itself)."""
    pieces = split_cache.get(_hash(row["task_name"])) if wants_split(row) else None
    if not pieces:
        return None
    if not valid_split(pieces):
        if log:
            print(f"  split REJECTED (one verb over an object list): {row['task_name'][:70]!r}")
        return None
    merged = coalesce(row["task_name"], pieces)
    if log and len(merged) < len(pieces):
        print(f"  split COALESCED {len(pieces)} -> {len(merged)}: {row['task_name'][:60]!r}")
    if len(merged) == 1 and merged[0] == row["task_name"]:
        return None
    return merged


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    """Run passes 0-3 over 1_ingested.csv and write 2_normalized.csv."""
    engine.set_stage("normalize")                     # use this stage's own API key, if one is set
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--parallel-calls", dest="parallel_calls", type=int, default=8,
                        help="batches sent at once (1 = one at a time)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not INPUT_CSV.exists():
        print(f"ERROR: {INPUT_CSV} not found.", file=sys.stderr)
        sys.exit(1)

    with INPUT_CSV.open() as f:
        targets = rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} tasks")

    filter_cache    = load_cache(FILTER_CACHE_PATH)
    normalize_cache = load_cache(NORMALIZE_CACHE_PATH)

    if args.dry_run:
        removed = sum(1 for r in targets
                      if _hash(r["task_name"]) in filter_cache
                      and not kept(filter_cache[_hash(r["task_name"])]))
        normed = sum(1 for r in targets
                     if _hash(r["task_name"]) in normalize_cache
                     and normalize_cache[_hash(r["task_name"])] != r["task_name"])
        print(f"Cached: {removed} would be removed, {normed} would be normalized")
        return

    passes = dict(client=engine.client(NORMALIZE_MODEL),           # prints the route and key it uses
                  batch_size=args.batch_size, parallel_calls=max(1, args.parallel_calls))
    task = lambda r: r["task_name"]

    # Pass 0: a split row is replaced by one row per task; `_raw` keeps what the participant typed
    split_cache = load_cache(SPLIT_CACHE_PATH)
    run_pass("Pass 0 — split", [r for r in targets if wants_split(r)], split_cache, SPLIT_CACHE_PATH,
             task, caller("split"), lambda r, res: None if res is None else (_pieces(res) or [r["task_name"]]),
             **passes)
    expanded, n_split = [], 0
    for r in targets:
        pieces = split_row(r, split_cache, log=True)
        if pieces:
            n_split += 1
            expanded += [{**r, "task_name": p, "_raw": r["task_name"]} for p in pieces]
        else:
            expanded.append({**r, "_raw": r["task_name"]})
    if n_split:
        print(f"  → {n_split} narrative(s) became {len(expanded) - len(targets) + n_split} tasks")
    targets = expanded

    # Pass 1: filter; a task the model skips stays uncached, counts as kept, and is retried next run
    run_pass("Pass 1 — filter", targets, filter_cache, FILTER_CACHE_PATH, task, caller("filter"),
             lambda r, res: None if res is None else {"keep": res["keep"], "rewrite": res.get("rewrite")},
             **passes)
    survivors = [r for r in targets if kept(filter_cache.get(_hash(r["task_name"])))]
    recovered = sum(1 for r in survivors
                    if not (filter_cache.get(_hash(r["task_name"])) or {}).get("keep", True))
    print(f"  → {len(targets) - len(survivors)} removed, {len(survivors)} survived"
          + (f" ({recovered} kept on their rewrite despite keep=false)" if recovered else ""))

    # Pass 2: normalize the filter's rewrite if it gave one, else the task itself
    def normalize_input(r: dict) -> str:
        return filter_cache.get(_hash(r["task_name"]), {}).get("rewrite") or r["task_name"]

    run_pass("Pass 2 — normalize", survivors, normalize_cache, NORMALIZE_CACHE_PATH, normalize_input,
             caller("normalize"), lambda r, res: (res or {}).get("normalized") or normalize_input(r), **passes)

    # Pass 3: remove brand names from pass 2's output
    brand_cache = load_cache(BRAND_CACHE_PATH)

    def normalized_of(r: dict) -> str:
        return normalize_cache.get(_hash(r["task_name"]), normalize_input(r))

    run_pass("Pass 3 — brand scrub", survivors, brand_cache, BRAND_CACHE_PATH, normalized_of, caller("brand"),
             lambda r, res: (res or {}).get("scrubbed") or normalized_of(r), **passes)

    # ── Write output ─────────────────────────────────────────────────────────────
    removed_count, changed_count, unchanged = 0, 0, 0
    out_rows = []
    for r in targets:                                   # after the split: one row per task
        key = _hash(r["task_name"])
        if not kept(filter_cache.get(key)):
            removed_count += 1
            continue
        normalized = brand_cache.get(key) or normalize_cache.get(key, r["task_name"])
        out_row = {k: v for k, v in r.items() if k != "_raw"}
        out_row["original_task_name"] = r["_raw"] if normalized != r["_raw"] else ""
        out_row["task_name"] = normalized
        if normalized != r["_raw"]:
            changed_count += 1
        else:
            unchanged += 1
        out_rows.append(out_row)

    fieldnames = list(rows[0].keys()) + ["original_task_name"]
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\nResults: {removed_count} removed, {changed_count} normalized, "
          f"{unchanged} unchanged")
    print(f"Output: {len(out_rows)} tasks written to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
