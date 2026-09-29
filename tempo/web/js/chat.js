// One conversation: rendering, streaming an answer, stop, regenerate, edit and resend,
// saving to history (never for Private turns), and opening an old chat exactly as it was.
import { $, el, icon, iconEl, uid, toast, announce, copyText, flash, reducedMotion, BRAND_PATH } from "./util.js";
import { api, errorFrom, friendly, json, put } from "./api.js";
import { renderMarkdown, typesetMath } from "./markdown.js";
import { Timeline, keepForHistory } from "./timeline.js";
import { togglePreview } from "./preview.js";
import { speak, stopSpeaking } from "./voice.js";

const thread = $("#thread"), scroller = $("#scroller"), empty = $("#empty"), jump = $("#jump");
const avatarSvg = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${BRAND_PATH}"/></svg>`;

let composer, hooks = { caps: () => ({}), onChange() {}, go() {}, askKey: async () => null };
let chat = blank();
let controller = null, busy = false, stuck = true;

function blank() { return { id: uid(), title: "", messages: [], created_at: Date.now() / 1000, pinned: false }; }
export const current = () => chat;
export const isBusy = () => busy;

const titleFrom = text => { const t = text.replace(/\s+/g, " ").trim(); return t.length > 48 ? t.slice(0, 47).trimEnd() + "…" : t || "New chat"; };
const slim = ev => { const { results, quota, outputs, ...rest } = ev; return rest; };

export function init(options) {
  hooks = { ...hooks, ...options };
  composer = options.composer;
  scroller.addEventListener("scroll", () => {
    stuck = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60;
    jump.hidden = stuck || !busy;
  }, { passive: true });
  jump.addEventListener("click", () => { stuck = true; scrollDown(); jump.hidden = true; });
  thread.addEventListener("click", onThreadClick);
  $("#suggestions").addEventListener("click", e => {
    const b = e.target.closest("button[data-q]"); if (b) send({ text: b.dataset.q, attachments: [], ...composer.current() });
  });
  renderAll();
}

function scrollDown() { scroller.scrollTop = scroller.scrollHeight; }
function follow() { if (stuck) scrollDown(); }

// ---- rendering -------------------------------------------------------------------------------
function renderAll() {
  thread.classList.add("no-anim");
  thread.replaceChildren();
  chat.messages.forEach((msg, i) => thread.append(msg.role === "user" ? userEl(msg, i) : botEl(msg, i).root));
  empty.hidden = chat.messages.length > 0;
  $("#chat-title").textContent = chat.title || "New chat";
  stuck = true; scrollDown();
  requestAnimationFrame(() => requestAnimationFrame(() => thread.classList.remove("no-anim")));
}

function attsView(msg) {
  if (!msg.attachments?.length) return null;
  return el("div", { class: "atts" }, msg.attachments.map(a => el("div", { class: "att" },
    a.kind === "image" ? el("img", { src: a.dataUrl, alt: a.name }) : iconEl("file"), el("span", { class: "n", title: a.name }, a.name))));
}

function userEl(msg, index) {
  const tools = el("div", { class: "msg-tools" },
    el("button", { type: "button", class: "icon-btn", title: "Copy", "aria-label": "Copy your message", html: icon("copy"),
      onclick: e => { copyText(msg.content); flash(e.currentTarget); } }),
    el("button", { type: "button", class: "icon-btn", title: "Edit and resend", "aria-label": "Edit and resend this message", html: icon("edit"),
      onclick: () => editUser(wrap, msg, index) }));
  const wrap = el("div", { class: "msg-user", dataset: { i: index } }, attsView(msg),
    msg.content ? el("div", { class: "bubble" }, msg.content) : null, tools);
  return wrap;
}

