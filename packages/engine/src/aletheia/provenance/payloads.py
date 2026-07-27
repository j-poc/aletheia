"""Content-addressed store for raw external payloads.

Every byte fetched from an external source is written here *before* it is parsed,
named by its own SHA-256. Three consequences, all of them the point:

* **Parsing bugs are recoverable.** The original bytes survive, so a fix can be
  replayed over history instead of requiring a re-fetch that may no longer return
  the same answer — EDGAR filings are immutable, but vendor endpoints are not.
* **Silent upstream change is detectable.** The same URI returning different bytes
  produces a second object rather than overwriting the first.
* **Provenance is verifiable, not asserted.** Any stored number can be traced to a
  file whose hash can be recomputed by hand.

Layout is two levels of hex fan-out (``ab/cd/abcd…``) so a directory never holds
hundreds of thousands of entries.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aletheia.core.hashing import sha256_bytes


@dataclass(frozen=True, slots=True)
class StoredPayload:
    """Where a payload landed and what it is."""

    content_sha256: str
    path: Path
    byte_len: int
    source_uri: str
    retrieved_at: datetime
    http_status: int | None
    was_new: bool
    """False when these exact bytes were already on disk — a re-fetch of unchanged data."""


class PayloadStore:
    """Immutable, content-addressed blob store rooted at ``data/raw``."""

    def __init__(self, root: Path, *, compress: bool = True) -> None:
        self.root = root
        self._compress = compress

    def put(
        self,
        payload: bytes,
        *,
        source_uri: str,
        retrieved_at: datetime,
        suffix: str = ".json",
        http_status: int | None = None,
    ) -> StoredPayload:
        """Store bytes under their own hash. Idempotent."""
        digest = sha256_bytes(payload)
        path = self._path_for(digest, suffix)
        was_new = not path.exists()
        if was_new:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a sibling temp file then rename: a crash mid-write must not
            # leave a truncated blob sitting under a hash that claims to describe
            # the whole payload.
            staging = path.with_suffix(path.suffix + ".partial")
            if self._compress:
                staging.write_bytes(gzip.compress(payload, mtime=0))
            else:
                staging.write_bytes(payload)
            staging.replace(path)
        return StoredPayload(
            content_sha256=digest,
            path=path,
            byte_len=len(payload),
            source_uri=source_uri,
            retrieved_at=retrieved_at,
            http_status=http_status,
            was_new=was_new,
        )

    def get(self, content_sha256: str, *, suffix: str = ".json") -> bytes:
        """Read back payload bytes and verify the hash still matches.

        Verification is not paranoia: this store is the evidence base for every
        number the system publishes, and silent bit-rot in it would invalidate all
        of them without any other symptom.
        """
        path = self._path_for(content_sha256, suffix)
        if not path.exists():
            raise FileNotFoundError(f"no payload {content_sha256[:12]}… at {path}")
        raw = path.read_bytes()
        payload = gzip.decompress(raw) if self._compress else raw
        actual = sha256_bytes(payload)
        if actual != content_sha256:
            raise ValueError(
                f"payload corruption at {path}: content hashes to {actual[:12]}…, "
                f"expected {content_sha256[:12]}…"
            )
        return payload

    def exists(self, content_sha256: str, *, suffix: str = ".json") -> bool:
        return self._path_for(content_sha256, suffix).exists()

    def _path_for(self, digest: str, suffix: str) -> Path:
        if len(digest) != 64:
            raise ValueError(f"not a sha256 hex digest: {digest!r}")
        extension = f"{suffix}.gz" if self._compress else suffix
        return self.root / digest[:2] / digest[2:4] / f"{digest}{extension}"
