"""Shared tdx-api client and macOS runtime."""

from pytdxfeed._version import __version__
from pytdxfeed.client import BASE_URL, TdxApiClient
from pytdxfeed.errors import (
    TdxApiDeploymentError,
    TdxApiError,
    TdxApiResponseError,
    TdxApiTransportError,
)
from pytdxfeed.runtime import ServiceInfo, ensure_service

__all__ = [
    "BASE_URL",
    "ServiceInfo",
    "TdxApiClient",
    "TdxApiDeploymentError",
    "TdxApiError",
    "TdxApiResponseError",
    "TdxApiTransportError",
    "__version__",
    "ensure_service",
]
