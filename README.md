# tts-audiobook

A tool for turning a text book into an audiobook, using
[tiny-voca](https://github.com/n4ze3m/tiny-voca) as the text-to-speech engine.
tiny-voca is a small (4.5M parameter), CPU-friendly, English-only model with
two voices.

> This is a tool: it provides the project structure, chapter pipeline,
> resumable progress and audio assembly. The actual speech comes from
> tiny-voca, which is installed separately (see below).

## Features

- Import plain text, Markdown or EPUB sources (EPUB metadata and chapter
  boundaries are detected automatically).
- Split a source text into chapters using configurable heading patterns.
- Synthesize chapters one at a time with tiny-voca (`F2` / `M2`, speed and
  pitch controls), writing one WAV per chapter.
- Lightweight **ONNX Runtime backend by default** (no torch/CUDA), with an
  optional torch backend for compatibility.
- Resumable: progress is tracked in `manifest.json`; finished chapters are
  skipped on the next run.
- Assemble chapters into one audiobook or into groups of N chapters per file,
  with configurable gaps between chapters and WAV/MP3 output.
- Config-driven (`book.toml`) with a small, documented CLI that can also
  read and write those settings.

## Install

Requires Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/).

The one-shot script creates `.venv`, installs the lightweight ONNX backend,
and downloads the model:

```bash
bash scripts/setup.sh
```

Or do it manually:

```bash
# Creates .venv, installs ONNX Runtime + dizione (no torch) and dev tools.
uv sync --extra onnx --extra dev

# Fetch only the ONNX model (21 MB), config and phonemizer data (5.4 MB).
uv run python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    "n4ze3m/tiny-voca",
    local_dir="weights",
    allow_patterns=["config.json", "onnx/model.onnx", "phonemizer/*"],
)
PY

# Tell the phonemizer where its data lives.
export DIZIONE_DATA="$PWD/weights/phonemizer"
```

`uv.lock` is committed for reproducible installs. To use the legacy torch
backend instead, run `uv sync --extra engine --extra dev`, download
`tiny-voca.pt` as well, and set `engine.backend = "torch"` in `book.toml`
(or pass `--backend torch`). `git` must be available because dizione/tiny-voca
are installed from their git repositories.

