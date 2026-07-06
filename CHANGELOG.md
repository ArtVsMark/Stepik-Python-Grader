# Changelog

## [Unreleased]

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

## [unreleased] / 2026-06-25 — refactor: extract parsers.py (fix #9)

### Refactored
- `parsers.py` — новый Infrastructure/Utility-модуль: единственная публичная
  функция `parse_testblock_file(text: str) -> list[str]`.
  Извлечена из `grader.py` (была приватной `_parse_testblock_file`), что
  устранило потенциально циклическую зависимость `downloader.py → grader.py`.
- `grader.py`: удалена дефиниция `_parse_testblock_file`;
  добавлен `from parsers import parse_testblock_file`.
- `downloader.py`: удалён lazy import `from grader import _parse_testblock_file`
  внутри `_download_github_tests()`; заменён top-level
  `from parsers import parse_testblock_file`.

### Architecture
- Граф зависимостей стал ациклическим:
  ```
  grader.py     → parsers.py
  downloader.py → parsers.py
  ```
  Вместо прежнего:
  ```
  downloader.py → grader.py  ← (lazy, циклически-опасный)
  ```

### Tests
- 12 новых тестов в `tests/test_parsers.py` — прямое покрытие
  `parse_testblock_file()`: базовый, пустые блоки, `# INPUT DATA:`, многострочные
  блоки, нет-маркеров, пробелы, комментарии внутри блока, параметрические.

### Closes
- Issue #9

---

## [unreleased] / 2026-06-24 — OAuth рефакторинг

### Added
- `oauth_flow.py` — новый Infrastructure/Auth модуль: единый OAuth2-фасад для `downloader.py` и `diagnostik_stepik.py`
  - `load_secrets(path)` → `tuple[str, str, str]` — чтение учётных данных из `secrets.json`
  - `load_secrets_dict(path)` → `dict` — полный словарь (с токенами)
  - `authorize_and_get_token(client_id, secret, uri)` — полный OAuth2 flow
  - Реэкспорт канонических функций из `stepik_client`: `token_is_valid`, `wait_for_auth_code`, `authorize_via_browser`, `create_user_session`, `make_session`, `refresh_access_token`

### Refactored
- `downloader.py`: заменён inline `load_secrets` на `oauth_flow.load_secrets_dict`; OAuth-импорты через `oauth_flow`
- `diagnostik_stepik.py`: удалён inline `load_secrets` и дублирующие OAuth-функции; импортируются из `oauth_flow`
- Устранено дублирование `load_secrets` (dict vs tuple) через единый фасад

### Tests
- 25 новых тестов в `tests/test_oauth_flow.py` — `load_secrets`, `token_is_valid`, `wait_for_auth_code`, `OAuthHandler`, `authorize_and_get_token`; `oauth_flow.py` покрыт на 100%
- Итого: **260 passed**, 0 failed, 0 warnings

## [unreleased] / 2026-06-24 — Tooling, rename & cleanup

### Added
- GitHub Actions CI (`.github/workflows/ci.yml`): `pytest` + `ruff` на Python 3.11 и 3.12; бейдж CI добавлен в `README.md`
- `pytest-cov>=5.0` в dev-зависимостях; `addopts` с `--cov` в `pyproject.toml`

### Changed
- Переименован `at_first.py` → `downloader.py`; все импорты и упоминания в README обновлены (`test.py` → `grader.py`)
- Удалены дублирующие/сломанные workflow `lint.yml` и `test.yml`, оставлен единый `ci.yml`

### Removed
- Зависимость `chardet` удалена из `requirements.txt`/`pyproject.toml` и из кода (файлы тестов читаются в UTF-8)
- Удалён мёртвый код; `microbench_runner.py` и `normalizers.py` помечены NOTE-комментариями как пока не импортируемые из `grader.py`

## [unreleased] / 2026-06-24 — Audit fixes + rich output

