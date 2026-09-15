"""Local-filesystem implementation of the Core Asset Storage port (M4-02).

The adapter accepts no destination path from a caller.  Each ingest generates
a canonical UUID, computes SHA-256 over the exact accepted bytes, and stores
those bytes under that identifier.  Identifier validation and lexical/root
containment checks happen before any operation on an identifier-derived path.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from audio_cue_locator.core.asset import (
    Asset,
    AssetNotFoundError,
    AssetStorageCollisionError,
    AssetStorageEntry,
    AssetStoragePort,
    AssetType,
    InvalidAssetIdentifierError,
    new_asset_identifier,
    normalize_media_type,
    sanitize_asset_name,
    validate_asset_identifier,
)

_MAX_IDENTIFIER_ALLOCATION_ATTEMPTS = 8


class UnsafeStoragePathError(InvalidAssetIdentifierError):
    """Raised when an identifier-derived location would escape storage root."""


class LocalFilesystemAssetStorage(AssetStoragePort):
    """Store Asset bytes beneath one configured local directory.

    The base directory is trusted application configuration.  It is resolved
    once at construction and never included in a Core ``Asset``.  Files are
    extensionless and named only with canonical internally generated UUIDs;
    client-supplied informative names cannot influence the path.
    """

    def __init__(self, base_directory: str | Path) -> None:
        if isinstance(base_directory, str) and not base_directory.strip():
            raise ValueError("Asset storage base_directory must be non-empty")
        if not isinstance(base_directory, (str, Path)):
            raise TypeError("Asset storage base_directory must be str or Path")
        self._base_directory = Path(base_directory).expanduser().resolve(strict=False)

    def ingest(
        self,
        content: bytes,
        *,
        logical_type: AssetType,
        informative_name: str,
        detected_media_type: str,
    ) -> Asset:
        """Store bytes under a fresh internal identifier with SHA-256 metadata.

        All caller-controlled values are validated before the storage directory
        is created or an identifier-derived path is accessed.  Checksum and
        size are calculated from the immutable payload before its write.
        """

        payload = _immutable_bytes(content)
        if not isinstance(logical_type, AssetType):
            raise TypeError("logical_type must be an AssetType value")
        sanitized_name = sanitize_asset_name(informative_name)
        media_type = normalize_media_type(detected_media_type)
        checksum = hashlib.sha256(payload).hexdigest()

        for _ in range(_MAX_IDENTIFIER_ALLOCATION_ATTEMPTS):
            identifier = new_asset_identifier()
            storage_path = self._path_for(identifier)
            try:
                self._write_new(storage_path, payload)
            except FileExistsError:
                continue
            return Asset(
                identifier=identifier,
                logical_type=logical_type,
                sanitized_name=sanitized_name,
                media_type=media_type,
                size_bytes=len(payload),
                checksum=checksum,
                storage_reference=identifier,
            )

        raise AssetStorageCollisionError(
            "Could not allocate a unique internal Asset identifier"
        )

    def read(self, identifier: str) -> bytes:
        """Read bytes by canonical internal identifier only."""

        storage_path = self._path_for(identifier)
        try:
            return storage_path.read_bytes()
        except FileNotFoundError:
            raise AssetNotFoundError(
                f"No Asset bytes exist for identifier {identifier!r}"
            ) from None

    def delete(self, identifier: str) -> bool:
        """Delete bytes by canonical internal identifier only.

        Deletion is idempotent so a cleanup pass can be safely retried after
        an interruption.  Identifier validation, symlink rejection, and root
        containment are completed before ``unlink`` is attempted.
        """

        storage_path = self._path_for(identifier)
        try:
            storage_path.unlink()
        except FileNotFoundError:
            return False
        return True

    def list_entries(self) -> tuple[AssetStorageEntry, ...]:
        """Return inventory metadata for every managed direct child file (S0012).

        Enumeration never recurses below the configured root.  A symlink,
        directory, or non-canonical-UUID filename is never treated as a
        managed entry.  Nothing here reads, writes, or touches file content,
        so ``stat().st_mtime`` -- the write-once local adapter's only age
        signal (bytes are exclusively created, read, or unlinked; nothing
        rewrites them in place) -- is never disturbed by inventory itself.
        """

        if not self._base_directory.is_dir():
            return ()

        entries: list[AssetStorageEntry] = []
        for child in self._base_directory.iterdir():
            if child.is_symlink():
                continue
            try:
                identifier = validate_asset_identifier(child.name)
            except InvalidAssetIdentifierError:
                continue
            if not child.is_file():
                continue
            stat_result = child.stat()
            entries.append(
                AssetStorageEntry(
                    identifier=identifier,
                    size_bytes=stat_result.st_size,
                    stored_at=datetime.fromtimestamp(
                        stat_result.st_mtime, tz=timezone.utc
                    ),
                )
            )
        entries.sort(key=lambda entry: entry.identifier)
        return tuple(entries)

    def _path_for(self, identifier: str) -> Path:
        """Resolve a validated identifier beneath the configured root.

        ``validate_asset_identifier`` rejects path syntax before ``Path`` is
        consulted.  Resolving the candidate also catches a pre-existing
        identifier-named symlink that points outside the configured root.
        """

        storage_key = validate_asset_identifier(identifier)
        candidate = self._base_directory / storage_key
        if candidate.is_symlink():
            raise UnsafeStoragePathError(
                "Identifier-derived Asset path must not be a symbolic link"
            )
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(self._base_directory)
        except ValueError as exc:
            raise UnsafeStoragePathError(
                "Identifier-derived Asset path escapes the configured storage root"
            ) from exc
        if resolved_candidate.parent != self._base_directory:
            raise UnsafeStoragePathError(
                "Identifier-derived Asset path must be a direct storage-root child"
            )
        return resolved_candidate

    def _write_new(self, storage_path: Path, payload: bytes) -> None:
        self._base_directory.mkdir(parents=True, exist_ok=True)
        try:
            handle = storage_path.open("xb")
        except FileExistsError:
            raise

        try:
            with handle:
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError(
                        "Asset storage did not write the complete payload "
                        f"({written} of {len(payload)} bytes)"
                    )
        except BaseException:
            storage_path.unlink(missing_ok=True)
            raise


def _immutable_bytes(content: bytes) -> bytes:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise TypeError("Asset content must be bytes-like")
    return bytes(content)
