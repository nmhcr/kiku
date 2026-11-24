// Kiku browser app. All state lives in IndexedDB; all synthesis happens in
// engine/worker.js. No network calls after the 8 MB model is cached.

import { defaultSettings, normalizeSettings, describePlan } from "./lib/settings.js";
import { readSource, splitChapters } from "./lib/book.js";
import { groupChapters, outputPlans, buildOutputBlob } from "./lib/assemble.js";
import * as store from "./lib/store.js";
import { engineReady, runJob, preview, jobControl, onEngineStatus, onEngineError } from "./lib/synth.js";

const $ = (id) => document.getElementById(id);

const state = {
  project: null,
  projects: [],
  chapters: [],
  outputs: [],
  jobId: null,
  jobState: null,
  outputUrls: [],
};

let restoreToken = 0;

function banner(message, kind = "info") {
  const el = $("banner");
  el.textContent = message;
  el.className = `banner ${kind}`;
  el.hidden = false;
  clearTimeout(banner.timer);
  banner.timer = setTimeout(() => { el.hidden = true; }, kind === "error" ? 8000 : 4000);
}

onEngineStatus((text, progress) => {
  const box = $("engine-status");
  if (!text) { box.hidden = true; return; }
  box.hidden = false;
  $("engine-label").textContent = text;
  $("engine-bar").style.width = progress == null ? "0%" : `${Math.round(progress * 100)}%`;
});

onEngineError((error) => banner(error.message, "error"));

function renderProjects() {
  const list = $("project-list");
  list.replaceChildren();
  if (!state.projects.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No projects yet.";
    list.append(empty);
    return;
  }
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.className = "project" + (state.project && project.id === state.project.id ? " active" : "");
    const label = document.createElement("span");
    label.textContent = project.settings.title || project.id;
    const meta = document.createElement("small");
    meta.textContent = project.id;
    button.append(label, meta);
    button.onclick = () => selectProject(project.id).catch((error) => banner(error.message, "error"));
    list.append(button);
  }
}

function renderStatus() {
  if (!state.project) return;
  const settings = state.project.settings;
  const done = state.chapters.filter((chapter) => chapter.status === "done").length;
  const seconds = state.chapters.reduce(
    (sum, chapter) => sum + (chapter.status === "done" ? chapter.durationSeconds || 0 : 0), 0
  );
  $("summary").textContent =
    `${settings.title}${settings.author ? " — " + settings.author : ""} · ` +
    `${done}/${state.chapters.length} synthesized · ${(seconds / 60).toFixed(1)} min · ` +
    describePlan(settings, state.chapters.length);

  const list = $("chapter-list");
  list.replaceChildren();
  for (const chapter of state.chapters) {
    const row = document.createElement("div");
    row.className = `chapter ${chapter.status || "pending"}`;
    row.dataset.number = String(chapter.number);

    const number = document.createElement("span");
    number.className = "num";
    number.textContent = String(chapter.number).padStart(3, "0");
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = chapter.title;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = chapter.status || "pending";
    const duration = document.createElement("span");
    duration.className = "dur";
    duration.textContent = chapter.durationSeconds ? `${chapter.durationSeconds.toFixed(1)}s` : "—";

    row.append(number, title, badge, duration);
    list.append(row);
  }
  updateProgress();
}

function releaseOutputUrls() {
  for (const url of state.outputUrls) URL.revokeObjectURL(url);
  state.outputUrls = [];
}

function renderOutputs() {
  const list = $("output-list");
  list.replaceChildren();
  releaseOutputUrls();
  if (!state.outputs.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Not assembled yet.";
    list.append(empty);
    return;
  }
  for (const output of state.outputs) {
    const wrap = document.createElement("div");
    wrap.className = "output";
    const label = document.createElement("span");
    label.textContent = `${output.name} — chapters ${output.first}-${output.last} (${output.count} clips)`;
    const audio = document.createElement("audio");
    audio.controls = true;
    const blob = buildOutputBlob(output, state.chapters, state.project.settings);
    const url = URL.createObjectURL(blob);
    state.outputUrls.push(url);
    audio.src = url;
    const link = document.createElement("a");
    link.href = url;
    link.download = output.name;
    link.textContent = "Download";
    wrap.append(label, audio, link);
    list.append(wrap);
  }
}

