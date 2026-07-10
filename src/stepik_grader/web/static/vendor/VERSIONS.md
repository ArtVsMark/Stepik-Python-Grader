# Vendored CodeMirror 6 bundles (issue #265)

No npm/bundler in this repo (per project philosophy — see `static/fonts/`
for the same pattern with web fonts, issue #260). Each file below is a
pre-built ESM bundle fetched from [esm.sh](https://esm.sh) with the *other*
packages in this set marked `external` so they share ONE copy of
`@codemirror/state`/`@codemirror/view`/`@codemirror/language`/`@lezer/common`
across all bundles — `index.html`'s import map resolves the resulting bare
specifiers back to these local files. This is load-bearing: CodeMirror's
`Facet`/`StateField` extension system works by object identity, so two
independently-bundled copies of e.g. `@codemirror/state` would silently
break cross-package extensions (verified live in a browser during
development — this is not a theoretical concern).

## Versions (pinned 2026-07-10)

| File | Package | Version |
|---|---|---|
| `codemirror-state@6.7.1.mjs` | `@codemirror/state` | 6.7.1 |
| `codemirror-view@6.43.6.mjs` | `@codemirror/view` | 6.43.6 |
| `codemirror-language@6.12.4.mjs` | `@codemirror/language` | 6.12.4 |
| `codemirror-commands@6.10.4.mjs` | `@codemirror/commands` | 6.10.4 |
| `codemirror-lang-python@6.2.1.mjs` | `@codemirror/lang-python` | 6.2.1 |
| `lezer-common@1.5.2.mjs` | `@lezer/common` | 1.5.2 |
| `lezer-highlight@1.2.3.mjs` | `@lezer/highlight` | 1.2.3 |
| `lezer-lr@1.4.10.mjs` | `@lezer/lr` | 1.4.10 |
| `node-events.mjs`, `node-tty.mjs`, `node-async_hooks.mjs`, `node-process.mjs` | esm.sh Node browser-compat shims | n/a (see below) |

`@lezer/lr` has one optional, browser-unused debug code path (a `Stack`
class extending Node's `EventEmitter` for parse tracing) gated behind a
static `import "process"` esm.sh always includes regardless of `external` —
hence the four small `node-*.mjs` polyfill shims. They're inert in normal
use (nothing calls the trace/debug path from this app) but must be present
or the module fails to load.

## How to update

Bump `PKGVER` below to the desired versions, then re-run for **every**
package in the set (the peer-exclusion list must include every *other*
package, never the package being built itself — see the warning below):

```bash
declare -A PKGVER=(
  ["@codemirror/state"]="6.7.1"
  ["@codemirror/view"]="6.43.6"
  ["@codemirror/language"]="6.12.4"
  ["@codemirror/commands"]="6.10.4"
  ["@codemirror/lang-python"]="6.2.1"
  ["@lezer/common"]="1.5.2"
  ["@lezer/highlight"]="1.2.3"
  ["@lezer/lr"]="1.4.10"
)
ALL_PKGS=("@codemirror/state" "@codemirror/view" "@codemirror/language" \
          "@codemirror/commands" "@codemirror/lang-python" \
          "@lezer/common" "@lezer/highlight" "@lezer/lr")

for pkg in "${ALL_PKGS[@]}"; do
  ver="${PKGVER[$pkg]}"
  peers=""
  for p2 in "${ALL_PKGS[@]}"; do
    [ "$p2" != "$pkg" ] && peers="${peers}${peers:+,}${p2}"
  done
  fname=$(echo "$pkg" | sed 's/@//;s/\//-/')
  shim=$(curl -s "https://esm.sh/${pkg}@${ver}?bundle&external=${peers}")
  # IMPORTANT: grep the "export * from" line specifically, not just the
  # first quoted path in the response -- @lezer/lr's shim has an earlier
  # `import "/node/process.mjs";` side-effect line that a naive `head -1`
  # extraction picks up by mistake, silently vendoring esm.sh's Node
  # `process` polyfill instead of the real package (this happened once
  # during development and produced a file that imports successfully but
  # exports none of the real API -- broke with a confusing "does not
  # provide an export named ..." error three modules downstream).
  real=$(echo "$shim" | grep 'export \* from' | grep -oE '"/[^"]*"' | tr -d '"')
  curl -s "https://esm.sh${real}" -o "${fname}@${ver}.mjs"
done
curl -s "https://esm.sh/node/events.mjs" -o node-events.mjs
curl -s "https://esm.sh/node/tty.mjs" -o node-tty.mjs
curl -s "https://esm.sh/node/async_hooks.mjs" -o node-async_hooks.mjs
curl -s "https://esm.sh/node/process.mjs" -o node-process.mjs
```

**Warning — the self-exclusion bug.** An earlier draft of this recipe built
one shared `peers` string (including every package, including whichever one
was currently being built) and reused it for all 8 fetches. Passing a
package's own name in its *own* `external` list makes esm.sh return a
degenerate module instead of the real bundle — for `@lezer/lr` specifically
it silently resolved to something resembling Node's `process` polyfill, and
only failed loudly three modules downstream (`@codemirror/lang-python`
importing `LRParser` from it). The loop above builds a fresh `peers` string
per package with `[ "$p2" != "$pkg" ]` for exactly this reason — don't
"simplify" it back to one shared string.

After regenerating: update the version table above, update the filenames
referenced in `index.html`'s import map and `web/server.py`'s
`_STATIC_VENDOR_ROUTES`, bump `pyproject.toml` if the file *set* changed
(not just versions), and re-run the manual browser check (mount the editor,
confirm Python syntax highlighting renders, check the browser console for
import errors) before committing — this bundling scheme has no automated
CI check for cross-bundle identity mismatches.