> tiny-voca is English only and has two fixed voices. Foreign names and unusual
> spellings are often mispronounced. See the
> [tiny-voca README](https://github.com/n4ze3m/tiny-voca) for details.

## Source formats

| Extension | Reader | Notes |
| --- | --- | --- |
| `.txt`, `.md` | UTF-8 plain text | Chapters come from the heading regex. |
| `.epub` | built-in, standard library only | Title/author and reading order come from EPUB metadata; no extra dependency. |

EPUB documents are read in spine order. If the book contains "Chapter One"-style
headings they are used as chapters; otherwise each EPUB document becomes a
chapter, using its first `<h1>` as the title. The source file is copied into
the project, so generated projects are self-contained:

```bash
# --title / --author are optional for EPUB: metadata is used when omitted.
uv run audiobook-creator init --source book.epub --dir my-book
uv run audiobook-creator status --config my-book/book.toml
```

Scanned/image PDFs and other formats are not supported yet; convert them to
text first (or ask for PDF support, which would need an optional extraction
dependency).

## Edge devices

The default setup is deliberately CPU-only and lightweight:

- **Requirements**: Python 3.11–3.14 (onnxruntime has no 3.15 wheels yet),
  ~1 GB RAM for synthesis, no GPU.
- **Footprint**: ONNX Runtime, numpy, soundfile, the 5.4 MB dizione data and
  the 21 MB ONNX model — no torch, no CUDA libraries, no runtime network calls.
- **Memory**: assembly streams one chapter at a time, so even a 16-hour book
  uses roughly one chapter of RAM, not the whole audiobook.
- **Speed**: tiny-voca is around 70x real time on four CPU threads; slower
  boards are still fine for unattended batch rendering.

Synthesis uses `engine.backend = "onnx"` by default. ONNX Runtime also has
mobile and WebAssembly builds, which is the bridge to phone and browser-local
apps. The torch backend remains available via `--backend torch` if needed.

## Quickstart

```bash
# 1. Create a project from a source text.
uv run audiobook-creator init \
  --source examples/sample-book.txt \
  --title "My First Audiobook" \
  --author "Me" \
  --dir my-book

# 2. Adjust settings by editing my-book/book.toml or from the CLI:
uv run audiobook-creator config --config my-book/book.toml \
  --voice F2 --speed 1.0 --chapter-gap-ms 700 --chapters-per-file all
#    `init` anchors the weights path absolutely when the model assets exist
#    relative to your current directory; otherwise set `engine.checkpoint`.

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
| `init` | Create a project directory, config, manifest and directories. Reads EPUB title/author when `--title`/`--author` are omitted. |
| `config` | Print effective settings, or persist changes to `book.toml`. |
| `synthesize` | Synthesize pending chapters. `--force`, `--only N [N...]`, plus voice/engine flags. |
| `status` | Show per-chapter progress, duration and the output split plan. |
| `assemble` | Join chapter clips into one or several files; audio/output flags. |
| `voices` | List available tiny-voca voices. |

Every config value can be overridden on the command line for a single run:

- Voice: `--voice F2|M2`, `--speed`, `--pitch-shift`, `--pitch-range`, `--seed`.
- Engine: `--device`, `--checkpoint`.
- Audio: `--chapter-gap-ms`, `--sample-rate`, `--format wav|mp3`, `--bitrate`.
- Output: `--chapters-per-file N|all`, `--name-pattern`, `--title`, `--output`.

`config` writes those overrides into `book.toml` permanently; `synthesize` and
`assemble` apply them only to that run. `init` accepts them when creating a project.

## Project layout

```
tts-audiobook/
├── pyproject.toml
├── scripts/setup.sh            # env, install, weights download
├── examples/sample-book.txt    # sample source text
├── src/audiobook_creator/
│   ├── cli.py                  # argparse CLI (presentation only)
│   ├── service.py              # application functions for any frontend
│   ├── config.py               # book.toml data model + loader
│   ├── book.py                 # source dispatch + chapter splitting
│   ├── epub.py                 # EPUB reader (stdlib zipfile/XML)
│   ├── synthesis.py            # backend facade (ONNX/torch)
│   ├── onnx_backend.py         # ONNX Runtime synthesis
│   ├── speech_text.py          # sentence splitting + peak limiting
│   ├── audio.py                # WAV I/O + streaming final assembly
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

## Local API (stage one)

A FastAPI app exposes the service layer on `127.0.0.1:8000` by default — all
processing stays on your machine. Install the web extra and run it:

```bash
uv sync --extra onnx --extra web --extra dev
export DIZIONE_DATA="$PWD/weights/phonemizer"
uv run python -m webapi        # or: uv run audiobook-api
```

Interactive docs are at `/docs`. Main endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/projects` | Upload `.txt`/`.md`/`.epub` with optional settings; returns a project id |
| `GET /api/projects` | List local projects |
| `GET /api/projects/{id}` | Status, chapters and output plan |
| `PATCH /api/projects/{id}` | Persist settings changes |
| `POST /api/projects/{id}/synthesize` | Start a background job; returns a job id |
| `GET /api/projects/{id}/jobs/{job}` | Poll job state/result |
| `GET /api/projects/{id}/events?job_id=…` | Server-sent events with progress |
| `POST /api/projects/{id}/assemble` | Assemble one or several audiobook files |
| `GET /api/projects/{id}/audio/{file}` | Play/download a clip or finished file |
| `POST /api/projects/{id}/preview` | Audition a sentence without rendering a book |

Projects live under `./projects/` by default; override with
`AUDIOBOOK_PROJECTS_DIR`, and change the bind address with `AUDIOBOOK_HOST` /
`AUDIOBOOK_PORT`. A minimal placeholder page is at `/static/index.html`; a full
web interface will replace it in stage two.

## Service layer

`service.py` holds the application logic as plain functions that take paths
and data and return structured, JSON-friendly results. It has no argparse and
no printing, so the CLI and a future local web/desktop frontend can share it:

| Function | Purpose |
| --- | --- |
| `create_project()` | Load EPUB/txt, copy the source, write config + manifest |
| `get_settings()` / `update_settings()` | Read or persist `book.toml` |
| `list_chapters()` / `get_status()` | Chapter list, progress, total duration, output plan |
| `synthesize()` | Render chapters, update manifest, emit `SynthProgress` events |
| `assemble()` | Stream audio to one or several files, return `OutputFile` records |
| `preview_voice()` | Synthesize one sentence to audition a voice |

Long-running work reports progress through callbacks: the CLI prints lines,
while a web UI can turn the same events into WebSocket/SSE messages. Errors are
raised as exceptions, leaving presentation to the caller. The CLI commands now
delegate to these functions; existing command behavior is unchanged.

## Configuration (`book.toml`)

```toml
[book]
title = "My First Audiobook"
author = "Me"

[source]
path = "book.txt"
chapter_pattern = "^\\s*(?:#{1,3}\\s*)?(?:chapter|part|book)\\s+([^\\n]+?)\\s*$"

[engine]
backend = "onnx"              # "onnx" (default) or "torch"
checkpoint = "weights/tiny-voca.pt"   # anchors weights/ dir; needed for torch
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

[output]
# 0 or "all" = one file for the whole book; N = N chapters per output file.
chapters_per_file = 0
# Placeholders: {title}, {chapter} (part number or "all"), {range} (or "all").
# Empty = use paths.output, or append _partN when splitting.
name_pattern = ""

[paths]
audio_dir = "audio"
output = "out/audiobook.wav"
```

Relative paths are resolved against the directory containing `book.toml`.
The output extension follows `audio.output_format` (the config adds/renames it
automatically).

### Output splitting and naming

`output.chapters_per_file` groups chapters into separate audiobook files.
The final group may be shorter: **77 chapters with 10 per file produces 8
files** — seven with 10 chapters and a last one with 7. `0` or `"all"` produces
one file containing everything.

`output.name_pattern` controls filenames. Available placeholders:

| Placeholder | Split mode | Single-file mode |
| --- | --- | --- |
| `{title}` | book title | book title |
| `{chapter}` | part number (`1`, `2`, …) | `all` |
| `{range}` | `001-010`, `071-077`, … | `all` |

With an empty pattern, single-file output uses `paths.output` exactly, and
split mode appends `_partN` to its stem. When splitting, the pattern must
contain `{chapter}` or `{range}` so files cannot overwrite each other.

Examples:

```bash
# One big book, custom name: out/My Book - all.wav
uv run audiobook-creator config --config book.toml \
  --chapters-per-file all --name-pattern "{title} - {chapter}"

# Ten chapters per file: out/My Book - 1.wav ... out/My Book - 8.wav
uv run audiobook-creator config --config book.toml \
  --chapters-per-file 10 --name-pattern "{title} - {chapter}"

# One-off override for a single assembly, by chapter ranges:
uv run audiobook-creator assemble --config book.toml \
  --chapters-per-file 10 --name-pattern "{title}_{range}"

# Change voice and pacing, then synthesize:
uv run audiobook-creator config --config book.toml --voice M2 --speed 1.1
uv run audiobook-creator synthesize --config book.toml

# Or override just one run without touching the config:
uv run audiobook-creator synthesize --config book.toml --voice F2 --pitch-range 1.2
```

`config` and `status` both print the resulting output plan (file count and
chapters per file) before you render.

## Tests

```bash
uv run python -m unittest discover -s tests
```

## How synthesis works

`synthesis.py` is a facade over two interchangeable backends with the same
`load()` / `synthesize(text)` interface:

- **ONNX (default)**: `onnx_backend.py` phonemizes text with dizione and runs
  `onnx/model.onnx` through ONNX Runtime on CPU.
- **Torch (optional)**: `synthesis.py` calls tiny-voca's
  `tiny_voca.infer.speak_text()` on the PyTorch checkpoint.

Both read text of any length sentence by sentence, join the audio and keep the
peak below clipping. `speech_text.py` contains the local sentence splitting and
peak limiting, so the ONNX backend never needs to import torch. The tool
handles chapter-level concerns (splitting, file naming, resume, gaps, assembly)
and leaves voice quality and pronunciation to tiny-voca.

## License

Apache-2.0, matching tiny-voca. tiny-voca weights are Apache-2.0 and its
dataset is CC BY 4.0 — see the upstream repository for full terms.
