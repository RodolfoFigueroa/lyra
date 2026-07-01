from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, TypeVar

from textual import on
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult
    from textual.widget import Widget
    from textual.widgets._select import NoSelection


@dataclass(frozen=True, slots=True)
class PluginRepoForm:
    source: str
    repo_id: str | None


@dataclass(frozen=True, slots=True)
class RoutingForm:
    metric_name: str
    queue: str


ResultT = TypeVar("ResultT")


class KeyboardModalScreen(ModalScreen[ResultT]):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("tab", "focus_next", "Focus next", show=False, priority=True),
        Binding(
            "shift+tab",
            "focus_previous",
            "Focus previous",
            show=False,
            priority=True,
        ),
        Binding("up", "focus_up", "Focus up", show=False),
        Binding("down", "focus_down", "Focus down", show=False),
        Binding("left", "focus_previous_button", "Focus previous button", show=False),
        Binding("right", "focus_next_button", "Focus next button", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.add_class("dialog-screen")

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_focus_previous(self) -> None:
        self.focus_previous()

    def action_focus_up(self) -> None:
        inputs = list(self.query(Input))
        buttons = list(self.query(Button))
        focused = self.focused
        if focused in buttons and inputs:
            inputs[-1].focus()
            return
        focused_index = self._focused_index(inputs)
        if focused_index is None:
            return
        if focused_index > 0:
            inputs[focused_index - 1].focus()

    def action_focus_down(self) -> None:
        inputs = list(self.query(Input))
        buttons = list(self.query(Button))
        focused_index = self._focused_index(inputs)
        if focused_index is None:
            return
        if focused_index < len(inputs) - 1:
            inputs[focused_index + 1].focus()
        elif buttons:
            buttons[0].focus()

    def action_focus_previous_button(self) -> None:
        self._focus_relative(list(self.query(Button)), -1)

    def action_focus_next_button(self) -> None:
        self._focus_relative(list(self.query(Button)), 1)

    def _focus_relative(self, widgets: Sequence[Widget], direction: int) -> None:
        focused_index = self._focused_index(widgets)
        if focused_index is None:
            return
        widgets[(focused_index + direction) % len(widgets)].focus()

    def _focused_index(self, widgets: Sequence[Widget]) -> int | None:
        focused = self.focused
        for index, widget in enumerate(widgets):
            if widget is focused:
                return index
        return None


class ConfirmDialog(KeyboardModalScreen[bool]):
    BINDINGS: ClassVar[list[Binding]] = [
        *KeyboardModalScreen.BINDINGS,
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        message: str,
        *,
        confirm_label: str = "Confirm",
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.dialog_message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Container(classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title")
            yield Static(self.dialog_message, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, variant="primary", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def action_cancel(self) -> None:
        result = False
        self.dismiss(result)

    def action_confirm(self) -> None:
        result = True
        self.dismiss(result)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#confirm")
    def confirm_button(self) -> None:
        self.action_confirm()


class RestartWorkersDialog(KeyboardModalScreen[float | None]):
    BINDINGS: ClassVar[list[Binding]] = [
        *KeyboardModalScreen.BINDINGS,
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, *, timeout: float = 30.0) -> None:
        super().__init__()
        self.timeout = timeout

    def compose(self) -> ComposeResult:
        with Container(classes="dialog"):
            yield Static("Restart workers", classes="dialog-title")
            yield Static(
                "Restart worker pools after draining active tasks.",
                classes="dialog-body",
            )
            with Vertical(classes="dialog-fields"):
                yield Input(
                    str(self.timeout), placeholder="Timeout seconds", id="timeout"
                )
                yield Static("", id="restart-error", classes="dialog-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Restart", variant="primary", id="submit")

    def on_mount(self) -> None:
        self.query_one("#timeout", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        raw_timeout = self.query_one("#timeout", Input).value.strip()
        try:
            timeout = float(raw_timeout)
        except ValueError:
            self.query_one("#restart-error", Static).update("Timeout must be a number.")
            return
        if timeout < 0:
            self.query_one("#restart-error", Static).update(
                "Timeout must be non-negative."
            )
            return
        self.dismiss(timeout)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit_button(self) -> None:
        self.action_submit()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        self.action_submit()


class PluginRepoDialog(KeyboardModalScreen[PluginRepoForm | None]):
    BINDINGS: ClassVar[list[Binding]] = [
        *KeyboardModalScreen.BINDINGS,
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.error_message = ""

    def compose(self) -> ComposeResult:
        with Container(classes="dialog"):
            yield Static("Add plugin repo", classes="dialog-title")
            with Vertical(classes="dialog-fields"):
                yield Input("", placeholder="Source", id="source")
                yield Input("", placeholder="Optional repo ID", id="repo-id")
                yield Static("", id="repo-error", classes="dialog-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="primary", id="submit")

    def on_mount(self) -> None:
        self.query_one("#source", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        source = self.query_one("#source", Input).value.strip()
        repo_id = self.query_one("#repo-id", Input).value.strip() or None
        if not source:
            self.error_message = "Source is required."
            self.query_one("#repo-error", Static).update(self.error_message)
            return
        self.dismiss(PluginRepoForm(source=source, repo_id=repo_id))

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit_button(self) -> None:
        self.action_submit()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        self.action_submit()


class RoutingDialog(KeyboardModalScreen[RoutingForm | None]):
    BINDINGS: ClassVar[list[Binding]] = [
        *KeyboardModalScreen.BINDINGS,
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        allowed_queues: list[str],
        metric_name: str | None = None,
    ) -> None:
        super().__init__()
        self.allowed_queues = allowed_queues
        self.metric_name = metric_name or ""

    def compose(self) -> ComposeResult:
        queue_options = [(queue, queue) for queue in self.allowed_queues]
        with Container(classes="dialog"):
            yield Static("Assign metric route", classes="dialog-title")
            with Vertical(classes="dialog-fields"):
                yield Input(
                    self.metric_name, placeholder="Metric name", id="metric-name"
                )
                yield Select[str](
                    queue_options,
                    prompt="Queue",
                    allow_blank=False,
                    id="queue",
                )
                yield Static("", id="routing-error", classes="dialog-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Assign", variant="primary", id="submit")

    def on_mount(self) -> None:
        self.query_one("#metric-name", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        metric_name = self.query_one("#metric-name", Input).value.strip()
        queue_value = self.query_one("#queue", Select).value
        if not metric_name:
            self.query_one("#routing-error", Static).update("Metric name is required.")
            return
        if not isinstance(queue_value, str):
            self.query_one("#routing-error", Static).update("Queue is required.")
            return
        self.dismiss(RoutingForm(metric_name=metric_name, queue=queue_value))

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit_button(self) -> None:
        self.action_submit()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        self.action_submit()


def select_value_is_blank(value: str | NoSelection) -> bool:
    return not isinstance(value, str)
