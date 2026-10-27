"""synthesize — write one O*NET-style task statement per cluster, from its reports (step 2 of each round).

    python -m tasknet.aggregation.synthesize [--in build.json] [--out build.json] [--adopted final.json]

Input:  a build artifact whose clusters have members (and maybe old statements).
Output: the artifact with a `statement` on every cluster, plus `trace`: every write and repair decision.

Per cluster:
  1. Skip it if its members and the prompt are unchanged since its statement was written
     (`written_for` / `written_with` stamps).
  2. Otherwise reuse the adopted statement for the same members (--adopted, normally final.json), or
     draft a new one from the reports. The writer never sees the cluster's head, only its reports and the other
     clusters' heads.
  3. Check the draft: cover_repair redrafts once if it covers less than COV_MIN of its own
     reports; then the fixed checks (umbrella lead verb, over MAX_WORDS words, stacked "-ing" clauses) and the
     writing judge name at most one defect per round, and the writer repairs it. A repair is refused if it
     changes nothing, still has the same defect, or covers fewer reports. At most REPAIR_ROUNDS rounds.
The settings are together at the top of this file.
Adopted ("adopted") statements are never rewritten: their defects are only printed for a reviewer. A
fusion defect also sets the cluster's `form_flag` (⚑), which makes the split step judge it again.
"""
import argparse
import collections
import csv
import functools
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from tasknet import engine
from tasknet import judges
from tasknet import onet_db
from tasknet.aggregation import cluster, identity
from tasknet.aggregation.identity import adopted_statement, member_key

# ── settings ───────────────────────────────────────────────────────────────────────────────────────
# The writer
MODEL = os.environ.get("SYNTHESIZE_MODEL", "claude-sonnet-4-6")
EFFORT = os.environ.get("SYNTHESIZE_EFFORT", "medium")
MAX_OUT = int(os.environ.get("SYNTHESIZE_MAX_OUT", "12000"))            # output token cap per call
PARALLEL_CALLS = int(os.environ.get("PARALLEL_CALLS", 96))              # clusters written at once

# Coverage repair: a draft that covers less than this share of its own reports is redrafted once.
COV_MIN = float(os.environ.get("SYNTHESIZE_COV_MIN", "0.6"))

# Repair rounds: each round fixes one defect; a draft is repaired at most this many times.
REPAIR_ROUNDS = int(os.environ.get("SYNTHESIZE_REPAIR_ROUNDS", "4"))
# The writing judge's defect counts only at this confidence or more (split uses the same bar for ⚑).
GATE_CONF = float(os.environ.get("WRITING_GATE_CONF", "0.75"))

# The fixed checks, run before the writing judge (writing_principles §1.4-1.4c):
MAX_WORDS = int(os.environ.get("SYNTHESIZE_MAX_WORDS", "25"))           # longer statements are cut down
UMBRELLA_VERBS = {"manage", "handle", "conduct", "oversee", "pursue", "deal",   # lead verbs that name no act
                  "ensure", "use", "utilize", "execute", "do"}
ATTESTED_VERBS = {"perform"}     # allowed to lead only where O*NET's own statements, in the occupation's SOC
#                                  major group, pair the verb with one of the words that follow it
# (and at most one "-ing" clause: _gerund_clauses)

SEP = "␟"                                                              # separates cache-key fields


def _repair_hash():
    """A short hash of the repair prompt, part of every repair's cache key."""
    return engine.sha256(engine.load_prompt("synthesize_repair"))[:16]


def _word(x):
    """`x` lowercased, keeping only letters and hyphens."""
    return re.sub(r"[^a-z-]", "", x.lower())


@functools.cache
def _onet_pairs(major_group):
    """(lead verb, word in the next six) pairs from O*NET's task statements whose SOC code starts with
    `major_group`, read once from onet_db.CSV_PATH."""
    pairs = set()
    with onet_db.CSV_PATH.open() as fh:
        for r in csv.DictReader(fh):
            if r["O*NET-SOC Code"].startswith(major_group):
                w = r["Task"].split()
                pairs.update((w[0].lower().rstrip(","), _word(x)) for x in w[1:7])
    return frozenset(pairs)


