# pytdxfeed

[![PyPI](https://img.shields.io/pypi/v/pytdxfeed)](https://pypi.org/project/pytdxfeed/)
[![Python](https://img.shields.io/pypi/pyversions/pytdxfeed)](https://pypi.org/project/pytdxfeed/)
[![CI](https://github.com/hermanzhaozzzz/pytdxfeed/actions/workflows/ci.yml/badge.svg)](https://github.com/hermanzhaozzzz/pytdxfeed/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/hermanzhaozzzz/pytdxfeed)](LICENSE)

English | [简体中文](README_zh.md)

`pytdxfeed` is a Python client and macOS service manager for a local
[`tdx-api`](https://github.com/hermanzhaozzzz/tdx-api) market-data service. It gives
Python applications one strict HTTP client contract and, on macOS, installs a
pinned native service without requiring Docker or a local Go toolchain.

The service is always local: the host is fixed to `127.0.0.1`, the default port
is `8080`, and callers may select another explicit port.

## Installation

Python 3.11-3.13 is supported.

```bash
uv add pytdxfeed
```

Or install it into the current uv environment:

```bash
uv pip install pytdxfeed
```

## Quick Start

On macOS, `ensure_service()` installs or upgrades the bundled service, registers
its per-user LaunchAgent, waits for strict health, and returns its runtime
details. On other operating systems it only verifies an already running local
service.

```python
from pytdxfeed import TdxApiClient, ensure_service

service = ensure_service()
print(service.base_url)  # http://127.0.0.1:8080

with TdxApiClient(timeout_sec=10.0) as client:
    print(client.health())

    raw_daily = client.kline_all(
        "sh600000",
        source="tdx",
        interval="day",
        limit=20,
    )
    qfq_daily = client.kline_all(
        "sh600000",
        source="ths",
        interval="day",
        limit=20,
    )
    index_daily = client.index_all("sh000300", interval="day", limit=20)
    quotes = client.batch_quote(["sh600000", "sz000001"])
    recent_trades = client.trade("sh600000")
```

Use the same explicit port for service management and client requests:

```python
from pytdxfeed import TdxApiClient, ensure_service

service = ensure_service(port=18080)
with TdxApiClient(port=service.port) as client:
    client.health()
```

The port must be an integer from `1` to `65535`. The host and full base URL are
not configurable. The runtime never scans for a free port.

### Near-tick Polling

`batch_quote()` provides the current quote and five-level order-book snapshot,
while `trade()` returns a rolling window of recent transaction records. Polling
both endpoints can provide near-tick behavior for a small symbol set:

```python
from time import monotonic, sleep

from pytdxfeed import TdxApiClient, TdxApiError

CODES = ("sh600000", "sz000001")
POLL_INTERVAL_SEC = 1.0
TRADE_KEY_FIELDS = ("Time", "Price", "Volume", "Status", "Number")


def trade_rows(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    rows = data.get("List")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def trade_key(row: dict) -> tuple:
    return tuple(row.get(field) for field in TRADE_KEY_FIELDS)


previous_windows: dict[str, set[tuple]] = {}

with TdxApiClient(timeout_sec=3.0) as client:
    while True:
        started = monotonic()
        try:
            quote_snapshot = client.batch_quote(CODES)
            new_trades: dict[str, list[dict]] = {}

            for code in CODES:
                rows = trade_rows(client.trade(code))
                current_keys = {trade_key(row) for row in rows}
                previous_keys = previous_windows.get(code)
                new_trades[code] = (
                    []
                    if previous_keys is None
                    else [row for row in rows if trade_key(row) not in previous_keys]
                )
                previous_windows[code] = current_keys

            # Replace this with a queue, callback, or strategy handler.
            print({"quotes": quote_snapshot, "new_trades": new_trades})
        except TdxApiError as exc:
            print(f"poll failed: {exc}")

        elapsed = monotonic() - started
        sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))
```

The first response establishes a baseline instead of replaying up to 1,800 old
records. Later responses emit rows not present in the previous rolling window.
This is best-effort polling: if more records arrive between polls than the
endpoint retains, intermediate trades can still be missed. Reduce request load
or increase the interval as the symbol count grows.

## Capabilities

| Capability | Public API | Scope |
|---|---|---|
| Strict health check | `health()` | Requires the exact healthy response from the pinned service |
| Stock K-lines | `kline_all()` | Raw TDX or THS-adjusted stock data |
| Index K-lines | `index_all()` | Full index history for one interval |
| Quote snapshots | `batch_quote()` | 1-50 explicit symbols per request |
| Recent trades | `trade()` | Polling snapshot of up to 1,800 recent transaction records |
| Historical minute summary | `minute()` | One explicit `YYYYMMDD` trade date |
| Historical daily trades | `minute_trade_all()` | All records returned for one explicit `YYYYMMDD` trade date |
| Near-tick polling | `batch_quote()` + `trade()` | Caller-controlled polling with incremental trade detection |
| macOS lifecycle | `ensure_service()` | Installs, upgrades, starts, migrates ports, and checks the LaunchAgent |
| Other platforms | `ensure_service()` | Health-checks an existing service; it does not install one |
| Push tick stream | Not available | No WebSocket, server-side subscription, or exchange event stream |
| Storage API | Not available | The package returns upstream JSON and does not write user market data |

pytdxfeed therefore supports near-tick polling and transaction-level records,
but does not claim exchange-native push delivery or lossless tick capture.

### Public Methods

| Method | Important arguments | Upstream endpoint |
|---|---|---|
| `ensure_service()` | `port=8080`, `timeout_sec=2.0`, `startup_timeout_sec=120.0` | Service lifecycle and `/api/health` |
| `TdxApiClient()` | `timeout_sec=10.0`, `port=8080` | Creates thread-local HTTP sessions |
| `health()` | None | `GET /api/health` |
| `kline_all()` | `code`, `source`, `interval`, optional `limit` and `end_date` | `GET /api/kline-all/{tdx|ths}` |
| `index_all()` | `code`, `interval`, optional `limit` | `GET /api/index/all` |
| `batch_quote()` | Iterable of 1-50 codes | `POST /api/batch-quote` |
| `trade()` | One code | `GET /api/trade` |
| `minute()` | `code`, `date=YYYYMMDD` | `GET /api/minute` |
| `minute_trade_all()` | `code`, `date=YYYYMMDD` | `GET /api/minute-trade-all` |

Codes use the upstream lower-case exchange prefix, for example `sh600000`,
`sz000001`, or `bj920002`. Responses are the upstream JSON objects; pytdxfeed
does not rename fields, rescale prices, or convert results to pandas objects.

`end_date` is an explicit historical as-of boundary (`YYYY-MM-DD`). For THS
qfq requests it prevents a newer, still-publishing tail from invalidating an
older completed date; it does not relax the same-date raw/qfq validation.

### K-line Coverage

| Source | Intervals | Adjustment | Typical coverage and limits |
|---|---|---|---|
| `tdx` stock | `minute1`, `minute5`, `minute15`, `minute30`, `hour` | Raw | About 24,000 bars per interval; the calendar span grows with the interval |
| `tdx` stock | `day`, `week`, `month`, `quarter`, `year` | Raw | Usually listing-to-present; the service joins underlying 800-bar batches |
| `ths` stock | `day`, `week`, `month` | Forward-adjusted | Listing-to-latest data available from the THS endpoint |
| Index | Same interval names as TDX | Index points | Coverage depends on the selected index and upstream servers |

For THS forward-adjusted daily data, the bundled service treats `all.js` as the
history source. From 15:10 Asia/Shanghai onward it can complete a missing closed
tail from `today.js`, but only after the date and integer-milliyuan OHLC match
the same TDX raw daily bar exactly. TDX supplies that tail's volume and amount.
Incomplete or mismatched responses fail explicitly; `last.js` is not used.

The minute history is bar-count limited, not year limited. In a check on
`sh600000` dated 2026-07-20, `minute1` returned 23,520 bars from 2026-02-26 and
`minute5` returned 23,952 bars from 2024-06-28. Daily TDX data returned 6,349
bars from the 1999-11-10 listing date. Different symbols and upstream servers
can produce different ranges.

Calls fetch the latest data available from the selected endpoint. pytdxfeed has
no scheduler and makes no freshness guarantee beyond the successful upstream
response.

## Failure Semantics

The client does not retry requests or switch sources. This keeps the selected
market-data source observable.

```python
from pytdxfeed import (
    TdxApiClient,
    TdxApiDeploymentError,
    TdxApiResponseError,
    TdxApiTransportError,
)

try:
    with TdxApiClient(timeout_sec=10.0) as client:
        payload = client.kline_all(
            "sh600000",
            source="tdx",
            interval="minute1",
        )
except TdxApiTransportError as exc:
    print(f"HTTP connection failed: {exc}")
except TdxApiResponseError as exc:
    print(f"tdx-api rejected the request: {exc}")
except TdxApiDeploymentError as exc:
    print(f"the managed service could not start: {exc}")
```

If the selected port is occupied by another strictly healthy `tdx-api`, it is
reused as an external service. If an unknown process returns an unhealthy or
incompatible response, startup fails without terminating that process.

## Managed Runtime

| Setting | Value |
|---|---|
| Host | `127.0.0.1` |
| Default port | `8080` |
| Managed platforms | macOS arm64 and x86_64 |
| Runtime root | `~/.pytdxfeed` |
| Persistent service data | `~/.pytdxfeed/data/database` |
| LaunchAgent label | `com.hermanzhaozzzz.pytdxfeed.tdx-api` |
| Logs | `~/.pytdxfeed/logs` |

Changing the configured port rebuilds the managed plist and restarts the owned
service while preserving its data directory. Newer compatible artifact
generations are never downgraded.

### Bundled tdx-api

| Item | v1.0.4 value |
|---|---|
| Source repository | [`hermanzhaozzzz/tdx-api`](https://github.com/hermanzhaozzzz/tdx-api) |
| Source commit | [`e6778c6b8ec4cbf0705dc1fe4c3c413a6b6518ff`](https://github.com/hermanzhaozzzz/tdx-api/commit/e6778c6b8ec4cbf0705dc1fe4c3c413a6b6518ff) |
| Upstream repository | [`oficcejo/tdx-api`](https://github.com/oficcejo/tdx-api) |
| Upstream base | [`ea07dccb67aeb92ebde851ac31503b4bf457a318`](https://github.com/oficcejo/tdx-api/commit/ea07dccb67aeb92ebde851ac31503b4bf457a318) |
| Source commit date | 2026-07-23 |
| Service API major | `1` |
| Artifact generation | `5` |
| Bundled architectures | `darwin-arm64`, `darwin-x86_64` |
| Reviewed fork changes | Strict `TDX_API_PORT`; THS response validation; verified qfq close tail; historical qfq `end_date` isolation |

The source fork tracks the upstream project and preserves its general-purpose
code only. The upstream project declares the MIT License in its README.
pytdxfeed's own source and release metadata are provided under the
[MIT License](LICENSE).

## Performance

The following local measurements used an Apple M2 Mac mini (8 cores, 16 GB),
macOS 26.5.2, `sh600000`, a warm service on loopback, one warm-up request, and
five measured requests on 2026-07-20.

| Operation | Returned data | p50 latency |
|---|---:|---:|
| Health | 41-byte response | `0.5 ms` |
| Batch quote | 2 symbols | `44.7 ms` |
| Recent trades | 1,800 records | `50.2 ms` |
| Full daily history | 6,349 bars / about 1.0 MiB | `381.5 ms` |
| Full 1-minute history | 23,520 bars / about 3.5 MiB | `1.406 s` |

These measurements are examples, not a service-level guarantee. Latency varies
with symbol history, response size, upstream server selection, network quality,
and market hours.

Run the reproducible benchmark against your local service:

```bash
uv run python benchmarks/benchmark_client.py \
  --symbol sh600000 \
  --second-symbol sz000001 \
  --port 8080 \
  --iterations 5
```

The script reports rows, response size, minimum, p50, p95, and maximum latency
as a Markdown table.

## Distribution

| Channel | Purpose |
|---|---|
| [PyPI](https://pypi.org/project/pytdxfeed/) | Primary installation channel |
| [GitHub Releases](https://github.com/hermanzhaozzzz/pytdxfeed/releases) | Release notes plus matching wheel and source archive |
| GitHub Packages | Not used because GitHub does not provide a Python/PyPI package registry |

## Development

```bash
git clone https://github.com/hermanzhaozzzz/pytdxfeed.git
cd pytdxfeed
uv sync --locked --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/check_release_audit.py
uv build
uvx --from twine twine check dist/*
```

Regenerate the deterministic macOS assets when the locked fork commit changes:

```bash
uv run python scripts/build_assets.py
```

The build checks out the exact fork commit into clean build and test clones,
runs both Go modules' tests, builds both macOS architectures without CGO, and
records source/upstream provenance plus SHA-256 hashes in the asset manifest.
Before a release, update its machine-readable upstream audit and run the audit
checker with `--check-remotes`.

## License

MIT. See [LICENSE](LICENSE).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=hermanzhaozzzz/pytdxfeed&type=Date)](https://www.star-history.com/#hermanzhaozzzz/pytdxfeed&Date)
