"""Errors raised while defining, preparing, and validating plugin computations."""


class PluginDefinitionError(ValueError):
    """An authoring definition or schema uses an unsupported capability."""


class _MetricError(ValueError):
    def __init__(self, metric: str, path: str, message: str) -> None:
        self.metric = metric
        self.path = path
        self.message = message
        super().__init__(f"Metric {metric!r}, {path}: {message}")


class MetricInputError(_MetricError):
    """Submitted parameters fail structural or Python-only validation."""


class MetricResultError(_MetricError):
    """A native computation result violates the declared output contract."""
