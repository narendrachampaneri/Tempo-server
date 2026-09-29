// Read-aloud through the server's free speech model. Nothing plays without a key that allows it,
// and the button is hidden then. Answers are read in short pieces (the model takes about 200
// characters at a time), and only the first few, to protect the free daily voice limit.
import { api, errorFrom, friendly } from "./api.js";
import { toast } from "./util.js";

const PIECE = 190, MAX_PIECES = 6;
let playing = null; // {stop}

export function speakable(markdown) {
  const text = markdown
    .replace(/```[\s\S]*?```/g, " (code omitted) ").replace(/`([^`]*)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[*_#>|~]+/g, " ").replace(/\s+/g, " ").trim();
  const pieces = [];
  for (let sentence of text.split(/(?<=[.!?।])\s+/)) {
    while (sentence.length > PIECE) {
      let cut = sentence.lastIndexOf(" ", PIECE); if (cut < 40) cut = PIECE;
      pieces.push(sentence.slice(0, cut).trim()); sentence = sentence.slice(cut).trim();
    }
    if (sentence) pieces.push(sentence);
  }
  const merged = [];
  for (const p of pieces) {
    if (merged.length && merged[merged.length - 1].length + 1 + p.length <= PIECE) merged[merged.length - 1] += " " + p;
    else merged.push(p);
  }
  return merged;
}

export function stopSpeaking() { playing?.stop(); }
export const isSpeaking = () => !!playing;

// Returns a promise that resolves when reading ends. onEnd is called in every case.
export async function speak(markdown, onEnd = () => {}) {
  stopSpeaking();
  const pieces = speakable(markdown);
  if (!pieces.length) { onEnd(); return; }
  let stopped = false, audio = null;
  const handle = { stop() { stopped = true; audio?.pause(); playing = null; onEnd(); } };
  playing = handle;
  if (pieces.length > MAX_PIECES) toast("Reading the first part only, to protect your free voice limit.");
  try {
    for (const piece of pieces.slice(0, MAX_PIECES)) {
      if (stopped) return;
      const res = await api("/api/speech/say", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: piece }) });
      if (!res.ok) throw await errorFrom(res);
      const url = URL.createObjectURL(await res.blob());
      if (stopped) { URL.revokeObjectURL(url); return; }
      audio = new Audio(url);
      await new Promise((resolve, reject) => { audio.onended = resolve; audio.onerror = reject; audio.play().catch(reject); });
      URL.revokeObjectURL(url);
    }
  } catch (e) {
    if (!stopped) { const f = friendly(e); toast(`${f.detail} ${f.next}`, 6000); }
  }
  if (playing === handle) { playing = null; onEnd(); }
}
