"""Application: bounded Asset ingestion for source-media and cue uploads
(M5-03).

M5-03's own highest-severity named risk is that "limits are checked only
after full buffering and permit resource exhaustion"
(`issues/M5/M5-03/formal-issue.json`, section 13); its second is that
"filenames influence storage paths and permit collision or traversal".
The second risk is already closed by construction: `core.asset.AssetStoragePort`
generates the internal identifier itself and `core.asset.sanitize_asset_name`
reduces a client-supplied filename to presentation-only metadata, both
already implemented by M4-02 and confirmed unmodified by this issue. This
module closes the first: it is the Application-level policy boundary that
`interfaces.rest_api.asset_routes`'s own bounded multipart stream read
(gap G4, `intents/M5/M5-03/implementation-handoff.json`) hands already-
bounded bytes to, so that no more than one configured limit's worth of
payload is ever read from a client before this module even runs.

`core.asset.AssetStoragePort.ingest(content: bytes, ...)` accepts only a
complete, already-buffered payload; it defines no chunked-read or size-limit
primitive of its own (confirmed by direct read of both `core/asset.py` and
`infrastructure/asset_storage/local_filesystem_storage.py` at handoff
preparation). Enforcing "reject before unsafe resource consumption" for the
*read itself* is therefore necessarily this issue's own
`interfaces.rest_api.asset_routes` responsibility -- it owns the only code
path that can stop reading the client's request stream early. This module's
own size check in `AssetIngestionUseCase.ingest` below is a second,
defense-in-depth confirmation, not the primary enforcement point: by the
time `content: bytes` exists as a parameter here, the interfaces layer has
already finished reading it, bounded or not, and this function has no way to
un-read it.

Resolves gaps this issue's own handoff deliberately left open
(`intents/M5/M5-03/implementation-handoff.json`):

- G3 (Asset Storage port location): the formal issue's own Premissas
  describe Asset Storage as available "behind an Application port", but the
  verified dependency is `core.asset.AssetStoragePort`, a Core-defined
  Protocol; no `application/ports/asset_storage.py` module exists anywhere
  in this repository (confirmed by direct listing of `application/ports/`
  at handoff preparation, which contains only `analysis_repository.py`).
  `AssetIngestionUseCase` below depends on `AssetStoragePort` directly.
- G1 (initial upload-size limits and supported-media allowlist): fixed
  below as `default_upload_limits()`, with each number's own rationale
  documented alongside it. No empirical usage evidence exists yet for this
  project; these are conservative, explicitly documented starting points,
  not a benchmarked ceiling, and are deliberately kept overridable by
  `interfaces.rest_api.app`'s composition-root wiring rather than hardcoded
  as the only possible values (formal issue restricao: "Limits must be
  explicit and configurable, with safe defaults validated by evidence").

`media_type` detection: `docs/asset-identity-and-storage.md` requires
`Asset.media_type` to be "the result of trusted media inspection" and
states plainly that "a filename extension alone is not detection". This
module never trusts a client-declared Content-Type header as that
inspection result; `sniff_media_type` below inspects the payload's own
leading bytes for the two container signatures
`infrastructure/media_processing/ffmpeg_adapter.py` already documents as
this project's confirmed, tested supported inputs ("WAV as the audio input
format and MP4 ... as the video container"), not a new, independently
invented allowlist. A full FFmpeg decode/stream probe remains out of this
issue's scope -- that validation happens later, at Analysis creation
(M5-04) and matching -- so a payload that merely carries a recognized
container signature but fails to decode is not rejected here.

`interfaces/rest_api/errors.py`'s own module docstring describes translating
a failure "into one of the REST-boundary exception types below ... [as]
Application's responsibility" -- but this module deliberately does *not*
import `UnsupportedMediaError`/`ResourceLimitExceededError` from
`interfaces.rest_api.errors` to do that directly: `interfaces/rest_api/
__init__.py` eagerly imports `app.py` (`from .app import ...`), and `app.py`
must import this module to build its composition root
(`_build_asset_ingestion_use_case`), so an import here of anything under
`interfaces.rest_api` -- even a leaf submodule like `errors.py` -- forces
Python to resolve `interfaces.rest_api.app`, which imports this module
back, before this module has finished executing: a genuine circular import,
confirmed by direct execution at implementation time, not merely a
layering-purity concern. `UnsupportedAssetMediaError` and
`AssetUploadTooLargeError` below are this module's own, Application-owned
vocabulary for the same two failure classes; `interfaces.rest_api.
asset_routes` (which already legitimately imports `errors.py`) is the
narrow translation point that converts them to `errors.
UnsupportedMediaError`/`ResourceLimitExceededError` before either can reach
`errors.install_error_handlers`.
"""

from __future__ import annotations

from dataclasses import dataclass

from audio_cue_locator.core.asset import Asset, AssetStoragePort, AssetType


class UnsupportedAssetMediaError(ValueError):
    """Raised when an upload's detected media type (see `sniff_media_type`)
    is not in the configured allowlist for its `logical_type`. Translated to
    `interfaces.rest_api.errors.UnsupportedMediaError` by
    `interfaces.rest_api.asset_routes`."""


class AssetUploadTooLargeError(ValueError):
    """Raised when an upload's content exceeds the configured maximum size
    for its `logical_type`. Translated to `interfaces.rest_api.errors.
    ResourceLimitExceededError` by `interfaces.rest_api.asset_routes`."""

