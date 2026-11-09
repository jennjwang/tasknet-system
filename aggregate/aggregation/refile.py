"""refile — every atom's placement, re-asked: off-home atoms and the pile through the disposer.

    build, report = refile.place(ctx, build)

`excluded` finds the filed atoms their own statement no longer covers (covers judge; off-home) and the atoms
nothing covers (missing, reported). The disposer, shown every published statement, rules on off-home atoms and
the pile alike: FILE moves or keeps the atom (stamps clear so the writer re-gates), UNFILED/LEAVE releases an
off-home atom and leaves a pile atom. An atom leaves its family only when coverage AND the disposer agree, and
the disposer must say the same thing TWICE before anything moves (the second draw is paid only when the first
proposes a change). Never founds a family; the pile it leaves is what the next cluster step draws over. Then
the source floor is applied as a placement rule. filed + pile == n_atoms throughout.
"""
from __future__ import annotations

import collections
import math
import sys
from concurrent.futures import ThreadPoolExecutor

from .. import families
from . import blocks


def cover_pairs(ctx, atoms, labels, owner=None):
    """The (atom, label) pairs the coverage scan will ask about -> [(atom, label)]."""
    atoms, labels = sorted(set(atoms)), sorted(set(labels))
    if not (atoms and labels):
        return []
    topk = int(ctx.config.get("chain.cover_topk", 0))
    k = topk if topk > 0 else max(3, math.ceil(len(labels) / 6))
    V = ctx.judges.embed(atoms + labels)
    owner = owner or {}
    out = []
    for a in atoms:
        ranked = sorted(labels, key=lambda x: -float(V[a] @ V[x]))[:k]
        own = owner.get(a)
        if own and own in labels and own not in ranked:
            ranked.append(own)
        out += [(a, L) for L in ranked]
    return out


def _ask(ctx, jobs):
    """Run covers over [(atom, label)] -> {atom: [covering labels]}."""
    jobs = sorted(set(jobs))
    if len(jobs) > 500:
        print(f"  coverage scan: {len(jobs):,} (atom, label) pairs", file=sys.stderr, flush=True)
    hits = ctx.judges.covers_all(jobs)
    out = collections.defaultdict(list)
    for (a, L), ok in hits.items():
        if ok:
            out[a].append(L)
    return out


def excluded(ctx, core, unfiled=()):
    """-> (missing, off_home) where off_home = {atom: (owner statement, [other covering statements])}."""
    labels = [c["statement"] for c in core]
    owner = {m: c["statement"] for c in core for m in (c.get("members") or [])}
    for a in unfiled:
        owner.setdefault(a, None)
    atoms = sorted(owner)
    own_ok = _ask(ctx, [(a, owner[a]) for a in atoms if owner.get(a) in labels])
    failed = [a for a in atoms if owner[a] not in own_ok.get(a, [])]
    rest = _ask(ctx, [(a, L) for a, L in cover_pairs(ctx, failed, labels, owner) if L != owner.get(a)])
    missing, off_home = [], {}
    for a in failed:
        hits = [L for L in rest.get(a, []) if L != owner.get(a)]
        off_home[a] = (owner.get(a), hits)
        if not hits:
            missing.append(a)
    return missing, off_home


def _home_number(home, tasks):
    for line in tasks.splitlines():
        n, _, rest = line.partition(". ")
        if rest.strip() == (home or "").strip():
            return int(n)
    return None


def _proposes_change(d, home, tasks):
    fate = str(d.get("fate", "")).upper()
    if fate in ("UNFILED", "LEAVE", "RELEASE"):
        return bool(home)
    if fate.startswith("FILE"):
        return d.get("task") != _home_number(home, tasks)
    return False


def _try_dispose(ctx, atom, tasks, home=None, siblings=()):
    """Concurrence on a structural change: a move or a release needs two agreeing draws."""
    try:
        first = ctx.judges.dispose(atom, tasks, home, siblings, draw=1)
    except Exception as e:                                # noqa: BLE001
        return {"fate": "ERR", "why": str(e)[:60]}
    if not _proposes_change(first, home, tasks):
        return first
    try:
        second = ctx.judges.dispose(atom, tasks, home, siblings, draw=2)
    except Exception as e:                                # noqa: BLE001
        return {"fate": "ERR", "why": f"second draw: {str(e)[:44]}"}
    if (str(first.get("fate", "")).upper() == str(second.get("fate", "")).upper() and first.get("task") == second.get("task")):
        return first
    return {"fate": "FILE", "task": _home_number(home, tasks), "why": "no-concurrence: draws disagreed"} \
        if home else {"fate": "LEAVE", "why": "no-concurrence: draws disagreed"}


