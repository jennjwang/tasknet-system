"""Judges — the semantic primitives the chain and the evaluation call, as one instrument bound to a Context.

    j = ctx.judges
    j.covers(atom, statement) -> bool            does the statement describe this atom (an instance, sub-type or step of it)?
    j.overlaps(a, b) -> bool                     does an act appear on BOTH statements? (routing only; the merge
                                                 question belongs to merge_or_separate, which sees the members)
    j.duties(statement, atoms) -> {one_duty, outcomes, reason}      one duty, or fused with named parts + members
    j.dispose(atom, statements, home) -> {fate, task, why}          FILE under which numbered statement, or UNFILED/LEAVE
    j.merge_or_separate(nodes, edges, members_of) -> [groups]      MERGE (with basis) / SEPARATE / KEEP over a component
    j.sharpen(nodes, edges, members_of, frozen) -> {node: [candidate rewrites]}
    j.writing_defects(statement, atoms) -> {defect, principle, trigger, confidence}
    j.relation(x, y) -> {S,P,O,N: logprob}      the pair-relation judge, one order; relation_pair() averages both
    j.embed(texts) -> {text: unit vector}
    j.covers_many(pairs) / overlaps_many(pairs) / duties_many(rows) / dispose_many(items)   cache prefills

Every verdict is memoised on (model, prompt hash, inputs): THE KEY RECIPES ARE AN INTERFACE and are kept byte
for byte (the frozen tags OVLPVERIFY, COVREASONED, OD1, DISPOSE1, RESOLVEGROUP, SHARPENGROUP, WO4, PAIRREL and
the historical section names). `draw > 1` is an independent verdict in its own cache slot (concurrence), never
a replay. A failed call is never cached (NoCache / a raise). Reliability notes on each judge: git history.
"""
from __future__ import annotations

import collections
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from .cache import NoCache
from .engine import bare_model
from .lib import sha256, strip_json

SEP = "␟"
WRITING_PRINCIPLES = ("verb", "vocabulary", "fusion", "objects", "clauses", "counterpart", "acronym", "face_valid", "none")
RELATION_LETTERS = ("S", "P", "O", "N")
_SWAP = {"S": "P", "P": "S", "O": "O", "N": "N"}
_TO_LABEL = {"S": "ours_part", "P": "ours_wider", "O": "overlap", "N": "none"}
_PRIORITY = ("none", "overlap", "ours_part", "ours_wider")


def fixed_prefix(tmpl, *slots) -> str:
    """The part of a template before its first substituted slot — byte-identical on every call, so it is what
    `cache_prefix` and `prewarm` are handed."""
    at = [tmpl.find(x) for x in slots if tmpl.find(x) >= 0]
    return tmpl[:min(at)] if at else tmpl


def _chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


