"""Tests for shared runtime safety and reconciliation rules."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import pytdxfeed.runtime as runtime
from pytdxfeed import TdxApiDeploymentError


def _manager(tmp_path: Path):
    client = SimpleNamespace(health=lambda: {"status": "healthy"})
    return runtime._RuntimeManager(
        client=client,
        home_dir=tmp_path,
        startup_timeout_sec=1,
    )


def test_runtime_paths_are_package_named(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager.runtime_root == tmp_path / ".pytdxfeed"
    assert manager.binary_path == tmp_path / ".pytdxfeed/bin/tdx-api"
    assert manager.data_path == tmp_path / ".pytdxfeed/data/database"


def test_healthy_unknown_service_is_reused_without_directories(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_job_loaded", lambda _target: False)
    info = manager.ensure()
    assert info.managed is False
    assert not manager.runtime_root.exists()


def test_newer_generation_is_never_downgraded(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.binary_path.parent.mkdir(parents=True)
    manager.binary_path.touch()
    manager.static_path.mkdir()
    assert manager._deployment_acceptable(
        {"api_major": 1, "artifact_generation": 2},
        {"api_major": 1, "artifact_generation": 1},
    )


def test_newer_api_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(TdxApiDeploymentError, match="newer"):
        manager._deployment_acceptable(
            {"api_major": 2, "artifact_generation": 1},
            {"api_major": 1, "artifact_generation": 1},
        )


def test_copy_missing_tree_does_not_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "database.db").write_text("old", encoding="utf-8")
    (source / "new.db").write_text("new", encoding="utf-8")
    (destination / "database.db").write_text("valuable", encoding="utf-8")
    runtime._copy_missing_tree(source, destination)
    assert (destination / "database.db").read_text(encoding="utf-8") == "valuable"
    assert (destination / "new.db").read_text(encoding="utf-8") == "new"


def test_named_legacy_directory_requires_manifest(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    source = tmp_path / ".tdx-api/data/database"
    source.mkdir(parents=True)
    (source / "legacy.db").write_text("legacy", encoding="utf-8")
    manager.data_path.mkdir(parents=True)
    manager._seed_legacy_data()
    assert not (manager.data_path / "legacy.db").exists()
    (tmp_path / ".tdx-api/deployment.json").write_text(
        json.dumps({"upstream_sha": runtime.UPSTREAM_SHA}), encoding="utf-8"
    )
    manager._seed_legacy_data()
    assert (manager.data_path / "legacy.db").is_file()


def test_archive_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(TdxApiDeploymentError, match="unsafe"):
        runtime._extract_archive_safe(archive, tmp_path / "output")


def test_bundled_asset_manifest_and_checksums_are_valid() -> None:
    manifest = runtime._load_asset_manifest()
    asset_root = Path(runtime.__file__).parent / "assets"
    assert manifest["upstream_sha"] == runtime.UPSTREAM_SHA
    for asset in manifest["assets"].values():
        path = asset_root / asset["filename"]
        assert path.is_file()
        assert runtime._sha256(path) == asset["sha256"]
