# Stepik Python Grader

[![CI](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-1.5.0-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)
![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)

> **Status:** Stable — v1.5.0

> Локальный грейдер для курсов «Поколение Python» на Stepik.
> Скачивает данные задачи с сайта и позволяет не только проверить решение локально, но и **сравнить несколько решений более честно**: сначала по корректности, потом по benchmark-метрикам.

[Первоисточник грейдера](https://github.com/PavloOps/python_generation_grader)

Курсы:
- [Поколение Python: Курс для начинающих](https://stepik.org/course/58852)
- [Поколение Python: Курс для продвинутых](https://stepik.org/course/68343)
- [Поколение Python: Курс для профессионалов](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)
- [Поколение Python: Курс для самураев](https://stepik.org/course/134318)

---

## Содержание

- [Что умеет](#что-умеет)
- [Архитектура модулей](#архитектура-модулей)
- [Структура проекта](#структура-проекта)
- [Установка](#установка)
- [Быстрый старт](#быстрый-старт)
- [Работа с API Stepik](#работа-с-api-stepik)
- [Режимы работы](#режимы-работы)
- [Формат тест-кейсов](#формат-тест-кейсов)
- [Конфигурация](#конфигурация)
- [Зависимости](#зависимости)
- [Диагностика](#диагностика)
- [Ограничения и безопасность](#ограничения-и-безопасность)
- [Что изменилось по сравнению с оригиналом](#что-изменилось-по-сравнению-с-оригиналом)
- [Эволюция версий](#эволюция-версий)
- [Python версия](#python-версия)

---

## Что умеет

> Пакет живёт в `src/stepik_grader/` (Issue #35, src-layout). Пути ниже —
> относительно `src/stepik_grader/`.

| Модуль | Архитектурный слой | Что делает |
|---|---|---|
| `grader.py` | Application | Тонкий фасад обратной совместимости — реэкспортирует `core/grader_core.py`, `core/reporter.py`, `cli.py` |
| `cli.py` | Application / CLI | Интерактивное меню (режимы 0-4) и non-interactive argparse CLI, профили нагрузки; консольная команда `stepik-grader` |
| `config.py` | Application / Configuration | `GraderConfig` (frozen dataclass) + `CONFIG` singleton; переопределяется через `[tool.stepik-grader]` в `pyproject.toml` |
| `downloader.py` | Domain / Application | Управление конфигом и secrets, разбор URL шага, построение директорий задач (`slugify`, `build_task_directory`), сохранение файлов задачи, **автоизвлечение тест-кейсов** из HTML-таблицы и ZIP-архивов, оркестрация вызовов API |
| `diagnostic_stepik.py` | Application / Diagnostics | Диагностика: проверяет структуру ответа API и корректность токена авторизации |
| `core/grader_core.py` | Application | Исполнение тест-кейса в subprocess и агрегация статистики: 4 режима работы (`run_tests`, `run_benchmark`, `run_microbench_mode`) |
| `core/test_loader.py` | Application | Обнаружение файлов-решений, загрузка тест-кейсов (`load_test_cases`), `resolve_test_dir` (Issue #45 A-01) |
| `core/mode_detector.py` | Application | Детекция режима запуска stdin/function (`_detect_run_mode`, `is_function_only_solution`) (Issue #45 A-01) |
| `core/wrapper_builder.py` | Application | Генерация wrapper-скриптов для function-mode запуска (Issue #45 A-01) |
| `core/reporter.py` | Application / UI | rich-таблицы с цветами, вердикты AC/WA/TLE/RE, verbose-diff при WA, адаптивное форматирование времени (`fmt_time`) |
| `core/executor.py` | Infrastructure | Запускатель решений: `compile + exec` с таймаутом и изолированным namespace |
| `core/microbench_runner.py` | Infrastructure | Timeit-микробенчмарк через subprocess (`python -c`) + подавление stdout решения в `os.devnull`; peak memory через `tracemalloc` |
| `core/normalizers.py` | Infrastructure / Utilities | Нормализация вывода для сравнения: `normalize_floats` (округление float до 9 знаков), `sort_lines`, `normalize_whitespace` (experimental) |
| `core/storage.py` | Infrastructure / Utilities | Чтение и запись JSON-файлов (`load_json_file`, `save_json_file`, `save_secrets`); нет зависимостей от других модулей проекта |
| `core/stepik_client.py` | Infrastructure / HTTP | OAuth2-авторизация, `requests.Session`, GET-запросы к Stepik REST API, скачивание сабмишнов |
| `core/oauth_flow.py` | Infrastructure / Auth | OAuth2-фасад: единая точка входа для авторизации — `load_secrets`, `load_secrets_dict`, `token_is_valid`, `authorize_and_get_token`; устраняет дублирование между `downloader.py` и `diagnostic_stepik.py` |
| `core/parsers.py` | Infrastructure / Utilities | Парсинг тест-блоков (`# TEST_N:`) — единственный источник истины для `grader.py` и `downloader.py` |

Основные возможности:

- ✅ Запуск решений против наборов тест-кейсов (`tests/N` + `tests/N.clue`)
- 📋 **Автоматическое извлечение тест-кейсов** из HTML-таблицы в тексте задачи Stepik
- 📦 **Автоскачивание тестов из ZIP-архива** по ссылке в тексте задачи
- 🔗 Обнаружение ссылок на GitHub-тесты с подсказкой скачать вручную
- 📊 Сравнение нескольких решений одной задачи в таблице
- 🚀 Subprocess-бенчмарк с замером времени и памяти
- ⚡ Timeit-микробенчмарк через subprocess (`python -c`) с подавлением stdout решения в `os.devnull`
- 🎨 Цветной вывод через `rich` — зелёный OK/AC, красный WA/TLE/RE, жёлтый SLOWER
- 🔍 Diff при WA — сравнение ожидаемого и фактического вывода при провале теста
- ⚖️ Вердикты AC / WA / TLE / RE по каждому тест-кейсу
- 🔍 Диагностика окружения и авторизация через Stepik API

---

## Архитектура модулей

Граф зависимостей — DAG без циклов (все модули живут в `src/stepik_grader/`):

```
downloader.py          ──→  core/storage.py
downloader.py          ──→  core/stepik_client.py
downloader.py          ──→  core/parsers.py
core/stepik_client.py ──→  core/storage.py
grader.py              ──→  core/grader_core.py, core/reporter.py, cli.py  (тонкий фасад)
core/grader_core.py    ──→  core/executor.py, core/microbench_runner.py, core/normalizers.py
core/grader_core.py    ──→  core/test_loader.py, core/mode_detector.py, core/wrapper_builder.py
core/test_loader.py    ──→  core/mode_detector.py, core/parsers.py
core/mode_detector.py  ──→  core/storage.py
cli.py                 ──→  core/grader_core.py, core/reporter.py, core/microbench_runner.py
diagnostic_stepik.py ──→  core/stepik_client.py
diagnostic_stepik.py ──→  downloader.py       ← parse_stepik_step_url
downloader.py        ──→  core/oauth_flow.py
diagnostic_stepik.py ──→  core/oauth_flow.py
core/oauth_flow.py    ──→  core/stepik_client.py
core/oauth_flow.py    ──→  core/storage.py
```

downloader.py больше не импортирует grader.py: дублирующая копия
`_parse_testblock_file` в grader.py устранена (Issue #19) — оба модуля
читают `parse_testblock_file` из `core/parsers.py`.

Слои (снизу вверх):

```
┌───────────────────────────────────────────────────────────────┐
│  Domain / Application  (src/stepik_grader/ — точки входа)      │
│  downloader.py  │  grader.py (facade)  │  diagnostic_stepik   │
├───────────────────────────────────────────────────────────────┤
│  Application  (core/, грейдер разбит по SRP — Sprint 7, A-01) │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
│  core/test_loader.py │ core/mode_detector.py │ wrapper_builder │
│  cli.py (меню, публичная точка входа — stepik-grader)          │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure  (core/)                                       │
│  core/stepik_client.py  │  core/executor.py                    │
│  core/microbench_runner.py  │  core/oauth_flow.py              │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (core/, leaf, no deps)            │
│  core/storage.py  │  core/normalizers.py                       │
└───────────────────────────────────────────────────────────────┘
```

`core/storage.py` и `core/normalizers.py` — leaf-модули: не импортируют ничего из проекта, легко тестируются изолированно.

---

## Структура проекта

```
Stepik-Python-Grader/
├── src/
│   └── stepik_grader/            # src-layout (Issue #35 / CLAUDE.md Sprint 8.2)
│       ├── __init__.py
│       ├── grader.py              # Тонкий фасад обратной совместимости (Sprint 7)
│       ├── cli.py                 # Интерактивное меню (режимы 0-4) + stepik-grader entry point
│       ├── config.py              # GraderConfig, CONFIG — единая конфигурация
│       ├── downloader.py         # Domain: конфиг, slugify, построение папок, оркестрация API
│       ├── diagnostic_stepik.py  # Диагностика API и токена
│       └── core/                  # Internal Infrastructure/Utility модули (Issue #23, #26)
│           ├── __init__.py
│           ├── grader_core.py    # Исполнение тест-кейса в subprocess, агрегация статистики
│           ├── test_loader.py    # Обнаружение файлов-решений, загрузка тест-кейсов (Issue #45 A-01)
│           ├── mode_detector.py  # Детекция режима stdin/function (Issue #45 A-01)
│           ├── wrapper_builder.py # Генерация wrapper-скриптов для function-mode (Issue #45 A-01)
│           ├── reporter.py       # rich-таблицы, вывод, verbose-diff
│           ├── executor.py       # Запускатель решений: compile + exec с таймаутом
│           ├── microbench_runner.py  # Timeit-микробенчмарк через subprocess + os.devnull
│           ├── normalizers.py    # Нормализация вывода: округление float, sort/whitespace
│           ├── stepik_client.py  # Infrastructure: OAuth2, requests.Session, Stepik API
│           ├── oauth_flow.py     # Infrastructure/Auth: OAuth2-фасад поверх stepik_client
│           ├── parsers.py        # Парсинг тест-блоков (# TEST_N:)
│           └── storage.py        # Utilities: load/save JSON, save_secrets (нет project-зависимостей)
├── conftest.py                 # Добавляет src/ в sys.path для тестов
├── tests/                     # 622 теста (pytest)
│   ├── test_analyzer.py
│   ├── test_downloader.py
│   ├── test_executor.py
│   ├── test_grader_core.py
│   ├── test_integration_repos.py
│   ├── test_loader.py
│   ├── test_menu_modes.py
│   ├── test_microbench.py
│   ├── test_microbench_grader.py
│   ├── test_microbench_runner_module.py
│   ├── test_normalizers.py
│   ├── test_oauth_flow.py
│   ├── test_slugify.py
│   ├── test_stepik_client.py
│   ├── test_storage.py
│   └── test_testblock.py
├── .github/workflows/ci.yml   # CI: pytest + ruff на Python 3.12/3.13/3.14
├── .pre-commit-config.yaml    # Pre-commit хуки (ruff check + ruff format)
├── pyproject.toml             # Конфигурация проекта (ruff, mypy, pytest, зависимости, packages.find where=["src"])
├── secrets.json.example       # Шаблон файла с OAuth-токеном
├── stepik_config.json.example # Шаблон конфига Stepik
├── CHANGELOG.md               # История изменений
└── README.md
```

Локально обычно появляются:

```text
StepikTasks/
stepik_config.json
secrets.json
errors.txt
stepik_diagnostics/
```

Эти файлы и папки держи в `.gitignore`.

---

## Установка

> **Коротко для новичка:** если просто хочешь пользоваться — ставь через
> **`pipx`** (Способ A): он сам всё изолирует и добавит команду в PATH, никаких
> `venv` и `activate`. Способ B (из исходников) нужен только если будешь менять
> код.

### Требования

- **Python 3.12 или 3.13.** Версия 3.14 — экспериментальная (может ломаться),
  ставь её только осознанно. Проверить свою версию: `python --version`.
- **Git** — только для установки из исходников.

---

### Способ A — через pipx (рекомендуется, если просто пользоваться)

[pipx](https://pipx.pypa.io) ставит CLI-инструмент в отдельное окружение и сам
прописывает команду в PATH — не нужно ни `venv`, ни `activate`.

```bash
python -m pip install --user pipx
python -m pipx ensurepath      # один раз добавляет pipx в PATH — ПЕРЕЗАПУСТИ терминал после этого
pipx install stepik-python-grader
```

Проверь, что всё встало:

```bash
stepik-grader --version        # должно напечатать текущую версию
```

> Пакет публикуется на [PyPI](https://pypi.org/project/stepik-python-grader/)
> (issue #70). Если нужна ещё не выпущенная версия прямо из репозитория —
> `pipx install git+https://github.com/ArtVsMark/Stepik-Python-Grader.git`.
> Обычный `pip install stepik-python-grader` тоже работает, но `pipx` удобнее
> для CLI-инструмента (изоляция + PATH).

---

### Способ B — из исходников (для разработки / изменения кода)

**Шаг 1. Клонировать репозиторий:**

```bash
git clone https://github.com/ArtVsMark/Stepik-Python-Grader.git
cd Stepik-Python-Grader
```

**Шаг 2. Создать виртуальное окружение:**

```bash
python -m venv .venv
```

**Шаг 3. Активировать окружение:**

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

> ⚠️ **Windows: «выполнение сценариев отключено в этой системе»
> (PSSecurityException)?** PowerShell по умолчанию блокирует активацию venv.
> Два выхода:
>
> 1. **Разрешить скрипты для своего пользователя (один раз):**
>    ```powershell
>    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
>    ```
>    затем снова `.venv\Scripts\Activate.ps1`.
> 2. **Или не активировать вообще** — звать интерпретатор из venv напрямую:
>    ```powershell
>    .venv\Scripts\python.exe -m pip install -e .
>    .venv\Scripts\python.exe -m stepik_grader
>    ```
>
> ❗ **Не пропускай активацию, если ставишь просто `pip install -e .`** — иначе
> пакет уедет в *глобальный* Python, а не в venv, и команда `stepik-grader`
> может «не найтись» (её каталог не в PATH). В любом случае надёжный запуск —
> `python -m stepik_grader` (работает всегда, см. «Быстрый старт»).

**Шаг 4. Установить зависимости:**

```bash
pip install -e .             # рантайм: requests, psutil, rich
```

Для разработки (тесты, линтер, типизация):

```bash
pip install -e ".[dev]"      # + pytest, pytest-cov, ruff, mypy
```

**Шаг 5. Проверить установку:**

```bash
python -m stepik_grader --version   # напр. 1.5.0
```

> Проект использует src-layout (`src/stepik_grader/`, Issue #35) — модули
> запускаются только как пакет (`python -m stepik_grader`) или командой
> `stepik-grader` (если её каталог в PATH). Прямого `python grader.py` из корня
> репозитория нет.

---

## Быстрый старт

Запусти интерактивное меню:

```bash
python -m stepik_grader       # надёжный способ: работает даже если stepik-grader не в PATH
# или короче, если команда в PATH (Способ A / активированный venv):
stepik-grader
```

При запуске появится меню (русский язык по умолчанию, issue #51 D-01;
`--lang en` — английский):

```
==================================================
  Stepik Python Grader
==================================================
  1. Проверить одно решение
  2. Проверить все решения в папке
  3. Бенчмарк решений в папке
  4. Микро-бенчмарк (timeit) для папки
  0. Выход
==================================================
Выберите режим [0-4]:
```

### Первый пример за 2 минуты (шаг за шагом)

Проверим простое решение «прибавь 1 к числу» без Stepik — вручную.

**Шаг 1. Создай файл решения** `task.py` в любой папке:

```python
# task.py
n = int(input())
print(n + 1)
```

**Шаг 2. Рядом создай папку `tests/` с одной парой файлов** — вход и ожидаемый
вывод. Имя файла со входом — просто число, ожидаемый вывод — то же имя с
`.clue`:

```
task.py
tests/
  1          ← содержимое: 4      (это подаётся решению на stdin)
  1.clue     ← содержимое: 5      (это ожидаемый вывод)
```

> То есть: файл `tests/1` содержит строку `4`, файл `tests/1.clue` — строку `5`.
> Можно добавить ещё кейсы: `tests/2` + `tests/2.clue`, и так далее.

**Шаг 3. Запусти проверку одного решения (режим 1):**

```bash
python -m stepik_grader --mode 1 --file task.py
```

**Шаг 4. Прочитай результат.** Колонка `Passed` покажет `1/1`, статус — `OK`:

```
File        Passed   Total time   Avg time   Memory, MB   Status   Fail test
task.py       1/1       0.0123     0.0123         4.20       OK           -
```

Если вывод решения не совпадёт с `.clue`, статус будет `FAIL`, а с флагом
`--verbose` грейдер покажет построчный diff «ожидалось / получено».

> **Откуда брать тесты для реальных задач Stepik?** Их можно скачать
> автоматически — см. раздел [Работа с API Stepik](#работа-с-api-stepik) ниже.
> Формат папки `tests/` при этом тот же самый.

### Non-interactive запуск (CLI-флаги)

Для запуска из CI/скриптов без интерактивного ввода:

```bash
stepik-grader --version                                    # версия и выход
stepik-grader --mode 1 --file path/to/task.py               # режим 1
stepik-grader --mode 2 --dir path/to/folder                 # режим 2
stepik-grader --mode 3 --dir path/to/folder --repeats 15    # режим 3 (по умолчанию 15)
stepik-grader --mode 4 --dir path/to/folder --number 1000   # режим 4 (по умолчанию 1000)
```

Эквивалентно через `python -m`: `python -m stepik_grader --version` или
`python -m stepik_grader.grader --version` и т.д. (пакет содержит
`__main__.py`, поэтому короткая форма `python -m stepik_grader` работает —
issue #65).

Без `--mode` показывается обычное интерактивное меню.

### Веб-интерфейс (`--serve`)

Для тех, кому консоль — барьер (новички, работа из IDE), есть локальный
веб-интерфейс с двумя режимами:

- **Корректность** — таблица AC/WA, время, память; клик по имени файла
  раскрывает тест-кейсы и diff при WA.
- **Бенчмарк** — решения ранжируются по медиане (быстрейшее первым) с
  вердиктом SIMILAR/SLOWER/MUCH_SLOWER, как в режиме 3 CLI.

```bash
stepik-grader --serve                 # http://127.0.0.1:8000
stepik-grader --serve --port 9000     # другой порт
```

- **Только localhost** (`127.0.0.1`) — в сеть не торчит.
- **Без новых зависимостей** — на stdlib `http.server`, переиспользует ту же
  логику грейдинга, что и CLI (`run_tests`/`run_benchmark`).
- Поле пути по умолчанию — папка запуска; последний путь и режим
  запоминаются (localStorage).
- В поле пути — файл решения (`.py`) или папка с решениями; тесты
  резолвятся так же, как в режимах 1/2/3.
- Тот же threat model, что у CLI (нет OS-sandbox) — запускай свои решения.

> Эпик #80 Tier 1 / issue #58. Drag-and-drop загрузка файлов — следующая
> итерация.

### Интеграция с IDE (эпик #80 Tier 2)

Проверять решение прямо из редактора, не переключаясь в терминал.

**VS Code** — сгенерировать задачи одной командой (из папки проекта):

```bash
stepik-grader --init-vscode
```

Создаётся `.vscode/tasks.json` с задачами:
- **Stepik: проверить текущий файл** (дефолтная — `Ctrl+Shift+B`) → `--mode 1 --file ${file}`
- **Stepik: проверить папку** → `--mode 2 --dir ${fileDirname}`
- **Stepik: бенчмарк папки** → `--mode 3 --dir ${fileDirname}`
- **Stepik: веб-интерфейс** → `--serve`

Запуск: `Ctrl+Shift+B` (проверить открытый файл) или `Terminal → Run Task →
«Stepik: …»`. Существующий `tasks.json` не перезатирается — команда предупредит.

> Задачи запускают грейдер через **интерпретатор, выбранный в VS Code**
> (`${command:python.interpreterPath} -m stepik_grader.grader …`), а не через
> консольную команду `stepik-grader`. Поэтому venv **не нужно активировать
> вручную** — достаточно один раз выбрать интерпретатор своего окружения
> (`Ctrl+Shift+P → Python: Select Interpreter`), где установлен пакет. Требуется
> расширение **Python** для VS Code (стандартное). Если задача не запускается —
> проверь, что выбран правильный интерпретатор и в нём выполнено
> `pip install stepik-python-grader` (или `pip install -e .`).

**PyCharm** — через *External Tool* (настраивается вручную, один раз):

1. `Settings → Tools → External Tools → +`
2. Заполнить:
   - **Program:** `$PyInterpreterDirectory$/python`
   - **Arguments:** `-m stepik_grader.grader --mode 1 --file $FilePath$`
   - **Working directory:** `$FileDir$`
3. Запуск: правый клик по файлу → `External Tools → …` (или назначить горячую
   клавишу в `Keymap`).

> **Program:** `$PyInterpreterDirectory$/python` — это интерпретатор проекта
> (venv), выбранный в `Settings → Project → Python Interpreter`. Так venv **не
> нужно активировать вручную**, и грейдер берётся из того же окружения, где он
> установлен (`pip install stepik-python-grader` / `pip install -e .`). Прямой
> вызов `stepik-grader` работает только если venv активирован в PATH — поэтому
> здесь используется явный путь к интерпретатору, как и в задачах VS Code.

#### Дополнительные флаги (Sprint E, issues #50/#51)

```bash
stepik-grader --mode 1 --file task.py --lang en        # меню/сообщения на английском (по умолчанию — ru)
stepik-grader --mode 1 --file task.py --quiet           # без подробного diff (режим 1 по умолчанию verbose)
stepik-grader --mode 2 --dir . --verbose                # с подробным diff по каждому кейсу (режим 2 по умолчанию quiet)
stepik-grader --mode 1 --file task.py --output json     # машиночитаемый JSON вместо таблицы
stepik-grader --mode 2 --dir . --output json > results.json
```

`--verbose`/`--quiet` взаимоисключающие; управляют только режимами 1/2
(режимы 3/4 всегда печатают итоговую таблицу бенчмарка, `--verbose` для них
не имеет смысла). `--output json` печатает ровно одну JSON-строку —
структура повторяет словари, которые уже возвращают `run_tests()`/
`run_benchmark()`/`run_microbench_mode()` (ключи `file`/`results`/`groups` в
зависимости от режима), без отдельной документированной схемы.

#### `--output csv` / `--output markdown` (roadmap, issues #53, #58)

```bash
stepik-grader --mode 2 --dir . --output csv > results.csv
stepik-grader --mode 3 --dir . --output markdown > BENCHMARK.md
```

Те же данные, что и в `--output json`, но плоской таблицей (одна строка на
файл/тест-кейс) в CSV или Markdown-таблице. Пишут в stdout, как и `json` —
для сохранения в файл используется обычное перенаправление шелла, отдельного
флага "сохранить в файл" нет.

#### `--watch` (roadmap, issue #54)

```bash
pip install "stepik-grader[watch]"     # опциональная зависимость: watchfiles

stepik-grader --mode 1 --file task.py --watch
stepik-grader --mode 2 --dir . --watch
```

Перезапускает режим 1/2 при любом изменении внутри отслеживаемого
файла/папки (очищает экран перед повторным запуском). Работает только с
`--mode 1/2` — для 3/4 (дорогой бенчмарк) неприменимо. Без установленного
`watchfiles` печатает сообщение с инструкцией по установке вместо падения.

Для `--mode 2` перезапуск **инкрементальный** (issue #71): под `--watch` кэш
результатов (#56) включается автоматически, поэтому на событие реально
перепрогоняется только изменённый файл, а строки остальных решений берутся из
кэша — на папке с десятком задач цикл обратной связи не деградирует. В конце
печатается сводка «N из M решений из кэша». Отключить можно флагом
`--no-cache` (тогда каждый раз перезапускается вся папка, как раньше).

#### `--cache` / `--clear-cache` (issue #56)

```bash
stepik-grader --mode 1 --file task.py --cache     # первый прогон — считает и кэширует
stepik-grader --mode 1 --file task.py --cache     # без изменений — берёт из кэша (0 перезапусков)
stepik-grader --mode 2 --dir . --cache            # для всей папки: "N из M из кэша"
stepik-grader --clear-cache                       # удалить кэш и выйти
```

Opt-in кэш результатов проверки для `--mode 1/2`. Решение пропускается и
показывается прошлый вердикт, пока не изменились ни содержимое файла решения
(`sha256`), ни файлы его тест-директории (`sha256`); любое изменение
инвалидирует запись и тест перезапускается. Кэш хранится в одном файле
`.grader_cache/results.json` в текущей папке (добавлен в `.gitignore`).
Включить по умолчанию можно через `pyproject.toml`:

```toml
[tool.stepik-grader]
use_cache = true
```

При включённом по умолчанию кэше отдельный запуск можно форсировать флагом
`--no-cache`. Режимы 3/4 (бенчмарк) кэш не используют — их смысл в свежих
замерах времени.

#### pytest-плагин: `pytest --grader-mode` (issue #57)

Если вы привыкли к pytest, грейдер можно запускать как обычный тест-сьют.
Плагин ставится вместе с `stepik-python-grader` (нужен установленный `pytest`)
и по умолчанию бездействует — включается флагом `--grader-mode`:

```bash
pip install pytest                       # если ещё не установлен
pytest --grader-mode StepikTasks/        # собрать решения как pytest-тесты
```

pytest обходит переданную папку, находит файлы-решения (`task*.py`) и на
каждый тест-кейс из соседней `tests/` создаёт отдельный тест. Вывод —
стандартный pytest: `PASSED` для верного решения, `FAILED` с diff
«Ожидалось/Получено» для WA, текст исключения для ошибки выполнения.

```
StepikTasks/module1/task_1.py::test_1 PASSED
StepikTasks/module1/task_1.py::test_2 FAILED
```

Включить без флага можно через `pytest.ini` / `pyproject.toml`:

```toml
[tool.pytest.ini_options]
grader_mode = true
```

Работает совместно с `pytest-xdist` (`-n auto`) и `pytest-cov`. Отдельный
пакет `pytest-stepik-grader` на PyPI пока не выделен — плагин едет внутри
основного пакета.

---

## Работа с API Stepik

### Шаг 0 — Настройка OAuth на Stepik

**1. Создай OAuth-приложение на Stepik**

1. Зайди на <https://stepik.org/oauth2/applications/>
2. Нажми **+ New Application**
3. Заполни поля:

| Поле | Значение |
|---|---|
| Name | любое, например `my-grader` |
| Client type | `Confidential` |
| Authorization grant type | `Authorization code` |
| Redirect uris | `http://localhost:8080/callback` |

4. Нажми **Save** — Stepik покажет `Client ID` и `Client Secret`.

### Шаг 1 — Создай `secrets.json`

Скопируй шаблон:

```bash
cp secrets.json.example secrets.json
```

Заполни своими значениями:

```json
{
  "client_id": "<Client ID из настроек приложения Stepik>",
  "client_secret": "<Client Secret из настроек приложения Stepik>",
  "redirect_uri": "http://localhost:8080/callback",
  "access_token": "",
  "refresh_token": "",
  "expires_at": 0
}
```

### Что означают поля в `secrets.json`

| Поле | Что это |
|---|---|
| `client_id` | ID OAuth-приложения в Stepik |
| `client_secret` | секрет OAuth-приложения |
| `redirect_uri` | адрес для возврата после авторизации |
| `access_token` | текущий токен доступа, заполняется автоматически |
| `refresh_token` | токен обновления, заполняется автоматически |
| `expires_at` | время истечения `access_token` (Unix-timestamp), заполняется автоматически |

> `secrets.json` — локальный файл, не должен попадать в Git.
> При первом запуске оставь `access_token`, `refresh_token`, `expires_at` пустыми — скрипт заполнит их сам через `storage.save_secrets()`.

### Шаг 2 — Скачать данные задачи

```bash
python -m stepik_grader.downloader
```

При первом запуске:
- будет предложено выбрать корневую папку (по умолчанию `StepikTasks`) и путь к `secrets.json`,
- откроется браузер для подтверждения доступа,
- после успешной авторизации токены сохранятся в `secrets.json` через `storage.save_secrets()`.

Введи URL шага, например:

```text
URL шага: https://stepik.org/lesson/569749/step/4?unit=564263
```

Скрипт создаст структуру:

```text
StepikTasks/
└── название-курса/
    └── название-секции/
        └── название-урока/
            └── 04/                     # только номер, если у шага нет заголовка
            └── 04-название-шага/       # номер + slug, если заголовок есть
                ├── task4_1.py          # основное решение (из шаблона задачи или пустой)
                ├── task4_2.py          # заготовка для альтернативного решения (всегда создаётся)
                ├── solution.py         # последний сабмишн с сайта (если доступен)
                ├── meta.json           # метаданные шага (id, lesson, course, ...)
                ├── task.md             # текст задачи в Markdown/HTML
                └── tests/
                    ├── 1               # входные данные теста №1
                    ├── 1.clue          # ожидаемый вывод теста №1
                    ├── 1.type          # тип теста (только для function-style)
                    ├── 2
                    ├── 2.clue
                    └── ...
```

**Схема именования рабочих файлов:**

| Файл | Содержимое | Создаётся |
|---|---|---|
| `task{N}_1.py` | шаблон из задачи (или пустой, если шаблона нет) | всегда |
| `task{N}_2.py` | заготовка для альтернативного решения 1 | всегда (только если файл ещё не существует) |
| `task{N}_3.py` и далее | альтернативные решения 2, 3, … | вручную |
| `solution.py` | последний сабмишн с сайта | если сабмишн доступен |

> Повторный запуск `downloader.py` для того же шага **не перезапишет** `task{N}_2.py` и выше — твои наработки сохранятся.

### Как ищутся тест-кейсы

`downloader.py` перебирает источники по приоритету — первый успешный выигрывает:

| Приоритет | Источник | Поведение |
|---|---|---|
| 1 | ZIP-ссылка в HTML задачи | Скачивается автоматически, распаковывается в `tests/` |
| 2 | HTML-таблица в тексте задачи | Парсится автоматически в `tests/N` + `tests/N.clue` |
| 3 | Ссылка на GitHub в HTML | Адрес печатается в консоль — скачать вручную |
| 4 | Ничего не найдено | Предупреждение `⚠️`, остальные файлы уже сохранены |

OAuth-поток полностью реализован в `stepik_client.py` (`create_user_session`, `authorize_via_browser`, `refresh_access_token`); `downloader.py` только оркестрирует вызовы.

---

## Режимы работы

### Режим 1 — Проверка одного файла

Быстро прогнать одно решение:

```
Enter path to solution file: module1/task1/task1_1.py

File                       Passed   Total time   Avg time   Memory, MB   Status   Fail test
task1_1.py                    5/5       0.1234     0.0247        25.30       OK           -
```

Результат выводится rich-таблицей (зелёный `OK`, красный `FAIL`); при провале
теста в verbose-режиме печатается diff ожидаемого и фактического вывода.

### Режим 2 — Сравнение всех решений

Проходит по всей папке, находит все `task*.py` и верифицирует каждый. Результаты — таблица, сгруппированная по задачам.

```
📂 module1/task1
--------------------------------------------------------------------
File                       Passed   Total time   Avg time   Memory, MB  Status  Fail test
--------------------------------------------------------------------
module1/task1/task1_1.py      5/5       0.1234     0.0247        25.30      OK          -
module1/task1/task1_2.py      5/5       0.1456     0.0291        24.80      OK          -
```

> Режим 2 — проверка **корректности**, не полноценный benchmark.

### Режим 3 — Subprocess-бенчмарк

Запускает N повторений для каждого **прошедшего все тесты** решения через отдельный процесс. Выводит min / median / mean / max / std-dev и сравнивает решения относительно быстрейшего.

**Профили нагрузки (repeats):**

| # | Режим | Повторений |
|---|-------|------------|
| 1 | low | 5 |
| 2 | medium | 15 |
| 3 | high | 50 |
| 4 | custom | 5–100 |

**Что показывает benchmark:**

| Поле | Значение |
|---|---|
| `Runs` | всего запусков |
| `Min` | лучший замер |
| `Median` | медианное время — главный ориентир |
| `Mean` | среднее время |
| `Max` | худший замер |
| `Std dev` | разброс замеров (мало → стабильно) |
| `Memory` | пиковая память |
| `Relative` | относительное время к лучшему решению |
| `Verdict` | `SIMILAR`, `SLOWER`, `MUCH SLOWER` |

```
🚀 Benchmark: module1/task1
---------------------------------------------------------------------
File                       Runs     Min  Median    Mean     Max  Std dev  Memory  Relative   Verdict
---------------------------------------------------------------------
module1/task1/task1_1.py     25  0.0234  0.0249  0.0250  0.0279   0.0011   25.30    100.0%   SIMILAR
module1/task1/task1_2.py     25  0.0257  0.0271  0.0273  0.0301   0.0013   24.80    108.9%    SLOWER
```

### Режим 4 — Micro-bench (timeit)

Замеряет время через `timeit.timeit` внутри одного процесса — без накладных расходов на запуск интерпретатора. Поддерживает script-style (с `input()`) и function-only решения.

> **Колонка `Py-heap` (не `Memory`).** В отличие от режима 3 (RSS через psutil),
> режим 4 для stdin-блоков меряет пик **Python-heap через `tracemalloc`**, а для
> function-блоков — RSS. Это два разных метода в одной колонке, поэтому она
> называется `Py-heap`, а не `Memory`. `tracemalloc` не видит аллокации
> C-расширений (numpy и т.п.) — для чистого Python это приемлемо (issue #66).

**Количество вызовов (calls per run):**

| # | Режим | Вызовов |
|---|-------|---------|
| 1 | fast | 500 |
| 2 | normal | 1 000 |
| 3 | thorough | 5 000 |
| 4 | deep | 50 000 |
| 5 | hard | 100 000 |
| 6 | custom | 100–500 000 |

> Режим `hard` — только для коротких детерминированных функций.

```
⚡ Micro-bench (timeit): module1/task1
---------------------------------------------------------------------------
File                       Repeats  Min, us  Median, us  Mean, us  Max, us  Std dev, us  Relative     Verdict
---------------------------------------------------------------------------
module1/task1/task1_1.py      1000    12.34       13.01     13.12    15.67         0.82    100.0%      SIMILAR
module1/task1/task1_2.py      1000    14.21       15.34     15.45    18.90         1.12    117.9%  MUCH SLOWER
```

### Вердикты тест-кейсов

| Вердикт | Значение |
|---------|----------|
| AC | Accepted — вывод совпал с ожидаемым |
| WA | Wrong Answer — вывод не совпал |
| TLE | Time Limit Exceeded — превышен таймаут |
| RE | Runtime Error — процесс завершился с ненулевым кодом |

---

## Формат тест-кейсов

```
module1/
└── task1/
    ├── task1_1.py        # основное решение
    ├── task1_2.py        # альтернативное решение 1
    └── tests/
        ├── 1             # входные данные теста №1 (stdin)
        ├── 1.clue        # ожидаемый вывод теста №1
        ├── 1.type        # тип теста: файл присутствует только для function-style задач,
        │                 # содержит строку "function"
        ├── 2
        ├── 2.clue
        └── ...
```

**Типы тестов (`*.type`):**

| Значение в файле | Когда создаётся | Поведение |
|---|---|---|
| *(файл отсутствует)* | stdin-задача | входные данные подаются через `stdin` |
| `function` | function-style задача | входные данные — объявление переменной (`x = 5`), передаётся через `exec` |

Файлы тестов читаются в кодировке UTF-8.

> При скачивании задачи через `downloader.py` файлы `tests/N`, `tests/N.clue` и при необходимости `tests/N.type`
> создаются **автоматически** из ZIP-архива или HTML-таблицы в тексте задачи.
> Если ни ZIP, ни таблицы нет — папку `tests/` нужно заполнить вручную.

## Форматы тестов

Грейдер автоматически распознаёт три формата:

### Format 1 — Legacy (Stepik ZIP / downloader.py)
Файлы `1`, `1.clue`, `2`, `2.clue` в папке `tests/`. Создаётся автоматически при скачивании через `downloader.py`.

### Format 2 — Именованные файлы
`input_1.txt` + `expected_1.txt`, `input_2.txt` + `expected_2.txt`...

### Format 3 — python-generation (приоритет)
`tests/input.txt` + `tests/output.txt` с маркерами `# TEST_N:`.
Используется репозиториями [python-generation/Professional](https://github.com/python-generation/Professional), [python-generation/OOP](https://github.com/python-generation/OOP), [python-generation/Samurai](https://github.com/python-generation/Samurai).

Stepik ZIP-архивы автоматически конвертируются в Format 3 при скачивании через `downloader.py`.
GitHub-ссылки в тексте задачи обрабатываются автоматически.

---

## Конфигурация

### Корневая папка задач

При первом запуске `downloader.py` предложит указать:

```
Укажи корневую папку для всех задач Stepik [StepikTasks]:
Укажи путь к secrets.json [secrets.json]:
```

Значения сохраняются в `stepik_config.json`. Структура директорий внутри:

```
StepikTasks/
└── <курс>/<секция>/<урок>/<NN>/ или <NN-шаг>/
```

### Таймаут subprocess

В `core/grader_core.py` константа `TIMEOUT_SECONDS` (по умолчанию `10.0` с) защищает от зависания:

```python
TIMEOUT_SECONDS: float = 10.0  # секунд
```

### Таймаут executor

В `core/executor.py` таймаут передаётся через переменную окружения `EXECUTOR_TIMEOUT` (по умолчанию `10` с). На Unix — `signal.alarm`; на Windows (где `SIGALRM` недоступен) защита обеспечивается таймаутом subprocess уровня `core/grader_core.py` (`TIMEOUT_SECONDS`):

```python
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))
```

### Замер памяти дочернего процесса

```python
MEASURE_CHILD_MEMORY: bool = True  # False — быстрее, но грубее
```

- `True` (по умолчанию) — мониторинг дочернего процесса через `psutil` в отдельном потоке (честнее, но медленнее)
- `False` — RSS родительского процесса (быстро, приблизительно)

### Лимит тест-кейсов для microbench

```python
MICROBENCH_MAX_CASES = 5
```

Ограничивает число тест-кейсов при `timeit`-замерах для стабильного std-dev.

---

## Зависимости

| Пакет | Назначение | Используется в |
|-------|------------|----------------|
| `requests>=2.34.2` | HTTP-запросы к Stepik API, OAuth2, скачивание ZIP | `core/stepik_client.py`, `downloader.py` |
| `psutil>=5.9` | Замер памяти и мониторинг процессов | `core/grader_core.py`, `core/executor.py` |
| `rich>=13.0` | Цветные таблицы, прогресс-бар, WA diff в терминале | `core/reporter.py` |

Dev-зависимости (`pip install -e ".[dev]"`):

| Пакет | Назначение |
|-------|------------|
| `pytest>=8.2` | Тестирование |
| `pytest-cov>=5.0` | Покрытие тестами (`--cov`) |
| `ruff>=0.4` | Линтер и форматтер |

---

## Диагностика

Если `downloader.py` не нашёл данных шага автоматически:

```bash
python -m stepik_grader.diagnostic_stepik
```

Скрипт сохранит в папку `stepik_diagnostics/`:
- `lesson_debug.json`
- `step_debug.json`
- `diagnostic_result.json`

`diagnostic_stepik.py` также позволяет:
- проверить доступность Stepik API;
- убедиться в корректности токена авторизации;
- получить информацию о курсе, уроке или задаче по ID.

---

## Ограничения и безопасность

**Threat model: решения запускаются БЕЗ полноценного sandbox на уровне ОС.**
Дочерний процесс имеет тот же доступ к файловой системе, сети и переменным
окружения, что и сам grader. Защита по времени выполнения есть всегда
(таймаут); на POSIX (Linux/macOS) есть ещё best-effort лимит памяти
(`GraderConfig.max_memory_mb`, по умолчанию 1024 МБ — `resource.setrlimit
(RLIMIT_AS)` через `preexec_fn`); на Windows этого лимита нет (`resource`
недоступен), решение может использовать сколько угодно памяти. Ограничений
диска или сети нет ни на одной платформе. Запускай только доверенные решения
(свои собственные или скачанные из Stepik as-is) — grader не предназначен для
проверки произвольного untrusted-кода (см. `core/executor.py`, который явно
задокументирован как "нет sandbox на уровне ОС" в `CLAUDE.md`).

- **Режимы 1–3 (`grader_core.run_single_test`):** решение запускается напрямую
  через `subprocess.Popen` (для function-mode — во временном wrapper-скрипте,
  импортирующем функцию решения). Единственная защита — `timeout=` у
  `proc.communicate()` (`grader_core.TIMEOUT_SECONDS`, по умолчанию 10с);
  `core/executor.py` (`run_solution`) реализует ту же модель с `signal.alarm`
  на Unix, но используется только в тестах, не в самом grader'е.
- **Режим 4 (`core/microbench_runner.py`):** решения запускаются через
  subprocess (`python -c`) с `timeit.repeat`, защищены фиксированным
  `subprocess.run(timeout=60)`. Исходник передаётся через временный файл;
  `stdin` сбрасывается перед каждой итерацией, а `stdout` решения
  перенаправляется в `os.devnull` на время замера, чтобы его вывод не
  смешивался с числами-таймингами.
- **Microbench: локальный per-call таймаут отсутствует** — решение, зависающее
  внутри одного вызова (не в бесконечном цикле верхнего уровня), упрётся в
  общий 60-секундный `subprocess.run(timeout=60)` вокруг всего замера
  (5 повторов × N итераций), а не в индивидуальный лимит на итерацию.
  Сообщение об ошибке при таймауте указывает `number=<N>` (сколько итераций
  было в замере), чтобы хотя бы приблизительно понять масштаб зависания
  (issue #47 R-01).

---

## Что изменилось по сравнению с оригиналом

Этот форк существенно расширяет [оригинальный проект PavloOps/python_generation_grader](https://github.com/PavloOps/python_generation_grader):

| Возможность | Оригинал | Этот форк |
|---|---|---|
| Проверка одного файла | ✅ | ✅ |
| Сравнение нескольких решений | ❌ | ✅ |
| Subprocess-benchmark | ❌ | ✅ режим 3 |
| Timeit-microbench | ❌ | ✅ режим 4 |
| Разделение корректности и benchmark | ❌ | ✅ |
| Профили нагрузки | ❌ | ✅ low/medium/high/custom |
| Оценка по median (не одиночный замер) | ❌ | ✅ |
| Вердикт SIMILAR / SLOWER / MUCH SLOWER | ❌ | ✅ |
| OAuth2 + скачивание данных задачи с API | ❌ | ✅ |
| Автоизвлечение тест-кейсов из HTML-таблицы | ❌ | ✅ Sprint 4 |
| Автоскачивание тестов из ZIP-архива | ❌ | ✅ Sprint 4 |
| Обнаружение ссылок на GitHub-тесты | ❌ | ✅ Sprint 4 |
| Поддержка function-style тестов (`*.type`) | ❌ | ✅ Sprint 4 |
| Схема файлов task{N}_1.py / task{N}_2.py | ❌ | ✅ Sprint 5 |
| Диагностика API | ❌ | ✅ |
| Поддержка function-only решений | ❌ | ✅ |
| Выделенный HTTP/OAuth слой (`stepik_client.py`) | ❌ | ✅ Sprint 3 |
| Утилиты хранилища без project-зависимостей (`storage.py`) | ❌ | ✅ Sprint 3 |
| pyproject.toml (ruff, pytest, зависимости) | ❌ | ✅ |
| Pre-commit хуки (ruff check + ruff format) | ❌ | ✅ |
| Unit-тесты (622 теста) | ❌ | ✅ |
| OAuth2-фасад (`oauth_flow.py`) | ❌ | ✅ |
| GitHub Actions CI (pytest + ruff) | ❌ | ✅ |

---

## Эволюция версий

Таблица ниже — про **фундаментальные** сдвиги между релизами, а не про
отдельные фичи (полный список изменений — в [`CHANGELOG.md`](CHANGELOG.md)).
Каждая версия — это качественный скачок в отдельной плоскости.

| | **v1.0.0** | **v1.1.0** | **v1.2.0** | **v1.3.0** | **v1.4.0** | **v1.5.0** |
|---|---|---|---|---|---|---|
| **Суть релиза** | Первый стабильный форк — «работает» | Зрелая архитектура, установка как пакет | Безопасность, кроссплатформа, дистрибуция, UX | Онбординг новичков + дистрибуция через PyPI | «Оболочки» — веб-интерфейс и интеграция с IDE | Рабочий поток — кэш, pytest-плагин, инкрементальный watch |
| **Структура кода** | Плоский корень репозитория | src-layout: `src/stepik_grader/` + пакет `core/` | стабилизирована | → | + `web.py`, `ide.py` | + `pytest_plugin.py`, `core/cache.py` |
| **Запуск** | `python grader.py` | `stepik-grader` / `python -m stepik_grader.X` | `python -m stepik_grader` | + нативный файловый диалог (fallback без пути) | + `--serve` (Web UI), `--init-vscode` | + `pytest --grader-mode`; IDE-задачи через интерпретатор |
| **CLI** | Только интерактивное меню | + argparse (`--mode/--file/--dir`) | + `--output json/csv/md`, `--watch`, `--lang`, `--verbose/--quiet` | → | + веб-интерфейс, задачи VS Code | + `--cache/--no-cache/--clear-cache`, инкрементальный `--watch` |
| **CI** | Ubuntu (pytest + ruff) | Ubuntu | Ubuntu + Windows + macOS, + mypy | → | → | → |
| **Безопасность** | Только таймаут выполнения | Только таймаут | + лимит памяти `RLIMIT_AS` (POSIX), явные импорты вместо wildcard | → | → | + `prlimit` после spawn (потокобезопасно) |
| **Дистрибуция** | `git clone` + `requirements.txt` | `pip install -e .` (единый источник — `pyproject.toml`) | GitHub Releases (sdist+wheel), `pipx` из git | + PyPI: `pipx install stepik-python-grader` (OIDC trusted publishing) | → | → |
| **Версионирование** | статичная строка | `importlib.metadata` (единый источник) | задокументированная схема + `scripts/version.py` | → | → | → |
| **Тестов / покрытие** | 260 / 59% | 523 / 95% | 591 / 96% | 599 / 95% | 622 / 95% | 660 / 95% |

> **MAJOR остаётся `1`** на всём протяжении: все изменения укладываются в рамки
> «локальный инструмент для Python-задач Stepik». Смена MAJOR (`2.0`)
> предполагается только при фундаментальном выходе за эти рамки — другие языки
> программирования или платформы. Подробнее — в разделе «Версионирование»
> [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## Python версия

Python **3.12+**
