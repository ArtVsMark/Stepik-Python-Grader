# Changelog

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
