// Source reading and chapter splitting. Mirrors book.py and epub.py.
// EPUB unzipping uses a vendored copy of JSZip (window.JSZip).

import { DEFAULT_CHAPTER_PATTERN } from "./settings.js";

const SKIP_TAGS = new Set(["script", "style", "head", "nav", "svg"]);
const BLOCK_TAGS = new Set([
  "p", "div", "section", "article", "blockquote", "br", "hr", "li", "ul",
  "ol", "tr", "table", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
]);
const HEADING_TAGS = new Set(["h1", "h2", "h3"]);
const TEXT_TYPES = new Set([
  "application/xhtml+xml", "text/html", "application/xml", "text/xml",
]);

function clean(text) {
  return text
    .replace(/\u00a0/g, " ")
    .replace(/[ \t\r\f\v]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function localName(element) {
  return (element.localName || element.tagName || "").split(":").pop().toLowerCase();
}

function byLocal(root, name) {
  return [...root.getElementsByTagName("*")].filter((el) => localName(el) === name);
}

function posixJoin(directory, name) {
  const parts = (directory ? directory.split("/") : []).concat(name.split("/"));
  const out = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") out.pop();
    else out.push(part);
  }
  return out.join("/");
}

function decode(data) {
  return new TextDecoder("utf-8", { fatal: false }).decode(data);
}

function htmlToText(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const parts = [];
  const headings = [];

  function walk(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      parts.push(node.data);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const tag = node.localName.toLowerCase();
    if (SKIP_TAGS.has(tag)) return;
    if (BLOCK_TAGS.has(tag)) parts.push("\n");

    let heading = null;
    if (HEADING_TAGS.has(tag)) heading = [];
    for (const child of node.childNodes) {
      walk(child);
      if (heading) heading.push(child.textContent || "");
    }
    if (heading) {
      const text = clean(heading.join(" "));
      if (text) headings.push(text);
    }
    if (BLOCK_TAGS.has(tag)) parts.push("\n");
  }

  walk(doc.body || doc.documentElement);
  return { text: clean(parts.join("")), headings };
}

async function readEpub(file) {
  if (!window.JSZip) throw new Error("EPUB support is not loaded (JSZip is missing)");
  let zip;
  try {
    zip = await window.JSZip.loadAsync(file);
  } catch (error) {
    throw new Error(`not a valid EPUB (ZIP) file: ${error.message}`);
  }
  const has = (name) => Object.prototype.hasOwnProperty.call(zip.files, name);
  const textOf = async (name) => decode(await zip.file(name).async("uint8array"));

  if (!has("META-INF/container.xml")) throw new Error("EPUB is missing META-INF/container.xml");
  const container = new DOMParser().parseFromString(
    await textOf("META-INF/container.xml"), "application/xml"
  );
  const rootfiles = byLocal(container, "rootfile");
  const opfPath = rootfiles.length && rootfiles[0].getAttribute("full-path");
  if (!opfPath) throw new Error("EPUB container.xml has no rootfile");
  if (!has(opfPath)) throw new Error(`EPUB is missing its OPF file: ${opfPath}`);

  const opf = new DOMParser().parseFromString(await textOf(opfPath), "application/xml");
  let title = "";
  let author = "";
  for (const el of byLocal(opf, "metadata")[0]?.getElementsByTagName("*") || []) {
    const value = clean(el.textContent || "");
    if (!value) continue;
    if (localName(el) === "title") title = [title, value].filter(Boolean).join(" ");
    if (localName(el) === "creator") author = [author, value].filter(Boolean).join(", ");
  }

  const manifest = new Map();
  for (const item of byLocal(opf, "item")) {
    const id = item.getAttribute("id");
    const href = item.getAttribute("href");
    if (id && href) {
      manifest.set(id, {
        name: posixJoin(opfPath.split("/").slice(0, -1).join("/"), decodeURIComponent(href)),
        type: (item.getAttribute("media-type") || "").toLowerCase(),
      });
    }
  }

  const documents = [];
  const spine = byLocal(opf, "spine")[0];
  if (spine) {
    for (const ref of byLocal(spine, "itemref")) {
      if (ref.getAttribute("linear") === "no") continue;
      const entry = manifest.get(ref.getAttribute("idref") || "");
      if (!entry || !has(entry.name)) continue;
      if (entry.type && ![...TEXT_TYPES].some((t) => entry.type.startsWith(t))) continue;
      if (!/\.(xhtml|html|htm)$/i.test(entry.name) && !TEXT_TYPES.has(entry.type)) continue;
      const { text, headings } = htmlToText(await textOf(entry.name));
      if (!/[\p{L}\p{N}]/u.test(text)) continue;
      documents.push({
        title: headings[0] || entry.name.split("/").pop().replace(/\.[^.]+$/, ""),
        text,
      });
    }
  }
  if (!documents.length) throw new Error("EPUB has no readable text documents");
  return {
    text: documents.map((doc) => doc.text).join("\n\n"),
    title: clean(title),
    author: clean(author),
    documents,
  };
}

export async function readSource(file) {
  const name = file.name || "book.txt";
  const extension = name.split(".").pop().toLowerCase();
  if (extension === "epub") return readEpub(file);
  if (!["txt", "md", "text", "markdown"].includes(extension)) {
    throw new Error("supported source files: .txt, .md, .epub");
  }
  let text = await file.text();
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  return {
    text,
    title: name.replace(/\.[^.]+$/, ""),
    author: "",
    documents: [],
  };
}

export function splitChapters(text, pattern = DEFAULT_CHAPTER_PATTERN, documents = []) {
  let regex;
  try {
    regex = new RegExp(pattern, "gim");
  } catch (error) {
    throw new Error(`invalid chapter pattern: ${error.message}`);
  }
  const matches = [...text.matchAll(regex)];
  const chapters = [];
  const add = (title, body) => {
    chapters.push({ number: chapters.length + 1, title: title || `Chapter ${chapters.length + 1}`, text: body });
  };

  if (!matches.length) {
    const body = text.trim();
    if (body) add("Full text", body);
  } else {
    const preamble = text.slice(0, matches[0].index).trim();
    if (preamble) add("Prelude", preamble);
    matches.forEach((match, index) => {
      const title = (match[1] || match[0]).trim();
      const end = index + 1 < matches.length ? matches[index + 1].index : text.length;
      const body = text.slice(match.index + match[0].length, end).trim();
      if (!/[\p{L}\p{N}]/u.test(body)) return;
      add(title || `Chapter ${index + 1}`, body);
    });
  }

  // Like epub.py: when no headings were found, use the EPUB's own documents.
  if (chapters.length === 1 && chapters[0].title === "Full text" && documents.length > 1) {
    chapters.length = 0;
    for (const doc of documents) {
      if (!/[\p{L}\p{N}]/u.test(doc.text)) continue;
      add(doc.title || `Part ${chapters.length + 1}`, doc.text);
    }
  }
  return chapters;
}
