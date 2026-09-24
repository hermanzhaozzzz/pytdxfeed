"""Run tdx-api until the last calling Python process releases its file lock."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _exit_on_signal(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def run(runtime_root: Path, port: int) -> None:
    """Own only our child process; file locks contain no persisted process IDs."""
    signal.signal(signal.SIGTERM, _exit_on_signal)
    signal.signal(signal.SIGINT, _exit_on_signal)
    with (
        (runtime_root / "service.lock").open("a+") as owner,
        (runtime_root / "users.lock").open("a+") as users,
    ):
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process = subprocess.Popen(  # noqa: S603 - installed bundled executable
            [str(runtime_root / "bin" / "tdx-api")],
            cwd=runtime_root,
            env={**os.environ, "TDX_API_PORT": str(port)},
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        try:
            while process.poll() is None:
                # Wait without holding the deployment lock needed by new callers.
                try:
                    fcntl.flock(users, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    time.sleep(0.1)
                    continue
                fcntl.flock(users, fcntl.LOCK_UN)
                with (runtime_root / "deployment.lock").open("a+") as deployment:
                    fcntl.flock(deployment, fcntl.LOCK_EX)
                    try:
                        fcntl.flock(users, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        # A caller joined before shutdown acquired the lock.
                        continue
                    _stop(process)
                    fcntl.flock(owner, fcntl.LOCK_UN)
                    return
            exit_code = process.returncode
            raise SystemExit(exit_code if exit_code >= 0 else 128 - exit_code)
        finally:
            _stop(process)


if __name__ == "__main__":
    run(Path(sys.argv[1]), int(sys.argv[2]))
