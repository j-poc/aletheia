"""Raw payload store and configuration.

Both exist to prevent a specific class of silent failure: losing the bytes a
number came from, and leaking a credential into a log.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from aletheia.core.clock import FrozenClock
from aletheia.core.config import DEFAULT_SEC_USER_AGENT, Secret, load_settings
from aletheia.core.errors import ConfigError, PermanentSourceError
from aletheia.provenance.payloads import PayloadStore, StoredPayload
from aletheia.sources.http import Fetcher

RETRIEVED_AT = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
PAYLOAD = b'{"cik":320193,"val":5.36}'


class TestPayloadStore:
    def test_round_trips_bytes(self, payloads: PayloadStore) -> None:
        stored = payloads.put(
            PAYLOAD, source_uri="https://data.sec.gov/x", retrieved_at=RETRIEVED_AT
        )
        assert payloads.get(stored.content_sha256) == PAYLOAD

    def test_is_content_addressed(self, payloads: PayloadStore) -> None:
        stored = payloads.put(PAYLOAD, source_uri="https://a", retrieved_at=RETRIEVED_AT)
        again = payloads.put(PAYLOAD, source_uri="https://b", retrieved_at=RETRIEVED_AT)
        assert stored.content_sha256 == again.content_sha256, "same bytes, same object"
        assert stored.was_new and not again.was_new

    def test_different_bytes_do_not_overwrite(self, payloads: PayloadStore) -> None:
        """A source that silently changes its answer must produce a second object."""
        first = payloads.put(PAYLOAD, source_uri="https://a", retrieved_at=RETRIEVED_AT)
        second = payloads.put(b'{"val":6.78}', source_uri="https://a", retrieved_at=RETRIEVED_AT)
        assert first.content_sha256 != second.content_sha256
        assert payloads.get(first.content_sha256) == PAYLOAD

    def test_detects_corruption_on_read(self, payloads: PayloadStore) -> None:
        """Positive control: an intact read succeeds, a corrupted one raises."""
        stored = payloads.put(PAYLOAD, source_uri="https://a", retrieved_at=RETRIEVED_AT)
        assert payloads.get(stored.content_sha256) == PAYLOAD  # control
        stored.path.write_bytes(gzip.compress(b'{"val":999}', mtime=0))
        with pytest.raises(ValueError, match="payload corruption"):
            payloads.get(stored.content_sha256)

    def test_storage_bytes_are_deterministic(self, tmp_path: Path) -> None:
        """gzip mtime is pinned, so identical payloads produce identical files.

        Without mtime=0 the stored bytes differ run to run and any repro-hash
        over the raw layer drifts for no substantive reason.
        """
        left = PayloadStore(tmp_path / "a").put(PAYLOAD, source_uri="u", retrieved_at=RETRIEVED_AT)
        right = PayloadStore(tmp_path / "b").put(PAYLOAD, source_uri="u", retrieved_at=RETRIEVED_AT)
        assert left.path.read_bytes() == right.path.read_bytes()

    def test_no_partial_file_survives(self, payloads: PayloadStore) -> None:
        payloads.put(PAYLOAD, source_uri="u", retrieved_at=RETRIEVED_AT)
        assert list(payloads.root.rglob("*.partial")) == []

    def test_missing_payload_raises(self, payloads: PayloadStore) -> None:
        with pytest.raises(FileNotFoundError):
            payloads.get("a" * 64)

    def test_rejects_a_non_digest_key(self, payloads: PayloadStore) -> None:
        with pytest.raises(ValueError, match="not a sha256 hex digest"):
            payloads.exists("nope")


class TestSecret:
    def test_does_not_leak_through_repr_or_str(self) -> None:
        secret = Secret("super-secret-key")
        assert "super-secret-key" not in repr(secret)
        assert "super-secret-key" not in str(secret)
        assert "super-secret-key" not in f"{secret}"

    def test_does_not_leak_through_an_exception_message(self) -> None:
        """Tracebacks are the most common accidental credential sink."""
        secret = Secret("super-secret-key")
        message = str(RuntimeError(f"request failed with {secret}"))
        assert "super-secret-key" not in message

    def test_reveal_is_the_only_way_out(self) -> None:
        assert Secret("abc").reveal() == "abc"

    def test_length_is_available_without_the_value(self) -> None:
        assert len(Secret("abc123")) == 6


class TestSettings:
    def test_reports_credential_presence_without_values(self) -> None:
        settings = load_settings(
            env={"FRED_API_KEY": "real-key", "ALETHEIA_DATA_DIR": "somewhere/else"}
        )
        described = settings.describe()
        assert described["fred_api_key"] == "present"
        assert described["fmp_api_key"] == "absent"
        assert "real-key" not in str(described)

    def test_require_raises_a_named_actionable_error(self) -> None:
        settings = load_settings(env={})
        with pytest.raises(ConfigError, match="FMP_API_KEY"):
            settings.require("fmp_api_key")

    def test_require_returns_the_secret_when_present(self) -> None:
        settings = load_settings(env={"FMP_API_KEY": "k"})
        assert settings.require("fmp_api_key").reveal() == "k"

    def test_blank_credential_counts_as_absent(self) -> None:
        """An empty env var is a common footgun — it must not read as configured."""
        settings = load_settings(env={"FRED_API_KEY": "   "})
        assert settings.fred_api_key is None

    def test_user_agent_is_honest_when_unconfigured(self) -> None:
        """Never impersonate a plausible-looking contact address to the SEC."""
        assert load_settings(env={}).sec_user_agent == DEFAULT_SEC_USER_AGENT

    def test_user_agent_uses_a_configured_contact(self) -> None:
        settings = load_settings(env={"ALETHEIA_CONTACT_EMAIL": "a@b.c"})
        assert "a@b.c" in settings.sec_user_agent

    def test_paths_derive_from_the_data_dir(self, tmp_path: Path) -> None:
        settings = load_settings(data_dir=tmp_path, env={})
        assert settings.raw_dir == tmp_path / "raw"
        assert settings.warehouse_path == tmp_path / "warehouse.duckdb"
        assert settings.evidence_dir == tmp_path / "evidence"


class TestEveryFetchedPayloadIsIndexed:
    """The provenance ledger must enumerate what was fetched, not a subset.

    Indexing used to happen at the ingest call sites, and only one of them did it.
    A production warehouse ended up holding **one** ledger row against 2,281
    payload files on disk. Row-level provenance was intact -- every fact carried a
    content hash that resolved to a real file -- but the index that is supposed to
    answer "what did this system fetch" was empty, and nothing said so.

    The hook now lives in the one place every payload passes through, so a new
    source cannot forget to call it.
    """

    def test_the_hook_fires_for_every_fetch(
        self, payloads: PayloadStore, clock: FrozenClock
    ) -> None:
        seen: list[tuple[str, str]] = []

        def sink(source: str, stored: StoredPayload) -> None:
            seen.append((source, stored.content_sha256))

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": str(request.url)})
        )
        with httpx.Client(transport=transport) as client:
            fetcher = Fetcher(
                payloads=payloads,
                clock=clock,
                user_agent="test",
                client=client,
                on_stored=sink,
            )
            fetcher.get("https://example.invalid/a", source="alpha")
            fetcher.get("https://example.invalid/b", source="beta")

        assert [source for source, _ in seen] == ["alpha", "beta"]
        assert len({digest for _, digest in seen}) == 2

    def test_no_hook_means_no_error(self, payloads: PayloadStore, clock: FrozenClock) -> None:
        """The hook is optional; a Fetcher without one still fetches and stores."""
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": 1}))
        with httpx.Client(transport=transport) as client:
            result = Fetcher(payloads=payloads, clock=clock, user_agent="test", client=client).get(
                "https://example.invalid/a", source="alpha"
            )
        assert result.stored is not None
        assert result.stored.path.exists()

    def test_a_failed_fetch_indexes_nothing(
        self, payloads: PayloadStore, clock: FrozenClock
    ) -> None:
        """A 404 stores no bytes, so it must not appear in the ledger either."""
        seen: list[str] = []
        transport = httpx.MockTransport(lambda request: httpx.Response(404, text="nope"))
        with httpx.Client(transport=transport) as client:
            fetcher = Fetcher(
                payloads=payloads,
                clock=clock,
                user_agent="test",
                client=client,
                on_stored=lambda source, stored: seen.append(source),
            )
            with pytest.raises(PermanentSourceError):
                fetcher.get("https://example.invalid/missing", source="alpha")
        assert seen == []
