# Installing and Configuring Stepik

> Project overview:
> [README](../../README.md); documentation map: [../README.md](../README.md);
> working with the grader after installation:
> [grader-workflow.md](grader-workflow.md).

## Table of Contents

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

- **Python 3.12 or 3.13.** Version 3.14 is experimental (and may break);
  install it only if you know what you're doing. Check your version: `python --version`.
- **Git** — required only for installation from source.

> **Quick tip for beginners:** if you just want to use the tool, install it via
> **`pipx`** (Method A): it handles isolation and adds the command to your PATH
> automatically—no need for `venv` or `activate`. Method B (from source) is
> necessary only if you plan to modify the code.

---

## Method A — via pipx (recommended)

[pipx](https://pipx.pypa.io) installs the CLI tool in an isolated environment and
automatically adds the command to your PATH — no need for `venv` or `activate`.

```bash
python -m pip install --user pipx
python -m pipx ensurepath      # adds pipx to PATH once — RESTART your terminal after this
pipx install stepik-python-grader
```

Verify the installation:

```bash
stepik-grader --version        # should print the current version
```

> The package is published on [PyPI](https://pypi.org/project/stepik-python-grader/).
> If you need an unreleased version directly from the repository:
> `pipx install git+https://github.com/ArtVsMark/Stepik-Python-Grader.git`.
> A standard `pip install stepik-python-grader` works too, but `pipx` is more
> convenient for CLI tools (isolation + PATH handling).

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

>⚠️ **Windows: "script execution is disabled on this system"
>(PSSecurityException)?** PowerShell blocks venv activation by default.
> Two solutions:
>
> 1. **Allow scripts for your user (one-time):**
>    ```powershell
>    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
>    ```
>    then run `.venv\Scripts\Activate.ps1` again.
> 2. **Or don't activate it at all** — call the venv interpreter directly:
>    ```powershell
>    .venv\Scripts\python.exe -m pip install -e .
>    .venv\Scripts\python.exe -m stepik_grader
>    ```
>    
>❗ **Don't skip activation if you simply run `pip install -e .`** — otherwise
> the package will end up in the *global* Python environment rather than the venv, and the `stepik-grader` command
> might not be found (its directory won't be in your PATH). In any case, the reliable way to run it is
> `python -m stepik_grader` (this always works; see
> [grader-workflow.md](grader-workflow.md)).
>
> ⚠️ **`stepik-grader --serve`/`--sandbox`/any command fails with
> `ModuleNotFoundError: No module named 'stepik_grader'`, even though the command
> is found (i.e., not "command not found")?** This means `stepik-grader` is resolving to
> a *different* (global) Python installation rather than the active `.venv` —
> usually due to an old editable install performed before the project switched to
> the src-layout, or simply because it wasn't installed in the venv. Check where the command originates:
> ```powershell
> Get-Command stepik-grader   # Windows: path should point to .venv\Scripts\
> ```
> ```bash
> which stepik-grader          # macOS/Linux: path should point to .venv/bin/
> ```
> If the path is NOT inside this repository's `.venv`, activate the venv (Step 3 above)
> and run the command again; the PATH within an activated venv places its `Scripts`/
> `bin` directory first, ensuring the correct `stepik-grader` is found instead of the other one.
> To prevent such a "stale" global installation from causing confusion later, you should
> remove it explicitly (replace `<version>`/`<hash>` with the values shown by `pip show -f
> stepik-python-grader` when run from the global Python):
> ```bash
> pip uninstall stepik-python-grader   # if it complains "No files were found to
>                                      # uninstall" — the metadata is corrupt; manually
>                                      # delete *.dist-info/, __editable__*.pth, and
>                                      # __editable___*_finder.py from site-packages
>                                      # (the path is shown by `python -c "import site;
>                                      # print(site.getsitepackages())"`) and
>                                      # the corresponding stepik-grader(.exe) from
>                                      # the adjacent Scripts/bin directory.
> ```

 **Step 4. Install dependencies:**

```bash
pip install -e .             # runtime: requests, psutil, rich
```

For development (tests, linter, type checking):

```bash
pip install -e ".[dev]"      # + pytest, pytest-cov, ruff, mypy
```

**Step 5. Verify the installation:**

```bash
python -m stepik_grader --version   # e.g., 1.9.0
```

> The project uses the src-layout (`src/stepik_grader/`) — modules
> can only be run as a package (`python -m stepik_grader`) or via the
> `stepik-grader` command (provided its directory is in your PATH).
> There is no direct `python grader.py` command from the repository root.

---

## Verifying the installation — web interface

The most straightforward way to verify that the installation is working (applicable to both Method 
A and Method B) is to launch the local web interface:

```bash
stepik-grader --serve          # or: python -m stepik_grader --serve
```

Open <http://127.0.0.1:8000> in your browser — the default port is 8000, otherwise
is specified by the `--port` flag (e.g. `stepik-grader --serve --port 9000`). When
upon the first launch, the initial welcome screen is displayed, followed by —
a shell with sections "Check solutions", "Task downloader", "Glossary",
"Rules (PEP)", "Practice", "Sandbox". If the interface is rendered —
the setup is working. To stop the server, press `Ctrl+C` in the terminal.

The server listens only on `127.0.0.1` and is not exposed externally; without `--sandbox`
there is no execution isolation — run only your own solutions. Sections, flags, and threat model —
[grader-workflow.md](grader-workflow.md#веб-интерфейс---serve).

>**Launching without the command line.** In addition to `python -m stepik_grader`
> and `stepik-grader`, a GUI launcher—`stepik-grader-gui`—is available after installation
> (on Windows, this is a shortcut without a console window; alternatively, use `python -m stepik_grader.launcher`):
> a window for selecting the server mode, port, and working directory. See details in
> [grader-workflow.md](grader-workflow.md#веб-интерфейс---serve).

---

## Dependencies

| Package | Purpose | Used in |
|---------|---------|---------|
| `requests>=2.34.2` | HTTP requests to Stepik API, OAuth2, ZIP downloads | `core/stepik_client.py`, `downloader.py` |
| `psutil>=5.9` | Memory measurement and process monitoring | `core/grader_core.py`, `core/runner.py` |
| `rich>=13.0` | Colored tables, progress bars, WA diffs in the terminal | `core/reporter.py` |

Dev dependencies (`pip install -e ".[dev]"`):

| Package | Purpose |
|---------|---------|
| `pytest>=8.2` | Testing |
| `pytest-cov>=5.0` | Test coverage (`--cov`) |
| `pytest-timeout>=2.3` | Timeout for hanging tests (`timeout` in `[tool.pytest]`) |
| `ruff>=0.15.19` | Linter and formatter |
| `mypy>=1.10` | Type checking |
| `hypothesis>=6.0` | Property-based testing (test block parser, float normalization) |

Separate opt-in extras (set explicitly, not included in `[dev]`): `[watch]` —
`watchfiles` for watch mode; `[lint]` — `ruff` as the **runtime** engine
of the "Style" block; `[e2e]` — `playwright` for web UI smoke tests
(see [CONTRIBUTING.md § E2E](../../CONTRIBUTING.md)). A complete inventory
of runtime versions/licenses and vendor web assets, plus `pip-audit` in CI —
[../dev/supply-chain.md](../dev/supply-chain.md).

---

## Working with the Stepik API (OAuth)

Configuration is required if you want to **automatically download problem data** from Stepik
or **submit a solution to Stepik** directly from the web interface (the
"Submit to Stepik" button in mode 1; see
[grader-workflow.md](grader-workflow.md#веб-интерфейс---serve)). Grading
local solutions (see [grader-workflow.md](grader-workflow.md)) works without
OAuth.

> **The easiest way is without manual configuration.** In the interactive `downloader` when
>  in the absence of `secrets.json`, a step-by-step wizard is launched, and in
>  in the web interface (`--serve`), the "Task Loader" section presents an authorization form.
> directly in the browser. Both options replace manual creation.
> `secrets.json` — the steps below are necessary only if you prefer to set everything up
> manually

### Step 0 — Setting up OAuth on Stepik

**1. Create an OAuth application on Stepik**

1. Go to <https://stepik.org/oauth2/applications/>
2. Click **+ New Application**
3. Fill in the fields:

| Field | Value |
|---|---|
| Name | Any, e.g., `my-grader` |
| Client type | `Confidential` |
| Authorization grant type | `Authorization code` |
| Redirect URIs | `http://localhost:8080/callback` |

4. Click **Save** — Stepik will display the `Client ID` and `Client Secret`.

### Step 1 — Create `secrets.json`

**In the cloned repository**, there is a template file:

```bash
cp secrets.json.example secrets.json
```

**When installing via pipx/pip**, the template is not included—it is not part of the wheel. Create
the file yourself in the working directory (`secrets.json`) with the following content:

Fill in your own values:

```json
{
  "client_id": "<Client ID from Stepik app settings>",
  "client_secret": "<Client Secret from Stepik app settings>",
  "redirect_uri": "http://localhost:8080/callback",
  "access_token": "",
  "refresh_token": "",
  "expires_at": 0
}
```

### What the fields in `secrets.json` mean

| Field | Description |
|---|---|
| `client_id` | OAuth application ID on Stepik |
| `client_secret` | OAuth application secret |
| `redirect_uri` | Redirect URL after authorization |
| `access_token` | Current access token; populated automatically |
| `refresh_token` | Refresh token; populated automatically |
| `expires_at` | Access token expiration time (Unix timestamp); populated automatically |

> `secrets.json` is a local file and should not be committed to Git.
> Leave `access_token`, `refresh_token`, and `expires_at`
> blank on the first run — the script will populate them automatically via `storage.save_secrets()`.

> Next — [downloading the task data](grader-workflow.md#task-download-step).

### Resilience to network failures

Requests to the Stepik API are made using `requests.Session`, which automatically
retries after transient failures; there is no need to crash due to a momentary
API overload—simply waiting and reconnecting is sufficient.

- **Which statuses trigger retries.** `429` (Too Many Requests) and temporary `5xx` errors —
  `500`/`502`/`503`/`504`. Other `4xx` errors (e.g., `404` — task not found) **do not**
  trigger retries: these are not temporary issues, meaning the request cannot
  simply "succeed on the second attempt."
- **Backoff.** Exponential; base delay is 1 second, doubling with each
  attempt (1s → 2s → 4s...). If the server sends a `Retry-After` header,
  that value is used instead of the calculated delay. Default is 3 retries
  (up to 4 attempts per request in total).
- **What to do if the error persists.** If after all the repetitions the request still
  it still crashes - this is not a flap, but a real problem (Stepik API is unavailable,
  expired token, network isolation). The script will show an exception.
  `requests.exceptions.RetryError` (transport layer) or
  `requests.RequestException` (network layer, e.g., timeout) — see text
  errors; for token/API availability diagnostics, see the
  [Diagnostics](#diagnostics) section below.
- **Implementation.** `core/stepik_client.make_session()` mounts
  `requests.adapters.HTTPAdapter` from `urllib3.util.Retry` to `http://`/`https://` -
  the retry is valid at the transport level for any request through the session, not
  only where the internal `_get_with_retry()` is explicitly called (that one remains
  an additional retry layer for network exceptions such as connection drops
  connections). The `urllib3` dependency is already included with `requests`, no new packages have been
  added.
  
  ---

## Diagnostics

If `downloader.py` did not automatically find the step data:

```bash
python -m stepik_grader.diagnostic_stepik
```

The script will save the following to the `stepik_diagnostics/`:
- `lesson_debug.json`
- `step_debug.json`
- `diagnostic_result.json`

`diagnostic_stepik.py` also allows you to:
- check the availability of the Stepik API;
- verify the validity of the authorization token;
- retrieve information about a course, lesson, or task by ID.

  **Launch log** — the `--diagnostic` flag (or the `STEPIK_GRADER_LOG=debug`
variable): logs network, OAuth, and download steps to
`stepik_diagnostics/grader.log` with secrets redacted. Disabled by default;
no file is created. For more details, see
[grader-workflow.md § `--diagnostic`](grader-workflow.md#--diagnostic).

---

## Development environment diagnostics (pytest, Windows)

Three known issues, all reproducible on a clean
`main` branch—they are caused not by the project code, but by the state of the local environment:

**`test_packaging.py::test_license_is_mit_in_metadata` fails**
(`License-Expression` is `None`, expected `"MIT"`) or
`tests/test_pytest_plugin.py` fails with `unrecognized arguments:
--grader-mode`. Both symptoms stem from the same root cause: **stale
metadata from an editable installation** (`pip install -e ".[dev]"` was
performed before changes to `pyproject.toml` affecting `license`/`entry-points`,
and the `.dist-info/` directory was not updated). The package's
`entry-points.txt` still contains the old (or missing) registration
`pytest11 = stepik_grader.pytest_plugin` — hence the failure of
`test_pytest_plugin.py` (the plugin fails to resolve within the child
`pytest.main()` call invoked by `pytester`) and the license metadata
failure. This is fixed by reinstalling:

```bash
pip install -e ".[dev]" --force-reinstall --no-deps
```

To verify the cause/fix:

```bash
python -m pytest tests/test_packaging.py tests/test_pytest_plugin.py -q
```

**`PermissionError: [WinError 5] Access is denied` on
`%TEMP%\pytest-of-<user>`.** The directory was created by a previous pytest run
in a different context or with different privileges (e.g., a different Windows user,
elevated privileges) and remains unwritable for the current user. This is not
project-related—the `tmp_path` fixture simply cannot create the
subdirectory. Diagnosis:

```powershell
Get-Acl "$env:TEMP\pytest-of-$env:USERNAME"
```
If `Owner`/`Access` do not match the current user - either fix
permissions (`icacls` as administrator), or bypass it by explicitly specifying `--basetemp` outside
of this catalog:


```bash
pytest tests/ --basetemp=C:\temp\pytest-basetemp
```
Check both symptoms *in this order* - if the root cause (rotten
installation) resolved by reinstalling; `test_pytest_plugin.py` usually
it starts passing with the default `--basetemp` as well, without the workaround.
