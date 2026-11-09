"""synthesize — one O*NET task statement per family, written from the ATOMS and judged before it leaves.

    build = synthesize.statements(ctx, build, standing=None)          # every family that needs a statement
    stmt  = synthesize.statement(ctx, head, atoms, siblings, standing) # one family

Per family: skip if members + prompt + stamps are unchanged; else port the adopted statement (`standing`, a
families.registry, matched by member set) or draft fresh (the writer, head withheld, siblings supplied); then
the gate — deterministic checks (gerunds, length, umbrella verb attested against ctx.reference_statements), own-atom coverage
with cover_repair, the writing judge, then repair — one per principle, max 4 rounds, with three refusals that
keep the draft (no-change, still-rejected, coverage-fell). Fusion is flag-and-repair (⚑ form_flag). Standing
prose is never machine-rewritten: a defect on an adopted statement is reported to ctx.docket and the statement
stands. Every decision lands in ctx.trace.
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor

from .. import families
from ..lib import sha256
from . import blocks, cluster

SEP = "␟"
OFFENDER_HEADS = {"manage", "handle", "conduct", "oversee", "pursue", "deal", "ensure", "use", "utilize", "execute", "do"}
CAUTION_HEADS = {"perform"}
FLAG_PRINCIPLES = {"fusion"}
_STOP = {"a", "an", "and", "or", "the", "to", "for", "of", "in", "on", "with", "from", "by", "at", "as",
         "into", "across", "their", "its", "this", "that", "other", "such"}
_GERUND_SKIP = {"existing", "ongoing", "engineering", "marketing", "incoming", "outgoing", "pending",
                "standing", "governing", "operating", "training", "housing", "staffing", "billing",
                "accounting", "reporting", "underlying", "remaining", "following", "upcoming", "including",
                "hearing", "funding", "spending", "closing", "opening", "everything", "nothing", "wrongdoing"}
_DELIBERATION = ("wait", "wait,", "wait —", "hmm", "hold on", "actually", "let me", "on reflection",
                 "i should", "correction", "revised", "revision", "better:", "final:", "instead:")
# THE JUDGE NAMES A DEFECT IN ONE WORD; THE REPAIR NEEDS AN INSTRUCTION. Keys are writing_only.txt's `principle`
# enum; values are what the repair prompt gets as $complaints. Section numbers point into writing_principles.txt.
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


def _trace(ctx, **kw):
    ctx.trace.append(kw)


def _repair_hash(ctx):
    try:
        return sha256(ctx.prompt("synthesize_repair"))[:16]
    except Exception:                                     # noqa: BLE001
        return "?"


# ── reference attestation of umbrella verbs ───────────────────────────────────────────────────────

def _word(x):
    return re.sub(r"[^a-z-]", "", x.lower())


def _group_pairs(ctx):
    """(lead verb, following content word) pairs from ctx.reference_statements — a corpus of well-formed task
    statements of this occupation's group, the record of which umbrella + object pairings practitioners use.
    Empty without one (then every caution verb reads as an umbrella)."""
    cached = getattr(ctx, "_attest_pairs", None)
    if cached is not None:
        return cached
    pairs = set()
    for s in ctx.reference_statements or []:
        w = str(s).split()
        if w:
            pairs.update((w[0].lower().rstrip(","), _word(x)) for x in w[1:7])
    ctx._attest_pairs = frozenset(pairs)
    return ctx._attest_pairs


def _attested(ctx, head, following):
    pairs = _group_pairs(ctx)
    return any((head, _word(x)) in pairs for x in following if len(_word(x)) > 3)


# ── the writer ─────────────────────────────────────────────────────────────────────────────────────

def _tag_items(ctx, atoms):
    """The activity list with a stable per-worker tag: "- <atom>  [W2]"."""
    W = ctx.atoms.sources
    ids = sorted({w for a in atoms for w in W.get(a, ())})
    tag = {w: f"W{i}" for i, w in enumerate(ids, 1)}
    return "\n".join(f"- {a}" + (f"  [{']['.join(tag[w] for w in sorted(W.get(a, ())))}]" if W.get(a) else "")
                     for a in sorted(atoms))


def one_line(raw):
    """The statement, with the model's own deliberation stripped. -> str."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if len(lines) <= 1:
        return (raw or "").strip()
    keep = [ln for ln in lines
            if not any(ln.lower().startswith(d) for d in _DELIBERATION)
            and not ln.endswith("?") and not ln.startswith(("#", "-", "*", "`")) and len(ln.split()) >= 4]
    if not keep:
        return (raw or "").strip()
    if len(keep) > 1:
        print(f"  one_line: dropped {len(lines) - 1} of {len(lines)} lines of writer deliberation", file=sys.stderr)
    return keep[-1]


