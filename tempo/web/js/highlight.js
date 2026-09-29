// A small syntax highlighter (no dependency): comments, strings, numbers, keywords, calls, types.
// Output is escaped HTML with <span class="tok-*"> around tokens.
import { esc } from "./util.js";

const words = s => new RegExp(`\\b(?:${s.trim().split(/\s+/).join("|")})\\b`);
const KW = {
  py: "and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield None True False self match case",
  js: "as async await break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while with yield null undefined true false interface type enum implements public private protected readonly namespace declare abstract",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class namespace template typename using new delete public private protected virtual override final bool true false nullptr this try catch throw operator package import interface extends implements abstract fn let mut pub use mod impl trait match loop where self Self crate move ref dyn unsafe as func var go defer chan select range map string byte rune nil fun val when object data sealed init lateinit suspend null internal is in out foreach echo function elseif endif",
  sh: "if then else elif fi for while until do done case esac in function select time return exit export local readonly declare unset shift break continue echo cd set source alias trap eval exec",
  sql: "select from where and or not null insert into values update set delete create table alter drop index view join left right inner outer full cross on as group by order having limit offset union all distinct case when then else end primary key foreign references default unique check constraint exists in between like is asc desc with returning begin commit rollback",
};

const STR_DQ = String.raw`"(?:\\[\s\S]|[^"\\\n])*"`;
const STR_SQ = String.raw`'(?:\\[\s\S]|[^'\\\n])*'`;
const NUM = String.raw`\b(?:0[xX][\da-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b`;

// each rule: [className, source]. First alternative that matches at a position wins.
function build(rules, flags = "gm") {
  const source = rules.map(([, src]) => `(${src})`).join("|");
  return { re: new RegExp(source, flags), classes: rules.map(r => r[0]) };
}
const cache = new Map();
const wordSrc = s => words(s).source;

function spec(lang) {
  if (cache.has(lang)) return cache.get(lang);
  let rules;
  const call = ["tok-fn", String.raw`\b[A-Za-z_]\w*(?=\s*\()`];
  const type = ["tok-type", String.raw`\b[A-Z][A-Za-z0-9_]*\b`];
  switch (lang) {
    case "py":
      rules = [["tok-com", "#.*"], ["tok-str", String.raw`[rRbBfFuU]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))`],
        ["tok-str", String.raw`[rRbBfFuU]{0,2}(?:${STR_DQ}|${STR_SQ})`], ["tok-kw", wordSrc(KW.py)], ["tok-num", NUM], ["tok-fn", String.raw`@[\w.]+`], call, type];
      break;
    case "js":
      rules = [["tok-com", String.raw`//.*|/\*[\s\S]*?(?:\*/|$)`], ["tok-str", "`(?:\\\\[\\s\\S]|[^`\\\\])*`?"], ["tok-str", STR_DQ], ["tok-str", STR_SQ],
        ["tok-kw", wordSrc(KW.js)], ["tok-num", NUM], call, type];
      break;
    case "c":
      rules = [["tok-com", String.raw`//.*|/\*[\s\S]*?(?:\*/|$)`], ["tok-kw", String.raw`^\s*#\s*\w+`], ["tok-str", STR_DQ], ["tok-str", String.raw`'(?:\\.|[^'\\\n])'`],
        ["tok-str", "`[^`]*`"], ["tok-kw", wordSrc(KW.c)], ["tok-num", NUM], call, type];
      break;
    case "sh":
      rules = [["tok-com", String.raw`(?:^|\s)#.*`], ["tok-str", STR_DQ], ["tok-str", String.raw`'[^']*'`], ["tok-type", String.raw`\$(?:\w+|\{[^}]*\}|\(|\?)`],
        ["tok-kw", wordSrc(KW.sh)], ["tok-num", NUM], ["tok-fn", String.raw`(?:^|[|;&]\s*)[\w./-]+`]];
      break;
    case "sql":
      rules = [["tok-com", String.raw`--.*|/\*[\s\S]*?(?:\*/|$)`], ["tok-str", STR_SQ], ["tok-str", STR_DQ], ["tok-kw", wordSrc(KW.sql)], ["tok-num", NUM], call];
      break;
    case "json":
      rules = [["tok-fn", String.raw`"(?:\\.|[^"\\\n])*"(?=\s*:)`], ["tok-str", STR_DQ], ["tok-kw", String.raw`\b(?:true|false|null)\b`], ["tok-num", String.raw`-?\d[\d.eE+-]*`]];
      break;
    case "html":
      rules = [["tok-com", String.raw`<!--[\s\S]*?(?:-->|$)`], ["tok-kw", String.raw`</?[A-Za-z][\w:-]*|/?>`], ["tok-str", STR_DQ], ["tok-str", STR_SQ], ["tok-fn", String.raw`[\w:-]+(?=\s*=)`]];
      break;
    case "css":
      rules = [["tok-com", String.raw`/\*[\s\S]*?(?:\*/|$)`], ["tok-str", STR_DQ], ["tok-str", STR_SQ], ["tok-kw", String.raw`@[\w-]+`], ["tok-fn", String.raw`[\w-]+(?=\s*:(?!:))`],
        ["tok-num", String.raw`#[\da-fA-F]{3,8}\b|-?\d[\d.]*(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?\b`], ["tok-type", String.raw`[.#][A-Za-z_][\w-]*`]];
      break;
    case "yaml":
      rules = [["tok-com", "#.*"], ["tok-fn", String.raw`^\s*-?\s*[\w.\-/ ]+(?=:(?:\s|$))`], ["tok-str", STR_DQ], ["tok-str", STR_SQ], ["tok-kw", String.raw`\b(?:true|false|null|yes|no)\b`], ["tok-num", NUM]];
      break;
    default:
      rules = [["tok-com", String.raw`//.*|/\*[\s\S]*?(?:\*/|$)|(?:^|\s)#.*`], ["tok-str", STR_DQ], ["tok-str", STR_SQ], ["tok-num", NUM]];
  }
  const built = build(rules, lang === "sql" ? "gmi" : "gm");
  cache.set(lang, built);
  return built;
}

const ALIASES = {
  python: "py", py: "py", python3: "py",
  javascript: "js", js: "js", jsx: "js", mjs: "js", typescript: "js", ts: "js", tsx: "js", node: "js",
  c: "c", h: "c", cpp: "c", "c++": "c", cc: "c", java: "c", go: "c", golang: "c", rust: "c", rs: "c", cs: "c", csharp: "c", kotlin: "c", kt: "c", swift: "c", php: "c", scala: "c", dart: "c",
  bash: "sh", sh: "sh", shell: "sh", zsh: "sh", console: "sh", terminal: "sh",
  sql: "sql", mysql: "sql", postgresql: "sql", sqlite: "sql",
  json: "json", jsonc: "json", html: "html", xml: "html", svg: "html", vue: "html",
  css: "css", scss: "css", less: "css", yaml: "yaml", yml: "yaml", toml: "yaml",
};

export function canonical(lang) { return ALIASES[(lang || "").toLowerCase()] || "text"; }

export function highlight(code, lang) {
  const { re, classes } = spec(canonical(lang));
  let out = "", last = 0;
  re.lastIndex = 0;
  for (const m of code.matchAll(re)) {
    if (m[0] === "") continue;
    out += esc(code.slice(last, m.index));
    const group = m.findIndex((v, i) => i > 0 && v !== undefined) - 1;
    out += `<span class="${classes[group]}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}
