"""The CLI's own rendering path.

Everything else in the suite tests the values. This file tests the last few
inches -- the formatting between a correct `Revision` and the characters a
reader sees -- because that is where a value the library computes fine can still
crash or lie on the way out.

The immediate reason is D12. Widening the magnitude filter to keep revisions off
a zero base made `Revision.relative_change` reachable through the *filtered*
path for the first time, and it raises rather than returning a number: a change
off zero has no denominator. Every consumer had to be checked. The API guards,
the web table renders an em dash, and the CLI catches -- but nothing pinned that
last one, so a later tidy-up could have deleted the `except` and taken
`aletheia revisions MS` down with it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from aletheia.cli.main import main
from aletheia.store.db import Warehouse
from tests._factories import RUN_ID, make_entity, make_fact, make_filing, make_identifier

MS_CIK = 895421
CONCEPT = "LongTermDebtCurrent"
PERIOD_END = date(2011, 12, 31)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A warehouse holding one revision off a zero base.

    Real: Morgan Stanley first reported no current long-term debt for FY2011 and
    a later filing put it at $35.082bn. The relative change is undefined -- you
    cannot divide by the zero it came from -- which is exactly the case the CLI
    has to render without dividing.
    """
    with Warehouse.open(tmp_path / "warehouse.duckdb") as store:
        store.start_run(source="test", params={"fixture": True}, run_id=RUN_ID)
        store.write_entity(make_entity(cik=MS_CIK, name="MORGAN STANLEY", sic="6199"))
        store.write_identifiers([make_identifier(cik=MS_CIK, ticker="MS")])
        store.write_filings(
            [
                make_filing(
                    accn="0001193125-12-081807",
                    filed_at=date(2012, 2, 27),
                    form="10-K",
                    cik=MS_CIK,
                    period_of_report=PERIOD_END,
                ),
                make_filing(
                    accn="0001193125-13-077191",
                    filed_at=date(2013, 2, 26),
                    form="10-K",
                    cik=MS_CIK,
                    period_of_report=date(2012, 12, 31),
                ),
            ]
        )
        store.write_facts(
            [
                make_fact(
                    value="0",
                    filed_at=date(2012, 2, 27),
                    accn="0001193125-12-081807",
                    concept=CONCEPT,
                    unit="USD",
                    cik=MS_CIK,
                    form="10-K",
                    period_start=None,
                    period_end=PERIOD_END,
                ),
                make_fact(
                    value="35082000000",
                    filed_at=date(2013, 2, 26),
                    accn="0001193125-13-077191",
                    concept=CONCEPT,
                    unit="USD",
                    cik=MS_CIK,
                    form="10-K",
                    period_start=None,
                    period_end=PERIOD_END,
                ),
            ]
        )
    return tmp_path


class TestRenderingARevisionOffAZeroBase:
    def test_the_command_prints_it_instead_of_dividing_by_zero(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A percentage is not available here, so the column says so in words.

        Before D12 the filter dropped these rows and the printer never saw one.
        Now `--min-change` returns them, and an unguarded `f"{...:+.1%}"` would
        raise ZeroDivisionError on a ticker the command handled the day before.
        """
        code = main(
            [
                "--data-dir",
                str(data_dir),
                "revisions",
                "MS",
                "--concept",
                CONCEPT,
                "--min-change",
                "0.05",
                "--date",
                "2013-06-01",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "from 0" in out, out
        assert "35.082B" in out, out

    def test_and_the_row_is_present_at_a_threshold_no_ratio_could_pass(
        self, data_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The D12 claim, asserted at the surface a user actually types.

        A move off zero is unbounded, not negligible, so no finite threshold is
        grounds for hiding it.
        """
        code = main(
            [
                "--data-dir",
                str(data_dir),
                "revisions",
                "MS",
                "--concept",
                CONCEPT,
                "--min-change",
                "1000",
                "--date",
                "2013-06-01",
            ]
        )
        assert code == 0
        assert "from 0" in capsys.readouterr().out