class Draft:
    """What it takes to write one more statement for this family: the prompt, the atom list, the siblings, and
    the template hash the memo key needs. The heading is absent by construction."""

    def __init__(self, ctx, template, items, sibs):
        self.ctx, self.template, self.items, self.sibs = ctx, template, items, sibs
        self.hash = sha256(template)
        self.prefix = template.split("$items")[0]

    def __call__(self, tag, items=None, extra=""):
        prompt = self.template.replace("$items", items if items is not None else self.items).replace("$siblings", self.sibs)
        return one_line(cluster.chat(self.ctx, prompt + extra, int(self.ctx.config.get("models.write.max_out", 12000)), tag,
                                     model=self.ctx.model("write"), effort=self.ctx.effort("write"), cache_prefix=self.prefix)[0])


def statement(ctx, family, atoms, siblings=(), standing=None) -> str:
    """Write one task statement covering `atoms`. -> statement. With blocks.member_cap the writer and its
    coverage checks see the capped member list; the caller's stamps still key on the full set."""
    atoms = blocks.cap_members(ctx, atoms)
    model, effort = ctx.model("write"), ctx.effort("write")
    template = ctx.prompt("synthesize", occupation=ctx.occupation)
    items = _tag_items(ctx, atoms)
    sibs = "\n".join(f"- {h}" for h in sorted(siblings) if h and h != family)
    draft_fn = Draft(ctx, template, items, sibs)
    if standing is not None:
        draft = one_line(standing)
        return _quality_gate(ctx, draft, atoms, draft_fn, sum(covered(ctx, draft, atoms)), standing=True)
    # "RELABEL" is the historical memo prefix and must stay: it keys every cached first draft.
    key = sha256(SEP.join(["RELABEL", model, effort, draft_fn.hash, family, items] + (["SIBS"] if sibs else [])))
    draft = one_line(ctx.cache.memo("relabel_head", key, lambda: draft_fn(family[:48])))
    draft, cov = _cover_repair(ctx, draft, atoms, draft_fn)
    return _quality_gate(ctx, draft, atoms, draft_fn, cov)


# ── coverage repair ────────────────────────────────────────────────────────────────────────────────

def covered(ctx, stmt, atoms):
    """Which of `atoms` the statement covers -> [bool] in sorted(atoms) order. THE one coverage primitive here."""
    j = ctx.judges
    j.covers_many([(m, stmt) for m in atoms])
    with ThreadPoolExecutor(max_workers=j.workers) as ex:
        return list(ex.map(lambda m: bool(j.covers(m, stmt)), sorted(atoms)))