def place(ctx, build):
    """Every atom's placement, re-asked. Mutates and returns (build, report)."""
    core = build["core"]
    for f in core:
        f.setdefault("statement", f.get("head"))
    filed = {m for f in core for m in f["members"]}
    pile = sorted(set(families.pile(build)) - filed)
    log = {"pile": len(pile), "off_home": 0, "filed": 0, "moved": 0, "released": 0, "fates": {}}
    report = {"missing": [], "off_home": {}}
    if not core:
        build["refile_log"] = log
        return build, report
    heard = blocks.publishable_pile(ctx, pile)
    if len(heard) != len(pile):
        print(f"  scope: {len(heard)} of {len(pile)} pile atoms have >= 2 sources and are heard")
    missing, off_home = excluded(ctx, core, heard)
    report = {"missing": missing, "off_home": {a: {"owner": o, "covered_by": h} for a, (o, h) in off_home.items()}}
    log["off_home"] = sum(1 for a, (own, _) in off_home.items() if own)
    log["pile_heard"] = len(heard)
    owner = {m: i for i, f in enumerate(core) for m in f["members"]}
    ask = sorted(set(heard) | {a for a, (own, _) in off_home.items() if own})
    tasks_all = "\n".join(f"{i + 1}. {f['statement']}" for i, f in enumerate(core))
    fn_fam = [blocks.function_of(ctx, f["members"]) for f in core] if blocks.block_by(ctx) else None
    meta = ctx.atoms.meta if blocks.block_by(ctx) else {}

    def tasks_for(a):
        if not fn_fam:
            return tasks_all
        fa = meta.get(a, {}).get("function", "")
        keep = [i for i, f in enumerate(core) if blocks.same_block(ctx, fa, fn_fam[i]) or owner.get(a) == i]
        return "\n".join(f"{i + 1}. {core[i]['statement']}" for i in keep)
    items = [(a, tasks_for(a), core[owner[a]]["statement"] if a in owner else None,
              core[owner[a]]["members"] if a in owner else ()) for a in ask]
    if ctx.config.get("batch.items") and items:
        n1 = ctx.judges.dispose_many(items, draw=1)
        need2 = []
        for a, tasks, home, sibs in items:
            try:
                if _proposes_change(ctx.judges.dispose(a, tasks, home, sibs, draw=1), home, tasks):
                    need2.append((a, tasks, home, sibs))
            except Exception:                             # noqa: BLE001
                pass
        n2 = ctx.judges.dispose_many(need2, draw=2) if need2 else 0
        print(f"  disposer batched: {n1} first-draw + {n2} second-draw verdicts prefilled over {len(items)} atoms", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=ctx.workers_for("refile")) as ex:
        fates = dict(zip(ask, ex.map(lambda it: _try_dispose(ctx, *it), items)))
    consumed, released = set(), []
    for atom, v in fates.items():
        log["fates"][atom] = v.get("fate")
        t = v.get("task")
        home = owner.get(atom)
        if v.get("fate") == "FILE" and isinstance(t, int) and 1 <= t <= len(core):
            if home == t - 1:
                continue
            if home is not None:
                core[home]["members"] = sorted(set(core[home]["members"]) - {atom})
                core[home].pop("written_for", None)
                log["moved"] += 1
            else:
                log["filed"] += 1
            core[t - 1]["members"] = sorted(set(core[t - 1]["members"]) | {atom})
            core[t - 1].pop("written_for", None)
            consumed.add(atom)
        elif home is not None and v.get("fate") in ("UNFILED", "LEAVE"):
            core[home]["members"] = sorted(set(core[home]["members"]) - {atom})
            core[home].pop("written_for", None)
            released.append(atom)
    for k in families.LEFTOVERS:
        if build.get(k):
            build[k] = sorted(set(build[k]) - consumed)
    if released:
        build["released"] = sorted(set(build.get("released") or []) | set(released))
        log["released"] = len(released)
    W = ctx.atoms.sources
    keep, demoted = [], []
    for f in core:
        if not f["members"]:
            continue
        if len({w for m in f["members"] for w in (W.get(m) or ())}) >= ctx.min_sources:
            keep.append(f)
        else:
            demoted.append(f)
    if demoted:
        build["released"] = sorted(set(build.get("released") or []) | {m for f in demoted for m in f["members"]})
        log["demoted"] = [f.get("statement") or f.get("head") for f in demoted]
    build["core"] = keep
    families.recount(ctx, build["core"])
    build["refile_log"] = log
    print(f"  REFILED: pile {log['pile']} + off-home {log['off_home']} -> filed {log['filed']}, moved {log['moved']}, "
          f"released {log['released']}, demoted {len(log.get('demoted') or [])}")
    return build, report
