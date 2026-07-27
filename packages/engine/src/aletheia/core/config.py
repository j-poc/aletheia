"""Runtime configuration.

Loaded once at the composition root and passed down explicitly. Two rules:

* **Secrets never reach a log, a repr, or an exception message.** ``Secret`` wraps
  them and refuses to stringify. A traceback from a failing HTTP call must not be
  the thing that leaks an API key into a log file.
* **Missing credentials fail loudly at construction**, not at the first request
  three minutes into an ingest. ``require`` names the variable and the source it
  serves without printing anything sensitive.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from aletheia.core.errors import ConfigError

# The SEC requires a descriptive User-Agent with contact details and throttles to
# ~10 requests/second. Both are conditions of access, not suggestions:
# https://www.sec.gov/os/webmaster-faq#developers
SEC_RATE_LIMIT_PER_SEC: Final = 8.0  # deliberately under the documented ceiling
DEFAULT_SEC_USER_AGENT: Final = "ALETHEIA research (contact: set ALETHEIA_SEC_USER_AGENT)"


class Secret:
    """A string that will not appear in logs, reprs, or tracebacks."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Explicit, greppable unwrap. The only way to read the value."""
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return f"Secret(<redacted, {len(self._value)} chars>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the engine needs to run, resolved and validated."""

    data_dir: Path
    sec_user_agent: str
    fred_api_key: Secret | None = None
    fmp_api_key: Secret | None = None
    sec_rate_limit_per_sec: float = SEC_RATE_LIMIT_PER_SEC
    http_timeout_seconds: float = 60.0
    max_retries: int = 4
    _origins: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @property
    def raw_dir(self) -> Path:
        """Immutable content-addressed payload store."""
        return self.data_dir / "raw"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.duckdb"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    def require(self, name: str) -> Secret:
        """Fetch a credential or fail with an actionable, secret-free message."""
        secret: Secret | None = getattr(self, name, None)
        if secret is None or not secret:
            origin = self._origins.get(name, "environment")
            raise ConfigError(
                f"{name} is not configured (looked in {origin}). "
                f"Source ~/.claude/.env, or pass it explicitly. "
                f"Presence is not liveness — verify with one real request before relying on it."
            )
        return secret

    def describe(self) -> dict[str, str]:
        """Loggable summary. Credentials appear as present/absent only."""
        return {
            "data_dir": str(self.data_dir),
            "sec_user_agent": self.sec_user_agent,
            "fred_api_key": "present" if self.fred_api_key else "absent",
            "fmp_api_key": "present" if self.fmp_api_key else "absent",
            "sec_rate_limit_per_sec": f"{self.sec_rate_limit_per_sec:g}",
        }


def load_settings(
    *,
    data_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Resolve settings from the environment.

    ``env`` is injectable so tests never depend on the developer's shell.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    resolved_dir = data_dir or Path(source.get("ALETHEIA_DATA_DIR", "data")).expanduser()

    user_agent = source.get("ALETHEIA_SEC_USER_AGENT", "").strip()
    if not user_agent:
        # Fall back to a contact address if one is discoverable, else a value that
        # is honest about being unconfigured rather than impersonating a real one.
        contact = source.get("ALETHEIA_CONTACT_EMAIL", "").strip()
        user_agent = f"ALETHEIA research ({contact})" if contact else DEFAULT_SEC_USER_AGENT

    return Settings(
        data_dir=resolved_dir,
        sec_user_agent=user_agent,
        fred_api_key=_secret(source, "FRED_API_KEY"),
        fmp_api_key=_secret(source, "FMP_API_KEY"),
        _origins={"fred_api_key": "FRED_API_KEY", "fmp_api_key": "FMP_API_KEY"},
    )


def _secret(source: Mapping[str, str], name: str) -> Secret | None:
    value = source.get(name, "").strip()
    return Secret(value) if value else None
