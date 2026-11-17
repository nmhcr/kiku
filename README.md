# tts-audiobook

A tool for turning a text book into an audiobook, using
[tiny-voca](https://github.com/n4ze3m/tiny-voca) as the text-to-speech engine.
tiny-voca is a small (4.5M parameter), CPU-friendly, English-only model with
two voices.

> This is a tool: it provides the project structure, chapter pipeline,
> resumable progress and audio assembly. The actual speech comes from
> tiny-voca, which is installed separately (see below).

## Features

- Split a source text into chapters using configurable heading patterns.
- Synthesize chapters one at a time with tiny-voca (`F2` / `M2`, speed and
  pitch controls), writing one WAV per chapter.
- Resumable: progress is tracked in `manifest.json`; finished chapters are
  skipped on the next run.
- Assemble chapters into a single WAV (or MP3 via ffmpeg), with configurable
  gaps between chapters.
- Config-driven (`book.toml`) with a small, documented CLI.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

The one-shot script creates `.venv`, installs the project with the tiny-voca
engine, and downloads the weights:

```bash
bash scripts/setup.sh
```

Or do it manually:

```bash
# Creates .venv and uv.lock, installs project + engine + dev tools.
# pyproject.toml pins torch to the CPU-only index (~184 MB instead of ~4 GB
# of CUDA libraries from the default Linux PyPI wheel).
uv sync --extra engine --extra dev

# Fetch only the checkpoint (18 MB) and phonemizer data (5.4 MB).
uv run python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "n4ze3m/tiny-voca",
    local_dir="weights",
    allow_patterns=["tiny-voca.pt", "phonemizer/*"],
)
PY

# Tell tiny-voca's phonemizer where its data lives.
export DIZIONE_DATA="$PWD/weights/phonemizer"
```

Commit `uv.lock` for reproducible installs. The engine is installed from tiny-voca's
git repository, so `git` must be available. To opt into a CUDA build of torch,
remove the `[[tool.uv.index]]` and `[tool.uv.sources]` sections in `pyproject.toml`.

> tiny-voca is English only and has two fixed voices. Foreign names and unusual
> spellings are often mispronounced. See the
> [tiny-voca README](https://github.com/n4ze3m/tiny-voca) for details.

## Quickstart

```bash
# 1. Create a project from a source text.
uv run audiobook-creator init \
  --source examples/sample-book.txt \
  --title "My First Audiobook" \
  --author "Me" \
  --dir my-book

# 2. Edit my-book/book.toml (voice, speed, chapter pattern, paths, output format).
#    `init` stores an absolute checkpoint path if `weights/tiny-voca.pt` exists
#    when you run it; otherwise adjust `engine.checkpoint` in the config.

# 3. Synthesize chapters (safe to re-run; resumes where it stopped).
export DIZIONE_DATA="$PWD/weights/phonemizer"
uv run audiobook-creator synthesize --config my-book/book.toml

# 4. Check progress, then build the final file.
uv run audiobook-creator status --config my-book/book.toml
uv run audiobook-creator assemble --config my-book/book.toml
```

`uv run` uses the project's `.venv` automatically. The module form also works:
`uv run python -m audiobook_creator --help`.

### CLI commands

| Command | Purpose |
| --- | --- |
| `init` | Create a project directory, config, manifest and directories. |
| `synthesize` | Synthesize pending chapters. `--force`, `--only N [N...]`. |
| `status` | Show per-chapter progress and total duration. |
| `assemble` | Join chapter clips; `--format mp3`, `--output PATH`. |
| `voices` | List available tiny-voca voices. |

## Project layout

```
tts-audiobook/
├── pyproject.toml
├── scripts/setup.sh            # env, install, weights download
├── examples/sample-book.txt    # sample source text
├── src/audiobook_creator/
│   ├── cli.py                  # argparse CLI entry point
│   ├── config.py               # book.toml data model + loader
│   ├── book.py                 # source reading + chapter splitting
│   ├── synthesis.py            # lazy tiny-voca wrapper
│   ├── audio.py                # WAV I/O + final assembly
│   └── manifest.py             # resumable progress (JSON)
└── tests/                      # stdlib unittest tests
```

A generated project looks like:

```
my-book/
├── book.toml
├── manifest.json
├── book.txt          # copied source text
├── audio/            # chapter_0001.wav, chapter_0002.wav, ...
└── out/audiobook.wav
```

## Configuration (`book.toml`)

```toml
[book]
title = "My First Audiobook"
author = "Me"

[source]
path = "book.txt"
chapter_pattern = "^\\s*(?:#{1,3}\\s*)?(?:chapter|part|book)\\s+([^\\n]+?)\\s*$"

[engine]
checkpoint = "weights/tiny-voca.pt"   # relative to project dir
device = "cpu"

[voice]
voice = "F2"        # F2 (female) or M2 (male)
speed = 1.0
pitch_shift = 0.0   # ~0.058 per semitone
pitch_range = 1.0
seed = 0

[audio]
sample_rate = 24000
chapter_gap_ms = 700
output_format = "wav"   # "wav" or "mp3" (mp3 needs ffmpeg)
mp3_bitrate = "128k"

[paths]
audio_dir = "audio"
output = "out/audiobook.wav"
```

Relative paths are resolved against the directory containing `book.toml`.

## Tests

```bash
uv run python -m unittest discover -s tests
```

## How synthesis works

`synthesis.py` calls tiny-voca's `tiny_voca.infer.speak_text()`, which reads
text of any length sentence by sentence, joins the audio and keeps the peak
below clipping. The tool handles chapter-level concerns (splitting, file
naming, resume, gaps, assembly) and leaves voice quality and pronunciation to
tiny-voca.

## License

Apache-2.0, matching tiny-voca. tiny-voca weights are Apache-2.0 and its
dataset is CC BY 4.0 — see the upstream repository for full terms.
