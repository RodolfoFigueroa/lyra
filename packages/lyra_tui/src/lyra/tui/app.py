from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

if TYPE_CHECKING:
    from typing import ClassVar

    from lyra.tui.config import TuiConfig


class LyraTuiApp(App[None]):
    """Minimal Lyra operator console shell."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #placeholder {
        height: 1fr;
        content-align: center middle;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: TuiConfig) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self.placeholder_text, id="placeholder")
        yield Footer()

    @property
    def placeholder_text(self) -> str:
        scheme = "https" if self.config.secure else "http"
        auth_state = (
            "admin key configured" if self.config.has_admin_key else "no admin key"
        )
        return f"Lyra TUI\n{scheme}://{self.config.host}\n{auth_state}"
