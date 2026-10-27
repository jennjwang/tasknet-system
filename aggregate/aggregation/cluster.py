"""cluster — group the unfiled reports into new task clusters (step 1 of each chain round).

    python -m tasknet.aggregation.cluster --seed build.json          # fresh: empty list, every report in the pool
    python -m tasknet.aggregation.cluster --over-pool build.json     # group the pool, append the new clusters

Terms used across tasknet.aggregation (the paper's):
  report    one distinct task text from processing, with the workers (interview sessions) who reported it
  cluster   a group of reports (its `members`) published as one task statement
  pool      reports in no cluster, kept under the POOL_KEYS keys of the artifact
  round     one pass of the four steps; the chain repeats rounds while clustering finds new clusters
  floor     a cluster publishes only if its reports come from at least MIN_WORKERS different workers

Input:  results/processing/<occ>/3_deduped.csv, and (--over-pool) the build artifact's pool.
Output: the artifact with new clusters appended; each has a head and members but no statement yet
        (synthesize writes it). Reports the model leaves ungrouped stay in the pool for the next round.
"""
import argparse
import csv
import functools
import os
import re
import sys
from collections import defaultdict

from tasknet import engine
from tasknet.aggregation import identity

SEP = "␟"      # separates the fields of a cache key

MODEL = os.environ.get("CLUSTER_MODEL", "gpt-5.4")
EFFORT = os.environ.get("CLUSTER_EFFORT", "medium")
TIMEOUT = int(os.environ.get("CLUSTER_TIMEOUT", "300"))       # read timeout per call, in seconds
MIN_WORKERS = int(os.environ.get("MIN_WORKERS", "2"))         # the publishing floor, used by every stage

# The artifact keys that together hold the pool; every stage copies them into its output (carry). Each holds
# a list of reports, and verify checks that filed reports + these == meta.n_atoms.
POOL_KEYS = ("released", "declined", "dropped_atoms", "thin_members")


def load_reports():
    """Read the processed corpus as reports: one per distinct task_name, with the sessions that reported it.
    -> [{"task", "workers", "prevalence"}], where `workers` are session ids and `prevalence` their count."""
    deduped = engine.results_dir("processing") / "3_deduped.csv"
    if not deduped.exists():
        raise SystemExit(f"{deduped} not found — run the processing pipeline first.")
    sessions = defaultdict(set)
    for r in csv.DictReader(deduped.open()):
        if (t := (r["task_name"] or "").strip()):
            sessions[t].add(r["session_id"])
    return [{"task": t, "workers": sorted(s), "prevalence": len(s)} for t, s in sessions.items()]


def occupation_soc():
    """The occupation's (O*NET code, O*NET title), read from the corpus's first row."""
    with (engine.results_dir("processing") / "3_deduped.csv").open() as fh:
        r = next(csv.DictReader(fh))
    return r["onet_code"], r["onet_title"]


def chat_until_complete(prompt, max_out, tag, model=None, effort=None, cache_prefix=None):
    """Make one model call; if the reply is cut off, retry with a larger output cap, and only as a last resort
    at a lower effort (which can change the answer). -> (reply text, the effort that produced it).
    synthesize uses this for its writer too. With a Claude model, `cache_prefix` (the fixed start of the
    prompt) is sent as a separately cacheable block, which only changes the bill."""
    model, effort = model or MODEL, effort or EFFORT
    content = engine.user_content(prompt, model, cache_prefix)
    rungs = ([(max_out, effort), (max_out * 3, effort), (max_out * 3, "low")] if effort != "low"
             else [(max_out, "low"), (max_out * 3, "low")])
    for i, (cap, eff) in enumerate(rungs):
        kw = {**engine.chat_kwargs(model, cap, effort=eff), "timeout": TIMEOUT}
        r = engine.with_retries(lambda: engine.client(model).chat.completions.create(
            model=model, messages=[{"role": "user", "content": content}], **kw), label=model)
        engine.record_usage(model, r)          # this bypasses engine.chat, so record the cost here
        if r.choices[0].finish_reason != "length":
            return r.choices[0].message.content or "", eff
        nxt = rungs[i + 1] if i + 1 < len(rungs) else None
        print(f"  chat_until_complete[{tag}]: truncated at cap={cap:,} effort={eff}"
              + (f" — retrying at cap={nxt[0]:,} effort={nxt[1]}" if nxt else " — no retry left"),
              file=sys.stderr)
    raise RuntimeError(f"{model} hit the output cap ({max_out}) at every effort")


