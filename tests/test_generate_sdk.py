import runpy
from pathlib import Path
from types import FunctionType

from build_scripts.generate_sdk import generate_sdk_interface


def test_annotation_imports_are_not_required_at_runtime(tmp_path: Path) -> None:
    source = tmp_path / "client.py"
    destination = tmp_path / "db.py"
    source.write_text(
        """\
from collections.abc import Sequence
from enum import auto
from typing import Literal

import unavailable_sdk_dependency as dependency


class LyraDBImplicit:
    def load(
        self,
        columns: Sequence[str],
        *,
        kind: Literal[\"mesh\"] = \"mesh\",
        marker=auto(),
    ) -> dependency.Frame:
        return dependency.Frame(columns, kind, marker)
""",
        encoding="utf8",
    )

    generate_sdk_interface(str(source), str(destination))

    generated_source = destination.read_text(encoding="utf8")
    namespace = runpy.run_path(str(destination))

    lyra_db = namespace["LyraDB"]
    assert isinstance(lyra_db, type)
    load = vars(lyra_db)["load"]
    assert isinstance(load, FunctionType)
    assert load.__annotations__["return"] == "dependency.Frame"
    assert "if TYPE_CHECKING:" in generated_source
    assert "import unavailable_sdk_dependency as dependency" in generated_source
    assert "from collections.abc import Sequence\n" in generated_source
    assert "from typing import Literal\n" in generated_source
