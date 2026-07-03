# Stepik Python Grader

[![CI](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-1.1.0-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)
![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)

> **Status:** Stable — v1.1.0

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
| `core/grader_core.py` | Application | Загрузка тест-кейсов, исполнение решений: 4 режима работы (`run_tests`, `run_benchmark`, `run_microbench_mode`) |
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
core/grader_core.py    ──→  core/reporter.py     ← _print_case_verbose (run_tests verbose)
core/grader_core.py    ──→  core/executor.py
core/grader_core.py    ──→  core/microbench_runner.py
core/grader_core.py    ──→  core/normalizers.py
core/grader_core.py    ──→  core/parsers.py
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
│  Application  (core/, грейдер разбит по SRP — Sprint 7)       │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
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
│           ├── grader_core.py    # Загрузка тест-кейсов, исполнение решений
│           ├── reporter.py       # rich-таблицы, вывод, verbose-diff
│           ├── executor.py       # Запускатель решений: compile + exec с таймаутом
│           ├── microbench_runner.py  # Timeit-микробенчмарк через subprocess + os.devnull
│           ├── normalizers.py    # Нормализация вывода: округление float, sort/whitespace
│           ├── stepik_client.py  # Infrastructure: OAuth2, requests.Session, Stepik API
│           ├── oauth_flow.py     # Infrastructure/Auth: OAuth2-фасад поверх stepik_client
│           ├── parsers.py        # Парсинг тест-блоков (# TEST_N:)
│           └── storage.py        # Utilities: load/save JSON, save_secrets (нет project-зависимостей)
├── conftest.py                 # Добавляет src/ в sys.path для тестов
├── tests/                     # 523 теста (pytest)
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
├── pyproject.toml             # Конфигурация проекта (ruff, pytest, зависимости, packages.find where=["src"])
├── requirements.txt           # Runtime-зависимости
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

### 1. Клонировать репозиторий

```bash
git clone https://github.com/ArtVsMark/Stepik-Python-Grader.git
cd Stepik-Python-Grader
```

### 2. Создать виртуальное окружение

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

Для разработки (линтер, тесты):

```bash
pip install -e ".[dev]"
```

> Проект использует src-layout (`src/stepik_grader/`, Issue #35) — модули
> запускаются только как пакет (`python -m stepik_grader.X`) или через
> консольную команду `stepik-grader`, установленную `pip install -e .`.
> Прямой запуск `python grader.py` из корня репозитория больше не работает.

---

## Быстрый старт

```bash
python -m stepik_grader.grader
# или, после pip install -e .:
stepik-grader
```

При запуске появится меню:

```
==================================================
  Stepik Python Grader
==================================================
  1. Check one solution
  2. Check all solutions in folder
  3. Benchmark solutions in folder
  4. Micro-benchmark (timeit) for folder
  0. Exit
==================================================
Select mode [0-4]:
```

### Non-interactive запуск (CLI-флаги)

Для запуска из CI/скриптов без интерактивного ввода:

```bash
stepik-grader --version                                    # версия и выход
stepik-grader --mode 1 --file path/to/task.py               # режим 1
stepik-grader --mode 2 --dir path/to/folder                 # режим 2
stepik-grader --mode 3 --dir path/to/folder --repeats 15    # режим 3 (по умолчанию 15)
stepik-grader --mode 4 --dir path/to/folder --number 1000   # режим 4 (по умолчанию 1000)
```

Эквивалентно через `python -m`: `python -m stepik_grader.grader --version` и т.д.

Без `--mode` показывается обычное интерактивное меню.

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

**Threat model: решения запускаются БЕЗ sandbox на уровне ОС.** Дочерний процесс
имеет тот же доступ к файловой системе, сети и переменным окружения, что и сам
grader. Единственная защита — таймаут по времени выполнения; ограничений CPU,
памяти, диска или сети нет. Запускай только доверенные решения (свои
собственные или скачанные из Stepik as-is) — grader не предназначен для
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
| Unit-тесты (520 тестов) | ❌ | ✅ |
| OAuth2-фасад (`oauth_flow.py`) | ❌ | ✅ |
| GitHub Actions CI (pytest + ruff) | ❌ | ✅ |

---

## Python версия

Python **3.12+**
