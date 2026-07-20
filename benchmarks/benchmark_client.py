#!/usr/bin/env python3
"""Measure end-to-end latency of the public client against a local service."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pytdxfeed import TdxApiClient


@dataclass(frozen=True)
class BenchmarkCase:
    """One public client operation included in the benchmark."""

    name: str
    call: Callable[[], object]


@dataclass(frozen=True)
class BenchmarkResult:
    """Latency and response-size measurements for one operation."""

    name: str
    rows: int | None
    payload_bytes: int
    minimum_ms: float
    median_ms: float
    p95_ms: float
    maximum_ms: float


def parse_args() -> argparse.Namespace:
    """Parse benchmark parameters."""
    parser = argparse.ArgumentParser(
        description="Benchmark pytdxfeed against an already healthy local tdx-api."
    )
    parser.add_argument("--symbol", default="sh600000")
    parser.add_argument("--second-symbol", default="sz000001")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("--iterations must be at least 2")
    return args


def response_rows(payload: object) -> int | None:
    """Return the primary result count from an upstream response."""
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return len(data)
    if not isinstance(data, Mapping):
        return None
    count = data.get("count", data.get("Count"))
    if isinstance(count, int):
        return count
    for key in ("list", "List"):
        items = data.get(key)
        if isinstance(items, Sequence) and not isinstance(
            items, (str, bytes, bytearray)
        ):
            return len(items)
    return None


def percentile(values: Sequence[float], fraction: float) -> float:
    """Calculate one linearly interpolated percentile."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def run_case(case: BenchmarkCase, iterations: int) -> BenchmarkResult:
    """Warm up once, then measure a benchmark case."""
    case.call()
    samples: list[float] = []
    payload: object = None
    for _ in range(iterations):
        started = time.perf_counter()
        payload = case.call()
        samples.append((time.perf_counter() - started) * 1000)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return BenchmarkResult(
        name=case.name,
        rows=response_rows(payload),
        payload_bytes=len(encoded),
        minimum_ms=min(samples),
        median_ms=statistics.median(samples),
        p95_ms=percentile(samples, 0.95),
        maximum_ms=max(samples),
    )


def format_size(size: int) -> str:
    """Format response size using binary units."""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MiB"
    if size >= 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size} B"


def print_results(results: Sequence[BenchmarkResult]) -> None:
    """Print a Markdown table suitable for release documentation."""
    print("| Operation | Rows | Payload | Min | p50 | p95 | Max |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for result in results:
        rows = "-" if result.rows is None else f"{result.rows:,}"
        print(
            f"| {result.name} | {rows} | {format_size(result.payload_bytes)} "
            f"| {result.minimum_ms:.1f} ms | {result.median_ms:.1f} ms "
            f"| {result.p95_ms:.1f} ms | {result.maximum_ms:.1f} ms |"
        )


def main() -> None:
    """Run the standard local benchmark suite."""
    args = parse_args()
    with TdxApiClient(timeout_sec=args.timeout, port=args.port) as client:
        client.health()
        cases = (
            BenchmarkCase("health", client.health),
            BenchmarkCase(
                "batch quote (2 symbols)",
                lambda: client.batch_quote((args.symbol, args.second_symbol)),
            ),
            BenchmarkCase("recent trades", lambda: client.trade(args.symbol)),
            BenchmarkCase(
                "all daily bars",
                lambda: client.kline_all(
                    args.symbol,
                    source="tdx",
                    interval="day",
                ),
            ),
            BenchmarkCase(
                "all 1-minute bars",
                lambda: client.kline_all(
                    args.symbol,
                    source="tdx",
                    interval="minute1",
                ),
            ),
        )
        results = [run_case(case, args.iterations) for case in cases]
    print_results(results)


if __name__ == "__main__":
    main()
