# pytdxfeed

`pytdxfeed` is a private Python wrapper around the local
[`oficcejo/tdx-api`](https://github.com/oficcejo/tdx-api) service used by VQT
and `vnpy_ths`. It provides one macOS LaunchAgent and one HTTP client contract,
so both consumers share the same process instead of deploying competing copies.

The package is installed only from the private Git repository. It is not the
unrelated package formerly published under similar names on PyPI.

## Runtime

- Host: `127.0.0.1`
- Port: `8080` by default; consumers may select another explicit port
- Runtime root: `~/.pytdxfeed`
- LaunchAgent: `com.hermanzhaozzzz.pytdxfeed.tdx-api`
- Pinned upstream commit: `ea07dccb67aeb92ebde851ac31503b4bf457a318`

On macOS, `ensure_service()` reuses an already healthy service or installs the
bundled binary for the current architecture. The persistent upstream database
is kept in `~/.pytdxfeed/data/database`. On other systems the function only
checks an existing local service.

```python
from pytdxfeed import TdxApiClient, ensure_service

service = ensure_service(port=18080)
with TdxApiClient(timeout_sec=3.0, port=18080) as client:
    health = client.health()
    quotes = client.batch_quote(["sh600000", "sz159915"])
```

The host and LaunchAgent identity stay fixed. Port selection is explicit: the
runtime never scans for a free port, and an unhealthy unknown process occupying
the selected port causes startup to fail without terminating that process. The
managed macOS service receives the selected port through `TDX_API_PORT`.

## Development

```bash
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Regenerate the private macOS assets when the locked upstream commit or reviewed
private patch set changes:

```bash
uv run python scripts/build_assets.py --source /path/to/tdx-api
```

The build verifies the exact clean upstream checkout, applies the patches in
`patches/`, runs their Go tests, and records every patch checksum in the asset
manifest. Artifact generation 3 retains explicit THS JSONP validation and adds
strict `TDX_API_PORT` handling to the Go service. It does not retry or switch
market-data sources.

The upstream repository currently has no declared license. This repository and
its derived binaries must remain private/internal until redistribution terms are
clear.
