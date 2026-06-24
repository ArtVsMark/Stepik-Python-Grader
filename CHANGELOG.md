# Changelog

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
