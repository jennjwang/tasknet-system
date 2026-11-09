"""Engine — the model client and the chat wrapper. Pure infrastructure, no pipeline logic.

    Engine(cfg)                                   # keys from the environment, routed by model name (below)
    Engine(cfg, client=openai.OpenAI(...))        # one OpenAI-compatible client for every model
    Engine(cfg, router=lambda model: client)      # your own routing
    eng.chat(prompt, model, max_out=700, json_mode=True)
    eng.create(model, messages, **kw)             # the raw response, for finish_reason / logprobs
    eng.usage_report()

The client contract is the OpenAI SDK surface the package uses: `.chat.completions.create(**kw)` and
`.embeddings.create(model=, input=)`; the Batch API prefill additionally wants `.files`, `.batches` and
`.models`, and degrades to live calls without them. Default routing (the keys in .env.example): claude-* models
go to an OpenAI-compatible gateway that serves them (CLAUDE_API_KEY at CLAUDE_BASE_URL, required); every other
model and the embeddings go to OPENAI_API_KEY at OPENAI_BASE_URL, else api.openai.com. LLM_API_KEY and
LLM_BASE_URL, if set, send both routes to one endpoint (e.g. a LiteLLM proxy). Clients are built lazily, so an
Engine needs no key until the first call. SDK retries are OFF: `with_retries` is the one
retry policy, on the provider's timescale.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import openai

def bare_model(model) -> str:
    """The model name with any provider/route prefix stripped — "openai/gpt-5.4" -> "gpt-5.4"."""
    return str(model or "").rsplit("/", 1)[-1]


def is_reasoning(model) -> bool:
    """Models that reject `temperature` and meter output via `max_completion_tokens`: gpt-5 / o-series, Claude."""
    return bool(model) and bare_model(model).startswith(("gpt-5", "o1", "o3", "claude"))


class BudgetExceeded(BaseException):
    """The run's estimated spend reached config budget_usd. A BaseException, like KeyboardInterrupt, so the judges'
    fail-open `except Exception` handlers cannot swallow it: the run stops, and the caller's cleanup still runs."""


def _env(name) -> str:
    return (os.environ.get(name) or "").strip()


