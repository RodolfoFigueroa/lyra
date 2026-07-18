#!/usr/bin/env bash

set -euo pipefail

sdk_path="packages/lyra_sdk/src/lyra/sdk/db.py"

uv run python build_scripts/generate_sdk.py
uv run ruff check --fix-only "${sdk_path}"
uv run ruff format "${sdk_path}"