def _parse_clusters(content, labels):
    """Parse the reply's "<heading> :: 3, 7, 12" lines into [{"head", "members"}]. Numbers index `labels`
    (1-based) and "12-15" ranges are expanded. A report claimed twice stays with its first cluster, a repeated
    heading is skipped, and unparseable member tokens are reported and dropped."""
    out, seen_heads, claimed, junk = [], set(), set(), []
    for line in (content or "").splitlines():
        line = line.strip().lstrip("-•*").strip()
        if "::" not in line:                  # prose, or a heading with no members
            continue
        parts = line.split("::")
        head = parts[0].strip().rstrip(".")
        pos = parts[-1]                       # the member numbers are always after the LAST "::"
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
            elif (m := re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", tok)):     # "12-15" is four reports, not junk
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
        print(f"  _parse_clusters: {len(junk)} unparsed member token(s) discarded: "
              f"{', '.join(sorted(set(junk))[:8])}", file=sys.stderr)
    return out


def draw(task_labels):
    """Group `task_labels` into clusters with one model call (cached). -> [{"head", "members"}].
    The cache section and key prefix ("rollup_decompose_groups", "DECOMPOSEG") are old names; renaming them
    would orphan every cached draw."""
    template = engine.load_prompt("cluster", occupation=engine.occupation())
    labels = sorted(task_labels)
    # the numbered list the prompt expects ("1. ...", "2. ..."); part of the cache key, so keep its format
    items = "\n".join(f"{i}. {lab}" for i, lab in enumerate(labels, 1))
    cap = max(32000, int(os.environ.get("CLUSTER_MAX_OUT", "0")) or 120 * len(labels))
    key = engine.sha256(SEP.join(["DECOMPOSEG", MODEL, EFFORT, str(cap), engine.sha256(template), items]))

    def compute():
        content, eff = chat_until_complete(template.replace("$items", items), cap, "draw")
        got = _parse_clusters(content, labels)
        if eff != EFFORT:
            # a reply that only came back at a lower effort is used but not cached, so the next run
            # gets another chance at the configured effort
            print(f"  draw: DEGRADED to effort={eff}; using it but NOT caching it", file=sys.stderr)
            return engine.NoCache(got)
        return got

    return engine.memo("rollup_decompose_groups", key, compute)


def carry(build):
    """The POOL_KEYS keys present in `build`, for a stage to splat into the artifact it writes. -> dict.
    A stage that forgets this silently drops the pool."""
    return {k: build[k] for k in POOL_KEYS if build.get(k)}


def report_workers():
    """{report: frozenset of worker ids} for the current occupation ($OCCUPATION), read once and cached."""
    return _report_workers(engine.occupation())


@functools.cache
def _report_workers(occ):     # noqa: ARG001 - `occ` only keys the cache; load_reports() reads $OCCUPATION itself
    return {a["task"]: frozenset(a["workers"]) for a in load_reports()}


def recount_workers(fams):
    """Recompute each cluster's `workers` and `prevalence` from its members, in place. -> fams.
    Every stage that changes members calls this, so the counts never go stale."""
    workers = report_workers()
    unmatched = sorted({m for f in fams for m in (f.get("members") or []) if m not in workers})
    if unmatched:
        print(f"  recount_workers: {len(unmatched)} member(s) not found in the report corpus -- prevalence for their "
              f"clusters is UNDERSTATED: {unmatched[0][:70]}", file=sys.stderr)
    for f in fams:
        w = sorted({x for m in (f.get("members") or []) for x in workers.get(m, ())})
        f["workers"], f["prevalence"] = w, len(w)
    return fams


def draw_pool(art):
    """Group the artifact's pool into new clusters and append those that clear the floor (head and members
    only). Reports that join a cluster leave the pool. Mutates `art`. -> a summary {pool, founded, corpus_new,
    corpus_gone}

    First reconciles the artifact with the current corpus: an AMEND build starts from the adopted list, so
    reports ingested since then are added to the pool, and members the corpus no longer has are removed from
    their clusters (the coverage step's floor then demotes any cluster left too thin)."""
    core = art["core"]
    AW = report_workers()
    corpus = set(AW)
    known = {m for f in core for m in f["members"]} | {a for k in POOL_KEYS for a in art.get(k) or []}
    new = sorted(corpus - known)
    gone = sorted(known - corpus)
    if gone:
        gs = set(gone)
        for f in core:
            f["members"] = [m for m in f["members"] if m not in gs]
        for k in POOL_KEYS:
            if art.get(k):
                art[k] = sorted(set(art[k]) - gs)
        core[:] = [f for f in core if f["members"]]
    if new:
        art["released"] = sorted(set(art.get("released") or []) | set(new))
    art.setdefault("meta", {})["n_atoms"] = len(corpus)
    pool = sorted({a for k in POOL_KEYS for a in art.get(k) or []})
    filed = {m for f in core for m in f["members"]}
    pool = [a for a in pool if a not in filed]
    founded = []
    if len(pool) >= 2:                               # a cluster needs at least two reports
        consumed = set()
        for g in draw(pool):
            w = sorted(set().union(*(AW.get(m) or () for m in g["members"])))
            if len(w) < MIN_WORKERS:                # the floor: reports from at least MIN_WORKERS workers
                continue
            ms = sorted(set(g["members"]) - consumed - filed)
            if len(ms) < 2:
                continue
            core.append({"sid": identity.new_cluster_id(), "head": g["head"], "members": ms,
                         "workers": w, "prevalence": len(w), "founded_by": "cluster --over-pool"})
            consumed.update(ms)
            founded.append(g["head"])
        for k in POOL_KEYS:
            if art.get(k):
                art[k] = sorted(set(art[k]) - consumed)
    recount_workers(core)
    return {"pool": len(pool), "founded": founded, "corpus_new": len(new), "corpus_gone": gone}


def main():
    engine.set_stage("cluster")                      # use this stage's own API key, if one is set
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", metavar="OUT", default=None,
                    help="write a FRESH seed artifact: empty core, every corpus report in the pool; the chain's "
                         "first cluster step is then --over-pool on it, same as every later round")
    ap.add_argument("--over-pool", metavar="ART", default=None,
                    help="draw over the POOL of an existing artifact and append the new clusters to it")
    ap.add_argument("--out", default=None, help="output filename (default: the --over-pool artifact itself)")
    args = ap.parse_args()
    if args.seed:
        reports = load_reports()
        soc, title = occupation_soc()
        art = {"meta": {"occupation": engine.occupation(), "soc": soc, "onet_title": title, "model": MODEL,
                        "effort": EFFORT, "min_workers": MIN_WORKERS, "n_atoms": len(reports),
                        "prompt": engine.sha256(engine.load_prompt("cluster", occupation="$OCC"))[:12]},
               "core": [], "released": sorted(a["task"] for a in reports)}
        identity.write_artifact(args.seed, art, indent=1)
        print(f"{engine.occupation()}: seeded {args.seed} with {len(reports)} reports in the pool and no clusters")
        return
    if not args.over_pool:
        raise SystemExit("cluster.py: pass --seed OUT (fresh) or --over-pool ART")
    art = identity.read_artifact(args.over_pool)
    log = draw_pool(art)
    if log["corpus_new"] or log["corpus_gone"]:
        print(f"  reconcile: {log['corpus_new']} corpus report(s) new since the seed -> pool; "
              f"{len(log['corpus_gone'])} member(s) no longer in the corpus -> dropped"
              + (f" (e.g. {log['corpus_gone'][0][:60]!r})" if log["corpus_gone"] else ""))
    print(f"{engine.occupation()}: pool {log['pool']} -> {len(log['founded'])} new cluster"
          f"{'' if len(log['founded']) == 1 else 's'}")
    for h in log["founded"]:
        print(f"  NEW [{h[:90]}]  (statement from synthesize; verdicts from split and overlap)")
    print(f"→ {identity.write_artifact(args.out or args.over_pool, art, indent=1)}")


if __name__ == "__main__":
    main()
