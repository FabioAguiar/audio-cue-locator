"""Focused M4-06 coverage for Asset Storage deletion semantics."""

from pathlib import Path
from uuid import uuid4

import pytest

from audio_cue_locator.core.asset import (
    AssetStoragePort,
    AssetType,
    InvalidAssetIdentifierError,
)
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
