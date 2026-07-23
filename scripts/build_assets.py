"""Build deterministic macOS tdx-api assets for pytdxfeed."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

SOURCE_URL = "https://github.com/hermanzhaozzzz/tdx-api.git"
SOURCE_SHA = "e6778c6b8ec4cbf0705dc1fe4c3c413a6b6518ff"
UPSTREAM_URL = "https://github.com/oficcejo/tdx-api.git"
UPSTREAM_BASE_SHA = "ea07dccb67aeb92ebde851ac31503b4bf457a318"
ARTIFACT_GENERATION = 5
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GO_VERSION_RE = re.compile(r"\bgo(\d+)\.(\d+)(?:\.\d+)?\b")
ARCHITECTURES = ("arm64", "x86_64")
GO_ARCHITECTURES = {"arm64": "arm64", "x86_64": "amd64"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "pytdxfeed" / "assets",
    )
    args = parser.parse_args()

    go = _require_go()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pytdxfeed-assets-") as temp_text:
        temp = Path(temp_text)
        source = temp / "source"
        source_origin = (
            args.source.expanduser().resolve() if args.source else SOURCE_URL
        )
        if args.source is not None:
            _verify_source(source_origin)
        _run(["git", "clone", "--no-checkout", str(source_origin), str(source)])
        _run(["git", "-C", str(source), "checkout", "--detach", SOURCE_SHA])
        _verify_source(source)

        test_source = temp / "test-source"
        _run(["git", "clone", "--no-checkout", str(source), str(test_source)])
        _run(["git", "-C", str(test_source), "checkout", "--detach", SOURCE_SHA])
        _run([str(go), "test", "./..."], cwd=test_source)
        _run([str(go), "test", "./..."], cwd=test_source / "web")

        assets: dict[str, dict[str, str]] = {}
        for architecture in ARCHITECTURES:
            root = temp / f"tdx-api-{architecture}"
            binary = root / "bin" / "tdx-api"
            binary.parent.mkdir(parents=True)
            environment = {
                **os.environ,
                "CGO_ENABLED": "0",
                "GOOS": "darwin",
                "GOARCH": GO_ARCHITECTURES[architecture],
            }
            _run(
                [
                    str(go),
                    "build",
                    "-trimpath",
                    "-ldflags=-s -w",
                    "-o",
                    str(binary),
                    ".",
                ],
                cwd=source / "web",
                env=environment,
            )
            binary.chmod(0o755)
            shutil.copytree(source / "web" / "static", root / "static")
            shutil.copytree(
                source / "web" / "data" / "database",
                root / "data" / "database",
            )
            filename = f"tdx-api-{SOURCE_SHA[:12]}-darwin-{architecture}.tar.gz"
            archive = args.output / filename
            _write_deterministic_archive(root, archive)
            assets[architecture] = {
                "filename": filename,
                "sha256": _sha256(archive),
            }

    manifest = {
        "schema_version": 2,
        "api_major": 1,
        "artifact_generation": ARTIFACT_GENERATION,
        "source_url": SOURCE_URL,
        "source_sha": SOURCE_SHA,
        "upstream_url": UPSTREAM_URL,
        "upstream_base_sha": UPSTREAM_BASE_SHA,
        "assets": assets,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    expected_archives = {asset["filename"] for asset in assets.values()}
    for archive in args.output.glob("tdx-api-*-darwin-*.tar.gz"):
        if archive.name not in expected_archives:
            archive.unlink()


def _require_go() -> Path:
    raw = shutil.which("go")
    if raw is None:
        raise SystemExit("Go 1.23+ is required to regenerate bundled assets")
    result = _run([raw, "version"])
    match = GO_VERSION_RE.search(result.stdout)
    if match is None or (int(match.group(1)), int(match.group(2))) < (1, 23):
        raise SystemExit(f"Go 1.23+ is required, found: {result.stdout.strip()}")
    return Path(raw).resolve()


def _verify_source(source: Path) -> None:
    head = _run(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.strip()
    if head != SOURCE_SHA:
        raise SystemExit(f"source HEAD is {head}, expected {SOURCE_SHA}")
    if not (source / "web" / "go.mod").is_file():
        raise SystemExit(f"source does not contain web/go.mod: {source}")
    status = _run(
        ["git", "-C", str(source), "status", "--short", "--untracked-files=all"]
    ).stdout.strip()
    if status:
        raise SystemExit(f"source checkout is dirty:\n{status}")


def _write_deterministic_archive(source: Path, destination: Path) -> None:
    with (
        destination.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT) as tar,
    ):
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            info = tar.gettarinfo(str(path), arcname=str(relative))
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            if path.is_file():
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
            else:
                tar.addfile(info)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        rendered = " ".join(command)
        raise SystemExit(
            f"command failed ({result.returncode}): {rendered}\n{result.stdout}"
        )
    return result


if __name__ == "__main__":
    main()