function editUser(wrap, msg, index) {
  if (busy) { toast("Stop the current answer first."); return; }
  const area = el("textarea", { "aria-label": "Edit your message", rows: 3 }); area.value = msg.content;
  const box = el("div", { class: "edit-box" }, area, el("div", { class: "row" },
    el("button", { type: "button", class: "btn small", onclick: () => renderAll() }, "Cancel"),
    el("button", { type: "button", class: "btn small primary", onclick: () => {
      const text = area.value.trim(); if (!text && !msg.attachments?.length) return;
      editAndResend(index, text);
    } }, "Save and resend")));
  wrap.classList.add("editing");
  wrap.replaceChildren(box);
  area.focus(); area.setSelectionRange(area.value.length, area.value.length);
  area.addEventListener("keydown", e => { if (e.key === "Escape") renderAll(); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) box.querySelector(".primary").click(); });
}

function botEl(msg, index, live = false) {
  const thinking = el("div", { class: "thinking" });
  const answer = el("div", { class: "answer" });
  const tools = el("div", { class: "bot-tools" });
  const root = el("div", { class: "msg-bot" + (live ? " working" : ""), role: "article", "aria-label": "Tempo's answer", dataset: { i: index } },
    el("div", { class: "avatar", html: avatarSvg }), el("div", { class: "bot-body" }, thinking, answer, tools));
  const view = { root, thinking, answer, tools, timeline: null, msg };
  if (live || msg.events?.length) {
    view.timeline = new Timeline(thinking);
    if (!live) view.timeline.replay(msg.events || []);
  } else thinking.hidden = true;
  if (!live) finishBot(view, index);
  return view;
}

function paintAnswer(view, streaming) {
  view.answer.innerHTML = renderMarkdown(view.msg.content) + (streaming ? '<span class="caret" aria-hidden="true"></span>' : "");
  if (!streaming) typesetMath(view.answer);
}

function errorBox(err) {
  const f = friendly(err);
  const actions = f.actions.map(a => el("button", { type: "button", class: "btn small" + (a.go === "retry" || a.go === "apikey" ? " primary" : ""), onclick: async () => {
    if (a.go === "retry") regenerate();
    else if (a.go === "apikey") { const k = await hooks.askKey(); if (k) { localStorage.setItem("tempo_api_key", k.trim()); regenerate(); } }
    else hooks.go(a.go);
  } }, a.label));
  return el("div", { class: "errbox", role: "alert" }, el("b", {}, f.title), el("div", {}, f.detail === f.title ? "" : f.detail),
    el("div", { class: "next" }, f.next), actions.length ? el("div", { class: "row" }, actions) : null);
}

