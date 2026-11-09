"""families — one family identity, everywhere, and the atom accounting every stage must forward.

A family is its MEMBER SET. `key` is the ledger/stamp identity every stage uses (defuse ledger, strain,
continuity stamps); `same` is the 60% mutual-overlap test that lets an adopted statement survive a rewording or
one filed atom; `registry` / `match` look an adopted statement up for a member set. `new_sid` mints a statement's
permanent id (random, never derived: a derived id changes with what it derives from). `recount` re-derives
sources and prevalence from members; `leftovers` is what a stage must splat into its output or lose atoms.
"""
from __future__ import annotations

import secrets
import sys

from .lib import sha256

SAME_FAMILY = 0.6      # two member-sets are the same family at >= 60% of the larger side
# The ATOM accounting: every name holds atoms, cluster subtracts consumed ones, verify sums them. draw_log holds
# no atoms and must stay out of LEFTOVERS; CARRIED is what a stage forwards.
LEFTOVERS = ("released", "declined", "dropped_atoms", "thin_members")
CARRIED = LEFTOVERS + ("draw_log",)


def new_sid() -> str:
    return "s_" + secrets.token_hex(4)


def key(members) -> str:
    """Stable identity for a family across runs: the hash of its sorted member set."""
    return sha256("\n".join(sorted(members)))


def registry(artifact):
    """Adopted statements keyed for continuity lookup -> (exact: {key: stmt}, fuzzy: [(member_set, stmt)])."""
    fams = artifact.get("core") or []
    exact = {key(f["members"]): (f.get("statement") or f.get("head")) for f in fams if f.get("members")}
    fuzzy = [(set(f["members"]), f.get("statement") or f.get("head")) for f in fams if f.get("members")]
    return exact, fuzzy


def same(members, other, bar=SAME_FAMILY) -> bool:
    ms, os_ = set(members), set(other)
    return bool(ms) and len(ms & os_) / max(len(ms), len(os_)) >= bar


def match(members, reg):
    """The adopted statement for this member set, or None: exact first, else the best candidate sharing >= 60%
    of the LARGER side's members."""
    if not reg:
        return None
    exact, fuzzy = reg
    hit = exact.get(key(members))
    if hit:
        return hit
    ms = set(members)
    best, best_r = None, 0.0
    for cand, stmt in fuzzy:
        r = len(ms & cand) / max(len(ms), len(cand), 1)
        if r > best_r:
            best, best_r = stmt, r
    return best if best_r >= SAME_FAMILY else None


def leftovers(build) -> dict:
    """The carried keys present in `build`, for a stage to splat into its own output."""
    return {k: build[k] for k in CARRIED if build.get(k)}


def pile(build) -> list[str]:
    """Every atom in the pile (the union of the leftover keys), sorted."""
    return sorted({a for k in LEFTOVERS for a in (build.get(k) or [])})


def recount(ctx, fams):
    """Recompute `sources` and `prevalence` FROM `members`, in place, and re-derive the block stamps. -> fams."""
    from .aggregation import blocks
    W = ctx.atoms.sources
    unmatched = sorted({m for f in fams for m in (f.get("members") or []) if m not in W})
    if unmatched:
        print(f"  recount: {len(unmatched)} member(s) not found in the atom corpus -- prevalence for their "
              f"families is UNDERSTATED: {unmatched[0][:70]}", file=sys.stderr)
    for f in fams:
        w = sorted({x for m in (f.get("members") or []) for x in W.get(m, ())})
        f["sources"], f["prevalence"] = w, len(w)
        if blocks.block_by(ctx):
            f["function"] = blocks.function_of(ctx, f.get("members") or [])
            f["duty"] = blocks.duty_of(ctx, f.get("members") or [])
    return fams
