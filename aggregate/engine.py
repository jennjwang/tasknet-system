#!/usr/bin/env python3
"""Model calls and the verdict cache shared by every pipeline stage (`tasknet.engine`).

No pipeline logic lives here. It provides: per-model routing to an OpenAI-compatible client, the chat wrapper
(chat / chat_kwargs, with retries and an optional Anthropic cacheable prefix), token accounting, the
content-keyed verdict cache (memo / _cached / NoCache), and re-exports of the helpers in tasknet.lib
(load_prompt, atomic_write, sha256, results_dir, strip_json).

Used by tasknet.judges, tasknet.processing and tasknet.aggregation.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time

# Re-exports: callers use these as engine.<name>, so a linter flagging them as unused here is a false positive.
from tasknet.lib import REPO_ROOT as ROOT, env_path, load_prompt, atomic_write, sha256, sha256_short, load_env, results_dir, strip_json  # noqa: E402,F401,I001
import openai                                                    # noqa: E402

load_env(ROOT / ".env")

# ── run config ─────────────────────────────────────────────────────────────────────────

# One cache file per verdict section (cache/aggregation/<section>.json). LLM_CACHE_DIR moves the whole
# verdict cache elsewhere, e.g. to compare two prompts with both runs starting from an empty cache.
CACHE_DIR = env_path("LLM_CACHE_DIR", ROOT / "cache" / "aggregation")


def _section_path(section):
    """The cache file for one section. -> CACHE_DIR/<section>.json"""
    return CACHE_DIR / f"{section}.json"


def _occupation_title():
    """The current run's occupation title (onet_title), read from the processing output 3_deduped.csv.
    -> the title, or '' if it is unavailable."""
    try:
        import csv
        rows = list(csv.DictReader((results_dir("processing") / "3_deduped.csv").open()))
        return rows[0]["onet_title"].strip() if rows else ""
    except Exception:
        return ""


def occupation():
    """The occupation name to render prompts with. -> the title, or "an unspecified occupation".

    Resolved at call time on purpose: results_dir() follows $OCCUPATION on every call, and a constant captured
    at import would render prompts for the wrong occupation if a driver sets $OCCUPATION after importing."""
    return _occupation_title() or "an unspecified occupation"


# ── OpenAI-compatible clients: one per (route, stage), built on first use so importing needs no key ─────
_CLIENTS = {}   # (route, stage) -> client

# Which API key is used. Each stage runs as its own process and names itself with set_stage(). For every
# call, the first of these that is set is used:
#   <STAGE>_API_KEY   (NORMALIZE_API_KEY, CLUSTER_API_KEY, ...) this stage's own key, for all its calls
#   LLM_API_KEY   one key for everything, whichever provider a call goes to
#   OPENAI_API_KEY / CLAUDE_API_KEY   the provider's own key
# One key for every provider needs one endpoint that serves them all (e.g. a proxy such as LiteLLM): set
# LLM_BASE_URL to send every call there.
STAGES = ("normalize", "cluster", "synthesize", "split", "overlap", "coverage")
_STAGE = [os.environ.get("PIPELINE_STAGE", "").lower() or None]


def set_stage(name):
    """Name the stage this process runs, before its first model call. -> name"""
    if name not in STAGES:
        raise ValueError(f"unknown stage {name!r}; expected one of {STAGES}")
    _STAGE[0] = name
    return name


def _key_env(var):
    """Pick the API key: <STAGE>_API_KEY, else LLM_API_KEY, else the provider's `var`. -> (name, value)"""
    names = ([f"{_STAGE[0].upper()}_API_KEY"] if _STAGE[0] else []) + ["LLM_API_KEY"]
    for name in names:
        if os.environ.get(name):
            return name, os.environ[name]
    return var, os.environ.get(var)


