"""Model clients from the TaskNet System's environment variables, handed to aggregate as its `router`.

aggregate's own default reads ONET_CLAUDE_API_KEY / ONET_CLAUDE_BASE_URL and falls back to a project gateway
for claude-* models. The TaskNet System keeps the names its .env.example documents and never sends a call to an
endpoint the user did not name:

  LLM_API_KEY + LLM_BASE_URL       one endpoint (e.g. a LiteLLM proxy) for every model
  OPENAI_API_KEY [+ OPENAI_BASE_URL]   gpt-* models, embeddings and the Batch API (default api.openai.com)
  CLAUDE_API_KEY + CLAUDE_BASE_URL     claude-* models, through an OpenAI-compatible gateway (no default)

LLM_API_KEY, when set, is used for both routes; LLM_BASE_URL, when set, sends both routes to one endpoint.
"""
from __future__ import annotations

import os
import sys

import openai


def _is_claude(model) -> bool:
    return str(model or "").rsplit("/", 1)[-1].startswith("claude")


def router(model):
    """One OpenAI-compatible client for `model`'s route (aggregate builds one per route and reuses it)."""
    shared = os.environ.get("LLM_BASE_URL")
    if _is_claude(model):
        key = os.environ.get("LLM_API_KEY") or os.environ.get("CLAUDE_API_KEY")
        name = "LLM_API_KEY" if os.environ.get("LLM_API_KEY") else "CLAUDE_API_KEY"
        url = shared or os.environ.get("CLAUDE_BASE_URL")
        if not key:
            raise RuntimeError(f"{model} needs CLAUDE_API_KEY (or LLM_API_KEY) in .env")
        if not url:
            raise RuntimeError(f"{model} needs an OpenAI-compatible endpoint that serves Claude models: set "
                               "CLAUDE_BASE_URL (or LLM_BASE_URL, to send every call there) in .env")
    else:
        key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        name = "LLM_API_KEY" if os.environ.get("LLM_API_KEY") else "OPENAI_API_KEY"
        url = shared or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if not key:
            raise RuntimeError(f"no OPENAI_API_KEY (or LLM_API_KEY) for {model or 'the OpenAI API'}: set it in .env")
    print(f"  engine: {'Claude' if _is_claude(model) else 'OpenAI-side'} calls using {name} via {url}", file=sys.stderr)
    return openai.OpenAI(api_key=key, base_url=url, timeout=60.0, max_retries=0)
