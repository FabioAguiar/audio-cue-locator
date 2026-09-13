# Asset Identity and Local Filesystem Storage

## Purpose and boundary

M4-02 defines the logical `Asset` contract and the first Asset Storage
implementation. An Asset identifies source media, a cue, a derived artifact,
or an exported Analysis result without making its physical filesystem path part
of domain identity.

The executable contract is split across two authorized modules:

- `src/audio_cue_locator/core/asset.py` contains the immutable `Asset`, its
  value vocabulary, validation rules, and the Application-facing
  `AssetStoragePort`.
- `src/audio_cue_locator/infrastructure/asset_storage/local_filesystem_storage.py`
  implements that port with local filesystem storage.

Application code must depend on `AssetStoragePort`. It must not import
`LocalFilesystemAssetStorage` as its contract. Core imports only Python's
standard library and has no dependency on `pathlib`, filesystem APIs, SQLite,
FFmpeg, HTTP, or transport schemas.

This issue does not persist Asset or Analysis metadata. M4-03 owns SQLite
persistence. It also does not define retention/cleanup, remote references,
object storage, upload limits, or a public API.

## Logical Asset contract

`Asset` is a frozen dataclass with these fields:

| Field | Type and meaning |
|---|---|
| `identifier` | Canonical lowercase UUID string generated inside Asset Storage. It is stable logical identity, never a client filename or path. |
| `logical_type` | `AssetType`: `source_media`, `cue`, `derived_artifact`, or `analysis_result`. |
| `sanitized_name` | Presentation-only name produced by `sanitize_asset_name`; it never participates in storage addressing. |
| `media_type` | Lowercase detected `type/subtype` value. The caller supplies the result of trusted media inspection; a filename extension alone is not detection. |
| `size_bytes` | Non-negative byte count calculated from the exact ingested payload. |
| `checksum` | Lowercase 64-character SHA-256 hexadecimal digest calculated from those same bytes. |
| `storage_reference` | Opaque internal reference. Core and Application must not parse it as a filesystem path. The local adapter currently uses the identifier as this value. |
| `checksum_algorithm` | Fixed value `sha256`, making the checksum representation explicit. |

The generic M3 `Analysis.source_asset` and `Cue.asset_reference` fields refer
to this logical identity/metadata contract. They do not receive decoded audio,
a storage adapter, or a filesystem path. This document does not change the M3
contracts or their canonical-audio requirement.

### Identifier ownership

Only the storage implementation generates identifiers during `ingest`.
`new_asset_identifier` uses UUID4 and `validate_asset_identifier` accepts only
the canonical lowercase UUID rendering. Consequently, values such as absolute
paths, `..`, separators, URI-like strings, and non-canonical UUID spellings are
rejected before an identifier-derived filesystem operation.

UUID is the initial internal representation, not a promise that IDs expose
storage topology. A future storage mechanism may retain the same logical ID
while locating bytes elsewhere.

### Informative-name sanitization

An informative name is metadata for display and diagnostics:

- Unicode is normalized with NFKC and surrounding whitespace is removed.
- `/`, `\\`, NUL, `.` and `..` path components are rejected.
- Unicode letters and numbers, spaces, `.`, `_`, and `-` are retained.
- Other character runs become `_`.
- Leading/trailing spaces, dots, and underscores are removed.
- The stored value is limited to 255 characters and must remain non-empty.

Sanitization does not make a client path usable. Path-like input is rejected,
and even a valid sanitized name is never passed to path construction.

### Media type

`normalize_media_type` accepts a syntactically valid `type/subtype`, strips
surrounding whitespace, and lowercases it. The value is called
`detected_media_type` at the port boundary to make ownership explicit: callers
must supply a result obtained from trusted probing appropriate to the media
pipeline. The local storage adapter does not infer media type from the client
filename or extension.

## Application-facing port

`AssetStoragePort` exposes only:

- `ingest(content, *, logical_type, informative_name, detected_media_type)`,
  which stores bytes under a generated identifier and returns the complete
  `Asset` metadata;
- `read(identifier)`, which resolves bytes by internal identifier.

There is deliberately no destination-path parameter. Python structural typing
allows Application to accept this port and a test double without depending on
the concrete Infrastructure class. `LocalFilesystemAssetStorage` explicitly
implements the protocol.

## Local filesystem adapter

`LocalFilesystemAssetStorage` receives a trusted base directory through its
constructor. The configured root is resolved once and is not exposed on the
returned `Asset`.

For each ingest, the adapter performs the following sequence:

1. Copy the supplied bytes-like input to immutable `bytes`.
2. Validate logical type, reject path-like informative names, sanitize the
   presentation name, and normalize the detected media type.
3. Compute SHA-256 and byte size from the immutable payload.
4. Generate a canonical UUID identifier.
5. Validate that identifier before path construction and verify that the
   resolved candidate remains a direct child of the configured root.
6. Create the root if necessary and write a new extensionless file using
   exclusive creation, so an existing identifier is never overwritten.
7. Return an immutable `Asset` whose opaque storage reference is the logical
   identifier rather than the physical path.

The physical layout is an Infrastructure detail equivalent to
`<configured-root>/<canonical-uuid>`. Client names, media types, logical types,
and external paths never select the destination. A UUID collision causes a new
identifier attempt; repeated allocation failure is explicit rather than an
overwrite.

`read` applies the same identifier and containment checks. A missing identifier
raises `AssetNotFoundError`. A path that would escape the root, including an
identifier-named symlink resolved outside it, raises `UnsafeStoragePathError`.

## Checksum and traceability guarantees

Every public byte-ingest route is `ingest`; there is no unchecked write method.
The adapter hashes the immutable payload before it creates a destination file.
The returned `size_bytes` and `checksum` therefore describe the exact bytes
accepted for storage. SHA-256 is for integrity and traceability and is not the
public Asset identifier.

Asset metadata persistence and later integrity re-verification are separate
responsibilities. A repository implemented by M4-03 may persist the returned
metadata but must not store large media bytes in SQLite or reinterpret
`storage_reference` as domain identity.

## Validation expectations

The separate ASF test phase should cover at least:

- ingest/read round trips for `bytes`, `bytearray`, and `memoryview`;
- independent verification of `size_bytes` and SHA-256;
- distinct identifiers and storage locations for identical names or bytes;
- rejection of `/`, `\\`, absolute paths, `.`/`..`, malformed UUIDs, and
  identifier paths that resolve outside the configured root;
- sanitized-name normalization and media-type validation;
- missing-asset behavior and exclusive-write collision handling;
- runtime conformance of `LocalFilesystemAssetStorage` to `AssetStoragePort`;
- dependency review proving Core has no filesystem dependency and Application
  can consume the port without importing the concrete adapter.

No deployment, Docker, database, broker, distributed worker, or remote storage
change is required for this implementation.