def _attested(verb, following):
    """Does O*NET, in this occupation's SOC major group, lead with `verb` before any of the `following` words
    (those longer than 3 letters)? False if the O*NET file or the corpus can't be read."""
    try:
        pairs = _onet_pairs(cluster.occupation_soc()[0][:2])
    except Exception as e:                              # noqa: BLE001 - no O*NET file: the verb is not attested
        print(f"      (umbrella attestation unavailable: {e!r})", file=sys.stderr)
        return False
    return any((verb, _word(x)) in pairs for x in following if len(_word(x)) > 3)


_FUSED_FLAGS = {}                 # statement -> its fusion flag, filled by _find_defect


def _set_fusion_flag(row, stmt):
    """Set the row's `form_flag` (⚑) if its current statement was flagged as fused, and clear it if not.
    split.reopen reads it. -> row"""
    row.pop("form_flag", None)
    if stmt in _FUSED_FLAGS:
        row["form_flag"] = _FUSED_FLAGS[stmt]
    return row


# The repair instruction for each defect the writing judge can name (the `principle` values in
# prompts/evaluation/statement/writing_only.txt). _find_defect passes it to the repair prompt as $complaints; the §
# numbers refer to prompts/shared/writing_principles.txt. The fixed-rule defects (umbrella_verb, too_long,
# gerund_stack) build their own instructions in _fixed_check. This text is part of the repair cache key, so editing
# it re-pays for repairs.
_SECTION = {"verb":        "writing_principles §1 VERB SELECTION - the verb misstates the authority, names the "
                           "forum instead of the work, or sits above the activities' own verb. IF THE OBJECTION "
                           "IS THE CHORE-ENUMERATION SHAPE - a responsibility head trailed by an inventory of "
                           "its own chores - the VERB AND OBJECT ARE RIGHT: keep them EXACTLY as written and "
                           "DELETE the inventory clause, adding nothing; the members carry the detail (§1.4b)",
            "vocabulary":  "writing_principles §2 VOCABULARY - house jargon, a clipped form, or a term borrowed "
                           "from an adjacent field",
            "fusion":      "writing_principles §3.2/§3.3 - separate duties conjoined",
            "objects":     "writing_principles §3.1/§3.1a/§3.1b/§3.4 - the object list is malformed. Fix the "
                           "LIST and nothing else: replace an item that is not a co-hyponym of the others, "
                           "delete one of two near-synonyms, swap 'and' for 'or' where the items are "
                           "alternatives, or - for §3.4 - generalise objects drawn from ONE specialty to the "
                           "category that covers what the other activities name",
            "clauses":     "writing_principles §4 PURPOSE, OCCASION AND METHOD CLAUSES - run §4.3's delete-test "
                           "on the named clause and take the branch it lands on. DELETE the clause only if the "
                           "head still names the work without it. IF THE HEAD IS A CATEGORY VERB (represent, "
                           "manage, handle, coordinate, support) AND THE CLAUSE WAS CARRYING THE ONLY CONCRETE "
                           "CONTENT, deleting it makes the statement vaguer and is the WRONG branch: promote "
                           "the clause's own act to the lead verb instead and keep its detail. IF THE OBJECTION "
                           "IS §4.4 - a lasting condition standing where the object should be, something no one "
                           "could be watched doing - the goal was promoted into the object slot: take the one or "
                           "two observable acts the members name and write THEM as plain coordinate verb phrases, "
                           "with the condition demoted to the purpose clause. No 'through' list may remain, and "
                           "no stand-in verb of association ('engage with', 'connect with', 'interact with') may "
                           "lead - those name the same unwatchable condition; the members' own verbs lead",
            "acronym":     "an initialism is left unexpanded - writing_principles and onet_statement both "
                           "require every acronym spelled out in full; write the words, keep the meaning",
            "face_valid":  "the statement is true but reads as something no practitioner in this occupation "
                           "would say - rewrite it the way the work is actually described",
            "counterpart": "writing_principles §5 COUNTERPARTS - the statement needs at least ONE FINDABLE "
                           "party a reader could go and locate (a role, function or published occupation "
                           "title). Read §5.2 before you touch it: a generic word is fine as the OPEN TAIL "
                           "once a named party stands beside it - 'the site foreman and other trades' is "
                           "right, 'other trades' alone is not - so ADD the named party rather than deleting "
                           "the tail, and do not swap one collective for another"}

