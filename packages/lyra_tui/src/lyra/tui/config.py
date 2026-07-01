from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TuiConfig:
    host: str = "localhost:5219"
    secure: bool = False
    admin_api_key: str | None = field(default=None, repr=False)
    timeout: float = 30.0
    refresh_interval: float = 5.0

    @property
    def has_admin_key(self) -> bool:
        return bool(self.admin_api_key)

    @property
    def display_url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.host}"
