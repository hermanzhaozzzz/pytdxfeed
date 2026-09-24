"""Tests for shared runtime safety and reconciliation rules."""

from __future__ import annotations

import io
import json
import plistlib
import select
import signal
import socket
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import pytdxfeed.runtime as runtime
from pytdxfeed import TdxApiClient, TdxApiDeploymentError


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
    assert manifest["artifact_generation"] == 8
    assert "patches" not in manifest
    for asset in manifest["assets"].values():
        path = asset_root / asset["filename"]
        assert path.is_file()
        assert runtime._sha256(path) == asset["sha256"]


def test_release_version_fields_agree() -> None:
    repository = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repository / "pyproject.toml").read_text())["project"]
    locked = tomllib.loads((repository / "uv.lock").read_text())["package"]
    assert project["version"] == runtime.__version__
    assert next(item for item in locked if item["name"] == "pytdxfeed")["version"] == (
        runtime.__version__
    )
    for name in ("README.md", "README_zh.md"):
        assert f"v{runtime.__version__}" in (repository / name).read_text()


def test_old_launchagent_is_removed_only_after_binary_verification(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": runtime.LAUNCHD_LABEL,
                "ProgramArguments": [str(manager.binary_path)],
                "WorkingDirectory": str(manager.runtime_root),
            }
        )
    )
    commands = []

    def launchctl(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(
            returncode=0, stdout=f"\tprogram = {manager.binary_path}\n"
        )

    monkeypatch.setattr(manager, "_launchctl", launchctl)
    monkeypatch.setattr(runtime, "_port_open", lambda _port: False)
    manager._retire_launchagent()
    assert commands == [
        ["print", manager.service_target],
        ["bootout", manager.service_target],
    ]
    assert not manager.plist_path.exists()


def test_old_launchagent_with_different_running_program_is_not_stopped(
    tmp_path: Path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    manager.plist_path.parent.mkdir(parents=True)
    manager.plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": runtime.LAUNCHD_LABEL,
                "ProgramArguments": [str(manager.binary_path)],
                "WorkingDirectory": str(manager.runtime_root),
            }
        )
    )
    commands = []

    def launchctl(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="program = /unrelated/program\n")

    monkeypatch.setattr(manager, "_launchctl", launchctl)
    with pytest.raises(TdxApiDeploymentError, match="does not match"):
        manager._retire_launchagent()
    assert commands == [["print", manager.service_target]]
    assert manager.plist_path.exists()


