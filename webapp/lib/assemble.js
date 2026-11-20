// Chapter grouping, file naming and WAV assembly.
// Audio is kept as Float32 ArrayBuffers per chapter; output Blobs are built
// from parts so a long book never needs one giant contiguous Float32Array.

const pad = (n) => String(n).padStart(3, "0");

export function groupChapters(chapters, chaptersPerFile, numFiles = null) {
  if (!chapters.length) return [];
  let per = chaptersPerFile || 0;
  if (numFiles !== null && numFiles !== "") {
    const count = Number(numFiles);
    if (!Number.isInteger(count) || count < 1) throw new Error("num_files must be greater than zero");
    per = Math.max(1, Math.ceil(chapters.length / count));
  }
  if (!per) return [chapters];
  const groups = [];
  for (let i = 0; i < chapters.length; i += per) groups.push(chapters.slice(i, i + per));
  return groups;
}

function safeName(value) {
  return String(value).replace(/[^A-Za-z0-9._ -]/g, "_").replace(/^\.+/, "") || "book";
}

export function outputName(settings, group, groups) {
  const per = settings.chaptersPerFile;
  const pattern = settings.namePattern || (per ? "{title} - {chapter}" : "{title} - all");
  const first = group[0].number;
  const last = group[group.length - 1].number;
  const chapter = per ? String(groups.indexOf(group) + 1) : "all";
  const range = per ? `${pad(first)}-${pad(last)}` : "all";
  const name = pattern
    .replaceAll("{title}", settings.title || "book")
    .replaceAll("{chapter}", chapter)
    .replaceAll("{range}", range);
  return `${safeName(name)}.wav`;
}

function wavHeader(totalSamples, rate) {
  const view = new DataView(new ArrayBuffer(44));
  const text = (at, value) => {
    for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i));
  };
  const bytes = totalSamples * 2;
  text(0, "RIFF");
  view.setUint32(4, 36 + bytes, true);
  text(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, bytes, true);
  return view.buffer;
}

function pcm16(samples) {
  const out = new ArrayBuffer(samples.length * 2);
  const view = new DataView(out);
  for (let i = 0; i < samples.length; i++) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(value * 32767), true);
  }
  return out;
}

export function outputPlans(project, chapters, groups, numFiles = null) {
  const withNumFiles = numFiles ? { ...project.settings, chaptersPerFile: 0 } : project.settings;
  if (numFiles) {
    const count = Number(numFiles);
    const per = Math.max(1, Math.ceil(chapters.length / count));
    withNumFiles.chaptersPerFile = per;
  }
  return groups.map((group, index) => ({
    id: `${project.id}-part${index + 1}`,
    projectId: project.id,
    name: outputName(withNumFiles, group, groups),
    first: group[0].number,
    last: group[group.length - 1].number,
    count: group.length,
    chapterNumbers: group.map((chapter) => chapter.number),
  }));
}

export function buildOutputBlob(output, chapters, settings) {
  const wanted = new Set(output.chapterNumbers);
  const chosen = chapters.filter((chapter) => wanted.has(chapter.number));
  const rate = settings.sampleRate;
  const gapSamples = Math.round((settings.chapterGapMs / 1000) * rate);
  const totalSamples =
    chosen.reduce((sum, chapter) => sum + (chapter.samples || 0), 0) +
    gapSamples * Math.max(0, chosen.length - 1);
  const parts = [wavHeader(totalSamples, rate)];
  const silence = gapSamples ? new Int16Array(gapSamples).buffer : null;
  chosen.forEach((chapter, index) => {
    if (index && silence) parts.push(silence);
    if (chapter.audio) parts.push(pcm16(new Float32Array(chapter.audio)));
  });
  return new Blob(parts, { type: "audio/wav" });
}
