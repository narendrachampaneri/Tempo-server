/**
 * A thin client for Tempo-server's HTTP API, for Node 18+ and browsers (uses fetch).
 * No server code inside, no dependencies.
 *
 *   import { TempoClient } from "tempo-server-client";
 *   const tempo = new TempoClient({ baseUrl: "http://127.0.0.1:8000", apiKey: "your-tempo-key" });
 *   for await (const event of tempo.stream("Explain TCP vs UDP")) {
 *     if (event.text) console.log("▸", event.text);
 *   }
 */

export const VERSION = "0.1.0";
const DEFAULT_URL = "http://127.0.0.1:8000";

export type Mode = "auto" | "fast" | "best" | "private";
export type Privacy = "default" | "local_only" | "no_logging";
export type Strategy = "single" | "cascade" | "mixture" | "decompose";

export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string | Array<Record<string, unknown>>;
  [key: string]: unknown;
}

/** One thinking-window event: `type` (received, analyze, plan, stage_start, call_start,
 *  answer_delta, check, fallback, note, done, error, ...) and its fields. */
export interface TempoEvent {
  type: string;
  /** Seconds since the question was received. */
  t?: number;
  /** The one-line summary the thinking window shows (absent for answer text pieces). */
  text?: string;
  [key: string]: unknown;
}

export interface AskOptions {
  prompt?: string;
  messages?: Message[];
  mode?: Mode;
  /** local_only: only local models; no_logging: never a model whose free tier may log or train on prompts. */
  privacy?: Privacy;
  model?: string;
  max_stages?: number;
  time_budget_s?: number;
  quota_budget?: number;
  max_parallel?: number;
  strategy?: Strategy;
  allow_providers?: string[];
  signal?: AbortSignal;
}

export interface Answer {
  text: string;
  reasoning: string;
  model: string | null;
  questionId: string | null;
  stages: number;
  requests: number;
  stopReason: string | null;
  score: number | null;
  error: string | null;
  events: TempoEvent[];
  /** The thinking window as lines of text. */
  trace: string[];
}

export interface ProviderQuota {
  provider: string;
  label: string;
  local: boolean;
  per_day: number | null;
  left_today: number | null;
  per_minute: number | null;
  per_month: number | null;
  resets_in_s: number | null;
  resets_in: string;
  note: string;
}

export interface Quota {
  providers: ProviderQuota[];
  all_used_up: boolean;
}

export interface ModelRow {
  id: string;
  name: string;
  provider: string;
  family: string;
  status: string;
  context_window: number;
  free_rpd: number | null;
  local: boolean;
  vision: boolean;
  strength: number;
  [key: string]: unknown;
}

export interface Models {
  providers: Array<Record<string, unknown> & { id: string; label: string; configured: boolean }>;
  models: ModelRow[];
  [key: string]: unknown;
}

export interface ClientOptions {
  /** Defaults to $TEMPO_URL (Node) or http://127.0.0.1:8000. */
  baseUrl?: string;
  /** Your Tempo key (never a provider key); defaults to $TEMPO_API_KEY (Node). */
  apiKey?: string;
  /** A fetch implementation (defaults to the global one). */
  fetch?: typeof fetch;
}

/** An error answer from Tempo-server. `retryAfter`: seconds, when every free quota is used up. */
export class TempoError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly retryAfter: number | null;

  constructor(message: string, status: number | null = null, code: string | null = null, retryAfter: number | null = null) {
    super(message);
    this.name = "TempoError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

function env(name: string): string | undefined {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return proc?.env?.[name];
}

async function errorFrom(response: Response): Promise<TempoError> {
  let message = `HTTP ${response.status}`;
  let code: string | null = null;
  try {
    const data = (await response.json()) as { error?: unknown; detail?: unknown };
    const err = data.error ?? data;
    if (typeof err === "string") message = err;
    else if (err && typeof err === "object") {
      const e = err as { message?: string; code?: string };
      if (e.message) message = e.message;
      code = e.code ?? null;
    }
    if (typeof data.detail === "string") message = data.detail;
  } catch {
    /* not JSON */
  }
  const retry = response.headers.get("retry-after");
  return new TempoError(message, response.status, code, retry ? Number(retry) : null);
}

/** Parse server-sent events (`data: {...}` lines) from a streamed response body. */
export async function* parseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<TempoEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline: number;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        const event = parseLine(line);
        if (event) yield event;
      }
      if (done) break;
    }
    const last = parseLine(buffer);
    if (last) yield last;
  } finally {
    reader.releaseLock();
  }
}

function parseLine(line: string): TempoEvent | null {
  if (!line.startsWith("data:")) return null;
  const payload = line.slice(5).trim();
  if (!payload || payload === "[DONE]") return null;
  return JSON.parse(payload) as TempoEvent;
}

