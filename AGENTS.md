# AGENTS.md

## Environment

- This repository uses uv exclusively. Use `uv sync`, `uv run`, `uv lock`, and
  `uv build`; do not add Poetry, pip, pipx, or repository-local environment
  managers.
- The package is public and released to PyPI and GitHub Releases. Keep the
  package version in `pyproject.toml` and `src/pytdxfeed/_version.py` identical.
- Release tags use `vMAJOR.MINOR.PATCH`. The release workflow must validate,
  build, publish with uv, and attach the exact same distributions to GitHub.
- Keep the bundled tdx-api commit, API major, artifact generation, patches,
  and supported architectures visible in both user READMEs.
- Before pushing, run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pytest`, `uv build`, and distribution metadata checks.

## Runtime contract

- The service host is fixed at `127.0.0.1`; the explicit service port defaults
  to `8080` and may be configured by consumers. Do not add configurable hosts,
  base URLs, automatic free-port selection, or alternate service fallback.
- The macOS runtime root is fixed at `~/.pytdxfeed` and the LaunchAgent label is
  `com.hermanzhaozzzz.pytdxfeed.tdx-api`.
- Never kill or reuse an unhealthy unknown configured-port owner. A strictly
  healthy unknown tdx-api service on the configured port may be used without
  taking ownership.
- Market-data requests have no source fallback and no silent retry.

## Documentation contract

- `README.md` is the default English package description and `README_zh.md`
  is the Chinese translation. Keep their commands, capability tables, and
  version facts aligned.
- Documentation is written for package users. Do not add product-specific
  sections for downstream consumers.
- Performance results must state hardware, date, sample count, symbol, and
  that the measurements are not a service-level guarantee.
