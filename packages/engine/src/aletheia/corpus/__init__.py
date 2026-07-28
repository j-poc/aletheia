"""Measurements *of* the corpus, taken on the store side of the lookahead boundary.

Everything in :mod:`aletheia.features`, :mod:`aletheia.research` and
:mod:`aletheia.book` reads through :mod:`aletheia.pit`, which filters to a single
knowledge date. That is the guarantee the system exists to make.

This package is the deliberate exception, and it is quarantined rather than
excused. Its questions are *about* the revision history itself -- how much of the
panel standing today differs from what was public at the time -- so answering
them requires seeing every vintage at once, which is precisely what ``as_of``
hides. A module that can see all vintages must not be reachable from anything
that produces a trading signal, because a signal that read the latest value of a
fact would be reading the future.

So the import boundary treats ``aletheia.corpus`` exactly like
``aletheia.store``: forbidden to the research packages, enforced in
``tests/unit/test_lookahead_guard.py``. The study scripts import it directly,
which is allowed because a study script is not a signal. If a feature ever needs
a number from here, the answer is not to relax the boundary -- it is that the
number has to be computed as of a knowledge date, which makes it a different
number.
"""

from aletheia.corpus.contamination import (
    QUANTILES,
    THRESHOLDS,
    Contamination,
    CrossGrainSpread,
    UnitClass,
    contamination_by_unit_class,
    cross_grain_spread,
    measure_contamination,
)

__all__ = [
    "QUANTILES",
    "THRESHOLDS",
    "Contamination",
    "CrossGrainSpread",
    "UnitClass",
    "contamination_by_unit_class",
    "cross_grain_spread",
    "measure_contamination",
]