def client(model=None):
    """The OpenAI-compatible client for `model`, built on first use. -> openai.OpenAI

    Claude models (claude-*) go to an OpenAI-compatible gateway (CLAUDE_BASE_URL, required); every other
    model, embeddings and the Batch API go to OPENAI_BASE_URL or api.openai.com. A bare client() is the
    OpenAI route. LLM_BASE_URL, if set, sends both routes to one endpoint. The key comes from _key_env.
    The SDK's own retries are off (max_retries=0): with_retries() is the one retry policy."""
    gateway = bool(model) and str(model).startswith("claude")
    route = "claude" if gateway else "openai"
    slot = (route, _STAGE[0])
    if slot not in _CLIENTS:
        if gateway:
            name, key = _key_env("CLAUDE_API_KEY")
            if not key:
                raise RuntimeError(f"no {name} available for {model}")
            url = os.environ.get("LLM_BASE_URL") or os.environ.get("CLAUDE_BASE_URL")
            if not url:
                raise RuntimeError(f"{model} needs an OpenAI-compatible endpoint that serves Claude models: set "
                                   "CLAUDE_BASE_URL (or LLM_BASE_URL, to send every call there) in .env")
            kwargs = {"api_key": key, "timeout": 60.0, "max_retries": 0, "base_url": url}
            print(f"  engine: Claude calls using {name} via {url}", file=sys.stderr)
        else:
            # an explicit endpoint wins (LLM_BASE_URL, else OPENAI_BASE_URL); else OpenAI itself
            base_url = (os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                        or "https://api.openai.com/v1")
            key_name, api_key = _key_env("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(f"no {key_name} available for {model or 'the OpenAI API'}: "
                                   "set OPENAI_API_KEY (or LLM_API_KEY) in .env")
            kwargs = {"api_key": api_key, "timeout": 60.0, "max_retries": 0, "base_url": base_url}
            print(f"  engine: OpenAI-side calls using {key_name} via {base_url}", file=sys.stderr)
        _CLIENTS[slot] = openai.OpenAI(**kwargs)
    return _CLIENTS[slot]


def _bare(model):
    """Strip any provider prefix from a model name. -> e.g. "openai/gpt-5.4" -> "gpt-5.4"

    Every capability test here is a prefix match, so a routed name (as LiteLLM proxies list them) would
    otherwise fail them all and get the wrong request parameters."""
    return str(model or "").rsplit("/", 1)[-1]


def is_reasoning(model):
    """True for models that reject `temperature` and take `max_completion_tokens`: gpt-5, the o-series, and
    Claude through the gateway. -> bool

    reasoning_effort is gpt-5/o-series only (Claude rejects it), so gate that on the prefix, not on this."""
    return bool(model) and _bare(model).startswith(("gpt-5", "o1", "o3", "claude"))


def chat_kwargs(model, max_out, *, json_mode=False, effort=None):
    """The chat.completions keyword arguments for `model`, apart from model and messages. -> dict

    Reasoning models get max_completion_tokens; others get temperature=0 and max_tokens. `effort` is sent
    only to gpt-5/o-series models. A fixed seed is always sent for best-effort reproducibility."""
    kw = ({"max_completion_tokens": max_out} if is_reasoning(model)
          else {"temperature": 0, "max_tokens": max_out})
    if effort and _bare(model).startswith(("gpt-5", "o1", "o3")):
        kw["reasoning_effort"] = effort
    if json_mode and not _bare(model).startswith("claude"):
        # OpenAI-compatible Claude gateways can return an empty {} when response_format=json_object is sent,
        # so it is omitted for Claude; the prompt asks for JSON and callers parse with strip_json().
        kw["response_format"] = {"type": "json_object"}
    kw["seed"] = int(os.environ.get("LLM_SEED", "0"))   # best-effort reproducibility for any cache-miss recompute
    return kw


# Token accounting, printed at exit. Every completion path records here, including call sites that call
# create() directly instead of chat(). cached_tokens shows whether the provider's prefix cache is landing.
# Failures are swallowed: accounting must never break a build. LLM_PRICES overrides the rate table as
# "model=in_per_M/out_per_M,..."; the default rates are estimates and the report says so.
USAGE = {}
_usage_lock = threading.Lock()


