# Contributing to Stepik-Python-Grader

Спасибо за интерес к проекту! Это руководство поможет быстро начать работу.

> Участвуя в проекте, вы соглашаетесь соблюдать
> [Кодекс поведения](CODE_OF_CONDUCT.md).

---

## Первый вклад за 15 минут

Короткий путь для новичка — от нуля до открытого PR:

1. **Выберите задачу.** Метки [`good first issue`](https://github.com/ArtVsMark/Stepik-Python-Grader/labels/good%20first%20issue)
   и [`help wanted`](https://github.com/ArtVsMark/Stepik-Python-Grader/labels/help%20wanted)
   — задачи с понятным объёмом. Вопросы/идеи — в
   [Discussions](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions).
2. **Форк + ветка от свежего `main`** (`git checkout -b <type>/<slug>`,
   Conventional Commits — см. [§ Правила коммитов](#правила-коммитов-conventional-commits)).
3. **Установка** (5 минут): `python -m venv .venv && source .venv/bin/activate`
   → `pip install -e ".[dev]"` (см. [§ Установка](#установка)).
4. **Локальные гейты перед PR** (зеркалят CI): `pytest tests/ -x -q` ·
   `ruff check .` · `ruff format --check .` · `mypy src/stepik_grader scripts`
   (см. [§ Запуск тестов](#запуск-тестов)).
5. **Одна строка в `CHANGELOG.md`** под `## [Unreleased]` + PR в `main` (черновик
   можно; см. [§ Процесс внесения изменений](#процесс-внесения-изменений)).

Не уверены, за что взяться, или хотите обсудить идею? Откройте
[Discussion](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions) — это
ни к чему не обязывает.

---

## Метки при заведении issue

Каждый новый issue помечается для навигации (полный список — `gh label list`):

- **`area/*`** — область кода: `cli`, `web`, `glossary`, `docs`, `sandbox`,
  `core`, `rules`.
- **`difficulty/*`** — `easy` (локальное изменение, минимум контекста) ·
  `medium` (несколько модулей или неочевидная логика) · `hard` (архитектура,
  кросс-модульные инварианты).
- **`good first issue`** / **`help wanted`** — изолированные задачи с низким
  барьером входа; питают блок «Первый вклад за 15 минут» выше, поэтому ставятся
  только на реально посильное новичку.
- Плюс тип по смыслу: `enhancement`, `bug`, `tech-debt`, `security`,
  `documentation`, …

---

## Архитектура проекта

Подробная архитектурная карта (DAG модулей, слои, «что умеет каждый модуль»)
живёт в [`docs/dev/architecture.md`](docs/dev/architecture.md), дерево проекта — в
[`docs/dev/project-structure.md`](docs/dev/project-structure.md). CONTRIBUTING.md
хранит только правила для контрибьюторов, workflow и политику
версионирования — во избежание расхождений архитектура здесь не дублируется.

---

## Правила размещения файлов

> **Корень репозитория — не свалка. Только `src/stepik_grader/` и
> инфраструктура репозитория (src-layout).**

### В `src/stepik_grader/` остаются точки входа

| Файл / паттерн | Причина |
|---|---|
| `grader.py`, `cli/` | Точки входа — запускаются как `python -m stepik_grader.X` / `stepik-grader` (`cli/` — пакет, `__init__.py` facade + `options.py` leaf) |
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

Правило: **README — входная витрина**, а не свалка
технической памяти. В README живут только:

- описание проекта и бейджи;
- быстрый старт и установка;
- 4 режима работы и основные CLI-флаги;
- ссылки на подробную документацию.

Большие технические разделы живут в [`docs/`](docs/README.md), разложенной по
четырём направлениям — **перечень файлов здесь не дублируется**, он в индексе
каждого направления:

| Направление | Что туда | Индекс |
|---|---|---|
| `docs/use/` | справка пользователю: установка, режимы, флаги, веб, конфигурация | [`docs/use/README.md`](docs/use/README.md) |
| `docs/dev/` | устройство кода: архитектура, контракты, API, ADR (+ `design/` — спроектированное без кода) | [`docs/dev/README.md`](docs/dev/README.md) |
| `docs/agent/` | служебное для Claude Code: роли, очередь работ | [`docs/agent/README.md`](docs/agent/README.md) |
| `docs/archive/` | всё историческое: история разработки, архив CHANGELOG, разовые аудиты | [`docs/archive/README.md`](docs/archive/README.md) |

**Куда добавлять новый раздел.** Спроси, кто читатель: пользователь → `use/`
(+ строчка в README, если это заметная возможность); разработчик → `dev/`;
инвариант ядра → `CLAUDE.md`. Не уверен между `use/` и `dev/` — реши по вопросу,
на который отвечает текст: «как этим пользоваться» или «как это внутри».

**Чего в активном документе быть не должно:** журнала работ. `use/` и `dev/`
описывают, как всё работает **сейчас**. «Что сделано» → `CHANGELOG.md`, «что
предстоит» → GitHub Issues, «как шло» → `docs/archive/`. Номер issue уместен
только там, где объясняет неочевидный компромисс, а не хвостом в каждой строке.
Числа тестов, покрытия и размера глоссария не вписываются в прозу — они живут в
бейджах README.

Не давай README снова разрастаться — проверяй это при ревью PR, добавляющих
документацию.

**Line-budget и link-check.** Правило «README — витрина» защищено
машинно: CI-job `docs-guardrails` (`.github/workflows/ci.yml`) запускает
[`scripts/check_docs_guardrails.py`](scripts/check_docs_guardrails.py), который

- **падает, если `README.md` превышает 220 строк** (константа
  `README_LINE_BUDGET` в скрипте — единственный источник числа; при изменении
  править и здесь);
- **проверяет локальные Markdown-ссылки** README ↔ `docs/` ↔ корневые `*.md`:
  относительный путь должен вести на существующий файл, а якорь
  (`file.md#заголовок`) — на реально существующий заголовок. Внешние
  ссылки (http/https/mailto) осознанно не проверяются, чтобы CI не был флаки.

Запуск локально перед PR, добавляющим/меняющим документацию:

```bash
python scripts/check_docs_guardrails.py     # exit 0 — ок, 1 — нарушение
```

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
                              # ОБЯЗАТЕЛЬНО editable (src-layout): без
                              # него пакет stepik_grader не импортируется, и
                              # консольная команда stepik-grader не появится на
                              # PATH. Также: cli.__version__ читается через
                              # importlib.metadata из package-метаданных
                              # — без editable install падает
                              # на fallback "0.0.0+unknown"
```

> Нет отдельного `requirements.txt`: единственный источник зависимостей —
> `pyproject.toml`.

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

### E2E-тесты (Playwright, опционально)

Смок-тесты реального веб-UI (`--serve`) через headless Chromium — свыше 20
сценариев по всем разделам workspace (режимы 2/1 грейдинга и a11y-озвучка,
«Функции в коде», глоссарий с deep-link, песочница с пошаговым плеером и
memory-диаграммой, «Прогресс», AI-подсказка с consent-гейтом, локализация
RU/EN) плюс регрессионный тест на XSS в `app.js` (экранирование в `esc()`,
экранирование в `esc()`). Живут в
`tests/e2e/`, **не входят**
в `pytest tests/` (см. `norecursedirs` в `pyproject.toml`) — отдельный
`playwright` нужен только для них, это dev-extra, а не runtime-зависимость:

```bash
pip install -e ".[e2e]"        # playwright>=1.40, отдельно от [dev]
playwright install chromium    # скачать браузер (один раз)
pytest tests/e2e/ -v           # запустить e2e-сьют явно
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
[`docs/configuration.md § Формат тест-кейсов`](docs/use/configuration.md#формат-тест-кейсов);
здесь не дублируется во избежание расхождений.

---

## Соглашения по коду

Обязательные инварианты — union-типы вместо `Optional`/`List`, `from __future__
import annotations` в новом файле, `pathlib` вместо `os.path`, `Path` в
путь-сигнатурах, `sys.executable` вместо строки `"python"`, `__all__` в новых
модулях, вывод через `_console`, никаких голых `except:` — живут в
[`CLAUDE.md § Стиль кода`](CLAUDE.md). Здесь только то, чего там нет:

- **Docstrings** публичных функций — краткий Google-style.
- Все импорты в начале файла; lazy import — лишь с явной причиной в комментарии
  (например, разрыв DAG-ребра).
- Константы модуля — в начале файла, до функций.
- Строгость `mypy` (`disallow_untyped_defs`, `disallow_incomplete_defs`,
  `check_untyped_defs`, `warn_return_any`, `ignore_missing_imports`) задана в
  `[tool.mypy]` — не ослабляйте её точечными `# type: ignore` без объяснения.

Паттерн имён файлов решений (`task.py`, `task1.py`, `task1_2.py`, `task_12.py`) —
в [`docs/use/configuration.md`](docs/use/configuration.md).

---

## Процесс внесения изменений

1. Создайте ветку: `git checkout -b feat/your-feature`
2. Внесите изменения с тестами
3. Обновите `CHANGELOG.md` — запись под `## [Unreleased]` в **каждом** PR, без
   исключений для рефакторингов (одна строка на изменение)
4. Прогоните `pytest tests/ -v`
5. Прогоните `mypy src/stepik_grader scripts`
6. Прогоните `pre-commit run --all-files`
7. Создайте Pull Request в `main`

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

## Добавь свою карточку в глоссарий

Локальный глоссарий (`src/stepik_grader/glossary/data/*.json`) — хороший первый
вклад: малый диф, воспроизводимая проверка, реальная польза ученикам. Новые
термины, функции и исключения добавляются полуавтоматическим конвейером
`scripts/glossary_draft_pipeline.py`: он **валидирует примеры прогоном** и
**никогда не мержит в готовую базу автоматически** — карточка попадает в базу
только через ревью человеком.

Найти пробел → предложить черновик → сверить и открыть PR. Команды всех трёх
шагов, формат карточки и Python-API —
[`docs/dev/glossary.md`](docs/dev/glossary.md). Число готовых карточек считает
`scripts/generate_glossary_badge.py` (живой бейдж), вручную его не правьте.

---

## Версионирование

**Это НЕ SemVer.** Своя схема: тег ставится только на границе MINOR (все теги
имеют вид `vX.Y.0`), PATCH — счётчик принятых изменений после тега, версия
пакета вычисляется из git-тегов через `setuptools-scm`, а `version` в
`pyproject.toml` править **нельзя** — статической строки там нет.

Полная политика — [`docs/dev/versioning.md`](docs/dev/versioning.md): следствия
схемы, release- vs dev-форма, когда тегировать, блокирующая ротация CHANGELOG
перед тегом, CI-защита от дрейфа.

---

## Известные ограничения

- Memory measurement через `psutil` может давать нулевые значения для очень быстрых процессов.
