// The JavaScript SDK: stream the thinking window, then use the answer.
//
//   (cd sdk/js && npm install && npm run build)   # `npm install tempo-server-client` once published
//   TEMPO_ENABLE_MOCK=1 tempo-server serve &
//   node examples/sdk_js.mjs
import { TempoClient } from "../sdk/js/dist/index.js"; // from "tempo-server-client" once published

const tempo = new TempoClient(); // $TEMPO_URL (default http://127.0.0.1:8000) and $TEMPO_API_KEY

for await (const event of tempo.stream({ prompt: "What is 17% of 2,340?", mode: "fast" })) {
  if (event.text) console.log("▸", event.text);
}

const answer = await tempo.ask("Explain the difference between TCP and UDP in two lines");
console.log(`\n${answer.text}\n\n— ${answer.model}, ${answer.stages} stages, ${answer.stopReason}`);
await tempo.feedback(answer.questionId, "up");
const quota = await tempo.quota();
console.log("Free requests left today:", quota.providers.map((q) => `${q.label}: ${q.left_today}`));
