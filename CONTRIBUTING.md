# Contributing to Stepik-Python-Grader

Спасибо за интерес к проекту! Это руководство поможет быстро начать работу.

---

## Архитектура проекта

> src/-layout (Issue #35): весь пакет живёт в `src/stepik_grader/`. Пути
> ниже — относительно этого пакета.

```
Stepik-Python-Grader/
├── src/
│   └── stepik_grader/
│       ├── grader.py            # Тонкий фасад обратной совместимости (Sprint 7)
│       ├── cli.py                # Интерактивное меню (режимы 0-4), entry point stepik-grader
│       ├── config.py            # Конфигурация уровня проекта
│       ├── downloader.py        # Загрузка задач/тестов со Stepik API
│       ├── diagnostic_stepik.py # Диагностика и отладка API
│       └── core/                # Все внутренние модули проекта
│           ├── grader_core.py       # Исполнение (run_single_test/run_tests/
│           │                       # run_benchmark/run_microbench_mode)
│           ├── test_loader.py       # Обнаружение файлов-решений, загрузка тест-кейсов,
│           │                       # resolve_test_dir (Issue #45 A-01)
│           ├── mode_detector.py     # Детекция stdin/function (Issue #45 A-01)
│           ├── wrapper_builder.py   # Генерация wrapper-скриптов (Issue #45 A-01)
│           ├── reporter.py          # rich-таблицы, вывод, verbose-diff
│           ├── executor.py          # Запуск кода из строки (run_solution)
│           ├── microbench_runner.py # timeit-бенчмарк (run_microbench)
│           ├── normalizers.py       # Нормализация float-вывода
│           ├── storage.py           # Чтение JSON-файлов
│           ├── stepik_client.py     # HTTP-клиент Stepik API
│           ├── oauth_flow.py        # OAuth 2.0 авторизация
│           └── parsers.py           # Парсинг тест-блоков (# TEST_N:)
├── conftest.py          # sys.path.insert(0, "src") — pytest discovery
└── tests/               # Автотесты
```

### Слои и зоны ответственности

| Модуль | Слой | Зона ответственности |
|---|---|---|
| `grader.py` | Application | Фасад — реэкспортирует grader_core/reporter/cli |
| `core/grader_core.py` | Application | Исполнение тест-кейса в subprocess, агрегация статистики |
| `core/test_loader.py` | Application | Обнаружение файлов-решений, загрузка тест-кейсов, resolve_test_dir |
| `core/mode_detector.py` | Application | Детекция режима запуска (stdin vs function) |
| `core/wrapper_builder.py` | Application | Генерация wrapper-скриптов для function-mode |
| `core/reporter.py` | Application / UI | rich-таблицы, вердикты, verbose-diff |
| `cli.py` | Application / CLI | Интерактивное меню, профили нагрузки |
| `core/executor.py` | Infrastructure | Subprocess-запуск кода из строки |
| `core/microbench_runner.py` | Infrastructure | timeit-замеры |
| `core/normalizers.py` | Domain | Нормализация float |
| `core/storage.py` | Infrastructure | I/O JSON |
| `downloader.py` | Application | Загрузка данных Stepik |
| `core/stepik_client.py` | Infrastructure | HTTP Stepik API |

---

## Правила размещения файлов

> **Корень репозитория — не свалка. Только `src/stepik_grader/` и
> инфраструктура репозитория (Issue #35, src-layout).**

### В `src/stepik_grader/` остаются точки входа

| Файл / паттерн | Причина |
|---|---|
| `grader.py`, `cli.py` | Точки входа — запускаются как `python -m stepik_grader.X` / `stepik-grader` |
| `config.py` | Project-level конфигурация; импортируется из `core/*` (перенос вызовет circular import) |
| `downloader.py`, `diagnostic_stepik.py` | Самостоятельные пользовательские утилиты |

### В корне репозитория остаются только

| Файл / паттерн | Причина |
|---|---|
| `conftest.py`, `pyproject.toml` | Инфраструктура тестирования и сборки |
| `*.md`, `*.txt`, `*.toml`, `*.json.example` | Документация и шаблоны конфигурации |

### В `src/stepik_grader/core/` — всё остальное

Любой новый **внутренний модуль** (библиотечный код, не запускаемый пользователем напрямую) создаётся в `core/`, а не рядом с точками входа.

**Правило одной строки:**
> Если файл не запускается пользователем напрямую и не является конфигурацией
> уровня проекта — его место в `core/`, а не рядом с точками входа.

### В `tests/` — все тесты

Файлы `test_*.py` и `*_test.py` — только в `tests/`. Никаких тестовых файлов в корне.

---

## Документация: README как витрина, `docs/` как база знаний

Правило (issue #107 / эпик #102): **README — входная витрина**, а не свалка
технической памяти. В README живут только:

- описание проекта и бейджи;
- быстрый старт и установка;
- 4 режима работы и основные CLI-флаги;
- ссылки на подробную документацию.

Большие технические разделы выносятся в `docs/`:

| Раздел | Файл |
|--------|------|
| Карта документации + канонические источники | [`docs/README.md`](docs/README.md) |
| Установка, OAuth, secrets.json, диагностика | [`docs/installation.md`](docs/installation.md) |
| Режимы работы, CLI-флаги, web/IDE, скачивание задачи | [`docs/grader-workflow.md`](docs/grader-workflow.md) |
| Конфигурация (`[tool.stepik-grader]`), форматы тест-кейсов, ограничения и безопасность | [`docs/configuration.md`](docs/configuration.md) |
| Архитектура модулей (DAG, слои, «что умеет») | [`docs/architecture.md`](docs/architecture.md) |
| Структура проекта (дерево файлов) | [`docs/project-structure.md`](docs/project-structure.md) |
| Версии и сравнение с оригиналом | [`docs/versions.md`](docs/versions.md) |

**Куда добавлять новый большой раздел:** если это справка для пользователя
(режим, флаг, сценарий) — коротко в README + при необходимости подробности в
`docs/`. Если это внутренняя техническая память (архитектура, история,
инварианты) — сразу в `docs/` (или `CLAUDE.md` для инвариантов ядра), а в
README максимум строчка-ссылка. Не давай README снова разрастаться —
проверяй это при ревью PR, добавляющих документацию.

---

## Быстрый старт

### Требования

- Python 3.12+
- Windows / macOS / Linux
- `.venv` (рекомендуется)

### Установка

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS/Linux

pip install -e ".[dev]"      # runtime (requests/psutil/rich) + pytest/ruff/mypy
                              # ОБЯЗАТЕЛЬНО editable (Issue #35, src-layout): без
                              # него пакет stepik_grader не импортируется, и
                              # консольная команда stepik-grader не появится на
                              # PATH. Также: cli.__version__ читается через
                              # importlib.metadata из package-метаданных
                              # (Issue #36) — без editable install падает
                              # на fallback "0.0.0+unknown"
```

> Нет отдельного `requirements.txt` — единственный источник зависимостей
> `pyproject.toml` (issue #51 P-01, было дублирование).

> После bump'а версии в `pyproject.toml` перезапусти `pip install -e .`,
> иначе `cli.__version__`/`stepik-grader --version` останутся показывать
> старое значение (package-метаданные не обновляются автоматически).
>
> Запуск без установки: `pytest` работает благодаря `sys.path.insert(0,
> "src")` в `conftest.py`, но `python -m stepik_grader.grader` и
> `stepik-grader` требуют `pip install -e .`.

### Запуск тестов

```bash
pytest tests/ -v
```

### Pre-commit хуки

```bash
pip install pre-commit
pre-commit install
```

---

## Форматы тест-кейсов

Grader поддерживает три автодетектируемых формата (Legacy `N`/`N.clue`,
именованные `input_N.txt`/`expected_N.txt`, python-generation
`input.txt`/`output.txt` с `# TEST_N:`). Канонический справочник — в
[`docs/configuration.md § Формат тест-кейсов`](docs/configuration.md#формат-тест-кейсов);
здесь не дублируется во избежание расхождений.

---

## Имена файлов решений

Grader автоматически ищет файлы по паттерну:

```
task.py        # базовый
task1.py       # с номером задачи
task1_2.py     # задача + номер решения
task_1.py      # стиль downloader.py
task_12.py     # двузначный суффикс
```

---

## Соглашения по коду

- **Type hints обязательны** для всех публичных функций
- **Docstrings** для всех публичных функций (краткий формат Google-style)
- **PEP 8** — форматирование через `ruff format` (настроен в `pyproject.toml`)
- **Линтинг** через `ruff check`
- **Типизация** через `mypy src/stepik_grader --ignore-missing-imports` (Sprint D, issue #49 C-02)
- Все импорты в начале файла (никаких lazy imports без явной причины)
- Константы модуля — в начале файла, до функций

---

## Процесс внесения изменений

1. Создайте ветку: `git checkout -b feat/your-feature`
2. Внесите изменения с тестами
3. Прогоните `pytest tests/ -v`
4. Прогоните `mypy src/stepik_grader --ignore-missing-imports`
5. Прогоните `pre-commit run --all-files`
6. Создайте Pull Request в `main`

### Правила коммитов (Conventional Commits)

```
feat:  новая функциональность
fix:   исправление бага
refactor: рефакторинг без изменения поведения
test:  добавление/изменение тестов
docs:  документация
chore: инфраструктура, зависимости
```

---

## Версионирование (issue #68)

> **Это НЕ SemVer.** Проект использует собственную схему. Не применяйте
> привычные правила SemVer — они сломают инвариант «каждый тег = `vX.Y.0`».

```
MAJOR . MINOR . PATCH
  │       │       │
  │       │       └─ +1 на КАЖДЫЙ коммит / закрытие issue;
  │       │          обнуляется при инкременте MINOR
  │       │
  │       └─ +1 ВСЕГДА при постановке git-тега + GitHub Release
  │
  └─ меняется только при фундаментальных изменениях:
     выход за пределы локального инструмента,
     поддержка других языков программирования и т.п.
```

**Следствия схемы:**

- Теги ставятся только на границе MINOR → **все теги имеют вид `vX.Y.0`**.
  PATCH-тегов не существует, поэтому коллизий версий нет.
- **PATCH — это счётчик коммитов с момента последнего тега**, а не номер
  «патч-релиза». Например, `1.2.17` — это «17 коммитов после тега `v1.2.0`»,
  **а НЕ «17-й патч-релиз»**. Отдельного релиза `1.2.17` не существует и не
  публикуется — на PyPI/Releases уходят только тегированные `X.Y.0`.
- `CHANGELOG.md`: блок `[Unreleased]` копится и при теге переносится в
  `[X.Y.0]`. Промежуточные PATCH-версии в CHANGELOG **не** документируются
  построчно (иначе CHANGELOG превратится в `git log`).

**Автоматический расчёт версии.** Схема математически совпадает с
`git describe --tags --long` (`vX.Y.0-N-gHASH` → `X.Y.N`). Скрипт
`scripts/version.py` вычисляет версию по этой схеме без ручных правок:

```bash
python scripts/version.py     # → напр. 1.2.17
```

До первого тега (когда `git describe` ещё нечего описывать) скрипт берёт
`MAJOR.MINOR` из `pyproject.toml` и PATCH = число коммитов в истории —
разумный fallback, дающий монотонно растущий PATCH уже сейчас.

> На момент написания сборка (`pyproject.toml`) всё ещё объявляет
> `version` статически, а `stepik_grader.__version__` читается через
> `importlib.metadata`. `scripts/version.py` — вспомогательный/справочный
> инструмент (например, для CI-тегирования); перевод сборки на динамическую
> версию (`setuptools-scm`) — отдельная задача, требующая новой build-
> зависимости, и здесь не делается.

---

## Известные ограничения

- Memory measurement через `psutil` может давать нулевые значения для очень быстрых процессов.