TRACE = []       # every write/repair decision, including the ones that changed nothing; saved as `trace`


def _trace(**kw):
    """Record one decision in TRACE."""
    TRACE.append(kw)


# ── the writer ─────────────────────────────────────────────────────────────────────────────────────

def _report_list(reports):
    """The reports as the writer's list, each tagged with who reported it: "- <report>  [W1][W3]". The tags are
    numbered within this cluster only, so they show which reports share a worker without identifying anyone."""
    W = cluster.report_workers()
    ids = sorted({w for a in reports for w in W.get(a, ())})
    tag = {w: f"W{i}" for i, w in enumerate(ids, 1)}
    return "\n".join(
        f"- {a}" + (f"  [{']['.join(tag[w] for w in sorted(W.get(a, ())))}]" if W.get(a) else "")
        for a in sorted(reports))


# opening words of lines where the model is thinking aloud rather than giving its answer
_DELIBERATION = ("wait", "wait,", "wait —", "hmm", "hold on", "actually", "let me", "on reflection",
                 "i should", "correction", "revised", "revision", "better:", "final:", "instead:")


def _final_line(raw):
    """The statement from a writer reply, dropping lines where the model thinks aloud. If several lines
    look like statements, the last one wins. -> str"""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if len(lines) <= 1:
        return (raw or "").strip()
    keep = [ln for ln in lines
            if not any(ln.lower().startswith(d) for d in _DELIBERATION)
            and not ln.endswith("?") and not ln.startswith(("#", "-", "*", "`"))
            and len(ln.split()) >= 4]
    if not keep:
        return (raw or "").strip()
    if len(keep) > 1:
        print(f"  _final_line: dropped {len(lines) - 1} of {len(lines)} lines of writer deliberation",
              file=sys.stderr)
    return keep[-1]


class _Writer:
    """The writer for one cluster. Holds the prompt template, the cluster's report list and its siblings (the
    other clusters' heads); calling it writes one statement, with `extra` appended to the prompt (a repair
    request). The cluster's own head is never shown: the prompt has no slot for it."""

    def __init__(self, template, items, sibs):
        self.template, self.items, self.sibs = template, items, sibs
        self.hash = engine.sha256(template)             # part of every cache key for this writer
        self.prefix = template.split("$items")[0]       # identical on every call: the cacheable prefix

    def __call__(self, tag, items=None, extra=""):
        prompt = self.template.replace("$items", items if items is not None else self.items) \
                              .replace("$siblings", self.sibs)
        return _final_line(cluster.chat_until_complete(prompt + extra, MAX_OUT, tag, model=MODEL, effort=EFFORT,
                                         cache_prefix=self.prefix)[0])


