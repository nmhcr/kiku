// IndexedDB state: projects, chapter audio and output plans.
// This replaces book.toml, manifest.json and the on-disk audio/ directory.

const DB_NAME = "kiku-web";
const DB_VERSION = 1;

let dbPromise = null;

function open() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("projects")) {
        db.createObjectStore("projects", { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains("chapters")) {
        const store = db.createObjectStore("chapters", { keyPath: ["projectId", "number"] });
        store.createIndex("byProject", "projectId");
      }
      if (!db.objectStoreNames.contains("outputs")) {
        const store = db.createObjectStore("outputs", { keyPath: "id" });
        store.createIndex("byProject", "projectId");
      }
      if (!db.objectStoreNames.contains("meta")) {
        db.createObjectStore("meta", { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return dbPromise;
}

function asPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run(stores, mode, work) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(stores, mode);
    let result;
    tx.oncomplete = () =>
      resolve(result && typeof result === "object" && "result" in result ? result.result : result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
    try {
      result = work(tx);
    } catch (error) {
      tx.abort();
      reject(error);
    }
  });
}

export async function createProject(project, chapters) {
  await run(["projects", "chapters"], "readwrite", (tx) => {
    tx.objectStore("projects").put(project);
    const store = tx.objectStore("chapters");
    for (const chapter of chapters) store.put(chapter);
  });
  await setMeta("lastProjectId", project.id);
  return project;
}

export function listProjects() {
  return run(["projects"], "readonly", (tx) => tx.objectStore("projects").getAll());
}

export function getProject(id) {
  return run(["projects"], "readonly", (tx) => tx.objectStore("projects").get(id));
}

export function putProject(project) {
  return run(["projects"], "readwrite", (tx) => tx.objectStore("projects").put(project));
}

export function getChapters(projectId) {
  return run(["chapters"], "readonly", (tx) =>
    tx.objectStore("chapters").index("byProject").getAll(projectId)
  ).then((list) => list.sort((a, b) => a.number - b.number));
}

export function putChapter(chapter) {
  return run(["chapters"], "readwrite", (tx) => tx.objectStore("chapters").put(chapter));
}

export function runReset(projectId, chapters) {
  return run(["chapters"], "readwrite", (tx) => {
    const store = tx.objectStore("chapters");
    const request = store.index("byProject").getAllKeys(IDBKeyRange.only(projectId));
    request.onsuccess = () => {
      for (const key of request.result) store.delete(key);
      for (const chapter of chapters) store.put(chapter);
    };
  });
}

export function resetChapter(projectId, number) {
  return run(["chapters"], "readwrite", (tx) => {
    const store = tx.objectStore("chapters");
    const key = [projectId, number];
    const request = store.get(key);
    request.onsuccess = () => {
      const chapter = request.result;
      if (!chapter) return;
      chapter.status = "pending";
      chapter.durationSeconds = null;
      chapter.samples = 0;
      delete chapter.audio;
      store.put(chapter);
    };
  });
}

export async function deleteProject(id) {
  await run(["projects", "chapters", "outputs"], "readwrite", (tx) => {
    tx.objectStore("projects").delete(id);
    const range = IDBKeyRange.only(id);
    tx.objectStore("chapters").index("byProject").delete(range);
    tx.objectStore("outputs").index("byProject").delete(range);
  });
}

export function getOutputs(projectId) {
  return run(["outputs"], "readonly", (tx) =>
    tx.objectStore("outputs").index("byProject").getAll(projectId)
  );
}

export async function setOutputs(projectId, outputs) {
  await run(["outputs"], "readwrite", (tx) => {
    const store = tx.objectStore("outputs");
    const range = IDBKeyRange.only(projectId);
    const request = store.index("byProject").getAllKeys(range);
    request.onsuccess = () => {
      for (const key of request.result) store.delete(key);
      for (const output of outputs) store.put(output);
    };
  });
}

export function getMeta(key) {
  return run(["meta"], "readonly", (tx) => tx.objectStore("meta").get(key))
    .then((row) => row && row.value);
}

export function setMeta(key, value) {
  return run(["meta"], "readwrite", (tx) => tx.objectStore("meta").put({ key, value }));
}
