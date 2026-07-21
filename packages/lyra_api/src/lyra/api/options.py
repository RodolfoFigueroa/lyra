from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubmitOptions:
    """Options that affect submission without becoming metric arguments."""

    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Options that affect submission and waiting for a metric run."""

    idempotency_key: str | None = None
    timeout: float | None = None


__all__ = ["RunOptions", "SubmitOptions"]
