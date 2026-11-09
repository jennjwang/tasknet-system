"""DocSet — the held-out contract: documents (a posting, an interview) with their extracted atoms and covariates.

    {"slug": "...", "source": "postings|interviews",
     "docs": [{"id": "job_1", "split": "heldout"|"construction", "text": "...", "atoms": ["..."],
               "meta": {"employer": "rcid:42", "salary": 91000, "seniority": 3, "year": 2024}}]}

A source writes this file; every evaluation step reads it. `heldout` and `construction` are the two splits;
`covariates()` lists the numeric meta keys the incidence step correlates with.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..lib import atomic_write


class DocSet:
    def __init__(self, docs, slug="", source=""):
        self.docs = [dict(d) for d in docs]
        self.slug, self.source = slug, source
        for d in self.docs:
            d.setdefault("split", "heldout")
            d.setdefault("atoms", [])
            d.setdefault("meta", {})

    @classmethod
    def read(cls, path) -> "DocSet":
        d = json.loads(Path(path).read_text())
        return cls(d.get("docs") or [], d.get("slug", ""), d.get("source", ""))

    def write(self, path) -> None:
        atomic_write(path, {"slug": self.slug, "source": self.source, "docs": self.docs})

    @property
    def heldout(self) -> list[dict]:
        return [d for d in self.docs if d.get("split") == "heldout"]

    @property
    def construction(self) -> list[dict]:
        return [d for d in self.docs if d.get("split") == "construction"]

    def tasks(self, split="heldout") -> list[dict]:
        """[{source, text}] — one row per (document, distinct atom) of the split."""
        out = []
        for d in (self.heldout if split == "heldout" else self.construction if split == "construction" else self.docs):
            seen = set()
            for t in d.get("atoms") or []:
                t = " ".join(str(t).split()).strip()
                if t and t not in seen:
                    seen.add(t)
                    out.append({"source": d["id"], "text": t})
        return out

    def covariates(self) -> list[str]:
        keys = set()
        for d in self.docs:
            for k, v in (d.get("meta") or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    keys.add(k)
        return sorted(keys)

    def __len__(self):
        return len(self.docs)
