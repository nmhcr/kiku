// Browser synthesis worker. Loads the tiny-voca GGUF engine once, then
// synthesizes chapters off the main thread. Message protocol is documented
// in lib/synth.js.

import { readGGUF, TinyVoca } from "./tiny-voca.js";
import { fromGGUF } from "./dizione/engine.js";
import { utterancesOf, limited } from "./text.js";

const MODEL_URL = new URL("../assets/tiny-voca-js.gguf", import.meta.url);
const CACHE_NAME = "kiku-engine-v1";

let voca = null;
let phonemizer = null;
let sampleRate = 24000;
let maxTokens = 0;
let control = null;
let currentJobId = null;

class Stopped extends Error {}

function freshControl() {
  return { paused: false, stopped: false, waiters: [] };
}

function post(message, transfers = []) {
  postMessage(message, transfers);
}

function status(text, progress = null) {
  post({ type: "status", text, progress });
}

function release(control) {
  const waiters = control.waiters.splice(0);
  for (const resolve of waiters) resolve();
}

function gate() {
  const current = control;
  return new Promise((resolve, reject) => {
    const check = () => {
      if (!current || current !== control) return reject(new Stopped());
      if (current.stopped) return reject(new Stopped());
      if (!current.paused) return resolve();
      current.waiters.push(check);
    };
    check();
  });
}

async function fetchModel() {
  const cache = await caches.open(CACHE_NAME).catch(() => null);
  let response = cache ? await cache.match(MODEL_URL.href).catch(() => null) : null;
  if (!response || !response.ok) {
    status("Downloading the speech engine (about 8 MB)…", 0);
    response = await fetch(MODEL_URL.href);
    if (!response.ok) throw new Error(`could not download the engine: HTTP ${response.status}`);
    if (cache) cache.put(MODEL_URL.href, response.clone()).catch(() => {});
  } else {
    status("Loading the speech engine…", 0);
  }

  const total = Number(response.headers.get("content-length")) || 0;
  if (!response.body || !total) return new Uint8Array(await response.arrayBuffer()).buffer;

  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    status("Downloading the speech engine (about 8 MB)…", received / total);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes.buffer;
}

async function load() {
  if (voca) return;
  status("Starting the phonemizer and model…");
  const buffer = await fetchModel();
  const file = readGGUF(buffer);
  voca = new TinyVoca(file);
  phonemizer = await fromGGUF(file);
  sampleRate = voca.config.sample_rate;
  maxTokens = voca.config.max_tokens;
  status("Ready.");
  post({ type: "ready", sampleRate, voices: voca.voices });
}

function voiceSettings(settings) {
  return {
    voice: settings.voice,
    speed: Number(settings.speed),
    pitchShift: Number(settings.pitchShift),
    pitchRange: Number(settings.pitchRange),
    seed: Number(settings.seed) || 0,
  };
}

async function synthesizeText(text, settings) {
  const utterances = utterancesOf(text, phonemizer, maxTokens - 12);
  if (!utterances.length) return null;
  const options = voiceSettings(settings);
  const chunks = [];
  let length = 0;
  for (const utterance of utterances) {
    const audio = voca.synthesize(utterance.ids, utterance.stress, options).audio;
    chunks.push(audio);
    length += audio.length;
    // Let queued pause/resume/stop messages be handled even inside a long chapter.
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  const whole = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    whole.set(chunk, offset);
    offset += chunk.length;
  }
  const limitedAudio = limited(whole);
  return {
    audio: limitedAudio.buffer,
    samples: limitedAudio.length,
    durationSeconds: limitedAudio.length / sampleRate,
  };
}

async function synthesize(id, chapters, settings) {
  control = freshControl();
  currentJobId = id;
  let synthesized = 0;
  let skipped = 0;
  try {
    for (const chapter of chapters) {
      await gate();
      post({ type: "progress", id, number: chapter.number, state: "started" });
      const result = await synthesizeText(chapter.text, settings);
      if (!result) {
        skipped += 1;
        post({ type: "progress", id, number: chapter.number, state: "skipped" });
        continue;
      }
      await gate();
      post({
        type: "chapterAudio",
        id,
        number: chapter.number,
        samples: result.samples,
        durationSeconds: result.durationSeconds,
        audio: result.audio,
      }, [result.audio]);
      post({
        type: "progress",
        id,
        number: chapter.number,
        state: "done",
        durationSeconds: result.durationSeconds,
      });
      synthesized += 1;
    }
    post({ type: "done", id, result: { synthesized, skipped } });
  } catch (error) {
    if (error instanceof Stopped) {
      post({ type: "done", id, result: { synthesized, skipped, stopped: true } });
    } else {
      post({ type: "error", id, message: String(error && error.message || error) });
    }
  } finally {
    control = null;
    currentJobId = null;
  }
}

onmessage = async ({ data }) => {
  try {
    if (data.type === "load") {
      await load();
    } else if (data.type === "synthesize") {
      if (!voca) await load();
      await synthesize(data.id, data.chapters, data.settings);
    } else if (data.type === "preview") {
      if (!voca) await load();
      const result = await synthesizeText(data.text, data.settings);
      if (!result) throw new Error("nothing to pronounce in that text");
      post({ type: "preview", id: data.id, audio: result.audio, sampleRate }, [result.audio]);
    } else if (data.type === "pause" && control) {
      control.paused = true;
      post({ type: "jobState", id: currentJobId, state: "paused" });
    } else if (data.type === "resume" && control) {
      control.paused = false;
      post({ type: "jobState", id: currentJobId, state: "running" });
      release(control);
    } else if (data.type === "stop" && control) {
      control.stopped = true;
      post({ type: "jobState", id: currentJobId, state: "stopping" });
      release(control);
    }
  } catch (error) {
    post({ type: "error", id: data.id, message: String(error && error.message || error) });
  }
};
