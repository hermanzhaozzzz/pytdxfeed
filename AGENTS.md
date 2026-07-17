# AGENTS.md

## Environment

- This repository uses uv exclusively. Use `uv sync`, `uv run`, `uv lock`, and
  `uv build`; do not add Poetry, pip, pipx, or repository-local environment
  managers.
- The package is private and Git-only. Do not publish it or its bundled
  tdx-api binaries to PyPI or a public release while the upstream repository
  has no declared license.
- Before pushing, run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pytest`, and `uv build`.

## Runtime contract

- The service address is fixed at `http://127.0.0.1:8080`.
- The macOS runtime root is fixed at `~/.pytdxfeed` and the LaunchAgent label is
  `com.hermanzhaozzzz.pytdxfeed.tdx-api`.
- Never kill or reuse an unhealthy unknown port owner. A strictly healthy
  unknown tdx-api service may be used without taking ownership.
- Market-data requests have no source fallback and no silent retry.

