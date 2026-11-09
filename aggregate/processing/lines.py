"""lines — one free-text entry -> the task lines it actually holds."""
from __future__ import annotations

import re

_MARKER = re.compile(r"^\s*(?:[-•*·▪●–—]|\(?\d{1,2}[.)]|[a-z][.)])\s+")   # "- x", "• x", "1. x", "a) x"


def lines(text: str) -> list[str]:
    """A string that holds a bulleted or numbered list -> its items, never empty. Deterministic and marker-only:
    a line is a task if it has two or more words once its bullet or number is stripped; a bare header line
    ending in ':' is dropped. A string with no newline comes back as itself."""
    ls = [ln.strip() for ln in text.splitlines()]
    if len([ln for ln in ls if ln]) <= 1:
        return [text.strip()]
    out = []
    for ln in ls:
        ln = _MARKER.sub("", ln).strip()
        if len(ln.split()) < 2 or ln.endswith(":"):
            continue
        out.append(ln)
    return out or [" ".join(text.split())]
