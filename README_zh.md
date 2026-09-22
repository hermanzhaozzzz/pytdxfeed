# pytdxfeed

[![PyPI](https://img.shields.io/pypi/v/pytdxfeed)](https://pypi.org/project/pytdxfeed/)
[![Python](https://img.shields.io/pypi/pyversions/pytdxfeed)](https://pypi.org/project/pytdxfeed/)
[![CI](https://github.com/hermanzhaozzzz/pytdxfeed/actions/workflows/ci.yml/badge.svg)](https://github.com/hermanzhaozzzz/pytdxfeed/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/hermanzhaozzzz/pytdxfeed)](LICENSE)

[English](README.md) | 简体中文

`pytdxfeed` 是本地 [`tdx-api`](https://github.com/hermanzhaozzzz/tdx-api)
行情服务的 Python 客户端和 macOS 服务管理器。Python 程序通过统一、严格的
HTTP 客户端读取行情；macOS 用户无需安装 Docker 或 Go 工具链，包会自动安装
锁定版本的原生服务。

服务只监听本机：host 固定为 `127.0.0.1`，端口默认 `8080`，调用方可以明确
指定其他端口。

## 安装

支持 Python 3.11-3.13。

```bash
uv add pytdxfeed
```

也可以安装到当前 uv 环境：

```bash
uv pip install pytdxfeed
```

## 快速开始

在 macOS 上，`ensure_service()` 会安装或升级内置服务、注册当前用户的
LaunchAgent、等待严格健康检查通过，并返回运行信息。在其他操作系统上，它只会
检查已有的本地服务，不负责部署。

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

服务管理和客户端必须使用同一个明确端口：

```python
from pytdxfeed import TdxApiClient, ensure_service

service = ensure_service(port=18080)
with TdxApiClient(port=service.port) as client:
    client.health()
```

端口必须是 `1` 到 `65535` 的整数。host 和完整 base URL 不允许配置，运行时也
不会自动寻找空闲端口。

### 接近 tick 的轮询

`batch_quote()` 提供当前行情和五档盘口快照，`trade()` 提供滚动窗口内的最近
逐笔成交。对少量标的同时轮询两个接口，可以得到接近 tick 的使用效果：

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

            # 实际使用时替换为队列、回调或自己的行情处理函数。
            print({"quotes": quote_snapshot, "new_trades": new_trades})
        except TdxApiError as exc:
            print(f"轮询失败：{exc}")

        elapsed = monotonic() - started
        sleep(max(0.0, POLL_INTERVAL_SEC - elapsed))
```

第一次响应只建立基线，不会把最多 1,800 条旧记录当作新成交重放。之后每轮输出
相对于上一个滚动窗口新增的记录。这仍是尽力而为的轮询：如果两轮之间新增记录数
超过接口保留窗口，中间成交仍可能遗漏。标的数量增加时应降低请求负载或延长间隔。

## 能力范围

| 能力 | 公开 API | 范围 |
|---|---|---|
| 严格健康检查 | `health()` | 必须得到锁定服务定义的健康响应 |
| 股票 K 线 | `kline_all()` | TDX 原始行情或 THS 前复权行情 |
| 指数 K 线 | `index_all()` | 指定周期的指数历史数据 |
| 批量行情快照 | `batch_quote()` | 每次明确传入 1-50 个标的 |
| 最近逐笔成交 | `trade()` | 轮询获取最多 1,800 条最近逐笔记录 |
| 历史分钟摘要 | `minute()` | 一个明确的 `YYYYMMDD` 交易日 |
| 历史逐笔成交 | `minute_trade_all()` | 一个明确的 `YYYYMMDD` 交易日内接口返回的全部记录 |
| 接近 tick 的轮询 | `batch_quote()` + `trade()` | 调用方控制轮询频率并增量识别逐笔成交 |
| macOS 服务管理 | `ensure_service()` | 安装、升级、启动、迁移端口并检查 LaunchAgent |
| 其他操作系统 | `ensure_service()` | 只检查已有服务，不自动安装 |
| 推送式 tick 流 | 不支持 | 没有 WebSocket、服务端订阅或交易所事件流 |
| 行情存储 API | 不支持 | 返回上游 JSON，不替用户写入行情数据库 |

TDX 原始日线的 `limit` 在 1～799 时，同时限制源端取数；额外读取一根，以保留返回首日的昨收价。

因此 pytdxfeed 支持接近 tick 的轮询和逐笔级别记录，但不宣称提供交易所原生推送
或无损 tick 采集。

内置服务最多支持 `MAX_CONCURRENT_MARKET_DATA_REQUESTS == 6` 个并发行情请求。
消费者必须用这个公开上限约束自己的 worker pool；健康检查和生命周期检查不计入
行情请求。

### 公开方法

| 方法 | 主要参数 | 上游接口 |
|---|---|---|
| `ensure_service()` | `port=8080`、`timeout_sec=2.0`、`startup_timeout_sec=120.0` | 服务生命周期和 `/api/health` |
| `TdxApiClient()` | `timeout_sec=10.0`、`port=8080` | 为每个线程创建独立 HTTP session |
| `health()` | 无 | `GET /api/health` |
| `kline_all()` | `code`、`source`、`interval`、可选 `limit` 和 `end_date` | `GET /api/kline-all/{tdx|ths}` |
| `index_all()` | `code`、`interval`、可选 `limit` | `GET /api/index/all` |
| `batch_quote()` | 1-50 个代码 | `POST /api/batch-quote` |
| `trade()` | 一个代码 | `GET /api/trade` |
| `minute()` | `code`、`date=YYYYMMDD` | `GET /api/minute` |
| `minute_trade_all()` | `code`、`date=YYYYMMDD` | `GET /api/minute-trade-all` |

代码使用上游要求的小写交易所前缀，例如 `sh600000`、`sz000001`、
`bj920002`。返回值保持上游 JSON 原样；pytdxfeed 不重命名字段、不缩放价格，
也不转换为 pandas 对象。

`end_date` 是显式的历史截止日期（`YYYY-MM-DD`）。对 THS 前复权请求，它会
隔离仍在发布中的更新尾部，避免较新的不稳定数据阻断已经完成的历史日期；
同日 raw/qfq 的严格核验规则不变。

### K 线覆盖范围

| 来源 | 周期 | 价格口径 | 常见覆盖范围和限制 |
|---|---|---|---|
| `tdx` 股票 | `minute1`、`minute5`、`minute15`、`minute30`、`hour` | 原始 | 每个周期约 24,000 根；周期越长，覆盖的自然时间越长 |
| `tdx` 股票 | `day`、`week`、`month`、`quarter`、`year` | 原始 | 通常覆盖上市以来；服务端自动拼接底层每批 800 根的数据 |
| `ths` 股票 | `day`、`week`、`month` | 前复权 | 覆盖 THS 接口当前可提供的上市以来数据 |
| 指数 | 与 TDX 相同的周期名称 | 指数点位 | 取决于具体指数和上游服务器 |

THS 前复权日线以 `all.js` 作为历史来源。上海时间 15:10 以后，如果已完成的
收盘尾部仍缺失，内置服务可以用 `today.js` 补齐，但日期和按千分之一元转换后的
OHLC 必须与同日 TDX raw 日线精确一致；该尾部的成交量和成交额取自 TDX。
响应残缺或不一致会明确失败，不使用 `last.js`。

分钟线按 bar 数量限制，不按固定年限限制。2026-07-20 对 `sh600000` 的实测中，
`minute1` 返回 23,520 根，起点为 2026-02-26；`minute5` 返回 23,952 根，
起点为 2024-06-28。TDX 日线返回 6,349 根，起点为上市日 1999-11-10。
不同标的和上游服务器得到的区间可能不同。

每次调用读取所选接口当时可以提供的最新数据。pytdxfeed 不包含定时任务，也不在
上游成功响应之外承诺固定的数据时效。

## 失败语义

客户端不会静默重试，也不会切换数据源，因此调用方始终知道实际选择的行情来源。

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
    print(f"HTTP 连接失败：{exc}")
except TdxApiResponseError as exc:
    print(f"tdx-api 拒绝了请求：{exc}")
except TdxApiDeploymentError as exc:
    print(f"托管服务无法启动：{exc}")
```

如果目标端口已经存在严格健康的 `tdx-api`，pytdxfeed 会把它作为外部服务复用。
如果未知进程返回不健康或不兼容的响应，启动会直接失败，不会终止该进程。

## 托管运行时

| 设置 | 值 |
|---|---|
| Host | `127.0.0.1` |
| 默认端口 | `8080` |
| 自动托管平台 | macOS arm64、x86_64 |
| 运行目录 | `~/.pytdxfeed` |
| 持久化服务数据 | `~/.pytdxfeed/data/database` |
| LaunchAgent label | `com.hermanzhaozzzz.pytdxfeed.tdx-api` |
| 日志目录 | `~/.pytdxfeed/logs` |

切换配置端口时，pytdxfeed 会重建托管 plist 并重启自己管理的服务，保留行情数据
目录。已经部署的兼容新版本资产不会被旧包降级。

### 内置 tdx-api 版本

| 项目 | v1.0.8 使用值 |
|---|---|
| 源码仓库 | [`hermanzhaozzzz/tdx-api`](https://github.com/hermanzhaozzzz/tdx-api) |
| 源码 commit | [`38b1af37e16b0aa84e88cb88cb0a75f8d05e407e`](https://github.com/hermanzhaozzzz/tdx-api/commit/38b1af37e16b0aa84e88cb88cb0a75f8d05e407e) |
| 上游仓库 | [`oficcejo/tdx-api`](https://github.com/oficcejo/tdx-api) |
| 上游基线 | [`ea07dccb67aeb92ebde851ac31503b4bf457a318`](https://github.com/oficcejo/tdx-api/commit/ea07dccb67aeb92ebde851ac31503b4bf457a318) |
| 源码 commit 日期 | 2026-08-19 |
| 服务 API major | `1` |
| Artifact generation | `7` |
| 内置架构 | `darwin-arm64`、`darwin-x86_64` |
| 审核后的 fork 改动 | 严格的 `TDX_API_PORT`；THS 响应校验；经交叉核验的 qfq 收盘尾部；历史 qfq `end_date` 隔离；代码表就绪门禁 |

源码 fork 跟踪上游，并只保留通用实现。上游项目在 README 中声明使用 MIT
License。pytdxfeed 自有源码和发布元数据使用 [MIT License](LICENSE)。

## 性能

以下本地测试使用 Apple M2 Mac mini（8 核、16 GB）、macOS 26.5.2、
`sh600000`、已预热的 loopback 服务。每项先预热一次，再于 2026-07-20 连续测量
五次。

| 操作 | 返回数据 | p50 延迟 |
|---|---:|---:|
| 健康检查 | 41 字节响应 | `0.5 ms` |
| 批量快照 | 2 个标的 | `44.7 ms` |
| 最近逐笔成交 | 1,800 条 | `50.2 ms` |
| 全量日线 | 6,349 根 / 约 1.0 MiB | `381.5 ms` |
| 全量 1 分钟线 | 23,520 根 / 约 3.5 MiB | `1.406 s` |

这些数字是可复现实测，不是服务等级承诺。实际延迟取决于标的历史长度、响应体积、
上游服务器、网络质量和交易时段。

在自己的本地服务上运行同一组测试：

```bash
uv run python benchmarks/benchmark_client.py \
  --symbol sh600000 \
  --second-symbol sz000001 \
  --port 8080 \
  --iterations 5
```

脚本会输出响应行数、响应大小以及 min、p50、p95、max 的 Markdown 表格。

## 发布渠道

| 渠道 | 用途 |
|---|---|
| [PyPI](https://pypi.org/project/pytdxfeed/) | 主要安装渠道 |
| [GitHub Releases](https://github.com/hermanzhaozzzz/pytdxfeed/releases) | 发布说明，以及与 PyPI 一致的 wheel 和源码包 |
| GitHub Packages | 不使用；GitHub 目前没有 Python/PyPI package registry |

## 开发

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

锁定的 fork commit 变化时需要重建 macOS 资产：

```bash
uv run python scripts/build_assets.py
```

构建脚本会把准确的 fork commit 分别检出到干净的构建和测试 clone，运行两个 Go
module 的测试，无 CGO 构建两种 macOS 架构，并把源码、上游基线和 SHA-256 写入
资产 manifest。发布前还要更新机器可读的上游审计记录，并使用
`--check-remotes` 运行审计检查器。

## 许可证

MIT，详见 [LICENSE](LICENSE)。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=hermanzhaozzzz/pytdxfeed&type=Date)](https://www.star-history.com/#hermanzhaozzzz/pytdxfeed&Date)

`client.recent_minute_bars("sh600000", limit=240)` 直接取最近 1–800 根原生 raw 分钟线，不扫描全历史。响应为 `data.List`，可能包含尚未结束的分钟；消费完成分钟时须检查来源时间。
