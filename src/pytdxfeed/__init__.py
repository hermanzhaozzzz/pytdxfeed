"""Shared tdx-api client and macOS runtime."""

from pytdxfeed._version import __version__
from pytdxfeed.client import (
    BASE_URL,
    DEFAULT_PORT,
    HOST,
    MAX_CONCURRENT_MARKET_DATA_REQUESTS,
    TdxApiClient,
    local_base_url,
    validate_port,
)
from pytdxfeed.errors import (
    TdxApiDeploymentError,
    TdxApiError,
    TdxApiResponseError,
    TdxApiTransportError,
)
from pytdxfeed.runtime import ServiceInfo, ensure_service

__all__ = [
    "BASE_URL",
    "DEFAULT_PORT",
    "HOST",
    "MAX_CONCURRENT_MARKET_DATA_REQUESTS",
    "ServiceInfo",
    "TdxApiClient",
    "TdxApiDeploymentError",
    "TdxApiError",
    "TdxApiResponseError",
    "TdxApiTransportError",
    "__version__",
    "ensure_service",
    "local_base_url",
    "validate_port",
]
