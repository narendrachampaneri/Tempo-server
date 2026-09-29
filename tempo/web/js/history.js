// The sidebar: past chats grouped by Today, Yesterday, Last 7 days and Older; search, rename, pin,
// delete and export. Chats live in the data folder on this computer (see tempo/history.py).
import { $, el, icon, iconEl, toast, debounce, download } from "./util.js";
import { api, json, patch, del, errorFrom, friendly } from "./api.js";

const list = $("#history"), searchBox = $("#search");
let hooks = { open() {}, deleted() {}, renamed() {}, active: () => null, confirm: async () => true };
let chats = [], query = "", loaded = false, saved = true, openMenu = null;

export function init(options) {
  hooks = { ...hooks, ...options };
  searchBox.addEventListener("input", debounce(() => { query = searchBox.value.trim(); refresh(); }, 180));
  searchBox.addEventListener("keydown", e => { if (e.key === "Escape") { searchBox.value = ""; query = ""; refresh(); searchBox.blur(); } });
  document.addEventListener("pointerdown", e => { if (openMenu && !openMenu.contains(e.target) && !e.target.closest(".more")) closeMenu(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && openMenu) { const b = openMenu.opener; closeMenu(); b?.focus(); } });
  skeleton();
}

export const focusSearch = () => { searchBox.focus(); searchBox.select(); };
export const setSaved = value => { saved = value; };

function skeleton() {
  list.replaceChildren(el("div", { class: "skel-list", "aria-hidden": "true" },
    ...[70, 55, 80, 60, 75].map(w => el("div", { class: "skel", style: `height:34px;width:${w}%` }))));
}

function group(chat) {
  const now = new Date(), day = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const t = chat.updated_at;
  if (chat.pinned) return "Pinned";
  if (t >= day) return "Today";
  if (t >= day - 86400) return "Yesterday";
  if (t >= day - 6 * 86400) return "Last 7 days";
  return "Older";
}
const ORDER = ["Pinned", "Today", "Yesterday", "Last 7 days", "Older"];

export async function refresh() {
  try {
    const data = await json("/api/chats?limit=500" + (query ? "&q=" + encodeURIComponent(query) : ""));
    chats = data.chats; loaded = true;
  } catch (e) {
    if (!loaded) { list.replaceChildren(el("div", { class: "side-empty" }, "History could not be loaded. ", friendly(e).next)); return; }
  }
  render();
}

export function markActive() {
  const id = hooks.active();
  list.querySelectorAll(".chat-item").forEach(n => {
    const on = n.dataset.id === id;
    n.classList.toggle("active", on);
    on ? n.querySelector(".chat-open").setAttribute("aria-current", "true") : n.querySelector(".chat-open").removeAttribute("aria-current");
  });
}

function render() {
  const nodes = [];
  if (!saved) nodes.push(el("div", { class: "side-note" }, "Kept in memory only: this server was started with no data folder, so chats vanish when it stops."));
  if (!chats.length) {
    nodes.push(el("div", { class: "side-empty" }, query ? "No chats match your search." : "Your chats will appear here. They stay on this computer."));
  } else {
    const groups = new Map(ORDER.map(g => [g, []]));
    for (const c of chats) groups.get(group(c)).push(c);
    for (const name of ORDER) {
      if (!groups.get(name).length) continue;
      nodes.push(el("div", { class: "group-title", role: "heading", "aria-level": "2" }, name));
      groups.get(name).forEach(c => nodes.push(item(c)));
    }
  }
  list.replaceChildren(...nodes);
  markActive();
}

