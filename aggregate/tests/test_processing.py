"""lines, the split guard and coalesce, dedup, condense (key merge, blocked cosine merge)."""
import numpy as np

from tasknet import Atoms
from tasknet.processing import condense, dedup, lines
from tasknet.processing.normalize import _pieces, coalesce, valid_split


def test_lines_single_line_is_unchanged_and_lists_split():
    assert lines("Mentor junior engineers though pairing.") == ["Mentor junior engineers though pairing."]
    assert lines("  Fix leaks  ") == ["Fix leaks"]
    raw = "- Mentor junior engineers\n- Answer support questions\n- Write unit tests\n- Evaluate new tools"
    assert lines(raw) == ["Mentor junior engineers", "Answer support questions", "Write unit tests", "Evaluate new tools"]
    assert lines("My primary responsibilities include:\n1. Designing new features\n2) Implement backend services\nRespond to bugs") == \
        ["Designing new features", "Implement backend services", "Respond to bugs"]
    assert lines("Testing\n\n- Write unit tests\n-\n") == ["Write unit tests"]
    assert lines("Testing\nDebugging") == ["Testing Debugging"]


def test_split_guard():
    assert not valid_split(["Document their symptoms", "Document patient's medications", "document prior care received"])
    assert valid_split(["Monitor the waiting room", "Order xrays before a patient is seen"])
    assert valid_split(["Interpret ECG's"])
    assert _pieces({"tasks": ["  Fix   leaks ", "Fix leaks", "x", ""]}) == ["Fix leaks"]


def test_coalesce():
    raw = ("Obtain a patient medical history and document their symptoms. Also document patient's "
           "medications, allergies, and prior care received.")
    pieces = ["Obtain a patient medical history", "Document their symptoms", "Document patient's medications",
              "Document patient's allergies", "Document prior care received"]
    assert coalesce(raw, pieces) == ["Obtain a patient medical history", "Document their symptoms",
                                     "Document patient's medications, patient's allergies, and prior care received"]
    raw = "I spend hours reviewing mail, more than 1000 a day. Separately I review scientific journals that may affect my work."
    pieces = ["Review mail", "Review scientific journals that may affect my work"]
    assert coalesce(raw, pieces) == pieces
    raw = "Tell the oncoming doctor what's going on with them, where they are in their workup, and what they need to do to finish it off."
    pieces = ["Tell the oncoming doctor what's going on with them", "Tell the oncoming doctor where they are in their workup",
              "Tell the oncoming doctor what they need to do to finish it off"]
    assert coalesce(raw, pieces) == ["Tell the oncoming doctor what's going on with them, where they are in their workup, and what they need to do to finish it off"]
    assert coalesce("Review articles and scientific updates in one sitting.", ["Review articles", "Review scientific updates"]) == ["Review articles and scientific updates"]


def test_dedup_within_worker_only():
    a = Atoms([{"source": "s1", "task": "Do x."}, {"source": "s1", "task": "Do x. "}, {"source": "s2", "task": "Do x."}])
    assert len(dedup(a).rows) == 2


def _row(sid, text, fn, verb, obj):
    return {"source": sid, "occupation": "1", "title": "T", "status": "extracted", "task": text, "original": "",
            "duty": "d", "function": fn, "level": "", "verb": verb, "object": obj}


def test_condense_key_merge_then_blocked_cosine_merge():
    rows = [_row("e1", "Process vendor invoices for payment.", "data", "process", "vendor invoices"),
            _row("e2", "Process vendor invoices using the ledger.", "data", "process", "vendor invoice"),
            _row("e2", "Process vendor invoices for payment.", "data", "process", "vendor invoices"),
            _row("e3", "Greet arriving customers at the counter.", "people", "greet", "arriving customers"),
            _row("e4", "Greet guests at the front counter.", "people", "greet", "guests"),
            _row("e5", "Operate the dish machine.", "things", "operate", "dish machine")]
    vec = {"Process vendor invoices for payment.": [1, 0, 0], "Process vendor invoices using the ledger.": [1, 0, 0],
           "Greet arriving customers at the counter.": [0, 1, 0], "Greet guests at the front counter.": [0, 0.99, 0.14],
           "Operate the dish machine.": [0, 0.99, 0.14]}
    embed = lambda texts: {t: np.array(vec[t]) / np.linalg.norm(vec[t]) for t in texts}
    out, mapping = condense(None, Atoms(rows), tau=0.92, cap=50, embed=embed)
    atoms = set(out.tasks)
    assert "Process vendor invoices for payment." in atoms and "Process vendor invoices using the ledger." not in atoms
    assert sorted(out.sources["Process vendor invoices for payment."]) == ["e1", "e2"]
    assert len([a for a in atoms if a.startswith("Greet")]) == 1 and "Operate the dish machine." in atoms
    reasons = {m["original"]: m["reason"] for m in mapping}
    assert reasons["Process vendor invoices using the ledger."].startswith("key ") and any(v.startswith("cos") for v in reasons.values())
