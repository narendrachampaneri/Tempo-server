// The JS SDK against a real Tempo-server in demo mode (offline demo models, no provider keys).
// Uses $TEMPO_URL and $TEMPO_API_KEY when set (CI starts the server); otherwise starts one with
// `python -m tempo.cli serve` ($TEMPO_PYTHON, default python3 / python on Windows) and a user
// made with `tempo-server users add`.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";

import { TempoClient, TempoError, applyEvent, newAnswer } from "../dist/index.js";

const QUESTION = "What is 17% of 2,340?";
let url = process.env.TEMPO_URL;
let key = process.env.TEMPO_API_KEY;
let server;

function freePort() {
  return new Promise((resolve) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

before(async () => {
  if (url && key) return;
  const python = process.env.TEMPO_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const env = {
    ...process.env,
    TEMPO_DATA_DIR: mkdtempSync(join(tmpdir(), "tempo-js-")),
    TEMPO_ENABLE_MOCK: "1",
    TEMPO_EMBEDDINGS: "off",
    TEMPO_LAYA: "off",
    TEMPO_SYNC_INTERVAL: "0",
    TEMPO_CACHE: "off",
    PYTHONUTF8: "1",
  };
  delete env.TEMPO_API_KEY;
  const made = execFileSync(python, ["-m", "tempo.cli", "users", "add", "sdk-test"], {
    env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  key = made.trim().split(/\r?\n/).pop();
  const port = await freePort();
  url = `http://127.0.0.1:${port}`;
  server = spawn(python, ["-m", "tempo.cli", "serve", "--port", String(port)], { env, stdio: "ignore" });
  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      if ((await fetch(url + "/health")).ok) break;
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) throw new Error("demo server did not start");
    await new Promise((r) => setTimeout(r, 200));
  }
});

after(() => server?.kill());

const client = () => new TempoClient({ baseUrl: url, apiKey: key });

test("stream shows the thinking window live", async () => {
  const events = [];
  for await (const event of client().stream({ prompt: QUESTION, max_stages: 3 })) events.push(event);
  const types = events.map((e) => e.type);
  assert.equal(types[0], "received");
  assert.equal(types.at(-1), "done");
  for (const t of ["analyze", "plan", "stage_start", "answer_delta"]) assert.ok(types.includes(t), t);
  assert.ok(events.filter((e) => e.type === "stage_start").every((e) => e.text));
});

test("ask folds the answer", async () => {
  const answer = await client().ask(QUESTION);
  assert.ok(answer.text && answer.model?.startsWith("mock/"));
  assert.ok(answer.questionId && answer.stages >= 1 && answer.trace.length > 0);
  assert.equal(answer.error, null);
  const chat = await client().ask({ messages: [{ role: "user", content: "hi" }], privacy: "no_logging", mode: "fast" });
  assert.ok(chat.text);
});

test("feedback, consent and delete my data", async () => {
  const tempo = client();
  const answer = await tempo.ask("hi there");
  await tempo.feedback(answer.questionId, "up", "helpful");
  await tempo.feedback(answer.questionId, -1);
  await assert.rejects(tempo.feedback(answer.questionId, 5), TypeError);
  assert.equal(await tempo.consent(), false);
  assert.equal(await tempo.setConsent(true), true);
  assert.equal(await tempo.consent(), true);
  assert.equal(await tempo.setConsent(false), false);
  assert.ok((await tempo.deleteMyData()) >= 1);
  await assert.rejects(tempo.feedback(answer.questionId, 1), (err) => err instanceof TempoError && err.status === 404);
});

test("models, quota, me and health", async () => {
  const tempo = client();
  const models = await tempo.models();
  assert.ok(models.models.length > 0 && "status" in models.models[0]);
  const quota = await tempo.quota();
  assert.ok(Array.isArray(quota.providers) && quota.all_used_up === false);
  assert.equal((await tempo.me()).user, "sdk-test");
  assert.equal((await tempo.health()).status, "ok");
});

test("a wrong key raises TempoError 401", async () => {
  const bad = new TempoClient({ baseUrl: url, apiKey: "not-a-tempo-key" });
  await assert.rejects(bad.quota(), (err) => err instanceof TempoError && err.status === 401 && /API key/.test(err.message));
  await assert.rejects(async () => {
    for await (const _ of bad.stream("hi")) void _;
  }, TempoError);
});

test("applyEvent folds resets and final answers", () => {
  const answer = newAnswer();
  for (const e of [
    { type: "received", question_id: "q1", text: "Received" },
    { type: "answer_delta", delta: "draft" },
    { type: "answer_reset", text: "Replacing" },
    { type: "answer_final", answer: "final", score: 0.9 },
    { type: "done", model: "m", stages: 2, requests: 3, stop_reason: "passed", text: "Done" },
  ]) applyEvent(answer, e);
  assert.equal(answer.text, "final");
  assert.equal(answer.questionId, "q1");
  assert.deepEqual(answer.trace, ["Received", "Replacing", "Done"]);
});