def write_statement(cluster, reports, siblings=(), adopted=None):
    """Write and check one statement for a cluster. -> statement
    `cluster` is its head (used only in the cache key), `siblings` the other clusters' heads, and `adopted`
    an adopted statement to check instead of drafting a new one."""
    template = engine.load_prompt("synthesize", occupation=engine.occupation())
    items = _report_list(reports)
    sibs = "\n".join(f"- {h}" for h in sorted(siblings) if h and h != cluster)
    draft_fn = _Writer(template, items, sibs)

    if adopted is not None:
        draft = _final_line(adopted)
        return _check_and_repair(draft, reports, draft_fn, sum(_coverage(draft, reports)), adopted=True)
    # "RELABEL" is an old name for this cache entry; renaming it would orphan every cached first draft
    key = engine.sha256(SEP.join(
        ["RELABEL", MODEL, EFFORT, draft_fn.hash, cluster, items] + (["SIBS"] if sibs else [])))
    # _final_line again on the cached value: older cached drafts were stored with the model's thinking
    draft = _final_line(engine.memo("relabel_head", key, lambda: draft_fn(cluster[:48])))
    draft, cov = _cover_repair(draft, reports, draft_fn)
    return _check_and_repair(draft, reports, draft_fn, cov)


# ── coverage repair ────────────────────────────────────────────────────────────────────────────────

def _coverage(stmt, reports):
    """Which of `reports` the statement covers, by the coverage judge. -> [bool], in sorted(reports) order"""
    with ThreadPoolExecutor(max_workers=judges.PARALLEL_CALLS) as ex:
        return list(ex.map(lambda m: bool(judges.covers(m, stmt)), sorted(reports)))


def _cover_repair(stmt, reports, draft_fn):
    """If the draft covers less than COV_MIN of its own reports, redraft once, naming the reports it missed, and
    keep whichever version covers more (unless the redraft reads as two duties and the draft didn't).
    -> (statement, number of reports it covers)"""
    if not (stmt and reports):
        return stmt, 0
    missed = [m for m, ok in zip(sorted(reports), _coverage(stmt, reports)) if not ok]
    covered = len(reports) - len(missed)
    if not missed or covered / len(reports) >= COV_MIN:
        return stmt, covered
    key = engine.sha256(SEP.join(["COVREPAIR2", MODEL, EFFORT, draft_fn.hash, _repair_hash(), stmt,
                                  engine.sha256(SEP.join(missed))]))

    def _redraft():
        # the repair prompt, with the missed reports as the complaint
        why = ("The statement does not describe these activities:\n" + _report_list(missed))
        block = engine.load_prompt("synthesize_repair").replace("$draft", stmt).replace("$complaints", why)
        return draft_fn("cover-repair", extra="\n\n" + block)
    try:
        alt = engine.memo("relabel_cover", key, _redraft)
    except Exception:
        return stmt, covered
    if not alt or alt == stmt:
        return stmt, covered
    cov_new = sum(_coverage(alt, reports))
    win = cov_new > covered
    def fused(w):                                       # the writing judge says "two duties", confidently
        return w.get("principle") == "fusion" and float(w.get("confidence") or 0) >= GATE_CONF
    if win:
        try:
            if fused(_writing_defect(alt, reports)):
                if not fused(_writing_defect(stmt, reports)):
                    _trace(stage="cover_repair", exit="5-fusion-introduced", kept=stmt, draft=stmt,
                           repair=alt, cov_before=covered, cov_after=cov_new, n_atoms=len(reports))
                    return stmt, covered
        except Exception as e:                          # noqa: BLE001 - if the check fails, keep the repair
            print(f"      writing judge failed during cover_repair: {e!r}", file=sys.stderr)
    _trace(stage="cover_repair", exit="accepted" if win else "0-no-gain", kept=alt if win else stmt,
           draft=stmt, repair=alt, cov_before=covered, cov_after=cov_new, n_atoms=len(reports))
    return (alt, cov_new) if win else (stmt, covered)


# ── check and repair: fix only what a check names ──────────────────────────────────────────────────

_STOP = {                                                 # words ignored when comparing two versions
"a", "an", "and", "or", "the", "to", "for", "of", "in", "on", "with", "from", "by", "at", "as",
         "into", "across", "their", "its", "this", "that", "other", "such"}