function item(chat) {
  const open = el("button", { type: "button", class: "chat-open", title: chat.title, onclick: () => hooks.open(chat.id) },
    el("span", { class: "t" }, chat.pinned ? iconEl("pin") : null, el("span", { style: "overflow:hidden;text-overflow:ellipsis" }, chat.title)),
    chat.snippet ? el("span", { class: "s" }, "…" + chat.snippet + "…") : null);
  const more = el("button", { type: "button", class: "icon-btn more", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Options for ${chat.title}`, html: icon("more"),
    onclick: e => { e.stopPropagation(); showMenu(more, chat); } });
  return el("div", { class: "chat-item", dataset: { id: chat.id } }, open, more);
}

function closeMenu() { if (openMenu) { openMenu.opener?.setAttribute("aria-expanded", "false"); openMenu.remove(); openMenu = null; } }

function showMenu(button, chat) {
  if (openMenu?.opener === button) { closeMenu(); return; }
  closeMenu();
  const act = fn => () => { closeMenu(); fn(); };
  const menu = el("div", { class: "menu", role: "menu" },
    el("button", { role: "menuitem", type: "button", html: `${icon("edit")}Rename`, onclick: act(() => rename(chat)) }),
    el("button", { role: "menuitem", type: "button", html: `${icon("pin")}${chat.pinned ? "Unpin" : "Pin to top"}`, onclick: act(() => pin(chat)) }),
    el("button", { role: "menuitem", type: "button", html: `${icon("download")}Export as Markdown`, onclick: act(() => exportChat(chat, "md")) }),
    el("button", { role: "menuitem", type: "button", html: `${icon("download")}Export as JSON`, onclick: act(() => exportChat(chat, "json")) }),
    el("hr"),
    el("button", { role: "menuitem", type: "button", class: "danger", html: `${icon("trash")}Delete`, onclick: act(() => remove(chat)) }));
  menu.opener = button;
  document.body.append(menu);
  const r = button.getBoundingClientRect(), m = menu.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(innerWidth - m.width - 8, r.right - m.width)) + "px";
  menu.style.top = (r.bottom + m.height + 8 > innerHeight ? Math.max(8, r.top - m.height - 4) : r.bottom + 4) + "px";
  button.setAttribute("aria-expanded", "true");
  menu.addEventListener("keydown", e => {
    const items = Array.from(menu.querySelectorAll("button")), i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  });
  openMenu = menu;
  menu.querySelector("button").focus();
}

function rename(chat) {
  const row = list.querySelector(`.chat-item[data-id="${chat.id}"]`); if (!row) return;
  const input = el("input", { class: "rename-input", value: chat.title, "aria-label": "Chat name", maxlength: 120 });
  row.replaceChildren(input);
  input.focus(); input.select();
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    const title = input.value.trim();
    if (save && title && title !== chat.title) {
      try { await patch(`/api/chats/${chat.id}`, { title }); hooks.renamed(chat.id, title); } catch (e) { toast(friendly(e).detail); }
    }
    refresh();
  };
  input.addEventListener("keydown", e => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}

async function pin(chat) {
  try { await patch(`/api/chats/${chat.id}`, { pinned: !chat.pinned }); refresh(); } catch (e) { toast(friendly(e).detail); }
}

async function exportChat(chat, format) {
  try {
    const res = await api(`/api/chats/${chat.id}/export?format=${format}`);
    if (!res.ok) throw await errorFrom(res);
    const name = (res.headers.get("Content-Disposition") || "").match(/filename\*=UTF-8''([^;]+)/);
    download(name ? decodeURIComponent(name[1]) : `chat.${format}`, await res.text(), format === "md" ? "text/markdown" : "application/json");
    toast(`Exported as ${format === "md" ? "Markdown" : "JSON"}.`);
  } catch (e) { toast(friendly(e).detail); }
}

async function remove(chat) {
  if (!await hooks.confirm({ title: "Delete this chat?", text: `“${chat.title}” will be deleted from this computer. This cannot be undone.`, ok: "Delete" })) return;
  try { await del(`/api/chats/${chat.id}`); hooks.deleted(chat.id); toast("Chat deleted."); refresh(); } catch (e) { toast(friendly(e).detail); }
}
