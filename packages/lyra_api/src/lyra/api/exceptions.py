"""Custom exceptions for Lyra API client."""


class LyraAPIError(Exception):
    """Base exception for all Lyra API errors."""


class WebSocketError(LyraAPIError):
    """Exception raised for WebSocket-related errors."""


class DownloadError(LyraAPIError):
    """Exception raised for download/HTTP-related errors."""