def _cover_repair(ctx, stmt, atoms, draft_fn):
    """If the draft describes less than chain.coverage_min of its own members, draft once more against the
    atoms it MISSED and keep whichever version covers more. -> (statement, coverage count)."""
    if not (stmt and atoms):
        return stmt, 0
    model, effort = ctx.model("write"), ctx.effort("write")
    gate_conf = float(ctx.config.get("chain.gate_conf", 0.75))
    missed = [m for m, ok in zip(sorted(atoms), covered(ctx, stmt, atoms)) if not ok]
    cov = len(atoms) - len(missed)
    if not missed or cov / len(atoms) >= float(ctx.config.get("chain.coverage_min", 0.6)):
        return stmt, cov
    key = sha256(SEP.join(["COVREPAIR2", model, effort, draft_fn.hash, _repair_hash(ctx), stmt, sha256(SEP.join(missed))]))

    def redraft():
        why = "The statement does not describe these activities:\n" + _tag_items(ctx, missed)
        block = ctx.prompt("synthesize_repair").replace("$draft", stmt).replace("$complaints", why)
        return draft_fn("cover-repair", extra="\n\n" + block)
    try:
        alt = ctx.cache.memo("relabel_cover", key, redraft)
    except Exception:                                     # noqa: BLE001
        return stmt, cov
    if not alt or alt == stmt:
        return stmt, cov
    cov_new = sum(covered(ctx, alt, atoms))
    win = cov_new > cov
    if win:
        try:
            wa = _form(ctx, alt, atoms)
            if wa.get("principle") == "fusion" and float(wa.get("confidence") or 0) >= gate_conf:
                wd = _form(ctx, stmt, atoms)
                if not (wd.get("principle") == "fusion" and float(wd.get("confidence") or 0) >= gate_conf):
                    _trace(ctx, stage="cover_repair", exit="5-fusion-introduced", kept=stmt, draft=stmt, repair=alt,
                           cov_before=cov, cov_after=cov_new, n_atoms=len(atoms))
                    return stmt, cov
        except Exception:                                 # noqa: BLE001
            pass
    _trace(ctx, stage="cover_repair", exit="accepted" if win else "0-no-gain", kept=alt if win else stmt,
           draft=stmt, repair=alt, cov_before=cov, cov_after=cov_new, n_atoms=len(atoms))
    return (alt, cov_new) if win else (stmt, cov)


# ── the quality gate ───────────────────────────────────────────────────────────────────────────────

def _content(text):
    return {w for w in re.findall(r"[a-z][a-z-]+", (text or "").lower()) if w not in _STOP}


def _form(ctx, stmt, atoms):
    """The writing judge, the same instrument the expert study used. -> {defect, principle, trigger, confidence}."""
    return ctx.judges.writing_defects(stmt, atoms) or {}


def gerund_clauses(stmt):
    """Deterministic §1.4b counter -> the participial clause openers found."""
    return [m.group(1) for m in re.finditer(r"(?:,|\bincluding|\bby|\bwhile)\s+(?:and\s+|or\s+)?(\w+ing)\b", stmt)
            if m.group(1).lower() not in _GERUND_SKIP]