def record_usage(model, resp):
    """Accumulate one response's token counts under `model`. Safe to call from any thread, never raises."""
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return
        cached = 0
        det = getattr(u, "prompt_tokens_details", None)
        if det is not None:
            cached = int(getattr(det, "cached_tokens", 0) or 0)
        with _usage_lock:
            e = USAGE.setdefault(_bare(model), {"calls": 0, "in": 0, "out": 0, "cached": 0})
            e["calls"] += 1
            e["in"] += int(getattr(u, "prompt_tokens", 0) or 0)
            e["out"] += int(getattr(u, "completion_tokens", 0) or 0)
            e["cached"] += cached
    except Exception:                                        # noqa: BLE001 - accounting is never load-bearing
        pass


_PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-5.4-mini": (0.30, 1.20), "gpt-5.4": (2.50, 10.00),
           "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00),
           "gpt-5.5": (3.00, 12.00),                       # estimate
           "claude-sonnet-4-6": (3.00, 15.00),             # Anthropic first-party rates; a gateway
           "claude-opus-4-7": (5.00, 25.00)}               # may bill differently — override with LLM_PRICES


def _prices():
    """The rate table ($ per million input/output tokens), with LLM_PRICES overrides applied. -> dict"""
    out = dict(_PRICES)
    for part in (os.environ.get("LLM_PRICES") or "").split(","):
        if "=" in part and "/" in part:
            m, rates = part.split("=", 1)
            try:
                i, o = rates.split("/")
                out[m.strip()] = (float(i), float(o))
            except ValueError:
                pass
    return out


def usage_report():
    """-> the run's token usage as a printable table. Empty string if nothing was called."""
    with _usage_lock:
        rows = sorted(USAGE.items(), key=lambda kv: -kv[1]["in"])
    if not rows:
        return ""
    pr = _prices()
    lines = ["  usage        calls        in        out     cached   cache%   est $"]
    tot = 0.0
    for m, e in rows:
        i_rate, o_rate = pr.get(m, (0.0, 0.0))
        billed = max(e["in"] - e["cached"], 0)
        cost = (billed * i_rate + e["cached"] * i_rate * 0.5 + e["out"] * o_rate) / 1_000_000
        tot += cost
        pct = (100.0 * e["cached"] / e["in"]) if e["in"] else 0.0
        lines.append(f"  {m:<12} {e['calls']:6,} {e['in']:9,} {e['out']:10,} {e['cached']:10,} "
                     f"{pct:6.1f}%  {cost:7.3f}")
    lines.append(f"  {'TOTAL':<12} {sum(e['calls'] for _, e in rows):6,} "
                 f"{sum(e['in'] for _, e in rows):9,} {sum(e['out'] for _, e in rows):10,} "
                 f"{sum(e['cached'] for _, e in rows):10,} {'':7} {tot:7.3f}   (rates are ESTIMATES; "
                 f"override with LLM_PRICES)")
    return "\n".join(lines)


# Prefix-cache warm-up. OpenAI caches long prompt prefixes automatically, but only for calls that arrive after
# the prefix is cached, so the first burst of a concurrent fan-out pays full rate. prewarm() lets the first
# caller send one short call with the shared prefix while the others wait on its Event. The warm request puts
# the prefix in the user message, as the real calls do. PREWARM=0 disables it; failures are swallowed.
_warm_state = {}
_warm_lock = threading.Lock()
WARM_WAIT = float(os.environ.get("PREWARM_WAIT", "45"))


def prewarm(static, model):
    """Warm the provider's prefix cache for `static` once per process; other callers wait for it. -> None"""
    if not static or os.environ.get("PREWARM") == "0":
        return
    key = (sha256(static), _bare(model))
    with _warm_lock:
        ev = _warm_state.get(key)
        mine = ev is None
        if mine:
            ev = _warm_state[key] = threading.Event()
    if not mine:
        ev.wait(timeout=WARM_WAIT)                       # someone else is warming; arrive after they finish
        return
    try:
        cap = int(os.environ.get("PREWARM_MAX_OUT", "64"))
        chat(static + "\n\nReply with the single character X.", model, max_out=cap)
    except Exception:                                    # noqa: BLE001 - never load-bearing
        pass
    finally:
        ev.set()


