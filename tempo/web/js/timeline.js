// The thinking window as an animated timeline: one node per step, lighting up while it runs,
// with the models shown as chips the moment they are called. Live runs and saved chats use the
// same code: a saved chat replays its events instantly.
import { el, icon } from "./util.js";

const JOBS = {
  draft: "Drafting an answer", check: "Checking the answer", fix: "Fixing what the check found",
  merge: "Merging the best parts", polish: "Final polish", combine: "Combining the parts",
  split: "Splitting the question", parts: "Answering the parts",
};
const CHECK = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>`;
const CROSS = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17"/></svg>`;

const clock = s => s < 10 ? s.toFixed(1) + "s" : s < 600 ? Math.round(s) + "s" : Math.floor(s / 60) + "m " + Math.round(s % 60) + "s";
const shortModel = id => id.replace(/^[^/]+\//, "");

// Persisted events leave out the token streams; everything else replays.
export const keepForHistory = ev => ev.type !== "answer_delta" && ev.type !== "reasoning_delta";

export class Timeline {
  constructor(root) {
    this.root = root;
    root.innerHTML = `
      <button class="thinking-head" type="button" aria-expanded="true">
        <span class="pulse-dot" aria-hidden="true"></span><span class="title">Thinking</span>
        <span class="stage-dots" aria-hidden="true"></span><span class="summary">Starting…</span>
        <span class="clock"></span>${icon("chevron", "chev")}
      </button>
      <div class="thinking-body"><div class="thinking-inner">
        <ol class="tl" tabindex="0" aria-label="What Tempo is doing, step by step"></ol>
        <details class="raw"><summary>Full log</summary><pre tabindex="0"></pre></details>
      </div></div>`;
    this.head = root.querySelector(".thinking-head");
    this.list = root.querySelector(".tl");
    this.raw = root.querySelector(".raw pre");
    this.dots = root.querySelector(".stage-dots");
    this.summary = root.querySelector(".summary");
    this.clockEl = root.querySelector(".clock");
    this.nodes = new Map();
    this.current = null;      // the running stage node
    this.rawLines = 0;
    this.lastT = 0;
    this.head.addEventListener("click", () => this.toggle());
  }

  toggle(force) {
    const collapsed = force ?? !this.root.classList.contains("collapsed");
    this.root.classList.toggle("collapsed", collapsed);
    this.head.setAttribute("aria-expanded", String(!collapsed));
  }

  start() {
    this.t0 = performance.now();
    this.timer = setInterval(() => { this.clockEl.textContent = clock((performance.now() - this.t0) / 1000); }, 200);
  }

  // Replay saved events without animation.
  replay(events) {
    this.root.classList.add("no-anim");
    for (const ev of events) this.push(ev);
    this.finish(events.some(e => e.type === "error") ? "failed" : "done");
    requestAnimationFrame(() => requestAnimationFrame(() => this.root.classList.remove("no-anim")));
  }

  node(key, title, sub = "") {
    let n = this.nodes.get(key);
    if (n) return n;
    const li = el("li", { class: "node running" },
      el("span", { class: "pt", html: CHECK }),
      el("div", { class: "nt" }, title, sub ? el("small", {}, sub) : null),
      el("div", { class: "why" }), el("div", { class: "models" }), el("div", { class: "notes" }));
    n = { li, key, chips: new Map(), why: li.querySelector(".why"), models: li.querySelector(".models"), notes: li.querySelector(".notes"), title: li.querySelector(".nt") };
    this.nodes.set(key, n);
    const stick = this.nearBottom();
    this.list.append(li);
    if (stick) this.list.scrollTop = this.list.scrollHeight;
    return n;
  }

  nearBottom() { return this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 30; }

  settle(n, state = "done") {
    if (!n) return;
    n.li.classList.remove("running");
    n.li.classList.add(state);
    if (state === "failed") n.li.querySelector(".pt").innerHTML = CROSS;
  }

  note(n, text, kind = "") {
    if (!n || !text) return;
    const stick = this.nearBottom();
    n.notes.append(el("div", { class: "note " + kind }, text));
    if (stick) this.list.scrollTop = this.list.scrollHeight;
  }

  chip(n, model) {
    let c = n.chips.get(model);
    if (!c) {
      c = el("span", { class: "mchip", title: model }, el("i", { class: "st" }), el("span", {}, shortModel(model)));
      n.chips.set(model, c); n.models.append(c);
    }
    return c;
  }

  setChip(n, model, state, ms) {
    const c = this.chip(n, model);
    c.className = "mchip " + state;
    if (ms != null && !c.querySelector(".ms")) c.append(el("span", { class: "ms" }, (ms / 1000).toFixed(1) + "s"));
  }

  setDots(max) {
    this.dots.replaceChildren(...Array.from({ length: Math.min(max, 12) }, () => document.createElement("i")));
  }
  markDot(n, cls) { const d = this.dots.children[n - 1]; if (d) d.className = cls; }

  push(ev) {
    this.lastT = Math.max(this.lastT, ev.t || 0);
    if (ev.text) this.logLine(ev);
    const stageKey = () => this.current || this.node("start", "Understanding your question");
    switch (ev.type) {
      case "received": this.node("start", "Understanding your question"); break;
      case "cache_hit": this.note(this.node("start", "Understanding your question"), ev.text, "good"); break;
      case "analyze": this.note(this.node("start", "Understanding your question"), ev.text); break;
      case "plan": this.note(this.node("start", "Understanding your question"), ev.text); if (ev.max_stages) this.setDots(ev.max_stages); break;
      case "stage_start": {
        this.settle(this.nodes.get("start"));
        const prev = this.current; if (prev) this.settle(prev);
        const n = this.node("stage-" + ev.stage, JOBS[ev.job] || ev.job, `stage ${ev.stage}`);
        n.li.dataset.stage = ev.stage;
        if (ev.reason) n.why.textContent = ev.reason;
        for (const m of ev.models || []) this.setChip(n, m, "pending");
        this.current = n; this.markDot(ev.stage, "now");
        this.summary.textContent = JOBS[ev.job] || ev.job;
        break;
      }
      case "call_start": {
        const n = stageKey();
        this.setChip(n, ev.model, "");
        if (ev.attempt > 1) this.note(n, ev.text, "warn");
        this.summary.textContent = ev.text;
        break;
      }
      case "call_end": this.setChip(stageKey(), ev.model, "ok", ev.ms); break;
      case "call_error": { const n = stageKey(); this.setChip(n, ev.model, "err"); this.note(n, ev.text, "bad"); break; }
      case "fallback": { const n = stageKey(); this.note(n, ev.text, "warn"); if (ev.to) this.setChip(n, ev.to, "pending"); break; }
      case "check": this.note(stageKey(), ev.text, ev.passed ? "good" : "warn"); break;
      case "sandbox": this.note(stageKey(), ev.text, ev.status === "passed" ? "good" : ev.status === "failed" ? "bad" : ""); break;
      case "answer_reset": this.note(stageKey(), ev.text, "warn"); break;
      case "budget": case "note": this.note(stageKey(), ev.text, "warn"); break;
      case "stage_end": {
        const n = this.nodes.get("stage-" + ev.stage);
        if (n) {
          for (const [m, c] of n.chips) if (c.classList.contains("pending")) { c.remove(); n.chips.delete(m); }
          this.settle(n);
        }
        if (this.current === n) this.current = null;
        this.markDot(ev.stage, "done");
        break;
      }
      case "answer_final": if (ev.text) this.note(this.node("final", "Answer ready"), ev.text, "good"); break;
      case "done": {
        for (const n of this.nodes.values()) this.settle(n);
        this.current = null;
        this.summary.textContent = ev.text || "Done";
        const final = this.node("final", "Answer ready");
        this.note(final, ev.text, "good");
        this.settle(final);
        break;
      }
      case "error": {
        const n = this.current || this.nodes.get("start") || this.node("start", "Understanding your question");
        this.settle(n, "failed"); this.note(n, ev.text, "bad");
        this.summary.textContent = ev.text || "Stopped";
        break;
      }
    }
  }

  logLine(ev) {
    if (this.rawLines++ > 600) return;
    this.raw.append(document.createTextNode(`${(ev.t ?? 0).toFixed(2).padStart(6)}s  ${ev.text}\n`));
  }

  finish(status = "done") {
    clearInterval(this.timer);
    this.root.classList.add(status === "failed" ? "failed" : "finished");
    if (status !== "failed") for (const n of this.nodes.values()) this.settle(n);
    else for (const n of this.nodes.values()) if (n.li.classList.contains("running")) this.settle(n, "failed");
    this.clockEl.textContent = this.lastT ? clock(this.lastT) : this.clockEl.textContent;
    for (const d of this.dots.children) if (d.className === "now") d.className = "done";
    this.head.querySelector(".title").textContent = status === "failed" ? "Stopped" : "Thought it through";
    // A finished timeline folds away so the answer leads; the header stays to reopen it.
    this.toggle(true);
  }
}
