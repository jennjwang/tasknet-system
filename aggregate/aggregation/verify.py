"""verify — check a list artifact's invariants, so a silent defect is caught before it reaches the list.

    python -m tasknet.aggregation.verify build.json [final.json ...]

No model calls. Prints "ok" per artifact, or a FAIL line per problem and exits 1. The chain runs it after the
seed and after every round. Each check has caught a real defect that looked like plausible output:

  reports       filed + pool == meta.n_atoms, and no report filed twice.
  sid         every statement has an id, and no two share one.
  duplicates  no statement text twice (a half-applied merge leaves both copies).
  corpus      every member is a real report (one that is not counts as zero workers).
  prevalence  each cluster's `prevalence` equals the distinct workers behind its members.
  floor       each cluster rests on at least MIN_WORKERS workers.
"""
import json
import os
import sys
from pathlib import Path

from tasknet import engine
from tasknet.aggregation import cluster

MIN_WORKERS = cluster.MIN_WORKERS
POOL_KEYS = cluster.POOL_KEYS


def _stmt(f):
    """A cluster's statement, or its head if it has none yet."""
    return f.get("statement") or f.get("head") or ""


def check_invariants(art, workers=None):
    """Run every check on one artifact. -> [failure strings]
    `workers` maps report -> worker ids; None (no corpus available) skips the corpus, prevalence and floor
    checks."""
    bad, core = [], art.get("core") or []
    filed = [m for f in core for m in (f.get("members") or [])]
    pool = [a for k in POOL_KEYS for a in (art.get(k) or ())]
    n = (art.get("meta") or {}).get("n_atoms")

    if n is not None and len(set(filed)) + len(set(pool)) != n:
        bad.append(f"reports: filed {len(set(filed))} + pool {len(set(pool))} = "
                   f"{len(set(filed)) + len(set(pool))}, expected n_atoms {n}")
    if len(filed) != len(set(filed)):
        dupes = len(filed) - len(set(filed))
        bad.append(f"reports: {dupes} member(s) filed under more than one cluster")

    sids = [f.get("sid") for f in core]
    if any(not x for x in sids):
        bad.append(f"sid: {sum(1 for x in sids if not x)} statement(s) carry no id")
    dup = len(sids) - len(set(x for x in sids if x))
    if dup > 0 and all(sids):
        bad.append(f"sid: {dup} duplicate id(s) — two clusters share one identity")

    seen = set()
    for f in core:
        s = _stmt(f)
        if s in seen:
            bad.append(f"duplicates: {s[:70]!r} appears twice")
        seen.add(s)

    if workers is not None:
        for f in core:
            mem = f.get("members") or []
            unknown = [m for m in mem if m not in workers]
            if unknown:
                bad.append(f"corpus: {len(unknown)} member(s) of {_stmt(f)[:50]!r} are not reports")
                break
            real = len({w for m in mem for w in workers.get(m, ())})
            if f.get("prevalence") is not None and f["prevalence"] != real:
                bad.append(f"prevalence: {_stmt(f)[:50]!r} says {f['prevalence']}, members give {real}")
            if real < MIN_WORKERS:
                bad.append(f"floor: {_stmt(f)[:50]!r} rests on {real} worker(s), floor is {MIN_WORKERS}")
    return bad


def main():
    """Check each artifact named on the command line. -> exit code (0 ok, 1 failures, 2 no arguments)"""
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__.split("\n")[2].strip())
        return 2
    arts, fails = [], []
    for p in paths:
        f = p if p.exists() else engine.results_dir("aggregation") / p.name
        art = json.loads(Path(f).read_text())
        arts.append((p.name, art))
    # take the occupation from the artifact's folder (results/aggregation/<occ>/), so the corpus checks can
    # find its reports; meta.occupation holds the O*NET title, not the folder name
    first = paths[0].resolve()
    if first.parent.parent.name == "aggregation":
        os.environ.setdefault("OCCUPATION", first.parent.name)
    try:
        W = cluster.report_workers()
    except (Exception, SystemExit):                     # noqa: BLE001 - no corpus: skip the corpus checks
        W = None                                        # (load_reports exits when 3_deduped.csv is missing)
    for name, art in arts:
        fails += [f"{name}: {msg}" for msg in check_invariants(art, W)]
    for f in fails:
        print(f"  FAIL  {f}")
    if not fails:
        for name, art in arts:
            core = art.get("core") or []
            filed = len({m for f in core for m in (f.get("members") or [])})
            pool = len({a for k in POOL_KEYS for a in (art.get(k) or ())})
            print(f"  ok  {name:20} {len(core):3} clusters   {filed} filed + {pool} pool = {filed + pool}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