class Judges:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cfg = ctx.config
        self._overlap_instr = None
        self._covers_instr = None
        self._duties_tag = None
        self._relation_instr = None
        self._writing_version = None

    @property
    def workers(self) -> int:
        return self.ctx.workers_for("judges")

    def embed(self, texts):
        return self.ctx.embeddings(texts)

    def _pool(self, fn, items, workers=None):
        with ThreadPoolExecutor(max_workers=max(1, workers or self.workers)) as ex:
            return list(ex.map(fn, items))

    # ── overlaps ──────────────────────────────────────────────────────────────────────────────────
    @property
    def overlap_instr(self) -> str:
        if self._overlap_instr is None:
            self._overlap_instr = self.ctx.prompt("overlap_labels")
        return self._overlap_instr

    def _overlap_req(self, a, b, draw=1):
        a, b = sorted([a, b])
        model = self.cfg.model("overlaps")
        return (sha256(SEP.join(["OVLPVERIFY", model, sha256(self.overlap_instr), a, b] + ([f"D{draw}"] if draw > 1 else []))),
                f"{self.overlap_instr}\n\n- {a}\n- {b}")

    @staticmethod
    def _overlap_parse(text) -> str:
        out = (text or "").strip().lower()
        return "yes" if out.startswith(("y", "redundant", "partial")) else "no"

    def overlaps(self, a, b, draw=1) -> bool:
        """Does an act appear on BOTH statements? Uncertainty resolves to yes (a wrong yes costs one look at
        the members; a wrong no publishes an overlap)."""
        model = self.cfg.model("overlaps")
        key, prompt = self._overlap_req(a, b, draw)
        instr = self.overlap_instr
        self.ctx.engine.prewarm(instr, model)
        v = self.ctx.cache.memo("overlap_verify", key,
                                lambda: self._overlap_parse(self.ctx.engine.chat(prompt, model, max_out=2000, cache_prefix=instr)))
        return v != "no"

    def overlaps_many(self, pairs) -> int:
        """Fill the overlap cache for [(a, b)]: several pairs per call when batch.items is on, then the Batch API."""
        if not pairs:
            return 0
        n = self._overlaps_batched(pairs) if self.cfg.get("batch.items") else 0
        jobs = [self._overlap_req(a, b) for a, b in pairs]
        return n + self.ctx.batch.fill("overlap_verify", jobs, self.cfg.model("overlaps"), self._overlap_parse,
                                       max_out=2000, tag="overlap_labels")

    def _overlaps_batched(self, pairs) -> int:
        todo = [(a, b) for a, b in {tuple(sorted(p)) for p in pairs}
                if self.ctx.cache.get("overlap_verify", self._overlap_req(a, b)[0]) is None]
        chunks = [c for c in _chunks(sorted(todo), int(self.cfg.get("batch.overlap", 25))) if len(c) > 1]
        if not chunks:
            return 0
        tmpl = self.ctx.prompt("overlap_labels_batch")
        model = self.cfg.model("overlaps")

        def one(chunk):
            body = "\n".join(f"{i}.\n- {a}\n- {b}" for i, (a, b) in enumerate(chunk, 1))
            try:
                d = json.loads(strip_json(self.ctx.engine.chat(tmpl.replace("$pairs", body), model,
                                                               max_out=min(16000, 60 * len(chunk) + 2000), json_mode=True)) or "{}")
            except Exception as e:                        # noqa: BLE001
                print(f"  overlaps_many: call failed ({type(e).__name__}); {len(chunk)} pairs fall to single calls", file=sys.stderr)
                return 0
            n = 0
            for v in (d.get("verdicts") if isinstance(d, dict) else None) or []:
                try:
                    a, b = chunk[int(v.get("id")) - 1]
                except (TypeError, ValueError, IndexError, AttributeError):
                    continue
                ans = str(v.get("answer", "")).strip().lower()
                if not ans or ans[0] not in ("y", "n"):
                    continue
                self.ctx.cache.put("overlap_verify", self._overlap_req(a, b)[0], self._overlap_parse(ans))
                n += 1
            return n
        return sum(self._pool(one, chunks))

    # ── covers ────────────────────────────────────────────────────────────────────────────────────
    @property
    def covers_instr(self) -> str:
        if self._covers_instr is None:
            self._covers_instr = self.ctx.prompt("coverage")
        return self._covers_instr

    def _covers_req(self, atom, label, model=None):
        """(model, cache key, prompt) — THE one place either is built; the sync path and the prefills share it."""
        m = model or self.cfg.model("covers")
        return (m, sha256(SEP.join(["COVREASONED", m, sha256(self.covers_instr), atom, label])),
                f"{self.covers_instr}\n\nATOM: {atom}\nLABEL: {label}")

    @staticmethod
    def _covers_parse(text):
        """Reply -> the dict to cache, or None (= do not cache; an unparseable reply is not a 'no')."""
        try:
            d = json.loads(strip_json(text) or "{}")
        except Exception:                                 # noqa: BLE001
            return None
        return d if str(d.get("verdict", "")).strip().lower()[:1] in ("y", "n") else None

    def covers(self, atom, statement, model=None) -> bool:
        """Does `atom` fall UNDER `statement`? The model states act, object and the deciding rule before the
        verdict; the reasoning stays in the cache for audit. Do not tune it (eleven attempts landed in noise)."""
        m, key, prompt = self._covers_req(atom, statement, model)

        def compute():
            r = self._covers_parse(self.ctx.engine.chat(prompt, m, max_out=700, json_mode=True))
            if r is None:
                raise RuntimeError("covers: unusable reply — refusing to cache a non-verdict")
            return r
        return str(self.ctx.cache.memo("covers_reasoned", key, compute).get("verdict", "")).strip().lower().startswith("y")

    def covers_all(self, pairs, model=None) -> dict:
        """{(atom, statement): bool} over many pairs, prefilled then fanned out."""
        pairs = sorted(set(pairs))
        self.covers_many(pairs, model)
        return dict(zip(pairs, self._pool(lambda p: bool(self.covers(p[0], p[1], model)), pairs)))

    def covers_many(self, pairs, model=None) -> int:
        """Fill the coverage cache for [(atom, statement)] before a fan-out. -> n filled."""
        if not pairs:
            return 0
        n = self._covers_batched(pairs, model) if self.cfg.get("batch.items") else 0
        m = model or self.cfg.model("covers")
        jobs = [(k, p) for (_, k, p) in (self._covers_req(a, L, model) for a, L in pairs)]
        return n + self.ctx.batch.fill("covers_reasoned", jobs, m, self._covers_parse, max_out=700, json_mode=True,
                                       tag="covers_reasoned")

    def _covers_batched(self, pairs, model=None) -> int:
        m = model or self.cfg.model("covers")
        by_label = collections.defaultdict(set)
        for a, L in set(pairs):
            if self.ctx.cache.get("covers_reasoned", self._covers_req(a, L, model)[1]) is None:
                by_label[L].add(a)
        size = int(self.cfg.get("batch.covers", 40))
        jobs = [(L, chunk) for L, atoms in by_label.items() for chunk in _chunks(sorted(atoms), size) if len(chunk) > 1]
        if not jobs:
            return 0
        tmpl = self.ctx.prompt("coverage_batch")
        fields = ("label_act", "label_object", "atom_act", "atom_object", "same_act", "deciding_rule", "why", "verdict")

        def one(job):
            L, atoms = job
            prompt = tmpl.replace("$label", L).replace("$atoms", "\n".join(f"{i}. {a}" for i, a in enumerate(atoms, 1)))
            try:
                d = json.loads(strip_json(self.ctx.engine.chat(prompt, m, max_out=min(16000, 250 * len(atoms) + 400),
                                                               json_mode=True)) or "{}")
            except Exception as e:                        # noqa: BLE001
                print(f"  covers_many: call failed ({type(e).__name__}); {len(atoms)} atoms fall to single calls", file=sys.stderr)
                return 0
            n = 0
            for v in (d.get("verdicts") if isinstance(d, dict) else None) or []:
                try:
                    atom = atoms[int(v.get("id")) - 1]
                except (TypeError, ValueError, IndexError, AttributeError):
                    continue
                if str(v.get("verdict", "")).strip().lower()[:1] not in ("y", "n"):
                    continue
                rec = {k: v.get(k) for k in fields}
                rec["batched"] = len(atoms)
                self.ctx.cache.put("covers_reasoned", self._covers_req(atom, L, model)[1], rec)
                n += 1
            return n
        return sum(self._pool(one, jobs))

    # ── duties (ex one_duty) ──────────────────────────────────────────────────────────────────────
    @property
    def duties_tag(self) -> str:
        """The cache tag with the PROMPT HASH folded in; the occupation is pinned to a placeholder so the tag
        varies per prompt, never per occupation."""
        if self._duties_tag is None:
            base = str(self.cfg.get("models.duties.tag", "OD1"))
            try:
                self._duties_tag = base + SEP + sha256(self.ctx.prompt("one_duty", occupation="$OCC", statement="$S", members="$M"))[:12]
            except Exception:                             # noqa: BLE001
                self._duties_tag = base
        return self._duties_tag

    @staticmethod
    def _members_block(members) -> str:
        return "\n".join(f"- {m}" for m in sorted(members)) or "(no member activities)"

    @staticmethod
    def _duties_cap(members) -> int:
        """Output tokens for one verdict. A FUSED reply copies every member under its part (~20 tokens each), so a
        fixed 2000 truncated the verdict on large families, which failed open as one duty. Not in the cache key."""
        return 2000 + 30 * len(members)

    def duties_key(self, statement, members, draw=1) -> str:
        tag = self.duties_tag + (f"{SEP}D{draw}" if draw > 1 else "")
        return self.ctx.cache.pair_key("one_duty", self.cfg.model("duties"), tag, statement, self._members_block(members))

    @staticmethod
    def _outcomes(raw):
        out = []
        for x in (raw or []):
            if isinstance(x, dict):
                st = str(x.get("statement") or "").strip()
                if st:
                    out.append({"statement": st, "members": [str(m) for m in (x.get("members") or [])]})
            else:
                st = str(x).strip()
                if st:
                    out.append({"statement": st, "members": []})
        return out

    def _duties_parse(self, text):
        try:
            d = json.loads(strip_json(text) or "{}")
            if not isinstance(d.get("one_duty"), bool):
                return None
            return {"one_duty": d["one_duty"], "outcomes": self._outcomes(d.get("outcomes")), "reason": str(d.get("reason") or "")}
        except Exception:                                 # noqa: BLE001
            return None

    def duties(self, statement, members, draw=1) -> dict:
        """-> {one_duty: bool, outcomes: [{statement, members}], reason}. Fails OPEN (one_duty=True) and never
        caches the failure. Self-flips on ~16% of identical inputs: one verdict is a queue entry, not evidence."""
        model, effort = self.cfg.model("duties"), self.cfg.effort("duties")
        block = self._members_block(members)
        tag = self.duties_tag + (f"{SEP}D{draw}" if draw > 1 else "")
        prompt = self.ctx.prompt("one_duty", occupation=self.ctx.occupation, statement=statement, members=block)
        instr = fixed_prefix(self.ctx.prompt("one_duty", occupation=self.ctx.occupation), "$statement", "$members")

        def compute():
            try:
                d = json.loads(strip_json(self.ctx.engine.chat(prompt, model, json_mode=True, max_out=self._duties_cap(members),
                                                               effort=effort, cache_prefix=instr)) or "{}")
                if not isinstance(d.get("one_duty"), bool):
                    raise ValueError("no one_duty bool")
                return {"one_duty": d["one_duty"], "outcomes": self._outcomes(d.get("outcomes")), "reason": str(d.get("reason") or "")}
            except Exception as e:                        # noqa: BLE001 - fail open, never cache the failure
                return NoCache({"one_duty": True, "outcomes": [], "reason": "", "error": str(e)[:120]})
        self.ctx.engine.prewarm(instr, model)
        return self.ctx.cache.cached("one_duty", model, tag, statement, block, compute)

    def duties_many(self, rows) -> int:
        """Batch API prefill for [(statement, members)]."""
        if not rows:
            return 0
        jobs = [(self.duties_key(s, m), self.ctx.prompt("one_duty", occupation=self.ctx.occupation, statement=s,
                                                        members=self._members_block(m))) for s, m in rows]
        return self.ctx.batch.fill("one_duty", jobs, self.cfg.model("duties"), self._duties_parse,
                                   max_out=max(self._duties_cap(m) for _, m in rows), json_mode=True,
                                   effort=self.cfg.effort("duties"), tag="one_duty")

    # ── dispose ───────────────────────────────────────────────────────────────────────────────────
    def _dispose_text(self, atom, tasks, home=None, siblings=()):
        """(where text, siblings text, instr, prompt, key stem) — THE one place the disposer's prompt and key are built."""
        where = (f'This activity is currently filed under: "{home}"' if home
                 else "This activity is in the pile — it is filed under no task at all, so there is nothing "
                      "to leave it in and FILE must earn its place on the statement's own words.")
        sib = [m for m in siblings if m != atom][:12]
        sib_txt = ("The other activities already filed under that task:\n"
                   + "\n".join(f"  - {m}" for m in sib)) if sib else "(no other activities are filed there)"
        occ = self.ctx.occupation
        instr = self.ctx.prompt("dispose", occupation=occ, tasks=tasks, atom="", home="", home_members="")
        prompt = self.ctx.prompt("dispose", occupation=occ, tasks=tasks, atom=atom, home=where, home_members=sib_txt)
        return where, sib_txt, instr, prompt, "DISPOSE1" + self.cfg.model("dispose") + sha256(prompt)

    def dispose_key(self, atom, tasks, home=None, siblings=(), draw=1) -> str:
        stem = self._dispose_text(atom, tasks, home, siblings)[4]
        return sha256(stem + (f"|D{draw}" if draw > 1 else ""))

    def dispose(self, atom, tasks, home=None, siblings=(), draw=1) -> dict:
        """`tasks` is the numbered statement block; `home` the statement the atom sits under (None for a pile
        atom), `siblings` the other activities there. -> {fate: FILE|UNFILED|LEAVE, task: n, why}."""
        model = self.cfg.model("dispose")
        _w, _s, instr, prompt, stem = self._dispose_text(atom, tasks, home, siblings)
        self.ctx.engine.prewarm(instr, model)
        key = sha256(stem + (f"|D{draw}" if draw > 1 else ""))
        raw = self.ctx.cache.memo("found_dispose", key,
                                  lambda: self.ctx.engine.chat(prompt, model, max_out=300, json_mode=True, cache_prefix=instr))
        return json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))

    def dispose_many(self, items, draw=1) -> int:
        """Batched disposer (batch.items): `items` = [(atom, tasks, home, siblings)]; atoms sharing one `tasks`
        block go batch.dispose to a call, each verdict written under the single-call key. -> n written."""
        if not self.cfg.get("batch.items") or not items:
            return 0
        model = self.cfg.model("dispose")
        todo = collections.defaultdict(list)
        for atom, tasks, home, siblings in items:
            where, sib_txt, _, _, stem = self._dispose_text(atom, tasks, home, siblings)
            key = sha256(stem + (f"|D{draw}" if draw > 1 else ""))
            if self.ctx.cache.get("found_dispose", key) is None:
                todo[tasks].append((atom, where, sib_txt, key))
        size = int(self.cfg.get("batch.dispose", 20))
        jobs = [(tasks, chunk) for tasks, lst in todo.items() for chunk in _chunks(lst, size) if len(chunk) > 1]
        if not jobs:
            return 0
        tmpl = self.ctx.prompt("dispose_batch", occupation=self.ctx.occupation)

        def one(job):
            tasks, chunk = job
            acts = "\n".join(f"{i}. {atom}\n   {where}\n   {sib.replace(chr(10), chr(10) + '   ')}"
                             for i, (atom, where, sib, _k) in enumerate(chunk, 1))
            prompt = tmpl.replace("$tasks", tasks).replace("$activities", acts)
            try:
                d = json.loads(strip_json(self.ctx.engine.chat(prompt, model, json_mode=True,
                                                               max_out=min(8000, 120 * len(chunk) + 300))) or "{}")
            except Exception as e:                        # noqa: BLE001
                print(f"  dispose_many: call failed ({type(e).__name__}); {len(chunk)} atoms fall to single calls", file=sys.stderr)
                return 0
            n = 0
            for v in (d.get("verdicts") if isinstance(d, dict) else None) or []:
                try:
                    _atom, _w, _s, key = chunk[int(v.get("id")) - 1]
                except (TypeError, ValueError, IndexError, AttributeError):
                    continue
                fate = str(v.get("fate", "")).upper()
                if fate not in ("FILE", "UNFILED", "LEAVE"):
                    continue
                rec = json.dumps({"fate": fate, "task": v.get("task") if isinstance(v.get("task"), int) else None,
                                  "why": str(v.get("why") or "")[:80], "batched": len(chunk)})
                self.ctx.cache.put("found_dispose", key, rec)
                n += 1
            return n
        return sum(self._pool(one, jobs, self.ctx.workers_for("refile")))

    # ── merge_or_separate (ex resolve_group) ──────────────────────────────────────────────────────
    def merge_or_separate(self, nodes, edges, members_of, strain=None, draw=1):
        """ONE joint KEEP/SEPARATE/MERGE decision for a component of colliding statements. `strain` =
        (evidence lines, key tokens) from the sharpen ledger. -> [{"members", "decision", "relation",
        "proposed", "divider", "basis"[, "contested"]}] covering every node exactly once (a node listed twice keeps its
        first decision; a node left out is KEEP), or None when the reply does not parse or names an unknown node. Dominated by its own variance: the caller demands
        a second draw (draw=2) before a MERGE applies."""
        model = self.cfg.model("merge")
        idx = {t: i for i, t in enumerate(nodes, 1)}
        tmpl = self.ctx.prompt("resolve_group", occupation=self.ctx.occupation)
        tasks = "\n".join(f"{idx[t]} — {t} — " + "; ".join(sorted(members_of[t])) for t in nodes)
        W = self.ctx.atoms.sources
        wk = {t: set().union(set(), *[set(W.get(m, ())) for m in members_of[t]]) for t in nodes}

        def wline(a, b):
            sh, small = len(wk[a] & wk[b]), max(1, min(len(wk[a]), len(wk[b])))
            verdict = ("DIFFERENT people" if sh <= 1 else f"SAME people — {sh}/{small} of the smaller task's workers also hold the other")
            return f"{idx[a]}-{idx[b]}: {sh} shared worker(s) of {len(wk[a])} and {len(wk[b])} — {verdict}"
        strain_lines, strain_tokens = strain or ([], [])
        workers_txt = "\n".join([wline(a, b) for a, b in edges] + list(strain_lines)) or "(none)"
        clash = ", ".join(f"{idx[a]}-{idx[b]}" for a, b in edges)
        # $redundant renders empty and "" holds its place in the key — both deliberate, both load-bearing for
        # every cached verdict (see git history: this resolver is perturbation-sensitive).
        prompt = (tmpl.replace("$clash", clash).replace("$tasks", tasks).replace("$redundant", "").replace("$workers", workers_txt))
        key = sha256(SEP.join(["RESOLVEGROUP", model, sha256(tmpl), clash, "", "MEMWINDOW=all"] + list(strain_tokens)
                              + list(nodes) + [sha256(SEP.join(sorted(members_of[t]))) for t in nodes]))
        if draw > 1:
            key = sha256(key + "|CONC2")
        instr = fixed_prefix(tmpl, "$clash", "$tasks", "$redundant", "$workers")

        def compute():
            self.ctx.engine.prewarm(instr, model)
            return json.loads(strip_json(self.ctx.engine.chat(prompt, model, max_out=4000, cache_prefix=instr)))
        try:
            d = self.ctx.cache.memo("mece_resolve_group", key, compute)
        except (ValueError, KeyError, TypeError) as e:
            print(f"    merge_or_separate: unparseable reply ({e}) — component left alone", file=sys.stderr)
            return None
        gs = d.get("groups") if isinstance(d, dict) else None
        if not isinstance(gs, list):
            return None
        rev = {str(i): t for t, i in idx.items()}
        edge_set = {tuple(e) for e in edges} | {tuple(e[::-1]) for e in edges}
        out, seen = [], set()
        for g in gs:
            ids = g.get("ids") if isinstance(g, dict) else None
            if not isinstance(ids, list) or not ids:
                return None
            mem = [rev.get(str(i)) for i in ids]
            if any(m is None for m in mem):
                return None                             # an unknown number
            # a statement listed twice keeps its first decision (rejecting the whole reply over one repeat left
            # the whole component undecided)
            mem = list(dict.fromkeys(m for m in mem if m not in seen))
            if not mem:
                continue
            seen.update(mem)
            dec = str(g.get("decision") or "").upper()
            rel = str(g.get("relation") or "").upper()
            prop = str(g.get("proposed") or g.get("statement") or "").strip()
            if dec == "MERGE" and len(mem) == 1:
                dec = "SEPARATE"
            if dec == "MERGE":
                if len(mem) < 2 or rel not in ("EQUIVALENT", "CONTAINMENT"):
                    return None
            elif dec in ("SEPARATE", "KEEP"):
                if len(mem) != 1:
                    return None
            else:
                return None
            rec = {"members": mem, "decision": dec, "relation": rel, "proposed": prop,
                   "divider": str(g.get("divider") or "")[:200], "basis": str(g.get("basis") or "")[:400]}
            if dec in ("SEPARATE", "KEEP"):
                me = mem[0]
                for other in nodes:
                    if other == me:
                        continue
                    sh = len(wk[me] & wk[other])
                    if sh <= 1 and len(wk[me]) >= 2 and len(wk[other]) >= 2 and (me, other) in edge_set:
                        rec["contested"] = f"different-people evidence ({sh} shared of {len(wk[me])}|{len(wk[other])}) vs SEPARATE verdict"
            out.append(rec)
        # a statement the reply left out is kept as it is (rejecting the whole reply over one omission left the
        # whole component, duplicates included, undecided)
        out += [{"members": [t], "decision": "KEEP", "relation": "", "proposed": "", "divider": "",
                 "basis": "left out of the resolver's reply"} for t in nodes if t not in seen]
        return out

    # ── sharpen ───────────────────────────────────────────────────────────────────────────────────
    def sharpen(self, nodes, edges, members_of, frozen=()):
        """ONE joint rewrite proposal for a component: -> {node: [candidate statements]} for the movable nodes,
        or None when the reply does not parse. `frozen` nodes are SHOWN but may not move. The caller gates the
        candidates with the writer's form judge."""
        model, effort = self.cfg.model("sharpen"), self.cfg.effort("sharpen")
        idx = {t: i for i, t in enumerate(nodes, 1)}
        tmpl = self.ctx.prompt("sharpen", occupation=self.ctx.occupation)
        tasks = "\n".join(f"{idx[t]} — {t} — " + "; ".join(sorted(set(members_of[t]))) for t in nodes)
        clash = ", ".join(f"{idx[a]}-{idx[b]}" for a, b in edges)
        frz = sorted(idx[t] for t in frozen if t in idx)
        frozen_txt = ("" if not frz else
                      f"TASK{'S' if len(frz) > 1 else ''} {', '.join(str(i) for i in frz)} "
                      f"{'ARE' if len(frz) > 1 else 'IS'} ALREADY PUBLISHED AND CANNOT CHANGE. Only the other "
                      "tasks may be rewritten, so the separator you choose must be one THEY can carry on their "
                      "own. If the only way to tell a pair apart is to reword a frozen task, say so by leaving "
                      "the movable one unchanged rather than writing a rewrite that does not separate them.")
        prompt = tmpl.replace("$clash", clash).replace("$tasks", tasks).replace("$frozen", frozen_txt)
        key = sha256(SEP.join(["SHARPENGROUP", model, sha256(tmpl), clash, "MEMWINDOW=all", sha256(frozen_txt)]
                              + list(nodes) + [sha256(SEP.join(sorted(set(members_of[t])))) for t in nodes]))
        instr = fixed_prefix(tmpl, "$clash", "$tasks", "$frozen")

        def compute():
            self.ctx.engine.prewarm(instr, model)
            d = json.loads(strip_json(self.ctx.engine.chat(prompt, model, max_out=4000, effort=effort, cache_prefix=instr)))
            return {"sep": d.get("sep") or {}, "h": d.get("h") or {}}
        try:
            d = self.ctx.cache.memo("mece_sharpen_group", key, compute)
        except (ValueError, KeyError, TypeError) as e:
            print(f"    sharpen: unparseable reply ({e})", file=sys.stderr)
            return None
        rev = {str(i): t for t, i in idx.items()}
        out = {}
        for k, v in (d.get("h") or {}).items():
            if k not in rev or rev[k] in set(frozen):
                continue
            cands = [str(c).strip() for c in (v if isinstance(v, list) else [v]) if str(c).strip()]
            if cands:
                out[rev[k]] = cands
        return out

    # ── writing defects (the restored writing judge) ──────────────────────────────────────────────
    @property
    def writing_version(self) -> str:
        if self._writing_version is None:
            self._writing_version = sha256(self.ctx.prompt("writing_only", title="", statement="", own_tasks=""))[:12]
        return self._writing_version

    def writing_defects(self, statement, atoms, exclude=()) -> dict:
        """Will an expert object to HOW this statement is written? ONE defect per statement (verb, vocabulary,
        fusion, objects, clauses, counterpart, acronym, face_valid) with a confidence; `exclude` names
        principles the caller cannot act on. -> {defect, principle, trigger, confidence}; fails open, uncached."""
        model, effort = self.cfg.model("writing"), self.cfg.effort("writing")
        tag = str(self.cfg.get("models.writing.tag", "WO4"))
        title = self.ctx.occupation
        own = "\n".join(f"- {m}" for m in sorted(atoms))
        prompt = self.ctx.prompt("writing_only", title=title, statement=statement, own_tasks=own)
        if exclude:
            prompt += ("\n\nDO NOT REPORT " + " or ".join(sorted(exclude)).upper() + " for this statement — "
                       "that objection is already known and handled elsewhere. Report the most serious OTHER "
                       "defect, or no defect if nothing else rises to one.")

        def compute():
            try:
                d = json.loads(strip_json(self.ctx.engine.chat(prompt, model, json_mode=True, max_out=2000, effort=effort)) or "{}")
                if not isinstance(d.get("defect"), bool):
                    raise ValueError("no defect bool")
                pr = str(d.get("principle", "none"))
                return {"defect": d["defect"], "principle": pr if pr in WRITING_PRINCIPLES else "none",
                        "trigger": str(d.get("trigger", ""))[:120], "confidence": float(d.get("confidence") or 0.5)}
            except Exception as e:                        # noqa: BLE001
                return NoCache({"defect": False, "principle": "none", "trigger": "", "confidence": 0.0, "error": str(e)[:120]})
        xt = ("¬" + ",".join(sorted(exclude))) if exclude else ""
        return self.ctx.cache.cached("writing_only", model, f"{tag}:{self.writing_version}{xt}", statement, title, compute)

    # ── relation (the pair-relation judge) ────────────────────────────────────────────────────────
    @property
    def relation_instr(self) -> str:
        if self._relation_instr is None:
            self._relation_instr = self.ctx.prompt(str(self.cfg.get("models.relation.prompt", "pair_relation_paper")))
        return self._relation_instr

    def relation_key(self, x, y, model=None) -> str:
        m = model or self.cfg.model("relation")
        return sha256(SEP.join(["PAIRREL", bare_model(m), sha256(self.relation_instr), x, y]))

    @staticmethod
    def _parse_top(top):
        mass = collections.defaultdict(float)
        for t in top:
            tok = str(t.token).strip().upper()
            if tok in RELATION_LETTERS:
                mass[tok] += math.exp(t.logprob)
        if not mass:
            return None
        floored = {l: max(mass[l], 1e-9) for l in RELATION_LETTERS}
        tot = sum(floored.values())
        return {l: math.log(floored[l] / tot) for l in RELATION_LETTERS}

    def relation(self, x, y, model=None) -> dict:
        """One ordered pair -> {S, P, O, N: log-probability} read off the first token's top-20 logprobs:
        S = X is a part of Y, P = X is wider than Y, O = overlap, N = none."""
        m = model or self.cfg.model("relation")

        def compute():
            kw = self.ctx.engine.chat_kwargs(m, 1, logprobs=True)
            kw["top_logprobs"] = 20
            msgs = [{"role": "system", "content": self.relation_instr},
                    {"role": "user", "content": f"TASK X: {x}\nTASK Y: {y}\n\nAnswer with one letter: S, P, O or N."}]
            r = self.ctx.engine.create(m, msgs, **kw)
            try:
                top = r.choices[0].logprobs.content[0].top_logprobs
            except (AttributeError, IndexError, TypeError):
                top = []
            lp = self._parse_top(top)
            if lp is None:
                raise RuntimeError("no answer letter in the top logprobs")
            return lp
        return self.ctx.cache.memo("pair_relation", self.relation_key(x, y, m), compute)

    def relation_pair(self, ours, other, model=None) -> dict:
        """Both orders averaged, S and P swapped back -> {"label": ours_part|ours_wider|overlap|none, "p": {...}}."""
        lp_xy, lp_yx = self.relation(ours, other, model), self.relation(other, ours, model)
        avg = {_TO_LABEL[l]: 0.5 * (lp_xy[l] + lp_yx[_SWAP[l]]) for l in RELATION_LETTERS}
        mx = max(avg.values())
        z = sum(math.exp(v - mx) for v in avg.values())
        p = {c: math.exp(v - mx) / z for c, v in avg.items()}
        label = next(c for c in _PRIORITY if avg[c] >= mx - 1e-9)
        return {"label": label, "p": {c: round(v, 4) for c, v in p.items()}}
