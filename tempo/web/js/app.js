// Tempo web app: wires the sidebar, chat, pages, theme, shortcuts and dialogs together.
import { $, $$, el, icon, store, toast, announce } from "./util.js";
import { json, setKeyPrompt, refreshStatus } from "./api.js";
import * as chat from "./chat.js";
import * as sidebar from "./history.js";
import * as pages from "./pages.js";
import { createComposer } from "./composer.js";

let caps = { vision: false, speech: {}, history: { saved: true } };

// ---- icons in the static markup ------------------------------------------------------------------
$("#open-side").innerHTML = icon("menu");
$("#close-side").innerHTML = icon("x");
$("#new-chat").innerHTML = `${icon("plus")}New chat`;
$("#attach").innerHTML = icon("clip");
$("#mic").innerHTML = icon("mic");
$("#gear").innerHTML = icon("gear");
$("#shortcuts-btn").innerHTML = icon("keyboard");
const NAV = { models: ["models", "Models"], usage: ["chart", "Usage"], keys: ["key", "Keys"], developers: ["code", "Developers"] };
$$(".side-nav a").forEach(a => { const [ic, label] = NAV[a.dataset.page]; a.innerHTML = `${icon(ic)}${label}`; });

// ---- theme: follows the system until you choose --------------------------------------------------
const themeBtn = $("#theme-btn");
const systemDark = () => matchMedia("(prefers-color-scheme: dark)").matches;
function paintTheme() {
  const choice = store.get("tempo_theme");
  const explicit = choice === "light" || choice === "dark";
  const dark = explicit ? choice === "dark" : systemDark();
  themeBtn.innerHTML = icon(dark ? "moon" : "sun");
  const label = explicit ? `Theme: ${choice}` : `Theme: system (${dark ? "dark" : "light"})`;
  themeBtn.setAttribute("aria-label", label + ". Press to change.");
  themeBtn.title = label;
}
themeBtn.addEventListener("click", () => {
  // system -> the opposite of what the system shows -> the other one -> back to system
  const choice = store.get("tempo_theme");
  const next = choice === "light" || choice === "dark" ? (choice === (systemDark() ? "dark" : "light") ? null : (choice === "dark" ? "light" : "dark")) : (systemDark() ? "light" : "dark");
  if (next) { store.set("tempo_theme", next); document.documentElement.dataset.theme = next; }
  else { store.remove("tempo_theme"); delete document.documentElement.dataset.theme; }
  paintTheme(); toast(themeBtn.title);
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTheme);
paintTheme();

// ---- dialogs -------------------------------------------------------------------------------------
function ask(dialog, setup) {
  return new Promise(resolve => {
    setup?.();
    dialog.returnValue = "";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    dialog.showModal();
  });
}
const confirmDialog = ({ title, text, ok = "Delete" }) => ask($("#dlg-confirm"), () => {
  $("#dlg-confirm-title").textContent = title; $("#dlg-confirm-text").textContent = text; $("#dlg-confirm-ok").textContent = ok;
});
setKeyPrompt(() => askKey());
const SHORTCUTS = [
  ["New chat", ["Ctrl", "Shift", "O"]], ["Search your chats", ["Ctrl", "K"]], ["Focus the message box", ["/"]],
  ["Stop the answer", ["Esc"]], ["Send", ["Enter"]], ["New line", ["Shift", "Enter"]], ["Show these shortcuts", ["?"]],
];
$("#shortcut-list").append(...SHORTCUTS.flatMap(([label, keys]) => [
  el("span", {}, label), el("span", {}, keys.flatMap((k, i) => [i ? " + " : "", el("kbd", {}, k)]))]));
const showShortcuts = () => { const d = $("#dlg-shortcuts"); if (!d.open) d.showModal(); };
$("#shortcuts-btn").addEventListener("click", showShortcuts);
$("#dlg-sc-close").addEventListener("click", () => $("#dlg-shortcuts").close());
for (const d of $$("dialog")) d.addEventListener("click", e => { if (e.target === d) d.close(); });

// ---- composer and chat -----------------------------------------------------------------------------
const composer = createComposer({ onSend: payload => chat.send(payload), onStop: () => chat.stop(), caps: () => caps });
composer.init();
const askKey = async () => { $("#dlg-key-input").value = ""; return (await ask($("#dlg-key"))) ? $("#dlg-key-input").value : null; };
chat.init({ composer, caps: () => caps, askKey, onChange() { sidebar.refresh(); syncHash(); }, go: page => { location.hash = "#/" + page; } });

sidebar.init({
  active: () => (chat.current().messages.some(m => !m.private) ? chat.current().id : null),
  open: id => { location.hash = "#/c/" + id; closeSide(); },
  deleted: id => { chat.forget(id); if (location.hash === "#/c/" + id) history.replaceState(null, "", "#/"); },
  renamed: (id, title) => chat.rename(id, title),
  confirm: confirmDialog,
});
$("#new-chat").addEventListener("click", () => { chat.newChat(); history.replaceState(null, "", "#/"); route(); closeSide(); });

function syncHash() {
  const c = chat.current();
  if (c.messages.some(m => !m.private) && !location.hash.startsWith("#/c/")) history.replaceState(null, "", "#/c/" + c.id);
  sidebar.markActive();
}

// ---- pages / router -----------------------------------------------------------------------------------
const VIEWS = ["chat", "models", "usage", "keys", "developers"];
const PAGE_TITLES = { models: "Models", usage: "Usage", keys: "Keys and data", developers: "Developers" };
async function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [head, rest] = hash.split("/");
  let view = VIEWS.includes(head) && head !== "chat" ? head : "chat";
  if (head === "c" && rest) {
    if (!await chat.open(rest)) { history.replaceState(null, "", "#/"); view = "chat"; }
  }
  for (const v of VIEWS) $(`#${v}-view`).hidden = v !== view;
  $$(".side-nav a").forEach(a => (a.dataset.page === view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  $("#chat-title").textContent = view === "chat" ? (chat.current().title || "New chat") : PAGE_TITLES[view];
  document.title = view === "chat" ? "Tempo" : `${PAGE_TITLES[view]} · Tempo`;
  sidebar.markActive();
  if (view === "models") pages.loadModels();
  if (view === "usage") pages.loadUsage();
  if (view === "keys") { pages.loadKeys(); pages.loadData(); }
  if (view === "chat") { composer.focus(); if (!chat.current().messages.length) pages.loadQuota(); }
  announce(view === "chat" ? "" : `${PAGE_TITLES[view]} page`);
}
addEventListener("hashchange", () => { route(); closeSide(); });
$("#status-pill").addEventListener("click", () => { location.hash = "#/models"; });
pages.wireDelete(confirmDialog, () => { chat.newChat(); sidebar.refresh(); history.replaceState(null, "", "#/keys"); });
pages.fillDevelopers();

// ---- small screens: the sidebar slides in -----------------------------------------------------------------
const side = $("#sidebar"), backdrop = $("#backdrop");
function openSide() { side.classList.add("open"); backdrop.hidden = false; $("#search").focus({ preventScroll: true }); }
function closeSide() { side.classList.remove("open"); backdrop.hidden = true; }
$("#open-side").addEventListener("click", openSide);
$("#close-side").addEventListener("click", closeSide);
backdrop.addEventListener("click", closeSide);

// ---- keyboard shortcuts -----------------------------------------------------------------------------------
addEventListener("keydown", e => {
  const mod = e.ctrlKey || e.metaKey;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); if (matchMedia("(max-width: 900px)").matches) openSide(); sidebar.focusSearch(); }
  else if (mod && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); $("#new-chat").click(); }
  else if (e.key === "Escape" && chat.isBusy() && !document.querySelector("dialog[open]") && $("#settings-pop").hidden) { chat.stop(); }
  else if (e.key === "Escape") closeSide();
  else if (!typing && !mod && e.key === "/") { e.preventDefault(); location.hash.startsWith("#/c/") || location.hash === "" || location.hash === "#/" ? composer.focus() : (location.hash = "#/", composer.focus()); }
  else if (!typing && !mod && e.key === "?") { e.preventDefault(); showShortcuts(); }
});

// ---- start --------------------------------------------------------------------------------------------------
async function loadCaps() {
  try { caps = await json("/api/capabilities"); } catch { return; }
  composer.showVoice(!!caps.speech?.transcribe);
  sidebar.setSaved(caps.history?.saved !== false);
}
(async () => {
  await Promise.all([loadCaps(), refreshStatus()]);
  await sidebar.refresh();
  await route();
})();
setInterval(refreshStatus, 60000);
