"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  projectId: null,
  projects: [],
  status: null,
  settings: null,
  outputs: [],
  eventSource: null,
  jobId: null,
  jobState: null,
};

function banner(message, kind = "info") {
  const el = $("banner");
  el.textContent = message;
  el.className = `banner ${kind}`;
  el.hidden = false;
  clearTimeout(banner.timer);
  banner.timer = setTimeout(() => { el.hidden = true; }, kind === "error" ? 8000 : 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  let data = null;
  if (contentType.includes("application/json")) {
    data = await response.json();
  } else if (!response.ok) {
    data = { detail: await response.text() };
  }
  if (!response.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string"
      ? detail
      : (detail && detail.message) || (detail && detail.job_id) || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function apiJson(path, method = "GET", body = null) {
  return api(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

function describePlan(plan) {
  if (!plan) return "";
  if (!plan.chapters_per_file) return `one file with all ${plan.total_chapters} chapter(s)`;
  const sizes = plan.files.map((file) => file.chapter_count).join(", ");
  return `${plan.files.length} file(s): ${sizes} chapter(s)`;
}

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
    button.className = "project" + (project.id === state.projectId ? " active" : "");
    const label = document.createElement("span");
    label.textContent = project.title || project.id;
    const meta = document.createElement("small");
    meta.textContent = project.id;
    button.append(label, meta);
    button.onclick = () => selectProject(project.id).catch((error) => banner(error.message, "error"));
    list.append(button);
  }
}

function renderStatus() {
  const status = state.status;
  if (!status) return;
  $("summary").textContent =
    `${status.title}${status.author ? " — " + status.author : ""} · ` +
    `${status.done}/${status.chapters.length} synthesized · ` +
    `${(status.total_seconds / 60).toFixed(1)} min · ${describePlan(status.output_plan)}`;

  const list = $("chapter-list");
  list.replaceChildren();
  for (const chapter of status.chapters) {
    const row = document.createElement("div");
    row.className = `chapter ${chapter.status}`;
    row.dataset.number = String(chapter.number);

    const number = document.createElement("span");
    number.className = "num";
    number.textContent = String(chapter.number).padStart(3, "0");
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = chapter.title;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = chapter.status;
    const duration = document.createElement("span");
    duration.className = "dur";
    duration.textContent = chapter.duration_seconds ? `${chapter.duration_seconds.toFixed(1)}s` : "—";

    row.append(number, title, badge, duration);
    list.append(row);
  }
  updateProgress();
}

function renderOutputs() {
  const list = $("output-list");
  list.replaceChildren();
  if (!state.outputs.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Not assembled yet.";
    list.append(empty);
    return;
  }
  for (const file of state.outputs) {
    const name = file.path.split("/").pop();
    const url = `/api/projects/${encodeURIComponent(state.projectId)}/audio/${encodeURIComponent(name)}`;
    const wrap = document.createElement("div");
    wrap.className = "output";
    const label = document.createElement("span");
    label.textContent = `${name} — chapters ${file.first_chapter}-${file.last_chapter} (${file.chapter_count} clips)`;
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = url;
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.textContent = "Download";
    wrap.append(label, audio, link);
    list.append(wrap);
  }
}

function renderSettings(settings) {
  $("settings-form").hidden = false;
  $("settings-hint").hidden = true;
  const voice = settings.voice || {};
  const audio = settings.audio || {};
  const output = settings.output_options || {};
  const values = {
    "st-title": settings.title,
    "st-author": settings.author,
    "st-voice": voice.voice,
    "st-backend": settings.backend,
    "st-speed": voice.speed,
    "st-pitch-shift": voice.pitch_shift,
    "st-pitch-range": voice.pitch_range,
    "st-seed": voice.seed,
    "st-gap": audio.chapter_gap_ms,
    "st-sample-rate": audio.sample_rate,
    "st-format": audio.output_format,
    "st-bitrate": audio.mp3_bitrate,
    "st-split": output.chapters_per_file ? output.chapters_per_file : "all",
    "st-pattern": output.name_pattern,
    "st-device": settings.device,
    "st-chapter-pattern": settings.chapter_pattern,
  };
  for (const [id, value] of Object.entries(values)) {
    if ($(id)) $(id).value = value == null ? "" : String(value);
  }
}

async function refreshProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  renderProjects();
}

async function refreshStatus() {
  if (!state.projectId) return;
  state.status = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
  renderStatus();
}

async function refreshSettings() {
  if (!state.projectId) return;
  state.settings = await api(`/api/projects/${encodeURIComponent(state.projectId)}/settings`);
  renderSettings(state.settings);
}

function setJobState(value) {
  state.jobState = value;
  const active = value != null;
  $("btn-synthesize").disabled = active;
  $("btn-pause").disabled = !active || value === "paused" || value === "stopping";
  $("btn-resume").disabled = value !== "paused";
  $("btn-stop").disabled = !active || value === "stopping";
}

function closeEvents() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  $("progress-wrap").hidden = true;
}

function finishJob(message, kind = "info") {
  closeEvents();
  state.jobId = null;
  setJobState(null);
  banner(message, kind);
  // Chapter rows and the progress bar were already updated from SSE events
  // in the browser; no server round-trip is needed to show completion.
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
  if (event.duration_seconds != null) {
    row.querySelector(".dur").textContent = `${event.duration_seconds.toFixed(1)}s`;
  }
  updateProgress();
}

function startEvents(jobId) {
  closeEvents();
  state.jobId = jobId;
  setJobState("running");
  $("progress-wrap").hidden = false;
  const url =
    `/api/projects/${encodeURIComponent(state.projectId)}/events?job_id=${encodeURIComponent(jobId)}`;
  const events = new EventSource(url);
  state.eventSource = events;

  events.onmessage = (message) => {
    let data;
    try { data = JSON.parse(message.data); } catch { return; }
    if (data.type === "done" || data.type === "stopped") {
      const text = data.type === "stopped"
        ? `Stopped: ${data.result.synthesized} chapter(s) completed`
        : `Synthesis complete: ${data.result.synthesized} chapter(s)`;
      finishJob(text);
      return;
    }
    if (data.type === "error") {
      finishJob(data.message, "error");
      return;
    }
    if (data.state === "paused") setJobState("paused");
    else if (data.state === "resumed") setJobState("running");
    else if (data.state === "stopped") setJobState("stopping");
    applyProgressEvent(data);
  };

  events.onerror = () => {
    events.close();
    state.eventSource = null;
    $("progress-wrap").hidden = true;
    banner("Progress stream disconnected. Select the project again to reload its state.", "error");
  };
}

async function jobAction(action) {
  if (!state.projectId || !state.jobId) return;
  try {
    const result = await apiJson(
      `/api/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(state.jobId)}/${action}`,
      "POST",
      {}
    );
    if (result.state === "paused") setJobState("paused");
    else if (result.state === "running") setJobState("running");
    else if (result.state === "stopping") setJobState("stopping");
    banner(`Job ${action} requested.`);
  } catch (error) {
    banner(error.message, "error");
  }
}

async function selectProject(id) {
  closeEvents();
  state.jobId = null;
  setJobState(null);
  state.projectId = id;
  state.outputs = [];
  $("detail-empty").hidden = true;
  $("detail").hidden = false;
  renderProjects();
  await Promise.all([refreshStatus(), refreshSettings()]);
  renderOutputs();
}

function numberOrBlank(id) {
  const value = $(id).value.trim();
  return value === "" ? null : Number(value);
}

$("new-project-form").onsubmit = async (event) => {
  event.preventDefault();
  const file = $("np-file").files[0];
  if (!file) { banner("Choose a source file first.", "error"); return; }
  const form = new FormData();
  form.append("file", file);
  form.append("title", $("np-title").value);
  form.append("author", $("np-author").value);
  form.append("voice", $("np-voice").value);
  const speed = numberOrBlank("np-speed");
  if (speed != null) form.append("speed", String(speed));
  const gap = numberOrBlank("np-gap");
  if (gap != null) form.append("chapter_gap_ms", String(gap));
  if ($("np-split").value.trim()) form.append("chapters_per_file", $("np-split").value.trim());
  if ($("np-pattern").value.trim()) form.append("name_pattern", $("np-pattern").value.trim());
  try {
    const info = await api("/api/projects", { method: "POST", body: form });
    banner(`Created project "${info.title}"`);
    await refreshProjects();
    await selectProject(info.id);
  } catch (error) {
    banner(error.message, "error");
  }
};

$("settings-form").onsubmit = async (event) => {
  event.preventDefault();
  const changes = {
    title: $("st-title").value,
    author: $("st-author").value,
    voice: $("st-voice").value,
    backend: $("st-backend").value,
    speed: numberOrBlank("st-speed"),
    pitch_shift: numberOrBlank("st-pitch-shift"),
    pitch_range: numberOrBlank("st-pitch-range"),
    seed: numberOrBlank("st-seed"),
    chapter_gap_ms: numberOrBlank("st-gap"),
    sample_rate: numberOrBlank("st-sample-rate"),
    output_format: $("st-format").value,
    mp3_bitrate: $("st-bitrate").value.trim(),
    chapters_per_file: $("st-split").value.trim(),
    name_pattern: $("st-pattern").value.trim(),
    device: $("st-device").value.trim(),
    chapter_pattern: $("st-chapter-pattern").value.trim(),
  };
  try {
    const result = await apiJson(
      `/api/projects/${encodeURIComponent(state.projectId)}`, "PATCH", changes
    );
    banner(result.changed ? "Settings saved." : "No changes to save.");
    await refreshStatus();
    await refreshProjects();
  } catch (error) {
    banner(error.message, "error");
  }
};

$("btn-synthesize").onclick = async () => {
  if (!state.projectId) return;
  const options = {
    force: $("chk-force").checked,
    from: numberOrBlank("syn-from"),
    to: numberOrBlank("syn-to"),
    count: numberOrBlank("syn-count"),
  };
  try {
    const result = await apiJson(
      `/api/projects/${encodeURIComponent(state.projectId)}/synthesize`,
      "POST",
      options
    );
    startEvents(result.job_id);
  } catch (error) {
    const existing = error.data && error.data.detail && error.data.detail.job_id;
    if (error.status === 409 && existing) {
      banner("A job is already running; following its progress.", "info");
      startEvents(existing);
    } else {
      banner(error.message, "error");
    }
  }
};

$("btn-pause").onclick = () => jobAction("pause");
$("btn-resume").onclick = () => jobAction("resume");
$("btn-stop").onclick = () => jobAction("stop");

$("btn-assemble").onclick = async () => {
  if (!state.projectId) return;
  try {
    const files = await apiJson(
      `/api/projects/${encodeURIComponent(state.projectId)}/assemble`,
      "POST",
      { num_files: numberOrBlank("asm-num-files") }
    );
    state.outputs = files || [];
    renderOutputs();
    banner(`Wrote ${state.outputs.length} audiobook file(s).`);
  } catch (error) {
    banner(error.message, "error");
  }
};

$("btn-preview").onclick = async () => {
  if (!state.projectId) return;
  const text = $("preview-text").value.trim();
  if (!text) { banner("Enter some text to preview.", "error"); return; }
  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.projectId)}/preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: $("st-voice").value }),
      }
    );
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "Preview failed");
    }
    const blob = await response.blob();
    const audio = $("preview-audio");
    if (audio.dataset.url) URL.revokeObjectURL(audio.dataset.url);
    audio.dataset.url = URL.createObjectURL(blob);
    audio.src = audio.dataset.url;
    audio.hidden = false;
    audio.play().catch(() => {});
  } catch (error) {
    banner(error.message, "error");
  }
};

refreshProjects().catch((error) => banner(error.message, "error"));