### Added
- `rich>=13.0` dependency: colored tables in all 4 modes (green=AC, red=WA/TLE/RE, yellow=SLOWER)
- WA diff: expected vs actual output shown on test failure (verbose mode)
- Verdicts AC / WA / TLE / RE per test case (replaces OK/FAIL/timeout/error)
- Progress bar (rich.progress.track) in modes 2 and 3
- `_apply_run_mode_override()` helper extracted from duplicate code in `run_tests`/`run_benchmark`

### Fixed
- Mode 4 (microbench) stdin-mode: solution stdout no longer contaminates timing measurements (redirected to devnull during timeit)
- `conftest.py`: stale `collect_ignore = ["test.py"]` → `["grader.py"]`
- `pyproject.toml`: removed non-existent `t.py` from coverage omit list
- `stepik_client.py`: `_get_with_retry` now raises `RuntimeError` instead of `None` when called with 0 retries
- `downloader.py`: typo "обрабного шага" → "обработки шага"
- `executor.py`: `__builtins__` now explicitly set via `import builtins` for cross-module determinism
- `grader.py`: `_build_function_wrapper` uses `repr()` for path interpolation (Windows-safe, consistent with `_build_call_wrapper`)
- `grader.py`: added module-level docstring
- `grader.py`: removed misleading comment about `run_solution` usage

### Notes
- `microbench_runner.py` and `normalizers.py`: added NOTE comments clarifying these modules are not currently imported by `grader.py` (preserved for future refactoring)

## [Unreleased] — June 2026 — menu modes 2/3/4 test-dir & run-mode fixes

### 🐛 Bug fixes

- **grader.py (mode 2)** — `_interactive_menu` now resolves the test directory
  **per solution** via `_resolve_test_dir(path)` instead of reusing one folder-level
  `test_dir` for every file. Folders containing solutions for different tasks are
  now graded against each task's own tests. Falls back to the folder-level
  `test_dir` when a solution has no individual tests directory.
- **grader.py (mode 3)** — same per-solution `test_dir` resolution as mode 2.
- **grader.py — `run_benchmark()`** — now calls `_detect_run_mode()` and promotes
  `stdin` cases to `function` for function-mode tasks, matching `run_tests()`.
  Previously function-mode solutions were benchmarked in the wrong stdin mode.
- **grader.py — `run_microbench_mode()` (mode 4)** — function-call blocks
  (`print(func(...))`, detected via `_is_python_code_block`) are now timed through
  `run_single_test` (subprocess) instead of being fed as stdin to `timeit`, which
  broke completely. Plain stdin blocks still use the `timeit` path.
- **grader.py — `run_microbench()`** — the solution source is now passed via a
  temporary file read inside the bench script rather than embedded in a `'''`
  heredoc, eliminating breakage on solutions containing triple quotes.
- **grader.py — `_resolve_test_dir_from_input(is_dir=True)`** — now recognizes
  Format 3 (`input.txt` + `output.txt`) directly in the given directory, not only
  a `tests/` subdir.

### ✅ Tests added

- `tests/test_menu_modes.py` — per-solution `test_dir` in mode 2 (+ folder
  fallback), `run_benchmark` applying function run-mode, `run_microbench_mode`
  routing function-call blocks to subprocess and stdin blocks to `timeit`, and
  `_resolve_test_dir_from_input` Format 3 handling.
- `tests/test_loader.py` — `_resolve_test_dir` across `tests/` subdir,
  python-generation `input.txt`/`output.txt` (alongside and in parent), and
  legacy `.clue` layouts.

## [Unreleased] — June 2026 — ZIP→Format 3 conversion, GitHub test download

### ✨ Features

- **downloader.py** — `_download_zip_tests()` rewritten to convert the Stepik ZIP
  layout (`1`, `1.clue`, `2`, `2.clue`, …) directly into Format 3
  (`tests/input.txt` + `tests/output.txt` with `# TEST_N:` markers) instead of
  dumping raw numbered files. Blocks are emitted in numeric order and a leading
  directory prefix in the archive is stripped automatically. Verified against
  the real archive `tests_2491371.zip` (4 cases).
