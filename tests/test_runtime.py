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


def _manager(tmp_path: Path, *, port: int = runtime.DEFAULT_PORT):
    client = SimpleNamespace(health=lambda: {"status": "healthy"})
    return runtime._RuntimeManager(
        client=client,
        home_dir=tmp_path,
        startup_timeout_sec=1,
        port=port,
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
        {"api_major": 1, "artifact_generation": 2, "service_port": 8080},
        {"api_major": 1, "artifact_generation": 1},
    )


def test_newer_api_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with pytest.raises(TdxApiDeploymentError, match="newer"):
        manager._deployment_acceptable(
            {"api_major": 2, "artifact_generation": 1, "service_port": 8080},
            {"api_major": 1, "artifact_generation": 1},
        )


def test_same_generation_requires_locked_source_sha(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.binary_path.parent.mkdir(parents=True)
    manager.binary_path.touch()
    manager.static_path.mkdir()

    assert not manager._deployment_acceptable(
        {
            "api_major": 1,
            "artifact_generation": 4,
            "service_port": 8080,
            "source_sha": "0" * 40,
        },
        {
            "api_major": 1,
            "artifact_generation": 4,
            "source_sha": runtime.SOURCE_SHA,
        },
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


def test_runtime_plist_contains_configured_port(tmp_path: Path) -> None:
    manager = _manager(tmp_path, port=18080)

    payload = manager._plist_payload()

    assert payload["EnvironmentVariables"] == {"TDX_API_PORT": "18080"}


def test_managed_port_change_reuses_healthy_external_service(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path, port=18080)
    manager.runtime_root.mkdir()
    manager.manifest_path.write_text(
        json.dumps({"service_port": 8080}), encoding="utf-8"
    )
    monkeypatch.setattr(
        manager,
        "_job_loaded",
        lambda target: target == manager.service_target,
    )
    retired: list[int] = []
    monkeypatch.setattr(manager, "_retire_managed_service", retired.append)

    info = manager._ensure_locked()

    assert retired == [8080]
    assert info.managed is False
    assert info.port == 18080


def test_managed_port_change_rejects_unhealthy_target_without_stopping_old_job(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path, port=18080)
    manager.client.health = lambda: (_ for _ in ()).throw(
        runtime.TdxApiError("unhealthy")
    )
    manager.runtime_root.mkdir()
    manager.manifest_path.write_text(
        json.dumps({"service_port": 8080}), encoding="utf-8"
    )
    monkeypatch.setattr(
        manager,
        "_job_loaded",
        lambda target: target == manager.service_target,
    )
    monkeypatch.setattr(runtime, "_port_open", lambda port: port == 18080)
    retired: list[int] = []
    monkeypatch.setattr(manager, "_retire_managed_service", retired.append)

    with pytest.raises(TdxApiDeploymentError, match=r"18080.*unhealthy"):
        manager._ensure_locked()

    assert retired == []


def test_acceptable_loaded_runtime_waits_for_health_without_redeploying(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    manager.binary_path.parent.mkdir(parents=True)
    manager.binary_path.touch()
    manager.static_path.mkdir()
    manager.runtime_root.mkdir(exist_ok=True)
    manager.manifest_path.write_text(
        json.dumps(
            {
                "api_major": 1,
                "artifact_generation": runtime._load_asset_manifest()[
                    "artifact_generation"
                ],
                "service_port": 8080,
                "source_sha": runtime.SOURCE_SHA,
                "upstream_base_sha": runtime.UPSTREAM_BASE_SHA,
            }
        ),
        encoding="utf-8",
    )
    outcomes: list[object] = [
        runtime.TdxApiError("temporarily busy"),
        runtime.TdxApiError("launchd is restarting"),
        {"status": "healthy"},
    ]

    def health() -> object:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    manager.client.health = health
    monkeypatch.setattr(
        manager,
        "_job_loaded",
        lambda target: target == manager.service_target,
    )
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        manager,
        "_deploy",
        lambda *_args, **_kwargs: pytest.fail("healthy deployment was redeployed"),
    )

    info = manager._ensure_locked()

    assert info.managed is True
    assert info.port == 8080
    assert outcomes == []


def test_acceptable_loaded_runtime_health_timeout_never_redeploys(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    manager.binary_path.parent.mkdir(parents=True)
    manager.binary_path.touch()
    manager.static_path.mkdir()
    manager.runtime_root.mkdir(exist_ok=True)
    manager.manifest_path.write_text(
        json.dumps(
            {
                "api_major": 1,
                "artifact_generation": runtime._load_asset_manifest()[
                    "artifact_generation"
                ],
                "service_port": 8080,
                "source_sha": runtime.SOURCE_SHA,
            }
        ),
        encoding="utf-8",
    )
    manager.client.health = lambda: (_ for _ in ()).throw(
        runtime.TdxApiError("still unavailable")
    )
    monkeypatch.setattr(
        manager,
        "_job_loaded",
        lambda target: target == manager.service_target,
    )
    monkeypatch.setattr(
        manager,
        "_wait_until_healthy",
        lambda: (_ for _ in ()).throw(
            TdxApiDeploymentError("service recovery timed out")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_deploy",
        lambda *_args, **_kwargs: pytest.fail("healthy deployment was redeployed"),
    )

    with pytest.raises(TdxApiDeploymentError, match="recovery timed out"):
        manager._ensure_locked()


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
    assert manifest["source_sha"] == runtime.SOURCE_SHA
    assert manifest["upstream_base_sha"] == runtime.UPSTREAM_BASE_SHA
    assert manifest["artifact_generation"] == 7
    assert "patches" not in manifest
    for asset in manifest["assets"].values():
        path = asset_root / asset["filename"]
        assert path.is_file()
        assert runtime._sha256(path) == asset["sha256"]
