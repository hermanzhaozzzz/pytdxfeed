"""Validate release provenance and the live tdx-api refs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-remotes",
        action="store_true",
        help="compare the audited SHAs with the live main branches",
    )
    args = parser.parse_args()

    project = _read_toml(REPOSITORY_ROOT / "pyproject.toml")
    version = str(project["project"]["version"])
    audit = _read_json(
        REPOSITORY_ROOT / "release" / "upstream-audits" / f"v{version}.json"
    )
    manifest = _read_json(
        REPOSITORY_ROOT / "src" / "pytdxfeed" / "assets" / "manifest.json"
    )

    _require(audit.get("schema_version") == 1, "unsupported audit schema")
    _require(audit.get("package_version") == version, "audit version is stale")
    _require(manifest.get("schema_version") == 2, "unsupported manifest schema")

    shared_fields = (
        "api_major",
        "artifact_generation",
        "source_url",
        "source_sha",
        "upstream_url",
        "upstream_base_sha",
    )
    for field in shared_fields:
        _require(
            audit.get(field) == manifest.get(field),
            f"audit and manifest disagree on {field}",
        )

    _require(
        GIT_SHA_RE.fullmatch(str(audit["source_sha"])) is not None,
        "bad source SHA",
    )
    _require(
        GIT_SHA_RE.fullmatch(str(audit["upstream_base_sha"])) is not None,
        "bad upstream base SHA",
    )
    _require(
        GIT_SHA_RE.fullmatch(str(audit["upstream_head_sha"])) is not None,
        "bad upstream HEAD SHA",
    )
    _require(
        audit["upstream_base_sha"] == audit["upstream_head_sha"],
        "upstream HEAD advanced after the recorded base; review and update the audit",
    )

    commits = audit.get("fork_commits")
    _require(isinstance(commits, list) and commits, "fork commit audit is empty")
    _require(
        commits[-1].get("sha") == audit["source_sha"],
        "fork commit audit does not end at source SHA",
    )
    _require(
        all(item.get("required") is True for item in commits),
        "fork audit contains a commit that is not marked required",
    )
    downstream = audit.get("downstream_reviews")
    _require(
        isinstance(downstream, list)
        and {item.get("repository") for item in downstream} == {"vnpy_ths", "VQT"},
        "both downstream reviews are required",
    )

    assets = manifest.get("assets")
    _require(
        isinstance(assets, dict) and set(assets) == {"arm64", "x86_64"},
        "manifest must contain both macOS architectures",
    )
    asset_root = REPOSITORY_ROOT / "src" / "pytdxfeed" / "assets"
    for architecture, asset in assets.items():
        filename = str(asset.get("filename") or "")
        expected = str(asset.get("sha256") or "")
        _require(SHA256_RE.fullmatch(expected) is not None, f"bad {architecture} hash")
        path = asset_root / filename
        _require(path.is_file(), f"missing {architecture} asset: {filename}")
        _require(_sha256(path) == expected, f"{architecture} asset hash mismatch")

    if args.check_remotes:
        _require(
            _remote_main_sha(str(audit["source_url"])) == audit["source_sha"],
            "fork main no longer equals the audited source SHA",
        )
        _require(
            _remote_main_sha(str(audit["upstream_url"])) == audit["upstream_head_sha"],
            "upstream main advanced after the release audit",
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return value


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid TOML {path}: {exc}") from exc
    return value


def _remote_main_sha(url: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "ls-remote", url, "refs/heads/main"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise SystemExit(f"git ls-remote failed for {url}: {result.stdout.strip()}")
    fields = result.stdout.split()
    if len(fields) != 2 or GIT_SHA_RE.fullmatch(fields[0]) is None:
        raise SystemExit(f"unexpected main ref for {url}: {result.stdout.strip()}")
    return fields[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


if __name__ == "__main__":
    main()
