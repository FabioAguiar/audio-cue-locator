"""M4 Asset Storage identity, integrity, layout, and path-safety coverage."""

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from audio_cue_locator.core.asset import (
    AssetNotFoundError,
    AssetStorageCollisionError,
    AssetStoragePort,
    AssetType,
    InvalidAssetIdentifierError,
)


@pytest.mark.parametrize(
    "content",
    [
        b"exact audio bytes",
        bytearray(b"exact audio bytes"),
        memoryview(b"exact audio bytes"),
    ],
)
def test_ingest_returns_opaque_identity_checksum_and_exact_byte_round_trip(
    tmp_path: Path, content: bytes | bytearray | memoryview
):
    payload = bytes(content)
    storage_root = tmp_path / "assets"
    storage = LocalFilesystemAssetStorage(storage_root)

    asset = storage.ingest(
        content,
        logical_type=AssetType.CUE,
        informative_name="  Cue.wav  ",
        detected_media_type=" AUDIO/WAV ",
    )

    assert str(UUID(asset.identifier)) == asset.identifier
    assert UUID(asset.identifier).version == 4
    assert asset.storage_reference == asset.identifier
    assert asset.size_bytes == len(payload)
    assert asset.checksum == hashlib.sha256(payload).hexdigest()
    assert asset.checksum_algorithm == "sha256"
    assert asset.sanitized_name == "Cue.wav"
    assert asset.media_type == "audio/wav"
    assert storage.read(asset.identifier) == payload
    assert (storage_root / asset.identifier).read_bytes() == payload
    assert {path.name for path in storage_root.iterdir()} == {asset.identifier}


def test_identical_inputs_receive_distinct_exclusive_storage_locations(tmp_path: Path):
    storage_root = tmp_path / "assets"
    storage = LocalFilesystemAssetStorage(storage_root)
    metadata = {
        "logical_type": AssetType.SOURCE_MEDIA,
        "informative_name": "same.wav",
        "detected_media_type": "audio/wav",
    }

    first = storage.ingest(b"same bytes", **metadata)
    second = storage.ingest(b"same bytes", **metadata)

    assert first.identifier != second.identifier
    assert first.checksum == second.checksum
    assert storage.read(first.identifier) == b"same bytes"
    assert storage.read(second.identifier) == b"same bytes"
    assert {path.name for path in storage_root.iterdir()} == {
        first.identifier,
        second.identifier,
    }


def test_repeated_identifier_collision_never_overwrites_existing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    storage_root = tmp_path / "assets"
    storage_root.mkdir()
    identifier = str(uuid4())
    existing_path = storage_root / identifier
    existing_path.write_bytes(b"original")
    monkeypatch.setattr(
        "audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage."
        "new_asset_identifier",
        lambda: identifier,
    )
    storage = LocalFilesystemAssetStorage(storage_root)

    with pytest.raises(AssetStorageCollisionError):
        storage.ingest(
            b"replacement",
            logical_type=AssetType.CUE,
            informative_name="cue.wav",
            detected_media_type="audio/wav",
        )

    assert existing_path.read_bytes() == b"original"


def test_ingest_rejects_path_like_name_before_creating_storage_root(tmp_path: Path):
    storage_root = tmp_path / "assets"
    storage = LocalFilesystemAssetStorage(storage_root)

    with pytest.raises(ValueError):
        storage.ingest(
            b"audio",
            logical_type=AssetType.SOURCE_MEDIA,
            informative_name="../outside.wav",
            detected_media_type="audio/wav",
        )

    assert not storage_root.exists()


def test_read_rejects_noncanonical_identifiers_before_filesystem_access(
    tmp_path: Path,
):
    storage_root = tmp_path / "assets"
    storage = LocalFilesystemAssetStorage(storage_root)

    with pytest.raises(InvalidAssetIdentifierError):
        storage.read("../outside")

    assert not storage_root.exists()


def test_read_rejects_identifier_named_symlink(tmp_path: Path):
    storage_root = tmp_path / "assets"
    storage_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"must remain")
    identifier = str(uuid4())
    (storage_root / identifier).symlink_to(outside)
    storage = LocalFilesystemAssetStorage(storage_root)

    with pytest.raises(UnsafeStoragePathError):
        storage.read(identifier)

    assert outside.read_bytes() == b"must remain"


def test_read_reports_missing_canonical_identifier(tmp_path: Path):
    storage = LocalFilesystemAssetStorage(tmp_path / "assets")

    with pytest.raises(AssetNotFoundError):
        storage.read(str(uuid4()))
from audio_cue_locator.infrastructure.asset_storage.local_filesystem_storage import (
    LocalFilesystemAssetStorage,
    UnsafeStoragePathError,
)


def test_delete_is_port_conformant_and_idempotent(tmp_path: Path):
    storage = LocalFilesystemAssetStorage(tmp_path / "assets")
    asset = storage.ingest(
        b"audio",
        logical_type=AssetType.SOURCE_MEDIA,
        informative_name="source.wav",
        detected_media_type="audio/wav",
    )

    assert isinstance(storage, AssetStoragePort)
    assert storage.read(asset.identifier) == b"audio"
    assert storage.delete(asset.identifier) is True
    assert storage.delete(asset.identifier) is False


def test_delete_rejects_noncanonical_identifiers_before_filesystem_access(
    tmp_path: Path,
):
    storage = LocalFilesystemAssetStorage(tmp_path / "assets")

    with pytest.raises(InvalidAssetIdentifierError):
        storage.delete("../outside")

    assert not (tmp_path / "assets").exists()


def test_delete_rejects_identifier_named_symlink(tmp_path: Path):
    storage_root = tmp_path / "assets"
    storage_root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"must remain")
    identifier = str(uuid4())
    (storage_root / identifier).symlink_to(outside)
    storage = LocalFilesystemAssetStorage(storage_root)

    with pytest.raises(UnsafeStoragePathError):
        storage.delete(identifier)

    assert outside.read_bytes() == b"must remain"
