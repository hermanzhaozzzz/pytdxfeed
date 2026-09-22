# AGENTS.md

## Environment

- This repository uses uv exclusively. Use `uv sync`, `uv run`, `uv lock`, and
  `uv build`; do not add Poetry, pip, pipx, or repository-local environment
  managers.
- The package is public and released to PyPI and GitHub Releases. Keep the
  package version in `pyproject.toml` and `src/pytdxfeed/_version.py` identical.
- Release tags use `vMAJOR.MINOR.PATCH`. The release workflow must validate,
  build, publish with uv, and attach the exact same distributions to GitHub.
- Keep the bundled fork commit, upstream base commit, API major, artifact
  generation, and supported architectures visible in both user READMEs.
- Before pushing, run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pytest`, `uv build`, and distribution metadata checks.

## Release and legal boundaries

- `pytdxfeed` is public, redistributable infrastructure. Formal fixes to its
  Python API, runtime manager, HTTP client, release metadata, or bundled
  `tdx-api` assets must be released through the public GitHub repository and
  PyPI with the same version and distributions; do not leave a formal fix only
  on a local branch, private commit, or downstream runtime patch.
- `vqt_ths` has a separate legal boundary: it may be hosted and versioned only
  in its private GitHub repository. Never publish `vqt_ths` to PyPI, a public
  GitHub repository, public GitHub Releases, or another public package index.
  VQT must consume an exact commit from that private repository.
- This publication boundary does not relax downstream synchronization. Every
  public `pytdxfeed` release still requires exact-version dependency and lock
  updates in both `vqt_ths` and VQT, plus updated rolling compatibility markers
  in both downstream `AGENTS.md` files. Every private `vqt_ths` release still
  requires VQT's exact Git revision, lock file, and compatibility marker to be
  updated together.

## tdx-api release audit

- Bundled binaries are built from an exact commit of
  `hermanzhaozzzz/tdx-api`; do not restore the downstream patch-application
  build path. `oficcejo/tdx-api` remains the upstream project and the fork's
  `upstream-main` branch is its strict mirror.
- Before every pytdxfeed release, fetch the upstream `main` branch and tags,
  then review every change from the recorded upstream base through the latest
  upstream HEAD. The review must cover Go code, dependencies, HTTP contracts,
  market-data semantics, and newly available capabilities.
- Record the reviewed upstream HEAD, the adopt/defer decision and reason, each
  still-required fork commit, and the compatibility impact for `vqt_ths` and
  VQT in `release/upstream-audits/vMAJOR.MINOR.PATCH.json`.
- A release is blocked when its audit record, package version, fork source SHA,
  upstream HEAD, asset manifest, API major, artifact generation, or asset
  checksums disagree. The release workflow must verify the live upstream and
  fork refs before publication.
- Every released version requires an exact dependency and lock-file update in
  `vqt_ths` and VQT plus a rolling compatibility marker in each downstream
  `AGENTS.md`, even when the audit concludes that no code adaptation is needed.
- After an upstream qfq change is merged, update `upstream-main` and rebuild
  fork `main` so the equivalent local commit is removed or replayed from the
  upstream implementation rather than maintained twice.

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