def _writing_defect(stmt, reports):
    """Ask the writing judge (judges.writing) for the statement's main writing defect, if any.
    -> {defect, principle, trigger, confidence}"""
    return judges.writing(stmt, engine.occupation(), "\n".join(f"- {m}" for m in sorted(reports))) or {}


# "-ing" words that are nouns or adjectives here, not the start of a clause
_GERUND_SKIP = {"existing", "ongoing", "engineering", "marketing", "incoming", "outgoing", "pending",
                "standing", "governing", "operating", "training", "housing", "staffing", "billing",
                "accounting", "reporting", "underlying", "remaining", "following", "upcoming", "including",
                "hearing", "funding", "spending", "closing", "opening", "everything", "nothing", "wrongdoing"}


def _gerund_clauses(stmt):
    """The "-ing" words that open a clause in `stmt` (", reviewing ...", "by checking ..."), for the §1.4b
    rule. Counted by regex because the judge miscounts them; a false hit costs one repair attempt."""
    hits = []
    for m in re.finditer(r"(?:,|\bincluding|\bby|\bwhile)\s+(?:and\s+|or\s+)?(\w+ing)\b", stmt):
        if m.group(1).lower() not in _GERUND_SKIP:
            hits.append(m.group(1))
    return hits


def _fixed_check(stmt):
    """The fixed checks, in order: umbrella lead verb, too long, stacked "-ing" clauses. -> (principle,
    trigger, repair instruction) for the first that fails, or None. The instruction text is part of the
    repair's cache key, so editing it re-pays for repairs."""
    words = stmt.split()
    head = words[0].lower().rstrip(",") if words else ""
    if head in UMBRELLA_VERBS or (head in ATTESTED_VERBS and not _attested(head, words[1:7])):
        return "umbrella_verb", words[0], (
            "writing_principles §1.4 — THE LEAD VERB IS AN UMBRELLA: '" + words[0] + "' names no "
            "act. Replace it with the concrete verb the members' own activities use (the activity the "
            "statement actually describes), and change nothing else.")
    if len(words) > MAX_WORDS:
        return "too_long", f"{len(words)} words", (
            "writing_principles §1.4c — A STATEMENT IS ABOUT 15 WORDS and this one runs "
            f"{len(words)}. Cut it toward ~20: collapse enumerations into a 'such as' list of two "
            "or three items, drop redundant clauses, keep the verb, object, and one clause — and change "
            "nothing else.")
    g = _gerund_clauses(stmt)
    if len(g) >= 2:
        return "gerund_stack", ", ".join(g), (
            "writing_principles §1.4b — GERUNDS ARE A LAST RESORT: this statement stacks "
            f"{len(g)} participial clauses ({', '.join(g)}); keep AT MOST ONE. DROP the extra clauses "
            "outright — the members carry the detail and the statement need not enumerate its chores — "
            "or demote them to a noun list ('including X, Y, and Z'); and change nothing else.")
    return None


def _find_defect(stmt, reports):
    """Check one statement. -> (repair instruction, verdict), or ("", verdict) if it passes.
    The fixed checks run first, then the writing judge, whose defect counts only at GATE_CONF confidence or
    more. A confident fusion defect is also recorded in _FUSED_FLAGS."""
    fixed = _fixed_check(stmt)
    if fixed:
        principle, trigger, why = fixed
        return why, {"defect": True, "principle": principle, "confidence": 1.0, "trigger": trigger}
    try:
        w = _writing_defect(stmt, reports)
        if (w.get("defect") and w.get("principle") == "fusion"
                and float(w.get("confidence") or 0.0) >= GATE_CONF):
            # a fusion is both flagged (for the split step) and repaired here; the flag stays either way
            _FUSED_FLAGS[stmt] = {"principle": w.get("principle"), "confidence": w.get("confidence"),
                                  "trigger": (w.get("trigger") or "")[:120]}
            print(f"      ⚑ {w.get('principle')} flagged (repair attempted): {stmt[:70]}", file=sys.stderr)
    except Exception as e:                              # noqa: BLE001 - fail open, but say so
        print(f"      writing judge failed, statement not checked: {e!r}", file=sys.stderr)
        w = {}
    if not (w.get("defect") and float(w.get("confidence") or 0.0) >= GATE_CONF):
        return "", w
    pr, trig = w.get("principle") or "form", (w.get("trigger") or "").strip()
    return (f"{_SECTION.get(pr, pr)}: {trig}" if trig else _SECTION.get(pr, pr)), w