def rejects(ctx, stmt, atoms):
    """(reason, verdict) — why the form judge rejects this statement, or ("", verdict) if it does not."""
    gate_conf = float(ctx.config.get("chain.gate_conf", 0.75))
    words = stmt.split()
    head = words[0].lower().rstrip(",") if words else ""
    if head in OFFENDER_HEADS or (head in CAUTION_HEADS and not _attested(ctx, head, words[1:7])):
        why = ("writing_principles §1.4 — THE LEAD VERB IS AN UMBRELLA: '" + words[0] + "' names no "
               "act. Replace it with the concrete verb the members' own activities use (the activity the "
               "statement actually describes), and change nothing else.")
        return why, {"defect": True, "principle": "offender_head", "confidence": 1.0, "trigger": words[0]}
    if len(words) > 25:
        why = ("writing_principles §1.4c — A STATEMENT IS ABOUT 15 WORDS and this one runs "
               f"{len(words)}. Cut it toward ~20: collapse enumerations into a 'such as' list of two "
               "or three items, drop redundant clauses, keep the verb, object, and one clause — and change "
               "nothing else.")
        return why, {"defect": True, "principle": "too_long", "confidence": 1.0, "trigger": f"{len(words)} words"}
    g = gerund_clauses(stmt)
    if len(g) >= 2:
        why = ("writing_principles §1.4b — GERUNDS ARE A LAST RESORT: this statement stacks "
               f"{len(g)} participial clauses ({', '.join(g)}); keep AT MOST ONE. DROP the extra clauses "
               "outright — the members carry the detail and the statement need not enumerate its chores — "
               "or demote them to a noun list ('including X, Y, and Z'); and change nothing else.")
        return why, {"defect": True, "principle": "gerund_stack", "confidence": 1.0, "trigger": ", ".join(g)}
    try:
        w = _form(ctx, stmt, atoms)
        if w.get("defect") and w.get("principle") in FLAG_PRINCIPLES and float(w.get("confidence") or 0.0) >= gate_conf:
            ctx.form_flags[stmt] = {"principle": w.get("principle"), "confidence": w.get("confidence"),
                                    "trigger": (w.get("trigger") or "")[:120]}
            print(f"      ⚑ {w.get('principle')} flagged (repair attempted): {stmt[:70]}", file=sys.stderr)
    except Exception:                                     # noqa: BLE001
        w = {}
    if not (w.get("defect") and float(w.get("confidence") or 0.0) >= gate_conf):
        return "", w
    pr, trig = w.get("principle") or "form", (w.get("trigger") or "").strip()
    return (f"{_SECTION.get(pr, pr)}: {trig}" if trig else _SECTION.get(pr, pr)), w


def _gate_once(ctx, stmt, atoms, draft_fn, cov_draft, rnd=1, verdict=None):
    """One gate round: rewrite once if a judge rejects the draft. -> statement."""
    if not stmt or not atoms:
        return stmt
    model, effort = ctx.model("write"), ctx.effort("write")
    why, w = verdict or rejects(ctx, stmt, atoms)
    base = dict(stage="quality_gate", draft=stmt, principle=w.get("principle"), conf=w.get("confidence"), why=why,
                trigger=(w.get("trigger") or ""))
    if not why:
        _trace(ctx, exit="pass", kept=stmt, **base)
        return stmt
    key = sha256(SEP.join(["RELQUAL2", model, effort, draft_fn.hash, _repair_hash(ctx), stmt, why] + ([f"RND{rnd}"] if rnd > 1 else [])))

    def compute():
        repair = ctx.prompt("synthesize_repair").replace("$draft", stmt).replace("$complaints", why)
        return draft_fn("quality-gate", extra="\n\n" + repair)
    try:
        fixed = ctx.cache.memo("relabel_quality", key, compute)
    except Exception:                                     # noqa: BLE001
        return stmt
    base["repair"] = fixed
    if not fixed or fixed == stmt:
        _trace(ctx, exit="no-change", kept=stmt, **base)
        return stmt
    fixed_content = _content(fixed)
    from_draft = len(fixed_content & _content(stmt)) / max(len(fixed_content), 1)
    deletion_repair = len(fixed.split()) < len(stmt.split()) and from_draft >= 0.8
    trim = w.get("principle") in ("gerund_stack", "too_long") or deletion_repair
    why_after, w_after = rejects(ctx, fixed, atoms)
    if why_after and w_after.get("principle") == w.get("principle"):
        _trace(ctx, exit="still-rejected", kept=stmt, why_after=why_after, **base)
        return stmt
    cov_new = sum(covered(ctx, fixed, atoms))
    if cov_new < cov_draft and not trim:
        _trace(ctx, exit="coverage-fell", kept=stmt, cov_before=cov_draft, cov_after=cov_new, **base)
        return stmt
    _trace(ctx, exit="accepted", kept=fixed, cov_before=cov_draft, cov_after=cov_new,
           residual=(w_after.get("principle") if why_after else None), **base)
    return fixed


