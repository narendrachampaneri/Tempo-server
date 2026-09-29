# Bundled web assets and their licences

Everything the web app loads ships inside this package (no CDN). Versions and licences,
checked 2026-09-29 against each package's own metadata and licence file.

| Component | Version | Licence | Where |
|---|---|---|---|
| Inter (variable, Latin and Latin Extended) | 5.3.0 (Fontsource package) | SIL Open Font License 1.1 | `fonts/`, text in `fonts/OFL-Inter.txt` |
| JetBrains Mono (variable, Latin) | 5.3.0 (Fontsource package) | SIL OFL 1.1 | `fonts/`, `fonts/OFL-JetBrains-Mono.txt` |
| Noto Sans Gujarati (400, 600) | 5.3.0 (Fontsource package) | SIL OFL 1.1 | `fonts/`, `fonts/OFL-Noto-Sans-Gujarati.txt` |
| Noto Sans Devanagari (400, 600) | 5.3.0 (Fontsource package) | SIL OFL 1.1 | `fonts/`, `fonts/OFL-Noto-Sans-Devanagari.txt` |
| KaTeX (maths; loaded only when an answer has a formula) | 0.18.9 | MIT | `vendor/katex/`, licence in `vendor/katex/LICENSE` |
| Mermaid (diagram previews; loaded only when you press Preview on a diagram) | 12.0.0 | MIT | `vendor/mermaid/`, licence in `vendor/mermaid/LICENSE` |

The app's own code (`css/`, `js/`, `index.html`) is Apache-2.0 like the rest of Tempo-server.
The syntax highlighter and the Markdown renderer are written for Tempo-server (`js/highlight.js`,
`js/markdown.js`); no highlighting or Markdown library is bundled.

## What is inside `mermaid.min.js`

Mermaid's own single-file build bundles its dependencies. From their package metadata: 64 MIT,
32 ISC (d3 and friends), 6 Apache-2.0, 3 BSD-3-Clause, 1 Unlicense (robust-predicates),
DOMPurify (MPL-2.0 OR Apache-2.0), and **elkjs, which is EPL-2.0** (used only for Mermaid's
optional `elk` layout). The file is shipped unmodified, and its licence comments are intact.
EPL-2.0 is a weak, file-level licence: it asks that the elkjs source stay available (it is
public at https://github.com/kieler/elkjs) and that changes to it, if any, be shared. If the
owner would rather not ship any EPL code, drop `vendor/mermaid/` and the Mermaid branch of
`js/preview.js`; nothing else depends on it.

The fonts were copied from the Fontsource npm packages, which repackage the upstream OFL fonts
unchanged. OFL 1.1 lets them be bundled, embedded and redistributed with software.