- **downloader.py** — new `_download_github_tests()` plus module-level
  `_GITHUB_TREE_RE` / `_GITHUB_CONTENTS_API`. Downloads tests from a GitHub
  `tree`/`blob` URL via the Contents API. Handles directories that already ship
  `input.txt` + `output.txt` (downloaded as-is) and directories with `N` +
  `N.clue` files (converted to Format 3). `save_task_files()` now calls it
  instead of printing a "download manually" stub.

### ♻️ Refactor

- **diagnostik_stepik.py** — removed the duplicate `parse_stepik_step_url`;
  it is now imported from `downloader`. The diagnostic `load_secrets` (tuple
  return) is kept as-is.

### ✅ Tests added

- `tests/test_downloader.py` — covers ZIP→Format 3 conversion (basic, prefixed,
  numeric ordering, empty/bad archive), the GitHub regex, GitHub download for
  both layouts, external-link extraction, and a ZIP→Format 3→`load_test_cases`
  round-trip.

## [Unreleased] — June 2026 — OOP/Samurai/Professional integration coverage

### ✅ Tests added

- `tests/test_integration_repos.py` — 11 end-to-end tests exercising the full
  `run_tests` pipeline against real data from all three `python-generation`
  repositories. No `grader.py` changes were required: the existing block
  detection (`_is_python_code_block`) and call wrapper (`_build_call_wrapper`)
  already handle every block type these repos use.
  - **OOP Module_4.3.10** — `Vector` class instantiation, attribute access,
    method calls, and a `for x, y in array:` loop over tuples.
  - **OOP Module_7.1.23** — class hierarchy with `issubclass(...)` checks; all
    class names resolve through `from solution import *`.
  - **Samurai Module_2.10.15** — custom `InvalidDateError` raised inside
    `try/except` blocks; the exception class is imported from the solution.
  - **Professional Module_10.2.20** — `filterfalse` (covered by the injected
    `from itertools import *`) plus an `import string` statement inside the
    test block itself.
  - **Samurai Module_13.1.1** — `group_ranges` function returning a list of
    formatted strings.
  - Negative control verifying the grader flags mismatched output, plus a
    parametrized check that each representative block is classified as Python
    code (function mode) rather than stdin.

## [Unreleased] — June 2026 — Module_2/4/6 block-detection fixes

### 🐛 Fixes

- **grader.py** — Renamed `_is_python_call_block()` → `_is_python_code_block()`
  and broadened the heuristic: a block is now classified as Python code
  (function sub-type) when it parses as a valid AST **and** contains at least
  one `ast.Name` node, instead of requiring a top-level `Expr(Call(...))`.
  This fixes Module_6.5.x blocks that start with an assignment and a `for`
  loop (e.g. `result = wins([...])` + `for ...: print(...)`), which were
  previously misrouted to stdin mode. Bare data such as `10\n20\n30` (no
  `Name` nodes) and `04.11.2021` (SyntaxError) still resolve to `stdin`.
  All callers in `load_test_cases()` and `run_single_test()` updated.
- **grader.py** — `_build_call_wrapper()` now injects stdlib wildcard imports
  (`from collections import *`, `datetime`, `itertools`, `functools`) **before**
  importing the solution, so blocks that reference `ChainMap`, `Counter`,
  `OrderedDict`, etc. without an explicit import (Module_6.10.x) run correctly.
  The solution import remains last, so the solution's public names take
  precedence over the wildcard-imported stdlib names.
- **grader.py** — `_parse_testblock_file()` now preserves **empty** `# TEST_N:`
  blocks as `''` (Module_4.1.10 TEST_5) instead of dropping them, keeping
  input/output block indices aligned.

### ✅ Tests added

- `tests/test_testblock.py` — 13 tests covering `_is_python_code_block`
  (numbers, dates, plain text, function calls, for-loop blocks, ChainMap
  assignments, empty/whitespace) and `_parse_testblock_file` (basic,
  empty/trailing-empty blocks, multiline, `# INPUT DATA:` handling).

