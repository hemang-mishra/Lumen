/*
 * The little that all three pages share.
 *
 * No framework and no build step, on purpose: these pages exist to make the
 * pipeline visible while it is being built, and a toolchain would outlive
 * its usefulness by years.
 *
 * One rule worth stating. Everything from the server is inserted as text,
 * never as markup. The strings on these pages are somebody's journal, and a
 * page that renders them as HTML would execute whatever an export happened
 * to contain.
 */

/** Ask the API for something, with a readable error when it says no. */
async function api(path, options) {
  const response = await fetch(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail = (body && (body.detail || body.error)) || response.statusText;
    throw new Error(detail);
  }
  return body;
}

/** Build an element. Children are always text, never markup. */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Replace everything inside an element. */
function fill(target, ...children) {
  const node = typeof target === "string" ? document.getElementById(target) : target;
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

/** A coloured, worded badge for a status. Colour is never the only signal. */
function tag(status) {
  const shade = {
    COMPLETE: "ok",
    RUNNING: "running",
    QUEUED: "queued",
    PENDING: "queued",
    FAILED: "bad",
    CANCELLED: "bad",
    DUPLICATE: "dup",
  }[status] || "queued";
  return el("span", { class: `tag ${shade}` }, status);
}

/** A timestamp as something a person reads, or a dash when there is none. */
function when(iso) {
  if (!iso) return "—";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

/** A duration in milliseconds, rounded to something readable. */
function howLong(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

/** Anything at all, printed as readable JSON. */
function pretty(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

/** A collapsed block holding something long. */
function foldout(label, value) {
  const text = pretty(value);
  if (!text) return null;
  return el("details", {}, el("summary", {}, label), el("pre", {}, text));
}

/** Put a message at the top of a page. */
function say(kind, message) {
  return el("div", { class: `notice ${kind}` }, message);
}

/** Mark the current page in the navigation. */
function markNav() {
  const here = location.pathname.split("/").pop() || "index.html";
  for (const link of document.querySelectorAll("header nav a")) {
    if (link.getAttribute("href") === here) link.classList.add("on");
  }
}

document.addEventListener("DOMContentLoaded", markNav);
