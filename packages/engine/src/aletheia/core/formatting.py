"""Rendering stored numbers for people, without changing them.

``facts.value`` is ``DECIMAL(38,10)``, so an EPS of 5.36 comes back as
``5.3600000000``. Ten decimal places on an earnings figure is not precision, it is
noise that reads as false precision -- and it was the first thing a reader saw in
the demo output.

Two rules:

* **Never lose a digit that was reported.** Trailing zeros from the storage scale
  are an artefact of the column; digits the filer actually reported are not.
  Stripping is only ever applied to the former.
* **Never render scientific notation.** ``Decimal("100").normalize()`` is
  ``1E+2``, which is correct and useless in a table. The ``f`` presentation type
  expands it back.

Money stays ``Decimal`` everywhere upstream; this is the last step before a
string, and it returns a string so the value cannot be silently re-parsed as a
float somewhere downstream.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = ["abbreviate", "plain"]


def plain(value: Decimal) -> str:
    """Exact decimal text, without the storage scale's trailing zeros.

    ``5.3600000000`` becomes ``5.36``; ``100`` stays ``100``; ``0.000000000`` and
    other zeros become ``0``. A value that genuinely carries ten decimals keeps
    all ten.
    """
    normalized = value.normalize()
    if normalized == 0:
        # normalize() maps every zero to 0E+n; the exponent is meaningless here.
        return "0"
    return format(normalized, "f")


def abbreviate(value: Decimal) -> str:
    """Readable at a glance for balance-sheet magnitudes.

    Only for display where the exact figure is available elsewhere -- a table that
    also carries an accession number, say. Anything that will be compared,
    summed, or quoted as a result uses :func:`plain`, because ``13.448B`` has
    silently dropped six digits.
    """
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:,.3f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:,.3f}M"
    return plain(value)
