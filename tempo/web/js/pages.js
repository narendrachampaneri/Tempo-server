// The Models, Usage, Keys and Developers pages.
import { $, $$, el, compact, toast } from "./util.js";
import { api, json, put, del, friendly, refreshStatus } from "./api.js";

const skeletonCards = (host, n = 6) => host.replaceChildren(...Array.from({ length: n }, () => el("div", { class: "skel skel-card" })));
const showError = (host, e) => { const f = friendly(e); host.replaceChildren(el("div", { class: "errbox" }, el("b", {}, f.title), el("div", { class: "next" }, f.next))); };

// ---- models --------------------------------------------------------------------------------------
const statusClass = s => s === "ready" ? "ready" : (s === "cooling down" || s === "provider key rejected") ? "cool" : "";
export async function loadModels() {
  const cards = $("#provider-cards"), rows = $("#model-rows");
  if (!cards.children.length) { skeletonCards(cards); }
  let data;
  try { data = await json("/api/models"); } catch (e) { showError(cards, e); return; }
  cards.replaceChildren();
  for (const p of data.providers) {
    const st = el("span", { class: "status " + (p.configured ? "ready" : "") }, p.configured ? "configured" : "not configured");
    const env = el("span", { class: "env" });
    if (p.configured) env.textContent = p.env ? `${p.env} is set` : "";
    else {
      env.append(`Set ${p.env} · `);
      if (p.signup_url) env.append(el("a", { href: p.signup_url, target: "_blank", rel: "noopener noreferrer" }, p.local ? "install" : "get a free key"));
    }
    cards.append(el("div", { class: "card", title: p.free_tier_note || null },
      el("div", { class: "name" }, p.label + (p.free_tier === "trial" ? " · trial credits" : "")), el("div", { class: "row" }, st, env)));
  }
  const order = { ready: 0 };
  data.models.sort((a, b) => ((order[a.status] ?? 1) - (order[b.status] ?? 1)) || b.strength - a.strength);
  rows.replaceChildren(...data.models.map(m => {
    const top = Object.entries(m.skills || {}).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k]) => k).join(", ");
    const features = [m.reasoning && "thinks", m.vision && "images"].filter(Boolean).join(", ") || "–";
    const ctx = m.context_window >= 1048576 ? Math.round(m.context_window / 1048576) + "M" : Math.round(m.context_window / 1024) + "k";
    const bar = el("td", {}, el("span", { class: "bar" }, el("i", { style: `width:${Math.round(m.strength * 100)}%` })), m.strength.toFixed(2));
    return el("tr", {}, el("td", {}, el("span", { class: "status " + statusClass(m.status) }, m.status)),
      el("td", { class: "mono" }, m.id), el("td", {}, m.type || "chat"),
      el("td", {}, m.local ? "local" : (m.free_rpd ? m.free_rpd.toLocaleString() : "–")), el("td", {}, ctx), bar,
      el("td", { class: "muted" }, top), el("td", { class: "muted" }, features));
  }));
}

