#!/usr/bin/env bash
# One-shot setup with uv: install the lightweight ONNX backend and download models.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

# Creates .venv and installs the ONNX backend + dev tools (no torch/CUDA).
# Use --extra engine instead if you want the legacy torch backend.
uv sync --extra onnx --extra dev

# Fetch only what the ONNX backend needs (the full HF repo is ~101 MB).
if [ ! -f "$ROOT/weights/onnx/model.onnx" ] || [ ! -d "$ROOT/weights/phonemizer" ]; then
  uv run python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "n4ze3m/tiny-voca",
    local_dir="weights",
    allow_patterns=["config.json", "onnx/model.onnx", "phonemizer/*"],
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
