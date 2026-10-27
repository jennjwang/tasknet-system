"""Shared helpers: workspace and prompt paths, prompt loading, atomic writes, hashing, .env loading.

The small utilities both tasknet.processing and tasknet.aggregation use, kept in one place so there is one
prompt loader, one atomic writer and one hashing function.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from string import Template

# {{include:name}} splices another prompt (by bare name) into this one, so shared definitions have one copy.
_INCLUDE = re.compile(r"\{\{\s*include:\s*([A-Za-z0-9_]+)\s*\}\}")

def _workdir() -> Path:
    """Where results/, cache/, data/ and .env live.

    $WORKSPACE if set; else the source checkout this file sits in (the first parent holding pyproject.toml
    and aggregate/); else the current directory, which is what an installed package gets. -> Path"""
    env = os.environ.get("WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    for d in Path(__file__).resolve().parents:
        if (d / "aggregate").is_dir() and (d / "pyproject.toml").is_file():
            return d
    return Path.cwd()


def _prompts_dir() -> Path:
    """The prompt tree, prompts/ next to this file (aggregate/prompts/ in a checkout). -> Path"""
    here = Path(__file__).resolve().parent
    d = here / "prompts"
    if d.is_dir():
        return d
    raise FileNotFoundError(f"no prompts/ directory next to {here}; reinstall tasknet")


REPO_ROOT = _workdir()          # the workspace; named for the checkout it defaults to
PROMPTS_DIR = _prompts_dir()
# Further prompt trees searched after prompts/ (colon-separated), e.g. for prompts kept outside the package.
EXTRA_PROMPT_DIRS = [Path(p) for p in os.environ.get("PROMPTS_EXTRA", "").split(":") if p]


def env_path(name: str, default: Path) -> Path:
    """$name as a path, else `default`. A relative value is taken from the workspace (where .env lives), not
    from the directory the command happens to run in. -> Path"""
    v = os.environ.get(name, "").strip()
    return REPO_ROOT / Path(v).expanduser() if v else default


def interviews_dir() -> Path:
    """Where the raw interview sessions are: $DATA_DIR/interviews, else data/interviews in the workspace.
    One directory per occupation slug, one <uuid>.json per session. -> Path"""
    return env_path("DATA_DIR", REPO_ROOT / "data") / "interviews"


def results_dir(kind: str) -> Path:
    """The output directory for `kind`: results/<kind>/<occupation> when $OCCUPATION is set, else
    results/<kind>. -> Path

    The pipeline handles one occupation at a time and occupations must not be pooled, so each gets its own
    directory. Caches stay shared (they are keyed by content). Simulated-worker runs ($OCCUPATION ending in
    `-sim`) go under results/sim/<kind>/<slug>/ instead, keeping real interview outputs separate.
    """
    occ = os.environ.get("OCCUPATION", "").strip()
    if occ.endswith("-sim"):
        return REPO_ROOT / "results" / "sim" / kind / occ[:-len("-sim")]
    base = REPO_ROOT / "results" / kind
    return base / occ if occ else base


def atomic_write(path, data) -> None:
    """Write `data` to `path` atomically (temp file + os.replace), so a crash never leaves a half-written
    file. A str is written as is; anything else is written as JSON. -> None

    The temp file is removed on any interrupt, Ctrl-C included. Temps left by a kill that could not be caught
    are swept on the next write to the same directory once over an hour old; only this function's own
    prefixed temps are touched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ".atomicwrite-"
    cutoff = time.time() - 3600
    for stale in path.parent.glob(f"{prefix}*.tmp"):   # sweep our own temps orphaned by an earlier kill
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
    except BaseException:                              # incl. KeyboardInterrupt: don't leave a temp
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _resolve_prompt_path(name: str) -> Path:
    """Find the .txt file for a bare prompt name. -> Path

    prompts/<name>.txt wins; otherwise prompts/ (and any PROMPTS_EXTRA trees) are searched
    recursively, and the name must match exactly one file."""
    direct = PROMPTS_DIR / f"{name}.txt"
    if direct.exists():
        return direct
    matches = sorted(PROMPTS_DIR.rglob(f"{name}.txt"))
    for extra in EXTRA_PROMPT_DIRS:
        matches += sorted(extra.rglob(f"{name}.txt"))
    if len(matches) > 1:
        rels = [str(m.relative_to(PROMPTS_DIR)) for m in matches]
        raise ValueError(f"prompt '{name}' is ambiguous across subdirs: {rels}")
    if not matches:
        raise FileNotFoundError(f"prompt '{name}.txt' not found under {PROMPTS_DIR}")
    return matches[0]


def load_prompt(name: str, **kwargs: object) -> str:
    """Load a prompt by bare name, expand its includes and fill its $placeholders from kwargs. -> str

    `{{include:other}}` splices in another prompt, recursively (circular includes raise). Placeholders are
    filled with safe_substitute, so unknown ones are left intact."""
    def expand(nm: str, seen: frozenset) -> str:
        if nm in seen:
            raise ValueError(f"circular prompt include: {nm} (chain: {sorted(seen)})")
        text = _resolve_prompt_path(nm).read_text()
        return _INCLUDE.sub(lambda m: expand(m.group(1), seen | {nm}), text)

    text = expand(name, frozenset())
    return Template(text).safe_substitute(**kwargs) if kwargs else text


def load_env(path) -> None:
    """Load a .env file into os.environ without overriding variables already set. -> None"""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def sha256(text: str) -> str:
    """SHA-256 of `text`. -> 64 hex chars"""
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_short(text: str) -> str:
    """SHA-256 truncated to 16 hex chars."""
    return sha256(text)[:16]


def occupation_meta(occ: str) -> dict:
    """The O*NET-SOC code and title for an occupation slug. -> {occ, soc, title}

    The SOC comes from results/crosswalk/<occ>/scoped.json if it exists, else from matching the slug against
    the slugified O*NET titles. Never raises: without a match it returns an empty SOC and the de-slugged name."""
    soc = ""
    scoped = REPO_ROOT / "results" / "crosswalk" / occ / "scoped.json"
    if scoped.exists():
        try:
            soc = (json.loads(scoped.read_text()).get("soc") or "").strip()
        except Exception:
            soc = ""
    try:
        from tasknet import onet_db  # local: onet_db imports this module, so a top-level import would round
        slug = lambda t: re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t.lower())).strip("-")
        if not soc:
            soc = next((s for s, t in onet_db.socs().items() if slug(t) == occ), "")
        title = onet_db.title(soc) if soc else ""
    except Exception:                                   # no O*NET exports on disk; the slug is still readable
        title = ""
    return {"occ": occ, "soc": soc, "title": title or occ.replace("-", " ").title()}


def strip_json(raw: str) -> str:
    """Strip markdown code fences from a model's JSON reply and keep only the first complete top-level object
    or array. -> str

    Anything after the JSON (prose, a second object) would otherwise make json.loads fail on a good reply.
    Brace matching ignores braces inside strings. If nothing balances (a truncated reply), the text is returned
    unchanged so the caller's parse still fails rather than accepting a trimmed fragment."""
    raw = raw.strip()
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
                return raw                      # unbalanced: hand it back and let the caller raise
            stack.pop()
            if not stack:
                return raw[start:i + 1]         # first complete value; anything after it is noise
    return raw                                   # never balanced (truncated reply) — caller decides