def test_unhealthy_unknown_listener_is_never_stopped(tmp_path: Path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_healthy", lambda: False)
    monkeypatch.setattr(runtime, "_port_open", lambda _port: True)
    monkeypatch.setattr(
        manager, "_start_service", lambda: pytest.fail("unknown listener was replaced")
    )
    with pytest.raises(TdxApiDeploymentError, match="unhealthy unknown"):
        manager.ensure()


_FAKE_SERVICE = """\
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({"status": "healthy", "pid": os.getpid()}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

HTTPServer.allow_reuse_address = True
HTTPServer(("127.0.0.1", int(os.environ["TDX_API_PORT"])), Handler).serve_forever()
"""

_CALLER = """\
import os
import sys
import time
from pathlib import Path
from pytdxfeed import TdxApiClient
from pytdxfeed.runtime import _RuntimeManager, _SERVICE_PROCESSES

client = TdxApiClient(timeout_sec=0.2, port=int(sys.argv[2]))
manager = _RuntimeManager(client=client, home_dir=Path(sys.argv[1]),
                          startup_timeout_sec=5, port=client.port)
info = manager.ensure()
assert info.managed and info.launchd_label is None
if len(sys.argv) > 3:
    reader, writer = os.pipe()
    if os.fork() == 0:
        os.close(reader)
        assert not _SERVICE_PROCESSES
        if sys.argv[3] == "fork_owner":
            manager.ensure()
        os.write(writer, b"r")
        os.close(writer)
        time.sleep(3)
        os._exit(0)
    os.close(writer)
    os.read(reader, 1)
    os.close(reader)
print("READY", flush=True)
while sys.stdin.readline().strip() == "ensure":
    manager.ensure()
    assert len(_SERVICE_PROCESSES) == 1
    print("READY", flush=True)
client.close()
"""


def _wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("subprocess lifecycle did not settle")


@pytest.fixture
def managed_service(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    manager = _manager(tmp_path, port=port)
    manager._prepare_directories()
    manager.binary_path.write_text(f"#!{sys.executable}\n" + _FAKE_SERVICE)
    manager.binary_path.chmod(0o755)
    manager.static_path.mkdir()
    assets = runtime._load_asset_manifest()
    manager.manifest_path.write_text(
        json.dumps(
            {
                "api_major": assets["api_major"],
                "artifact_generation": assets["artifact_generation"],
                "source_sha": assets["source_sha"],
                "service_port": port,
            }
        )
    )
    callers = []

    def start(*, fork=False, wait_ready=True):
        process = subprocess.Popen(  # noqa: S603 - fixed Python test caller
            [sys.executable, "-c", _CALLER, str(tmp_path), str(port)]
            + ([str(fork)] if fork else []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        callers.append(process)
        if wait_ready:
            ready(process)
        return process

    def ready(process):
        assert select.select([process.stdout], [], [], 10)[0], (
            "caller startup timed out"
        )
        line = process.stdout.readline()
        assert line.strip() == "READY", process.stderr.read()

    yield manager, start, ready
    for process in callers:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
    _wait_for(lambda: not manager._service_running())
    assert not runtime._port_open(port)


@pytest.mark.parametrize("exit_mode", ["normal", "terminate", "kill"])
def test_service_stops_when_sole_caller_exits(managed_service, exit_mode):
    manager, start, _ = managed_service
    process = start()
    if exit_mode == "normal":
        process.communicate("exit\n", timeout=5)
    else:
        process.send_signal(
            signal.SIGTERM if exit_mode == "terminate" else signal.SIGKILL
        )
        process.communicate(timeout=5)
    _wait_for(lambda: not runtime._port_open(manager.port))
    assert not manager.plist_path.parent.exists()


@pytest.mark.parametrize("killed", [False, True])
def test_first_caller_exit_preserves_service_for_second_caller(managed_service, killed):
    manager, start, _ = managed_service
    first = start()
    second = start()
    with TdxApiClient(port=manager.port) as client:
        original_pid = client.health()["pid"]
        if killed:
            first.kill()
            first.communicate(timeout=5)
        else:
            first.communicate("exit\n", timeout=5)
        assert client.health()["pid"] == original_pid
        second.communicate("exit\n", timeout=5)
    _wait_for(lambda: not runtime._port_open(manager.port))


def test_concurrent_callers_and_shutdown_startup_race(managed_service):
    manager, start, ready = managed_service
    for _ in range(4):
        first = start(wait_ready=False)
        second = start(wait_ready=False)
        ready(first)
        ready(second)
        first.communicate("exit\n", timeout=5)
        second.stdin.write("exit\n")
        second.stdin.flush()
        successor = start()
        second.communicate(timeout=5)
        with TdxApiClient(port=manager.port) as client:
            assert client.health()["status"] == "healthy"
        successor.communicate("exit\n", timeout=5)
    _wait_for(lambda: not runtime._port_open(manager.port))


def test_forked_worker_does_not_inherit_parent_lease(managed_service):
    manager, start, _ = managed_service
    parent = start(fork=True)
    parent.stdin.write("exit\n")
    parent.stdin.flush()
    parent.wait(timeout=5)
    _wait_for(lambda: not runtime._port_open(manager.port), timeout=2)


def test_forked_worker_can_acquire_its_own_lease(managed_service):
    manager, start, _ = managed_service
    parent = start(fork="fork_owner")
    with TdxApiClient(port=manager.port) as client:
        original_pid = client.health()["pid"]
        parent.stdin.write("exit\n")
        parent.stdin.flush()
        parent.wait(timeout=5)
        assert client.health()["pid"] == original_pid
    _wait_for(lambda: not runtime._port_open(manager.port))


def test_failed_startup_releases_lease_and_exits_helper(managed_service):
    manager, start, _ = managed_service
    manager.binary_path.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
    caller = start(wait_ready=False)
    stdout, stderr = caller.communicate(timeout=8)
    assert caller.returncode != 0
    assert "READY" not in stdout
    assert "exited before becoming healthy (exit=1)" in stderr
    assert "elapsed=" in stderr
    assert "startup timeout=" not in stderr
    _wait_for(lambda: not manager._service_running())
    assert not runtime._port_open(manager.port)


def test_startup_timeout_reports_measured_elapsed_time(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    manager.client.health = lambda: (_ for _ in ()).throw(runtime.TdxApiError("busy"))
    ticks = iter([10.0, 10.0, 11.2, 11.2])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    with pytest.raises(TdxApiDeploymentError, match=r"timeout=1s.*elapsed=1.2s"):
        manager._wait_until_healthy(SimpleNamespace(poll=lambda: None))


def test_joining_caller_stops_waiting_when_helper_exits(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    monkeypatch.setattr(manager, "_service_running", lambda: False)
    with pytest.raises(TdxApiDeploymentError, match="managed service exited") as error:
        manager._wait_until_healthy()
    assert "startup timeout=" not in str(error.value)


def test_short_lived_child_exits_helper_and_explicit_ensure_can_restart(
    managed_service,
):
    manager, start, ready = managed_service
    short_lived = _FAKE_SERVICE.replace(
        "HTTPServer.allow_reuse_address = True",
        "import threading\n"
        "threading.Timer(0.6, lambda: os._exit(0)).start()\n"
        "HTTPServer.allow_reuse_address = True",
    )
    manager.binary_path.write_text(f"#!{sys.executable}\n" + short_lived)
    caller = start()
    with TdxApiClient(port=manager.port) as client:
        first_pid = client.health()["pid"]
        _wait_for(lambda: not manager._service_running())
        assert not runtime._port_open(manager.port)
        manager.binary_path.write_text(f"#!{sys.executable}\n" + _FAKE_SERVICE)
        caller.stdin.write("ensure\n")
        caller.stdin.flush()
        ready(caller)
        assert client.health()["pid"] != first_pid
    caller.communicate("exit\n", timeout=5)
