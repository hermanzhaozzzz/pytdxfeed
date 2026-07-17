"""Public exceptions raised by pytdxfeed."""


class TdxApiError(RuntimeError):
    """Base error for tdx-api client and runtime failures."""


class TdxApiTransportError(TdxApiError):
    """Raised when an HTTP request cannot be completed or decoded."""


class TdxApiResponseError(TdxApiError):
    """Raised when tdx-api returns an invalid or unsuccessful response."""


class TdxApiDeploymentError(TdxApiError):
    """Raised when the shared macOS service cannot be deployed."""
