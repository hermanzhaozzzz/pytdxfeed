"""Shared macOS lifecycle manager for the bundled tdx-api service."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import plistlib
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from pytdxfeed._version import __version__
from pytdxfeed.client import (
    DEFAULT_PORT,
    HOST,
    TdxApiClient,
    local_base_url,
    validate_port,
)
from pytdxfeed.errors import TdxApiDeploymentError, TdxApiError

PORT = DEFAULT_PORT
SOURCE_SHA = "10e718f4844eb797e5d3750c0bfba9150a7866b7"
UPSTREAM_BASE_SHA = "ea07dccb67aeb92ebde851ac31503b4bf457a318"
# Retained as a public compatibility alias for callers that displayed the old field.
UPSTREAM_SHA = UPSTREAM_BASE_SHA
LAUNCHD_LABEL = "com.hermanzhaozzzz.pytdxfeed.tdx-api"
LEGACY_VQT_LABEL = "com.hermanzhaozzzz.vqt.tdx-api"
RUNTIME_DIRNAME = ".pytdxfeed"
LEGACY_RUNTIME_DIRNAME = ".tdx-api"
ASSET_MANIFEST = "manifest.json"
STARTUP_TIMEOUT_SEC = 120.0
PORT_RELEASE_TIMEOUT_SEC = 5.0


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """Description of the healthy local service selected for this process."""

    base_url: str
    port: int
    managed: bool
    launchd_label: str | None
    runtime_root: Path | None
    source_sha: str | None
    upstream_base_sha: str | None
    upstream_sha: str | None
    artifact_generation: int | None


def ensure_service(
    *,
    timeout_sec: float = 2.0,
    startup_timeout_sec: float = STARTUP_TIMEOUT_SEC,
    port: int = DEFAULT_PORT,
) -> ServiceInfo:
    """Return a healthy service, deploying the shared runtime on macOS if needed."""
    resolved_port = validate_port(port)
    client = TdxApiClient(timeout_sec=timeout_sec, port=resolved_port)
    try:
        if platform.system() != "Darwin":
            try:
                client.health()
            except TdxApiError as exc:
                raise TdxApiDeploymentError(
                    "[pytdxfeed] tdx-api is unavailable; automatic deployment "
                    "is supported only on macOS"
                ) from exc
            return _external_service_info(resolved_port)
        return _RuntimeManager(
            client=client,
            home_dir=Path.home(),
            startup_timeout_sec=startup_timeout_sec,
            port=resolved_port,
        ).ensure()
    finally:
        client.close()


class _RuntimeManager:
    def __init__(
        self,
        *,
        client: TdxApiClient,
        home_dir: Path,
        startup_timeout_sec: float,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.client = client
        self.home_dir = home_dir.expanduser().resolve()
        self.runtime_root = self.home_dir / RUNTIME_DIRNAME
        self.binary_path = self.runtime_root / "bin" / "tdx-api"
        self.static_path = self.runtime_root / "static"
        self.data_path = self.runtime_root / "data" / "database"
        self.logs_path = self.runtime_root / "logs"
        self.manifest_path = self.runtime_root / "deployment.json"
        self.lock_path = self.runtime_root / "deployment.lock"
        self.stdout_log_path = self.logs_path / "tdx-api.stdout.log"
        self.stderr_log_path = self.logs_path / "tdx-api.stderr.log"
        self.install_log_path = self.logs_path / "deployment.log"
        self.plist_path = (
            self.home_dir / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        )
        self.legacy_vqt_plist_path = (
            self.home_dir / "Library" / "LaunchAgents" / f"{LEGACY_VQT_LABEL}.plist"
        )
        self.domain = f"gui/{os.getuid()}"
        self.service_target = f"{self.domain}/{LAUNCHD_LABEL}"
        self.legacy_vqt_target = f"{self.domain}/{LEGACY_VQT_LABEL}"
        self.startup_timeout_sec = float(startup_timeout_sec)
        self.port = validate_port(port)
        self.base_url = local_base_url(self.port)
        if self.startup_timeout_sec <= 0:
            raise ValueError("startup_timeout_sec must be positive")

    def ensure(self) -> ServiceInfo:
        """Reconcile the single per-user service."""
        new_loaded = self._job_loaded(self.service_target)
        legacy_loaded = self._job_loaded(self.legacy_vqt_target)
        if not new_loaded and not legacy_loaded and self._healthy():
            return _external_service_info(self.port)

        self._prepare_directories()
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                return self._ensure_locked()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _ensure_locked(self) -> ServiceInfo:
        new_loaded = self._job_loaded(self.service_target)
        legacy_loaded = self._job_loaded(self.legacy_vqt_target)
        healthy = self._healthy()
        asset_manifest = _load_asset_manifest()
        deployment = self._read_json(self.manifest_path)
        deployed_port = self._deployed_port(deployment)

        if legacy_loaded:
            self._seed_legacy_data()
            return self._deploy(
                asset_manifest,
                old_target=self.legacy_vqt_target,
                old_plist=self.legacy_vqt_plist_path,
                remove_legacy_plist=True,
                old_port=DEFAULT_PORT,
            )

        if new_loaded:
            acceptable = self._deployment_acceptable(deployment, asset_manifest)
            if acceptable:
                if not healthy:
                    self._wait_until_healthy()
                return self._managed_service_info(deployment)
            if deployed_port != self.port:
                if healthy:
                    self._retire_managed_service(deployed_port)
                    return _external_service_info(self.port)
                if _port_open(self.port):
                    raise TdxApiDeploymentError(
                        f"[pytdxfeed] {HOST}:{self.port} is occupied by an "
                        "unhealthy unknown process; it will not be reused or terminated"
                    )
            return self._deploy(
                asset_manifest,
                old_target=self.service_target,
                old_plist=self.plist_path,
                remove_legacy_plist=False,
                old_port=deployed_port,
            )

        if healthy:
            return _external_service_info(self.port)
        if _port_open(self.port):
            raise TdxApiDeploymentError(
                f"[pytdxfeed] {HOST}:{self.port} is occupied by an unhealthy "
                "unknown process; it will not be reused or terminated"
            )

        self._seed_legacy_data()
        return self._deploy(
            asset_manifest,
            old_target=None,
            old_plist=None,
            remove_legacy_plist=False,
            old_port=None,
        )

    def _deployment_acceptable(
        self, deployment: Mapping[str, Any] | None, assets: Mapping[str, Any]
    ) -> bool:
        if deployment is None:
            return False
        deployed_api = int(deployment.get("api_major", 0) or 0)
        expected_api = int(assets["api_major"])
        deployed_generation = int(deployment.get("artifact_generation", 0) or 0)
        expected_generation = int(assets["artifact_generation"])
        if deployed_api > expected_api:
            raise TdxApiDeploymentError(
                "[pytdxfeed] the managed service API is newer than this client; "
                "refusing to downgrade it"
            )
        return (
            deployed_api == expected_api
            and deployed_generation >= expected_generation
            and (
                deployed_generation > expected_generation
                or deployment.get("source_sha") == assets["source_sha"]
            )
            and int(deployment.get("service_port", 0) or 0) == self.port
            and self.binary_path.is_file()
            and self.static_path.is_dir()
        )

    def _deploy(
        self,
        asset_manifest: Mapping[str, Any],
        *,
        old_target: str | None,
        old_plist: Path | None,
        remove_legacy_plist: bool,
        old_port: int | None,
    ) -> ServiceInfo:
        old_plist_bytes = _read_bytes(old_plist) if old_plist is not None else None
        with tempfile.TemporaryDirectory(
            prefix="rollback-", dir=self.runtime_root
        ) as rollback_text:
            rollback = Path(rollback_text)
            self._snapshot_runtime(rollback)
            try:
                deployment = self._install_asset(asset_manifest)
                desired_plist = plistlib.dumps(self._plist_payload(), sort_keys=True)
                _write_bytes_atomic(self.plist_path, desired_plist, mode=0o644)
                if old_target is not None and self._job_loaded(old_target):
                    self._launchctl(["bootout", old_target])
                    self._wait_for_port_release(old_port or self.port)
                if _port_open(self.port):
                    raise TdxApiDeploymentError(
                        f"[pytdxfeed] port {self.port} became occupied before bootstrap"
                    )
                self._launchctl(["bootstrap", self.domain, str(self.plist_path)])
                self._launchctl(["kickstart", "-k", self.service_target])
                self._wait_until_healthy()
            except Exception as exc:
                self._launchctl(["bootout", self.service_target], check=False)
                self._restore_runtime(rollback)
                if old_plist is not None and old_plist_bytes is not None:
                    _write_bytes_atomic(old_plist, old_plist_bytes, mode=0o644)
                    self._launchctl(
                        ["bootstrap", self.domain, str(old_plist)], check=False
                    )
                    if old_target is not None:
                        self._launchctl(["kickstart", "-k", old_target], check=False)
                if isinstance(exc, TdxApiDeploymentError):
                    raise
                raise TdxApiDeploymentError(
                    f"[pytdxfeed] deployment failed: {exc}"
                ) from exc

        if (
            remove_legacy_plist
            and old_plist is not None
            and old_plist != self.plist_path
        ):
            old_plist.unlink(missing_ok=True)
        return self._managed_service_info(deployment)

    def _install_asset(self, asset_manifest: Mapping[str, Any]) -> dict[str, Any]:
        architecture = _architecture()
        asset = asset_manifest["assets"].get(architecture)
        if not isinstance(asset, dict):
            raise TdxApiDeploymentError(
                f"[pytdxfeed] no bundled asset for architecture {architecture}"
            )
        filename = str(asset["filename"])
        expected_checksum = str(asset["sha256"])
        asset_ref = resources.files("pytdxfeed").joinpath("assets", filename)
        with resources.as_file(asset_ref) as archive:
            actual_checksum = _sha256(archive)
            if actual_checksum != expected_checksum:
                raise TdxApiDeploymentError(
                    "[pytdxfeed] bundled asset checksum mismatch: "
                    f"expected={expected_checksum} actual={actual_checksum}"
                )
            with tempfile.TemporaryDirectory(
                prefix="install-", dir=self.runtime_root
            ) as install_text:
                install = Path(install_text)
                _extract_archive_safe(archive, install)
                source_binary = install / "bin" / "tdx-api"
                source_static = install / "static"
                source_data = install / "data" / "database"
                if not source_binary.is_file() or not source_static.is_dir():
                    raise TdxApiDeploymentError(
                        "[pytdxfeed] bundled asset is missing binary or static files"
                    )
                temp_binary = self.binary_path.with_name(
                    f".{self.binary_path.name}.{os.getpid()}.tmp"
                )
                shutil.copy2(source_binary, temp_binary)
                temp_binary.chmod(0o755)
                temp_binary.replace(self.binary_path)
                self._replace_static(source_static)
                _copy_missing_tree(source_data, self.data_path)

        deployment = {
            "schema_version": 3,
            "api_major": int(asset_manifest["api_major"]),
            "artifact_generation": int(asset_manifest["artifact_generation"]),
            "package_version": __version__,
            "source_url": str(asset_manifest["source_url"]),
            "source_sha": str(asset_manifest["source_sha"]),
            "upstream_url": str(asset_manifest["upstream_url"]),
            "upstream_base_sha": str(asset_manifest["upstream_base_sha"]),
            "architecture": architecture,
            "asset_filename": filename,
            "asset_sha256": expected_checksum,
            "binary_path": str(self.binary_path),
            "service_port": self.port,
            "installed_at": int(time.time()),
        }
        _write_json_atomic(self.manifest_path, deployment)
        return deployment

    def _replace_static(self, source: Path) -> None:
        staged = self.runtime_root / f".static.{os.getpid()}.tmp"
        previous = self.runtime_root / f".static.{os.getpid()}.old"
        shutil.rmtree(staged, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)
        shutil.copytree(source, staged)
        if self.static_path.exists():
            self.static_path.replace(previous)
        staged.replace(self.static_path)
        shutil.rmtree(previous, ignore_errors=True)

    def _seed_legacy_data(self) -> None:
        sources: list[Path] = []
        legacy_payload = _read_plist(self.legacy_vqt_plist_path)
        if legacy_payload is not None:
            working = str(legacy_payload.get("WorkingDirectory") or "").strip()
            if working:
                sources.append(Path(working).expanduser() / "data" / "database")

        old_shared = self.home_dir / LEGACY_RUNTIME_DIRNAME
        old_manifest = self._read_json(old_shared / "deployment.json")
        if old_manifest is not None and (
            old_manifest.get("source_sha") or old_manifest.get("upstream_sha")
        ):
            sources.append(old_shared / "data" / "database")

        for source in sources:
            _copy_missing_tree(source, self.data_path)

    def _snapshot_runtime(self, target: Path) -> None:
        if self.binary_path.is_file():
            (target / "bin").mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.binary_path, target / "bin" / "tdx-api")
        if self.static_path.is_dir():
            shutil.copytree(self.static_path, target / "static")
        if self.manifest_path.is_file():
            shutil.copy2(self.manifest_path, target / "deployment.json")

    def _restore_runtime(self, source: Path) -> None:
        backup_binary = source / "bin" / "tdx-api"
        if backup_binary.is_file():
            shutil.copy2(backup_binary, self.binary_path)
            self.binary_path.chmod(0o755)
        backup_static = source / "static"
        if backup_static.is_dir():
            self._replace_static(backup_static)
        backup_manifest = source / "deployment.json"
        if backup_manifest.is_file():
            shutil.copy2(backup_manifest, self.manifest_path)

    def _prepare_directories(self) -> None:
        for path in (
            self.runtime_root,
            self.binary_path.parent,
            self.data_path,
            self.logs_path,
            self.plist_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.stdout_log_path.touch(exist_ok=True)
        self.stderr_log_path.touch(exist_ok=True)
        self.install_log_path.touch(exist_ok=True)

    def _plist_payload(self) -> dict[str, object]:
        return {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [str(self.binary_path)],
            "EnvironmentVariables": {"TDX_API_PORT": str(self.port)},
            "WorkingDirectory": str(self.runtime_root),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 30,
            "ProcessType": "Standard",
            "StandardOutPath": str(self.stdout_log_path),
            "StandardErrorPath": str(self.stderr_log_path),
        }

    def _wait_for_port_release(self, port: int) -> None:
        deadline = time.monotonic() + PORT_RELEASE_TIMEOUT_SEC
        while _port_open(port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _port_open(port):
            raise TdxApiDeploymentError(
                f"[pytdxfeed] LaunchAgent stopped but port {port} is still occupied"
            )

    def _retire_managed_service(self, deployed_port: int) -> None:
        """Stop a known managed job before reusing a healthy external service."""
        self._launchctl(["bootout", self.service_target])
        self._wait_for_port_release(deployed_port)
        self.plist_path.unlink(missing_ok=True)

    def _deployed_port(self, deployment: Mapping[str, Any] | None) -> int:
        """Read the last managed port without guessing from an unknown process."""
        if deployment is not None:
            raw = deployment.get("service_port")
            if isinstance(raw, int) and not isinstance(raw, bool):
                try:
                    return validate_port(raw)
                except ValueError:
                    pass
        payload = _read_plist(self.plist_path)
        environment = payload.get("EnvironmentVariables") if payload else None
        if isinstance(environment, dict):
            raw = environment.get("TDX_API_PORT")
            try:
                parsed = int(str(raw))
            except (TypeError, ValueError):
                pass
            else:
                try:
                    return validate_port(parsed)
                except ValueError:
                    pass
        return DEFAULT_PORT

    def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_sec
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.client.health()
                return
            except TdxApiError as exc:
                last_error = exc
                time.sleep(0.5)
        launch_state = self._launchctl(
            ["print", self.service_target], check=False
        ).stdout
        stderr_tail = _tail_file(self.stderr_log_path)
        raise TdxApiDeploymentError(
            "[pytdxfeed] service did not become healthy within "
            f"{self.startup_timeout_sec:.0f}s; last_error={last_error}\n"
            f"launchctl:\n{_tail_text(launch_state)}\n"
            f"stderr:\n{stderr_tail}"
        )

    def _healthy(self) -> bool:
        try:
            self.client.health()
        except TdxApiError:
            return False
        return True

    def _job_loaded(self, target: str) -> bool:
        return self._launchctl(["print", target], check=False).returncode == 0

    def _launchctl(
        self, args: Sequence[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(  # noqa: S603 - fixed executable with explicit argv
            ["/bin/launchctl", *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
        if check and result.returncode != 0:
            raise TdxApiDeploymentError(
                f"[pytdxfeed] launchctl {' '.join(args)} failed "
                f"with exit={result.returncode}:\n{_tail_text(result.stdout)}"
            )
        return result

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _managed_service_info(self, deployment: Mapping[str, Any]) -> ServiceInfo:
        return ServiceInfo(
            base_url=self.base_url,
            port=self.port,
            managed=True,
            launchd_label=LAUNCHD_LABEL,
            runtime_root=self.runtime_root,
            source_sha=(str(deployment.get("source_sha") or "") or None),
            upstream_base_sha=str(
                deployment.get("upstream_base_sha") or UPSTREAM_BASE_SHA
            ),
            upstream_sha=str(
                deployment.get("upstream_base_sha")
                or deployment.get("upstream_sha")
                or UPSTREAM_BASE_SHA
            ),
            artifact_generation=int(deployment.get("artifact_generation", 0) or 0),
        )


def _external_service_info(port: int) -> ServiceInfo:
    return ServiceInfo(
        base_url=local_base_url(port),
        port=port,
        managed=False,
        launchd_label=None,
        runtime_root=None,
        source_sha=None,
        upstream_base_sha=None,
        upstream_sha=None,
        artifact_generation=None,
    )


def _load_asset_manifest() -> dict[str, Any]:
    manifest_ref = resources.files("pytdxfeed").joinpath("assets", ASSET_MANIFEST)
    try:
        payload = json.loads(manifest_ref.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TdxApiDeploymentError(
            f"[pytdxfeed] bundled asset manifest is invalid: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 2
        or payload.get("source_sha") != SOURCE_SHA
        or payload.get("upstream_base_sha") != UPSTREAM_BASE_SHA
    ):
        raise TdxApiDeploymentError(
            "[pytdxfeed] bundled asset manifest does not match the locked source"
        )
    return payload


def _architecture() -> str:
    machine = platform.machine().lower()
    aliases = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }
    try:
        return aliases[machine]
    except KeyError as exc:
        raise TdxApiDeploymentError(
            f"[pytdxfeed] unsupported macOS architecture: {machine}"
        ) from exc


def _port_open(port: int = DEFAULT_PORT) -> bool:
    resolved_port = validate_port(port)
    try:
        with socket.create_connection((HOST, resolved_port), timeout=0.25):
            return True
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_archive_safe(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise TdxApiDeploymentError(
                    f"[pytdxfeed] unsafe archive member: {member.name}"
                )
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise TdxApiDeploymentError(
                    f"[pytdxfeed] unsupported archive member: {member.name}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise TdxApiDeploymentError(
                    f"[pytdxfeed] cannot read archive member: {member.name}"
                )
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def _copy_missing_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _read_plist(path: Path) -> dict[str, Any] | None:
    try:
        payload = plistlib.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_bytes(path: Path | None) -> bytes | None:
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    _write_bytes_atomic(path, encoded, mode=0o644)


def _write_bytes_atomic(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(payload)
        temp.chmod(mode)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _tail_text(value: str, *, lines: int = 80) -> str:
    return "\n".join(str(value or "").splitlines()[-lines:])


def _tail_file(path: Path) -> str:
    try:
        return _tail_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return f"<cannot read {path}: {exc}>"