_WAV_RIFF_MAGIC = b"RIFF"
_WAV_FORMAT_MAGIC = b"WAVE"
_ISO_BMFF_BOX_TYPE = b"ftyp"


def sniff_media_type(content: bytes) -> str | None:
    """Return a normalized media type detected from ``content``'s own
    leading bytes, or ``None`` if no supported container signature matches.

    Checks exactly the two signatures this project's Infrastructure media
    adapter already documents as supported (see module docstring): a RIFF/
    WAVE container (``audio/wav``) and an ISO base media file format
    ``ftyp`` box (``video/mp4``, matching MP4's own container family).
    Never consults a filename or a client-declared header.
    """

    if (
        len(content) >= 12
        and content[0:4] == _WAV_RIFF_MAGIC
        and content[8:12] == _WAV_FORMAT_MAGIC
    ):
        return "audio/wav"
    if len(content) >= 12 and content[4:8] == _ISO_BMFF_BOX_TYPE:
        return "video/mp4"
    return None


@dataclass(frozen=True)
class UploadPolicy:
    """One logical Asset type's explicit, configurable upload constraints."""

    max_size_bytes: int
    supported_media_types: frozenset[str]

    def __post_init__(self) -> None:
        if self.max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be a positive integer")
        if not self.supported_media_types:
            raise ValueError("supported_media_types must not be empty")


@dataclass(frozen=True)
class UploadLimits:
    """The complete, explicit upload policy for every upload-accepting
    ``AssetType`` this issue exposes. ``policy_for`` is the single place a
    caller resolves which policy applies to a given upload."""

    source_media: UploadPolicy
    cue: UploadPolicy

    def policy_for(self, logical_type: AssetType) -> UploadPolicy:
        if logical_type is AssetType.SOURCE_MEDIA:
            return self.source_media
        if logical_type is AssetType.CUE:
            return self.cue
        raise ValueError(
            f"No upload policy is defined for logical_type={logical_type!r}; "
            "this issue exposes only source_media and cue uploads"
        )


DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024
"""500 MiB. No empirical usage evidence exists yet for this project
(gap G1, `intents/M5/M5-03/implementation-handoff.json`); this is a
conservative, explicitly documented starting point sized to comfortably
hold a feature-length uncompressed WAV or a moderate-bitrate MP4, not a
benchmarked ceiling. Overridable at composition time by
`interfaces.rest_api.app`."""

DEFAULT_MAX_CUE_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024
"""50 MiB. A cue is a short reference clip, not a full source recording;
this comfortably covers several minutes of uncompressed CD-quality WAV.
Same evidence caveat and override mechanism as
`DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES` above."""

SOURCE_MEDIA_SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset({"audio/wav", "video/mp4"})
"""Matches `infrastructure/media_processing/ffmpeg_adapter.py`'s own
documented supported inputs exactly ("WAV as the audio input format and
MP4 ... as the video container")."""

CUE_SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset({"audio/wav"})
"""A cue is a short reference audio clip; only the audio container
`ffmpeg_adapter.py` supports is accepted -- a cue is never itself a video
container in this project's documented scope."""


def default_upload_limits() -> UploadLimits:
    """Return this project's documented starting-point upload policy."""

    return UploadLimits(
        source_media=UploadPolicy(
            max_size_bytes=DEFAULT_MAX_SOURCE_MEDIA_UPLOAD_SIZE_BYTES,
            supported_media_types=SOURCE_MEDIA_SUPPORTED_MEDIA_TYPES,
        ),
        cue=UploadPolicy(
            max_size_bytes=DEFAULT_MAX_CUE_UPLOAD_SIZE_BYTES,
            supported_media_types=CUE_SUPPORTED_MEDIA_TYPES,
        ),
    )


class AssetIngestionUseCase:
    """Application-level Asset ingestion: validates an already-bounded-read
    upload against this issue's explicit, configurable policy, then
    delegates physical persistence to the injected `AssetStoragePort`
    exclusively -- this class never imports or constructs a concrete
    storage adapter itself (dependency injection is
    `interfaces.rest_api.app`'s composition-root responsibility)."""

    def __init__(self, storage: AssetStoragePort, limits: UploadLimits | None = None) -> None:
        self._storage = storage
        self._limits = limits if limits is not None else default_upload_limits()

    @property
    def limits(self) -> UploadLimits:
        return self._limits

    def ingest(
        self,
        *,
        content: bytes,
        logical_type: AssetType,
        informative_name: str,
    ) -> Asset:
        """Validate and persist one already-bounded-read upload.

        ``content`` must already have been read under its own configured
        upper bound by the caller before this function is invoked (see
        module docstring); the size check below is a defense-in-depth
        confirmation, not the primary enforcement point. Sanitization of
        ``informative_name`` and generation of the internal identifier
        remain `AssetStoragePort.ingest`'s own responsibility, unchanged by
        this issue.
        """

        policy = self._limits.policy_for(logical_type)
        if len(content) > policy.max_size_bytes:
            raise AssetUploadTooLargeError(
                f"Upload of {len(content)} bytes exceeds the configured "
                f"{policy.max_size_bytes}-byte limit for {logical_type.value}"
            )

        detected_media_type = sniff_media_type(content)
        if detected_media_type is None or detected_media_type not in policy.supported_media_types:
            raise UnsupportedAssetMediaError(
                f"Upload for {logical_type.value} did not match a supported "
                "media signature"
            )

        return self._storage.ingest(
            content,
            logical_type=logical_type,
            informative_name=informative_name,
            detected_media_type=detected_media_type,
        )
