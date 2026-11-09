"""evaluate — the evaluations of a built list, each a function of the list and the data it needs. Nothing here
knows what O*NET is: a comparison list is a list of statements the caller passes in.

    lists = {"ours": statements_of(build), "reference": [...], "other": [...]}   # the inventories compared
    agg.evaluate.coverage(lists, docs)          held-out atoms covered by each list (source bootstrap)
    agg.evaluate.silhouette(lists, docs, pool)  held-out tasks tighter under one list's statements than another's?
    agg.evaluate.incidence(lists, docs)         docs x statements incidence per list, PCA / CA, covariate correlations
    agg.evaluate.crosswalk(ours, other)         new / refined / related for each statement of `ours` against `other`
    agg.evaluate.exclusivity(lists)             related pairs WITHIN each list
    agg.evaluate.occupational(lists, title, description, related)   this occupation's task, or a related one's?
    agg.evaluate.exposure(lists, title)         AI-exposure rubrics per statement
    agg.evaluate.equal_length(coverage, docs, lists, pool)   the lists compared at the same length
    agg.evaluate.discovery(rows)                what distinguishes the tasks one method finds (across occupations)

The FIRST key of `lists` is "ours": every gap is reported as ours minus the other list.
"""
from __future__ import annotations


def statements_of(build):
    """The statements of a build dict (final.json / build.json), or a list passed through."""
    if isinstance(build, dict):
        return [c.get("statement") or c["head"] for c in (build.get("core") or []) if (c.get("statement") or c.get("head"))]
    return list(build)


class Evaluate:
    def __init__(self, agg):
        self.agg = agg

    @property
    def ctx(self):
        return self.agg.context(self.agg._last_atoms) if self.agg._last_atoms is not None else self.agg._root

    def coverage(self, lists, docs, **kw):
        from . import coverage
        return coverage.run(self.ctx, lists, docs, **kw)

    def silhouette(self, lists, docs, pool, **kw):
        from . import silhouette
        return silhouette.run(self.ctx, lists, docs, pool, **kw)

    def incidence(self, lists, docs, **kw):
        from . import incidence
        return incidence.run(self.ctx, lists, docs, **kw)

    def crosswalk(self, ours, other, **kw):
        from . import crosswalk
        return crosswalk.run(self.ctx, ours, other, **kw)

    def exclusivity(self, lists, **kw):
        from . import exclusivity
        return exclusivity.run(self.ctx, lists, **kw)

    def occupational(self, lists, title, description="", related=(), **kw):
        from . import occupational
        return occupational.run(self.ctx, lists, title, description, related, **kw)

    def exposure(self, lists, title, **kw):
        from . import exposure
        return exposure.run(self.ctx, lists, title, **kw)

    def equal_length(self, coverage=None, docs=None, lists=None, pool=None, **kw):
        from . import equal_length
        return equal_length.run(self.ctx, coverage=coverage, docs=docs, lists=lists, pool=pool, **kw)

    def discovery(self, rows, **kw):
        from . import discovery
        return discovery.run(self.ctx, rows, **kw)
