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
живёт в [`docs/architecture.md`](docs/architecture.md), дерево проекта — в
[`docs/project-structure.md`](docs/project-structure.md). CONTRIBUTING.md
хранит только правила для контрибьюторов, workflow и политику
версионирования — во избежание расхождений архитектура здесь не дублируется.

---

## Правила размещения файлов

> **Корень репозитория — не свалка. Только `src/stepik_grader/` и
> инфраструктура репозитория (Issue #35, src-layout).**

### В `src/stepik_grader/` остаются точки входа

| Файл / паттерн | Причина |
|---|---|
| `grader.py`, `cli/` | Точки входа — запускаются как `python -m stepik_grader.X` / `stepik-grader` (`cli/` — пакет, `__init__.py` facade + `options.py` leaf, issue #119) |
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
| WEB MVP — реализовано (UX, error/action cards, «Функции в коде») | [`docs/web-current.md`](docs/web-current.md) |
| WEB MVP — замыслы/отложенное/отклонённое | [`docs/web-design.md`](docs/web-design.md) |
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

**Line-budget и link-check (issue #173).** Правило «README — витрина» защищено
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

### E2E-тесты (Playwright, опционально, issue #263)

Смок-тесты реального веб-UI (`--serve`) через headless Chromium — свыше 20
сценариев по всем разделам workspace (режимы 2/1 грейдинга и a11y-озвучка,
«Функции в коде», глоссарий с deep-link, песочница с пошаговым плеером и
memory-диаграммой, «Прогресс», AI-подсказка с consent-гейтом, локализация
RU/EN) плюс регрессионный тест на XSS в `app.js` (экранирование в `esc()`,
issue #214). Живут в
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
- **Типизация** через `mypy src/stepik_grader scripts` — строгость (`disallow_untyped_defs`,
  `disallow_incomplete_defs`, `check_untyped_defs`, `warn_return_any`,
  `ignore_missing_imports`) задана в `[tool.mypy]` (Sprint D, issue #49 C-02 / #441)
- Все импорты в начале файла (никаких lazy imports без явной причины)
- Константы модуля — в начале файла, до функций

---

## Процесс внесения изменений

1. Создайте ветку: `git checkout -b feat/your-feature`
2. Внесите изменения с тестами
3. Обновите `CHANGELOG.md` — запись под `## [Unreleased]` в **каждом** PR, без
   исключений для рефакторингов (одна строка на изменение, issue #373)
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
вклад: малый диф, воспроизводимая проверка, реальная польза ученикам. Эпик
наполнения #363 завершён (0 черновиков), но новые термины/функции/исключения
всегда можно добавить полуавтоматическим конвейером
`scripts/glossary_draft_pipeline.py` (issue #438) — он **валидирует примеры
прогоном** и **никогда не мержит в готовую базу автоматически**.

Три шага (человеческое ревью обязательно):

1. **Найти пробел** — сгенерировать очередь недостающих сущностей относительно
   официального Python/stdlib:
   ```bash
   python -m stepik_grader.glossary.coverage \
       --cards src/stepik_grader/glossary/data --missing-out gaps.json
   ```
2. **Предложить черновик** — конвейер соберёт карточку по шаблону, прогонит её
   примеры (`# → результат` сверяется с фактическим выводом) и покажет
   review-diff; запись идёт только для валидных примеров и только в отдельный
   draft-файл, никогда в `ready`-базу:
   ```bash
   python scripts/glossary_draft_pipeline.py propose --qualname str.rjust \
       --content-file draft.json --write review-drafts.json
   # аудит примеров уже существующих карточек:
   python scripts/glossary_draft_pipeline.py check --base src/stepik_grader/glossary/data
   ```
3. **Ревью и PR** — вручную сверьте `summary` (RU+EN), примеры и `status`,
   перенесите проверенную карточку в нужный `data/<cg>.json` со `status: "ready"`,
   добавьте строку в `CHANGELOG.md` и откройте PR. Ни одна карточка не попадает в
   базу автомержем — только через ревью человеком.

Формат карточки и Python-API — [docs/glossary.md](docs/glossary.md); число
готовых карточек считает `scripts/generate_glossary_badge.py` (живой бейдж, не
хардкод — issue #398), вручную его не правьте.

---

## Версионирование (issue #68)

> **Это НЕ SemVer.** Проект использует собственную схему. Не применяйте
> привычные правила SemVer — они сломают инвариант «каждый тег = `vX.Y.0`».

```
MAJOR . MINOR . PATCH
  │       │       │
  │       │       └─ +1 на принятое изменение (смерженный PR, first-parent);
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
- **PATCH — счётчик принятых изменений с последнего тега**, а не номер
  «патч-релиза». В «логическом» счётчике (`scripts/version.py`, README-бейдж)
  это коммиты на **first-parent** линии — один смерженный PR даёт +1,
  независимо от того, на сколько коммитов он разбит, и без CI-бота
  (`chore(ci): update badges`, issue #231); merge-дубли и внутренние коммиты PR
  не завышают счётчик. Например, `1.2.17` — это «17 принятых изменений после
  тега `v1.2.0`», **а НЕ «17-й патч-релиз»**. Отдельного релиза `1.2.17` не
  существует — на PyPI/Releases уходят только тегированные `X.Y.0`.
  (Метаданная `setuptools-scm` `X.Y.0.postN` — независимая «сырая дистанция»:
  считает **все** коммиты, включая CI-бота и merge; см. § Release vs dev.)
- `CHANGELOG.md`: блок `[Unreleased]` копится и при теге переносится в
  `[X.Y.0]`. Промежуточные PATCH-версии в CHANGELOG **не** документируются
  построчно (иначе CHANGELOG превратится в `git log`).
- **Краткость и ротация CHANGELOG (issue #373).** Запись = одна строка на
  изменение (`- <что> (#PR)`), детали — в PR/issue; многострочные пересказы —
  антипаттерн (раздули `[Unreleased]` перед v1.8.0). В живом `CHANGELOG.md`
  держим только `[Unreleased]` + **три последних MINOR**; более старые релизы
  ротируются в [`docs/changelog-archive.md`](docs/changelog-archive.md), а
  `scripts/check_docs_guardrails.py` стережёт лимит в 3 версионных заголовка.

### Release-версия vs dev-версия (после PR #183 / issue #162)

**Git-теги — единственный источник истины.** `pyproject.toml` больше НЕ
объявляет `version` статически: она вычисляется из git-тегов через
`setuptools-scm` (`dynamic = ["version"]`, `version_scheme = "post-release"`).
Вручную править `[project].version` **нельзя** — статической строки там нет,
а CI-проверка её возврат заблокирует (см. ниже).

Из-за этого одновременно живут **две формы** одного и того же номера:

| Форма | Когда | Пример | Откуда берётся | Для чего |
|---|---|---|---|---|
| **Release** (метаданные пакета на теге) | HEAD стоит ровно на теге `vX.Y.0` | `1.5.0` | `setuptools-scm` | То, что уходит на PyPI / GitHub Release; чистый PEP 440 без local-сегмента |
| **Dev** (метаданные пакета вне тега) | N коммитов после последнего тега (**все** коммиты, включая CI-бота и merge) | `1.5.0.post3+g1a2b3c4` | `setuptools-scm` (post-release) | Технически отличимая сборка «после релиза `1.5.0`» — видно, что это не официальный релиз |
| **Логический счётчик** (человекочитаемый) | всегда | `1.5.3` | `scripts/version.py` (если присутствует) | Удобный «тег + число смерженных PR» (first-parent, без CI-бота) для тегирования и заметок; **это не PEP 440** и не метаданные пакета |

**Почему две формы сосуществуют.** `setuptools-scm` обязан выдавать валидный
PEP 440 (`X.Y.0.postN+g<hash>`) — это то, что понимают `pip`/PyPI и что
попадает в метаданные установленного пакета. Логическая схема проекта
(`X.Y.N`, где `N` = число принятых изменений после тега — first-parent коммитов
без CI-бота, ≈ смерженных PR) удобнее человеку, но PEP 440 не является —
поэтому она остаётся отдельным справочным счётчиком в `scripts/version.py`, а
не источником для сборки. (Из-за разной логики подсчёта логический `N` обычно
**меньше** postN-дистанции `setuptools-scm`, которая считает все коммиты.)

**Когда какая используется:**

- `stepik_grader.__version__` и `stepik-grader --version` читают **метаданные
  пакета** (`importlib.metadata`) — то есть release- или dev-форму,
  вычисленную `setuptools-scm` при `pip install`/сборке. На теге увидишь
  `1.5.0`, вне тега — `1.5.0.postN+g<hash>`.
- `scripts/version.py` (если есть) — печатает **логический** `X.Y.N`; нужен
  человеку при подготовке тега/заметок, не при сборке.

> **UX вывода `--version`** (issue #163, закрыт). На теге `--version` печатает
> чистые метаданные пакета (`1.5.0`) без изменений. Вне тега к тем же сырым
> метаданным добавляется явная пометка: `1.5.0.post3+g1a2b3c4 (dev build, not
> a release)` — чтобы пользователь не принял PEP 440 postN/local-сегмент за
> официальный релиз. Логика — `cli._format_version_for_display()` /
> `cli._is_dev_build()` (наличие `+` в версии); способ вычисления самой
> версии (`setuptools-scm`) не менялся.

**Когда поднимать/тегировать релиз:**

- MINOR (`vX.(Y+1).0`) — качественный скачок (см. таблицу выше). Ставится
  git-тег `vX.Y.0` + GitHub Release; `setuptools-scm` подхватит его сам.
- PATCH **не тегируется** — это просто «дистанция коммитов» от последнего
  тега, она растёт автоматически.
- MAJOR (`v2.0.0`) — выход за рамки «локальный инструмент для Python-задач
  Stepik» (другие языки/платформы).
- Ручное редактирование версии в `pyproject.toml` не требуется и запрещено —
  всё делает тег.
- **⛔ Блокирующий шаг ПЕРЕД тегом `vX.Y.0` — ротация CHANGELOG (issue #562).**
  До постановки тега перенеси самый старый MINOR из `CHANGELOG.md` в
  [`docs/changelog-archive.md`](docs/changelog-archive.md) дословно, чтобы в
  живом `CHANGELOG.md` осталось ровно `[Unreleased]` + **три последних MINOR**
  (см. «Краткость и ротация CHANGELOG» выше), и переименуй `[Unreleased]` →
  `[X.Y.0] — ДАТА`, добавив сверху новый пустой `[Unreleased]`. Это **не**
  опциональная уборка, а гейт: `check_docs_guardrails.py` держит версионный
  бюджет `CHANGELOG.md` = 3, и без ротации CI на релизе **падает**.

**CI-защита от дрейфа.** `scripts/check_version_consistency.py` (issue #165)
следит, чтобы (1) статический `version` не вернулся в `pyproject.toml` и
(2) «текущая версия» в `CHECKPOINT.md`/`CHANGELOG.md`/`CLAUDE.md` не
расходилась с последним git-тегом. Baseline берётся из
`git describe --tags`. Поэтому отдельной ручной сверки версий по репозиторию
делать не нужно — за это отвечает CI.

> Полную таблицу эволюции релизов (`v1.0.0 … v1.8.0`) и отличия от оригинала
> см. в [`docs/versions.md`](docs/versions.md) — он ссылается на эту политику,
> а не копирует её.

---

## Известные ограничения

- Memory measurement через `psutil` может давать нулевые значения для очень быстрых процессов.