def _repair(stmt, reports, draft_fn, cov_draft, rnd, why, w):
    """Ask the writer to fix the one defect `why` (verdict `w`) and decide whether to keep the repair.
    -> the statement to carry forward

    The repair is refused (the old statement kept) if it is empty or unchanged ("no-change"), still fails
    the same check ("still-rejected"), or covers fewer reports ("coverage-fell"); a cut-down repair (for
    length, stacked clauses, or one that mostly deletes words) is allowed to lose coverage. `cov_draft` is
    the statement's coverage count, `rnd` the round."""
    base = dict(stage="quality_gate", draft=stmt, principle=w.get("principle"),
                conf=w.get("confidence"), why=why, trigger=(w.get("trigger") or ""))
    key = engine.sha256(SEP.join(["RELQUAL2", MODEL, EFFORT, draft_fn.hash, _repair_hash(), stmt, why]
                                 + ([f"RND{rnd}"] if rnd > 1 else [])))

    def compute():
        repair = engine.load_prompt("synthesize_repair").replace("$draft", stmt).replace("$complaints", why)
        return draft_fn("quality-gate", extra="\n\n" + repair)
    try:
        fixed = engine.memo("relabel_quality", key, compute)
    except Exception:
        return stmt
    base["repair"] = fixed
    if not fixed or fixed == stmt:
        _trace(exit="no-change", kept=stmt, **base)
        return stmt
    # a repair that is shorter and keeps 80% of the draft's content words (minus _STOP) is a deletion
    def words(text):
        return {w for w in re.findall(r"[a-z][a-z-]+", (text or "").lower()) if w not in _STOP}
    fixed_content = words(fixed)
    from_draft = len(fixed_content & words(stmt)) / max(len(fixed_content), 1)
    deletion_repair = len(fixed.split()) < len(stmt.split()) and from_draft >= 0.8
    trim = w.get("principle") in ("gerund_stack", "too_long") or deletion_repair
    why_after, w_after = _find_defect(fixed, reports)
    pr_before, pr_after = w.get("principle"), w_after.get("principle")
    if why_after and pr_after == pr_before:
        _trace(exit="still-rejected", kept=stmt, why_after=why_after, **base)
        return stmt
    cov_new = sum(_coverage(fixed, reports))
    if cov_new < cov_draft and not trim:
        _trace(exit="coverage-fell", kept=stmt, cov_before=cov_draft, cov_after=cov_new, **base)
        return stmt
    _trace(exit="accepted", kept=fixed, cov_before=cov_draft, cov_after=cov_new,
           residual=(pr_after if why_after else None), **base)
    return fixed


def _check_and_repair(stmt, reports, draft_fn, cov_draft, adopted=False):
    """Check and repair until the statement passes, a repair is refused, or REPAIR_ROUNDS are done; each round
    fixes one defect. An adopted statement (`adopted`) is checked and its defect printed, never rewritten.
    -> statement"""
    out, cov = stmt, cov_draft
    for rnd in range(1, REPAIR_ROUNDS + 1):
        why, w = _find_defect(out, reports)
        if not why:
            break
        if adopted:                                    # an adopted statement stays; print its defect
            print(f"  NOT APPLIED (adopted) [{w.get('principle')}] {str(out)[:70]}"
                  "  — adopted: adjudicated prose is not machine-rewritten", file=sys.stderr)
            break
        if not out or not reports:
            break
        nxt = _repair(out, reports, draft_fn, cov, rnd, why, w)
        if nxt == out:
            break
        out, cov = nxt, sum(_coverage(nxt, reports))
    return out