def _regate_admits(ctx, w, standing, stmt):
    """May this flag rewrite the statement? On ADJUDICATED prose, nothing may: the defect is docketed."""
    if not standing:
        return True
    entry = {"kind": "regate", "statement": stmt, "principle": w.get("principle"), "trigger": w.get("trigger", ""),
             "why": "standing: adjudicated prose is not machine-rewritten"}
    ctx.docket.append(entry)
    print(f"  DOCKET regate [{entry['principle']}] {stmt[:70]}  — {entry['why']}", file=sys.stderr)
    return False


def _quality_gate(ctx, stmt, atoms, draft_fn, cov_draft, standing=False):
    """Repair the draft until a judge stops rejecting it, at most 4 rounds; one repair per principle."""
    out, cov = stmt, cov_draft
    for rnd in range(1, 5):
        why, w = rejects(ctx, out, atoms)
        if not why:
            break
        if not _regate_admits(ctx, w, standing, out):
            break
        nxt = _gate_once(ctx, out, atoms, draft_fn, cov, rnd=rnd, verdict=(why, w))
        if nxt == out:
            break
        out, cov = nxt, sum(covered(ctx, nxt, atoms))
    return out


def stamp_flag(ctx, row, stmt):
    """The ⚑ rides on the row exactly when the STATEMENT NOW ON IT was flagged."""
    row.pop("form_flag", None)
    if stmt in ctx.form_flags:
        row["form_flag"] = ctx.form_flags[stmt]
    return row


# ── driver ─────────────────────────────────────────────────────────────────────────────────────────

def write_all(ctx, fams, standing_reg=None):
    """One statement per family, in parallel -> rows with statement + continuity stamps."""
    heads = [f["head"] for f in fams]
    by_block = bool(blocks.block_by(ctx))
    fn_of = [blocks.function_of(ctx, f["members"]) for f in fams] if by_block else [""] * len(fams)
    sibs_of = (lambda i: [h for h, fn in zip(heads, fn_of) if blocks.same_block(ctx, fn_of[i], fn)]) if by_block else (lambda i: heads)
    template = ctx.prompt("synthesize", occupation=ctx.occupation)
    ctx.engine.prewarm(template, ctx.model("write"))
    prompt_hash = sha256(template)

    def one(i):
        f = fams[i]
        prev = f.get("statement") or f.get("head")
        fkey = families.key(f["members"])
        written_for_these = prev and f.get("written_for") == fkey
        if written_for_these and f.get("written_with") == prompt_hash:
            _trace(ctx, stage="redraft", exit="0-unchanged", kept=prev)
            return {**f, "statement": prev}
        if written_for_these and f.get("written_with"):
            standing, kept_label, changed_label = prev, "regate-kept", "regated"
        else:
            standing = families.match(f["members"], standing_reg) if standing_reg else None
            kept_label, changed_label = "ported-standing", "ported-repaired"
        stmt2 = statement(ctx, f["head"], f["members"], sibs_of(i), standing=standing)
        if standing is not None:
            _trace(ctx, stage="redraft", exit=kept_label if stmt2 == standing else changed_label, draft=standing, kept=stmt2)
        return stamp_flag(ctx, {**f, "written_for": fkey, "written_with": prompt_hash, "statement": stmt2}, stmt2)
    with ThreadPoolExecutor(max_workers=min(ctx.workers_for("synthesize"), max(len(fams), 1))) as ex:
        return list(ex.map(one, range(len(fams))))


def statements(ctx, build, standing=None) -> dict:
    """Write and gate a statement for every family that needs one. `standing` is a families.registry (an adopted
    list) for continuity. -> a new build dict carrying the leftovers and ctx.trace."""
    fams = [{**c, "head": c.get("statement") or c["head"], "members": sorted(set(c.get("members") or []))} for c in build["core"]]
    out = write_all(ctx, fams, standing)
    return {"meta": {**build.get("meta", {}), "model": ctx.model("write"), "effort": ctx.effort("write")},
            "core": out, "trace": list(ctx.trace), **families.leftovers(build)}
