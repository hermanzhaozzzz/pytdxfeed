"""Thread-safe HTTP client for the local tdx-api service."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from threading import Lock, local
from typing import Any

import requests

from pytdxfeed.errors import TdxApiResponseError, TdxApiTransportError

HOST = "127.0.0.1"
DEFAULT_PORT = 8080
MAX_CONCURRENT_MARKET_DATA_REQUESTS = 6


def validate_port(port: object) -> int:
    """Require an explicit TCP port integer."""
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError("port must be an integer between 1 and 65535")
    if not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return port


def local_base_url(port: int = DEFAULT_PORT) -> str:
    """Build the fixed-host service URL for one validated port."""
    return f"http://{HOST}:{validate_port(port)}"


BASE_URL = local_base_url()


class TdxApiClient:
    """Access the raw tdx-api JSON contract with per-thread HTTP sessions."""

    def __init__(
        self,
        timeout_sec: float = 10.0,
        *,
        port: int = DEFAULT_PORT,
        session_factory: Callable[[], requests.Session] = requests.Session,
    ) -> None:
        timeout = float(timeout_sec)
        if timeout <= 0:
            raise ValueError("timeout_sec must be positive")
        self.timeout_sec = timeout
        self.port = validate_port(port)
        self.base_url = local_base_url(self.port)
        self._session_factory = session_factory
        self._local = local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = Lock()
        self._closed = False

    def __enter__(self) -> TdxApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close every session created by this client."""
        with self._sessions_lock:
            sessions = tuple(self._sessions)
            self._sessions.clear()
            self._closed = True
        for session in sessions:
            session.close()

    def health(self) -> dict[str, Any]:
        """Require the exact health response implemented by the pinned service."""
        payload = self._request_json("GET", "/api/health")
        if not isinstance(payload, dict):
            raise TdxApiResponseError("[tdx-api] health response must be an object")
        if str(payload.get("status") or "").lower() != "healthy":
            raise TdxApiResponseError(
                f"[tdx-api] health status is not healthy: {payload}"
            )
        return payload

    def kline_all(
        self,
        code: str,
        *,
        source: str,
        interval: str,
        limit: int | None = None,
        end_date: str | None = None,
    ) -> object:
        """Return stock K-lines, optionally bounded by an as-of date."""
        if source not in {"tdx", "ths"}:
            raise ValueError("source must be 'tdx' or 'ths'")
        params: dict[str, object] = {"code": _required(code, "code"), "type": interval}
        if limit is not None and limit > 0:
            params["limit"] = int(limit)
        if end_date:
            params["end_date"] = str(end_date)
        return self._request_json("GET", f"/api/kline-all/{source}", params=params)

    def index_all(
        self, code: str, *, interval: str, limit: int | None = None
    ) -> object:
        """Return all index K-lines."""
        params: dict[str, object] = {"code": _required(code, "code"), "type": interval}
        if limit is not None and limit > 0:
            params["limit"] = int(limit)
        return self._request_json("GET", "/api/index/all", params=params)

    def batch_quote(self, codes: Iterable[str]) -> object:
        """Return a raw quote response for at most 50 explicit API codes."""
        items = [_required(code, "code") for code in codes]
        if not items:
            raise ValueError("codes must not be empty")
        if len(items) > 50:
            raise ValueError("tdx-api accepts at most 50 quote codes per request")
        return self._request_json("POST", "/api/batch-quote", json={"codes": items})

    def trade(self, code: str) -> object:
        """Return recent trades for one API code."""
        return self._request_json(
            "GET", "/api/trade", params={"code": _required(code, "code")}
        )

    def minute(self, code: str, *, date: str) -> object:
        """Return the upstream historical minute summary for one trade date."""
        return self._request_json(
            "GET",
            "/api/minute",
            params={"code": _required(code, "code"), "date": _trade_date(date)},
        )

    def minute_trade_all(self, code: str, *, date: str) -> object:
        """Return all upstream historical trades for one trade date."""
        return self._request_json(
            "GET",
            "/api/minute-trade-all",
            params={"code": _required(code, "code"), "date": _trade_date(date)},
        )

    def _session(self) -> requests.Session:
        if self._closed:
            raise RuntimeError("TdxApiClient is closed")
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._session_factory()
            session.trust_env = False
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json: object | None = None,
    ) -> object:
        url = self.base_url + path
        try:
            response = self._session().request(
                method,
                url,
                params=params,
                json=json,
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TdxApiTransportError(
                f"[tdx-api] request failed {url}: {exc}"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            preview = str(getattr(response, "text", ""))[:200]
            raise TdxApiTransportError(
                f"[tdx-api] response is not JSON {url}: {preview}"
            ) from exc
        if isinstance(payload, dict) and payload.get("code") not in (None, 0):
            message = payload.get("message") or payload.get("error") or payload
            raise TdxApiResponseError(f"[tdx-api] {path} returned an error: {message}")
        return payload


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    return text


def _trade_date(value: object) -> str:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isascii() or not text.isdigit():
        raise ValueError("date must use YYYYMMDD")
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("date must use YYYYMMDD") from exc
    return text