/** Fold a stream of events into the final answer. */
export function newAnswer(): Answer {
  return {
    text: "",
    reasoning: "",
    model: null,
    questionId: null,
    stages: 0,
    requests: 0,
    stopReason: null,
    score: null,
    error: null,
    events: [],
    trace: [],
  };
}

export function applyEvent(answer: Answer, event: TempoEvent): Answer {
  answer.events.push(event);
  if (event.text) answer.trace.push(event.text);
  const get = <T>(key: string) => event[key] as T;
  switch (event.type) {
    case "answer_delta":
      answer.text += get<string>("delta") ?? "";
      break;
    case "reasoning_delta":
      answer.reasoning += get<string>("delta") ?? "";
      break;
    case "answer_reset":
      answer.text = "";
      answer.reasoning = "";
      break;
    case "answer_final":
      answer.text = get<string>("answer") ?? answer.text;
      answer.score = get<number | null>("score") ?? null;
      break;
    case "received":
      answer.questionId = get<string>("question_id") ?? null;
      break;
    case "done":
      answer.model = get<string | null>("model") ?? null;
      answer.stages = get<number>("stages") ?? 0;
      answer.requests = get<number>("requests") ?? 0;
      answer.stopReason = get<string | null>("stop_reason") ?? null;
      answer.questionId = get<string | null>("question_id") ?? answer.questionId;
      break;
    case "error":
      answer.error = get<string>("message") ?? "error";
      break;
  }
  return answer;
}

export class TempoClient {
  readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? env("TEMPO_URL") ?? DEFAULT_URL).replace(/\/+$/, "");
    this.apiKey = options.apiKey ?? env("TEMPO_API_KEY");
    const f = options.fetch ?? globalThis.fetch;
    if (!f) throw new Error("No fetch available: use Node 18+ or pass options.fetch");
    this.fetchImpl = f.bind(globalThis);
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json", ...extra };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    return headers;
  }

  private async json<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await this.fetchImpl(this.baseUrl + path, {
      method,
      headers: this.headers(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw await errorFrom(response);
    return (await response.json()) as T;
  }

  /** Ask and stream every thinking-window event as it happens. */
  async *stream(question: string | AskOptions): AsyncGenerator<TempoEvent> {
    const options: AskOptions = typeof question === "string" ? { prompt: question } : question;
    const { signal, ...rest } = options;
    if (rest.prompt === undefined && !rest.messages?.length) {
      throw new TypeError("give a prompt or messages");
    }
    const body: Record<string, unknown> = { mode: "auto", privacy: "default" };
    for (const [key, value] of Object.entries(rest)) if (value !== undefined) body[key] = value;
    const response = await this.fetchImpl(this.baseUrl + "/api/ask", {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) throw await errorFrom(response);
    if (!response.body) throw new TempoError("The server sent no stream.");
    yield* parseEvents(response.body);
  }

  /** Ask and wait for the checked final answer (`answer.events` has the trace). */
  async ask(question: string | AskOptions): Promise<Answer> {
    const answer = newAnswer();
    for await (const event of this.stream(question)) applyEvent(answer, event);
    return answer;
  }

  /** 👍 (1 or "up") or 👎 (-1 or "down") for an answer, saved for tuning. */
  async feedback(questionId: string, rating: 1 | -1 | "up" | "down", comment?: string): Promise<void> {
    const value = rating === "up" ? 1 : rating === "down" ? -1 : rating;
    if (value !== 1 && value !== -1) throw new TypeError('rating must be 1 / -1 or "up" / "down"');
    await this.json("POST", "/api/feedback", {
      question_id: questionId,
      rating: value,
      ...(comment ? { comment } : {}),
    });
  }

  health(): Promise<{ status: string; version: string; models_ready: number }> {
    return this.json("GET", "/health");
  }

  me(): Promise<{ user: string; mode: string }> {
    return this.json("GET", "/api/me");
  }

  /** Providers and models, ready or not (and why), with health and skills. */
  models(): Promise<Models> {
    return this.json("GET", "/api/models");
  }

  /** Free requests left today per provider, and whether everything is used up. */
  quota(): Promise<Quota> {
    return this.json("GET", "/api/quota");
  }

  /** Whether your questions may be used as training data (off by default). */
  async consent(): Promise<boolean> {
    return (await this.json<{ consent: boolean }>("GET", "/api/consent")).consent;
  }

  /** Opt in to training use, or withdraw it. */
  async setConsent(consent: boolean): Promise<boolean> {
    return (await this.json<{ consent: boolean }>("PUT", "/api/consent", { consent })).consent;
  }

  /** Delete every question you asked (answers, decisions, feedback); returns how many. */
  async deleteMyData(): Promise<number> {
    return (await this.json<{ deleted_questions: number }>("DELETE", "/api/data")).deleted_questions;
  }
}
