# Kiku browser app

A fully client-side build of Kiku: no server, no API, no uploads. Books and
audio stay in the browser's IndexedDB, and the speech engine runs in a Web
Worker. This directory is a static site and can be hosted on GitHub Pages or
any static host.

## Run locally

Any static file server works (module workers require HTTP, not `file://`):

```bash
cd webapp
python3 -m http.server 8000
# open http://localhost:8000
```

The first load downloads `assets/tiny-voca-js.gguf` (about 8 MB) and caches it
in the browser. Keep the tab open while synthesizing.

## Deploy to GitHub Pages

The workflow in `.github/workflows/pages.yml` publishes this directory as a
Pages artifact on every push to `main`. In the repository settings, set
**Pages → Build and deployment → Source: GitHub Actions**.

All URLs are relative, so the app also works under a project subpath
(`https://<owner>.github.io/<repo>/`).

## Layout

| Path | What it does |
| --- | --- |
| `index.html`, `style.css` | UI, adapted from `webapi/static/` |
| `app.js` | UI controller (project, settings, jobs, outputs) |
| `engine/` | tiny-voca GGUF engine and dizione phonemizer, copied from [tiny-voca](https://github.com/n4ze3m/tiny-voca) `js/` |
| `engine/worker.js` | Synthesis worker and message protocol |
| `lib/book.js` | TXT/MD/EPUB reading and chapter splitting |
| `lib/store.js` | IndexedDB: projects, chapter audio, output plans |
| `lib/synth.js` | Main-thread worker interface |
| `lib/assemble.js` | Output grouping, naming and WAV assembly |
| `lib/jszip.min.js` | Vendored JSZip 3.10.1 (EPUB unzipping) |
| `assets/tiny-voca-js.gguf` | Quantized tiny-voca model + phonemizer tables |

## Limitations compared to the Python API

- Synthesis only runs while the tab is open.
- Output is WAV only (MP3 remains available through the Python CLI).
- Very large books use browser storage; disk space is per-origin and shared
  with other sites.
- The engine is the 8 MB Q8 GGUF build, not the 21 MB float32 ONNX model.
