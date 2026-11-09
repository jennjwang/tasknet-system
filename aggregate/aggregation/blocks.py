"""blocks — the scale knobs for a large build: FJA-function blocking, member caps, chunked draws (configs.yml
`blocks`). Every knob is OFF by default, so an interview build is byte-identical with none of them:

    blocks.by = function     atoms and families carry a source-function orientation (data / people / things, the
                             atoms' `function` column); overlap pairs, disposer candidate lists, writer siblings and
                             cluster chunks stay inside one block
    blocks.member_cap = 40   the writer, the coverage check, duties and merge_or_separate see at most this many
                             members per family — the most prevalent, then the most central. Stamps and ledgers
                             still key on the FULL member set
    blocks.cluster_chunk     a pile larger than this is drawn in embedding-k-means chunks, then the chunk heads are
                             drawn once more to union families split across chunks
    blocks.refile_scope = publishable   the disposer hears pile atoms only when two or more sources report them
"""
from __future__ import annotations

import math
from collections import Counter


def block_by(ctx) -> str:
    return str(ctx.config.get("blocks.by", "") or "").strip()


def member_cap(ctx) -> int:
    return int(ctx.config.get("blocks.member_cap", 0) or 0)


def cluster_chunk(ctx) -> int:
    return int(ctx.config.get("blocks.cluster_chunk", 0) or 0)


def refile_scope(ctx) -> str:
    return str(ctx.config.get("blocks.refile_scope", "") or "").strip()


def function_of(ctx, members) -> str:
    """The majority source-function of a family's members, or "" when unknown or blocking is off."""
    if block_by(ctx) != "function":
        return ""
    meta = ctx.atoms.meta
    c = Counter(meta.get(m, {}).get("function", "") for m in members)
    c.pop("", None)
    return c.most_common(1)[0][0] if c else ""


def duty_of(ctx, members) -> str:
    meta = ctx.atoms.meta
    c = Counter(meta.get(m, {}).get("duty", "") for m in members)
    c.pop("", None)
    return c.most_common(1)[0][0] if c else ""


def same_block(ctx, fa, fb) -> bool:
    """May two things be compared? Yes unless blocking is on and both carry DIFFERENT known functions."""
    return block_by(ctx) != "function" or not fa or not fb or fa == fb


def cap_members(ctx, atoms, cap=None):
    """The members a judge or writer is SHOWN: all of them below the cap, else the most prevalent and, among
    equals, the most central (cosine to the family's mean vector). -> sorted list. Deterministic."""
    cap = member_cap(ctx) if cap is None else cap
    atoms = sorted(set(atoms))
    if not cap or len(atoms) <= cap:
        return atoms
    import numpy as np
    W = ctx.atoms.sources
    V = ctx.judges.embed(atoms)
    mean = np.mean([V[a] for a in atoms], axis=0)
    mean = mean / (np.linalg.norm(mean) or 1.0)
    score = {a: (len(W.get(a, ())), float(V[a] @ mean)) for a in atoms}
    return sorted(sorted(atoms, key=lambda a: (-score[a][0], -score[a][1]))[:cap])


def kmeans_chunks(ctx, labels, chunk, seed=0):
    """Split `labels` into chunks of about `chunk` by k-means over their embeddings. -> [[label]]."""
    labels = sorted(set(labels))
    if chunk <= 0 or len(labels) <= chunk:
        return [labels]
    import numpy as np
    V = ctx.judges.embed(labels)
    X = np.array([V[s] for s in labels])
    k = math.ceil(len(labels) / chunk)
    rng = np.random.default_rng(seed)
    cent = X[rng.choice(len(labels), size=k, replace=False)]
    assign = np.zeros(len(labels), dtype=int)
    for it in range(25):
        new = (X @ cent.T).argmax(axis=1)
        if np.array_equal(new, assign) and it > 0:
            break
        assign = new
        for j in range(k):
            pts = X[assign == j]
            if len(pts):
                c = pts.mean(axis=0)
                cent[j] = c / (np.linalg.norm(c) or 1.0)
    out = []
    for j in range(k):
        grp = [labels[i] for i in range(len(labels)) if assign[i] == j]
        if not grp:
            continue
        if len(grp) > int(chunk * 1.5) and len(grp) < len(labels):
            out += kmeans_chunks(ctx, grp, chunk, seed + 1)
        else:
            out.append(sorted(grp))
    return out or [labels]


def assign_nearest(ctx, orphans, parts):
    """{orphan: index of the part whose shown members' centroid is nearest}. Empty parts are never chosen."""
    orphans, parts = list(orphans), [list(p) for p in parts]
    if not orphans or not any(parts):
        return {}
    import numpy as np
    V = ctx.judges.embed(sorted(set(orphans) | {m for p in parts for m in p}))
    cents = []
    for p in parts:
        if p:
            c = np.mean([V[m] for m in p], axis=0)
            cents.append(c / (np.linalg.norm(c) or 1.0))
        else:
            cents.append(None)
    out = {}
    for o in orphans:
        best, best_s = None, -2.0
        for i, c in enumerate(cents):
            if c is not None and float(V[o] @ c) > best_s:
                best, best_s = i, float(V[o] @ c)
        if best is not None:
            out[o] = best
    return out


def publishable_pile(ctx, pile, min_prev=2):
    """The pile atoms the disposer should hear: with refile_scope=publishable, those with >= min_prev sources."""
    if refile_scope(ctx) != "publishable":
        return list(pile)
    W = ctx.atoms.sources
    return [a for a in pile if len(W.get(a) or ()) >= min_prev]
