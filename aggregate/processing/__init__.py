"""processing — raw task texts into atoms: lines, normalize (filter / rewrite / debrand / split), dedup, condense."""
from .lines import lines
from .normalize import normalize
from .dedup import dedup
from .condense import condense

__all__ = ["lines", "normalize", "dedup", "condense"]
