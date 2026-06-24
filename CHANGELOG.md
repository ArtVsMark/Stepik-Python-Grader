# Changelog

## [Unreleased] — June 2026 — ZIP→Format 3 conversion, GitHub test download

### ✨ Features

- **at_first.py** — `_download_zip_tests()` rewritten to convert the Stepik ZIP
  layout (`1`, `1.clue`, `2`, `2.clue`, …) directly into Format 3
  (`tests/input.txt` + `tests/output.txt` with `# TEST_N:` markers) instead of
  dumping raw numbered files. Blocks are emitted in numeric order and a leading
  directory prefix in the archive is stripped automatically. Verified against
  the real archive `tests_2491371.zip` (4 cases).
- **at_first.py** — new `_download_github_tests()` plus module-level
  `_GITHUB_TREE_RE` / `_GITHUB_CONTENTS_API`. Downloads tests from a GitHub
  `tree`/`blob` URL via the Contents API. Handles directories that already ship
  `input.txt` + `output.txt` (downloaded as-is) and directories with `N` +
  `N.clue` files (converted to Format 3). `save_task_files()` now calls it
  instead of printing a "download manually" stub.

### ♻️ Refactor

- **diagnostik_stepik.py** — removed the duplicate `parse_stepik_step_url`;
  it is now imported from `at_first`. The diagnostic `load_secrets` (tuple
  return) is kept as-is.

### ✅ Tests added

- `tests/test_at_first.py` — covers ZIP→Format 3 conversion (basic, prefixed,
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

- **at_first.py** — OAuth HTTP-server: added `server.timeout = 120` and
  extracted `wait_for_auth_code()` helper; replaced bare `RuntimeError` on
  timeout with `TimeoutError`; extracted `_make_oauth_handler()` factory so
  `OAuthHandler` is no longer defined at module scope (removes stale closure
  risk).
- **at_first.py** — Added `from typing import cast`; replaced
  `int(str(x) or 0)` patterns with `cast(int, ...)` for `section_id`,
  `course_id`, `step_id`; `cast(str, ...)` for `lesson_title`.
- **at_first.py** — Replaced broken `download_and_extract_submissions()`
  stub (was calling non-existent `/api/stepics/1` endpoint) with a working
  implementation that calls `/api/submissions?step=<id>&order=desc` and saves
  each `reply.code` as `submissions/submission_<id>.py`.
- **at_first.py** — Fixed typo `"нет хватает"` → `"не хватает"`.
- **at_first.py** — PEP 8 import order fixed; `from __future__ import
  annotations` moved to line 1.
- **test.py** — `avg_time` in `FAILED` early-return branch now uses
  `total_time / passed_tests if passed_tests else total_time` instead of
  the nonsensical `avg_time = total_time`.
- **test.py** / **executor.py** — `from __future__ import annotations` added.
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

- Extract `stepik_client.py` with all API call functions from `at_first.py`.
- Split `test.py` into `runner.py`, `benchmark.py`, `microbench.py`,
  `display.py`.
- Add `ruff` pre-commit hook and GitHub Actions CI.