def _default_router(model):
    """One client per route, from the environment (.env.example): the claude-* gateway or the OpenAI side."""
    if bool(model) and bare_model(model).startswith("claude"):
        key = _env("LLM_API_KEY") or _env("CLAUDE_API_KEY")
        url = _env("LLM_BASE_URL") or _env("CLAUDE_BASE_URL")
        if not key:
            raise RuntimeError(f"no CLAUDE_API_KEY (or LLM_API_KEY) for {model}; or pass Engine(client=...)")
        if not url:
            raise RuntimeError(f"{model} needs an OpenAI-compatible endpoint that serves Claude models: set "
                               "CLAUDE_BASE_URL (or LLM_BASE_URL, to send every call there)")
        print(f"  engine: Claude calls via {url}", file=sys.stderr)
        return openai.OpenAI(api_key=key, timeout=60.0, max_retries=0, base_url=url)
    key = _env("LLM_API_KEY") or _env("OPENAI_API_KEY")
    url = _env("LLM_BASE_URL") or _env("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    if not key:
        raise RuntimeError("no OPENAI_API_KEY (or LLM_API_KEY) available; or pass Engine(client=...)")
    print(f"  engine: OpenAI-side calls via {url}", file=sys.stderr)
    return openai.OpenAI(api_key=key, timeout=60.0, max_retries=0, base_url=url)


try:
    _TRANSIENT = (openai.InternalServerError, openai.APITimeoutError, openai.APIConnectionError)
except AttributeError:                                    # older sdk surface
    _TRANSIENT = (openai.APIError,)


class Engine:
    def __init__(self, config, *, client=None, router=None):
        if client is not None and router is not None:
            raise ValueError("pass client= or router=, not both")
        self.config = config
        self._client = client
        self._router = router or _default_router
        self._clients: dict[str, object] = {}
        self._client_lock = threading.Lock()
        self.usage: dict[str, dict] = {}
        self._usage_lock = threading.Lock()
        self._warm_state: dict = {}
        self._warm_lock = threading.Lock()

    # ── clients ───────────────────────────────────────────────────────────────────────────────────
    def client(self, model=None):
        """The client for `model` (what every call site uses); a bare client() is the OpenAI-side one."""
        if self._client is not None:
            return self._client
        route = "claude" if bare_model(model).startswith("claude") else "openai"
        with self._client_lock:
            if route not in self._clients:
                self._clients[route] = self._router(model)
            return self._clients[route]

    def has_batch_api(self, model=None) -> bool:
        c = self.client(model)
        return all(hasattr(c, a) for a in ("batches", "files", "models"))

    # ── requests ──────────────────────────────────────────────────────────────────────────────────
    def chat_kwargs(self, model, max_out, *, json_mode=False, effort=None, logprobs=False) -> dict:
        """The chat.completions kwargs (minus model/messages): reasoning models meter output via
        max_completion_tokens and reject temperature; reasoning_effort is gpt-5/o-series-only; response_format
        is omitted for Claude (the gateway returns {} with it)."""
        kw = ({"max_completion_tokens": max_out} if is_reasoning(model)
              else {"temperature": 0, "max_tokens": max_out})
        if effort and bare_model(model).startswith(("gpt-5", "o1", "o3")):
            kw["reasoning_effort"] = effort
        if json_mode and not bare_model(model).startswith("claude"):
            kw["response_format"] = {"type": "json_object"}
        if logprobs:
            kw["logprobs"] = True
        kw["seed"] = int(self.config.get("seed", 0) or 0)
        return kw

    def with_retries(self, fn, label=""):
        """Call `fn`, retrying transient upstream failures (5xx, timeouts, connection drops) with a linear
        backoff, and proxy cooldown 429s on their own patient ladder. -> fn()'s result."""
        attempts = int(self.config.get("retries.attempts", 4))
        backoff = float(self.config.get("retries.backoff_s", 30))
        for attempt in range(attempts + 9):
            try:
                return fn()
            except Exception as e:                        # noqa: BLE001
                head = str(e)[:300]
                transient = isinstance(e, _TRANSIENT) or "502" in head[:200] or "503" in head[:200]
                cooldown = "No deployments available" in head
                if cooldown and attempt < attempts + 8:
                    print(f"  cooldown 429 ({label or 'call'}): waiting 60s (attempt {attempt + 1})", file=sys.stderr)
                    time.sleep(60)
                    continue
                if not transient or attempt >= attempts:
                    raise
                wait = backoff * (attempt + 1)
                print(f"  engine: {type(e).__name__} from {label or 'upstream'}; retry {attempt + 1}/{attempts} "
                      f"in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)

    def create(self, model, messages, **kw):
        """One chat.completions call through the retry ladder, usage recorded. -> the raw response. Raises
        BudgetExceeded before the call once the estimated spend has reached budget_usd."""
        self.check_budget()
        r = self.with_retries(lambda: self.client(model).chat.completions.create(model=model, messages=messages, **kw),
                              label=bare_model(model))
        self.record_usage(model, r)
        return r

    def chat(self, prompt, model, *, system=None, max_out=2000, json_mode=False, effort=None, cache_prefix=None) -> str:
        """One call -> the content string ("" if empty). `cache_prefix` marks a leading slice of `prompt` as an
        Anthropic cacheable prefix (two adjacent text blocks, cache_control on the first); a no-op unless the
        model is Claude and the prefix is a prefix of the prompt. The timeout scales with max_out."""
        if cache_prefix and bare_model(model).startswith("claude") and prompt.startswith(cache_prefix):
            content = [{"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
                       {"type": "text", "text": prompt[len(cache_prefix):]}]
        else:
            content = prompt
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        kw = self.chat_kwargs(model, max_out, json_mode=json_mode, effort=effort)
        if max_out > 2000:
            kw["timeout"] = max(60.0, 25.0 * max_out / 1000.0)
        r = self.create(model, msgs, **kw)
        return r.choices[0].message.content or ""

    def prewarm(self, static, model, *, as_system=False):
        """Populate the provider's prefix cache for `static` once per process, before concurrent callers
        arrive: the first arrival fires one tiny call and the others wait on its Event."""
        pw = self.config.get("prewarm", {}) or {}
        if not static or not pw.get("on", True):
            return
        key = (static, bare_model(model), as_system)
        with self._warm_lock:
            ev = self._warm_state.get(key)
            mine = ev is None
            if mine:
                ev = self._warm_state[key] = threading.Event()
        if not mine:
            ev.wait(timeout=float(pw.get("wait_s", 45)))
            return
        try:
            cap = int(pw.get("max_out", 64))
            if as_system:
                self.chat("Reply with the single character X.", model, system=static, max_out=cap)
            else:
                self.chat(static + "\n\nReply with the single character X.", model, max_out=cap)
        except Exception:                                 # noqa: BLE001 - never load-bearing
            pass
        finally:
            ev.set()

    # ── accounting ────────────────────────────────────────────────────────────────────────────────
    def record_usage(self, model, resp) -> None:
        """Accumulate one response's token counts under `model`. Never raises."""
        try:
            u = getattr(resp, "usage", None)
            if u is None:
                return
            det = getattr(u, "prompt_tokens_details", None)
            cached = int(getattr(det, "cached_tokens", 0) or 0) if det is not None else 0
            with self._usage_lock:
                e = self.usage.setdefault(bare_model(model), {"calls": 0, "in": 0, "out": 0, "cached": 0})
                e["calls"] += 1
                e["in"] += int(getattr(u, "prompt_tokens", 0) or 0)
                e["out"] += int(getattr(u, "completion_tokens", 0) or 0)
                e["cached"] += cached
        except Exception:                                 # noqa: BLE001
            pass

    def _cost(self, model, e) -> float:
        i_rate, o_rate = (self.config.get("prices", {}) or {}).get(model, (0.0, 0.0))
        billed = max(e["in"] - e["cached"], 0)
        return (billed * i_rate + e["cached"] * i_rate * 0.5 + e["out"] * o_rate) / 1_000_000

    def spent(self) -> float:
        """The run's estimated spend so far, in USD (configs.yml prices)."""
        with self._usage_lock:
            return sum(self._cost(m, e) for m, e in self.usage.items())

    def check_budget(self) -> None:
        """Raise BudgetExceeded once the estimated spend reaches budget_usd (0 or unset: no cap). Calls already in
        flight finish, so a run can end a little past the cap."""
        cap = float(self.config.get("budget_usd", 0) or 0)
        if cap > 0 and (s := self.spent()) >= cap:
            raise BudgetExceeded(f"estimated spend ${s:.2f} reached the budget of ${cap:.2f}")

    def usage_report(self) -> str:
        """The run's token usage as a printable table; "" if nothing was called."""
        with self._usage_lock:
            rows = sorted(self.usage.items(), key=lambda kv: -kv[1]["in"])
        if not rows:
            return ""
        lines = ["  usage        calls        in        out     cached   cache%   est $"]
        tot = 0.0
        for m, e in rows:
            cost = self._cost(m, e)
            tot += cost
            pct = (100.0 * e["cached"] / e["in"]) if e["in"] else 0.0
            lines.append(f"  {m:<12} {e['calls']:6,} {e['in']:9,} {e['out']:10,} {e['cached']:10,} {pct:6.1f}%  {cost:7.3f}")
        lines.append(f"  {'TOTAL':<12} {sum(e['calls'] for _, e in rows):6,} {sum(e['in'] for _, e in rows):9,} "
                     f"{sum(e['out'] for _, e in rows):10,} {sum(e['cached'] for _, e in rows):10,} {'':7} {tot:7.3f}"
                     "   (rates are ESTIMATES; see configs.yml prices)")
        return "\n".join(lines)

    def calls(self) -> int:
        with self._usage_lock:
            return sum(e["calls"] for e in self.usage.values())

    def usage_log(self, path, **tags) -> None:
        """Append this process's usage as one JSON line to `path`, so a multi-stage run can be priced later."""
        try:
            with self._usage_lock:
                rows = {m: dict(e) for m, e in self.usage.items()}
            if not rows:
                return
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **tags, "usage": rows,
                   "est_usd": round(sum(self._cost(m, e) for m, e in rows.items()), 4)}
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except Exception:                                 # noqa: BLE001
            pass
