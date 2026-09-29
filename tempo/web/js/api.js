// Talking to the Tempo server, and turning every failure into a friendly message with a next step.
import { $, store } from "./util.js";

let askKey; // set by app.js: shows the API-key dialog and resolves to the entered key (or null)
export const setKeyPrompt = fn => { askKey = fn; };

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, retryAfter = null, kind = null } = {}) {
    super(message);
    Object.assign(this, { status, code, retryAfter, kind });
  }
}

export async function api(path, init = {}, retried = false) {
  const headers = new Headers(init.headers || {});
  const key = store.get("tempo_api_key");
  if (key) headers.set("Authorization", "Bearer " + key);
  let res;
  try { res = await fetch(path, { ...init, headers }); }
  catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Cannot reach the Tempo server.", { code: "unreachable" });
  }
  if (res.status === 401 && !retried && askKey) {
    const entered = await askKey();
    if (entered) { store.set("tempo_api_key", entered.trim()); return api(path, init, true); }
  }
  return res;
}

export async function errorFrom(res) {
  let data = {};
  try { data = await res.json(); } catch { /* no body */ }
  const e = data?.error || {};
  return new ApiError(e.message || data?.detail?.[0]?.msg || `Request failed (${res.status}).`, {
    status: res.status, code: e.code, kind: e.type, retryAfter: Number(res.headers.get("Retry-After")) || null,
  });
}

export async function json(path, init) {
  const res = await api(path, init);
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

export const put = (path, body) => json(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const patch = (path, body) => json(path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const del = path => json(path, { method: "DELETE" });

function wait(seconds) {
  if (!seconds) return "a little later";
  if (seconds < 90) return `in ${seconds} seconds`;
  if (seconds < 5400) return `in about ${Math.round(seconds / 60)} minutes`;
  return `in about ${Math.round(seconds / 3600)} hours`;
}

// error: {message, status?, code?, kind?, retryAfter?}; returns {title, detail, next, actions}.
export function friendly(error) {
  const msg = error.message || "Something went wrong.";
  const low = msg.toLowerCase();
  const KEYS = { label: "Add a free key", go: "keys" };
  const MODELS = { label: "See models", go: "models" };
  const RETRY = { label: "Try again", go: "retry" };
  if (error.code === "unreachable")
    return { title: "Can't reach Tempo", detail: "The page lost its connection to the server.",
      next: "Check that `tempo-server serve` is still running, then try again.", actions: [RETRY] };
  if (error.status === 401)
    return { title: "This server needs an API key", detail: "The key was missing or not accepted.",
      next: "Enter the key you set as TEMPO_API_KEY, or one made with `tempo-server users add`.", actions: [{ label: "Enter key", go: "apikey" }] };
  if (low.includes("no model yet") || low.includes("providers are configured"))
    return { title: "No models are set up yet", detail: "Tempo has nothing to send your question to.",
      next: "Add a free provider key (Groq, Google, OpenRouter and others are free), or start Ollama for local models.", actions: [KEYS, MODELS] };
  if (low.includes("free quota is used up") || low.includes("quota") && (error.status === 503 || error.kind === "unavailable"))
    return { title: "Free limits are used up for now", detail: "Every free model has reached its limit and no local model is running.",
      next: `Start Ollama for local answers, add another free key, or try ${wait(error.retryAfter)}.`, actions: [KEYS, RETRY] };
  if (error.status === 503 || error.kind === "unavailable")
    return { title: "No model can answer right now", detail: msg,
      next: `Switch mode, check the Models page, or try ${wait(error.retryAfter)}.`, actions: [MODELS, RETRY] };
  if (error.status === 502 || error.kind === "invalid")
    return { title: "The models did not give a usable answer", detail: msg,
      next: "Try again, or choose Best mode for a stronger model.", actions: [RETRY] };
  if (error.kind === "not_found" || error.code === "model_not_found")
    return { title: "That model isn't available", detail: msg, next: "Pick Auto in the settings, or check the Models page.", actions: [MODELS] };
  if (error.status === 429 || error.code === "rate_limited")
    return { title: "Slow down a little", detail: msg, next: `Try again ${wait(error.retryAfter)}.`, actions: [RETRY] };
  if (error.status === 413 || low.includes("too large"))
    return { title: "That is too large", detail: msg, next: "Try a smaller file or image, or split it into parts.", actions: [] };
  if (error.status === 400)
    return { title: "Tempo could not use that request", detail: msg, next: "Change your message and send it again.", actions: [] };
  return { title: "Something went wrong", detail: msg, next: "Try again. If it keeps happening, run `tempo-server doctor` and check the server log.", actions: [RETRY] };
}

// The header's status: what *this* caller can use now (their own keys, the owner's keys, local
// models). /health is only the fallback for a page that isn't signed in yet.
export async function refreshStatus() {
  const pill = $("#status-pill"), text = $("#status-text");
  try {
    // No key prompt from here (it refreshes every minute): without a key, the public count.
    const res = await api("/api/status", {}, true);
    const data = res.ok ? await res.json() : await (await fetch("/health")).json();
    const n = data.models_ready;
    pill.className = "pill " + (n ? "ok" : "warn");
    text.textContent = n ? `${n} model${n === 1 ? "" : "s"} ready` : "No model yet";
    pill.title = data.message || (n ? "Models that can answer right now." : "No model yet: run tempo-server setup or start Ollama.");
    const note = $("#setup-note");
    if (note) note.hidden = !!n;
    return data;
  } catch {
    pill.className = "pill bad"; text.textContent = "Server unreachable"; pill.title = "The page cannot reach the server.";
    return null;
  }
}
