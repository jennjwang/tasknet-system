"""crosswalk — the pair-relation judge over every (ours, other) pair: each statement of `ours` is new / refined
/ related against `other`, and the statements of `other` no statement of ours relates to are missing.

    frame = crosswalk.run(ctx, ours, other)

Each pair is asked in both orders (ctx.judges.relation_pair: S / P / O / N on the first token's logprobs,
averaged). Class: refined if any pair is S (ours is a subtask), related if any pair is P or O, new if every
pair is N. `compare_missing` sets two frames' missing lists against each other.
"""
from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor

CLASS_OF = {"ours_part": "refined", "ours_wider": "related", "overlap": "related", "none": "new"}
CLASS_RANK = {"refined": 0, "related": 1, "new": 2}
REL_WORD = {"ours_part": "refines", "ours_wider": "contains", "overlap": "overlaps"}


def classify(labels_by_statement):
    """{statement: [labels]} -> {statement: class} with precedence refined > related > new."""
    out = {}
    for s, labels in labels_by_statement.items():
        classes = {CLASS_OF[l] for l in labels} or {"new"}
        out[s] = min(classes, key=CLASS_RANK.get)
    return out


def run(ctx, ours, other, *, workers=32):
    """Judge every (ours, other) pair -> {k_ours, k_target, classes, counts, target_missing, target_related, pairs}."""
    pairs = [(s, o) for s in ours for o in other]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        verdicts = dict(zip(pairs, ex.map(lambda p: ctx.judges.relation_pair(*p), pairs)))
    by_s, by_o = collections.defaultdict(list), collections.defaultdict(list)
    for (s, o), v in verdicts.items():
        by_s[s].append(v["label"])
        by_o[o].append(v["label"])
    cls = classify(by_s)
    missing = sorted(o for o in other if all(l == "none" for l in by_o[o]))
    return {"model": ctx.model("relation"), "k_ours": len(ours), "k_target": len(other), "classes": cls,
            "counts": dict(collections.Counter(cls.values())), "target_missing": missing,
            "target_related": len(other) - len(missing),
            "pairs": [{"ours": s, "target": o, "label": v["label"], "p": v["p"]} for (s, o), v in verdicts.items() if v["label"] != "none"]}


def frame_from_pairs(pairs, ours, other):
    """A frame of the same shape from already-judged pairs [{ours, target, label}] (e.g. a census)."""
    by_s, by_o = collections.defaultdict(list), collections.defaultdict(list)
    for r in pairs:
        if r["target"] in set(other):
            by_s[r["ours"]].append(r["label"])
            by_o[r["target"]].append(r["label"])
    cls = classify({s: by_s.get(s, []) for s in ours} if ours else by_s)
    missing = sorted(o for o in other if all(l == "none" for l in by_o.get(o, ["none"])))
    return {"k_ours": len(cls), "k_target": len(other), "classes": cls, "counts": dict(collections.Counter(cls.values())),
            "target_missing": missing, "target_related": len(other) - len(missing),
            "pairs": [r for r in pairs if r["label"] != "none"]}


def compare_missing(a_missing, b_missing, total):
    """Two lists measured against the same target: which of its statements each one misses."""
    a, b = set(a_missing), set(b_missing)
    return {"target_total": total, "missing_both": len(a & b), "missing_a_only": len(a - b), "missing_b_only": len(b - a),
            "related_both": total - len(a | b), "jaccard_missing": (len(a & b) / len(a | b)) if (a | b) else 1.0}
