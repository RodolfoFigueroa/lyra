"""Keyboard-accessible modal forms for Lyra administrative actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, TypeVar

from textual import on
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static
from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult
    from textual.widget import Widget
    from textual.widgets._select import NoSelection


@dataclass(frozen=True, slots=True)
class PluginRepoForm:
    """Represent validated input for adding a plugin repository."""

    source: str
    repo_id: str | None


@dataclass(frozen=True, slots=True)
class RoutingForm:
    """Represent validated input for assigning a metric to a queue."""

    metric_name: str
    queue: str


ResultT = TypeVar("ResultT")


class KeyboardModalScreen(ModalScreen[ResultT]):
    """Provide consistent keyboard focus navigation for modal forms."""

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
        """Initialize a modal screen with the shared dialog styling class."""
        super().__init__()
        self.add_class("dialog-screen")

    def action_focus_next(self) -> None:
        """Move focus to the next focusable widget."""
        self.focus_next()

    def action_focus_previous(self) -> None:
        """Move focus to the previous focusable widget."""
        self.focus_previous()

    def action_focus_up(self) -> None:
        """Move focus upward through fields or from buttons to the last field."""
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
        """Move focus downward through fields and then to the first button."""
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
        """Move focus cyclically to the previous dialog button."""
        self._focus_relative(list(self.query(Button)), -1)

    def action_focus_next_button(self) -> None:
        """Move focus cyclically to the next dialog button."""
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
    """Ask the operator to confirm or cancel an action."""

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
        """Initialize a confirmation dialog with configurable copy."""
        super().__init__()
        self.dialog_title = title
        self.dialog_message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        """Compose the confirmation message and action buttons.

        Yields:
            Widgets forming the confirmation dialog.
        """
        with Container(classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title")
            yield Static(self.dialog_message, classes="dialog-body")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, variant="primary", id="confirm")

    def on_mount(self) -> None:
        """Focus the safe cancellation action when the dialog opens."""
        self.query_one("#cancel", Button).focus()

    def action_cancel(self) -> None:
        """Dismiss the dialog with a negative result."""
        result = False
        self.dismiss(result)

    def action_confirm(self) -> None:
        """Dismiss the dialog with a positive result."""
        result = True
        self.dismiss(result)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        """Handle activation of the cancellation button."""
        self.action_cancel()

    @on(Button.Pressed, "#confirm")
    def confirm_button(self) -> None:
        """Handle activation of the confirmation button."""
        self.action_confirm()


class RestartWorkersDialog(KeyboardModalScreen[float | None]):
    """Collect a drain timeout before requesting a worker restart."""

    BINDINGS: ClassVar[list[Binding]] = [
        *KeyboardModalScreen.BINDINGS,
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, *, timeout: float = 30.0) -> None:
        """Initialize the form with a default drain timeout in seconds."""
        super().__init__()
        self.timeout = timeout

    def compose(self) -> ComposeResult:
        """Compose the timeout field, validation message, and actions.

        Yields:
            Widgets forming the worker restart dialog.
        """
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
        """Focus the timeout field when the dialog opens."""
        self.query_one("#timeout", Input).focus()

    def action_cancel(self) -> None:
        """Dismiss the dialog without requesting a restart."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Validate the timeout and dismiss with its numeric value."""
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
        """Handle activation of the cancellation button."""
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit_button(self) -> None:
        """Handle activation of the restart button."""
        self.action_submit()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        """Submit the form when an input emits its submitted event."""
        self.action_submit()


class PluginRepoDialog(KeyboardModalScreen[PluginRepoForm | None]):
    """Collect and validate a source for a new plugin repository."""

    BINDINGS: ClassVar[list[Binding]] = [
        *KeyboardModalScreen.BINDINGS,
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self) -> None:
        """Initialize an empty repository form and error message."""
        super().__init__()
        self.error_message = ""

    @override
    def compose(self) -> ComposeResult:
        """Compose repository source fields, validation text, and actions.

        Yields:
            Widgets forming the plugin repository dialog.
        """
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
        """Focus the repository source field when the dialog opens."""
        self.query_one("#source", Input).focus()

    def action_cancel(self) -> None:
        """Dismiss the dialog without adding a repository."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Validate the repository source and dismiss with the form data."""
        source = self.query_one("#source", Input).value.strip()
        repo_id = self.query_one("#repo-id", Input).value.strip() or None
        if not source:
            self.error_message = "Source is required."
            self.query_one("#repo-error", Static).update(self.error_message)
            return
        self.dismiss(PluginRepoForm(source=source, repo_id=repo_id))

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        """Handle activation of the cancellation button."""
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit_button(self) -> None:
        """Handle activation of the add button."""
        self.action_submit()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        """Submit the repository form from an input event."""
        self.action_submit()


class RoutingDialog(KeyboardModalScreen[RoutingForm | None]):
    """Collect a queue assignment for a metric route."""

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
        """Initialize the form with allowed queues and an optional metric name."""
        super().__init__()
        self.allowed_queues = allowed_queues
        self.metric_name = metric_name or ""

    def compose(self) -> ComposeResult:
        """Compose metric and queue fields, validation text, and actions.

        Yields:
            Widgets forming the routing assignment dialog.
        """
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
        """Focus the metric-name field when the dialog opens."""
        self.query_one("#metric-name", Input).focus()

    def action_cancel(self) -> None:
        """Dismiss the dialog without assigning a route."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Validate the route fields and dismiss with the assignment."""
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
        """Handle activation of the cancellation button."""
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit_button(self) -> None:
        """Handle activation of the assignment button."""
        self.action_submit()

    @on(Input.Submitted)
    def submit_input(self) -> None:
        """Submit the routing form from an input event."""
        self.action_submit()


def select_value_is_blank(value: str | NoSelection) -> bool:
    """Return whether a Textual select value represents no selection."""
    return not isinstance(value, str)