function finishBot(view, index) {
  const { msg, answer, tools, root } = view;
  root.classList.remove("working");
  tools.replaceChildren();
  if (msg.content) paintAnswer(view, false);
  else answer.replaceChildren();
  if (msg.error && !msg.content) answer.replaceChildren(errorBox(msg.error));
  else if (msg.stopped && !msg.content) answer.replaceChildren(el("div", { class: "stopped" }, "Stopped before an answer came back."));
  else if (msg.stopped) answer.append(el("div", { class: "stopped" }, "Stopped. The answer above is partial."));
  const last = index === chat.messages.length - 1;
  const btn = (label, name, fn, extra = "") => el("button", { type: "button", class: "icon-btn " + extra, title: label, "aria-label": label, html: icon(name), onclick: fn });
  if (msg.model) tools.append(el("span", { class: "badge", title: "The model that wrote this answer" }, msg.model));
  const meta = [msg.stages ? `${msg.stages} stage${msg.stages === 1 ? "" : "s"}` : null, msg.score != null ? `score ${Number(msg.score).toFixed(2)}` : null,
    msg.private ? "private" : null].filter(Boolean).join(" · ");
  if (meta) tools.append(el("span", {}, meta));
  if (msg.content) {
    tools.append(btn("Copy answer", "copy", e => { copyText(msg.content); flash(e.currentTarget); }));
    if (hooks.caps().speech?.say) {
      const speaker = btn("Read aloud", "speaker", () => {
        if (speaker.classList.contains("on")) { stopSpeaking(); return; }
        speaker.classList.add("on"); speaker.setAttribute("aria-label", "Stop reading");
        speak(msg.content, () => { speaker.classList.remove("on"); speaker.setAttribute("aria-label", "Read aloud"); });
      });
      tools.append(speaker);
    }
  }
  if (last && !busy) tools.append(btn("Regenerate", "refresh", () => regenerate()));
  if (msg.question_id && !msg.private && msg.content) {
    for (const [rating, name, label] of [[1, "thumbUp", "Good answer"], [-1, "thumbDown", "Bad answer"]]) {
      const b = btn(label, name, async () => {
        try {
          await json("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question_id: msg.question_id, rating }) });
          msg.feedback = rating; tools.querySelectorAll("[data-rating]").forEach(x => x.classList.toggle("on", Number(x.dataset.rating) === rating));
          b.setAttribute("aria-pressed", "true"); persist(); toast("Thanks. This helps Tempo pick better models.");
        } catch (e) { toast(friendly(e).detail); }
      }, msg.feedback === rating ? "on" : "");
      b.dataset.rating = rating; b.setAttribute("aria-pressed", String(msg.feedback === rating)); tools.append(b);
    }
  }
}

function onThreadClick(e) {
  const b = e.target.closest(".code-btn[data-act]");
  if (!b) return;
  const block = b.closest(".codeblock");
  if (b.dataset.act === "copy") { copyText(block.querySelector("code").textContent); flash(b, `${icon("check")}Copied`); setTimeout(() => { b.innerHTML = `${icon("copy")}Copy`; }, 1300); }
  if (b.dataset.act === "preview") togglePreview(block, b);
}

// ---- sending -------------------------------------------------------------------------------------
function textFor(msg) {
  const files = (msg.attachments || []).filter(a => a.kind === "text");
  return [msg.content, ...files.map(f => `\n\n[Attached file: ${f.name}]\n${f.text}\n[End of ${f.name}]`)].join("");
}
function apiMessages() {
  const out = [];
  for (const m of chat.messages) {
    if (m.role === "assistant") { if (m.content) out.push({ role: "assistant", content: m.content }); continue; }
    const images = (m.attachments || []).filter(a => a.kind === "image");
    const text = textFor(m) || "Please look at the attached image.";
    out.push({ role: "user", content: images.length ? [{ type: "text", text }, ...images.map(a => ({ type: "image_url", image_url: { url: a.dataUrl } }))] : text });
  }
  return out;
}

export async function send({ text, attachments, mode, model, settings }) {
  if (busy) return;
  const msg = { id: uid(), role: "user", content: text, attachments: attachments.length ? attachments : undefined, private: mode === "private" };
  chat.messages.push(msg);
  if (!chat.title) chat.title = titleFrom(text || attachments[0]?.name || "");
  empty.hidden = true;
  thread.append(userEl(msg, chat.messages.length - 1));
  $("#chat-title").textContent = chat.title;
  stuck = true;
  await runTurn({ mode, model, settings });
}

async function runTurn({ mode, model, settings }) {
  const priv = mode === "private";
  thread.querySelectorAll('.bot-tools [aria-label="Regenerate"]').forEach(b => b.remove());  // only the newest answer can be regenerated
  const msg = { id: uid(), role: "assistant", content: "", events: [], private: priv };
  chat.messages.push(msg);
  const index = chat.messages.length - 1;
  const view = botEl(msg, index, true);
  thread.append(view.root);
  view.timeline.start();
  busy = true; composer.setBusy(true); controller = new AbortController();
  announce("Tempo is thinking.");
  scrollDown();
  let frame = 0, failure = null;
  const schedule = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; paintAnswer(view, true); follow(); }); };

  const apply = ev => {
    switch (ev.type) {
      case "answer_delta": msg.content += ev.delta; schedule(); break;
      case "answer_reset": msg.content = ""; schedule(); break;
      case "answer_final": msg.content = ev.answer; msg.model = ev.model; msg.score = ev.score; schedule(); break;
      case "received": msg.question_id = ev.question_id; break;
      case "done": msg.stages = ev.stages; msg.elapsed_ms = ev.total_ms; msg.stop_reason = ev.stop_reason; msg.model = ev.model || msg.model; break;
      case "error": failure = { message: ev.message, kind: ev.kind }; break;
    }
    if (ev.type !== "reasoning_delta" && ev.type !== "answer_delta") view.timeline.push(ev);
    if (keepForHistory(ev)) msg.events.push(slim(ev));
    follow();
  };

  try {
    const body = { messages: apiMessages(), mode, save: !priv, ...settings, ...(model ? { model } : {}), ...(priv ? { privacy: "local_only" } : {}) };
    const res = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
    if (!res.ok) throw await errorFrom(res);
    const reader = res.body.getReader(), decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let at;
      while ((at = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, at); buf = buf.slice(at + 2);
        for (const line of raw.split("\n")) if (line.startsWith("data: ")) apply(JSON.parse(line.slice(6)));
      }
    }
  } catch (e) {
    if (e.name === "AbortError") msg.stopped = true;
    else failure = { message: e.message, status: e.status, code: e.code, kind: e.kind, retryAfter: e.retryAfter };
  } finally {
    if (frame) cancelAnimationFrame(frame);
    if (failure && !msg.content) msg.error = failure;
    else if (failure) msg.stopped = false;
    busy = false; controller = null; composer.setBusy(false); jump.hidden = true;
    view.timeline.finish(msg.error ? "failed" : "done");
    if (msg.error && !msg.events.some(e => e.type === "error")) view.timeline.push({ type: "error", t: view.timeline.lastT, text: friendly(msg.error).title });
    finishBot(view, index);
    if (msg.content) announce("Answer ready."); else announce(friendly(msg.error || { message: "Stopped" }).title);
    follow();
    persist();
    composer.focus();
  }
}

