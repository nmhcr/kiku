#!/usr/bin/env bash
# One-shot setup with uv: install the project + tiny-voca engine, download weights.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

# Creates .venv, installs the project and extras, and writes uv.lock.
# The [tool.uv.sources] pin in pyproject.toml keeps torch on the CPU-only index.
uv sync --extra engine --extra dev

# Fetch only the checkpoint and phonemizer data (the full HF repo is ~101 MB).
if [ ! -f "$ROOT/weights/tiny-voca.pt" ] || [ ! -d "$ROOT/weights/phonemizer" ]; then
  uv run python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "n4ze3m/tiny-voca",
    local_dir="weights",
    allow_patterns=["tiny-voca.pt", "phonemizer/*"],
)
PY
fi

echo
echo "Setup complete. The environment is at $ROOT/.venv"
echo "Before synthesizing, point tiny-voca at the phonemizer data:"
echo "  export DIZIONE_DATA=$ROOT/weights/phonemizer"
echo
echo "Then, for example:"
echo "  uv run audiobook-creator init --source examples/sample-book.txt --title 'My Book'"
echo "  uv run audiobook-creator synthesize --config my-book/book.toml"
