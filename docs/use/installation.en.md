# Installing and configuring Stepik

> Project overview — [README](../../README.md); documentation map —
> [../README.md](../README.md); working with the grader after installation —
> [grader-workflow.md](grader-workflow.md).

## Contents

- [Requirements](#requirements)
- [Method A — via pipx (recommended)](#method-a--via-pipx-recommended)
- [Method B — from source (for development)](#method-b--from-source-for-development)
- [Verifying the installation — web interface](#verifying-the-installation--web-interface)
- [Dependencies](#dependencies)
- [Working with the Stepik API (OAuth)](#working-with-the-stepik-api-oauth)
- [Diagnostics](#diagnostics)
- [Development environment diagnostics (pytest, Windows)](#development-environment-diagnostics-pytest-windows)

---

## Requirements

- **Python 3.12 or 3.13.** 3.14 is experimental (may break) — install it only
  deliberately. Check your version: `python --version`.
- **Git** — only needed for installing from source.

> **Short version for a beginner:** if you just want to use the tool, install
> it with **`pipx`** (Method A): it isolates everything for you and adds the
> command to PATH — no `venv`, no `activate`. Method B (from source) is only
> needed if you are going to modify the code.

---

## Method A — via pipx (recommended)

[pipx](https://pipx.pypa.io) installs a CLI tool into its own isolated
environment and puts the command on PATH for you — no `venv`, no `activate`.

```bash
python -m pip install --user pipx
python -m pipx ensurepath      # adds pipx to PATH once — RESTART the terminal afterwards
pipx install stepik-python-grader
```

Verify it is installed:

```bash
stepik-grader --version        # should print the current version
```

> The package is published on [PyPI](https://pypi.org/project/stepik-python-grader/).
> If you need an unreleased version straight from the repository —
> `pipx install git+https://github.com/ArtVsMark/Stepik-Python-Grader.git`.
> A plain `pip install stepik-python-grader` works too, but `pipx` is more
> convenient for a CLI tool (isolation + PATH).

---

## Method B — from source (for development)

**Step 1. Clone the repository:**

```bash
git clone https://github.com/ArtVsMark/Stepik-Python-Grader.git
cd Stepik-Python-Grader
```

**Step 2. Create a virtual environment:**

```bash
python -m venv .venv
```

**Step 3. Activate the environment:**

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

> ⚠️ **Windows: "running scripts is disabled on this system"
> (PSSecurityException)?** PowerShell blocks venv activation by default.
> Two ways out:
>
> 1. **Allow scripts for your user (once):**
>    ```powershell
>    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
>    ```
>    then run `.venv\Scripts\Activate.ps1` again.
> 2. **Or don't activate at all** — call the venv interpreter directly:
>    ```powershell
>    .venv\Scripts\python.exe -m pip install -e .
>    .venv\Scripts\python.exe -m stepik_grader
>    ```
>
> ❗ **Don't skip activation if you're installing with a plain
> `pip install -e .`** — otherwise the package will land in the *global*
> Python instead of the venv, and the `stepik-grader` command may "not be
> found" (its directory is not on PATH). Either way, a reliable way to run is
> `python -m stepik_grader` (always works, see
> [grader-workflow.md](grader-workflow.md)).
>
> ⚠️ **`stepik-grader --serve` / `--sandbox` / any command fails with
> `ModuleNotFoundError: No module named 'stepik_grader'`, even though the
> command itself is found (not "command not found")?** It means
> `stepik-grader` resolves to a *different* (global) Python, not the active
> `.venv` — usually because of a stale editable install made before the
> project moved to src-layout, or simply outside the venv. Check where the
> command comes from:
> ```powershell
> Get-Command stepik-grader   # Windows: the path should point to .venv\Scripts\
> ```
> ```bash
> which stepik-grader          # macOS/Linux: the path should point to .venv/bin/
> ```
> If the path is NOT inside this repository's `.venv` — activate the venv
> (Step 3 above) and run the command again; PATH inside an activated venv puts
> its `Scripts`/`bin` first, so the right `stepik-grader` is found before the
> stray one. To keep such a "stale" global install from confusing things in the
> future, remove it explicitly (replace `<version>`/`<hash>` with what
> `pip show -f stepik-python-grader` reports from the global Python):
> ```bash
> pip uninstall stepik-python-grader   # if it complains "No files were found to
>                                      # uninstall" — the metadata is broken, remove
>                                      # *.dist-info/, __editable__*.pth,
>                                      # __editable___*_finder.py from site-packages
>                                      # manually (the path is shown by
>                                      # `python -c "import site;
>                                      # print(site.getsitepackages())"`) and the
>                                      # matching stepik-grader(.exe) from the
>                                      # adjacent Scripts/bin directory.
> ```

**Step 4. Install dependencies:**

```bash
pip install -e .             # runtime: requests, psutil, rich
```

For development (tests, linter, typing):

```bash
pip install -e ".[dev]"      # + pytest, pytest-cov, ruff, mypy
```

**Step 5. Verify the installation:**

```bash
python -m stepik_grader --version   # e.g. 1.9.0
```

> The project uses src-layout (`src/stepik_grader/`) — modules run only as a
> package (`python -m stepik_grader`) or via the `stepik-grader` command (if
> its directory is on PATH). There is no direct `python grader.py` from the
> repository root.

---

## Verifying the installation — web interface

The most convincing way to make sure the installation works (for both Method A
and Method B) is to start the local web interface:

```bash
stepik-grader --serve          # or: python -m stepik_grader --serve
```

Open <http://127.0.0.1:8000> in a browser — the default port is 8000; another
one is set with `--port` (e.g. `stepik-grader --serve --port 9000`). On the
first visit you see a welcome start screen, then the shell with the sections
"Check solutions", "Task downloader", "Glossary", "Rules (PEP)", "Practice",
"Sandbox". If the interface renders — the installation works. Stop the server
with `Ctrl+C` in the terminal.

The server listens on `127.0.0.1` only and is not exposed to the outside; there
is no execution isolation without `--sandbox` — run only your own solutions.
Sections, flags and the threat model —
[grader-workflow.md](grader-workflow.md#веб-интерфейс---serve).

> **Running without a command line.** Besides `python -m stepik_grader` and
> `stepik-grader`, the GUI launcher `stepik-grader-gui` is available after
> installation (on Windows — a shortcut without a console window; or
> `python -m stepik_grader.launcher`): a window to pick the server mode, port
> and working folder. Details —
> [grader-workflow.md](grader-workflow.md#веб-интерфейс---serve).

---

## Dependencies

| Package | Purpose | Used in |
|---------|---------|---------|
| `requests>=2.34.2` | HTTP requests to the Stepik API, OAuth2, ZIP download | `core/stepik_client.py`, `downloader.py` |
| `psutil>=5.9` | Memory measurement and process monitoring | `core/grader_core.py`, `core/runner.py` |
| `rich>=13.0` | Colored tables, progress bar, WA diff in the terminal | `core/reporter.py` |

Dev dependencies (`pip install -e ".[dev]"`):

| Package | Purpose |
|---------|---------|
| `pytest>=8.2` | Testing |
| `pytest-cov>=5.0` | Test coverage (`--cov`) |
| `pytest-timeout>=2.3` | Timeout for hanging tests (`timeout` in `[tool.pytest]`) |
| `ruff>=0.15.19` | Linter and formatter |
| `mypy>=1.10` | Type checking |
| `hypothesis>=6.0` | Property-based tests (test-block parser, float normalization) |

Separate opt-in extras (installed explicitly, not part of `[dev]`): `[watch]` —
`watchfiles` for watch-mode runs; `[lint]` — `ruff` as the **runtime** engine
of the "Style" block; `[e2e]` — `playwright` for web-UI smoke tests
(see [CONTRIBUTING.md § E2E](../../CONTRIBUTING.md)). The full inventory of
runtime and vendored web-asset versions/licenses, plus `pip-audit` in CI —
[../dev/supply-chain.md](../dev/supply-chain.md).

---

## Working with the Stepik API (OAuth)

This setup is needed if you want to **download task data from Stepik
automatically** or **submit a solution to Stepik** right from the web interface
(the "Submit to Stepik" button in mode 1, see
[grader-workflow.md](grader-workflow.md#веб-интерфейс---serve)). Grading local
solutions (see [grader-workflow.md](grader-workflow.md)) works without OAuth.

> **Easiest — no manual setup.** In the interactive `downloader`, when
> `secrets.json` is missing a step-by-step wizard starts; in the web interface
> (`--serve`), the "Task downloader" section provides an authorization form
> right in the browser. Both options replace creating `secrets.json` by hand —
> the steps below are only needed if you prefer to configure everything
> manually.

### Step 0 — OAuth setup on Stepik

**1. Create an OAuth application on Stepik**

1. Go to <https://stepik.org/oauth2/applications/>
2. Click **+ New Application**
3. Fill in the fields:

| Field | Value |
|-------|-------|
| Name | anything, e.g. `my-grader` |
| Client type | `Confidential` |
| Authorization grant type | `Authorization code` |
| Redirect uris | `http://localhost:8080/callback` |

4. Click **Save** — Stepik shows the `Client ID` and `Client Secret`.

### Step 1 — Create `secrets.json`

**In a repository clone**, a template sits next to the docs:

```bash
cp secrets.json.example secrets.json
```

**With a pipx/pip install** there is no template — it is not included in the
wheel. Create the file yourself in the working folder (`secrets.json`) with
this content, filling in your values:

```json
{
  "client_id": "<Client ID from the Stepik application settings>",
  "client_secret": "<Client Secret from the Stepik application settings>",
  "redirect_uri": "http://localhost:8080/callback",
  "access_token": "",
  "refresh_token": "",
  "expires_at": 0
}
```

### What the `secrets.json` fields mean

| Field | Meaning |
|-------|---------|
| `client_id` | OAuth application ID on Stepik |
| `client_secret` | OAuth application secret |
| `redirect_uri` | return address after authorization |
| `access_token` | current access token, filled in automatically |
| `refresh_token` | refresh token, filled in automatically |
| `expires_at` | `access_token` expiry time (Unix timestamp), filled in automatically |

> `secrets.json` is a local file and must not be committed to Git. On the first
> run leave `access_token`, `refresh_token`, `expires_at` empty — the script
> fills them in via `storage.save_secrets()`.

Next — [downloading task data](grader-workflow.md#шаг-скачивания-задачи).

### Resilience to network failures

Requests to the Stepik API go through a `requests.Session` that automatically
retries transient failures — a momentary API overload should not crash the run;
just wait and reconnect.

- **Which statuses are retried.** `429` (Too Many Requests) and transient
  `5xx` — `500`/`502`/`503`/`504`. Other `4xx` (e.g. `404` — task not found)
  are **not** retried: they are not a temporary problem, so the request cannot
  "just work on the second attempt".
- **Backoff.** Exponential, base 1 second, doubling with each attempt
  (1s → 2s → 4s...). If the server sends a `Retry-After` header, it is used
  instead of the calculated delay. By default 3 retries (up to 4 attempts per
  request in total).
- **What to do on a persistent error.** If the request still fails after all
  retries — it is not a flake, it is a real problem (Stepik API down, expired
  token, network isolation). The script raises
  `requests.exceptions.RetryError` (transport level) or
  `requests.RequestException` (network level, e.g. timeout) — read the error
  text; for token/API-availability diagnostics see the
  [Diagnostics](#diagnostics) section below.
- **Implementation.** `core/stepik_client.make_session()` mounts a
  `requests.adapters.HTTPAdapter` with `urllib3.util.Retry` on `http://`/
  `https://` — the retry works at the transport level for any request through
  the session, not only where the internal `_get_with_retry()` is called
  explicitly (that remains an additional retry layer for network exceptions
  such as dropped connections). The `urllib3` dependency already comes with
  `requests`; no new packages were added.

---

## Diagnostics

If `downloader.py` doesn't find step data automatically:

```bash
python -m stepik_grader.diagnostic_stepik
```

The script saves into the `stepik_diagnostics/` folder:
- `lesson_debug.json`
- `step_debug.json`
- `diagnostic_result.json`

`diagnostic_stepik.py` also lets you:
- check Stepik API availability;
- verify the authorization token;
- get information about a course, lesson or task by ID.

**Log of the run itself** — the `--diagnostic` flag (or the
`STEPIK_GRADER_LOG=debug` environment variable): writes network, OAuth and
download steps into `stepik_diagnostics/grader.log` with secrets redacted.
Disabled by default, no file is created. More —
[grader-workflow.md § `--diagnostic`](grader-workflow.md#--diagnostic).

---

## Development environment diagnostics (pytest, Windows)

Three known problems, all reproducible on a clean `main` — they are caused by
the local environment state, not by the project code:

**`test_packaging.py::test_license_is_mit_in_metadata` fails
(`License-Expression` — `None`, expected `"MIT"`), or
`tests/test_pytest_plugin.py` fails with `unrecognized arguments:
--grader-mode`.** Both symptoms share one root cause: **stale editable-install
metadata** (`pip install -e ".[dev]"` was run before `pyproject.toml` changes
touching `license`/`entry-points`, and `.dist-info/` was not refreshed). The
package's `entry-points.txt` still contains the old (or missing) registration
of `pytest11 = stepik_grader.pytest_plugin` — hence both the
`test_pytest_plugin.py` failure (the plugin doesn't resolve in the child
`pytest.main()` that starts `pytester`) and the license-metadata failure. Fix
by reinstalling:

```bash
pip install -e ".[dev]" --force-reinstall --no-deps
```

Verify the cause/fix:

```bash
python -m pytest tests/test_packaging.py tests/test_pytest_plugin.py -q
```

**`PermissionError: [WinError 5] Access is denied` on
`%TEMP%\pytest-of-<user>`.** The directory was created by a previous pytest
run in a different context/permissions (e.g. another Windows user, elevated
rights) and is no longer writable by the current user. This is unrelated to the
project — the `tmp_path` fixture simply can't create its subdirectory.
Diagnostics:

```powershell
Get-Acl "$env:TEMP\pytest-of-$env:USERNAME"
```

If `Owner`/`Access` don't match the current user — either fix the permissions
(`icacls` from an administrator) or bypass the directory with an explicit
`--basetemp` outside of it:

```bash
pytest tests/ --basetemp=C:\temp\pytest-basetemp
```

Check both symptoms *in this order* — once the root cause (stale install) is
fixed by reinstalling, `test_pytest_plugin.py` usually starts passing with the
default `--basetemp` too, without the workaround.
