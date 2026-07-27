"""Typed error hierarchy.

Every failure mode in this system is one of these. A bare ``Exception`` escaping
into a caller is a bug: the caller cannot tell a transient network fault from a
data-integrity violation, and those demand opposite responses (retry vs. stop).
"""

from __future__ import annotations


class AletheiaError(Exception):
    """Base for every error this package raises deliberately."""


# ------------------------------------------------------------------ config ---


class ConfigError(AletheiaError):
    """Configuration is missing or invalid. Never carries a secret value."""


# ------------------------------------------------------------------ source ---


class SourceError(AletheiaError):
    """A data source misbehaved."""

    def __init__(self, message: str, *, source: str, uri: str | None = None) -> None:
        super().__init__(message)
        self.source = source
        self.uri = uri


class TransientSourceError(SourceError):
    """Retryable: timeout, 429, 5xx. Callers may back off and retry."""


class PermanentSourceError(SourceError):
    """Not retryable: 401/403/404, or a payload that will never parse.

    Distinguished from :class:`TransientSourceError` because retrying a 403 burns
    quota and hides the real problem (a credential or an entitlement gap).
    """


class ContractViolation(SourceError):
    """The source returned a shape we do not recognise.

    Raised the moment an upstream payload stops matching the contract that was
    verified live when the client was written. Loud failure here is deliberate:
    silent coercion of an unexpected shape is how bad numbers enter a warehouse.
    """


# ------------------------------------------------------------------- store ---


class StoreError(AletheiaError):
    """Persistence-layer failure."""


class MigrationError(StoreError):
    """Schema migration could not be applied."""


class IntegrityViolation(StoreError):
    """Stored data contradicts an invariant this system guarantees."""


# --------------------------------------------------------------------- PIT ---


class LookaheadViolation(AletheiaError):
    """A value was observed that was not knowable at the stated knowledge date.

    This is the error the whole system exists to make impossible. It is raised by
    the runtime canary in :mod:`aletheia.pit`, never caught internally, and never
    downgraded to a warning.
    """

    def __init__(self, message: str, *, as_of: object, offending_filed_at: object) -> None:
        super().__init__(message)
        self.as_of = as_of
        self.offending_filed_at = offending_filed_at


class InsufficientData(AletheiaError):
    """The requested fact exists in no filing on or before the knowledge date.

    Distinct from an empty result by accident: callers must decide explicitly
    whether absence-of-evidence is tradeable, rather than silently receiving a
    NaN that propagates into a Sharpe ratio.
    """


class AmbiguousPeriod(AletheiaError):
    """One ``period_end`` matched more than one reporting period.

    A fiscal year and its fourth quarter end on the same day. Apple's FY2015 net
    income is $53.394B over 363 days and its Q4 2015 net income is $11.124B over
    90 days, and both are tagged ``period_end = 2015-09-26`` with
    ``report_seq = 1``. A query keyed on the end date alone matches both.

    Returning either one silently is how an accruals ratio ends up dividing a
    quarter's earnings by a year's cash flow and reporting it as a finding. So the
    ambiguity is raised instead, naming the candidates, and the caller states which
    period it meant.
    """

    def __init__(self, message: str, *, candidates: tuple[object, ...] = ()) -> None:
        super().__init__(message)
        self.candidates = candidates
