// Small helpers shared by every module: DOM, icons, storage, toasts, screen-reader announcements.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* storage unavailable */ } },
};

// Create an element: el("button", {class: "btn", onclick: fn}, "text", child...)
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const PATHS = {
  menu: "M4 7h16M4 12h16M4 17h16",
  plus: "M12 5v14M5 12h14",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  pin: "M12 17v5M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "M4 20h4L19 9l-4-4L4 16v4zM13 7l4 4",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  download: "M12 4v11M7 11l5 5 5-5M5 20h14",
  copy: "M9 9h10v11H9zM5 15V4h10",
  check: "M5 12l5 5 9-10",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  stop: "M7 7h10v10H7z",
  send: "M12 19V5M6 11l6-6 6 6",
  clip: "M20 11l-8.5 8.5a5 5 0 0 1-7-7L13 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L14 7",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  speaker: "M4 9v6h4l5 4V5L8 9H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19 12l2-1-1-3-2 .5-1.5-1.5.5-2-3-1-1 2h-2l-1-2-3 1 .5 2L6 8.5 4 8l-1 3 2 1v0l-2 1 1 3 2-.5L7.5 17l-.5 2 3 1 1-2h2l1 2 3-1-.5-2 1.5-1.5 2 .5 1-3z",
  lock: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
  x: "M6 6l12 12M18 6L6 18",
  chevron: "M6 9l6 6 6-6",
  keyboard: "M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10",
  bolt: "M13 3L5 14h6l-1 7 8-11h-6l1-7z",
  spark: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6z",
  auto: "M4 12h4l3-7 3 14 3-7h3",
  file: "M6 3h8l5 5v13H6zM14 3v5h5",
  play: "M8 5l11 7-11 7V5z",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  models: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  key: "M15 8a4 4 0 1 1-2.8 6.8L4 21v-3l2-1v-2l2-1 2.2-2.2A4 4 0 0 1 15 8z",
  code: "M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14",
  history: "M4 12a8 8 0 1 0 2.3-5.7M4 4v5h5M12 8v4l3 2",
  thumbUp: "M7 11v9H4v-9h3zM7 11l4-8c1.5 0 2.5 1 2.2 2.6L12.6 9H19a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.8 20H7",
  thumbDown: "M17 13V4h3v9h-3zM17 13l-4 8c-1.5 0-2.5-1-2.2-2.6l.6-3.4H5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6.2 4H17",
};

export function icon(name, extra = "") {
  return `<svg class="i ${extra}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${PATHS[name] || ""}"/></svg>`;
}
export const iconEl = (name, extra) => { const t = document.createElement("template"); t.innerHTML = icon(name, extra); return t.content.firstChild; };

export const BRAND_PATH = "M4.5 6.5h15M12 6.5V19";

// Polite live region for screen readers (used for "thinking", "answer ready", errors).
let liveRegion;
export function announce(message) {
  liveRegion ||= $("#live");
  if (!liveRegion) return;
  liveRegion.textContent = "";
  setTimeout(() => { liveRegion.textContent = message; }, 40);
}

export function toast(message, ms = 2400) {
  const host = $("#toasts");
  const node = el("div", { class: "toast", role: "status" }, message);
  host.append(node);
  setTimeout(() => node.remove(), ms);
}

export function uid() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

export const compact = n => n >= 10000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "K" : Number(n).toLocaleString();

export function download(name, text, type = "text/plain") {
  const blob = new Blob([text], { type: `${type};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {
    const area = el("textarea", { style: "position:fixed;opacity:0" }); area.value = text;
    document.body.append(area); area.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { /* not allowed */ }
    area.remove(); return ok;
  }
}

// Briefly show a confirmation on a button (e.g. "Copy" -> check mark).
export function flash(button, html = icon("check")) {
  const before = button.innerHTML;
  button.classList.add("flash"); button.innerHTML = html;
  setTimeout(() => { button.classList.remove("flash"); button.innerHTML = before; }, 1200);
}

const scripts = new Map();
export function loadScript(src) {
  if (!scripts.has(src)) {
    scripts.set(src, new Promise((resolve, reject) => {
      const s = el("script", { src });
      s.onload = resolve; s.onerror = () => { scripts.delete(src); reject(new Error("Could not load " + src)); };
      document.head.append(s);
    }));
  }
  return scripts.get(src);
}
export function loadStyle(href) {
  if ($(`link[href="${href}"]`)) return;
  document.head.append(el("link", { rel: "stylesheet", href }));
}

export function debounce(fn, ms) {
  let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}
