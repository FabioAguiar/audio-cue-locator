"""Core Asset identity and the storage port consumed by Application (M4-02).

An ``Asset`` describes identifiable media or an artifact without exposing the
filesystem location that currently holds its bytes.  Infrastructure owns that
mechanism and implements ``AssetStoragePort``; Application can depend on the
port without importing a concrete filesystem adapter.

The string identifier is deliberately constrained to canonical UUID form.  It
is generated internally by storage implementations, is safe to use as an
opaque storage key, and cannot contain path syntax.  A sanitized name remains
informational metadata only and never participates in storage addressing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

CHECKSUM_ALGORITHM = "sha256"
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)
_MAX_INFORMATIVE_NAME_LENGTH = 255


class AssetType(str, Enum):
    """Logical role of an Asset, independent of its storage mechanism."""

    SOURCE_MEDIA = "source_media"
    CUE = "cue"
    DERIVED_ARTIFACT = "derived_artifact"
    ANALYSIS_RESULT = "analysis_result"


class InvalidAssetIdentifierError(ValueError):
    """Raised when a value is not a canonical, path-safe Asset identifier."""


class InvalidAssetMetadataError(ValueError):
    """Raised when Asset metadata violates the Core contract."""


class AssetNotFoundError(LookupError):
    """Raised by an Asset Storage port when no bytes exist for an identifier."""


class AssetStorageCollisionError(RuntimeError):
    """Raised when storage cannot allocate a fresh internal identifier."""


def new_asset_identifier() -> str:
    """Return an internally generated canonical UUID identifier."""

    return str(uuid4())


def validate_asset_identifier(identifier: str) -> str:
    """Return ``identifier`` if it is a canonical UUID, otherwise reject it.

    This validation is intentionally stricter than a generic non-empty string:
    separators, traversal components, absolute paths, and URI-like values are
    invalid before a storage adapter performs any filesystem operation.
    """

    if not isinstance(identifier, str) or not identifier:
        raise InvalidAssetIdentifierError(
            "Asset identifier must be a non-empty canonical UUID string"
        )
    try:
        parsed = UUID(identifier)
    except (ValueError, AttributeError) as exc:
        raise InvalidAssetIdentifierError(
            "Asset identifier must be a canonical UUID string"
        ) from exc
    if str(parsed) != identifier:
        raise InvalidAssetIdentifierError(
            "Asset identifier must use lowercase canonical UUID form"
        )
    return identifier


def sanitize_asset_name(informative_name: str) -> str:
    """Return safe presentation metadata from a filename-like label.

    A name may contain ordinary Unicode letters and numbers, spaces, ``.``,
    ``_``, and ``-``.  Other characters are replaced with ``_``.  Path-like
    input is rejected rather than reduced to a basename so callers cannot
    accidentally treat client-controlled paths as supported input.
    """

    if not isinstance(informative_name, str):
        raise InvalidAssetMetadataError("Asset informative name must be a string")

    normalized = unicodedata.normalize("NFKC", informative_name).strip()
    if not normalized:
        raise InvalidAssetMetadataError("Asset informative name must be non-empty")
    if "\x00" in normalized or "/" in normalized or "\\" in normalized:
        raise InvalidAssetMetadataError(
            "Asset informative name must not contain path syntax"
        )
    if normalized in {".", ".."}:
        raise InvalidAssetMetadataError(
            "Asset informative name must not be a traversal component"
        )

    sanitized_parts: list[str] = []
    previous_was_replacement = False
    for character in normalized:
        if character.isalnum() or character in {" ", ".", "_", "-"}:
            sanitized_parts.append(character)
            previous_was_replacement = False
        elif not previous_was_replacement:
            sanitized_parts.append("_")
            previous_was_replacement = True

    sanitized = "".join(sanitized_parts).strip(" ._")
    sanitized = sanitized[:_MAX_INFORMATIVE_NAME_LENGTH].rstrip(" ._")
    if not sanitized:
        raise InvalidAssetMetadataError(
            "Asset informative name has no usable characters after sanitization"
        )
    return sanitized


def normalize_media_type(media_type: str) -> str:
    """Normalize and validate a detected ``type/subtype`` media type."""

    if not isinstance(media_type, str):
        raise InvalidAssetMetadataError("Asset media type must be a string")
    normalized = media_type.strip().lower()
    if not _MEDIA_TYPE_RE.fullmatch(normalized):
        raise InvalidAssetMetadataError(
            "Asset media type must be a detected type/subtype value"
        )
    return normalized


@dataclass(frozen=True)
class Asset:
    """Storage-independent identity and traceability metadata for bytes.

    ``storage_reference`` is opaque to Core.  It may be persisted or passed
    back to an adapter, but it must not be interpreted as a path by Core or
    Application.  The local adapter currently uses the identifier itself as
    this opaque reference, keeping physical base-directory information private.
    """

    identifier: str
    logical_type: AssetType
    sanitized_name: str
    media_type: str
    size_bytes: int
    checksum: str
    storage_reference: str
    checksum_algorithm: str = CHECKSUM_ALGORITHM

    def __post_init__(self) -> None:
        validate_asset_identifier(self.identifier)
        if not isinstance(self.logical_type, AssetType):
            raise InvalidAssetMetadataError(
                "Asset logical_type must be an AssetType value"
            )
        if sanitize_asset_name(self.sanitized_name) != self.sanitized_name:
            raise InvalidAssetMetadataError(
                "Asset sanitized_name must already be in canonical sanitized form"
            )
        if normalize_media_type(self.media_type) != self.media_type:
            raise InvalidAssetMetadataError(
                "Asset media_type must already be normalized"
            )
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise InvalidAssetMetadataError(
                "Asset size_bytes must be a non-negative integer"
            )
        if self.checksum_algorithm != CHECKSUM_ALGORITHM:
            raise InvalidAssetMetadataError(
                f"Asset checksum_algorithm must be {CHECKSUM_ALGORITHM!r}"
            )
        if not isinstance(self.checksum, str) or not _SHA256_HEX_RE.fullmatch(
            self.checksum
        ):
            raise InvalidAssetMetadataError(
                "Asset checksum must be a lowercase SHA-256 hexadecimal digest"
            )
        if not isinstance(self.storage_reference, str) or not self.storage_reference:
            raise InvalidAssetMetadataError(
                "Asset storage_reference must be a non-empty opaque string"
            )


@runtime_checkable
class AssetStoragePort(Protocol):
    """Application-facing port for ingesting and resolving Asset bytes.

    No method accepts a destination path.  ``informative_name`` is metadata,
    while the implementation generates the identifier that keys storage.
    """

    def ingest(
        self,
        content: bytes,
        *,
        logical_type: AssetType,
        informative_name: str,
        detected_media_type: str,
    ) -> Asset:
        """Store ``content`` under a generated identifier and return its Asset."""

        ...

    def read(self, identifier: str) -> bytes:
        """Return bytes addressed exclusively by an internal identifier."""

        ...
