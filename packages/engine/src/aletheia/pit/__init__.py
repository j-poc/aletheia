"""Point-in-time access. The ONLY door between stored data and research code.

``features/``, ``research/`` and ``book/`` import from here and never from
:mod:`aletheia.store`. That boundary is checked by
``tests/unit/test_lookahead_guard.py``, which walks the import graph of those
packages and fails if anything downstream reaches the warehouse directly.
"""

from aletheia.pit.view import (
    INSTANT,
    PeriodStart,
    PitEntity,
    PitFact,
    PitFiling,
    PitPrice,
    PitView,
    Revision,
    as_of,
)

__all__ = [
    "INSTANT",
    "PeriodStart",
    "PitEntity",
    "PitFact",
    "PitFiling",
    "PitPrice",
    "PitView",
    "Revision",
    "as_of",
]
