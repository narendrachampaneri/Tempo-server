// Preview HTML, SVG and Mermaid answers in a sandboxed frame, with a download button.
// The frame has an opaque origin (no access to this page, its storage or the API), and a
// content-security-policy that blocks every network request, so a preview can't phone home.
import { el, icon, loadScript, download, toast } from "./util.js";

const CSP = (scripts) =>
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; media-src data: blob:; font-src data:; style-src 'unsafe-inline'; ${scripts ? "script-src 'unsafe-inline'" : "script-src 'none'"}">`;

function htmlDoc(code) {
  const body = code.replace(/^\s*<!doctype[^>]*>/i, "");
  return `<!doctype html><meta charset="utf-8">${CSP(true)}<meta name="viewport" content="width=device-width,initial-scale=1">${body}`;
}
function svgDoc(svg) {
  return `<!doctype html><meta charset="utf-8">${CSP(false)}<style>html,body{margin:0;height:100%;background:#fff}body{display:grid;place-items:center;padding:12px;box-sizing:border-box}svg{max-width:100%;max-height:100%;height:auto}</style>${svg}`;
}

let mermaidReady;
async function mermaidToSvg(code) {
  mermaidReady ||= loadScript("/static/vendor/mermaid/mermaid.min.js").then(() => {
    globalThis.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", fontFamily: "Inter Variable, system-ui, sans-serif" });
  });
  await mermaidReady;
  const { svg } = await globalThis.mermaid.render("m" + Math.random().toString(36).slice(2), code);
  document.querySelectorAll('[id^="dm"], [id^="m"][data-mermaid-temp]').forEach(n => n.remove());
  return svg;
}

async function prepare(kind, code) {
  if (kind === "html") return { srcdoc: htmlDoc(code), sandbox: "allow-scripts", file: "preview.html", mime: "text/html", text: code };
  if (kind === "svg") return { srcdoc: svgDoc(code), sandbox: "", file: "image.svg", mime: "image/svg+xml", text: code };
  const svg = await mermaidToSvg(code);
  return { srcdoc: svgDoc(svg), sandbox: "", file: "diagram.svg", mime: "image/svg+xml", text: svg };
}

function frame(view) {
  return el("iframe", { title: "Preview of the answer (sandboxed)", sandbox: view.sandbox, srcdoc: view.srcdoc, referrerpolicy: "no-referrer", loading: "lazy" });
}

// Called when a code block's Preview button is pressed.
export async function togglePreview(block, button) {
  const open = block.querySelector(".preview");
  const label0 = block.dataset.lang || block.dataset.preview;
  if (open) { open.remove(); button.innerHTML = `${icon("play")}Preview`; button.setAttribute("aria-label", `Preview ${label0}`); return; }
  const kind = block.dataset.preview;
  const code = block.querySelector("code").textContent;
  button.disabled = true;
  let view;
  try { view = await prepare(kind, code); }
  catch (e) {
    button.disabled = false;
    toast(kind === "mermaid" ? "That diagram has a syntax error, so it can't be drawn. Check the code and try again." : "Could not preview this.");
    return;
  }
  button.disabled = false;
  button.innerHTML = `${icon("x")}Hide preview`;
  button.setAttribute("aria-label", "Hide preview");
  const label = { html: "HTML preview", svg: "SVG preview", mermaid: "Diagram preview" }[kind];
  const bar = el("div", { class: "preview-bar" },
    el("span", { class: "lang" }, label, " · sandboxed"),
    el("button", { type: "button", class: "code-btn", html: `${icon("download")}Download`, "aria-label": `Download ${view.file}`, onclick: () => download(view.file, view.text, view.mime) }),
    el("button", { type: "button", class: "code-btn", html: `${icon("external")}Expand`, "aria-label": "Open the preview larger", onclick: () => expand(view, label) }));
  block.append(el("div", { class: "preview" }, bar, frame(view)));
}

function expand(view, label) {
  const dialog = el("dialog", { class: "wide", "aria-label": label },
    el("div", { class: "preview-bar", style: "padding:10px 14px" }, el("span", { class: "lang" }, label),
      el("button", { type: "button", class: "code-btn", html: `${icon("download")}Download`, onclick: () => download(view.file, view.text, view.mime) }),
      el("button", { type: "button", class: "code-btn", html: `${icon("x")}Close`, onclick: () => dialog.close() })),
    frame(view));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}
