"""identity — how clusters are identified, and where the aggregation artifacts are read and written.

Two kinds of identity:
  sid         a random id minted when a cluster is created, carried by every later stage
  member_key   a hash of the cluster's member set, used to recognise the same cluster across builds

Artifacts live under results/aggregation/<occ>/: build.json and final.json at the top, then verdicts/
(each stage's decisions), handoffs/ (lists passed between stages), stages/ (snapshots) and history/ (what
later rounds and builds remember: split's verdicts and overlap's reword counts).
"""
import json
import os
import secrets
import sys

from tasknet import engine
# two member sets count as the same cluster if they share at least this share of the larger one
SAME_CLUSTER = float(os.environ.get("SAME_CLUSTER", "0.6"))


def new_cluster_id():
    """A new permanent id for a cluster ("s_" + 8 hex characters).

    Random rather than derived, because the statement text changes when reworded and the member set
    changes when a report moves. Minted in three places: cluster creates a cluster, split splits one
    (`sid_split_from` names the parent), and overlap merges several (`sid_merged_from`). Every other stage
    copies the cluster's row, so the id is kept."""
    return "s_" + secrets.token_hex(4)


# ── artifacts: results/aggregation/<occ>/<name> ──────────────────────────────────────────────────────

def artifact_path(name):
    """results/aggregation/<occ>/<name> for the current occupation."""
    return engine.results_dir("aggregation") / name


def read_artifact(name, hint=""):
    """Load the JSON artifact `name`. If it is missing, exit with a message, adding `hint` (the step that
    makes it)."""
    path = artifact_path(name)
    if not path.exists():
        raise SystemExit(f"{path} not found" + (f" — {hint}" if hint else ""))
    return json.loads(path.read_text())


def write_artifact(name, obj, indent=2):
    """Write `obj` as JSON to `name`, creating its folder (verdicts/, handoffs/, ...) if needed. -> the path"""
    path = artifact_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=indent))
    return path


def history_path(name):
    """The path of history/<name> (kept across rounds and builds), creating the folder if needed."""
    p = artifact_path("history") / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── cluster identity ───────────────────────────────────────────────────────────────────────────────────

def member_key(members):
    """A hash of the member set: the same reports give the same key in any build, however the cluster is
    worded. history/ and the adopted-list index are keyed on it."""
    return engine.sha256("\n".join(sorted(members)))


def load_adopted(name):
    """Index the adopted list `name` (normally final.json) by member set, so a cluster in a rebuild that
    matches one of its clusters reuses that statement instead of having a new one written.
    -> (exact: {member_key: statement}, fuzzy: [(member set, statement)]), for adopted_statement"""
    path = artifact_path(name)
    if not path.exists():
        raise SystemExit(f"--adopted {path} not found")
    fams = [f for f in json.loads(path.read_text()).get("core") or [] if f.get("members")]
    exact = {member_key(f["members"]): (f.get("statement") or f.get("head")) for f in fams}
    fuzzy = [(set(f["members"]), f.get("statement") or f.get("head")) for f in fams]
    print(f"  adopted list: {len(exact)} clusters from {name}", file=sys.stderr)
    return exact, fuzzy


def adopted_statement(members, registry):
    """The adopted statement for this member set, or None. An exact match wins; otherwise the adopted cluster
    with the largest overlap, if it reaches SAME_CLUSTER. A loose match is safe because synthesize still
    checks the reused statement against the actual members."""
    exact, fuzzy = registry
    hit = exact.get(member_key(members))
    if hit:
        return hit
    ms = set(members)
    best, best_r = None, 0.0
    for cand, stmt in fuzzy:
        r = len(ms & cand) / max(len(ms), len(cand), 1)
        if r > best_r:
            best, best_r = stmt, r
    return best if best_r >= SAME_CLUSTER else None
