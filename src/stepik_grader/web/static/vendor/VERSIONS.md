# Vendored CodeMirror 6 bundle

No npm/bundler *in this repo* (per project philosophy — see `static/fonts/`
for the same pattern with web fonts). `codemirror-bundle@6.mjs`
is a single self-contained ESM bundle, built ONCE outside the repo with
[esbuild](https://esbuild.github.io) from the real npm packages and committed
as a finished artifact — no build step, no Node.js, no bundler dependency at
runtime or in CI. There is no import map: `app.js` imports this one file
directly by URL (`/static/vendor/codemirror-bundle@6.mjs`).

## Why one bundle, not one esm.sh-fetched file per package

The previous scheme vendored 8 separate esm.sh bundles (one per
`@codemirror/*`/`@lezer/*` package, cross-linked via `external=` so they'd
share one copy of shared state — see git history for that approach) plus 4
tiny Node.js browser-compat polyfill files that one of them needed for an
optional, never-exercised debug/tracing code path esm.sh couldn't tree-shake
across package boundaries. That was ~12 HTTP requests, an import map, and
dead Node polyfill weight shipped to the browser for a code path the app
never calls.

Building with esbuild instead (bundling the *real* npm packages, not esm.sh's
per-package re-bundles) tree-shakes across the whole dependency graph at
once: the same debug/tracing code path that dragged in Node shims before
gets eliminated entirely, so **no Node shims are needed at all** — verified
by grepping the built output for `events`/`tty`/`process`/`async_hooks`
(none present). One file, no import map, smaller than the sum of the old 12
files.

## What's inside

`codemirror-bundle@6.mjs` bundles exactly the named exports `static/app.js`
imports (see `entry.js` in the build recipe below) — `EditorState`,
`EditorView`, `lineNumbers`, `keymap`, `placeholder`, `defaultKeymap`,
`history`, `historyKeymap`, `indentWithTab`, `syntaxHighlighting`,
`HighlightStyle`, `indentOnInput`, `python`, `tags` — from these packages
(pinned 2026-07-13; last built 2026-07-16 with esbuild 0.28.1):

`HighlightStyle` (from `@codemirror/language`) and `tags` (from
`@lezer/highlight`) were added so `app.js` builds a
theme-driven syntax highlight style on `--cm-*` CSS variables (readable in
light and dark) instead of the light-only `defaultHighlightStyle` — which is
therefore no longer exported.

| Package | Version |
|---|---|
| `@codemirror/state` | 6.7.1 |
| `@codemirror/view` | 6.43.6 |
| `@codemirror/language` | 6.12.4 |
| `@codemirror/commands` | 6.10.4 |
| `@codemirror/lang-python` | 6.2.1 |
| `@codemirror/autocomplete` | 6.20.3 |
| `@lezer/common` | 1.5.2 |
| `@lezer/highlight` | 1.2.3 |
| `@lezer/lr` | 1.4.10 |
| `@lezer/python` | 1.1.19 |
| `style-mod` | 4.1.3 |
| `w3c-keyname` | 2.2.8 |
| `crelt` | 1.0.7 |

`@codemirror/autocomplete` (Python snippet completions used by
`@codemirror/lang-python`) and `style-mod`/`w3c-keyname`/`crelt` (small
CodeMirror-ecosystem helper libraries) are transitive dependencies, not
top-level imports of `app.js` — they were already silently inlined in the
old per-package esm.sh bundles too (the earlier scheme never listed them explicitly
in this table; now documented properly since the full dependency graph is
visible during the esbuild step).

## How to rebuild (reproducible recipe)

Requires Node.js + npm (build-time only, outside this repo — nothing here
adds a runtime or CI dependency on either). Run in a scratch directory:

```bash
mkdir /tmp/codemirror-build && cd /tmp/codemirror-build
npm init -y
npm install --no-save \
  @codemirror/state@6.7.1 \
  @codemirror/view@6.43.6 \
  @codemirror/language@6.12.4 \
  @codemirror/commands@6.10.4 \
  @codemirror/lang-python@6.2.1 \
  @lezer/highlight@1.2.3 \
  esbuild@latest

cat > entry.js << 'EOF'
export { EditorState } from "@codemirror/state";
export { EditorView, lineNumbers, keymap, placeholder } from "@codemirror/view";
export { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
export { syntaxHighlighting, HighlightStyle, indentOnInput } from "@codemirror/language";
export { python } from "@codemirror/lang-python";
export { tags } from "@lezer/highlight";
EOF

npx esbuild entry.js --bundle --format=esm --platform=browser --minify \
  --outfile=codemirror-bundle@6.mjs
```

Copy the resulting `codemirror-bundle@6.mjs` into `static/vendor/`, replacing
the old one.

**Only re-export named bindings actually used by `app.js`** (`entry.js`
above) — do NOT switch to `export * from "..."` across multiple packages:
if two of the bundled packages ever export a same-named binding, a wildcard
re-export silently drops it (ambiguous export), rather than erroring, which
would fail at import time in `app.js` with a confusing "does not provide an
export" message far from the actual cause.

**Verify after rebuilding** (this bundling scheme has no automated CI check
for these two properties):
1. `grep -oE '"(events|tty|process|async_hooks|node:[a-z_]+)"' codemirror-bundle@6.mjs`
   must print nothing — a future package version could reintroduce a
   Node-dependent debug path that esbuild's tree-shaking no longer eliminates.
2. Manual browser check: mount the editor, confirm Python syntax
   highlighting renders, check the browser console for import errors.

## How to bump versions

Update the version numbers in the `npm install` line above (and the table
above) to the desired versions, re-run the recipe, then in this repo:
- Replace `static/vendor/codemirror-bundle@6.mjs` with the new build.
- If the *file name* changes (e.g. a future major bump renames it to
  `codemirror-bundle@7.mjs`), update the one reference each in
  `web/server.py` (`_VENDOR_FILES`) and `web/static/app.js` (the import
  path) — `pyproject.toml`'s `web/static/vendor/*` package-data glob matches
  any filename, no change needed there.
- Update this file's version table and pinned date.