## [Unreleased] — June 2026 — python-generation format support

### ✨ Features

- **grader.py** — Added support for the `python-generation/Professional`
  test format (Module_3). Tests live as a single `input.txt` + `output.txt`
  pair with `# TEST_N:` block markers instead of per-test files.
  - `_parse_testblock_file()` — parses a file with `# TEST_N:` markers into
    a list of block contents (ignores `# INPUT DATA:` / leading headers).
  - `_is_python_call_block()` — returns `True` when a block is valid Python
    containing a top-level `Expr(Call(...))` (function-call sub-type);
    plain data such as `04.11.2021` → `False` (stdin sub-type).
  - `_build_call_wrapper()` — builds a runner that imports all public names
    from the solution module and executes the test block verbatim (the block
    already contains the full `print(func(...))` call; no `inspect.signature`).
  - `load_test_cases()` — new Format 3 check (highest priority) that reads
    `input.txt`/`output.txt`, auto-detecting each block as `function` or
    `stdin`.
  - `_resolve_test_dir()` — now also locates `input.txt` + `output.txt` next
    to the solution or in its parent directory.
  - `run_single_test()` — function-mode now dispatches to `_build_call_wrapper`
    for Python call blocks and keeps `_build_function_wrapper` for the legacy
    variable-declaration format.
  - Verified on real tasks: Module_3.1 (function-call, 7/7) and Module_3.2
    (stdin, 4/4).

## [Unreleased] — June 2026 — Audit Sprint

### 🔴 Critical fixes

- **downloader.py** — OAuth HTTP-server: added `server.timeout = 120` and
  extracted `wait_for_auth_code()` helper; replaced bare `RuntimeError` on
  timeout with `TimeoutError`; extracted `_make_oauth_handler()` factory so
  `OAuthHandler` is no longer defined at module scope (removes stale closure
  risk).
- **downloader.py** — Added `from typing import cast`; replaced
  `int(str(x) or 0)` patterns with `cast(int, ...)` for `section_id`,
  `course_id`, `step_id`; `cast(str, ...)` for `lesson_title`.
- **downloader.py** — Replaced broken `download_and_extract_submissions()`
  stub (was calling non-existent `/api/stepics/1` endpoint) with a working
  implementation that calls `/api/submissions?step=<id>&order=desc` and saves
  each `reply.code` as `submissions/submission_<id>.py`.
- **downloader.py** — Fixed typo `"нет хватает"` → `"не хватает"`.
- **downloader.py** — PEP 8 import order fixed; `from __future__ import
  annotations` moved to line 1.
- **grader.py** — `avg_time` in `FAILED` early-return branch now uses
  `total_time / passed_tests if passed_tests else total_time` instead of
  the nonsensical `avg_time = total_time`.
- **grader.py** / **executor.py** — `from __future__ import annotations` added.
- **microbench_runner.py** — Removed unused `func_name: str = "<exec>"`
  field from `MicrobenchResult`; added explanatory comment.

### ✅ Tests added

- `tests/test_slugify.py` — 7 tests covering `slugify()`: basic,
  Cyrillic `ё`, special chars, empty/whitespace-only input, truncation to 80
  chars, spaces-to-dashes, leading/trailing dash stripping.
- `tests/test_microbench.py` — 7 tests covering `MicrobenchResult`,
  `apply_relative_micro`, `run_microbench`, and
  `SIMILAR_THRESHOLD_PERCENT`.

### 📦 Dependencies / tooling

- `pyproject.toml` already contains `pytest>=8.0`, `ruff>=0.4`, `mypy>=1.10`
  under `[project.optional-dependencies] dev`.
- `[tool.pytest.ini_options]` section confirms `testpaths = ["tests"]`.

### 💡 Deferred (Sprint 3+)

- Extract `stepik_client.py` with all API call functions from `downloader.py`.
- Split `grader.py` into `runner.py`, `benchmark.py`, `microbench.py`,
  `display.py`.
- Add `ruff` pre-commit hook and GitHub Actions CI.
