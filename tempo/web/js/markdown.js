// Markdown to HTML: escape first, then a safe subset (headings, lists, tables, quotes, code with
// highlighting, maths). Nothing from the model is ever inserted as HTML, and remote images are
// never loaded (offline, no trackers): an image becomes a link.
import { esc, icon, loadScript, loadStyle } from "./util.js";
import { highlight } from "./highlight.js";

const PREVIEW_LANGS = new Set(["html", "svg", "mermaid"]);

export function previewKind(lang, code) {
  const l = (lang || "").toLowerCase();
  if (l === "mermaid") return "mermaid";
  if (l === "svg" || ((l === "xml" || l === "") && /^\s*(?:<\?xml[^>]*>\s*)?<svg[\s>]/i.test(code))) return "svg";
  if (l === "html" && /<\/?[a-z][\s\S]*>/i.test(code)) return "html";
  return null;
}

// ---- inline ----------------------------------------------------------------------------------
const attr = s => esc(s).replace(/`/g, "&#96;");

function mathSpan(tex, display) {
  return `<span class="math pending" data-display="${display ? 1 : 0}" data-tex="${attr(tex)}">${esc(tex)}</span>`;
}

export function inline(src) {
  const held = [];
  const hold = html => `\u0000${held.push(html) - 1}\u0000`;
  let s = src;
  s = s.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_, _t, code) => hold(`<code>${esc(code.trim())}</code>`));
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => hold(mathSpan(tex.trim(), true)));
  s = s.replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => hold(mathSpan(tex.trim(), false)));
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => hold(mathSpan(tex.trim(), true)));
  s = s.replace(/(?<![\\$\w])\$(?![\s$])([^$\n]*?[^\s$\\])\$(?![\d\w$])/g, (_, tex) => hold(mathSpan(tex, false)));
  s = s.replace(/!?\[([^\]\n]+)\]\(\s*(https?:\/\/[^\s)]+|mailto:[^\s)]+)(?:\s+"[^"]*")?\s*\)/g, (whole, text, url) =>
    hold(`<a href="${attr(url)}" target="_blank" rel="noopener noreferrer">${whole.startsWith("!") ? "Image: " : ""}${inline(text)}</a>`));
  s = esc(s);
  s = s.replace(/\bhttps?:\/\/[^\s<]*[^\s<.,;:!?)\]'"&]/g, url => hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
    .replace(/(^|[^\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>")
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, n) => held[n]);
}

const lines_inline = text => text.split("\n").map(inline).join("<br>");

// ---- blocks ----------------------------------------------------------------------------------
const FENCE = /^\s*(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;

const cells = row => {
  let r = row.trim();
  if (r.startsWith("|")) r = r.slice(1);
  if (r.endsWith("|") && !r.endsWith("\\|")) r = r.slice(0, -1);
  return r.split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|"));
};

function codeBlock(lang, code, closed) {
  const kind = closed ? previewKind(lang, code) : null;
  const label = lang || "code";
  return `<div class="codeblock" data-lang="${attr(lang || "")}"${kind ? ` data-preview="${kind}"` : ""}>` +
    `<div class="code-head"><span class="lang">${esc(label)}</span>` +
    (kind ? `<button type="button" class="code-btn" data-act="preview" aria-label="Preview ${esc(label)}">${icon("play")}Preview</button>` : "") +
    `<button type="button" class="code-btn" data-act="copy" aria-label="Copy code">${icon("copy")}Copy</button></div>` +
    `<pre tabindex="0"><code>${highlight(code, lang)}</code></pre></div>`;
}

function renderList(items) {
  // items: [{indent, ordered, start, text, kids: []}] already nested
  const tag = items[0].ordered ? "ol" : "ul";
  const start = items[0].ordered && items[0].start !== 1 ? ` start="${items[0].start}"` : "";
  return `<${tag}${start}>` + items.map(item => {
    let text = item.text, cls = "", box = "";
    const task = text.match(/^\[([ xX])\]\s+(.*)$/s);
    if (task) { cls = ' class="task"'; box = `<input type="checkbox" disabled${task[1] !== " " ? " checked" : ""} aria-label="${task[1] !== " " ? "done" : "not done"}">`; text = task[2]; }
    return `<li${cls}>${box}${lines_inline(text)}${item.kids.length ? renderList(item.kids) : ""}</li>`;
  }).join("") + `</${tag}>`;
}

function buildList(lines) {
  const root = { indent: -1, kids: [] };
  const stack = [root];
  for (const line of lines) {
    const m = line.match(ITEM);
    if (!m) {  // a continuation line belongs to the last item
      const last = stack[stack.length - 1];
      if (last !== root && line.trim()) last.text += "\n" + line.trim();
      continue;
    }
    const indent = m[1].replace(/\t/g, "    ").length;
    const ordered = /\d/.test(m[2]);
    const item = { indent, ordered, start: ordered ? parseInt(m[2], 10) : 1, text: m[3], kids: [] };
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) stack.pop();
    stack[stack.length - 1].kids.push(item);
    stack.push(item);
  }
  return root.kids.length ? renderList(root.kids) : "";
}

function startsBlock(line, next) {
  return FENCE.test(line) || HEADING.test(line) || HR.test(line) || QUOTE.test(line) || ITEM.test(line) ||
    /^\s*(\$\$|\\\[)/.test(line) || (line.includes("|") && next !== undefined && TABLE_SEP.test(next) && next.includes("-"));
}

export function blocks(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let html = "";
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m = line.match(FENCE);
    if (m) {
      const fence = m[1], lang = m[2].toLowerCase(), body = [];
      const closer = new RegExp(`^\\s*${fence[0]}{${fence.length},}\\s*$`);
      i++;
      while (i < lines.length && !closer.test(lines[i])) body.push(lines[i++]);
      const closed = i < lines.length; i++;
      html += codeBlock(lang, body.join("\n"), closed);
      continue;
    }
    if ((m = line.match(/^\s*(\$\$|\\\[)(.*)$/))) {
      const end = m[1] === "$$" ? "$$" : "\\]";
      let tex = m[2];
      if (tex.includes(end)) { tex = tex.slice(0, tex.indexOf(end)); i++; }
      else { i++; const body = [tex]; while (i < lines.length && !lines[i].includes(end)) body.push(lines[i++]);
        if (i < lines.length) body.push(lines[i].slice(0, lines[i].indexOf(end))); i++; tex = body.join("\n"); }
      html += `<div class="math-display">${mathSpan(tex.trim(), true)}</div>`;
      continue;
    }
    if ((m = line.match(HEADING))) { html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; i++; continue; }
    if (HR.test(line) && !ITEM.test(line)) { html += "<hr>"; i++; continue; }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = cells(line), align = cells(lines[i + 1]).map(c => c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : "");
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(cells(lines[i++]));
      const cell = (tag, c, k) => `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${inline(c)}</${tag}>`;
      html += `<div class="tablewrap"><table><thead><tr>${head.map((c, k) => cell("th", c, k)).join("")}</tr></thead><tbody>` +
        rows.map(r => `<tr>${head.map((_, k) => cell("td", r[k] ?? "", k)).join("")}</tr>`).join("") + "</tbody></table></div>";
      continue;
    }
    if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && QUOTE.test(lines[i])) body.push(lines[i++].match(QUOTE)[1]);
      html += `<blockquote>${blocks(body.join("\n"))}</blockquote>`;
      continue;
    }
    if (ITEM.test(line)) {
      const body = [];
      while (i < lines.length) {
        const l = lines[i];
        if (ITEM.test(l) || (l.trim() && /^\s{2,}\S/.test(l) && !FENCE.test(l))) { body.push(l); i++; continue; }
        if (!l.trim() && i + 1 < lines.length && (ITEM.test(lines[i + 1]) || /^\s{2,}\S/.test(lines[i + 1]))) { i++; continue; }
        break;
      }
      html += buildList(body);
      continue;
    }
    const para = [line]; i++;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i], lines[i + 1])) para.push(lines[i++]);
    html += `<p>${lines_inline(para.join("\n"))}</p>`;
  }
  return html;
}

export const renderMarkdown = src => blocks(src || "");

// ---- maths (KaTeX loads only when an answer contains a formula) ---------------------------------
let katexReady;
export async function typesetMath(root) {
  const nodes = Array.from(root.querySelectorAll(".math.pending"));
  if (!nodes.length) return;
  try {
    katexReady ||= (loadStyle("/static/vendor/katex/katex.min.css"), loadScript("/static/vendor/katex/katex.min.js"));
    await katexReady;
  } catch { return; }
  for (const node of nodes) {
    try {
      node.innerHTML = globalThis.katex.renderToString(node.dataset.tex, { displayMode: node.dataset.display === "1", throwOnError: false, trust: false, output: "htmlAndMathml" });
      node.classList.remove("pending");
    } catch { node.classList.remove("pending"); }
  }
}

export { PREVIEW_LANGS };
