# Changelog

## [Unreleased]

<!--
Единственный актуальный «Unreleased» — этот, вверху файла. Записи вида
`## [unreleased] / <дата>` и `## [Unreleased] — <месяц> — …` ниже по файлу —
исторические до-релизные снимки ранних спринтов, а не текущий незарелиженный
раздел. Не путать с этим блоком.
-->

### Fixed
- Web UI accessibility for grading results (issue #298, WCAG 2.1 AA):
  results were announced silently to assistive tech. Added a polite
  `aria-live` region (`#result-announce`) that speaks a one-line outcome
  summary on completion ("task_1.py — OK, 12 из 12" / "Бенчмарк завершён: N
  решений" / error/cancel text) — not on every progress tick, to avoid
  noise. The progress bar now carries `role="progressbar"` +
  `aria-valuemin/valuemax/valuenow`, and focus moves to the results panel
  when a run finishes. Verdict badges already conveyed meaning as text (not
  colour alone) — now pinned by a regression test. The dark-theme
  `--color-warning` token was lightened from `#bb653b` to `#d98a5c` so the
  SLOWER verdict / warning text clears the 4.5:1 contrast minimum on dark
  surfaces (was ~3.5–4.4:1); light theme already passed and is unchanged.

### Changed
- Mode 1 (single-file correctness) in the web UI no longer saves to disk
  before grading (issue #297). "Проверить" now runs one
  `POST /api/v1/runs` with `mode="tests"` and the editor's `code` in the
  request body — the solution executes from a temp file and the target file
  on disk is never touched, closing the save→grade race two windows on the
  same folder could hit. Saving is now a separate explicit "Сохранить"
  button (`POST /api/save-solution`), with an unsaved-changes indicator on
  the editor and minimal optimistic locking: `save_solution`/`read_source`
  return the file `mtime`, and saving over an existing file whose on-disk
  `mtime` drifted from the loaded baseline is refused with
  `{"ok": false, "conflict": true, message_id: file_changed_on_disk}` (a
  second save overwrites). `POST /api/v1/runs` accepts `mode="tests"` in
  addition to `bench`/`microbench`. `GET /api/grade` is unchanged and still
  serves mode 2 (folder grading).

### Added
- Curated WA hint for non-UTF-8 output (issue #301): a solution that writes
  raw bytes to stdout (`sys.stdout.buffer.write(b"\xff...")`) is decoded with
  `errors="replace"`, so its diff shows `�` (U+FFFD) with no explanation.
  `web/viewmodels._wa_suggestion` now detects `�` in the actual output and
  returns a `message_id="output_invalid_utf8"` hint (ru/en) pointing at the
  likely cause (printing raw bytes / wrong encoding), taking priority over
  the trailing-whitespace hint. The runner's decode strategy is unchanged
  (still `errors="replace"`, a deliberate non-goal).
- Bundled glossary base (issue #326): 581 cards imported from Glossary-Python
  now ship in the wheel at `stepik_grader/glossary/data/*.json` (one file per
  colour-group). The web "Глоссарий" section serves them as the zero-config
  default when `CONFIG.glossary_store` is unset, turning the section from a
  ~28-exception fallback into a full reference; the compact `core/glossary.py`
  fallback remains for when the bundled dir is absent/broken. A reproducible,
  offline importer (`scripts/import_glossary_python.py`) does the one-time
  conversion (`name→title`, `docs→docs_url`, `version` null→`""`, exception
  ids lowercased to match the anchor convention). `stdlib` coverage
  (`python -m stepik_grader.glossary.coverage --cards …`) rises from 0 to
  ~190+ covered (builtins 94%).
- `GlossaryCard` gains four optional fields (issue #325): `syntax`
  (signature/usage template), `docs_url` (link to official docs.python.org;
  `docs` accepted as an alias, mirroring `hint`→`summary`), `version`
  (minimum Python version, e.g. `3.10`; JSON `null` normalises to `""`), and
  `subcat` (subcategory within `section`, for the glossary section's
  filters). All are backward-compatible — existing JSON bases without them
  still load — and the web glossary card now renders syntax, examples, a
  Python-version badge and a docs.python.org link. Foundation for the
  glossary content epic (import from Glossary-Python #326, redesign #329).
- `POST /api/v1/runs` job status gets a fifth, additive value: `"cancelled"`
  (issue #296), alongside `queued`/`running`/`done`/`error`. Previously a
  user-cancelled job reported `status="error"` with
  `message_id="run_cancelled"` — semantically a cancellation is not a
  failure of the solution or the grader, and future clients (server mode,
  an IDE extension) would otherwise have to parse `message_id` just to tell
  "user changed their mind" apart from "grader crashed" (e.g. to decide
  whether a retry makes sense — it does for `error`, never for
  `cancelled`). `message_id="run_cancelled"` is still set on the terminal
  status either way. The web UI now renders a cancelled run with a neutral
  tone (`.msg-neutral`) instead of the error-red `.msg`. Landed before the
  `/api/v1/*` contract freeze (issue #156) while the change is still cheap.

### Fixed
- Empty/missing `tests/` no longer reported as `FAIL 0/0` (issue #299): both
  the web `grade_path()` row status and the CLI correctness table
  (`core/reporter._correctness_status`) now return `"NO TESTS"` when
  `total == 0` — matching the contract already documented in
  `docs/result-contract.md`, which the code had drifted from. Previously a
  solution folder with an existing-but-empty `tests/` dir looked identical to
  a genuinely wrong solution.
- `core/runner._measure_peak_memory`'s "peak memory measurement unreliable"
  `UserWarning` no longer floods the console during batch grading (mode 2)
  or a long-running `--serve`: the message used to interpolate the child
  `pid`, so every occurrence was a distinct string that defeated Python's
  own "default" warning filter dedup (which keys on the exact rendered
  text) — one warning per trivially-fast solution (`print(1)` and similar,
  common Stepik exercises) instead of once per process. Message text is now
  constant; the stdlib filter shows it once per interpreter session.

### Docs
- `docs/README.md` navigation index now lists `docs/changelog-archive.md`
  (issue #300) — it existed in `docs/` since the CHANGELOG split but was
  never added to the index.
- New CI guardrail (`scripts/check_docs_guardrails.py`): every `docs/*.md`
  file must be referenced from `docs/README.md`, or the check fails — makes
  the class of drift behind issue #300 impossible to reintroduce silently.
  `docs/adr/*.md` is exempt (cataloged by its own `docs/adr/README.md` index).
- `core/sandbox/__init__.py`, `core/sandbox/_linux.py`, `core/runner.py`
  docstrings no longer describe `nsjail` as an implemented Linux fallback
  backend (issue #293): `bwrap` is the only Linux `--sandbox` backend in this
  MVP, matching what `SECURITY.md`/`docs/server-mode.md` already documented
  correctly — only the code docstrings had drifted.

### CI
- Per-OS coverage margin (issue #294): each CI matrix OS job's own
  `--cov-fail-under=85` gate used to count the OTHER two platforms'
  `core/sandbox/` backend files as permanently uncovered (structurally
  unreachable on that OS), leaving as little as ~1.1pp margin on ubuntu. New
  `scripts/generate_ci_coveragerc.py` generates a CI-only `.coveragerc.ci`
  that additionally omits, for each job, only the backend files unreachable
  on its own OS; the `coverage-combine` cross-OS aggregate job (
  `--fail-under=90`) is untouched and still sees every file from whichever
  job(s) can actually exercise it — no file is omitted everywhere at once.
  `fail_under = 85` itself is unchanged; local `pytest` runs are unaffected
  (this mechanism is CI-only).

### Docs
- `docs/installation.md`: new troubleshooting note for `stepik-grader ...`
  failing with `ModuleNotFoundError: No module named 'stepik_grader'` even
  though the command itself resolves — root cause is a stale global editable
  install (commonly predating the project's src-layout migration, issue #35)
  shadowing the working `.venv` install. Covers diagnosis
  (`Get-Command`/`which stepik-grader`) and cleanup (`pip uninstall`, plus
  manual removal of orphaned `.dist-info`/`.pth`/`_finder.py` files when pip
  can't find a RECORD to uninstall from).

### Refactored
- CodeMirror 6 frontend vendoring (issue #295): the 8 separate esm.sh
  per-package bundles + import map + 4 Node.js browser-compat polyfill files
  (issue #265) are replaced by a single self-contained esbuild bundle,
  `static/vendor/codemirror-bundle@6.mjs`. `app.js` now imports it directly
  by URL instead of via bare specifiers resolved through an import map.
  Building from the real npm packages (not esm.sh's per-package re-bundles)
  lets tree-shaking eliminate the optional debug/tracing code path that
  needed the Node shims in the first place — none are needed anymore
  (verified: no `events`/`tty`/`process`/`async_hooks` references in the
  output). ~12 HTTP requests for the editor down to 1; bundle is smaller than
  the sum of the files it replaces. No build tooling added to the repo or CI
  — the bundle is built once outside the repo and committed as a finished
  artifact, same philosophy as before (see `static/vendor/VERSIONS.md` for
  the reproducible build recipe and full pinned dependency list, now
  including previously-undocumented transitive deps `@codemirror/autocomplete`,
  `@lezer/python`, `style-mod`, `w3c-keyname`, `crelt`).

## [1.7.0] - 2026-07-12

### Added
- Opt-in OS-level sandboxed execution (issue #266): new `--sandbox` flag
  routes `--mode 1/2/3/4` through a new `SandboxRunner` (`core/sandbox/`)
  instead of the plain-subprocess `LocalRunner` — bubblewrap (`bwrap`) on
  Linux, `sandbox-exec` (Seatbelt) on macOS, Job Objects (ctypes, no
  `pywin32`) on Windows. Backend is selected once at CLI startup by OS; if
  unavailable (missing `bwrap`/`sandbox-exec`, or the Job Object API check
  fails), the command exits with a clear error — never a silent fallback to
  `LocalRunner`. Guarantees deliberately differ by OS (documented in
  `SECURITY.md`, not a bug): Linux gets full kernel-enforced isolation
  (network/fs/memory/CPU/process-count via namespaces + `RLIMIT_*`); macOS
  isolates network/fs/CPU via Seatbelt but approximates memory via psutil
  polling (`RLIMIT_AS` doesn't work on Darwin, bpo-34602) with a weaker
  process-count budget (no user-namespace equivalent); Windows gets
  kernel-enforced memory/CPU/process-count via Job Objects (memory limit is
  commit-charge-based and in practice faster than POSIX `RLIMIT_AS`) but has
  **no network isolation** and only soft (`cwd`-relative) filesystem
  containment in this MVP — both named, not silent, gaps (AppContainer and
  `CreateProcessAsUser`+restricted-token respectively were judged
  disproportionately complex/risky for a first cut). New additive verdict
  `SANDBOX_VIOLATION` (`RunOutcome.sandbox_violation`, additive to
  AC/WA/RE/TLE/CANCELLED) fires only for violations the runner proactively
  detects and kills itself — memory (RSS/commit threshold), `output_size`
  (stdout+stderr over `sandbox_max_output_bytes`), `cpu` (`SIGXCPU` on
  POSIX); network/filesystem/process-count violations are rejected by the
  kernel *inside* the sandbox and correctly surface as an ordinary `RE`
  instead (the runner doesn't parse a child's traceback to relabel it). New
  `grader_core.set_runner()` fulfills the injection point the codebase had
  already reserved for this. Three new `[tool.stepik-grader]` quota fields:
  `sandbox_max_cpu_seconds` (10.0), `sandbox_max_processes` (32),
  `sandbox_max_output_bytes` (10 MiB). Known MVP limitation on all three
  platforms: only the interpreter + stdlib are bound into the sandbox, not
  the grader's own venv site-packages, so solutions depending on third-party
  packages aren't supported under `--sandbox`; Linux's nsjail fallback
  (mentioned in the original design) also isn't implemented, `bwrap` is the
  only Linux backend. New `tests/test_sandbox_runner.py`: platform-
  independent unit tests plus `pytest.mark.skipif`-gated real-backend
  escape-matrix tests (write outside tmp, network, fork bomb, memory/output
  overruns, TLE) and a golden AC/RE/TLE comparison against `LocalRunner`,
  each executing for real only on its native OS.
- Opt-in local run statistics (issue #268): `--stats`/`--no-stats` (or
  `[tool.stepik-grader] record_stats = true`) appends one JSON-Lines record
  per grading run — mode, verdict tallies (AC/WA/RE/TLE for modes 1/2,
  SIMILAR/SLOWER/MUCH_SLOWER/ERR for modes 3/4), OS, and total time — to a
  new `.grader_stats.jsonl` in the current directory (added to `.gitignore`
  and CLAUDE.md's forbidden-commit list). No network calls anywhere — the
  file never leaves the machine, and off by default. New `--stats-summary`
  prints an aggregated view (total runs, by mode, by OS, by verdict, total
  time) via a new `core/reporter.print_stats_summary()`, rich table with a
  plain-text fallback like the existing correctness/benchmark tables. New
  leaf module `core/stats.py`: JSON Lines (not a single JSON object like
  `GraderCache`, issue #56) so an interrupted write can only lose the last
  line, not corrupt the whole file; size-based rotation keeps the newest
  half of lines past 1 MiB; both `record_run()`/`read_summary()` are
  best-effort and tolerate a missing/corrupt file or individual malformed
  lines, same principle as `GraderCache`. The interactive menu resolves
  `CONFIG.record_stats` directly (no argparse there) at all 4 mode choices,
  unlike the cache toggle which the menu never exposes today.
- `docs/configuration.md` documents the new `record_stats` config field
  with an explicit privacy paragraph ("data never leaves the machine").

### Changed
- **Breaking (issue #73):** public API functions/methods that take or return
  a filesystem path now use `pathlib.Path` instead of `str`, across
  `core/test_loader.py` (`resolve_test_dir`, `find_all_solution_files`,
  `collect_grouped_files`, `load_text_lines`, `load_test_cases`),
  `core/grader_core.py` (`run_single_test`, `run_tests`, `run_benchmark`,
  `run_microbench_mode`), `core/reporter.py` (table formatters),
  `core/cache.py` (`GraderCache`, `hash_solution`, `hash_tests`),
  `core/runner.py` (`RunSpec.path`), the CLI (`--file`/`--dir`/`--root` now
  parse to `Path` via argparse), and the `web/` adapter layer
  (`grade_path`/`grade_benchmark`/`grade_microbench`/`list_solutions`/
  `read_source`/`save_solution`/`submit_job`/glossary store paths). External
  code calling these with a bare `str` must now pass a `Path` (or wrap with
  `pathlib.Path(...)`) — the functions no longer defensively re-wrap `str`
  input. JSON-facing response fields (e.g. `"base"`, `"path"`, `"file"`) are
  unaffected — those remain plain strings. `grader.py`'s `__all__` also gains
  `resolve_test_dir`, closing a pre-existing facade gap (it was reachable as
  `grader.resolve_test_dir` via the wildcard re-export but wasn't listed).

### Docs
- Pre-release accuracy audit ahead of v1.7.0: `docs/project-structure.md`
  and `docs/architecture.md` now mention `core/sandbox/` (issue #266),
  `web/runs.py`/`web/i18n.py` (issue #262/#264), `core/stats.py` (issue
  #268), and `core/i18n.py`, plus the DAG/layer diagrams for all of them —
  `architecture.md` previously still called `SandboxRunner` "future work
  (issue #157)" after it had already shipped. `docs/configuration.md`
  gained the missing `glossary_store`/`glossary_missing_queue` rows.
  `docs/grader-workflow.md` gained a `--stats`/`--stats-summary` section
  (previously undocumented outside `configuration.md`).
  `docs/installation.md`'s pinned `ruff>=0.4` corrected to `>=0.15.19`
  (matching `pyproject.toml`). `docs/server-mode.md`'s unconditional "network
  unreachable" `SandboxRunner` guarantee now flags the Windows exception
  inline, not just in a separate paragraph below it. `SECURITY.md` gained a
  dedicated section naming the Host/Origin guard and path-confinement
  (`--root`/`--no-root-confinement`) mechanisms explicitly, cross-linked to
  `docs/api.md`. `CLAUDE.md`'s metrics table test count corrected
  (967 → 784, matching `CHECKPOINT.md`/`docs/versions.md` for the same
  v1.6.0 snapshot) and Python 3.14 now marked experimental/ubuntu-only
  there and in the README badge, matching `docs/grader-workflow.md`'s
  existing wording.
- New `docs/api.md` (issue #267): canonical HTTP API reference for
  `--serve` — every endpoint's method/path, params, limits, response codes,
  and a curl example, sourced from a full audit of `web/server.py`.
  `_Handler`'s docstring is trimmed to a short pointer instead of
  duplicating the endpoint list.
- `docs/web-mvp.md` split into `docs/web-current.md` (what's actually
  implemented) and `docs/web-design.md` (design-only/deferred/rejected
  ideas, including the `## MVP vs v1 vs later` status tracker) — the old
  file mixed both, making it hard for a new contributor to tell what's
  real without reading the code. All ~15 files with markdown links to the
  old file repointed to whichever new file actually covers that section.
- `CHANGELOG.md`'s 10 pre-versioning "pseudo-Unreleased" snapshots (dated
  June 2026, format `## [unreleased] / <date>`, predating git-tag-based
  versioning — issue #162/#183) moved verbatim to new
  `docs/changelog-archive.md`, cross-linked with `docs/history.md` (same
  period, different granularity/language). Live `CHANGELOG.md` now holds
  only the current `[Unreleased]` + real releases `[1.1.0]`...`[1.6.0]`.
- New troubleshooting section in `docs/installation.md` (issue #270):
  `test_pytest_plugin.py` failing with `unrecognized arguments:
  --grader-mode`, and `test_packaging.py::test_license_is_mit_in_metadata`
  failing with `License-Expression: None`, share the same root cause — a
  stale editable install whose `.dist-info/entry_points.txt` predates
  `pyproject.toml` changes to `license`/`entry-points` — fixed by
  `pip install -e ".[dev]" --force-reinstall --no-deps`. Also documents the
  unrelated `PermissionError` on a stale `%TEMP%\pytest-of-<user>` from a
  prior pytest run under different Windows permissions, with the
  `--basetemp` workaround. No code changes — all three findings from the
  2026-07-10 audit were confirmed non-reproducing once the install was
  refreshed (verified live: full suite green, `--grader-mode` plugin
  resolves correctly, with both default and deeply-nested custom
  `--basetemp` paths).
- `docs/versions.md`'s fork-vs-original comparison table condensed from
  ~24 single-feature rows down to 5 grouped-by-theme rows (correctness,
  benchmark/microbench, Stepik integration, web UI/IDE, engineering
  baseline) — the granular list had grown hard to scan and, worse, had
  drifted: it never mentioned the local web UI (`--serve`) or IDE
  integration at all. Now covers both, and names IDE integration
  correctly as VS Code (`--init-vscode`) **and** PyCharm (documented
  External Tool recipe, `docs/grader-workflow.md § Интеграция с IDE`) —
  a prior mention of only VS Code was an omission fixed here. Per-item
  detail is unchanged in `CHANGELOG.md`/`docs/history.md`, linked from
  the section for anyone who wants it. The `v1.4.0` row in the
  version-evolution table below got the same PyCharm correction.

### Fixed
- README badges (`Version`, `Coverage (ubuntu)`, `Coverage (all OS
  combined)`) now pass `&cacheSeconds=300` to shields.io's endpoint-badge
  API — the shortest TTL shields.io honors. Without it, GitHub's camo image
  proxy and shields.io's own edge cache could each hold a stale render for
  hours after the underlying `.github/badges/*.json` changed (as happened
  right after #289 landed), with no way for a reader to tell the badge was
  just out of date rather than the fix not having worked.
- CI (issue #289): the two coverage badges (`coverage.json`, single-OS
  ubuntu view; `coverage-combined.json`, cross-OS combined, issue #283)
  rendered with an identical `"coverage"` label baked into the badge image
  itself — shields.io draws `label` on the picture, not just in markdown
  alt-text, so both badges looked the same except for the percentage.
  `generate_coverage_badge.py` gained a `--label` flag (default `"coverage"`
  for backward compat); CI now passes `"coverage (ubuntu)"` and
  `"coverage (all OS)"` respectively.
- CI (issue #286): both badge-update steps (`test` job and `coverage-combine`
  job, issue #283) used plain `git diff --quiet -- .github/badges/` to decide
  whether to commit — which only looks at already-tracked files. This never
  once committed `coverage-combined.json` (a brand-new file as of #283): the
  script correctly computed the percentage every run, but the untracked file
  never showed up as a "change", so the commit step always took the "Badges
  unchanged" branch. Left the README's second coverage badge pointing at a
  file that was never actually in the repo (404). Fixed by `git add` before
  the check and diffing `--cached` instead, in both steps.

### Internal
- CI: the `Update badges (main only)` step now retries (up to 3 attempts)
  instead of failing the job outright when two pushes to `main` land close
  together. Two workflow runs racing to commit+push `.github/badges/*.json`
  is harmless in itself (the loser's `git pull --rebase` conflict is caught
  *before* `push`, so `main` never actually gets corrupted), but it did leave
  a spurious red CI run. On conflict, the step now aborts the rebase, resets
  to fresh `origin/main`, and regenerates the badges from that HEAD — which
  typically now matches what the other run already pushed, so the retry
  cleanly resolves as "Badges unchanged." A final failure after 3 attempts
  is a `::warning::`, not a job failure — a later push will catch the
  badges up regardless.
- CI: cross-OS combined coverage (issue #283). Since issue #266
  (`SandboxRunner`), `core/sandbox/_linux.py`/`_macos.py`/`_windows.py` are
  OS-specific backends — any single CI job/local machine only ever exercises
  one of the three, permanently reading the other two as 0% and capping
  single-job coverage at ~86-90% regardless of test quality (this is what
  dropped the badge from ~95% to 86.1% right after #266/#281 merged, not a
  real regression). New `coverage-combine` job merges the three OS matrix
  jobs' `.coverage` data (`coverage combine`, with `[tool.coverage.paths]`
  aliasing in `pyproject.toml` to reconcile each OS's different absolute
  checkout path) into one report gated at `--fail-under=90`, separate from
  the existing per-OS `fail_under = 85` in `pyproject.toml` (left unchanged
  — raising it globally would make every contributor's single-OS local
  `pytest` run fail on the two backends their machine can never see).
  README now shows both numbers as two distinct badges — single-OS
  (`coverage.json`, as before) and cross-OS combined (new
  `coverage-combined.json`) — rather than collapsing to one figure that
  would either overstate or understate reality.

### Refactored
- Web API `message` strings are now rendered server-side from a locale
  catalog instead of being Russian literals baked into `web/viewmodels.py`/
  `web/server.py` (issue #264). Every error/status response that carries a
  human-readable `message` gained two sibling fields: `message_id` (the
  catalog key, e.g. `"path_not_found"`) and `message_params` (the dict of
  values interpolated into it — empty if none). `message` itself is
  unchanged for existing callers: default locale is still `ru`, rendered
  byte-for-byte identical to the old hardcoded text. New `web/i18n.py`
  (`render_message()`/`message_fields()`/`resolve_lang()`) is a thin
  web-layer renderer built on top of `core/i18n.load_locale_messages()`
  (issue #144) — `core/i18n.py` itself stays a stdlib-only leaf, per
  CLAUDE.md's architectural invariant; the catalog and `message_params`
  interpolation are an application-layer concern, not core infra.
  `core/locales/ru.json`/`en.json` (previously empty placeholders from
  issue #144) are now populated with the actual web-layer message strings
  and their English translations. Locale is selected via a new `?lang=`
  query parameter on `/api/*` GET/POST endpoints (`ru`/`en`; anything else,
  or the param's absence, falls back to `ru` — no UX change for existing
  callers). New CI-wired guardrail `scripts/check_locale_guardrails.py`
  (modeled on `scripts/check_docs_guardrails.py`) checks that every
  `message_id` referenced in `web/*.py` exists in `ru.json`, and that
  `ru.json`/`en.json` have exactly the same key set. New
  `tests/test_i18n_guardrails.py` AST-parses `web/viewmodels.py`/
  `web/server.py` and fails on any string literal containing Cyrillic
  characters outside docstrings — the regression guard for "no hardcoded
  Russian message text left in the web layer." `docs/result-contract.md`'s
  Run result field table documents `message_id`/`message_params`.

### Added
- Mode 1's code editor (`--serve`) is now CodeMirror 6 instead of a plain
  `<textarea>` (issue #265): Python syntax highlighting, line numbers, and
  Tab-to-indent, themed via the existing `app.css` design tokens (follows
  light/dark automatically — no separate CodeMirror theme object per mode,
  just `var(--color-*)` references in one `EditorView.theme()`). Vendored,
  not CDN-loaded (same "everything offline" rule as issue #260's fonts):
  8 CodeMirror/Lezer sub-packages (`@codemirror/state`/`view`/`language`/
  `commands`/`lang-python`, `@lezer/common`/`highlight`/`lr`) plus 4 tiny
  Node browser-compat shims `@lezer/lr` needs for an unused debug path,
  each fetched pre-built from esm.sh with every *other* package in the set
  marked `external` so they all share one copy of `@codemirror/state`/
  `view`/`language` — CodeMirror's extension system works by object
  identity, so duplicate copies would have silently broken cross-package
  extensions. New `static/vendor/` (`LICENSE`, `VERSIONS.md` with the exact
  fetch recipe and a note on a self-exclusion bug hit once during
  development), wired into `index.html` via a `<script type="importmap">`
  and `web/server.py`'s static routes; `app.js` is now `type="module"`
  (no inline scripts/`on*=` handlers depended on it staying classic).
  `pyproject.toml`'s `package-data` gained `web/static/vendor/*`. The old
  `$("#solution-editor").value` read/write call sites became a small
  `getEditorCode()`/`setEditorCode()` API backed by CodeMirror's document
  state; focus visibility (accessibility) uses `#solution-editor:focus-
  within` since the actual focusable node is CodeMirror's own nested
  `.cm-content`, not the outer container `:focus` never fires on directly.
  `tests/e2e/test_journeys.py`'s mode-1 edit/save/run journey (issue #263)
  updated to type into `.cm-content` via real keyboard events instead of
  `.fill()`/`.input_value()` on a textarea — still green.
- Async job model for bench/microbench in `--serve` (issue #262): new
  `POST /api/v1/runs` (body `{"path"|"code","mode","params"}`) queues a job
  and returns `202 {"run_id","status":"queued"}` immediately instead of
  blocking the request for the whole benchmark; `GET /api/v1/runs/{id}`
  polls `{"status":"queued"|"running"|"done"|"error","progress":
  {"done","total"},"result"}`; `POST /api/v1/runs/{id}/cancel` is a
  best-effort cancel that actually terminates the running child process
  (not just flips a status flag). New `web/runs.py` — in-memory job
  registry (`threading.Lock`-guarded dict) + `ThreadPoolExecutor` (size
  configurable via new `GraderConfig.job_workers`, default 2), lazy
  TTL-based cleanup of finished jobs (15 min) on each registry access, no
  extra background thread. `core/runner.py`'s `LocalRunner.run()` gained an
  additive `RunSpec.cancel_event: threading.Event | None` — `None` (CLI,
  sync `/api/grade`) keeps the exact prior single blocking
  `proc.communicate()` call; when set, a 100ms poll loop with concurrent
  stdout/stderr drain threads checks it and kills the child early
  (`RunOutcome.cancelled`). `core/grader_core.py`'s `run_tests`/
  `run_benchmark`/`run_microbench_mode`/`run_single_test` gained matching
  optional `progress_callback`/`cancel_event` kwargs (both default `None`,
  CLI behavior unchanged) and a new additive case verdict `CANCELLED`
  (distinct from `TLE` — a cancelled run is not "your solution timed
  out"). `web/viewmodels.py`'s `grade_benchmark`/`grade_microbench` forward
  both through their per-solution loop, plus a new `estimate_run_count()`
  helper that cheaply pre-computes a job's total step count (file I/O only,
  no subprocess) for the progress bar's denominator. `POST /api/v1/runs`
  also accepts an optional `code` field (writes to a temp `.py` file next
  to `path`, graded instead of what's on disk — the same "editable code
  window without saving" scenario mode 1's `/api/save-solution` already
  supports, just for bench/microbench). Frontend (`static/app.js`): modes
  3/4 now POST + poll (600ms) with a new progress bar (`#bar`) and Cancel
  button (`#cancel-run`) instead of a single blocking fetch; modes 1/2
  (plain tests) are unaffected, still on sync `/api/grade`. `/api/grade`
  itself is unchanged and documented as deprecated (not removed) for
  bench/microbench in `server.py`'s docstrings — see
  `docs/server-mode.md § Контракт API удалённого исполнения` for how this
  local MVP intentionally deviates from that section's speculative future
  network-API contract (inlined `result`, no `failed` status). New tests:
  `tests/test_runs.py` (job-lifecycle, no HTTP), `tests/test_web.py`'s
  `TestRunsApi*` (golden comparison against sync `/api/grade`, real-process
  cancellation via a PID-file + `psutil.pid_exists()` check, two concurrent
  jobs not mixing results, path confinement/input validation/Host-guard
  reuse), plus new `cancel_event` scenarios in `tests/test_runner.py`/
  `tests/test_grader_mock.py`.
- Playwright e2e smoke suite for the web UI, `tests/e2e/` (issue #263): 4
  user journeys against a real `--serve` instance (mode 2 folder grading +
  detail tab, mode 1 file picker with an editable code window + save + run,
  glossary search + card, command palette open/execute) plus an XSS
  regression test asserting `app.js`'s `esc()` escaping (hardened in issue
  #214) neither executes an injected `<img onerror=...>` payload nor renders
  it as a live element anywhere across its ~41 `innerHTML` call sites. New
  opt-in dev-extra `[project.optional-dependencies].e2e` (`playwright>=1.40`)
  in `pyproject.toml` — **not** a runtime dependency, only installed via
  `pip install -e ".[e2e]"` + `playwright install chromium`; the issue itself
  explicitly authorizes this dev-only addition. `tests/e2e/` is excluded from
  the default `pytest`/`pytest tests/` sweep via a new `norecursedirs`
  pytest.ini_options entry (explicit `pytest tests/e2e/` still collects it).
  New separate `e2e` CI job (Linux-only, `.github/workflows/ci.yml`) with
  Playwright browser caching, deliberately not folded into the main `test`
  matrix — issue #263 explicitly authorizes touching the workflow for this.
  README/CONTRIBUTING.md document how to run the suite locally.
- `--serve` gained workspace root confinement (issue #261): all request
  paths (`/api/grade`, `/api/source`, `/api/solutions`, `/api/save-solution`
  — both `folder` and an optional target `path`) are now resolved and
  checked against a server workspace (new `_GraderServer` — a
  `ThreadingHTTPServer` subclass carrying `workspace`/`confine`, and
  `_resolve_within_root()`/`_Handler._confined_path()` in `server.py`) —
  `Path.resolve()` runs before the containment check, so `../` traversal
  and symlinks pointing outside the workspace are caught, not just literal
  absolute paths. A request outside the workspace gets `403`
  (`{"kind": "error", "message": ...}`) instead of silently reading/writing
  anywhere on disk (previously confirmed live: `/api/source?path=/etc/
  hostname` read arbitrary files). New CLI flags: `--root <dir>` sets the
  workspace (default: cwd at `--serve` launch, also used for
  `__DEFAULT_PATH__` in `index.html`, replacing the old raw `os.getcwd()`);
  `--no-root-confinement` is an explicit opt-out back to the old
  unconfined behavior, reflected in the server's startup message.
  `/api/download`'s `root` (where to download a task *to*) is a separate
  concern and isn't confined by this change.

### Changed
- Web UI fonts (JetBrains Mono/Inter) are now vendored locally instead of
  loaded from the Google Fonts CDN (`fonts.googleapis.com`/
  `fonts.gstatic.com`), issue #260: `static/index.html`'s CDN `<link>`s are
  gone, `app.css` declares local `@font-face` rules (latin + cyrillic
  subsets, one variable woff2 file per subset covering the full weight
  range each family needs — Google itself serves the same file for every
  requested static weight of these two families) pointing at new
  `static/fonts/*.woff2`, served via a new `_STATIC_BINARY_ROUTES` map in
  `server.py` (`Content-Type: font/woff2`). Fixes the contradiction with
  the module's own "no external dependencies" docstring claim, restores a
  working offline UI (previously degraded to fallback fonts with no
  network), and stops leaking the fact that the tool is running to a
  third-party host on every page load. Fonts are OFL 1.1 (`static/fonts/
  LICENSE`); `pyproject.toml` `package-data` gained a `web/static/fonts/*`
  entry (`web/static/*` doesn't recurse into subdirectories).

### Fixed
- Web API had no limits on request size or numeric query params (issue
  #259): a `POST` body of unbounded size was read fully into memory before
  any validation, and `GET /api/grade?mode=bench&repeats=999999999` (or
  `mode=microbench&number=...`) passed the raw value straight through to
  the benchmark runner — a single request could burn arbitrary CPU/memory
  (local DoS). `do_POST` now rejects a `Content-Length` over 1 MiB with
  `413` (draining a bounded amount of the still-incoming body first —
  otherwise Windows resets the connection before the client can read the
  413 response) and a missing/negative/non-numeric `Content-Length` with
  `400`; `repeats`/`number` are clamped to `[1, 1000]`/`[1, 1_000_000]` via
  a new `_clamp()` helper instead of passed through unbounded.
- `config.py::load_config()` resolved `pyproject.toml` relative to the
  installed package's own `__file__` (`src/stepik_grader/` → repo root),
  so a `pipx`/wheel install pointed inside the venv where no
  `pyproject.toml` exists — `[tool.stepik-grader]` was silently never
  read and every user got hardcoded defaults regardless of their config.
  `load_config()` now resolves the path via a new
  `_resolve_pyproject_path()`: `STEPIK_GRADER_CONFIG` env override (if it
  points at an existing file) → search upward from `cwd` (pip/ruff
  pattern, new `_find_pyproject()`) → legacy `__file__`-relative fallback
  (preserves behavior when tests run from the repo root) → defaults. An
  invalid `STEPIK_GRADER_CONFIG` value no longer raises — resolution just
  continues to the next source (issue #258).

### Added
- Editable code window for mode 1 in the web UI (issue #125): the
  file-picker panel's read-only source preview is now a persistent,
  editable textarea. Picking an existing solution loads its code into it
  for editing; leaving nothing picked lets you type a new solution from
  scratch. Running now saves the editor's content to disk first — to the
  picked file if one was selected, otherwise to a new file whose name
  extends the folder's existing `task<N>_<M>.py` numbering series (or
  starts at `task_1.py`) — via new `web/viewmodels.py::save_solution()`
  and `POST /api/save-solution`, then grades the saved path as before.
- Microbench (mode 4) in the web UI (issue #187): the "Режим 4 · Microbench"
  button is no longer a disabled placeholder — it runs the real
  `timeit`-based microbenchmark with a calls-per-run profile selector
  (fast/normal/thorough/deep/hard/custom, mirroring `cli/interactive.py`'s
  `_MICRO_PROFILES`) and a results table (Min/Median/Mean/Max/StdDev in µs,
  relative %, verdict, Py-heap). New `web/viewmodels.py::grade_microbench()`
  groups solutions by subfolder via `core/test_loader.py::collect_grouped_files`
  before calling `core/grader_core.py::run_microbench_mode` once per group —
  required because that function ranks all files passed to a single call
  against each other, so per-file calls (like `grade_benchmark`'s) would make
  every result trivially "SIMILAR". A folder with more than one solution
  group only benchmarks the first (sorted) group in this MVP; the rest are
  named in an `other_groups` hint above the table. New `mode=microbench`
  branch in `server.py`'s `/api/grade` routing (`number=` query param).
- Downloader workflow in the web UI (issue #186): a new, full sidebar
  section "Загрузчик задач" (symmetric with "Проверка решений"/"Глоссарий" —
  the owner confirmed a dedicated section over the design doc's original
  "workflow-block inside Проверка решений" plan) lets you paste a Stepik
  step URL and download the task + tests without leaving the browser.
  `web/downloader_adapter.py::download_task()` is a thin adapter over
  `downloader.py::process_step_url` (no download logic duplicated); auth
  goes through a new `core/oauth_flow.try_create_session_without_browser()`,
  which only ever uses a valid token or a `refresh_token` exchange — it
  never opens a browser or blocks the request thread the way
  `create_user_session`'s third fallback would. Two small additive core
  changes support this: `save_task_files`/`process_step_url` now return the
  `(count, source)`/`task_dir` they already computed instead of `None`
  (`source` — zip/html_table/github_link/none — can't be reconstructed
  from disk after the fact, since the ZIP and GitHub-variant-A paths both
  produce an identical `tests/input.txt`+`output.txt`). New `POST
  /api/download` endpoint (the server's first `do_POST`). Verified
  end-to-end against a real Stepik step with an already-configured OAuth
  session on this machine.
- Runner Protocol abstraction (epic #136, issues #137-#139): new
  `core/runner.py` implements `docs/server-mode.md`'s already-designed
  Runner layer (#140) as real code. `Runner` (runtime-checkable Protocol),
  `RunSpec`/`RunOutcome` (raw subprocess result, no verdict), and
  `LocalRunner` (the existing `subprocess.Popen` + best-effort `RLIMIT_AS`
  + psutil RSS-polling logic, moved verbatim out of `run_single_test`).
  `grader_core.run_single_test()` now builds a `RunSpec` and delegates to
  `_RUNNER.run(spec)`; verdict/diff computation stays in `grader_core.py`.
  No behavior change — sets up a future `SandboxRunner` (#157) behind the
  same interface.
- Lazy `CONFIG` + JSON-locale i18n foundation (epic #141, issues
  #142-#145): `stepik_grader.config` no longer reads `pyproject.toml` as
  an import-time side effect — a module `__getattr__` (PEP 562) +
  cached `get_config()` defer the read to first access to `.CONFIG`, with
  every existing `from stepik_grader.config import CONFIG` call site
  unaffected. `load_config()` filters overrides via
  `dataclasses.fields(GraderConfig)` instead of the private
  `__dataclass_fields__` dunder. New `core/i18n.py` +
  `core/locales/{ru,en}.json`: an additive JSON-locale loader sitting in
  front of `cli.py`'s static `_MESSAGES` dict — `_t()` checks the JSON
  locale first, falling back to `_MESSAGES`; empty locale files today
  keep behavior byte-identical.
- Stepik client retry/backoff (epic #108, issues #109-#111):
  `make_session()` mounts an `HTTPAdapter` with a `urllib3.Retry` on
  http/https, so 429 (rate limit) and transient 5xx (500/502/503/504) are
  retried with exponential backoff (respecting `Retry-After`) for every
  request through the session, not just the call sites that already used
  `_get_with_retry()`. 4xx other than 429 still isn't retried.
- `TestResult` dataclass + `Verdict` Literal (epic #112, issues
  #113-#115): new leaf module `core/result.py` matching
  `docs/result-contract.md`'s case-result fields; `from_dict()`/
  `to_dict()` round-trip the same dict shape `run_single_test()` has
  always returned, so the public dict contract (CLI JSON, `run_tests()`/
  `run_benchmark()`, `/api/grade`) is unchanged.
  `core/reporter.print_case_verbose` now reads typed attributes instead
  of ad-hoc `dict.get()` calls with inline defaults; output is
  byte-identical.
- WEB workspace (issue #125, epic #123): split-pane layout (sidebar/result/
  detail panels), extended ErrorCard fields on `/api/grade`'s case results
  (`case_n`/`severity`/`stdin`/`expected`/`actual`/`stderr`/`exit_code`/
  `timeout_s`/`suggestions`/`glossary_ids`/`actions`), a command registry
  (`GET /api/commands`) driving action cards, scenario buttons, and a
  Ctrl+K/⌘K command palette from one shared filter, and a Glossary section
  (`GET /api/glossary`, `GET /api/glossary/<id>`, `GET /api/glossary/missing`)
  with search, card detail, and a J7 missing-concept backlog view. All
  additive on top of the existing `/api/grade` contract — no existing field
  renamed/removed. New `GraderConfig.glossary_store`/`glossary_missing_queue`
  fields configure the local card store and backlog file (both optional,
  default to a zero-config fallback). `core/grader_core.run_single_test`
  gained an additive `exit_code` field and `core/glossary.all_entries()`
  lists the compact curated glossary for that fallback.
- WEB UI redesign to match the epic #123 reference mask (`web-mvp-mask.html`
  attached to the issue): full design-token system ("Hydra" light/dark
  palette, Inter + JetBrains Mono), a grid-based `.app-shell` (fixed 220px
  sidebar navigation replacing the old topbar section-switcher and
  resizable dividers), a 4-button mode row (Compare/Tests/Bench/Microbench
  — the last a disabled placeholder for #187), and a 2-column split-pane
  with the ErrorCard detail panel moved into a "Детали" tab alongside new
  "Лог"/"Эталон" tabs. All #125 functionality (palette, action cards,
  scenario buttons, Glossary section with backlog) preserved unchanged,
  only re-skinned. New **Сравнение (Compare)** mode: `grade_benchmark()`
  gained an optional `reference` parameter (path or filename among the
  found solutions) — resolved, ranking is computed relative to it instead
  of the fastest solution, with `REFERENCE`/`FASTER` verdicts added
  alongside the existing `SIMILAR`/`SLOWER`/`MUCH_SLOWER`; unresolved
  (typo/foreign file) silently falls back to the normal ranking. Response
  gains additive `reference_source`/`reference_file` fields for the new
  tab. `core/microbench_runner.apply_relative_ranking` (shared with CLI)
  is untouched — the new ranking lives in a web-only
  `_apply_reference_ranking()`. Sidebar has a disabled "Загрузчик задач"
  placeholder for #186. Note: the Stepik-side reference-solution *import*
  (issue #55, reopened) is a separate, unrelated mechanism — this redesign
  only lets `#ref-input` point at an already-local file.

### Changed
- Corrected web UI modes 1/2 after owner feedback on #125: the redesign
  above had mistranslated the mask's "Режим 1" as a benchmark-style
  "Сравнение" (Compare) mode. Режим 1 is now the actual analogue of CLI
  mode 1 (single-file check): pick a folder, click "Найти решения" (new
  `GET /api/solutions?path=` — thin adapter over the already-used
  `find_all_solution_files`), choose one found file, see its source
  (new `GET /api/source?path=`), and run just that file — no comparison
  involved. The "Найти эталонное решение" button is a disabled placeholder
  for #55. Режим 2's "Параметры" tab is now visibly present but greyed
  out/non-clickable (tests mode genuinely has no parameters — `repeats`
  only applies to bench); Режим 1 hides that tab entirely. The bottom
  scenario-button bar (auto-shown run_again/toggle_theme/switch_section
  when nothing is selected) is removed app-wide — the command palette and
  the detail panel's action cards are unaffected. `grade_benchmark(reference=...)`
  and `_apply_reference_ranking()` from the previous entry stay in the
  code and under test, just unused by the frontend for now.

### Fixed
- Glossary exception-name detector (`_last_exception_name`) reduced false
  positives: plain text lines that happened to look like a capitalized
  identifier (e.g. an `exc.add_note()` note) were being reported as
  exception names. `_looks_like_exception_name()` now requires the
  `Error`/`Exception`/`Warning` naming convention or membership in the
  small set of builtins that don't follow it (issue #191).
- Web UI client-side `esc()` escaped `&`/`<`/`>` for text context but not
  quotes, so a value landing inside an HTML attribute (`errorCard()`'s
  `href="..."`) could still break out of it. Not exploitable today
  (`g.url` is server-controlled), but hardened ahead of more action/error
  cards being added the same way (issue #214).
- `scripts/version.py`'s logical `X.Y.Z` version (README `Version` badge)
  no longer double-counts CI's own `chore(ci): update badges [skip ci]`
  bot commits toward PATCH — it excludes them via `git rev-list
  --invert-grep` instead of `git describe --tags --long`'s raw commit
  count (issue #231).
- `core/microbench_runner.py::run_microbench()` left `stdin` unset on its
  `subprocess.Popen` call, so the child inherited the parent's stdin
  handle. Under pytest's output capturing that handle is a fake/invalid
  Windows handle, which intermittently raised `OSError: [WinError 6]`
  (invalid handle) when several microbenchmarks ran in one test session —
  found while adding tests for issue #187. Fixed by passing
  `stdin=subprocess.DEVNULL` (the child never reads real stdin — it swaps
  `sys.stdin` itself), matching the pattern already used in
  `core/runner.py`.
- **Security (High):** `downloader.py` no longer sends the Stepik OAuth
  Bearer token to third-party hosts. ZIP/GitHub test-case links extracted
  from a task's HTML text were previously fetched through the same
  authenticated `requests.Session` used for the Stepik API, leaking the
  access token to any domain a task's text happened to link to. New
  `core/stepik_client.py::external_download_get()` performs those fetches
  through a fresh, unauthenticated session, validated by
  `validate_external_url()` against an explicit host allowlist
  (`github.com`, `raw.githubusercontent.com`, `api.github.com`,
  `codeload.github.com`) with loopback/private/link-local IP literals
  rejected outright. `is_stepik_url()` still routes genuine `stepik.org`
  ZIP links through the authenticated session, since that's a first-party
  call, not a leak. `_download_github_tests()` no longer accepts a session
  parameter at all — GitHub is always third-party (issue #240, security
  audit finding F-01, part of #146/#97).
- **Security (Medium):** OAuth authorization-code flow (`authorize_via_browser()`)
  now sends a cryptographically random `state` (`secrets.token_urlsafe(32)`)
  in the authorize URL and requires the local callback server to receive the
  same value back before extracting the code. Previously the loopback
  callback server accepted the first `?code=...` it saw with no `state`
  check, so a page that lured the victim into hitting
  `http://localhost:<port>/callback?code=<attacker's code>` could bind the
  local app to the attacker's Stepik account (Login-CSRF).
  `wait_for_auth_code()`/`_make_oauth_handler()` now take a required
  `expected_state` parameter and reject a missing/mismatched `state` with a
  clear `RuntimeError` instead of ever returning a code (issue #241,
  security audit finding F-02, part of #146/#149/#97).
- **Security (Medium):** the local web UI's `/api/*` endpoints (`/api/grade`,
  `/api/download`, `/api/save-solution`, etc.) now validate the `Host` header
  against `127.0.0.1`/`localhost` and, when present, the `Origin`/`Referer`
  header against the same. The server only ever binds to loopback, but a page
  open in the user's browser could still trigger grading/download/save
  actions via a plain cross-site request (no CORS preflight for a simple
  GET) or DNS-rebinding (an attacker domain briefly resolving to
  `127.0.0.1`). A mismatched `Host` or `Origin`/`Referer` now gets a 403;
  requests with no `Origin`/`Referer` at all (non-browser clients) are
  unaffected — those headers can't be forged by page JS, unlike the request
  body/query. `/` and `/static/*` are unaffected (issue #242, security audit
  finding F-03, part of #151/#97).
- **Security (Low):** `core/storage.py::save_secrets()` now creates/rewrites
  `secrets.json` with owner-only permissions (`0600`) on POSIX, using
  `os.open(..., mode=0o600)` so the file never briefly exists with the
  process's default (usually wider) umask-based permissions between creation
  and a follow-up `chmod`. An existing `secrets.json` left over from an older
  version with wider permissions is also forced back to `0600` on the next
  save. `secrets.json` holds the OAuth access/refresh token and
  `client_secret`. On Windows `os.chmod` has no equivalent to the Unix
  group/other bits (NTFS uses ACLs, not mode bits), so the call is
  effectively a no-op there and the file's protection stays whatever the
  user's profile directory already provides (issue #243, security audit
  finding F-04, part of #149/#146/#97).
- **Security (Low):** `core/wrapper_builder.py::_build_function_wrapper()`
  (legacy function-mode wrapper) now imports `datetime`/`decimal`/`fractions`
  before `sys.path.insert(0, <solution dir>)` instead of after. Previously a
  same-named file next to the solution (e.g. a stray `datetime.py`) would
  land first in `sys.path` and shadow the real stdlib module once the
  wrapper's own `from datetime import ...` ran, breaking (or worse, silently
  altering) any test case whose input relies on that stdlib type. The other
  wrapper builder, `_build_call_wrapper()`, already did this correctly
  (issue #244, security audit finding F-05, part of #136/#97).
- `core/wrapper_builder.py::_build_function_wrapper()` — the generated
  wrapper resolved a function's positional arguments via
  `[locals()[_p] for _p in _sig.parameters]`. A list comprehension is its
  own scope, so `locals()` called inside it only ever saw the comprehension's
  own loop variable, not the module-level variables assigned from the test
  case's `input_data` — a `KeyError` on every parameter name except by
  accident on Python 3.12 (broken on 3.11 and on 3.13+, per PEP 667's
  tightened `locals()` semantics). Found while adding an end-to-end
  regression test for the F-05 fix above — that test is the first thing to
  ever actually execute this wrapper's generated code instead of just
  inspecting its source. Fixed by snapshotting `locals()` into a plain dict
  before the comprehension.
- **Security (Low):** `core/executor.py`'s module-level `EXECUTOR_TIMEOUT`
  parsing was a bare `int(os.environ.get("EXECUTOR_TIMEOUT", ...))` — a
  non-numeric value in that environment variable raised `ValueError` at
  *import time*, crashing the whole module (and, transitively, anything that
  imports it — the grader can't run at all until the env var is fixed or
  unset). New `_parse_executor_timeout()` catches the invalid value and
  falls back to `CONFIG.executor_timeout`'s default (issue #245, security
  audit finding F-06, part of #136/#97).
- `core/test_loader.py::load_test_cases()`'s Format 3 (`input.txt`/`output.txt`)
  parsing zipped `input_blocks`/`output_blocks` with `strict=False`, so a
  file pair disagreeing on the number of `# TEST_N:` blocks silently
  truncated to the shorter one — dropped test cases with no indication,
  risking a false-positive "all tests pass" from an incomplete set. It now
  warns (same `warnings.warn` pattern already used for the Format-1/3
  coexistence case just above it) when the block counts differ, naming both
  counts; the truncating behavior itself is unchanged — normal (matching)
  cases still load exactly as before (issue #246, security audit finding
  F-07, part of #97).

### Refactored
- `cli.py` decomposed into a package (`cli/`), epic #117 (issues #118-#122).
  `stepik_grader.cli` (`__init__.py`) stays the compatibility facade —
  `main()`, mode-handler/interactive-menu wrapper functions, and mutable
  i18n state (`_LANG`/`_MESSAGES`/`_LOCALE_MESSAGES`/`_t`, deliberately kept
  in place since `main()` mutates `_LANG` at runtime and moving it would
  turn the facade re-export into a stale snapshot). Four new leaf modules
  hold the actual logic, none importing `stepik_grader.cli`:
  `cli/options.py` (argparse parsing, #119); `cli/commands.py` +
  `cli/context.py` (mode handlers behind an explicit `CliContext`
  dependency-injection object, #120); `cli/rendering.py` (csv/markdown
  table output, #121 Phase 1); `cli/interactive.py` (menu/prompts,
  extending `CliContext`, #121 Phase 2). `tests/test_entrypoint.py` adds
  subprocess-level regression coverage for the `stepik-grader` console
  script and `python -m stepik_grader[.grader]` (#122). Across all five
  PRs, essentially no existing test files needed modification — the
  `CliContext` design was built specifically to keep
  `monkeypatch.setattr(cli, "...", ...)`-based tests passing unmodified
  through the move.
- `web.py` decomposed into a `web/` package, issue #125: `server.py`
  (HTTP handler/routing), `viewmodels.py` (`grade_path`/`grade_benchmark`/
  the ErrorCard mapper), `glossary_adapter.py`, `commands.py`, and
  `static/{index.html,app.css,app.js}` (JS/CSS extracted from the old
  inline `_INDEX_HTML` string, served via a small fixed route allowlist).
  Pure move — public API (`grade_benchmark`/`grade_path`/`run_server`)
  unchanged; `_Handler`/`_INDEX_HTML`/`_APP_JS`/`_case_view` re-exported
  from `web/__init__.py` for test back-compat.

### Docs
- Sandbox limits clarified in `executor.py`'s module/`main()` docstrings —
  explicitly no OS-sandbox, no FS/network isolation, trusted solutions
  only (issue #213); Windows limitations of the future
  `SandboxRunner`/`LocalRunner` documented in `docs/server-mode.md`,
  completing #140's acceptance criteria. Stale follow-up references
  cleaned up in `docs/README.md`/`docs/claude-handoff.md`; README
  `--watch` marked as requiring the `[watch]` extra (issue #215).
- README line-budget (220 lines) and local Markdown link/anchor
  guardrails: new `scripts/check_docs_guardrails.py`, wired into CI as a
  `docs-guardrails` job, documented in CONTRIBUTING.md (issue #173).
- Architecture/design docs formalized: `glossary/stdlib_inventory.py` +
  `coverage.py` registered in the DAG (#199); `docs/result-contract.md`
  for CLI/Web/API case-result fields and verdicts (#116); server-mode
  design — Runner layer, remote execution API, sandbox requirements
  (#140/#156/#157) plus ADR-0001 (#152); diagnostic/logging design with
  secret redaction (#150); Contributor Covenant `CODE_OF_CONDUCT.md`
  linked from CONTRIBUTING (#204).

### Tests
- Cross-adapter user-journey coverage for the web UI (issue #129, closing
  epic #123): most journeys from `docs/web-mvp.md § User journeys` were
  already covered incrementally across `tests/test_web*.py` as #125/#186/
  #187 landed, but the issue's own follow-up comment (after PR #185)
  explicitly said not to close it using only the original 3-item checklist.
  New `tests/test_web_journeys.py` proves three previously-untested seams
  between adapters that were each only unit-tested in isolation: a
  downloaded task's path is immediately gradable via `grade_path()` (J0→J1),
  an RE case's `glossary_ids` actually resolve to a real card through
  `glossary_adapter`/HTTP instead of a dead link (error-card→glossary
  navigation), and an entry queued mid-grading is visible through
  `glossary_adapter.glossary_missing()` — the same read path
  `GET /api/glossary/missing` uses, not just the lower-level
  `json_provider`. Command-palette keyboard flows (Ctrl+K/arrows/Enter/
  Escape) were verified manually against a running server, the same
  no-JS-test-runner tradeoff #125 already documented.

## [1.6.0] - 2026-07-08

### Added
- Glossary coverage relative to official Python/stdlib (issues #195–#198, part
  of epic #123). `GlossaryMissingEntry` gained `origin`
  (`solution`/`error`/`stdlib_scan`), `module` and `qualname` fields
  distinguishing practice-driven gaps (`MissingConceptDetector`) from
  source-driven ones, with `kind`/`status`/`origin` validation on load (issues
  #190/#195; old queues without the new fields still load with defaults). New
  leaf module `stdlib_inventory.py` builds a deterministic, offline inventory
  of Python builtins, exceptions (recursive `BaseException` walk) and a
  curated set of stdlib modules — no network, no user-code execution (issue
  #196). New `coverage.py` compares that inventory against the local card base
  and produces a `CoverageReport` (`builtins`/`exceptions`/`stdlib` categories
  with covered/missing/ratio) plus `GlossaryMissingEntry(origin="stdlib_scan")`
  backlog entries; repeated scans stay idempotent via the existing
  concept-keyed dedup (issue #197). CLI entrypoint `python -m
  stepik_grader.glossary.coverage [--cards PATH] [--missing-out PATH]
  [--modules a,b,c]` prints the coverage summary and optionally appends
  missing entries, via its own rich-optional printer so the module stays a
  leaf (issue #198). Format and API documented in `docs/glossary.md`.
- `--version` now distinguishes dev builds from releases (issue #163, closes
  epic #161): off-tag output gets an explicit `(dev build, not a release)`
  suffix appended to the existing `setuptools-scm` string; on-tag output is
  unchanged (clean `X.Y.0`).
- Live README badges, replacing a hand-maintained static `Coverage` badge that
  had silently drifted from the real number. `scripts/generate_coverage_badge.py`
  and `scripts/generate_version_badge.py` write shields.io "endpoint badge" JSON
  (`.github/badges/*.json`) from the real `pytest --cov` result and the
  project's logical `X.Y.Z` version (`scripts/version.py`) respectively; CI
  (`ubuntu-latest`/3.12 leg, push to `main` only) regenerates and commits both
  files together after each test run. A new `Version` badge sits next to
  `Release` in README so `main` drifting ahead of the last tagged release is
  visible without checking git.
- Security policy (PR #203, issue #201): `SECURITY.md` with a responsible
  disclosure process and supported-versions note; README/threat-model docs link
  to it. Full policy lives in `SECURITY.md` (not duplicated here).
- Project workflow templates (PR #203, issue #202): GitHub PR and issue
  templates under `.github/`.

- Local glossary knowledge-module foundation (issue #126, part of epic #123).
  New `stepik_grader.glossary` subpackage: typed `GlossaryCard` /
  `GlossaryMissingEntry` models, `JsonGlossaryProvider` for loading and
  searching a local JSON card base (single file or directory; search by
  id/title/aliases/keywords/tags; filter by status/tag) with clear
  `GlossaryError` on missing/broken JSON, a JSON missing-entry queue
  (`load`/`save`/`append` with dedup), and a conservative, deterministic
  `MissingConceptDetector` that finds uncovered stdlib calls, notable builtins,
  `match/case` and traceback exceptions via AST (never executes user code) and
  suppresses concepts already covered by known glossary terms. JSON format and
  Python API documented in `docs/glossary.md` with a sample fixture at
  `docs/examples/glossary.sample.json`. The external Glossary-Python project
  stays a one-way export target; the local base is the source of truth. WEB UI,
  endpoints and the exporter remain in #125/#129.
- Packaging hygiene (PR-1, epic #98): explicit MIT `LICENSE` at the repo root
  and PEP 639 SPDX license metadata in `pyproject.toml` (`license = "MIT"` +
  `license-files = ["LICENSE"]`, issue #100); PEP 561 `py.typed` marker so
  downstream consumers' type checkers see the package's type hints (issue #101).
  Build requirement bumped to `setuptools>=77` for SPDX support; `py.typed`
  declared in `[tool.setuptools.package-data]`. (Version sync, issue #99, was
  already done — see the pre-merge version rule in CLAUDE.md.)
- Glossary hints on runtime errors (issue #72, first brick of epic #96).
  New leaf module `core/glossary.py` holds a curated map of ~28 built-in
  Python exceptions → a one-line Russian hint + a link to the full card in the
  separate Glossary-Python project (not a copy of the 581-card glossary — the
  "vendor a thin layer" choice from epic #96: offline hints, link out for
  depth). Single source of truth for two surfaces: `reporter.print_case_verbose`
  prints a hint line + URL on an RE verdict (CLI verbose); `web._case_view`
  attaches a `glossary` block that the web UI renders as an error card with a
  link. `lookup_from_error` parses the exception name from the traceback's last
  line (dropping any `module.` prefix). The base URL and anchor scheme
  (`#<classname-lowercased>`) are single constants, trivially adjustable if the
  glossary's anchors change.

### Changed
- Glossary source-of-truth / coverage-truth clarification (PR #203, issues
  #194/#200): docs and the Claude handoff now state the invariant consistently —
  the internal Stepik-Python-Grader base is the content source of truth, official
  Python/stdlib is the completeness/coverage truth, and the external
  Glossary-Python is an export/vitrine target only (never the completeness
  benchmark). Canonical wording in `docs/glossary.md`; not duplicated here.
- Documentation split (PR-2, epic #102): README is now a lean showcase; heavy
  technical sections moved into a `docs/` knowledge base — `docs/architecture.md`
  (module DAG + layers, #105), `docs/project-structure.md` (file tree, #104),
  `docs/versions.md` (release-comparison table + fork-vs-original, #106). README
  becomes a lean showcase with one-line pointers to `docs/` and an
  updated table of contents. CONTRIBUTING gains a "README as showcase, `docs/`
  as knowledge base" rule so it doesn't bloat again (#107).

## [1.5.0] - 2026-07-06

Post-audit roadmap batch — result caching, a pytest plugin, incremental
watch, and a beginner IDE-launch fix, plus the leftover backlog cleanup
(#67/#69) merged after v1.4.0.

### Added
- pytest plugin (`pytest --grader-mode`, issue #57).
- Opt-in result cache (`--cache` / `--no-cache`, `--clear-cache`, issue #56).

### Changed
- `--watch --mode 2` is now incremental (issue #71).
- Memory cap now applied via `resource.prlimit(child_pid, …)` after spawn
  instead of `preexec_fn` (issue #67).

### Fixed
- IDE integration tasks now launch via the editor's selected interpreter
  instead of the bare `stepik-grader` console script.

### Removed
- `run_microbench_with_timeout()` from `core/microbench_runner.py` (issue #69).

<details><summary>Подробности изменений v1.5.0</summary>

### Fixed
- IDE integration tasks now launch via the editor's selected interpreter
  instead of the bare `stepik-grader` console script, which is only on PATH
  when the venv is activated — the most likely reason the generated tasks
  "wouldn't launch" for beginners. VS Code `--init-vscode` tasks now use
  `${command:python.interpreterPath} -m stepik_grader.grader …`
  (`type: process`, robust to interpreter paths with spaces); the README
  PyCharm External Tool recipe now uses `$PyInterpreterDirectory$/python -m
  stepik_grader.grader …`. Both run from the interpreter where the package is
  installed, with no manual venv activation (VS Code needs the Python
  extension; both need an interpreter selected).

### Changed
- `--watch --mode 2` is now incremental (issue #71): watch mode auto-enables
  the #56 result cache, so a file-change event only re-runs the changed
  solution — every other row is served from cache instead of re-running the
  whole folder. On a folder with a dozen tasks the feedback loop no longer
  degrades. Opt out with `--no-cache` (restores the old full-folder rerun).
  `--cache` default changed from `CONFIG.use_cache` to `None` so an explicit
  `--cache`/`--no-cache` can be distinguished from "unset" and always wins;
  new `_resolve_use_cache` helper centralizes the precedence (explicit flag →
  watch-incremental default → `[tool.stepik-grader] use_cache`). `--mode 1`
  (single file) does not auto-enable the cache under `--watch`.

### Added
- pytest plugin (`pytest --grader-mode`, issue #57). New module
  `pytest_plugin.py` registered as a `pytest11` entry point, so
  `pytest --grader-mode StepikTasks/` collects each solution file (`task*.py`)
  as a pytest `File`, yielding one `Item` per test case from its `tests/`
  directory. Items run through the same `run_single_test` engine as CLI mode 1;
  a wrong answer is a normal FAILED with an "expected/actual" diff, a runtime
  error surfaces the exception text. Off by default (no-op unless
  `--grader-mode` or `grader_mode = true` in the ini) so it never interferes
  with a project's own suite. Core imports are lazy (inside the hooks) so the
  entry-point load doesn't distort coverage. 16 tests via the `pytester`
  fixture. `pytest` must be installed to use it (it already is wherever pytest
  runs); a standalone `pytest-stepik-grader` PyPI package remains future work.
- Opt-in result cache (`--cache` / `--no-cache`, `--clear-cache`, issue #56).
  New leaf module `core/cache.py` (stdlib `hashlib` + `core/storage.py` JSON
  I/O only — no new DAG edges/cycles). On `--mode 1/2 --cache`, a solution is
  skipped and its previous verdict reused when neither the solution file's
  `sha256` nor the `sha256` of all files in its test directory changed since
  the last run; any change to either invalidates the entry. Cache lives in a
  single `.grader_cache/results.json` under the CWD (added to `.gitignore`);
  a corrupt/version-mismatched file degrades to an empty cache rather than
  crashing. Defaults come from `[tool.stepik-grader] use_cache`
  (`GraderConfig.use_cache`, default `false`). Mode 1 prints "cache is up to
  date" on a hit; mode 2 prints an "N of M served from cache" summary.
  `--clear-cache` deletes the cache and reports how many entries were removed.

### Removed
- `run_microbench_with_timeout()` from `core/microbench_runner.py` (issue #69).
  Unwired for two release cycles; its own docstring admitted it added no
  protection (a `ThreadPoolExecutor` wrapper around an already
  `subprocess.run(timeout=60)`-bounded call) and would leak an orphan thread
  on a real timeout. Verdict: remove dead-but-misleading code (git keeps the
  history) rather than keep it. `import concurrent.futures` and `Callable`
  (now unused there) and two tests removed with it

### Changed
- Memory cap (`GraderConfig.max_memory_mb`, RLIMIT_AS) now applied via
  `resource.prlimit(child_pid, ...)` **after** spawn instead of a
  `subprocess` `preexec_fn` (issue #67). `preexec_fn` forks in a multithreaded
  parent — the grader runs a psutil memory-monitor thread — which is
  documented as unsafe; `prlimit` sets the limit on the already-spawned pid
  without forking in the parent. `core/microbench_runner.run_microbench`
  switched from `subprocess.run` to `Popen` + `communicate(timeout=60)` to get
  a pid to limit. Linux-only (`resource.prlimit` is absent on macOS →
  `AttributeError` → no-op; Windows has no `resource` module), a change from
  the previous in-child `setrlimit` which also ran on macOS — acceptable, the
  cap was always best-effort (issue #43 S-01), and thread-safety wins. The
  ~ms window where the child runs before the limit lands is before user code
  executes

</details>

## [1.4.0] - 2026-07-05

Epic #80 (onboarding/UX) Tiers 1–2 — the grader beyond the pure console:
a local web UI and IDE integration, plus the PyPI-install README pivot.

### Added
- IDE integration (`stepik-grader --init-vscode`, epic #80 Tier 2 / issue #58):
  generates `.vscode/tasks.json` in the current folder with grade tasks —
  "check current file" (default, `Ctrl+Shift+B` → `--mode 1 --file ${file}`),
  "check folder", "benchmark folder", "web UI". Won't overwrite an existing
  `tasks.json` (reports it instead). New leaf module `ide.py` (stdlib only);
  `cli.py` gains `--init-vscode`. PyCharm is set up manually via an External
  Tool — documented in the README. Both integrations just invoke the
  `stepik-grader` console command
- Local web UI (`stepik-grader --serve`, epic #80 Tier 1 / issue #58): a
  single-page interface on `127.0.0.1` (localhost only, `--port` configurable,
  default 8000) with **two modes** — **Correctness** (AC/WA table, time, memory;
  click a filename to expand per-case verdicts and the WA diff) and
  **Benchmark** (solutions ranked by median, fastest first, with the
  SIMILAR/SLOWER/MUCH_SLOWER verdict, same as CLI mode 3). Enter a solution
  file or a folder path; the path field defaults to the launch directory and
  the last path/mode are remembered (localStorage); a summary line shows
  pass/fail counts or the fastest solution. Built on the stdlib `http.server`
  — **no new dependency** — and reuses the same grading path as the CLI
  (`run_tests`, `run_benchmark`, `apply_relative_ranking`, `fmt_time`); no
  logic duplicated. New module `web.py` (`web → core`, acyclic); `cli.py`
  gains `--serve`/`--port` and lazily imports `web`. Same threat model as the
  CLI (no OS sandbox). Drag-and-drop upload is a planned follow-up

### Docs
- README install now leads with `pipx install stepik-python-grader` from PyPI
  (published as of v1.3.0, issue #70) instead of the `git+https://…` form,
  which is kept as the "unreleased from source" fallback

## [1.3.0] - 2026-07-04

Onboarding/UX epic #80 (feedback from a from-scratch install run on Windows)
plus the PyPI distribution pivot (#70, superseding the closed standalone-`.exe`
idea #78).

### Added
- PyPI publishing via OIDC trusted publishing (issue #70): a `pypi-publish`
  job in `release.yml` that builds sdist+wheel and uploads to PyPI on every
  `v*` tag, with no stored token/secret (`id-token: write`, `environment:
  pypi`). Independent of the GitHub Release job (`needs: release` only orders
  it) so a not-yet-configured trusted publisher fails only this job, not the
  Release. Requires a one-time manual setup by a repo owner on pypi.org
  (documented in the workflow); a commented TestPyPI dry-run step is included.
  Unblocks `pipx install stepik-python-grader` (superseded standalone-`.exe`
  idea #78, closed as not-viable — the grader runs solutions via
  `sys.executable`, which a frozen binary can't provide)
- File-dialog fallback (issue #79): when a mode needs a path and none is given
  — empty input in the interactive menu, or `--mode N` without `--file`/`--dir`
  — the grader opens a native `tkinter` file/folder picker instead of failing.
  Only in interactive text mode: never pops for `--output json/csv/markdown` or
  `--watch` (machine/non-interactive contexts), and degrades gracefully to the
  previous text behaviour when `tkinter` is absent or headless (no display).
  No new dependency — `tkinter` ships with CPython

### Fixed
- `stepik_config.json.example` used the owner's personal `root_dir` value
  `"P2.2"` (the folder ignored by the `P2.2/` line in `.gitignore`) instead of
  the documented default. Now `"StepikTasks"` — matches
  `downloader.DEFAULT_ROOT_DIR`, README and CLAUDE.md, and works as-is

### Docs
- Version-evolution comparison table in README (v1.0.0 → v1.1.0 → v1.2.0) —
  fundamental shifts per release (code layout, launch, CLI, CI, security,
  distribution, versioning), not a per-feature list; plus a note on why MAJOR
  stays `1`
- Beginner-proof install in README (part of epic #80): split into `pipx`
  (recommended for just-use) vs. from-source (venv) paths, an explicit Windows
  PowerShell ExecutionPolicy (`PSSecurityException`) warning with three ways
  out, a "Требования" section (Python 3.12/3.13; 3.14 experimental), and a
  step-by-step "first run in 2 minutes" walkthrough

## [1.2.0] - 2026-07-04

Sprints A (Security), B (Architecture), C (Reliability), D (CI/CD & Quality),
and E (UX/Docs/Deps) from the v1.1.0 audit epic #60: issues #43, #44, #45,
#46, #47, #48, #49, #50, #51, #52. Plus three roadmap items from the same
audit epic: #53, #54, #58 (partial). Plus the second audit round (docs
`ISSUES_AND_VERSIONING.md` / `AUDIT_FULL_20260704.md`): issues #64 (UTF-8
stdio), #65 (`python -m stepik_grader`), #66 (mode-4 `Py-heap` column), #68
(versioning scheme + `scripts/version.py`).

### Changed
- Split `core/grader_core.py` (1200+ lines) into `core/test_loader.py`
  (solution-file discovery, `load_test_cases`, `resolve_test_dir`),
  `core/mode_detector.py` (`_detect_run_mode`, `is_function_only_solution`,
  `_is_python_code_block`), and `core/wrapper_builder.py`
  (`_build_function_wrapper`, `_build_call_wrapper`). `grader_core.py` keeps
  `run_single_test`/`run_tests`/`run_benchmark`/`run_microbench_mode` and
  re-imports all 16 moved names by name (not `import *`) so `grader_core`'s
  own `__all__`, `grader.py`'s explicit facade import list, and `cli.py`'s
  imports all keep working unchanged. Only new internal dependency:
  `test_loader.py -> mode_detector.py` (for Format-3 block classification and
  run-mode detection) -- no cycles. An Explore agent audited the whole test
  suite for `monkeypatch`/`mock.patch` targeting any of the 16 moved names
  through `grader_core`/`cli` paths before starting; it found none, so no
  test files needed changes for the move itself (issue #45 A-01)

### Added
- `scripts/version.py` + a "Versioning" section in `CONTRIBUTING.md`
  documenting the project's non-SemVer scheme: MAJOR only on fundamental
  shifts, MINOR +1 per git tag + Release (so every tag is `vX.Y.0`), PATCH =
  commit count since the last tag (reset on MINOR bump). The script derives
  the version from `git describe --tags --long` (`vX.Y.0-N-g<hash>` → `X.Y.N`)
  and falls back to `MAJOR.MINOR` from `pyproject.toml` + total commit count
  before the first tag. Documented as a helper/CI tool only — the build still
  declares `version` statically, no `setuptools-scm` dependency added. Also
  added to `CLAUDE.md`'s critical-rules block so contributors/agents don't
  apply SemVer and break the "every tag = vX.Y.0" invariant (issue #68)
- `src/stepik_grader/__main__.py` — `python -m stepik_grader` now works as a
  shortcut for `python -m stepik_grader.grader` / the `stepik-grader` console
  script, delegating to the same `cli.main()`. Expected for an installed
  package with a console entry point (issue #65)
- `_force_utf8_stdio()` in `cli.main()` — reconfigures `stdout`/`stderr` to
  UTF-8 with `errors="replace"` at startup, fixing `UnicodeEncodeError` when
  running under Git Bash / cmd with a cp1251 code page. Removes the need for a
  manual `PYTHONIOENCODING=utf-8`. No-op on streams already in UTF-8 or
  without `reconfigure` (e.g. captured by pytest) (issue #64)
- `--output csv`/`--output markdown` for all four modes -- same underlying
  data as `--output json`, flattened to one row per file/test-case (issue
  #53, issue #58's "export to Markdown" idea)
- `--watch` for `--mode 1/2`: reruns the whole mode on any change inside the
  watched file/directory, clearing the screen first. Optional dependency
  `pip install "stepik-grader[watch]"` (`watchfiles`); prints an install
  hint instead of crashing when it's absent. Reruns the ENTIRE mode on any
  change rather than isolating which single file changed -- issue #54's own
  "only rerun the changed file" idea would need mapping a changed path back
  to its own test_dir and merging a partial result into the existing table,
  meaningfully more complex for uncertain benefit over a full rerun (issue
  #54)
- i18n for the interactive menu and CLI messages: Russian by default, `--lang
  en` switches to English (issue #51 D-01). Minimal message-dict + `_t()`
  helper in `cli.py` rather than a full gettext setup -- proportionate to
  this CLI's size
- `--verbose`/`--quiet` (mutually exclusive) for `--mode 1/2`: mode 1 already
  defaulted to verbose (unaffected unless `--quiet`), mode 2 already
  defaulted to quiet (unaffected unless `--verbose`) (issue #50 D-03)
- `--output {text,json}` for all four modes. `json` prints one JSON line
  reusing the existing `run_tests()`/`run_benchmark()`/`run_microbench_mode()`
  result dicts directly (`file`/`results`/`groups` keys depending on mode) --
  no separately-invented schema (issue #50 D-04)
- Richer "tests not found" diagnostic for `--mode 1`: names the expected
  folder and suggests `python -m stepik_grader.downloader` or manual
  `tests/1`, `tests/1.clue` creation, instead of a bare "not found" (issue
  #50 D-05)
- `.github/workflows/release.yml`: builds sdist+wheel and creates a GitHub
  Release with auto-generated notes on `v*` tag push. PyPI publishing is
  intentionally NOT included -- it needs a trusted-publisher relationship
  configured on pypi.org first, which requires manual setup by a repo owner
  with PyPI access (issue #51 C-03)
- Upper bounds on runtime dependencies: `requests<3.0`, `psutil<8.0`,
  `rich<16.0` (issue #51 P-02)
- `mypy>=1.10` in `[project.optional-dependencies].dev`; `mypy src/stepik_grader
  --ignore-missing-imports` runs as a CI step on every matrix leg (issue #49
  C-02). Fixed the ~12 pre-existing/newly-surfaced errors this uncovered:
  `load_json_file()` call site passing `str` instead of `Path`
  (`grader_core.py`), `str | None` propagating past `resolve_test_dir()`'s
  new contract into `cli.py`'s three call sites (narrowed with `assert`/
  explicit `is None` checks), and targeted `# type: ignore[attr-defined]` on
  `signal.alarm`/`resource.setrlimit`/`RLIMIT_AS` call sites that are
  legitimately POSIX-only (typeshed excludes them on win32) and already
  runtime-guarded
- CI matrix now runs on `ubuntu-latest`, `windows-latest`, and `macos-latest`
  (previously Ubuntu-only) for Python 3.12/3.13; the 3.14-experimental leg
  stays Ubuntu-only (issue #49 C-01)
- `GraderConfig.max_memory_mb` (default 1024) — best-effort `RLIMIT_AS`
  memory cap applied via `preexec_fn` to every subprocess that runs solution
  code (`core/grader_core.py::run_single_test`, `core/microbench_runner.py::
  run_microbench`). POSIX-only (`resource` module absent on Windows);
  degrades to a no-op there, same pattern as `executor.py`'s `SIGALRM`
  handling (issue #43 S-01)
- `warnings.warn()` when `load_test_cases()` uses Format 3 (`input.txt`/
  `output.txt`) while Format 1/2 files (`N.clue`/`input_N.txt`) also exist in
  the same directory and are silently ignored (issue #48 R-03)
- `warnings.warn()` in `_measure_peak_memory()` when the child process exits
  before `psutil` can sample it (`NoSuchProcess`/`AccessDenied`/
  `ZombieProcess`) — the returned peak (0.0) was previously indistinguishable
  from "genuinely used ~0 memory" (issue #48 R-05)
- Mock-based tests covering `downloader.py::_download_github_tests`'s two
  previously-uncovered error branches: `requests.RequestException`/HTTP
  errors from the GitHub Contents API, and a file listing with no
  recognizable Format 3/1 files (`downloader.py` coverage: 98% → 99%,
  issue #49 Q-01)

### Changed
- Mode 4 (micro-bench) memory column renamed from `Memory` to `Py-heap`.
  Mode 4 measures peak Python-heap via `tracemalloc` for stdin blocks (and
  RSS for function blocks), not process RSS like mode 3 — the shared header
  now reflects the measurement method instead of implying RSS. Added a
  one-line footnote under the mode-4 table and a README note; mode 3 keeps its
  RSS-based `Memory` column unchanged. Implemented via a `memory_header`
  parameter (default `"Memory"`) on `print_benchmark_header`/
  `print_benchmark_results`, so mode 3 call sites and existing reporter tests
  are unaffected (issue #66)
- `core/grader_core.py::_build_call_wrapper` — replaced
  `from collections/datetime/itertools/functools import *` with explicit
  imports covering each module's full documented public API. Removes the
  wildcard-import construct the audit flagged while preserving behavior for
  any test-block relying on stdlib names (issue #44 S-03)
- `run_tests()` gained a `verbose_callback` parameter; `core/grader_core.py`
  no longer imports `core/reporter.py` at module load time. `cli.py` now
  passes `reporter.print_case_verbose` explicitly for mode 1 (issue #45 A-02)
- Renamed three cross-module private symbols to public, adding each to its
  module's `__all__`: `_resolve_test_dir` → `resolve_test_dir`
  (`grader_core.py`), `_rich_track` → `rich_track`, `_print_case_verbose` →
  `print_case_verbose` (both `reporter.py`). `cli.py` no longer imports
  underscore-prefixed names from other modules (issue #45 A-04)
- `grader_core.__all__` no longer exports `TIMEOUT_SECONDS`/`ENCODING`/
  `SIMILAR_THRESHOLD`/`MUCH_SLOWER_THRESHOLD`/`MEASURE_CHILD_MEMORY`/
  `MICROBENCH_MAX_CASES` — these were module-level aliases of `CONFIG` values,
  not a standalone public API. `grader.py`'s own backward-compat `__all__`
  is unchanged; it now imports these six names explicitly by name instead of
  picking them up via `from grader_core import *` (issue #52 Q-03)
- `resolve_test_dir()` returns `str | None` instead of silently falling back
  to a non-existent `<parent>/tests/` path when no strategy matches.
  `cli.py`'s three call sites (`_run_mode_1/2/3`) updated to check for `None`
  before `pathlib.Path(...).is_dir()`, printing a friendly "Test directory
  not found" message instead of crashing on `pathlib.Path(None)` (issue #47
  R-04)
- `_is_python_code_block()` now classifies a bare single-name expression with
  no call and no assignment (e.g. `"x"`, `"print"`) as `False` (not a
  call-block) — narrow exception scoped to exactly that AST shape, doesn't
  touch classification of any realistic multi-statement or assignment
  content (issue #47 R-02)
- `microbench_runner.run_microbench()`'s timeout error message now reports
  the iteration count (`number=`) that was running when the 60s timeout
  fired — the most useful diagnostic available without a genuine per-call
  timeout inside the child process (issue #47 R-01, partial — see Notes)

### Removed
- `requirements.txt` — duplicated the same 3 runtime dependencies already in
  `pyproject.toml`. `pip install -e .` (or `-e ".[dev]"`) is now the only
  documented install path; README/CONTRIBUTING/CLAUDE.md updated (issue #51
  P-01)

### Notes
- Issue #47 R-01: a genuine PER-CALL timeout (interrupting one hung iteration
  out of `number x 5` inside `timeit.repeat()`) is NOT implemented — it would
  require abandoning `timeit.repeat()`'s batch execution for a manual
  per-call loop with time-based interruption, or a `SIGALRM`-style in-process
  signal that doesn't work on Windows (this project's primary dev platform).
  `run_microbench_with_timeout()` (Sprint 7.3, still unwired) wouldn't help
  either — wrapping the already-`subprocess.run(timeout=60)`-bounded call in
  a `ThreadPoolExecutor` adds no real protection, per its own docstring. The
  existing whole-run 60s timeout plus the new diagnostic message is the
  practical improvement shipped this pass
- Issue #43 S-02 ("code injection via f-string interpolation") closed as a
  duplicate of S-01, not a distinct fix: `safe_input`/`call_block` are
  embedded as top-level source, not inside a string literal, so there is no
  literal-escaping vector; the actual risk is that test-block content is
  executed as trusted Python code by design, which only OS-level process
  isolation (S-01) mitigates. Moving the data through an env var as the
  issue originally suggested would not reduce this risk and would risk
  truncating multi-line test blocks on Windows (~32KB env var limit)
- Issue #46 A-03 ("`executor.py` unused in production") closed with no code
  change: neither of the issue's two proposed options is actually safe here.
  Making it the production runner would drop peak-memory measurement and
  break on Windows (`SIGALRM` timeout is POSIX-only, this project's primary
  dev platform); moving it to `tests/helpers/` would break
  `tests/test_executor.py`'s subprocess invocations of `executor.main()`,
  which require it to stay part of the installed `stepik_grader.core`
  package. Status quo (tested, not production-wired) is an intentional,
  already-documented tradeoff, not an oversight
- Issue #45 A-01 (splitting `grader_core.py` into `test_loader.py`/
  `mode_detector.py`/`wrapper_builder.py`) was deferred out of Sprint B as its
  own follow-up; done in a later pass after Sprint E and the #53/#54/#58
  roadmap batch -- see the "Changed" entry above and CLAUDE.md's DAG section
- Issue #50 D-02 ("`CONTRIBUTING.md` отсутствует") was already stale at the
  time of the audit — the file exists and is fairly thorough. Fixed the two
  things that WERE actually stale in it (claimed "Python 3.10+", contradicting
  `pyproject.toml`'s `requires-python = ">=3.12"` everywhere else; a redundant
  separate `pip install rich` step now that `rich` is a required, not
  optional, runtime dependency) and added the `mypy` step Sprint D introduced
- Issue #51 P-02's own suggested upper bounds (`psutil<7.0`) are stale:
  `psutil` 7.2.2 and `rich` 15.0.0 are already installed and passing the full
  suite in this environment, so bounding to `<7.0`/`<15.0` would immediately
  break `pip install -e .[dev]`. Used `psutil<8.0`/`rich<16.0` instead --
  headroom above what's actually proven working, not the audit's example
  verbatim

## [1.1.0] - 2026-07-02

Sprints 6, 7, 8.1, and 8.2 from CLAUDE.md's backlog, plus GitHub issues
#19, #20, #21, #23, #24, #25, #26, #35 (audit findings from the v1.0.0
review, epic #18). Test suite grew from 355 to 523 tests; coverage from
88% to 95%.

### Added
- `config.py` — `GraderConfig` frozen dataclass, unified constants read
  from `[tool.stepik-grader]` in `pyproject.toml` (Sprint 6.3)
- `cli.py` — non-interactive argparse CLI (`--mode`, `--file`, `--dir`,
  `--repeats`, `--number`, `--version`), alongside the existing
  interactive menu (Sprint 8.1)
- `core/` package — every internal (non-entry-point) module now lives
  here: `executor.py`, `normalizers.py`, `parsers.py`, `storage.py`,
  `stepik_client.py`, `oauth_flow.py`, `microbench_runner.py`,
  `grader_core.py`, `reporter.py` (Issues #23, #26)
- `BenchStats` dataclass in `core/grader_core.py`, unifying the stats
  calculation shared by `run_benchmark()` and `_micro_stats()` (Sprint 7.2)
- `run_microbench_with_timeout()` in `core/microbench_runner.py` — not
  currently wired in; see its docstring for why (Sprint 7.3)
- `core/reporter.fmt_time()` — adaptive s/ms/µs/ns formatting for
  benchmark time columns, replacing a fixed `.4f` that truncated
  sub-millisecond timings to `0.0000` (Issue #24)
- Real peak-memory measurement in mode 4 via `tracemalloc`, replacing a
  hardcoded `0.0` (Issue #25)

### Changed
- `grader.py` (1460 lines) split into `core/grader_core.py` (execution),
  `core/reporter.py` (output), and `cli.py` (menu); `grader.py` itself is
  now an 8-statement backward-compatibility facade (Issue #20 finding #4)
- `core/executor.py`: `_PYTHON_CMD` — platform-string guess replaced with
  `sys.executable` (Sprint 6.1)
- `core/normalizers.py`: `sort_lines`/`normalize_whitespace` added to
  `__all__`, marked "experimental" instead of silently dead (Sprint 6.2)
- Duplicated relative/verdict ranking logic (grader.py had it in two
  places) consolidated into `core/microbench_runner.apply_relative_ranking()`
  (Issue #20 finding #6)
- `core/microbench_runner.py`'s broad `except Exception` narrowed to
  `(OSError, ValueError)`; redundant `float(str(x or 0))` simplified to
  `float(x or 0)` in `core/stepik_client.py` (Issue #21)
- `diagnostik_stepik.py` renamed to `diagnostic_stepik.py` (German-inflected
  spelling replaced with the correct English adjective; no imports pointed
  at it as a module, so this only touched docstrings/docs) (Issue #37)
- `cli.__version__` now reads from installed package metadata via
  `importlib.metadata.version("stepik-python-grader")` instead of a
  hardcoded literal, with `pyproject.toml`'s `version` field as the single
  source of truth (falls back to `"0.0.0+unknown"` if the package isn't
  pip-installed). Found and fixed a stale `stepik_python_grader.egg-info`
  reporting `0.1.0` with dependencies that didn't match current
  `pyproject.toml` (an old build artifact) by refreshing the editable
  install before wiring this up -- otherwise the new code would have
  faithfully reported the wrong version. `CONTRIBUTING.md`'s install
  steps now include `pip install -e .` and a note that it must be re-run
  after bumping the version (Issue #36)
- Documentation pass: `pyproject.toml`/`cli.py`/`CLAUDE.md`/`README.md`
  version bumped to 1.1.0 (Issue #29); `CHECKPOINT.md` fully rewritten to
  match current architecture and metrics (Issue #28); `README.md`'s module
  table synced with the `core/` layout, `config.py`, and `cli.py` (Issue
  #32); stale test-count/coverage numbers and the Glossary-Python freeze
  status corrected across `CLAUDE.md` (Issue #31); `grader.py`'s line
  count vs. coverage `Stmts` count disambiguated (Issue #33)

### Fixed
- `_parse_testblock_file` duplicated in `grader.py` and `core/parsers.py`
  — removed; `downloader.py` no longer imports `grader.py` at all
  (Issue #19)
- `core/grader_core._build_function_wrapper()` interpolated
  `function_name`/module stem into generated code without validating
  they were identifiers — a newline in either could inject statements
  into the wrapper script; now validated with `str.isidentifier()`
  (Issue #20 finding #5)

### Tests
- `cli.py` coverage: 40% → 97% (`tests/test_cli.py`, new)
- `config.py`: `tests/test_config.py`, new

### Sprint 8.2: migrate to src/-layout (#35)

#### Refactored
- Moved all 16 source files into `src/stepik_grader/` (via `git mv`,
  history preserved), including the whole `core/` subdirectory. Every
  internal cross-module import was rewritten with the `stepik_grader.`
  package prefix (e.g. `from core.grader_core import ...` → `from
  stepik_grader.core.grader_core import ...`).
- `pyproject.toml`: `[tool.setuptools.packages.find] where = ["src"]`;
  new `[project.scripts] stepik-grader = "stepik_grader.cli:main"` console
  entry point; `known-first-party = ["stepik_grader"]`; `--cov=src` /
  `source = ["src"]` for coverage.
- `conftest.py` now does `sys.path.insert(0, str(Path(__file__).parent /
  "src"))` so `import stepik_grader` works in tests without requiring
  `pip install -e .` first.
- `config.py`'s `load_config()` path resolution changed from
  `Path(__file__).parent / "pyproject.toml"` to `Path(__file__).parent
  .parent.parent / "pyproject.toml"`, since `config.py` now lives three
  directory levels below the repo root (`src/stepik_grader/config.py`)
  instead of one.
- All ~25 test files' imports and `unittest.mock.patch`/
  `monkeypatch.setattr` string targets updated to the new `stepik_grader.`
  dotted paths, including several indented local imports inside function
  bodies that a first-pass bulk regex missed (`tests/test_microbench.py`,
  `tests/test_config.py`), and `tests/test_executor.py`'s subprocess
  invocation paths and `-c` command strings.
- Removed the root-level `python grader.py` / `python downloader.py` /
  `python diagnostic_stepik.py` invocation style entirely (no
  backward-compatibility shims) — the project now runs only via `python -m
  stepik_grader.X` or the `stepik-grader` console script, per the explicit
  decision to do a clean src-layout rather than keep thin root shims.

#### Fixed
- A stale `stepik_python_grader.egg-info` directory was still reporting
  the wrong version/dependencies before this move started; refreshed via
  `pip install -e . --no-deps` (see Issue #36's entry above) — caught
  proactively before it could cause confusion post-migration.

#### Verified
- 523 passed (3 skipped), 95.24% coverage; ruff check/format clean — all
  tests passed on the first full run after moving 16 source files and
  rewriting ~25 test files' imports, following the same exhaustive
  grep-before-edit audit methodology used for Issues #23 and #20's
  `grader.py` split.
- `ruff check .` found 29 line-length/import-sort violations from the
  longer `stepik_grader.` import prefixes pushing lines past 100 chars;
  resolved via `ruff check --fix .` + `ruff format .` plus two manual
  fixes in `tests/test_executor.py`'s SIGALRM tests.

#### Closes
- Issue #35

### move grader_core.py and reporter.py into core/ (#26)

#### Refactored
- Relocated `grader_core.py` → `core/grader_core.py` and `reporter.py` →
  `core/reporter.py` (via `git mv`, history preserved) — continuation of
  the Issue #23 restructuring. All internal (non-entry-point) modules now
  live under `core/`; only `grader.py`, `cli.py`, `config.py`,
  `downloader.py`, and `diagnostik_stepik.py` remain at the project root.
- Updated cross-imports: `core/grader_core.py`'s `from reporter import
  _print_case_verbose` → `from core.reporter import _print_case_verbose`;
  `core/reporter.py`'s `TYPE_CHECKING`-only `from grader_core import
  TestCase` → `from core.grader_core import TestCase`.
- Updated `grader.py`'s facade imports and `cli.py`'s imports to
  `core.grader_core`/`core.reporter`.
- Updated tests that imported these modules directly (bypassing the
  `grader.py` facade, which was unaffected by this move): `import
  grader_core`/`import reporter` → `from core import grader_core`/`from
  core import reporter` in `tests/test_menu_modes.py`,
  `tests/test_formatters.py`, `tests/test_grader_extra.py`, a local
  `from grader_core import` in `tests/test_grader_core.py`; and
  `unittest.mock.patch("reporter.X", ...)` string targets → `"core.reporter.X"`
  in `tests/test_grader_coverage_gap.py` (13 occurrences).

#### Verified
- 520 passed (3 skipped), 95.21% coverage; ruff check/format clean —
  every test passed on the first run after the move (no follow-up fixes
  needed), thanks to the exhaustive import/patch audit done before editing.
- End-to-end: `python grader.py --version`, `python grader.py` (interactive
  exit), and `python grader.py --mode 1 --file ...` against a real solution
  all still work correctly through the new `core.grader_core`/`core.reporter`
  import paths.

#### Closes
- Issue #26

### fix #25: real memory measurement in mode 4 (tracemalloc)

#### Fixed
- **core/microbench_runner.py** — `run_microbench()`'s Memory column always
  showed `0.00` in mode 4, because all 5 `timeit.repeat` runs share a single
  subprocess and mode 3's psutil-RSS-in-a-thread approach can't attribute
  memory to one run. Added `tracemalloc.start()`/`get_traced_memory()`
  around the `timeit.repeat()` call inside `bench_script`; the peak is
  printed as a distinct `MEM:<bytes>` line after the timing lines and parsed
  separately, then returned as `peak_memory_mb` (bytes converted to MB) in
  `run_microbench()`'s result dict — present on every return path (success,
  timeout, `OSError`) so callers can rely on the key always existing.
  Measures Python-heap peak, not process RSS — doesn't see memory allocated
  by C extensions, documented in the module docstring.
- **grader_core.py** — `run_microbench_mode()` no longer hardcodes
  `stats["peak_memory_mb"] = 0.0`; now tracks a running max across all
  benchmarked cases per solution, same as `run_benchmark()` does for mode 3.
  Function-call blocks (routed through `run_single_test`) already had real
  psutil-based `r["memory"]`; stdin blocks now get the new tracemalloc value
  from `run_microbench()`. The two measurement methods are mixed within one
  solution's aggregate max when its test cases include both block types —
  consistent with how timings were already aggregated across both paths.

#### Tests
- `tests/test_microbench_runner_module.py` — `run_microbench()` reports
  nonzero memory for an allocating solution, and the `peak_memory_mb` key is
  present (0.0) on the runtime-error, timeout, and `OSError` paths.
- `tests/test_microbench_grader.py` — `run_microbench_mode()` aggregates a
  nonzero `peak_memory_mb` for a memory-allocating stdin-mode solution; the
  existing simple-addition test now also asserts the key's presence.
- Fixed a stale mock in `tests/test_menu_modes.py` (`fake_microbench`) that
  returned a dict without `peak_memory_mb`, which would now raise `KeyError`.

#### Verified
- End-to-end: `python grader.py --mode 4 --dir ... --number 5` against a
  solution allocating a 500k-element list now shows `7.90 MB` instead of
  `0.00`.
- 520 passed (3 skipped), 95.21% coverage; ruff check/format clean.

#### Closes
- Issue #25

### Sprint 8.1: non-interactive argparse CLI

#### Added
- **cli.py** — `python grader.py --mode {1,2,3,4} [--file PATH] [--dir PATH]
  [--repeats N] [--number N]` and `python grader.py --version`, alongside the
  existing interactive menu (still the default when `--mode` is omitted).
  Extracted each menu branch's body into standalone `_run_mode_1/2/3/4()`
  functions with no logic changes, so both `_interactive_menu()` and the new
  argparse dispatch in `main()` call the same code. `main()` now takes an
  explicit `argv: list[str] | None = None` parameter (defaults to reading
  `sys.argv[1:]`) so tests can pass argument lists directly instead of
  depending on `sys.argv`, which contains pytest's own CLI flags during a
  test run.
- `__version__` moved from `grader.py` into `cli.py` (where `--version`
  needs it) and re-exported back through `grader.py`'s facade import —
  `cli.py` importing `__version__` from `grader.py` would have created a
  cycle, since `grader.py` already imports `main` from `cli.py`.

#### Tests
- `tests/test_cli.py` — `--version`, missing `--file`/`--dir` per mode
  (`SystemExit`), invalid `--mode` choice, and dispatch-with-correct-arguments
  for all four modes (including `--repeats`/`--number` defaults).

#### Verified
- End-to-end: `python grader.py --version`, `--mode 1 --file ...`,
  `--mode 3 --dir ... --repeats 3`, `--mode 4 --dir ... --number 100` all ran
  correctly against a real solution/test-dir. Hit a pre-existing
  `UnicodeEncodeError` when running directly under Git Bash on Windows
  (console defaults to cp1251) — reproduced identically on the *unchanged*
  interactive-menu path too, confirming it predates this change and isn't a
  regression; not fixed here (out of scope, environment-specific,
  `PYTHONIOENCODING=utf-8` resolves it).

#### Closes
- CLAUDE.md Sprint 8.1

### Sprint 7.2/7.3: BenchStats dataclass, microbench timeout helper

#### Added
- **grader_core.py** — `BenchStats` dataclass (`timings: list[float]` with
  `min`/`median`/`mean`/`stdev`/`max` properties and a `relative_to()`
  helper). `run_benchmark()` and `_micro_stats()` both build one internally
  and read its properties instead of independently recomputing
  `min()`/`statistics.median()`/etc. — same dict-shaped return values as
  before, so `reporter.py` and existing tests are unaffected. Added to
  `grader_core.__all__` (re-exported via `grader.BenchStats`).
- **core/microbench_runner.py** — `run_microbench_with_timeout(fn, timeout=60.0)`:
  runs an arbitrary `fn() -> list[float]` in a single-worker
  `ThreadPoolExecutor`, returning `[]` if it doesn't finish within `timeout`.
  **Not wired into `run_microbench_mode()`**: `run_microbench()` already
  wraps its `subprocess.run()` call in `timeout=60`, which reliably kills
  the child process and unblocks the caller — layering a
  `ThreadPoolExecutor` on top adds no protection and, on an actual timeout,
  would abandon the worker thread without killing whatever it was running
  (only `subprocess.run`'s own `timeout=` does that). Documented in the
  function's docstring; kept available for a future `fn()` that isn't
  already subprocess-bounded.

#### Tests
- `tests/test_grader_core.py` — `BenchStats` field computation, zero-stdev
  single-timing case, `relative_to()` (including zero-baseline), and a
  cross-check that `_micro_stats()`'s dict matches a direct `BenchStats`
  computation on the same timings.
- `tests/test_microbench_runner_module.py` — `run_microbench_with_timeout()`
  happy path and timeout-returns-`[]` path.

#### Closes
- CLAUDE.md Sprint 7 (tasks 7.2, 7.3) — Sprint 7 fully done (7.1 core split
  already closed #20 finding #4)

### Sprint 6: sys.executable, normalizers cleanup, config.py

#### Added
- **config.py** (new) — `GraderConfig` frozen dataclass + `load_config()` +
  module-level `CONFIG` singleton. Reads overrides from `[tool.stepik-grader]`
  in `pyproject.toml`; falls back to documented defaults if the file/section
  is absent. `grader_core.py`'s `TIMEOUT_SECONDS`/`ENCODING`/
  `SIMILAR_THRESHOLD`/`MUCH_SLOWER_THRESHOLD`/`MEASURE_CHILD_MEMORY`/
  `MICROBENCH_MAX_CASES` now read their values from `CONFIG` at import time
  (same names, same default values — `grader.py`'s `__all__` re-exports are
  unaffected). `core/executor.py`'s `TIMEOUT` also reads
  `CONFIG.executor_timeout`, wrapped in `try/except ImportError` with a
  literal `10` fallback: `python core/executor.py` runs as a subprocess
  script with `sys.path[0] == core/`, where `config.py` (at the project
  root) isn't importable.
- **tests/test_config.py** (new) — `GraderConfig` defaults, `frozen=True`
  mutation raises `FrozenInstanceError`, `load_config()` against the real
  `pyproject.toml`, missing-file fallback, unknown-key tolerance.

#### Fixed
- **core/executor.py** — replaced the platform-dependent
  `"python3" if sys.platform in {...} else "python"` with `sys.executable`,
  which always points at the interpreter that launched grader (fixes a
  latent Windows bug where `"python"` could resolve to a system interpreter
  outside the active venv).

#### Changed
- **core/normalizers.py** — `sort_lines()` and `normalize_whitespace()`
  added to `__all__` and marked "experimental" in their docstrings (neither
  is wired into any `grader_core.py` mode yet). Resolves the "not called in
  production" NOTE comments without deleting fully-tested, working utilities.

#### Closes
- CLAUDE.md Sprint 6 (tasks 6.1, 6.2, 6.3)

### narrow except, menu coverage, float() cleanup, security docs (#21)

#### Fixed
- **core/microbench_runner.py** — narrowed the broad `except Exception` around
  `subprocess.run`/`float(line)` parsing to `except (OSError, ValueError)`,
  the only two exception types that path can actually raise (subprocess
  spawn failure and unparseable timing output).
- **core/stepik_client.py** — simplified three redundant
  `float(str(x or default))` conversions to `float(x or default)` in
  `token_is_valid()` and the two `expires_at` computations; `float()`
  already accepts int/float/str directly, so the intermediate `str()`
  round-trip was a no-op.

#### Tests
- **tests/test_cli.py** (new) — covers `_interactive_menu()` branches left
  untested by the Sprint 7 split: mode 1/2/3/4 "not found" early-returns,
  the mode-3 and mode-4 happy paths (including error-row printing), and
  `_ask_bench_profile`/`_ask_micro_profile` custom-value prompts. `cli.py`
  coverage: 40% → 97%; total project coverage: 88.97% → 95.48%.

#### Docs
- **README.md** — rewrote "Ограничения и безопасность" to state the threat
  model explicitly (no OS-level sandbox, no resource limits beyond wall-clock
  timeouts, run only trusted solutions) and correct stale module paths
  (`executor.py` → `core/executor.py`, clarifying that modes 1-3 actually run
  through `grader_core.run_single_test`'s own `subprocess.Popen`, not through
  `core/executor.py`, which is exercised only by its test suite).

#### Closes
- Issue #21 (findings #7-#10) — closes the #18 tracker epic (#19, #20, #21 all done)

### split grader.py into grader_core/reporter/cli (#20 finding #4)

#### Refactored
- **grader.py** (1460 lines) split into three modules per Sprint 7 / issue #20
  finding #4:
  - `grader_core.py` — test-case loading, run-mode detection, wrapper
    codegen, and the `run_single_test`/`run_tests`/`run_benchmark`/
    `run_microbench_mode` execution pipeline.
  - `reporter.py` — the `_console`/`_RICH` rich-optional singleton, all
    `format_*`/`print_*` table functions, `_cprint`, and `_print_case_verbose`.
  - `cli.py` — the interactive menu (`_interactive_menu`), load-profile
    prompts, and a new `main()` entry point.
  - `grader.py` itself is now an 8-statement backward-compatibility facade:
    `from grader_core import *`, `from reporter import *`, plus explicit
    re-exports of every private (`_`-prefixed) name and non-`__all__` public
    name (`run_microbench`, `apply_relative_ranking`) that the test suite
    references directly as `grader.X` — `from X import *` does not pull in
    underscore-prefixed names, so these needed listing individually.
- **pyproject.toml** — added a `per-file-ignores` entry for `grader.py`
  (F401/F403/F405/I001), since every import in the facade is an intentional
  re-export that static analysis can't otherwise verify as "used."

#### Fixed (test suite)
- Several tests patched `grader._RICH` / `grader._console` / `grader.Table` /
  `grader.Text` / `grader.run_tests` / `grader.run_single_test` /
  `grader.run_microbench` expecting to influence behavior inside functions
  that now live in `reporter.py`, `grader_core.py`, or `cli.py`. Python
  resolves those names via each function's *own* module globals at call
  time, not through `grader.py`'s re-exported copy, so patching `grader.X`
  no longer had any effect on the patched function's behavior (two cases —
  `test_grader_coverage_gap.py`'s rich-branch tests and
  `test_menu_modes.py`'s benchmark-mode patches — actually failed with
  `AssertionError`/`KeyError`; a few others silently degraded into testing
  the wrong branch without erroring). Updated the patch targets in
  `tests/test_grader_coverage_gap.py`, `tests/test_menu_modes.py`,
  `tests/test_grader_extra.py`, and `tests/test_formatters.py` to point at
  the module that actually owns each name (`reporter.X`, `cli.X`, or
  `grader_core.X`).

#### Verified
- 465 passed, 3 skipped, 88.97% coverage; `ruff check`/`ruff format --check`
  clean; `echo 0 | python grader.py` smoke-tested end-to-end.

#### Closes
- Issue #20, finding #4

### dedupe parser, close import-cycle risk (fix #19)

#### Fixed
- **grader.py** — removed the local `_parse_testblock_file` definition, which
  had drifted into an exact duplicate of `core/parsers.py`'s
  `parse_testblock_file`. `grader.py` now imports the canonical function
  (aliased as `_parse_testblock_file` to preserve the existing private name
  used by `tests/test_testblock.py` and `tests/test_grader_mock.py`).
- **downloader.py** — replaced the local `from grader import
  _parse_testblock_file` (inside `_download_github_tests()`) with a top-level
  `from core.parsers import parse_testblock_file`. `downloader.py` no longer
  imports `grader.py` at all — the local import was only masking the fact
  that both modules depended on logic that already had a canonical home in
  `parsers.py`.
- **README.md** / **CLAUDE.md** — corrected stale test-count references
  (355 → 461) and updated the DAG/structure diagrams to reflect both the
  `core/` restructuring (#23) and the removed `downloader → grader` edge.

#### Closes
- Issue #19 (findings #1 and #2; #3 addressed via the doc corrections above)

### move internal modules into core/ (closes #23)

#### Refactored
- Relocated `executor.py`, `normalizers.py`, `parsers.py`, `storage.py`,
  `stepik_client.py`, `oauth_flow.py`, and `microbench_runner.py` into a new
  `core/` package, separating entry-point scripts (`grader.py`,
  `downloader.py`, `diagnostik_stepik.py`) from internal Infrastructure/
  Utility modules. Added `core/__init__.py`. Updated every import site
  across root scripts, intra-`core` imports, and tests (including
  `unittest.mock.patch` target strings and `executor.py`'s subprocess
  self-invocation paths). Added `"core"` to ruff's isort
  `known-first-party` list.

#### Closes
- Issue #23

---

Более старые (до-версионные) записи — см.
[docs/changelog-archive.md](docs/changelog-archive.md).