# Retry settings for with_retries(). A failed call raises, so memo() never caches it.
_SERVER_RETRIES = int(os.environ.get("SERVER_RETRIES", "4"))
_SERVER_BACKOFF = float(os.environ.get("SERVER_BACKOFF", "30"))
_TRANSIENT = (openai.InternalServerError, openai.APITimeoutError, openai.APIConnectionError)


def with_retries(fn, label=""):
    """Call `fn`, retrying transient upstream failures with a slow backoff. -> fn()'s result

    This is the only retry policy: the clients have the SDK's retries off, so anything that calls the API
    directly (not through chat()) must wrap the call in this. Timeouts, connection errors, 5xx and 502/503
    retry up to SERVER_RETRIES times, waiting SERVER_BACKOFF x attempt seconds; anything else
    raises at once."""
    for attempt in range(_SERVER_RETRIES + 9):
        try:
            return fn()
        except Exception as e:
            transient = isinstance(e, _TRANSIENT) or "502" in str(e)[:200] or "503" in str(e)[:200]
            # A proxy "No deployments available" (cooldown) 429 can last minutes, longer than the normal
            # backoff, so it gets its own ladder: up to 8 extra attempts, 60s apart.
            cooldown = "No deployments available" in str(e)[:300]
            if cooldown and attempt < _SERVER_RETRIES + 8:
                print(f"  cooldown 429 ({label or 'call'}): waiting 60s (attempt {attempt + 1})", file=sys.stderr)
                time.sleep(60)
                continue
            if not transient or attempt >= _SERVER_RETRIES:
                raise
            wait = _SERVER_BACKOFF * (attempt + 1)
            print(f"  engine: {type(e).__name__} from {label or 'upstream'}; retry {attempt + 1}/"
                  f"{_SERVER_RETRIES} in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)


def user_content(prompt, model, cache_prefix=None):
    """The user message content for `prompt`: the text itself, or, for a Claude model whose prompt starts with
    `cache_prefix`, the same text as two blocks with cache_control on the first. -> str | list"""
    if cache_prefix and _bare(model).startswith("claude") and prompt.startswith(cache_prefix):
        return [{"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": prompt[len(cache_prefix):]}]
    return prompt


def chat(prompt, model, *, max_out=2000, json_mode=False, effort=None, cache_prefix=None):
    """Send `prompt` as one user message to `model`, with retries. -> the reply text ("" if empty)

    Callers that need the request body (for batching) or the finish_reason build kwargs with
    chat_kwargs() and call the client themselves (as aggregation/cluster.py's chat_until_complete does).

    cache_prefix marks a leading slice of `prompt` as an Anthropic cacheable prefix: the same user text is sent
    as two content blocks with cache_control on the first, so a long constant instruction is billed at the
    cached rate after the first call. The model sees the same text, so verdicts don't change. Only applies to
    Claude models (OpenAI caches prefixes automatically) and only if `prompt` starts with the prefix."""
    msgs = [{"role": "user", "content": user_content(prompt, model, cache_prefix)}]
    kw = chat_kwargs(model, max_out, json_mode=json_mode, effort=effort)
    # The timeout scales with max_out: a long reasoning reply takes minutes, and a flat 60s would cut it off
    # mid-generation on every retry. ~25s per 1k output tokens, never below 60s.
    if max_out > 2000:
        kw["timeout"] = max(60.0, 25.0 * max_out / 1000.0)
    # Retries come from with_retries() alone (the SDK's are off, or the two would multiply). Its backoff is
    # tens of seconds because gateways ask for ~30s after a 502, longer than the SDK's own ladder waits.
    r = with_retries(lambda: client(model).chat.completions.create(model=model, messages=msgs, **kw),
                     label=_bare(model))
    record_usage(model, r)
    return r.choices[0].message.content or ""


# ── Content-keyed verdict cache: the same inputs get the same answer across rounds and runs, so a rerun
#    reproduces a build instead of re-sampling a nondeterministic model. ──
_CACHES = {}                                   # section -> {key: value}; filled lazily by _load_section
_cache_lock = threading.Lock()                 # guards _CACHES; judges run in many threads


def _load_section(section):
    """Load a section's cache file on first use. Call holding _cache_lock. -> the section's dict

    Reads from the same path _save_cache() writes, so every section persisted is also read back."""
    if section not in _CACHES:
        p = _section_path(section)
        try:
            _CACHES[section] = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError):          # unreadable/corrupt cache must not kill the run
            _CACHES[section] = {}
    return _CACHES[section]


