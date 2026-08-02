# Stepik Python Grader — English quick start

[![CI](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
[![Glossary](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/.github/badges/glossary.json&cacheSeconds=300)](docs/dev/glossary.md)

> A local grader for the «Поколение Python» Stepik courses — **and for any
> directory of test cases**. Русская версия: [README.md](README.md).
>
> 💬 **Found a bug or have an idea?** Item `9` in the grader menu and the 💬
> button in the web UI open a [prefilled issue form](https://github.com/ArtVsMark/Stepik-Python-Grader/issues/new/choose)
> — version, OS and Python are filled in for you. Questions rather than bugs go
> to [Discussions](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions).

Although the grader was built for Stepik, its core is platform-agnostic: point
it at any folder with solutions and a `tests/` directory and it just runs — no
account, no download, no OAuth. The web UI and the glossary are bilingual
(`?lang=en`).

![Web UI (--serve): grading a folder of solutions against test cases, verdict OK and a results table](docs/assets/hero-serve.gif)

![Case detail: input 4 produces output 5, verdict AC, with copy-input and copy-output actions](docs/assets/serve-detail.png)

---

## Install

Easiest via [pipx](https://pipx.pypa.io):

```bash
pipx install stepik-python-grader
```

Full install (from source, virtualenv, Windows notes, Stepik OAuth setup) —
[docs/use/installation.md](docs/use/installation.md).

---

## Quick start (no Stepik needed)

Grade a plain "add 1 to a number" solution against your own tests.

1. Create `task.py`:

   ```python
   n = int(input())
   print(n + 1)
   ```

2. Next to it, create a `tests/` folder with one case — `tests/1` (input) and
   `tests/1.clue` (expected output):

   ```
   4
   ```

   ```
   5
   ```

3. Run — three entry points, one grading core:

   ```bash
   stepik-grader --mode 1 --file task.py   # one-shot check in the terminal
   stepik-grader --serve                   # web UI at http://127.0.0.1:8000
   stepik-grader-gui                       # launcher window, no command line
   ```

You get a per-case verdict (AC / WA / TLE / RE) with a diff on mismatch. Modes,
CLI flags and task downloading are covered in
[docs/use/grader-workflow.md](docs/use/grader-workflow.md) (see the step-by-step
[first example](docs/use/grader-workflow.md#первый-пример-за-2-минуты)).

`stepik-grader-gui` is the lowest-barrier entry point: a small window where you
pick plain or isolated (`--sandbox`) startup, the port and the working folder,
then press Start — it runs `--serve` as a separate process and opens the
browser. On Windows it is a shortcut **without a console window**. The launcher
window itself is Russian-only; the web UI it opens is bilingual. On a Python
build without `tkinter` it prints the equivalent `--serve` command instead of
failing.

---

## Generic mode: your own tests, no Stepik

The grader auto-detects three test-case layouts — use any of them for your own
tasks:

- **Format 1 (Legacy):** `tests/N` (input) + `tests/N.clue` (expected output).
- **Format 2 (Named):** `tests/input_N.txt` + `tests/expected_N.txt`.
- **Format 3 (python-generation):** a single `input.txt` + `output.txt` with
  `# TEST_N:` markers.

Point `--mode 1` at a file or `--mode 2` at a folder of solutions; each solution
resolves its own `tests/`. Full reference —
[docs/use/configuration.md § Test-case formats](docs/use/configuration.md#формат-тест-кейсов).

---

## Bilingual web UI & glossary

```bash
stepik-grader --serve
```

Open `http://127.0.0.1:8000/?lang=en` — the web interface and the local Python
glossary render in English (`?lang=en`). The glossary ships ready cards
(functions, exceptions, constructs — live count in the Glossary badge above) with deep links from error cards.

Some parts of the grader live **only** here — there are no CLI flags for them:

- **Sandbox** — run arbitrary code against your own stdin, plus a
  **step-by-step execution trace** (variables per step, table or diagram view;
  the trace is unavailable under `--sandbox`).
- **Code editor with Save** — edit the solution in the browser and write it back
  to disk.
- **Submit to Stepik** — send the current solution and poll the verdict without
  leaving the browser (mode 1; needs Stepik OAuth and the task's `step_id`).
- **Browsable sections** — Glossary, Rules (PEP), Practice and Progress. The CLI
  only prints one-shot reports (`--insights`, `--lint`, `--export-progress`).

---

## Why this fork

| Feature | Original | This fork |
|---|---|---|
| Single-file correctness check | ✅ | ✅ |
| Solution comparison & benchmarks (modes 3/4, median-based, SIMILAR/SLOWER verdicts) | ❌ | ✅ |
| Stepik integration — OAuth2, auto-download of task & test cases, API diagnostics | ❌ | ✅ |
| Local web UI (`--serve`) + GUI launcher (`stepik-grader-gui`) + VS Code / PyCharm integration | ❌ | ✅ |
| Local Python glossary — cards + missing-term detector + deep links from error cards | ❌ | ✅ |
| PEP 8 rules + «Practice» section (frequent mistakes from run history) | ❌ | ✅ |
| Optional OS sandbox (`--sandbox`) with network/FS isolation | ❌ | ✅ |
| Bilingual RU/EN interface — CLI, web shell, glossary | ❌ | ✅ |
| Local run history (SQLite) + stats — offline | ❌ | ✅ |
| Engineering base — src-layout, `pyproject.toml`, CI (pytest + ruff + mypy) on 3 OSes, 2100+ tests | ❌ | ✅ |

Per-release evolution — [docs/use/versions.md](docs/use/versions.md).

---

## Transparency & trust

- ✅ **2100+ automated tests** (pytest), CI matrix over 3 OSes × Python 3.12/3.13 (+3.14 experimental) — live coverage badges (single-OS + cross-OS) in the header.
- 🧠 **Strict mypy** + `ruff` (lint + format) in pre-commit and CI on every PR.
- 🔐 **Private vulnerability reporting** + a documented threat model — [SECURITY.md](SECURITY.md).
- 📦 **PyPI publishing via OIDC trusted publishing** — no stored token.
- 📜 **MIT**, open changelog — [CHANGELOG.md](CHANGELOG.md).

---

## First contribution in 15 minutes

New here? Pick a
[`good first issue`](https://github.com/ArtVsMark/Stepik-Python-Grader/labels/good%20first%20issue),
fork, branch off `main`, run the local gates (`pytest` / `ruff` / `mypy`), add one
`CHANGELOG.md` line, and open a PR. Full onboarding —
[CONTRIBUTING.md](CONTRIBUTING.md). Questions & ideas —
[Discussions](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions).

---

## More

Full documentation lives under [docs/](docs/README.md). This page is an English
entry point only — canonical content (install, formats, architecture) lives in
`docs/*` (one topic per file) and is linked here rather than duplicated.

## License

[MIT](LICENSE) © Artem Markitanov (ArtVsMark).
