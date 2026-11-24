// Main-thread interface to the synthesis worker.

const worker = new Worker(new URL("../engine/worker.js", import.meta.url), { type: "module" });

let readyPromise = null;
let statusHandler = () => {};
let errorHandler = () => {};
let engineLoaded = false;

export function onEngineStatus(handler) {
  statusHandler = handler;
}

export function onEngineError(handler) {
  errorHandler = handler;
}

worker.addEventListener("message", ({ data }) => {
  if (data.type === "error" && data.id == null && engineLoaded) {
    errorHandler(new Error(data.message));
    return;
  }
  if (data.type === "status") statusHandler(data.text, data.progress);
});

worker.addEventListener("error", (event) => {
  errorHandler(new Error(event.message || "worker error"));
});
worker.addEventListener("messageerror", () => {
  errorHandler(new Error("worker message could not be deserialized"));
});

export function engineReady() {
  if (!readyPromise) {
    readyPromise = new Promise((resolve, reject) => {
      const onMessage = ({ data }) => {
        if (data.type === "ready") {
          engineLoaded = true;
          worker.removeEventListener("message", onMessage);
          resolve(data);
        } else if (data.type === "error") {
          engineLoaded = false;
          worker.removeEventListener("message", onMessage);
          readyPromise = null;
          reject(new Error(data.message));
        }
      };
      worker.addEventListener("message", onMessage);
      worker.postMessage({ type: "load" });
    });
  }
  return readyPromise;
}

export function runJob({ id, chapters, settings, onProgress, onChapterAudio, onState }) {
  return engineReady().then(() => new Promise((resolve, reject) => {
    let writes = Promise.resolve();
    let finished = false;

    const cleanup = () => worker.removeEventListener("message", onMessage);
    const onMessage = ({ data }) => {
      if (data.id !== id) return;
      if (data.type === "jobState") {
        if (onState) onState(data.state);
      } else if (data.type === "progress") {
        if (onProgress) onProgress(data);
      } else if (data.type === "chapterAudio") {
        if (onChapterAudio) {
          writes = writes.then(() => onChapterAudio(data)).catch((error) => {
            cleanup();
            finished = true;
            reject(error);
          });
        }
      } else if (data.type === "done") {
        writes.then(() => {
          if (finished) return;
          finished = true;
          cleanup();
          resolve(data.result);
        });
      } else if (data.type === "error") {
        if (finished) return;
        finished = true;
        cleanup();
        reject(new Error(data.message));
      }
    };
    worker.addEventListener("message", onMessage);
    worker.postMessage({ type: "synthesize", id, chapters, settings });
  }));
}

export function preview(text, settings) {
  const id = `preview-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return engineReady().then(() => new Promise((resolve, reject) => {
    const onMessage = ({ data }) => {
      if (data.id !== id) return;
      worker.removeEventListener("message", onMessage);
      if (data.type === "preview") resolve(data.audio);
      else if (data.type === "error") reject(new Error(data.message));
    };
    worker.addEventListener("message", onMessage);
    worker.postMessage({ type: "preview", id, text, settings });
  }));
}

export const jobControl = {
  pause: () => worker.postMessage({ type: "pause" }),
  resume: () => worker.postMessage({ type: "resume" }),
  stop: () => worker.postMessage({ type: "stop" }),
};