# ── driver ─────────────────────────────────────────────────────────────────────────────────────────

def write_statements(clusters, adopted_reg=None):
    """Write a statement for every cluster, in parallel. -> the clusters, each with `statement` and stamps
    `written_for` (its member-set key) and `written_with` (the prompt hash) for the skip in step 1.
    `adopted_reg` is the adopted list's registry (identity.load_adopted), or None."""
    heads = [f["head"] for f in clusters]
    template = engine.load_prompt("synthesize", occupation=engine.occupation())
    engine.prewarm(template, MODEL)
    prompt_hash = engine.sha256(template)

    def one(f):
        prev = f.get("statement") or f.get("head")
        fkey = member_key(f["members"])
        written_for_these = prev and f.get("written_for") == fkey
        # skip: same members and same prompt as when this statement was written, so no model call
        if written_for_these and f.get("written_with") == prompt_hash:
            _trace(stage="redraft", exit="0-unchanged", kept=prev)
            return {**f, "statement": prev}
        # otherwise, an existing statement is checked rather than redrafted: the cluster's own (same members,
        # older prompt), or the adopted one for its member set; with neither, write() drafts a new one
        if written_for_these and f.get("written_with"):
            adopted, kept_label, changed_label = prev, "regate-kept", "regated"
        else:
            adopted = adopted_statement(f["members"], adopted_reg) if adopted_reg else None
            kept_label, changed_label = "ported-standing", "ported-repaired"
        stmt2 = write_statement(f["head"], f["members"], heads, adopted=adopted)
        if adopted is not None:
            _trace(stage="redraft", exit=kept_label if stmt2 == adopted else changed_label,
                   draft=adopted, kept=stmt2)
        return _set_fusion_flag({**f, "written_for": fkey, "written_with": prompt_hash,
                            "statement": stmt2}, stmt2)
    with ThreadPoolExecutor(max_workers=min(PARALLEL_CALLS, max(len(clusters), 1))) as ex:
        return list(ex.map(one, clusters))


def main():
    engine.set_stage("synthesize")                   # use this stage's own API key, if one is set
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--in", dest="inp", default="build.json", help="a build artifact (core or clusters)")
    ap.add_argument("--out", default="build.json", help="filename under results/aggregation/<occ>/")
    ap.add_argument("--adopted", default=None,
                    help="an ADOPTED deliverable artifact (e.g. final_46.json); clusters whose members "
                         "match one of its clusters (exact or >=60%% mutual overlap) are checked on its "
                         "statement instead of redrawn. Continuity for rebuilds; omit for a from-scratch "
                         "opinion.")
    args = ap.parse_args()
    build = identity.read_artifact(args.inp, "run cluster.py first.")
    fams = [{**c, "head": c.get("statement") or c["head"], "members": sorted(set(c.get("members") or []))}
            for c in build["core"]]
    out = write_statements(fams, identity.load_adopted(args.adopted) if args.adopted else None)

    print(f"\n{engine.occupation()}: {len(out)} statements from {args.inp}")
    for r in sorted(out, key=lambda r: -len(r["members"])):
        print(f"  {len(r['members']):3d} reports · {r['prevalence']:2d} workers  {r['statement']}")
    for stage in ("cover_repair", "quality_gate"):
        c = collections.Counter(t["exit"] for t in TRACE if t.get("stage") == stage)
        print(f"  {stage:14s} " + (" · ".join(f"{k} {v}" for k, v in sorted(c.items())) or "never fired"))

    print("→ " + str(identity.write_artifact(args.out, {
        "meta": {**build.get("meta", {}), "source": args.inp, "model": MODEL, "effort": EFFORT},
        "core": out, "trace": TRACE, **cluster.carry(build)})))


if __name__ == "__main__":
    main()
