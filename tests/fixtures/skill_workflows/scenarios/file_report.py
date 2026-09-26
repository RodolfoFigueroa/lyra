from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def write_report(feature_ids: list[str], output_path: Path) -> Path:
    """Write IDs in supplied order to a UTF-8 text/plain .txt file, ending in LF.

    This report has no geometry, CRS, scale, or external data requirements.
    """
    output_path.write_text("\n".join(feature_ids) + "\n", encoding="utf-8")
    return output_path