export function stop() { controller?.abort(); }

export async function regenerate() {
  if (busy) return;
  while (chat.messages.length && chat.messages[chat.messages.length - 1].role === "assistant") chat.messages.pop();
  if (!chat.messages.length) return;
  renderAll();
  await runTurn(composer.current());
}

async function editAndResend(index, text) {
  chat.messages.length = index + 1;
  const msg = chat.messages[index];
  msg.content = text; msg.private = composer.current().mode === "private";
  if (index === 0) chat.title = titleFrom(text);
  renderAll();
  await runTurn(composer.current());
}

// ---- history ---------------------------------------------------------------------------------------
let saveWarned = false;
export async function persist() {
  const messages = chat.messages.filter(m => !m.private);
  if (!messages.length) return;
  try {
    const saved = await put(`/api/chats/${chat.id}`, { title: chat.title, created_at: chat.created_at, messages });
    chat.title = saved.title; $("#chat-title").textContent = chat.title;
    hooks.onChange();
  } catch (e) {
    if (!saveWarned) { saveWarned = true; toast("This chat could not be saved: " + friendly(e).detail, 5000); }
  }
}

export function newChat() {
  controller?.abort(); stopSpeaking();
  chat = blank(); busy = false;
  renderAll();
  composer.focus();
  hooks.onChange();
}

export async function open(id) {
  if (chat.id === id && chat.messages.length) return true;
  controller?.abort(); stopSpeaking();
  try {
    const data = await json(`/api/chats/${id}`);
    chat = { ...blank(), ...data, id };
    busy = false; composer.setBusy(false);
    renderAll(); composer.focus();
    return true;
  } catch (e) {
    toast(e.status === 404 ? "That chat no longer exists." : friendly(e).detail);
    return false;
  }
}
export const forget = id => { if (chat.id === id) newChat(); };
export function rename(id, title) { if (chat.id === id) { chat.title = title; $("#chat-title").textContent = title; } }
