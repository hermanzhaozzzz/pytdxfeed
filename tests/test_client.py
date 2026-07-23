"""Tests for the raw tdx-api HTTP client."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from pytdxfeed import (
    DEFAULT_PORT,
    TdxApiClient,
    TdxApiResponseError,
    TdxApiTransportError,
)


class Response:
    def __init__(
        self, payload: object, *, status_error: Exception | None = None
    ) -> None:
        self.payload = payload
        self.status_error = status_error
        self.text = "not-json"

    def raise_for_status(self) -> None:
        if self.status_error is not None:
            raise self.status_error

    def json(self) -> object:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object, object]] = []
        self.closed = False
        self.trust_env = True

    def request(self, method, url, *, params, json, timeout):
        self.calls.append((method, url, params, json))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_health_requires_explicit_healthy_status() -> None:
    session = Session([Response({"code": 0, "message": "success"})])
    client = TdxApiClient(session_factory=lambda: session)
    with pytest.raises(TdxApiResponseError, match="not healthy"):
        client.health()


def test_client_endpoint_contracts() -> None:
    session = Session(
        [
            Response({"status": "healthy"}),
            Response({"code": 0, "data": []}),
            Response({"code": 0, "data": {"List": []}}),
        ]
    )
    with TdxApiClient(timeout_sec=3, session_factory=lambda: session) as client:
        assert client.health()["status"] == "healthy"
        client.batch_quote(["sh600000"])
        client.trade("sh600000")
    assert session.calls[1][0:2] == (
        "POST",
        "http://127.0.0.1:8080/api/batch-quote",
    )
    assert session.closed


def test_kline_all_passes_historical_end_date() -> None:
    session = Session([Response({"code": 0, "data": {"list": []}})])
    with TdxApiClient(session_factory=lambda: session) as client:
        client.kline_all(
            "sz300749",
            source="ths",
            interval="day",
            end_date="2026-07-22",
        )
    assert session.calls[0][2] == {
        "code": "sz300749",
        "type": "day",
        "end_date": "2026-07-22",
    }


def test_client_uses_configured_local_port() -> None:
    session = Session([Response({"status": "healthy"})])
    client = TdxApiClient(port=18080, session_factory=lambda: session)

    client.health()

    assert client.port == 18080
    assert client.base_url == "http://127.0.0.1:18080"
    assert session.calls[0][1] == "http://127.0.0.1:18080/api/health"


def test_client_disables_environment_proxy_for_loopback(monkeypatch) -> None:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/api/health"
            body = json.dumps({"status": "healthy"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        with TdxApiClient(timeout_sec=1, port=server.server_port) as client:
            assert client.health() == {"status": "healthy"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("port", [True, "18080", 0, 65536])
def test_client_rejects_invalid_port(port: object) -> None:
    with pytest.raises(ValueError, match="port must be an integer"):
        TdxApiClient(port=port)  # type: ignore[arg-type]


def test_client_default_port_is_8080() -> None:
    assert TdxApiClient().port == DEFAULT_PORT == 8080


def test_business_error_is_not_retried() -> None:
    session = Session([Response({"code": -1, "message": "bad code"})])
    client = TdxApiClient(session_factory=lambda: session)
    with pytest.raises(TdxApiResponseError, match="bad code"):
        client.trade("sh000000")
    assert len(session.calls) == 1


def test_transport_error_is_typed() -> None:
    error = requests.ConnectionError("offline")
    session = Session([Response({}, status_error=error)])
    client = TdxApiClient(session_factory=lambda: session)
    with pytest.raises(TdxApiTransportError, match="request failed"):
        client.health()


def test_client_creates_one_session_per_thread() -> None:
    sessions: list[Session] = []

    def factory() -> Session:
        session = Session([Response({"status": "healthy"})])
        sessions.append(session)
        return session

    client = TdxApiClient(session_factory=factory)
    threads = [Thread(target=client.health) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    client.close()
    assert len(sessions) == 3
    assert all(session.closed for session in sessions)


def test_quote_limit_is_validated_before_request() -> None:
    client = TdxApiClient()
    with pytest.raises(ValueError, match="at most 50"):
        client.batch_quote(str(index) for index in range(51))
