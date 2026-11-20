// Settings for the browser build. Mirrors book.toml, but lives in IndexedDB.

export const DEFAULT_CHAPTER_PATTERN =
  "^\\s*(?:#{1,3}\\s*)?(?:chapter|part|book)\\s+([^\\n]+?)\\s*$";

export function defaultSettings(title = "", author = "") {
  return {
    title,
    author,
    voice: "F2",
    speed: 1,
    pitchShift: 0,
    pitchRange: 1,
    seed: 0,
    chapterGapMs: 700,
    sampleRate: 24000,
    outputFormat: "wav",
    chaptersPerFile: 0, // 0 or "all" = one file
    namePattern: "",
    chapterPattern: DEFAULT_CHAPTER_PATTERN,
  };
}

export function parseChaptersPerFile(value) {
  if (value === null || value === undefined || value === "" || value === "all" || value === 0 || value === "0") {
    return 0;
  }
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1) {
    throw new Error("chapters per file must be 'all' or a positive integer");
  }
  return n;
}

const NUMBERS = {
  speed: [0.5, 3],
  pitchShift: [-5, 5],
  pitchRange: [0.1, 3],
  seed: [0, 1000000],
  chapterGapMs: [0, 60000],
  sampleRate: [8000, 96000],
};

export function normalizeSettings(raw = {}) {
  const settings = { ...defaultSettings(), ...raw };
  for (const [key, [min, max]] of Object.entries(NUMBERS)) {
    const value = Number(settings[key]);
    if (!Number.isFinite(value)) throw new Error(`${key} must be a number`);
    if (value < min || value > max) throw new Error(`${key} must be between ${min} and ${max}`);
    settings[key] = value;
  }
  if (!["F2", "M2"].includes(settings.voice)) throw new Error("voice must be F2 or M2");
  settings.title = String(settings.title || "").trim();
  settings.author = String(settings.author || "").trim();
  settings.chaptersPerFile = parseChaptersPerFile(settings.chaptersPerFile);
  settings.namePattern = String(settings.namePattern || "");
  settings.chapterPattern = String(settings.chapterPattern || DEFAULT_CHAPTER_PATTERN);
  try {
    new RegExp(settings.chapterPattern, "i");
  } catch (error) {
    throw new Error(`invalid chapter pattern: ${error.message}`);
  }
  return settings;
}

export function describePlan(settings, chapterCount) {
  const per = settings.chaptersPerFile;
  if (!per) return `one file with all ${chapterCount} chapter(s)`;
  const count = Math.ceil(chapterCount / per);
  return `${count} file(s), up to ${per} chapter(s) each`;
}
