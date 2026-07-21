from io import BytesIO

import pytest

from lyra_app.toml import TomlNormalizationError, load_normalized_toml


def test_load_normalized_toml_trims_nested_keys_and_string_values() -> None:
    source = BytesIO(
        b"""
[" workers "." interactive "]
" queues " = [" interactive ", " batch "]
""",
    )

    assert load_normalized_toml(source) == {
        "workers": {
            "interactive": {
                "queues": ["interactive", "batch"],
            },
        },
    }


def test_load_normalized_toml_rejects_keys_that_collide_after_trimming() -> None:
    source = BytesIO(
        b"""
worker = "interactive"
" worker " = "batch"
""",
    )

    with pytest.raises(TomlNormalizationError, match="duplicate key after trimming"):
        load_normalized_toml(source)


def test_load_normalized_toml_rejects_empty_normalized_strings() -> None:
    source = BytesIO(b'queue = "  "\n')

    with pytest.raises(TomlNormalizationError, match="must be a non-empty string"):
        load_normalized_toml(source)