function renderSettings(settings) {
  $("settings-form").hidden = false;
  $("settings-hint").hidden = true;
  const values = {
    "st-title": settings.title,
    "st-author": settings.author,
    "st-voice": settings.voice,
    "st-format": settings.outputFormat,
    "st-speed": settings.speed,
    "st-pitch-shift": settings.pitchShift,
    "st-pitch-range": settings.pitchRange,
    "st-seed": settings.seed,
    "st-gap": settings.chapterGapMs,
    "st-split": settings.chaptersPerFile ? settings.chaptersPerFile : "all",
    "st-pattern": settings.namePattern,
    "st-chapter-pattern": settings.chapterPattern,
  };
  for (const [id, value] of Object.entries(values)) {
    if ($(id)) $(id).value = value == null ? "" : String(value);
  }
}

async function refreshProjects() {
  state.projects = await store.listProjects();
  renderProjects();
}

async function selectProject(id) {
  restoreToken += 1;
  jobControl.stop();
  state.jobId = null;
  setJobState(null);
  state.project = await store.getProject(id);
  if (!state.project) throw new Error("project not found");
  state.chapters = await store.getChapters(id);
  state.outputs = await store.getOutputs(id);
  $("detail-empty").hidden = true;
  $("detail").hidden = false;
  renderProjects();
  renderStatus();
  renderSettings(state.project.settings);
  renderOutputs();
}

function setJobState(value) {
  state.jobState = value;
  const active = value != null;
  $("btn-synthesize").disabled = active;
  $("btn-pause").disabled = !active || value === "paused" || value === "stopping";
  $("btn-resume").disabled = value !== "paused";
  $("btn-stop").disabled = !active || value === "stopping";
  updateProgress();
}

function updateProgress() {
  const rows = [...document.querySelectorAll("#chapter-list .chapter")];
  const done = rows.filter((row) => row.classList.contains("done")).length;
  $("progress-bar").style.width = rows.length ? `${(done / rows.length) * 100}%` : "0%";
  const prefix =
    state.jobState === "paused" ? "Paused" :
    state.jobState === "stopping" ? "Stopping" : "Synthesizing";
  $("progress-label").textContent = `${prefix}… ${done}/${rows.length}`;
}

function applyProgressEvent(event) {
  const row = document.querySelector(`#chapter-list .chapter[data-number="${event.number}"]`);
  if (!row) return;
  row.className = `chapter ${event.state}`;
  row.querySelector(".badge").textContent = event.state;
  if (event.durationSeconds != null) {
    row.querySelector(".dur").textContent = `${event.durationSeconds.toFixed(1)}s`;
  }
  updateProgress();
}

function numberOrBlank(id) {
  const value = $(id).value.trim();
  return value === "" ? null : Number(value);
}