_FLUSH_EVERY = 250                             # persist every N new entries so an interrupted run keeps its work
_unsaved = [0]


def _union_with_disk(p, data):
    """Merge this process's entries over what is on disk now, ours winning on a collision. -> dict to write

    A plain write would erase entries another process saved since we loaded the file. Keys encode the model,
    prompt and inputs, so the files are append-only and a union is a safe merge."""
    try:
        disk = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):              # corrupt/unreadable disk copy must not lose this run's work
        return data
    if not isinstance(disk, dict) or not disk:
        return data
    disk.update(data)
    return disk


def _save_cache():
    """Write every section in memory to its cache file, merged with what is on disk. -> None"""
    with _cache_lock:                          # atomic write per section under the lock (no truncation on interrupt)
        for section, data in _CACHES.items():
            if data:
                p = _section_path(section)
                p.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(p, _union_with_disk(p, data))


# Save the cache at exit, so any script that calls the judges keeps what it paid for even without an explicit
# save. Only entries memo() accepted are written: a failed call returns NoCache and never reaches _CACHES.
atexit.register(lambda: _save_cache() if _unsaved[0] else None)
atexit.register(lambda: (lambda t: print("\n" + t) if t else None)(usage_report()))


def _pkey(*parts):
    """A short hash of the parts joined. -> 16 hex chars"""
    return sha256_short(" § ".join(parts))


class NoCache:
    """What a compute() returns to say "use this value, but do not cache it".

    Never cache a failed call. Several judges fail open so one flaky call cannot block a build, but caching
    that fallback would make it permanent and indistinguishable from a real verdict. Wrap the fallback in
    NoCache and the next run asks again."""

    def __init__(self, value):
        self.value = value


def memo(section, key, compute, *, only_cached=False):
    """Get-or-compute `key` in cache `section`, saving every _FLUSH_EVERY new entries. -> the value

    A stored value is never None, so None means absent (empty strings still cache). compute() may return
    NoCache(value) to have the value used but not stored. only_cached=True returns None on a miss instead of
    calling compute(), so it never starts a billed call."""
    with _cache_lock:
        hit = _load_section(section).get(key)
    if hit is not None:
        return hit
    if only_cached:
        return None
    val = compute()
    if isinstance(val, NoCache):
        return val.value          # a fallback from a FAILED call: use it, never persist it
    with _cache_lock:
        _load_section(section)[key] = val
        _unsaved[0] += 1
        due = _unsaved[0] >= _FLUSH_EVERY
        if due:
            _unsaved[0] = 0
    if due:
        _save_cache()
    return val


def cached_key(model, tag, sx, sy):
    """The key _cached() uses for a pair. -> str

    Exposed so batch.fill writes under exactly the key the synchronous call reads."""
    return _pkey(*(([] if model == "gpt-4o" else [model]) + [tag]), sx, sy)


def _cached(section, model, tag, sx, sy, compute):
    """memo() for a sorted pair (sx, sy), keyed on (model, tag) so models and prompts never collide. -> value"""
    return memo(section, cached_key(model, tag, sx, sy), compute)