// ---- usage ---------------------------------------------------------------------------------------
let usageHours = 24;
const seconds = ms => ms == null ? "–" : (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + " s";
const STOP_LABELS = { passed: "passed its check", cache: "from cache", polished: "final rewrite", unchecked: "unchecked (1 stage)",
  decided: "decided good enough", budget_stages: "stage budget used", budget_time: "time budget reached", budget_quota: "quota budget used" };

function tile(label, value, note) {
  return el("div", { class: "tile" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value), note ? el("div", { class: "note" }, note) : null);
}

function bars(figure, rows, { format = compact, tip = r => `${format(r.value)}` } = {}) {
  const holder = $(".bars", figure); holder.replaceChildren();
  figure.querySelector("details")?.remove();
  if (!rows.length) { holder.append(el("div", { class: "empty-note" }, "No data in this range yet.")); return; }
  const max = Math.max(...rows.map(r => r.value), 1), tipEl = $("#tip");
  for (const r of rows) {
    const fill = el("span", { class: "fill", style: `width:${Math.max(1, (r.value / max) * 100)}%` });
    const row = el("div", { class: "hbar-row", tabindex: "0" }, el("span", { class: "name", title: r.label }, r.label), el("span", { class: "track" }, fill), el("span", { class: "val" }, format(r.value)));
    const show = () => {
      tipEl.replaceChildren(el("b", {}, tip(r)), " " + r.label); tipEl.hidden = false;
      const box = row.getBoundingClientRect(), host = $("#usage-page").getBoundingClientRect();
      tipEl.style.left = `${box.left - host.left + 12}px`; tipEl.style.top = `${box.top - host.top - 30}px`;
    };
    row.addEventListener("pointerenter", show); row.addEventListener("focus", show);
    row.addEventListener("pointerleave", () => { tipEl.hidden = true; }); row.addEventListener("blur", () => { tipEl.hidden = true; });
    holder.append(row);
  }
  figure.append(el("details", { class: "tableview" }, el("summary", {}, "Show as table"),
    el("table", {}, ...rows.map(r => el("tr", {}, el("td", {}, r.label), el("td", {}, tip(r)))))));
}

function meters(figure, rows) {
  const holder = $(".bars", figure); holder.replaceChildren();
  if (!rows.length) { holder.append(el("div", { class: "empty-note" }, "No daily limits tracked for your ready models.")); return; }
  for (const r of rows) {
    const share = r.free_rpd ? r.rpd_left / r.free_rpd : 1;
    const name = r.model + (r.own_key ? " (your key)" : "");
    holder.append(el("div", { class: "hbar-row quota" }, el("span", { class: "name", title: name }, name),
      el("span", { class: "val" }, `${compact(r.rpd_left)} / ${compact(r.free_rpd)}`),
      el("span", { class: "meter" + (share <= 0 ? " empty" : share < 0.2 ? " low" : "") }, el("i", { style: `width:${Math.max(0, Math.min(1, share)) * 100}%` }))));
  }
}

export async function loadUsage() {
  const page = $("#usage-page");
  if (!$("#kpis").children.length) skeletonCards($("#kpis"), 6);
  let u;
  try { u = await json(`/api/usage?hours=${usageHours}`); } catch (e) { showError($("#kpis"), e); return; }
  $("#usage-scope").textContent = u.scope === "mine" ? "Your questions only." : "All questions on this server.";
  const passRate = u.questions ? Math.round((u.passed / Math.max(1, u.questions - u.errors)) * 100) + "%" : "–";
  $("#kpis").replaceChildren(
    tile("Questions", compact(u.questions), u.errors ? `${u.errors} failed` : null),
    tile("Passed their check", passRate),
    tile("Median time", seconds(u.p50_ms), u.p95_ms != null ? `p95 ${seconds(u.p95_ms)}` : null),
    tile("Average stages", String(u.avg_stages ?? "–"), u.questions ? `${u.escalated} needed 3+` : null),
    tile("Free requests used", compact(u.requests_used), u.cache_hits ? `${u.cache_hits} answered from cache` : null),
    tile("Feedback", `👍 ${u.thumbs_up}  👎 ${u.thumbs_down}`));
  bars($("#chart-models"), u.models.map(m => ({ label: m.model, value: m.calls, errors: m.errors, avg: m.avg_ms })),
    { tip: r => `${r.value} calls · ${r.errors} failed · ${seconds(r.avg)} avg` });
  bars($("#chart-stops"), Object.entries(u.stop_reasons).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: STOP_LABELS[k] || k, value: v })),
    { tip: r => `${r.value} question${r.value === 1 ? "" : "s"}` });
  meters($("#chart-quota"), u.quota);
  const laya = u.laya;
  bars($("#chart-laya"), Object.entries(laya.by_status).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v })), { tip: r => `${r.value} decisions` });
  $("#chart-laya figcaption span").textContent = `Status: ${laya.status} · ${laya.predicted} of ${laya.decisions} decisions predicted · ` +
    `${laya.predicted ? Math.round(laya.agree_with_rules / laya.predicted * 100) : 0}% agree with the rules`;
  page.dataset.loaded = "1";
}
$("#range").addEventListener("click", e => {
  const b = e.target.closest("button[data-hours]"); if (!b) return;
  usageHours = Number(b.dataset.hours);
  $$("#range button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  loadUsage();
});

// ---- keys and your data ---------------------------------------------------------------------------
export async function loadKeys() {
  const list = $("#key-list");
  if (!list.children.length) list.replaceChildren(...Array.from({ length: 4 }, () => el("div", { class: "key-row" }, el("div", { class: "skel", style: "height:30px" }))));
  let data;
  try { data = await json("/api/keys"); } catch (e) { showError(list, e); return; }
  list.replaceChildren();
  for (const p of data.providers) {
    const small = el("small", {}, p.has_key
      ? `Your key (${p.fingerprint})${p.verified === true ? " · verified" : p.verified === false ? " · rejected" : ""}`
      : p.server_key ? "Using the server's key" : "No key yet");
    if (p.off_note) small.textContent += ` · off by default: ${p.off_note}`;
    const field = el("input", { type: "password", autocomplete: "off", placeholder: p.has_key ? "Replace with a new key" : "Paste your API key", "aria-label": `${p.label} API key` });
    const save = el("button", { class: "btn primary small", type: "submit" }, "Save");
    const side = el("div", { class: "key-msg", role: "status" });
    if (p.has_key) side.append(el("button", { class: "btn small", type: "button", onclick: async () => { await api(`/api/keys/${encodeURIComponent(p.provider)}`, { method: "DELETE" }); loadKeys(); refreshStatus(); } }, "Remove"));
    else if (p.signup_url) side.append(el("a", { href: p.signup_url, target: "_blank", rel: "noopener noreferrer", title: p.free_tier_note || null }, p.free_tier === "trial" ? "Get trial credits" : "Get a free key"));
    const form = el("form", { onsubmit: async e => {
      e.preventDefault(); if (!field.value.trim()) return;
      save.disabled = true; save.textContent = "Checking…";
      try { await put(`/api/keys/${encodeURIComponent(p.provider)}`, { api_key: field.value.trim() }); field.value = ""; loadKeys(); refreshStatus(); toast(`${p.label} key saved.`); }
      catch (err) { field.value = ""; save.disabled = false; save.textContent = "Save"; side.textContent = err.message || "Could not save the key."; }
    } }, field, save);
    list.append(el("div", { class: "key-row" }, el("div", { class: "who" }, p.label, small), form, side));
  }
}

export async function loadData() {
  const toggle = $("#consent-toggle"), note = $("#consent-msg");
  try {
    const data = await json("/api/consent");
    toggle.checked = !!data.consent; toggle.disabled = !!data.owner;
    note.textContent = data.owner ? "You run this server: your questions are always yours to use." : "";
  } catch { /* shown by the keys list */ }
}
$("#consent-toggle").addEventListener("change", async e => {
  const note = $("#consent-msg"), on = e.target.checked;
  try { await put("/api/consent", { consent: on }); note.textContent = on ? "Thank you: your questions may be used, with personal data removed." : "Turned off: later exports leave your questions out."; }
  catch { e.target.checked = !on; note.textContent = "Could not save your choice."; }
});

export function wireDelete(confirm, onDeleted) {
  $("#delete-start").addEventListener("click", async () => {
    if (!await confirm({ title: "Delete all my data?", text: "This deletes every question you asked with its answers and feedback, and all your saved chats. Your account and keys stay. It cannot be undone.", ok: "Yes, delete everything" })) return;
    try {
      const r = await json("/api/data", { method: "DELETE" });
      $("#delete-msg").textContent = `Deleted ${r.deleted_questions ?? 0} question${r.deleted_questions === 1 ? "" : "s"} and your saved chats.`;
      onDeleted();
    } catch (e) { $("#delete-msg").textContent = friendly(e).detail; }
  });
}

// ---- developers ------------------------------------------------------------------------------------
export function fillDevelopers() {
  const base = location.origin;
  $("#snip-curl").textContent =
`curl ${base}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $TEMPO_API_KEY" \\
  -d '{"model": "tempo/auto", "messages": [{"role": "user", "content": "Hello!"}]}'`;
  $("#snip-python").textContent =
`from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="YOUR_TEMPO_API_KEY")  # any string if the server has no key

reply = client.chat.completions.create(
    model="tempo/auto",
    messages=[{"role": "user", "content": "Explain recursion in one paragraph."}],
    extra_body={"tempo": {"privacy": "default", "trace": True}},
)
print(reply.model)                       # the model Tempo routed to
print(reply.choices[0].message.content)
print(reply.tempo["trace"])              # the thinking-window events`;
  $("#snip-models").textContent =
`model: "tempo/auto"      balanced quality, speed and free quota
model: "tempo/fast"      prefer the fastest capable model
model: "tempo/best"      prefer the strongest model
model: "tempo/private"   local models only (Ollama)
model: "<registry id>"   try that model first, e.g. "groq/openai/gpt-oss-120b"

"tempo": {
  "mode": "fast",                          // overrides the mode in "model"
  "privacy": "local_only",                 // or "default"
  "allow_providers": ["groq", "cerebras"], // only route to these
  "trace": true,                           // include thinking-window events
  "max_stages": 8,                         // draft, check, fix, merge... (1-50)
  "time_budget_s": 45,                     // stop and return the best answer so far
  "quota_budget": 10,                      // most free provider requests to spend
  "strategy": "mixture"                    // or single / cascade / decompose
}

Streams carry the checked final answer; with "max_stages": 1 tokens stream live.`;
  $$(".snippet pre").forEach(pre => pre.setAttribute("tabindex", "0"));
  $("#snip-cli").textContent =
`tempo-server ask "What is the capital of Australia?"
tempo-server ask --mode best "Prove that the square root of 2 is irrational"
tempo-server ask --private "Summarize this" < notes.txt
tempo-server ask --max-stages 10 --time-budget 90 "Plan a 3-day trip to Jaipur with a budget"
tempo-server chat
tempo-server models
tempo-server keys add groq          # your own key, stored encrypted
tempo-server eval --limit 2         # measure models on the probe set
tempo-server export-laya --out ds   # decisions for Laya's fine-tuning notebook
tempo-server serve --port 8000`;
}

// ---- the free quota strip on the empty chat -----------------------------------------------------------
export async function loadQuota() {
  let data;
  try { data = await json("/api/quota"); } catch { return; }
  const chips = data.providers.map(p => {
    let text, cls = "chip", title = null;
    if (p.local) text = `${p.label}: local, no limit`;
    else if (p.left_today == null) text = `${p.label}: ${p.note || "no daily limit known"}`;
    else {
      text = `${p.label}: ${compact(p.left_today)}` + (p.per_day ? ` / ${compact(p.per_day)}` : "");
      if (p.left_today === 0) cls += " empty"; else if (p.per_day && p.left_today / p.per_day < 0.2) cls += " low";
      if (p.resets_in && p.resets_in !== "-") title = `Resets in ${p.resets_in}`;
    }
    return el("span", { class: cls, title }, text);
  });
  $("#quota-chips").replaceChildren(...chips);
  $("#quota-alert").hidden = !data.all_used_up;
  $("#quota-strip").hidden = !chips.length;
}
