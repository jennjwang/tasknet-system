"""verify — the between-stage checks, so a silent defect stops the rebuild instead of reaching the list.

    problems = verify.invariants(ctx, build)      # [] when clean; no model calls, milliseconds

  atoms       filed + pile == n_atoms, and no member filed under two families
  ids         every family carries a unique sid
  duplicates  no statement text twice
  corpus      every filed member is a real atom; prevalence equals the distinct sources behind the members;
              every family clears the floor
  fusion      (report-only) every published statement has a duties verdict for its CURRENT members
"""
from __future__ import annotations

from .. import families


def _core(art):
    return art.get("core") or []


def _stmt(f):
    return f.get("statement") or f.get("head") or ""


def check(art, sources=None, min_sources=2):
    """-> [failure strings]. `sources` is the atom -> sessions map; None skips the corpus check."""
    bad, core = [], _core(art)
    filed = [m for f in core for m in (f.get("members") or [])]
    pile = [a for k in families.LEFTOVERS for a in (art.get(k) or ())]
    n = (art.get("meta") or {}).get("n_atoms")
    if n is not None and len(set(filed)) + len(set(pile)) != n:
        bad.append(f"atoms: filed {len(set(filed))} + pile {len(set(pile))} = {len(set(filed)) + len(set(pile))}, expected n_atoms {n}")
    if len(filed) != len(set(filed)):
        bad.append(f"atoms: {len(filed) - len(set(filed))} member(s) filed under more than one family")
    sids = [f.get("sid") for f in core]
    if any(not x for x in sids):
        bad.append(f"sid: {sum(1 for x in sids if not x)} statement(s) carry no id")
    dup = len(sids) - len(set(x for x in sids if x))
    if dup > 0 and all(sids):
        bad.append(f"sid: {dup} duplicate id(s) — two families share one identity")
    seen = set()
    for f in core:
        s = _stmt(f)
        if s in seen:
            bad.append(f"duplicates: {s[:70]!r} appears twice")
        seen.add(s)
    if sources is not None:
        for f in core:
            mem = f.get("members") or []
            unknown = [m for m in mem if m not in sources]
            if unknown:
                bad.append(f"corpus: {len(unknown)} member(s) of {_stmt(f)[:50]!r} are not atoms")
                break
            real = len({w for m in mem for w in sources.get(m, ())})
            if f.get("prevalence") is not None and f["prevalence"] != real:
                bad.append(f"prevalence: {_stmt(f)[:50]!r} says {f['prevalence']}, members give {real}")
            if real < min_sources:
                bad.append(f"floor: {_stmt(f)[:50]!r} rests on {real} source(s), floor is {min_sources}")
    return bad


def fusion_verdicts(ctx, art):
    """Published statements with no duties verdict for their CURRENT members -> [strings]. Cache only."""
    bad = []
    for f in _core(art):
        stmt, mem = _stmt(f), (f.get("members") or [])
        if ctx.cache.get("one_duty", ctx.judges.duties_key(stmt, mem)) is None:
            bad.append(f"fusion: {stmt[:60]!r} ({len(mem)} atoms) has no duties verdict for its members")
    return bad


def siblings(arts):
    """Artifacts from ONE stage invocation must cover the same families -> [failure strings]."""
    if len(arts) < 2:
        return []
    sizes = {name: len(_core(a)) for name, a in arts}
    return [f"siblings: family counts disagree — {sizes}"] if len(set(sizes.values())) > 1 else []


def invariants(ctx, build):
    """Every structural check against the context's atoms -> [failure strings] ([] = ok)."""
    return check(build, ctx.atoms.sources if ctx.atoms is not None else None, ctx.min_sources)


def summary(build) -> str:
    core = _core(build)
    filed = len({m for f in core for m in (f.get("members") or [])})
    pile = len(set(families.pile(build)))
    return f"{len(core)} families   {filed} filed + {pile} pile = {filed + pile}"
