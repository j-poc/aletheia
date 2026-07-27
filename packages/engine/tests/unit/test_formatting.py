"""Rendering stored decimals without changing them.

DECIMAL(38,10) returns an EPS of 5.36 as 5.3600000000. Ten decimals on an
earnings figure reads as precision that is not there, and it was the first thing
a reader saw in the demo output. The risk in fixing that is stripping a digit the
filer actually reported, so these tests push from both sides.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aletheia.core.formatting import abbreviate, plain


class TestPlain:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ("5.3600000000", "5.36"),
            ("6.7800000000", "6.78"),
            ("100.0000000000", "100"),
            ("0E-10", "0"),
            ("0.0000000000", "0"),
            ("-2.5000000000", "-2.5"),
            ("39572000000.0000000000", "39572000000"),
        ],
    )
    def test_storage_scale_zeros_are_stripped(self, stored: str, expected: str) -> None:
        assert plain(Decimal(stored)) == expected

    @pytest.mark.parametrize(
        "reported",
        ["0.1234567891", "1.0000000001", "-0.0000000001", "2563579586000000000"],
    )
    def test_digits_the_filer_reported_are_never_lost(self, reported: str) -> None:
        """The other side of the same coin. Round-trips exactly."""
        assert Decimal(plain(Decimal(reported))) == Decimal(reported)

    def test_no_scientific_notation_for_round_magnitudes(self) -> None:
        """normalize() alone turns 100 into 1E+2, which is correct and unreadable."""
        for value in ("100", "1000", "1000000", "1E+2"):
            assert "E" not in plain(Decimal(value))

    def test_the_result_is_a_string_not_a_float(self) -> None:
        """So it cannot be silently re-parsed into something inexact downstream."""
        assert isinstance(plain(Decimal("5.36")), str)


class TestAbbreviate:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ("13447437000.0000000000", "13.447B"),
            ("5704000000", "5.704B"),
            ("1500000", "1.500M"),
            ("999999", "999999"),
            ("5.3600000000", "5.36"),
            ("-2500000000", "-2.500B"),
        ],
    )
    def test_magnitudes_are_abbreviated_only_above_a_million(
        self, stored: str, expected: str
    ) -> None:
        assert abbreviate(Decimal(stored)) == expected

    def test_small_values_fall_through_to_exact(self) -> None:
        assert abbreviate(Decimal("5.3600000000")) == plain(Decimal("5.3600000000"))
