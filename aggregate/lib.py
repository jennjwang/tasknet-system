"""Pure helpers shared across the package: hashing, atomic writes, JSON extraction, .env loading."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_short(text: str) -> str:
    """SHA-256 truncated to 16 hex chars."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def stmt_key(s: str) -> str:
    """Stable 12-hex key for a TASK STATEMENT, so annotations stay attached across rebuilds. FROZEN: lowercased
    and stripped of punctuation, so re-punctuating a statement does not orphan what hangs on it."""
    return sha256(re.sub(r"[^a-z0-9 ]", "", s.lower()).strip())[:12]


def slugify(title: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def atomic_write(path, data) -> None:
    """Write `data` to `path` atomically (temp file + os.replace). `str` is written raw; anything else is
    JSON-dumped. The temp is removed on any interrupt; temps orphaned by a SIGKILL are swept on the next write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ".atomicwrite-"
    cutoff = time.time() - 3600
    for stale in path.parent.glob(f"{prefix}*.tmp"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def load_env(path) -> None:
    """Load a .env file into os.environ without overriding already-set variables."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def strip_json(raw: str) -> str:
    """Strip markdown fences from an LLM reply and keep only the FIRST complete top-level object or array.
    String-aware brace matching; the text comes back unchanged when nothing balances, so a truncated reply
    still raises in the caller instead of being trimmed to something that parses."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json\n"):
            raw = raw[5:]
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    start = next((i for i, ch in enumerate(raw) if ch in "{["), None)
    if start is None:
        return raw
    opens, closes = {"{": "}", "[": "]"}, {"}": "{", "]": "["}
    stack, in_str, esc = [], False, False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in opens:
            stack.append(ch)
        elif ch in closes:
            if not stack or stack[-1] != closes[ch]:
                return raw
            stack.pop()
            if not stack:
                return raw[start:i + 1]
    return raw
