"""Command-line and environment configuration for the Lyra TUI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TuiConfig:
    """Connection and refresh settings for the terminal interface."""

    host: str = "localhost:5219"
    secure: bool = False
    admin_api_key: str | None = field(default=None, repr=False)
    timeout: float = 30.0
    refresh_interval: float = 5.0

    @property
    def has_admin_key(self) -> bool:
        """Whether administrator credentials are configured."""
        return bool(self.admin_api_key)

    @property
    def display_url(self) -> str:
        """The configured API origin suitable for display."""
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.host}"
