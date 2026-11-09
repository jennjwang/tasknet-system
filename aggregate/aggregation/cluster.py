"""cluster — draw the occupation's task FAMILIES over a pile of atoms, and apply the source floor.

    build = cluster.seed(ctx)                 # fresh: empty core, every atom in the pile
    build = cluster.propose(ctx, build)       # draw over the pile, append the new families (head-only)
    n_new = len(build["draw_pile_log"]["founded"])

One call sees every atom in the pile at once (prompts/aggregation/cluster/cluster.txt) and returns
`[{"head", "members"}]`; the same partitioner serves the first round of a fresh build (the whole corpus) and
every later round (what refile left). It may leave atoms UNFILED by design — that pile is what the next round
reads. A family publishes only on min_sources distinct sessions; survivors are appended head-only for
synthesize to write and the hearings to judge. Draw variance is real (~15-20% per draw), which is why an
adopted list is amended rather than redrawn.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .. import families
from ..cache import NoCache
from ..engine import bare_model
from ..lib import sha256
from . import blocks

SEP = "␟"


def _ladder(max_out, effort, ceiling=None):
    """The truncation rungs: raise the cap at the configured effort FIRST, degrade effort only last. The raised
    rung never asks for more than `ceiling` (the model's completion-token limit) unless max_out already does."""
    big = max_out * 3 if not ceiling else min(max_out * 3, max(ceiling, max_out))
    return ([(max_out, effort), (big, effort), (big, "low")] if effort != "low"
            else [(max_out, "low"), (big, "low")])


def _ceiling(ctx):
    """models.cluster.max_out_cap: the completion-token limit of the cluster/writer models (gpt-5.4: 128k)."""
    return int(ctx.config.get("models.cluster.max_out_cap", 128000) or 0) or None


def chat(ctx, prompt, max_out, tag, model=None, effort=None, cache_prefix=None):
    """One call, retried up the ladder on truncation. -> (content, the effort that produced it). Shared with
    the writer — the package's only call path that escalates on `finish_reason == "length"`."""
    model = model or ctx.model("cluster")
    effort = effort or ctx.effort("cluster")
    timeout = int(ctx.config.get("models.cluster.timeout_s", 300))
    if cache_prefix and bare_model(model).startswith("claude") and prompt.startswith(cache_prefix):
        content = [{"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": prompt[len(cache_prefix):]}]
    else:
        content = prompt
    rungs = _ladder(max_out, effort, _ceiling(ctx))
    for i, (cap, eff) in enumerate(rungs):
        kw = {**ctx.engine.chat_kwargs(model, cap, effort=eff), "timeout": timeout}
        r = ctx.engine.create(model, [{"role": "user", "content": content}], **kw)
        if r.choices[0].finish_reason != "length":
            return r.choices[0].message.content or "", eff
        nxt = rungs[i + 1] if i + 1 < len(rungs) else None
        print(f"  chat[{tag}]: truncated at cap={cap:,} effort={eff}"
              + (f" — retrying at cap={nxt[0]:,} effort={nxt[1]}" if nxt else " — no retry left"), file=sys.stderr)
    raise RuntimeError(f"{model} hit the output cap ({max_out}) at every effort")


def _items(labels):
    return "\n".join(f"{i}. {lab}" for i, lab in enumerate(labels, 1))


def _parse_groups(content, labels):
    """"<heading> :: 3, 7, 12" lines -> [{"head", "members"}]; one atom, one family."""
    out, seen_heads, claimed, junk = [], set(), set(), []
    for line in (content or "").splitlines():
        line = line.strip().lstrip("-•*").strip()
        if "::" not in line:
            continue
        parts = line.split("::")
        head = parts[0].strip().rstrip(".")
        pos = parts[-1]
        if not head or head in seen_heads:
            continue
        members = []
        for tok in re.split(r"[,;\s]+", pos.strip()):
            tok = tok.strip(".:;()[]").strip()
            if not tok:
                continue
            nums = []
            if tok.isdigit():
                nums = [int(tok)]
            elif (m := re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", tok)):
                a, b = int(m.group(1)), int(m.group(2))
                nums = list(range(a, b + 1)) if a <= b <= a + 200 else []
            if not nums:
                junk.append(tok)
                continue
            for n in nums:
                if 1 <= n <= len(labels) and (lab := labels[n - 1]) not in claimed and lab not in members:
                    members.append(lab)
        if not members:
            continue
        seen_heads.add(head)
        claimed.update(members)
        out.append({"head": head, "members": sorted(members)})
    if junk:
        print(f"  _parse_groups: {len(junk)} unparsed member token(s) discarded: {', '.join(sorted(set(junk))[:8])}",
              file=sys.stderr)
    return out


def group(ctx, task_labels):
    """ONE clustering draw: families AND their members from a single call -> [{"head", "members"}].
    The memo section and key prefix are historical ("rollup_decompose_groups", "DECOMPOSEG") and must stay."""
    model, effort = ctx.model("cluster"), ctx.effort("cluster")
    template = ctx.prompt("cluster", occupation=ctx.occupation)
    labels = sorted(task_labels)
    items = _items(labels)
    cap = max(int(ctx.config.get("models.cluster.max_out_min", 32000)),
              int(ctx.config.get("models.cluster.max_out_per_atom", 120)) * len(labels))
    if _ceiling(ctx):
        cap = min(cap, _ceiling(ctx))   # changes the key only past ~1,070 atoms, where the call could not succeed
    key = sha256(SEP.join(["DECOMPOSEG", model, effort, str(cap), sha256(template), items]))

    def compute():
        content, eff = chat(ctx, template.replace("$items", items), cap, "draw")
        got = _parse_groups(content, labels)
        if eff != effort:
            print(f"  group: DEGRADED to effort={eff}; using it but NOT caching it", file=sys.stderr)
            return NoCache(got)
        return got
    return ctx.cache.memo("rollup_decompose_groups", key, compute)


def group_chunked(ctx, pile):
    """The draw over a pile too large for one call (blocks.cluster_chunk). Chunks are k-means over the atoms'
    embeddings, inside one source-function block when blocking is on; the chunk HEADS are then drawn once more
    and heads grouped together union their members. With the knob unset this is exactly `group`."""
    chunk = blocks.cluster_chunk(ctx)
    if not chunk or len(pile) <= chunk:
        return group(ctx, pile) or []
    meta = ctx.atoms.meta
    by_fn = defaultdict(list)
    for a in pile:
        by_fn[meta.get(a, {}).get("function", "") if blocks.block_by(ctx) == "function" else ""].append(a)
    chunks = [ch for fn in sorted(by_fn) for ch in blocks.kmeans_chunks(ctx, by_fn[fn], chunk)]
    print(f"  group_chunked: {len(pile)} atoms in {len(chunks)} chunk(s) of {min(map(len, chunks))}-{max(map(len, chunks))} "
          "atoms; drawing in parallel", file=sys.stderr, flush=True)
    done = [0]

    def one(ch):
        g = group(ctx, ch) or []
        done[0] += 1
        print(f"  group_chunked: chunk {done[0]}/{len(chunks)} ({len(ch)} atoms) -> {len(g)} group(s)", file=sys.stderr, flush=True)
        return g
    with ThreadPoolExecutor(max_workers=min(len(chunks), ctx.workers_for("cluster"))) as ex:
        first = [g for gs in ex.map(one, chunks) for g in gs]
    if len(first) < 2:
        return first
    by_head = defaultdict(list)
    for g in first:
        by_head[g["head"]].append(g)
    second = group(ctx, sorted(by_head)) or []
    merged, used = [], set()
    for g2 in second:
        parts = [g for h in g2["members"] for g in by_head.get(h, [])]
        if not parts:
            continue
        members = sorted({m for p in parts for m in p["members"]})
        head = g2["head"] if len(parts) > 1 else parts[0]["head"]
        merged.append({"head": head, "members": members})
        used.update(id(p) for p in parts)
    merged += [g for g in first if id(g) not in used]
    print(f"  group_chunked: heads draw unioned {len(first)} -> {len(merged)} group(s)", file=sys.stderr)
    return merged


def admit(groups, sources, min_sources):
    """Apply the source floor -> (kept, thin). THE ONE DEFINITION of the floor."""
    kept, thin = [], []
    for g in groups:
        w = sorted(set().union(*(set(sources[m]) for m in g["members"])))
        rec = {"sid": families.new_sid(), "head": g["head"], "members": g["members"], "sources": w, "prevalence": len(w)}
        (kept if len(w) >= min_sources else thin).append(rec)
    return kept, thin


def seed(ctx) -> dict:
    """A FRESH seed artifact: empty core, every corpus atom in the pile."""
    atoms = ctx.atoms
    return {"meta": {"occupation": ctx.occupation, "code": atoms.code, "title": atoms.title,
                     "model": ctx.model("cluster"), "effort": ctx.effort("cluster"), "min_sources": ctx.min_sources,
                     "n_atoms": len(atoms), "prompt": sha256(ctx.prompt("cluster", occupation="$OCC"))[:12]},
            "core": [], "released": sorted(atoms.tasks)}


def propose(ctx, build) -> dict:
    """Draw families over the build's PILE and append the survivors as head-only families. Consumed atoms leave
    the pile; the rest stay; nothing is written for the new families here. Reconciles with the corpus first:
    atoms ingested since the seed enter the pile, members the corpus no longer has leave their family.
    Mutates and returns build, with `draw_pile_log` for this round and `draw_log` appended."""
    core = build["core"]
    W = ctx.atoms.sources
    corpus = set(W)
    known = {m for f in core for m in f["members"]} | {a for k in families.LEFTOVERS for a in build.get(k) or []}
    new = sorted(corpus - known)
    gone = sorted(known - corpus)
    if gone:
        gs = set(gone)
        for f in core:
            f["members"] = [m for m in f["members"] if m not in gs]
        for k in families.LEFTOVERS:
            if build.get(k):
                build[k] = sorted(set(build[k]) - gs)
        core[:] = [f for f in core if f["members"]]
    if new:
        build["released"] = sorted(set(build.get("released") or []) | set(new))
    build.setdefault("meta", {})["n_atoms"] = len(corpus)
    filed = {m for f in core for m in f["members"]}
    pile = [a for a in families.pile(build) if a not in filed]
    founded = []
    if len(pile) >= 2:
        groups = group_chunked(ctx, pile)
        fams, _thin = admit(groups, {m: set(W.get(m) or ()) for m in pile}, ctx.min_sources)
        consumed = set()
        for f in fams:
            ms = sorted(set(f["members"]) - consumed - filed)
            if len(ms) < 2:
                continue
            core.append({"sid": families.new_sid(), "head": f["head"], "members": ms, "sources": f["sources"],
                         "prevalence": f["prevalence"], "founded_by": "cluster --over-pile",
                         **({"function": blocks.function_of(ctx, ms), "duty": blocks.duty_of(ctx, ms)}
                            if blocks.block_by(ctx) else {})})
            consumed.update(ms)
            founded.append(f["head"])
        for k in families.LEFTOVERS:
            if build.get(k):
                build[k] = sorted(set(build[k]) - consumed)
    families.recount(ctx, core)
    entry = {"pile": len(pile), "founded": founded, "corpus_new": len(new), "corpus_gone": gone}
    build["draw_pile_log"] = entry
    build.setdefault("draw_log", []).append({"round": len(build.get("draw_log") or []) + 1, **entry})
    return build
