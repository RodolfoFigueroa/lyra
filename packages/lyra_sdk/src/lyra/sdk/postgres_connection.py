"""Optional PostgreSQL connection-profile helpers."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import URL


class PostgresWorkload(StrEnum):
    """Stable PostgreSQL workload identities used by Lyra data engines."""

    API = "lyra-api"
    SPATIAL = "lyra-spatial"
    WORKER = "lyra-worker"
    PROBE = "lyra-probe"
    INTEGRATION = "lyra-integration"
    LOCAL = "lyra-sdk-local"


def apply_read_only_postgres_profile(
    url: URL,
    *,
    workload: PostgresWorkload,
    statement_timeout_ms: float,
) -> URL:
    """Return a URL with Lyra's read-only session policy applied.

    Existing libpq query parameters and options are retained. The fixed workload
    identity replaces any caller-supplied application name.

    Returns:
        A copy of ``url`` containing the connection-profile query parameters.
    """
    existing_options = url.query.get("options", ())
    if isinstance(existing_options, str):
        option_parts = [existing_options] if existing_options else []
    else:
        option_parts = list(existing_options)
    option_parts.extend(
        (
            "-c default_transaction_read_only=on",
            f"-c statement_timeout={statement_timeout_ms}",
        )
    )
    return url.update_query_dict(
        {
            "application_name": workload.value,
            "options": " ".join(option_parts),
        }
    )


__all__ = ["PostgresWorkload", "apply_read_only_postgres_profile"]
