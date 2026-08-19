# Working with the grader

> Installation, setup and OAuth — [installation.en.md](installation.en.md);
> documentation map — [../README.md](../README.md); module architecture —
> [architecture.md](../dev/architecture.md). Reference for configuration,
> test-case formats, limits and security —
> [configuration.md](configuration.md).

## Contents

- [Quick start](#quick-start)
- [First example in 2 minutes](#first-example-in-2-minutes)
- [Non-interactive usage (CLI flags)](#non-interactive-usage-cli-flags)
- [Web interface (`--serve`)](#web-interface---serve)
- [IDE integration](#ide-integration)
- [Additional flags](#additional-flags)
- [Downloading a task](#downloading-a-task)
- [Operating modes](#operating-modes)
- [Formats, configuration, security](#formats-configuration-security)

---

## Quick start

Run the interactive menu:

```bash
python -m stepik_grader       # reliable way: works even if stepik-grader is not on PATH
# or shorter, if the command is on PATH (Method A / activated venv):
stepik-grader
```

On startup a menu appears (Russian by default; `--lang en` — English):

```
==================================================
  Stepik Python Grader
  📥 No task on disk yet? Item 8 — download it from Stepik by step URL.
==================================================
  1. Check one solution
  2. Check all solutions in a folder
  3. Benchmark solutions in a folder
  4. Micro-benchmark (timeit) for a folder
  5. Practice — common mistakes from run history
  6. Web interface in the browser
  7. Run-history recording: ON/OFF (pick to toggle)
  8. 📥 Download a task from Stepik (by step URL)
  9. 💬 Report a problem / suggest an idea
  0. Exit
==================================================
Select mode [0-9]:
```

The menu loops: after a mode finishes it is shown again until you pick `0`.
Item `6` opens the web interface (equivalent to `--serve`; `Ctrl+C` returns to
the menu). Item `8` downloads the task + tests from Stepik by step URL
(equivalent to `python -m stepik_grader.downloader`) and immediately offers to
check it with the path pre-filled — no need to type the multi-segment slug path
by hand; declining (`n`) simply returns to the menu, and if several tasks were
downloaded in one go, the last one is offered. Item `7` toggles run-history
recording and remembers the choice between launches — in a per-user settings
file (see [configuration.md § Where the settings file lives](configuration.md#где-лежит-файл-настроек)),
so the choice applies to all course folders, just like the history database
itself (in the CLI history is off by default — opt-in, see
[ADR-0002](../dev/adr/0002-history-opt-in.md)).

Item `9` — feedback: asks for the report type (bug / idea / a Stepik task is
graded incorrectly) and a short description, attaches auto-collected environment
info (version, OS, Python, `--sandbox` state, commit when run from a git clone),
prints a **preview of what will be sent**, and only after confirmation opens the
browser with a pre-filled GitHub issue form. The grader never sends anything
itself: the user presses Submit. Solution code and tokens never enter the
report; the home path is collapsed to `~`. The same channel exists in the web
interface — the 💬 button in the topbar (see
[web-interface.md § Feedback](web-interface.md#обратная-связь-кнопка--в-topbar)).

---

## First example in 2 minutes

Let's check a simple "add 1 to a number" solution without Stepik — by hand.

**Step 1. Create the solution file** `task.py` in any folder:

```python
# task.py
n = int(input())
print(n + 1)
```

**Step 2. Create a `tests/` folder next to it with one pair of files** — input
and expected output. The input file is named with just a number, the expected
output — the same name with `.clue`:

```
task.py
tests/
  1          ← contents: 4      (this is fed to the solution on stdin)
  1.clue     ← contents: 5      (this is the expected output)
```

> That is: `tests/1` contains the string `4`, `tests/1.clue` — the string `5`.
> You can add more cases: `tests/2` + `tests/2.clue`, and so on.

**Step 3. Run a single-solution check (mode 1):**

```bash
python -m stepik_grader --mode 1 --file task.py
```

**Step 4. Read the result.** The `Passed` column shows `1/1`, the status is
`OK`:

```
File        Passed   Total time   Avg time   Memory, MB   Status   Fail test
task.py       1/1       0.0123     0.0123         4.20       OK           -
```

If the solution's output doesn't match the `.clue`, the status is `FAIL`, and
with `--verbose` the grader shows for each failed case its **input**
(`Input:`), expected and actual output, then a line-by-line diff — you can
reproduce the failure straight from the report without opening `tests/`. The
input is printed on **any** failure, not only a wrong answer: crashed with an
exception (`RE`) or exceeded the time limit (`TLE`) — the report shows on which
data. On `TLE` the `Expected`/`Actual` values remain next to it; on `RE` they
don't — the solution produced no answer at all, and the error text says so.
Long values and diffs are truncated with a note of the dropped volume
(`… another 4600 chars`, `… another 480 diff lines`) so that one "chatty" case
doesn't wash the other verdicts out of the scrollback; the full data stays in
`tests/` and in `--output json`.

**Steps 3–4 in the browser (no console flags).** The solution file and the
`tests/` folder from steps 1–2 are the same; instead of `--mode 1`, start the
web interface from the same folder:

```bash
python -m stepik_grader --serve      # the address is printed to the console
```

Open the printed address (`http://127.0.0.1:8000` by default), in the "Check
solutions" section pick the **"Single file"** mode, put `task.py` in the path
field — by default it already contains the server's working directory, the one
`--serve` was started from — and press **"▶ Run"**. The result is the same table
as in the CLI: clicking a file name expands the test cases and the diff on WA;
there is no separate `--verbose` switch to enable. On the very first launch a
start screen appears first — the "▶ Check solution" button leads exactly to this
section.

> **Where to get tests for real Stepik tasks?** They can be downloaded
> automatically — see [Downloading a task](#downloading-a-task) below
> ([OAuth setup](installation.en.md#working-with-the-stepik-api-oauth) is
> required). The `tests/` folder format is exactly the same.

---

## Non-interactive usage (CLI flags)

> This section is about running without a human at the keyboard (CI, scripts,
> IDE tasks). For everyday hands-on work, go straight to
> [Web interface (`--serve`)](#web-interface---serve) below: some features
> exist **only** there — the playground (not to be confused with the OS-level
> `--sandbox` isolation), step-by-step execution trace, a code editor with
> saving, submitting the solution to Stepik. The "Glossary", "Rules (PEP)",
> "Practice" and "Progress" sections in the web UI are interactive, whereas the
> CLI only has `--lint`/`--insights` summaries and the `--export-progress`
> export.

For CI/scripts, without interactive input:

```bash
stepik-grader --version                                    # version and exit
stepik-grader --mode 1 --file path/to/task.py               # mode 1
stepik-grader --mode 2 --dir path/to/folder                 # mode 2
stepik-grader --mode 3 --dir path/to/folder --repeats 15    # mode 3 (default 15)
stepik-grader --mode 4 --dir path/to/folder --number 1000   # mode 4 (default 1000)
```

Equivalent via `python -m`: `python -m stepik_grader --version` or
`python -m stepik_grader.grader --version`, etc. (the package contains
`__main__.py`, so the short form `python -m stepik_grader` works).

Without `--mode` the usual interactive menu is shown.

### Exit codes

Modes 1 and 2 report the outcome through the **exit code** — that is what a CI
gate is built on, with no JSON parsing:

| Code | Meaning |
|---|---|
| `0` | all test cases passed |
| `1` | there are failing cases (`WA`, `RE`, timeout) |
| `2` | there was nothing to check: file or folder not found, no solutions, the case set is empty (`NO TESTS`) |

```bash
stepik-grader --mode 2 --dir tasks/ || echo "the check did not pass: $?"
```

**Why `2` is separate from `1`.** "The tests failed" and "there are no tests"
call for different actions: the first means fixing the solution, the second
means fixing the environment (the task was not downloaded, the folder is
wrong). Previously both cases, like success, returned `0`, and the gate went
green on an empty set — that is, it did not work exactly when it was needed.

Modes 3 and 4 are comparison and micro-bench; they have no "correct/incorrect"
verdict, so they always exit with `0`. Under `--watch` the code is `0` as well:
the loop lives until `Ctrl+C`, and it has no "final result".


---

## Web interface (`--serve`)

For anyone to whom the console is a barrier (beginners, work from an IDE) there
is a local web interface. The "Check solutions" section repeats all four CLI
modes (the "Mode 1…4" buttons — one file / folder / benchmark /
micro-benchmark), plus separate "Task downloader", "Glossary" and "Sandbox"
sections (see [web-interface.md](web-interface.md)):

- **Correctness** (modes 1/2) — an AC/WA table with time and memory; clicking a
  file name expands the test cases and the diff on WA.
- **Benchmark** (mode 3) — solutions are ranked by median (fastest first) with a
  SIMILAR/SLOWER/MUCH_SLOWER verdict, as in CLI mode 3; mode 4 is the
  micro-benchmark via `timeit`.

Modes 3/4 (benchmark/micro-benchmark) run asynchronously in the web UI
(`POST /api/v1/runs` + polling) — the tab is not blocked for the whole duration
of the run: a progress bar is shown and a "Cancel" button is available
(best-effort, it really does stop the child process). Modes 1/2 (a file or a
folder with ordinary tests) stay on the previous synchronous `/api/grade`.

When a solution crashes (the RE verdict), an **error card** appears under the
traceback: the exception type, a short explanation and a link to the detailed
card in **our own** "Glossary" section (`#/glossary/<id>`). The same hint is
printed by the CLI in verbose mode (`--mode 1`, or `--mode 2 --verbose`). It
works offline — all the content is local: a compact set of explanations for the
common built-in exceptions plus the bundled card database. The hint never links
out to the external
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python): that project is
a showcase copy of this database, and the link would lead to an outdated version
of the card.

**Launching without the command line (the launcher) — the lowest barrier.** If
even the terminal is too much, there is a launcher window: `stepik-grader-gui`
(after installation; on Windows it is a shortcut without a console window) or
`python -m stepik_grader.launcher`. The window offers an explicit choice of
startup variant ("Plain server" / "Server with `--sandbox` isolation"), the port
(with a "busy" check), the working folder, a "Start" button (which brings the
server up as a separate `--serve` process and opens the browser) and "Stop" with
the status and the address. The default working folder is the one where the
tasks are set up (`root_dir` from `stepik_config.json`, searched from the current
directory upwards and in the home folder), not the shortcut's directory: under
the path it says "Tasks found: N", so a wrong folder is visible before the
browser opens. If there are no tasks, the counter is replaced by the next step —
download a task with the downloader or pick another folder.

**The launch profile is the first row of the window.** Pick a saved profile and
the fields are filled from it; "Save as…" stores the current set under a name,
"Delete" removes the record without touching the form fields (the record is
deleted, not your current selection). The "— custom set —" item means the fields
match no saved profile. The same profile is available from the terminal:
`stepik-grader --serve --profile "name"` (see
[`--profile`](#--profile--a-saved-set-of-launch-parameters)).

**The choice is remembered between launches.** Isolation, history recording,
language, port and folder are saved into `.grader_settings.json` at the moment
the server starts, and the next time the window opens they are pre-filled. This
matters more than convenience: without that memory, whoever works with
`--sandbox` had to enable it every time — and one day forgot, silently losing a
security setting. Worth knowing:

- the write happens **at startup**, not when the window is closed: a window
  closed right after the server started still leaves its trace;
- "did not choose" and "chose a value that happens to equal the default" are
  different states: the first adds nothing to the command and leaves the
  decision to `pyproject.toml`/the server (see
  [ADR-0012](../dev/adr/0012-launcher-settings-store.md));
- the `--lang` flag beats the remembered language — it is a "for now" decision;
- if the remembered folder is gone (external drive, moved project), the window
  silently falls back to the computed one: a path that does not exist would show
  zero tasks and suggest that the tasks are lost rather than the folder.

The isolation mode is switched **only** here or in the CLI, never from the web
interface itself — otherwise the server would kill itself while sending the
response.

In a headless environment, or a Python build without `tkinter`, the window is
not created, but the launcher **brings the web interface up itself** (`--serve`)
and prints the address to `stderr`: it knows the whole command, and advising you
to type it by hand would be dishonest — the more so on Windows, where the
shortcut goes through `pythonw.exe` with no console for anyone to read the advice
in. The port is chosen the same way as in the window: taken by a stranger — the
nearest free one is used; taken by an already running server of ours — its
address is printed and a second one is not started. The exit code comes from the
server, so a script can tell "came up" from "could not".

From the terminal — the same via flags:

```bash
stepik-grader --serve                          # http://127.0.0.1:8000
stepik-grader --serve --port 9000              # a different port
stepik-grader --serve --root C:\StepikTasks    # explicit working directory
stepik-grader --serve --no-root-confinement    # no path confinement (see below)
```

- **Localhost only** (`127.0.0.1`) — not exposed to the network.
- **No new dependencies** — stdlib `http.server`, reuses the same grading
  logic as the CLI (`run_tests`/`run_benchmark`).
- The default path field is the server's working directory; the last path and
  mode are remembered (localStorage).
- The path field takes a solution file (`.py`) or a folder with solutions;
  tests are resolved the same way as in modes 1/2/3.
- The same threat model as the CLI: without `--sandbox` there is no isolation —
  run your own solutions. `--serve --sandbox` enables OS isolation in the web
  UI too. Full threat model — [configuration.md § Limits and
  security](configuration.md#ограничения-и-безопасность).

**Submitting a solution to Stepik.** In mode 1 (one file), under the editor
there is a **"Submit to Stepik"** button: it sends the current solution to
Stepik and polls the verdict without leaving the browser (the submit core is
`create_attempt`/`submit_solution`/`poll_submission`/`submit_and_wait` in
`core/stepik_client.py`, the web wrapper is the async `stepik_submit` job via
`POST /api/stepik/submit`, see [api.md](../dev/api.md#post-apistepiksubmit)).
Requires the task's `step_id` (written when downloading via the downloader)
and Stepik authorization. There is no CLI submit command yet — web only.

**Working directory (`--root`).** All paths from the requests
(`/api/grade`, `/api/source`, `/api/solutions`, `/api/save-solution`,
`POST /api/v1/runs`) are confined to the server's working directory — by
default the cwd at `--serve` start, or an explicit `--root <dir>`. A path
outside it (including via `../` traversal or a symlink pointing outside —
resolved with `Path.resolve()` before the check) is rejected with `403`
(`{"kind": "error", "message": ...}`). `--no-root-confinement` is an explicit
rollback to the previous behavior (access to any path on disk); a deliberate
user choice, not the default — use it when you run the grader only for
yourself and know what you are doing. `/api/download` (the `root` field in
the request body — where to DOWNLOAD a task) is not covered by this
confinement — a separate concern.

> Drag-and-drop file upload is the next iteration.

---

## IDE integration

> Check a solution right from the editor, without switching to the terminal.

**VS Code** — generate the tasks with one command (from the project folder):

```bash
stepik-grader --init-vscode
```

Creates `.vscode/tasks.json` with the tasks:
- **Stepik: check current file** (default — `Ctrl+Shift+B`) → `--mode 1 --file ${file}`
- **Stepik: check folder** → `--mode 2 --dir ${fileDirname}`
- **Stepik: benchmark folder** → `--mode 3 --dir ${fileDirname}`
- **Stepik: web interface** → `--serve`

Run: `Ctrl+Shift+B` (check the open file) or `Terminal → Run Task →
"Stepik: …"`. An existing `tasks.json` is not overwritten — the command warns.

> The tasks run the grader through the **interpreter selected in VS Code**
> (`${command:python.interpreterPath} -m stepik_grader.grader …`), not through
> the console command `stepik-grader`. So the venv does **not need to be
> activated manually** — just pick your environment's interpreter once
> (`Ctrl+Shift+P → Python: Select Interpreter`) where the package is installed.
> The standard **Python** extension for VS Code is required. If the task
> doesn't start — check that the right interpreter is selected and that
> `pip install stepik-python-grader` (or `pip install -e .`) was run in it.

**PyCharm** — via an *External Tool* (configured manually, once):

1. `Settings → Tools → External Tools → +`
2. Fill in:
   - **Program:** `$PyInterpreterDirectory$/python`
   - **Arguments:** `-m stepik_grader.grader --mode 1 --file $FilePath$`
   - **Working directory:** `$FileDir$`
3. Run: right-click a file → `External Tools → …` (or assign a hotkey in
   `Keymap`).

> **Program:** `$PyInterpreterDirectory$/python` is the project interpreter
> (venv) selected in `Settings → Project → Python Interpreter`. This way the
> venv does **not** need manual activation, and the grader comes from the same
> environment where it is installed (`pip install stepik-python-grader` /
> `pip install -e .`). A direct `stepik-grader` call works only if the venv is
> activated on PATH — which is why the explicit interpreter path is used here,
> same as in the VS Code tasks.

---

## Additional flags

### Language, verbose/quiet, output

```bash
stepik-grader --mode 1 --file task.py --lang en        # menu/messages in English (default — ru)
stepik-grader --lang en --help                         # the help itself is also in English
stepik-grader --mode 1 --file task.py --quiet           # no detailed diff (mode 1 is verbose by default)
stepik-grader --mode 2 --dir . --verbose                # detailed diff per case (mode 2 is quiet by default)
stepik-grader --mode 1 --file task.py --output json     # machine-readable JSON instead of a table
stepik-grader --mode 2 --dir . --output json > results.json
```

`--verbose`/`--quiet` are mutually exclusive; they only control modes 1/2
(modes 3/4 always print the final benchmark table, `--verbose` makes no sense
there). `--output json` prints exactly one JSON line — the structure mirrors
the dictionaries already returned by `run_tests()`/`run_benchmark()`/
`run_microbench_mode()` (`file`/`results`/`groups` keys depending on the mode),
without a separate documented schema.

### `--profile` — a saved set of launch parameters

```bash
stepik-grader --serve --profile "sandboxed"              # isolation, port, language, folder — by one name
stepik-grader --serve --profile "sandboxed" --port 9000  # same profile, different port
```

A profile is the set chosen in the launcher window and saved under a name in
`.grader_settings.json` (isolation, history recording, language, port, working
folder). The same name works both in the window and in the terminal — no
separate entity needed for the command line.

The usual precedence order applies: **explicit flag → profile → saved settings
→ `pyproject.toml` → default**. So "run as usual, but on another port" is a
flag, no profile editing needed.

An unknown name is an error listing the available profiles, not a silent
fallback to defaults: a quiet substitution would give a non-isolated server
where one was expected. Without `--serve` the flag is rejected too — a profile
describes a web-interface launch.

### `--config`

```bash
stepik-grader --mode 2 --dir . --config /path/to/course.toml   # explicit config file
```

Takes `[tool.stepik-grader]` from the given file instead of auto-discovery.
Higher priority than the `STEPIK_GRADER_CONFIG` variable and the
`pyproject.toml` search up the tree; a non-existent path is a startup error,
not a silent fallback to auto-discovery. Useful where CI defines the run
parameters and the working folder knows nothing about them.

Without the flag, `pyproject.toml` is searched from the working folder upwards,
but never beyond the project boundary (`.git`, `.hg`, `.grader_settings.json`
or the home directory) and only among files with a `[tool.stepik-grader]`
section — a neighbouring project's config never affects the verdict. More —
[configuration.md](configuration.md).

### `--output csv` / `--output markdown`

```bash
stepik-grader --mode 2 --dir . --output csv > results.csv
stepik-grader --mode 3 --dir . --output markdown > BENCHMARK.md
```

Same data as `--output json`, but as a flat table (one row per file/test case)
in CSV or a Markdown table. Written to stdout, like `json` — to save to a file
use plain shell redirection; there is no dedicated "save to file" flag.

### `--watch`

```bash
pip install "stepik-python-grader[watch]"   # optional dependency: watchfiles

stepik-grader --mode 1 --file task.py --watch
stepik-grader --mode 2 --dir . --watch
```

Re-runs mode 1/2 on any change inside the watched file/folder (clears the
screen before each re-run). Works only with `--mode 1/2` — not applicable to
3/4 (expensive benchmark). Without `watchfiles` installed it prints an
installation hint instead of crashing.

For `--mode 2` the re-run is **incremental**: under `--watch` the results cache
is enabled automatically, so on an event only the changed file is actually
re-run, and the rows of the other solutions come from the cache — the feedback
loop doesn't degrade on a folder with a dozen tasks. At the end a summary
"N of M solutions from cache" is printed. Can be disabled with `--no-cache`
(then the whole folder is re-run every time, as before).

### `--import-reference` / `--import-top`

```bash
stepik-grader --import-reference ./task_123               # pinned + top-5 by likes
stepik-grader --import-reference ./task_123 --import-top 3
```

Imports the pinned Stepik solution (and the top-liked ones, `--import-top N`,
default 5; zero-like solutions are not taken) from the step's solutions branch
into the task folder as `task{N}_{100+}.py` — a ready reference-competitor for
comparison in modes 2–4, then exits. Reads `meta.json` from `TASK_DIR` (a
downloaded task and an OAuth token are required, see
[installation.en.md](installation.en.md)). Implementation —
`core/stepik_reference.py`; in the web UI — the "Find reference solution"
button.

### `--export-progress md|html|json`

```bash
stepik-grader --export-progress md      # → grader-progress.md
stepik-grader --export-progress html    # → grader-progress.html
stepik-grader --export-progress json    # → grader-progress.json (machine format)
```

Exports progress aggregates from the local history (`.grader_history.db`): the
series of credits and earned achievements, attempts and time to first AC per
task, verdict and failure-kind counters — **without solution sources** — into a
self-contained `grader-progress.md`/`.html` file, then exits. The HTML reads
well on a phone and follows the system theme: the file is meant to be shared in
a messenger, not just opened on your laptop. Requires history recording
(`--history`); empty history — a friendly message, not an error.

`json` — the same report as a machine format with a stable schema
(`schema: "stepik-grader/progress/1"`, keys
`tasks[].task_key/attempts/solved`, `verdicts`, `failure_kinds`, `streak`,
`badges`). It exists for aggregating progress across a group: several such
files can be combined by a script, whereas from `md`/`html` the same numbers
would have to be scraped out of the layout. On empty history an object with
`reason: "no_history_data"` is returned — still JSON, so a script never has to
parse prose.

### `--cache` / `--clear-cache`

```bash
stepik-grader --mode 1 --file task.py --cache     # first run — computes and caches
stepik-grader --mode 1 --file task.py --cache     # unchanged — served from cache (0 re-runs)
stepik-grader --mode 2 --dir . --cache            # for a folder: "N of M from cache"
stepik-grader --clear-cache                       # delete the cache and exit
```

Opt-in result cache for `--mode 1/2`. The solution is skipped and the previous
verdict shown as long as three things are unchanged: the solution file contents
(`sha256`), its test-directory files (`sha256`) and the **run conditions** —
the executor (normal or `--sandbox` with a specific backend), timeout, output
and memory limits, encoding, Python and package versions. Any change
invalidates the entry and the test is re-run.

The third key matters more than it seems: the verdict depends on the conditions
no less than on the code. Without it, the cache handed a normal run the result
obtained in the sandbox (and vice versa — under `--sandbox` it returned a
non-sandbox verdict, i.e. isolation was effectively not active with `--cache`
on), and a `timeout_seconds` change recomputed nothing. The run conditions are
printed in the report header — you can see exactly what was checked.

**A run that failed on time (`TLE`) is not cached.** Such a verdict depends not
on the code but on what the machine was doing at that moment: on a busy laptop
a correct solution got `TLE`, the verdict stuck in the cache — and from then on
the solution "failed" without even being run, until the code changed or the
cache was cleared. Other verdicts are cached as before.

A cache hit is **not written** to `.grader_stats.jsonl`: there was no run, and
a row with someone else's `total_time` from a previous launch looked like one
more measurement and spoiled the timing statistics.

The cache lives in a single file `.grader_cache/results.json` in the current
folder (added to `.gitignore`). A write failure (read-only directory, no space)
doesn't crash the check — the verdict is already computed, and the cache is
regenerable. Enable by default via `pyproject.toml`:

```toml
[tool.stepik-grader]
use_cache = true
```

With the cache on by default, an individual run can be forced with
`--no-cache`. Modes 3/4 (benchmark) don't use the cache — their point is fresh
timing measurements.

### `--sandbox`

```bash
stepik-grader --mode 1 --file task.py --sandbox   # execute in an OS-isolated sandbox
stepik-grader --serve --sandbox                   # same for web
```

Opt-in OS-level execution isolation for `--mode 1/2/3/4` and for `--serve`
instead of a plain subprocess: bubblewrap on Linux, `sandbox-exec` on macOS,
Job Objects on Windows. In the web UI `SandboxRunner` is set as the active
runner before the server starts — grade/playground/microbench are isolated
together; the step-by-step trace is unavailable under `--sandbox` (the tracer
needs the project package inside the executing process). The backend is chosen
automatically for the current OS; if unavailable (no `bwrap`/`sandbox-exec`/
Job Object API) — the command fails immediately, without a silent fallback to a
normal run. The isolation guarantees **differ by OS** (network/FS/memory/CPU/
anti-fork-bomb) — the full asymmetry table and the named gaps (no network
isolation on Windows, etc.) — [SECURITY.md § `--sandbox`](../../SECURITY.md#--sandbox--sandboxrunner-mvp).
Quotas are configured via `[tool.stepik-grader]` —
`sandbox_max_cpu_seconds`/`sandbox_max_processes`/`sandbox_max_output_bytes`,
see [configuration.md](configuration.md).

### `--stats` / `--stats-summary`

```bash
stepik-grader --mode 1 --file task.py --stats    # write local statistics of this run
stepik-grader --stats-summary                    # show a summary and exit
```

Opt-in local run statistics (mode/verdicts/OS/total time) in
`.grader_stats.jsonl` in the current folder — local only, no network. Enable by
default via `pyproject.toml`:

```toml
[tool.stepik-grader]
record_stats = true
```

With default collection on, an individual run can be forced off with
`--no-stats`. `--stats-summary` prints an aggregated summary (rich table with a
plain-text fallback) and exits without grading.

### `--insights` / `--lint`

Learning insights in the terminal on top of the run history (`--history`):

```bash
stepik-grader --insights                          # summary of "Practice" cards and exit
stepik-grader --insights --output json            # same summary as machine format
stepik-grader --mode 1 --file task.py --lint      # + "Style" block (ruff) after the check
```

`--insights` prints the common-mistake cards from `.grader_history.db` with
their decay status (active/fading/watch) and links to the rules/glossary, then
exits; on empty history — a friendly hint, exit 0. The same is available as
item "5. Practice" in the interactive menu.

With `--output json` the same summary arrives as an object
(`schema: "stepik-grader/insights/1"`, `cards` and `tasks` lists) — a script
can fetch it without parsing terminal tables. Empty history stays JSON here
too: the lists are simply empty.

`--lint` (modes 1/2) shows the "Style" block after the results — PEP 8
violations from ruff (`⚐ E501 ×3 [lines …]` + the one-line rule from `rules/`).
Requires the extra `pip install stepik-python-grader[lint]`; without it — an
install hint. **Does not affect the verdict** — an informational channel. The
format canon — [rules-insights.md](../dev/rules-insights.md).

### `--diagnostic`

```bash
stepik-grader --diagnostic --mode 1 --file task.py   # log of this run
STEPIK_GRADER_LOG=debug stepik-grader --mode 2 --dir .   # same for the whole session
```

Enables a diagnostic log of network, OAuth and downloads into
`stepik_diagnostics/grader.log`. **Off** by default — without the flag no file
is created at all, a "clean" run leaves no traces. Secrets in the log are
redacted (tokens, keys, `Authorization`), but it's still worth reviewing the
log before pasting it into an issue.

This is the first thing to enable when a task doesn't download or
authorization fails: the log shows request URLs, response codes and parsing
steps. The `STEPIK_GRADER_LOG=debug` environment variable gives the same result
(levels: `off`/`warning`/`info`/`debug`) — more convenient in CI and long
debugging sessions. The mechanism — [logging.md](../dev/logging.md).

### `--ai-hints`

Opt-in AI explanation of failed cases in modes 1–4 (WA/RE in checks; in
benchmark modes 3/4 — solutions with a runtime error) — *why* the solution
failed, not just *what*. BYOK (bring your own key), OpenAI-compatible endpoint
on `requests`, **no new dependencies**. Off by default; grading **never**
fails or blocks because of AI (no network/timeout/invalid key → silent skip
with a hint).

Setup — the `[tool.stepik-grader]` section in `pyproject.toml`:

```toml
[tool.stepik-grader]
ai_base_url = "http://localhost:11434/v1"   # OpenAI-compatible endpoint
ai_model = "llama3.1"                        # model name at the provider
ai_api_key_env = "STEPIK_GRADER_AI_KEY"      # NAME of the env variable with the key
```

The key is read from the env variable (its name — in `ai_api_key_env`) **at
call time**; the key value is never written to project files and never logged
(redacted via `core/diag_log`). Run:

```bash
export STEPIK_GRADER_AI_KEY=...                    # for a cloud provider
stepik-grader --mode 1 --file task.py --ai-hints
```

**Consent is required.** A hint sends the solution code and its input/output to
an external provider, so before the first send a one-time explicit consent is
asked. It is stored in `.grader_settings.json` (`ai_hint_consent`) — the same
key the web interface uses, so consent given once applies to both paths. A
refusal is not recorded: you can change your mind. In a non-interactive session
(CI, pipe) consent is not asked — hints are skipped, **nothing goes to the
network**. For full privacy use a local provider (ollama): the data never
leaves the machine.

Two equal providers — one code, different `ai_base_url` (ADR-0003):

- **Local (ollama and compatible) — recommended for privacy.**
  `ai_base_url = "http://localhost:11434/v1"`, no key needed. The solution code
  **does not leave the machine**. Requires an installed runner + model.
- **Cloud OpenAI-compatible — quick start.** The provider's `ai_base_url` +
  a free API key in an env variable. Lower barrier (a key in minutes, no local
  model). ⚠️ **The solution code and the error text go to a third-party
  endpoint** — a conscious choice; for sensitive data prefer the local
  provider.

The prompt is grounded on `failure_kind` + diff + the `error_glossary` card;
the answer is length-limited and **marked as AI-generated** (never presented as
the grader's verdict). Strategy and rejected alternatives —
[ADR-0003](../dev/adr/0003-ai-integration.md).

### pytest plugin: `pytest --grader-mode`

If you are used to pytest, the grader can run as a regular test suite. The
plugin ships with `stepik-python-grader` (`pytest` must be installed) and is
inert by default — enabled with the `--grader-mode` flag:

```bash
pip install pytest                       # if not installed yet
pytest --grader-mode StepikTasks/        # collect solutions as pytest tests
```

pytest walks the given folder, finds solution files (`task*.py`) and creates a
separate test for each test case from the neighbouring `tests/`. The output is
standard pytest: `PASSED` for a correct solution, `FAILED` with an
"Expected/Actual" diff for WA, the exception text for a runtime error.

```
StepikTasks/module1/task_1.py::test_1 PASSED
StepikTasks/module1/task_1.py::test_2 FAILED
```

Can be enabled without the flag via `pytest.ini` / `pyproject.toml`:

```toml
[tool.pytest.ini_options]
grader_mode = true
```

**A solution without a neighbouring `tests/` does not vanish silently.** A
missing directory is a normal consequence of a merge or incomplete checkout,
and previously the plugin simply skipped such a solution: pytest printed
"collected N items" where N was silently smaller than the number of solutions,
and the run stayed green. Now such solutions are named: at the end of the
output a line appears — `solutions without tests/: N` with the file list (plus
the usual pytest warning).

For CI that is not enough — there "couldn't check" must go red, so there is a
strict mode:

```bash
pytest --grader-mode --grader-strict StepikTasks/   # solution without tests/ → FAILED
```

```toml
[tool.pytest.ini_options]
grader_mode = true
grader_strict = true
```

The lenient mode stays the default on purpose: `--grader-mode` is also enabled
on top of other people's suites, and sudden redness there would be a surprise.
Neighbouring solutions that have their `tests/` in place are checked as usual
in both modes.

Works together with `pytest-xdist` (`-n auto`) and `pytest-cov`. A separate
`pytest-stepik-grader` package on PyPI is not split out yet — the plugin rides
inside the main package.

---

## Downloading a task

> Requires [OAuth setup](installation.en.md#working-with-the-stepik-api-oauth).

```bash
python -m stepik_grader.downloader
```

On the first run:
- you'll be asked to pick a root folder (default `StepikTasks`) and the path to
  `secrets.json`,
- a browser opens to confirm access,
- after successful authorization the tokens are saved to `secrets.json` via
  `storage.save_secrets()`.

Enter the step URL, for example:

```text
Step URL: https://stepik.org/lesson/569749/step/4?unit=564263
```

The script creates the structure:

```text
StepikTasks/
└── course-name/
    └── section-name/
        └── lesson-name/
            └── 04/                     # number only, if the step has no title
            └── 04-step-title/          # number + slug, if the title exists
                ├── task4_1.py          # main solution (from the task template or empty)
                ├── task4_2.py          # stub for an alternative solution (always created)
                ├── solution.py         # the last submission from the site (if available)
                ├── meta.json           # step metadata (id, lesson, course, ...)
                ├── task.md             # the task text in Markdown/HTML
                ├── files.txt           # the statement attachment, if any (each task names its own)
                ├── submissions/        # history of your submissions (if any)
                │   ├── 2024-03-01T12-00-05_wrong_1234567.py
                │   ├── 2024-03-01T12-05-11_correct_1234890.py
                │   └── meta.json       # verdict, hint and time of each
                └── tests/
                    ├── 1               # input data of test №1
                    ├── 1.clue          # expected output of test №1
                    ├── 1.type          # test type (function-style only)
                    ├── 2
                    ├── 2.clue
                    └── ...
```

**Working-file naming scheme:**

| File | Contents | Created |
|---|---|---|
| `task{N}_1.py` | the template from the task (or empty, if there is none) | always |
| `task{N}_2.py` | stub for alternative solution 1 | always (only if the file doesn't exist yet) |
| `task{N}_3.py` and on | alternative solutions 2, 3, … | manually |
| `solution.py` | the last submission from the site | if a submission is available |
| `submissions/` | all your submissions for the step: one file per attempt + `meta.json` with verdict, hint and time | if there were submissions |
| statement attachments (`files.txt`, `data.csv`, …) | files the solution itself opens: the statement refers to them with "you are given a file" | if the statement links `media/attachments` |

> Re-running `downloader.py` for the same step **does not overwrite**
> `task{N}_2.py` and above — your work is kept. Statement attachments are not
> overwritten either: they are edited by hand (trimmed, extended with your own
> cases), and a re-download has no right to erase that work.

**About attachments.** A task like "group the files from `files.txt`" cannot be
reproduced without the `files.txt` itself: the solution crashes with
`FileNotFoundError`, which looks like the student's mistake even though the
platform accepted the same solution. So the downloader fetches attachments next
to `task.md` — where the solution reads them from. What arrived and what
didn't is visible in `meta.json` (the `attachments` field): an unavailable
attachment is marked explicitly rather than staying silent until the first run.

**How `submissions/` differs from `solution.py`.** `solution.py` is exactly the
last submission, and re-downloading the step overwrites it. The `submissions/`
directory accumulates **all** history and never erases what is saved: past
attempts, including wrong ones, stay together with the platform's verdict.
That is exactly the data showing where you went wrong — and it used to be lost
while history wasn't saved.

History files are deliberately not named with the solution mask
(`task{N}_{M}.py`), so modes 2–4 don't take forty old attempts for competing
solutions: only the task's working files are compared.

### How test cases are found

`downloader.py` tries sources by priority — the first successful one wins:

| Priority | Source | Behaviour |
|---|---|---|
| 1 | ZIP link in the task HTML | Downloaded automatically, unpacked into `tests/` |
| 2 | HTML table in the task text | Parsed automatically into `tests/N` + `tests/N.clue` |
| 3 | GitHub link in the HTML | The address is printed to the console — download manually |
| 4 | Nothing found | A ⚠️ warning, the other files are already saved |

The OAuth flow is fully implemented in `stepik_client.py`
(`create_user_session`, `authorize_via_browser`, `refresh_access_token`);
`downloader.py` only orchestrates the calls.

---

## Operating modes

### Mode 1 — Check one file

Quickly run one solution:

```
Enter path to solution file: module1/task1/task1_1.py

File                       Passed   Total time   Avg time   Memory, MB   Status   Fail test
task1_1.py                    5/5       0.1234     0.0247        25.30       OK           -
```

The result is printed as a rich table (green `OK`, red `FAIL`); on a test
failure, verbose mode prints the diff of expected vs actual output.

### Mode 2 — Compare all solutions

Walks the whole folder, finds all `task*.py` and verifies each. Results — a
table grouped by task.

```
📂 module1/task1
--------------------------------------------------------------------
File                       Passed   Total time   Avg time   Memory, MB  Status  Fail test
--------------------------------------------------------------------
module1/task1/task1_1.py      5/5       0.1234     0.0247        25.30      OK          -
module1/task1/task1_2.py      5/5       0.1456     0.0291        24.80      OK          -
```

> Mode 2 is a **correctness** check, not a full benchmark.

### Mode 3 — Subprocess benchmark

Runs N repetitions for each solution that **passed all tests** in a separate
process. Shows min / median / mean / max / std-dev and compares solutions
relative to the fastest one.

The words "passed all tests" are enforced by a check: before measuring, each
solution is run once through the test cases (pre-flight). A failing one gets
the `SKIPPED` verdict with a reason ("failed check: 2 of 3 (first failure:
WA)") and doesn't enter the comparison — previously a solution with a wrong
answer honestly got a median and a place in the ranking, because timing
measures time and doesn't compare output. The price — one extra test run per
solution; it is accounted for in the progress bar. The same rule applies in
mode 4 and in the web UI.

**Load profiles (repeats):**

| # | Mode | Repetitions |
|---|-------|-------------|
| 1 | low | 5 |
| 2 | medium | 15 |
| 3 | high | 50 |
| 4 | custom | 5–100 |

**What the benchmark shows:**

| Field | Meaning |
|---|---|
| `Runs` | total launches |
| `Min` | best measurement |
| `Median` | median time — the main reference |
| `Mean` | average time |
| `Max` | worst measurement |
| `Std dev` | spread of measurements (low → stable) |
| `Memory` | peak memory |
| `Relative` | time relative to the best solution |
| `Verdict` | `SIMILAR`, `SLOWER`, `MUCH SLOWER` |

```
🚀 Benchmark: module1/task1
---------------------------------------------------------------------
File                       Runs     Min  Median    Mean     Max  Std dev  Memory  Relative   Verdict
---------------------------------------------------------------------
module1/task1/task1_1.py     25  0.0234  0.0249  0.0250  0.0279   0.0011   25.30    100.0%   SIMILAR
module1/task1/task1_2.py     25  0.0257  0.0271  0.0273  0.0301   0.0013   24.80    108.9%    SLOWER
```

### Mode 4 — Micro-bench (timeit)

Measures time with `timeit.timeit` inside a single process — no interpreter
startup overhead. Supports script-style (with `input()`) and function-only
solutions.

> **The `Py-heap` column (not `Memory`).** Unlike mode 3 (RSS via psutil),
> mode 4 measures the peak **Python heap via `tracemalloc`** for stdin blocks,
> and RSS for function blocks. Two different methods in one column, which is
> why it's named `Py-heap`, not `Memory`. `tracemalloc` doesn't see C-extension
> allocations (numpy, etc.) — acceptable for pure Python.

**Call counts (calls per run):**

| # | Mode | Calls |
|---|-------|-------|
| 1 | fast | 500 |
| 2 | normal | 1 000 |
| 3 | thorough | 5 000 |
| 4 | deep | 50 000 |
| 5 | hard | 100 000 |
| 6 | custom | 100–500 000 |

> The `hard` mode is only for short deterministic functions.

```
⚡ Micro-bench (timeit): module1/task1
---------------------------------------------------------------------------
File                       Repeats  Min, us  Median, us  Mean, us  Max, us  Std dev, us  Relative     Verdict
---------------------------------------------------------------------------
module1/task1/task1_1.py      1000    12.34       13.01     13.12    15.67         0.82    100.0%      SIMILAR
module1/task1/task1_2.py      1000    14.21       15.34     15.45    18.90         1.12    117.9%  MUCH SLOWER
```

### Test-case verdicts

Verdicts per test case (AC / WA / TLE / RE) — in
[configuration.md § Test-case verdicts](configuration.md#вердикты-тест-кейсов).

---

## Formats, configuration, security

Reference material lives in the canonical document
[configuration.md](configuration.md), so this file stays about user scenarios:

- **Test-case formats** (the `tests/` folder, `*.type`, three auto-detected
  formats) — [configuration.md § Test-case formats](configuration.md#формат-тест-кейсов).
- **Configuration** (`[tool.stepik-grader]` in `pyproject.toml`, timeouts,
  memory, `microbench_max_cases`, `stepik_config.json`) —
  [configuration.md § `[tool.stepik-grader]`](configuration.md#toolstepik-grader-в-pyprojecttoml).
- **Limits and the security model** of local runs/subprocess —
  [configuration.md § Limits and security](configuration.md#ограничения-и-безопасность).
- **Configuration error diagnostics** —
  [configuration.md § Configuration error diagnostics](configuration.md#диагностика-конфигурационных-ошибок).