$("new-project-form").onsubmit = async (event) => {
  event.preventDefault();
  const file = $("np-file").files[0];
  if (!file) { banner("Choose a source file first.", "error"); return; }
  try {
    banner("Reading the book…");
    const source = await readSource(file);
    const settings = normalizeSettings({
      title: $("np-title").value.trim() || source.title,
      author: $("np-author").value.trim() || source.author,
      voice: $("np-voice").value,
      speed: numberOrBlank("np-speed") ?? 1,
      chapterGapMs: numberOrBlank("np-gap") ?? 700,
      chaptersPerFile: $("np-split").value.trim(),
      namePattern: $("np-pattern").value.trim(),
    });
    if (!settings.title) throw new Error("a title is required");
    const chapters = splitChapters(source.text, settings.chapterPattern, source.documents);
    if (!chapters.length) throw new Error("no readable chapters found in that file");
    const id = `book-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    const project = {
      id,
      settings,
      sourceName: file.name,
      sourceText: source.text,
      documents: source.documents,
      createdAt: new Date().toISOString(),
    };
    await store.createProject(project, chapters.map((chapter) => ({
      ...chapter,
      projectId: id,
      status: "pending",
      durationSeconds: null,
      samples: 0,
    })));
    banner(`Created project "${settings.title}" — ${chapters.length} chapter(s).`);
    await refreshProjects();
    await selectProject(id);
  } catch (error) {
    banner(error.message, "error");
  }
};

$("settings-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!state.project) return;
  try {
    const before = state.project.settings.chapterPattern;
    const settings = normalizeSettings({
      ...state.project.settings,
      title: $("st-title").value,
      author: $("st-author").value,
      voice: $("st-voice").value,
      outputFormat: $("st-format").value,
      speed: numberOrBlank("st-speed"),
      pitchShift: numberOrBlank("st-pitch-shift"),
      pitchRange: numberOrBlank("st-pitch-range"),
      seed: numberOrBlank("st-seed"),
      chapterGapMs: numberOrBlank("st-gap"),
      chaptersPerFile: $("st-split").value.trim(),
      namePattern: $("st-pattern").value.trim(),
      chapterPattern: $("st-chapter-pattern").value,
    });
    const project = { ...state.project, settings };
    let chapters = state.chapters;
    if (settings.chapterPattern !== before) {
      const old = new Map(state.chapters.map((c) => [`${c.number}:${c.title}`, c]));
      const fresh = splitChapters(project.sourceText, settings.chapterPattern, project.documents || []);
      chapters = fresh.map((chapter) => {
        const previous = old.get(`${chapter.number}:${chapter.title}`);
        return {
          ...chapter,
          projectId: project.id,
          status: previous?.status === "done" ? "done" : "pending",
          durationSeconds: previous?.durationSeconds ?? null,
          samples: previous?.samples ?? 0,
          audio: previous?.audio,
        };
      });
      await store.runReset(project.id, chapters);
    }
    await store.putProject(project);
    state.project = project;
    state.chapters = chapters;
    renderStatus();
    renderSettings(settings);
    renderProjects();
    banner("Settings saved.");
  } catch (error) {
    banner(error.message, "error");
  }
};

$("btn-synthesize").onclick = async () => {
  try {
    if (!state.project) throw new Error("Select a project first.");
    const settings = state.project.settings;
    const from = numberOrBlank("syn-from");
    const to = numberOrBlank("syn-to");
    const count = numberOrBlank("syn-count");
    const force = $("chk-force").checked;
    let chapters = state.chapters.filter((chapter) => {
      if (from !== null && chapter.number < from) return false;
      if (to !== null && chapter.number > to) return false;
      return true;
    });
    if (count !== null) chapters = chapters.slice(0, count);
    const targets = chapters.filter((chapter) => force || chapter.status !== "done");
    if (!targets.length) { banner("Nothing to synthesize."); return; }

    const id = `job-${Date.now().toString(36)}`;
    state.jobId = id;
    setJobState("running");
    $("progress-wrap").hidden = false;
    const result = await runJob({
      id,
      chapters: targets.map((chapter) => ({ number: chapter.number, text: chapter.text })),
      settings,
      onProgress: applyProgressEvent,
      onState: setJobState,
      onChapterAudio: async (data) => {
        const chapter = state.chapters.find((c) => c.number === data.number);
        if (!chapter) return;
        chapter.audio = data.audio;
        chapter.samples = data.samples;
        chapter.durationSeconds = data.durationSeconds;
        chapter.status = "done";
        await store.putChapter(chapter);
      },
    });
    setJobState(null);
    state.jobId = null;
    $("progress-wrap").hidden = true;
    const message = result.stopped
      ? `Stopped: ${result.synthesized} chapter(s) completed`
      : `Synthesis complete: ${result.synthesized} chapter(s)` +
        (result.skipped ? `, ${result.skipped} skipped` : "");
    banner(message);
    state.chapters = await store.getChapters(state.project.id);
    renderStatus();
  } catch (error) {
    setJobState(null);
    state.jobId = null;
    $("progress-wrap").hidden = true;
    banner(error.message, "error");
  }
};

$("btn-pause").onclick = () => jobControl.pause();
$("btn-resume").onclick = () => jobControl.resume();
$("btn-stop").onclick = () => jobControl.stop();

$("btn-assemble").onclick = async () => {
  if (!state.project) return;
  try {
    const groups = groupChapters(state.chapters, state.project.settings.chaptersPerFile, numberOrBlank("asm-num-files"));
    const outputs = outputPlans(state.project, state.chapters, groups, numberOrBlank("asm-num-files"));
    await store.setOutputs(state.project.id, outputs);
    state.outputs = outputs;
    renderOutputs();
    banner(`Prepared ${outputs.length} audiobook file(s).`);
  } catch (error) {
    banner(error.message, "error");
  }
};

$("btn-preview").onclick = async () => {
  if (!state.project) return;
  const text = $("preview-text").value.trim();
  if (!text) { banner("Enter some text to preview.", "error"); return; }
  try {
    const audio = $("preview-audio");
    if (audio.dataset.url) URL.revokeObjectURL(audio.dataset.url);
    const bytes = await preview(text, state.project.settings);
    const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    audio.dataset.url = url;
    audio.src = url;
    audio.hidden = false;
    audio.play().catch(() => {});
  } catch (error) {
    banner(error.message, "error");
  }
};

refreshProjects()
  .then(async () => {
    const token = ++restoreToken;
    const lastId = await store.getMeta("lastProjectId");
    if (token !== restoreToken || state.project) return;
    if (lastId && state.projects.some((project) => project.id === lastId)) {
      await selectProject(lastId);
    }
  })
  .catch((error) => banner(error.message, "error"));
engineReady().catch((error) => banner(error.message, "error"));
