from __future__ import annotations

from typing import TYPE_CHECKING

from lyra.tui.client import LyraApiClientAdapter
from lyra.tui.state import LyraTuiState, TuiSnapshot
from lyra.tui.widgets import ConnectionStatus
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

if TYPE_CHECKING:
    from typing import ClassVar

    from lyra.tui.config import TuiConfig
    from lyra.tui.state import SnapshotPhase


class LyraTuiApp(App[None]):
    """Minimal Lyra operator console shell."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #status {
        height: auto;
        padding: 0 1;
    }

    #placeholder {
        height: 1fr;
        content-align: center middle;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        config: TuiConfig,
        *,
        state: LyraTuiState | None = None,
        poll_on_mount: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.state = state or LyraTuiState(
            LyraApiClientAdapter(config),
            has_admin_key=config.has_admin_key,
        )
        self.poll_on_mount = poll_on_mount
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ConnectionStatus(self.state.snapshot, widget_id="status")
        yield Static(self.placeholder_text, id="placeholder")
        yield Footer()

    def on_mount(self) -> None:
        if not self.poll_on_mount:
            return
        self.request_refresh()
        self._refresh_timer = self.set_interval(
            self.config.refresh_interval,
            self.request_refresh,
            name="refresh",
        )

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
        self.workers.cancel_group(self, "refresh")

    def request_refresh(self) -> None:
        self.show_snapshot(_loading_snapshot(self.state.snapshot))
        self.run_worker(
            self.refresh_once(),
            name="refresh",
            group="refresh",
            exit_on_error=False,
            exclusive=True,
        )

    async def refresh_once(self) -> None:
        snapshot = await self.state.refresh()
        self.show_snapshot(snapshot)

    def show_snapshot(self, snapshot: TuiSnapshot) -> None:
        self.state.snapshot = snapshot
        self.query_one(ConnectionStatus).update_snapshot(snapshot)
        self.query_one("#placeholder", Static).update(self.placeholder_text)

    @property
    def placeholder_text(self) -> str:
        auth_state = (
            "admin key configured" if self.config.has_admin_key else "no admin key"
        )
        return (
            "Lyra TUI\n"
            f"{self.config.display_url}\n"
            f"{auth_state}\n"
            f"status: {self.state.snapshot.phase}"
        )


def _loading_snapshot(snapshot: TuiSnapshot) -> TuiSnapshot:
    phase: SnapshotPhase = "loading"
    return TuiSnapshot(
        phase=phase,
        health=snapshot.health,
        admin_status=snapshot.admin_status,
        config_summary=snapshot.config_summary,
        catalog=snapshot.catalog,
        workers=snapshot.workers,
        queues=snapshot.queues,
        jobs=snapshot.jobs,
        plugin_repos=snapshot.plugin_repos,
        plugin_routing=snapshot.plugin_routing,
        last_updated=snapshot.last_updated,
    )
