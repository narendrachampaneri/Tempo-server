// The message box: attachments (files, images; drag and drop, paste), mode picker, settings,
// voice input, and the Send / Stop button.
import { $, $$, el, icon, iconEl, store, toast, announce } from "./util.js";
import { api, errorFrom, friendly, json } from "./api.js";

const MAX_TEXT_BYTES = 200 * 1024, MAX_FILES = 5, MAX_IMAGES = 4, IMAGE_EDGE = 1280;
const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|jsonl|xml|html?|css|js|mjs|ts|tsx|jsx|py|java|c|h|cpp|hpp|cs|go|rs|rb|php|sh|bash|zsh|ps1|sql|yaml|yml|toml|ini|cfg|conf|log|tex|swift|kt|r|lua|pl|dart)$/i;
const IMAGE_TYPES = /^image\/(png|jpe?g|webp|gif)$/;

const MODE_INFO = {
  auto: ["auto", "Auto", "Balanced quality, speed and free quota"],
  fast: ["bolt", "Fast", "Prefer the fastest capable model"],
  best: ["spark", "Best", "Prefer the strongest model, even if its free quota is small"],
  private: ["lock", "Private", "Only local models (Ollama), and nothing from this chat is saved"],
};

export function createComposer({ onSend, onStop, caps }) {
  const form = $("#composer"), input = $("#input"), send = $("#send");
  const attsBox = $("#composer-atts"), noteBox = $("#composer-note");
  let mode = store.get("tempo_mode") in MODE_INFO ? store.get("tempo_mode") : "auto";
  let attachments = [], busy = false, model = "";

  // ---- modes ---------------------------------------------------------------------------------
  const modes = $("#modes");
  for (const [key, [ic, label, tip]] of Object.entries(MODE_INFO)) {
    modes.append(el("button", { type: "button", dataset: { mode: key }, title: tip, "aria-pressed": "false", html: `${icon(ic)}<span>${label}</span>` }));
  }
  function setMode(next) {
    mode = next; store.set("tempo_mode", mode);
    $$("button", modes).forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    renderNote();
    api_.onMode?.(mode);
  }
  modes.addEventListener("click", e => { const b = e.target.closest("button[data-mode]"); if (b) setMode(b.dataset.mode); });

  function renderNote() {
    noteBox.replaceChildren();
    noteBox.hidden = true;
    noteBox.className = "composer-note";
    if (mode === "private") {
      noteBox.className = "composer-note private"; noteBox.hidden = false;
      noteBox.append(iconEl("lock"), "Private: only local models answer, and nothing from this chat is saved or logged.");
    } else if (model) {
      noteBox.hidden = false; noteBox.append(iconEl("models"), `Using ${model} for the first draft. Tempo may still check with other models.`);
    }
  }

  // ---- text box -------------------------------------------------------------------------------
  const autosize = () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 220) + "px"; };
  const refresh = () => { if (!busy) send.disabled = !(input.value.trim() || attachments.length); };
  input.addEventListener("input", () => { autosize(); refresh(); });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", e => {
    e.preventDefault();
    if (busy) { onStop(); return; }
    const text = input.value.trim();
    if (!text && !attachments.length) return;
    const payload = { text, attachments: attachments.slice(), mode, model, settings: stageSettings() };
    input.value = ""; attachments = []; renderAtts(); autosize(); refresh();
    onSend(payload);
  });

  function setBusy(value) {
    busy = value;
    send.classList.toggle("stop", busy);
    send.innerHTML = icon(busy ? "stop" : "send");
    send.setAttribute("aria-label", busy ? "Stop generating" : "Send message");
    send.title = busy ? "Stop (Esc)" : "Send (Enter)";
    send.disabled = busy ? false : !(input.value.trim() || attachments.length);
  }
  setBusy(false);

  // ---- attachments ----------------------------------------------------------------------------
  function renderAtts() {
    attsBox.replaceChildren(...attachments.map((a, i) => el("div", { class: "att" },
      a.kind === "image" ? el("img", { src: a.dataUrl, alt: "" }) : iconEl("file"),
      el("span", { class: "n", title: a.name }, a.name),
      el("button", { type: "button", class: "icon-btn x", "aria-label": `Remove ${a.name}`, html: icon("x"),
        onclick: () => { attachments.splice(i, 1); renderAtts(); refresh(); input.focus(); } }))));
    attsBox.hidden = !attachments.length;
  }

  async function shrinkImage(file) {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale); canvas.height = Math.round(bitmap.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.85);
  }

  async function addFiles(files) {
    for (const file of files) {
      if (IMAGE_TYPES.test(file.type)) {
        if (!caps().vision) { toast("Images need a vision model, and none is ready. Add a key for one (see Models), or attach text instead."); continue; }
        if (attachments.filter(a => a.kind === "image").length >= MAX_IMAGES) { toast(`You can attach up to ${MAX_IMAGES} images.`); continue; }
        try { attachments.push({ kind: "image", name: file.name || "pasted-image.jpg", type: "image/jpeg", size: file.size, dataUrl: await shrinkImage(file) }); }
        catch { toast(`Could not read ${file.name || "that image"}.`); }
      } else if (file.type.startsWith("text/") || TEXT_EXT.test(file.name) || /json|xml|javascript/.test(file.type)) {
        if (file.size > MAX_TEXT_BYTES) { toast(`${file.name} is over 200 KB. Attach a smaller part of it.`); continue; }
        if (attachments.filter(a => a.kind === "text").length >= MAX_FILES) { toast(`You can attach up to ${MAX_FILES} files.`); continue; }
        attachments.push({ kind: "text", name: file.name, type: file.type || "text/plain", size: file.size, text: await file.text() });
      } else if (/pdf$/i.test(file.type) || /\.pdf$/i.test(file.name)) {
        toast("PDF files can't be read yet. Copy the text out of it, or attach it as a .txt file.");
      } else {
        toast(`${file.name || "That file"} isn't a type Tempo can read. Text, code and images work.`);
      }
    }
    renderAtts(); refresh(); input.focus();
  }

  const picker = el("input", { type: "file", multiple: true, hidden: true, "aria-hidden": "true", tabindex: "-1" });
  form.append(picker);
  picker.addEventListener("change", () => { addFiles(Array.from(picker.files)); picker.value = ""; });
  $("#attach").addEventListener("click", () => picker.click());

  input.addEventListener("paste", e => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  const stage = $("#chat-view");
  let depth = 0;
  const zone = el("div", { class: "dropzone" }, iconEl("clip"), " Drop files or images to attach");
  zone.hidden = true; stage.append(zone);
  const hasFiles = e => Array.from(e.dataTransfer?.types || []).includes("Files");
  stage.addEventListener("dragenter", e => { if (hasFiles(e)) { depth++; zone.hidden = false; } });
  stage.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) zone.hidden = true; });
  stage.addEventListener("dragover", e => { if (hasFiles(e)) e.preventDefault(); });
  stage.addEventListener("drop", e => {
    if (!hasFiles(e)) return;
    e.preventDefault(); depth = 0; zone.hidden = true; addFiles(Array.from(e.dataTransfer.files));
  });

  // ---- settings (stages, time budget, model) --------------------------------------------------------
  const pop = $("#settings-pop"), gear = $("#gear");
  const fields = { max_stages: $("#set-stages"), time_budget_s: $("#set-time"), quota_budget: $("#set-quota") };
  for (const [key, field] of Object.entries(fields)) {
    field.value = store.get("tempo_" + key) || "";
    field.addEventListener("change", () => store.set("tempo_" + key, field.value));
  }
  const modelSelect = $("#set-model");
  model = store.get("tempo_model") || "";
  modelSelect.addEventListener("change", () => { model = modelSelect.value; store.set("tempo_model", model); renderNote(); });
  async function loadModelChoices() {
    try {
      const data = await json("/api/models");
      const ready = data.models.filter(m => m.status === "ready" && ["chat", "code", "vision"].includes(m.type ?? "chat")).sort((a, b) => b.strength - a.strength);
      modelSelect.replaceChildren(el("option", { value: "" }, "Auto: Tempo chooses"),
        ...ready.map(m => el("option", { value: m.id }, `${m.name || m.id} · ${m.provider}`)));
      if (model && !ready.some(m => m.id === model)) { model = ""; store.set("tempo_model", ""); }
      modelSelect.value = model; renderNote();
    } catch { /* the Models page explains connection problems */ }
  }
  function togglePop(open) {
    pop.hidden = !(open ?? pop.hidden);
    gear.setAttribute("aria-expanded", String(!pop.hidden));
    if (!pop.hidden) { loadModelChoices(); fields.max_stages.focus(); }
  }
  gear.addEventListener("click", () => togglePop());
  $("#set-reset").addEventListener("click", () => {
    for (const [key, field] of Object.entries(fields)) { field.value = ""; store.set("tempo_" + key, ""); }
    modelSelect.value = ""; model = ""; store.set("tempo_model", ""); renderNote();
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !pop.hidden) { togglePop(false); gear.focus(); } });
  document.addEventListener("pointerdown", e => { if (!pop.hidden && !pop.contains(e.target) && !gear.contains(e.target)) togglePop(false); });
  function stageSettings() {
    const out = {};
    for (const [key, field] of Object.entries(fields)) {
      const n = Number(field.value);
      if (field.value !== "" && Number.isFinite(n) && n > 0) out[key] = n;
    }
    return out;
  }

  // ---- voice input (shown only when a speech model is ready) -----------------------------------------
  const mic = $("#mic");
  let recorder = null, chunks = [], recTimer = null;
  async function toggleRecording() {
    if (recorder) { recorder.stop(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch { toast("Tempo can't use the microphone. Allow it in your browser's address bar, then try again."); return; }
    chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      clearTimeout(recTimer);
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      recorder = null; mic.classList.remove("rec"); mic.setAttribute("aria-pressed", "false");
      if (blob.size < 800) { toast("That was too short. Hold the microphone button a little longer."); return; }
      mic.disabled = true; announce("Turning your speech into text");
      try {
        const res = await api("/api/speech/transcribe", { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
        if (!res.ok) throw await errorFrom(res);
        const { text } = await res.json();
        if (text) { input.value = (input.value ? input.value + " " : "") + text; autosize(); refresh(); input.focus(); }
        else toast("Tempo didn't catch any words. Try again a bit closer to the microphone.");
      } catch (e) { toast(friendly(e).detail + " " + friendly(e).next, 5000); }
      mic.disabled = false;
    };
    recorder.start();
    mic.classList.add("rec"); mic.setAttribute("aria-pressed", "true"); announce("Recording. Press the microphone again to stop.");
    recTimer = setTimeout(() => recorder?.stop(), 60000);
  }
  mic.addEventListener("click", toggleRecording);

  const api_ = {
    onMode: null,
    get mode() { return mode; },
    focus: () => input.focus(),
    current: () => ({ mode, model, settings: stageSettings() }),
    setBusy, setMode,
    setText(text) { input.value = text; autosize(); refresh(); },
    togglePop,
    showVoice(on) { mic.hidden = !on || !navigator.mediaDevices || typeof MediaRecorder === "undefined"; },
    showAttach(on) { $("#attach").hidden = !on; },
    init() { setMode(mode); renderAtts(); autosize(); },
  };
  return api_;
}
