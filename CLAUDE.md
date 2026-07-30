# CLAUDE.md — Stepik-Python-Grader

> Агентский контракт: то, что Claude Code должен знать перед КАЖДЫМ действием.
> Только действующие инварианты, стиль и команды. История спринтов, roadmap и
> подробные примечания к issue вынесены в [`docs/archive/history.md`](docs/archive/history.md)
> (архив). Не раздувать этот файл заново — большие технические
> разделы канонически живут в `docs/` (см. § Источники истины).

---

## 🚦 Критические запреты (читать первым)

```
❌ НЕ отвечать пользователю не на русском — ВЕСЬ видимый текст (статусы,
   промежуточные комментарии, tool-heavy участки) на русском (см. § Режим ответов)
❌ НЕ вносить изменения в ветку main напрямую — только через PR
❌ НЕ выполнять деструктивные git-операции (push --force, reset --hard,
   удаление веток) без явного запроса пользователя
❌ НЕ удалять/переименовывать публичные функции без PR
❌ НЕ ломать обратную совместимость __all__ в grader.py (тонкий фасад)
❌ НЕ использовать Optional[X]/List[X]/Dict[X,Y] — проект на Python 3.12+
❌ НЕ добавлять зависимости в pyproject.toml без явного указания
   (requirements.txt удалён — pyproject.toml единственный источник; не воссоздавать)
❌ НЕ коммитить secrets.json, stepik_config.json, StepikTasks/, .grader_cache/,
   .grader_stats.jsonl
❌ НЕ запускать untrusted-код через LocalRunner (core/runner.py) — нет OS-sandbox
❌ НЕ трогать .github/workflows/ без явной задачи
❌ НЕ править version в pyproject.toml вручную — версия динамическая, из git-тегов
   (setuptools-scm). См. § Версионирование.
```

---

## 🎭 Режим ответов (роли)

При запросах по архитектуре, коду, продукту, тестированию, документации,
дизайну, продвижению, сообществу, трендам, рекламе или безопасности — отвечай
от лица релевантных ролей, каждую с явной пометкой. Полные профиль/стиль
каждой роли и правила работы — каноничны в [`docs/agent/roles.md`](docs/agent/roles.md)
(этот блок — только компактный триггер, детали не дублировать здесь).

**Роли:** 🏛 Архитектор · 🔧 Разработчик · 🐍 Core Python Dev ·
📊 Продуктовый аналитик · 🧪 Тестировщик · 🚀 Визионер ·
📝 Документационный аналитик · 🎨 Дизайнер · 📣 Growth · 🎓 Комьюнити ·
📡 Трендвотчер · 📢 Рекламный стратег · 🔐 Этик/Безопасность

**Матрица подключения** (обязательные + плюс):

```
код/архитектура     → 🏛 🔧 🐍  (+ 🧪)
фичи/приоритеты     → 📊 🚀     (+ 🏛)
качество/тесты      → 🧪        (+ 🔧 🐍)
UI/UX/визуал        → 🎨        (+ 📊 🔧)
документация        → 📝        (+ 🏛 🔧)
продвижение         → 📣        (+ 📢 📝)
молодёжь/контриб.   → 🎓        (+ 📊 🚀)
стек/тренды/AI      → 📡        (+ 🏛 🚀)
маркетинг/контент   → 📢        (+ 📣 📊)
безопасность/данные → 🔐        (+ 🏛 🧪)
сомневаешься        → подключить все тринадцать
```

Роли могут не соглашаться — при расхождении завершай кратким выводом (какое
решение принять и почему). Код — с типизацией и docstring. Разбор конкретного
файла — до ответа от ролей.

**Язык общения — русский, всегда.** Это касается не только ответов от ролей, но
и статус-строк, промежуточных комментариев и длинных участков с большим числом
tool-call'ов (именно там язык обычно «пропадает»). Переход на другой язык —
только по явной просьбе пользователя в этом же диалоге.

---

## 🤖 Мультиагентный режим (волнами по 5)

Любая массовая работа агентами — сквозные правки, генерация контента, аудит,
миграции — идёт **волнами ровно по 5 агентов**, одна волна = **отдельный** вызов
Workflow, и **файлы правит хост, а не агенты** (`agent(schema=...)` →
детерминированный applier). Полные правила, разбор после волны и специфика
аудитов — каноничны в
[`docs/agent/multiagent.md`](docs/agent/multiagent.md) (этот блок — только
компактный триггер, детали не дублировать здесь).

> **Режим ultracode правило НЕ отменяет.** «Токены не ограничение» — про
> глубину, не про размер залпа: волны существуют ради надёжности. Прецеденты:
> 40 агентов залпом → упало 13 из 32; 29 залпом → упало 20 из 29 и 1.34M токенов
> ради 9 результатов; 24 залпом → остановлено вручную владельцем.

Перед запуском Workflow — чек-лист в конце
[`docs/agent/multiagent.md`](docs/agent/multiagent.md). Куда кладутся результаты
аудита — § Открытая работа (`docs/audit/`).

---

## 📍 Рабочая ветка

```bash
# Ветвиться от свежего main, PR — обратно в main:
git checkout main && git pull
git checkout -b <type>/<short-slug>     # напр. docs/versioning, fix/executor-timeout
```

Постоянной «рабочей ветки» нет: каждая задача — своя ветка от `main`, затем
PR. Тип ветки/коммита — по Conventional Commits (см. § Формат коммитов).

---

## 🗂️ Структура и архитектура

Канонические источники (здесь **не дублируются**, чтобы не расходиться):

- Дерево файлов — [`docs/dev/project-structure.md`](docs/dev/project-structure.md)
- Модули, слои, граф зависимостей (DAG), «что умеет каждый модуль» —
  [`docs/dev/architecture.md`](docs/dev/architecture.md)

Пакет живёт в `src/stepik_grader/` (src-layout). Точки входа —
`grader.py`/`cli.py`/`downloader.py`/`diagnostic_stepik.py` + `config.py`;
всё остальное внутреннее — в `src/stepik_grader/core/`. Запуск — только
`python -m stepik_grader.X` или `stepik-grader` после `pip install -e .`
(прямого `python grader.py` из корня нет).

---

## ⚙️ Команды

### Установка

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # runtime (requests/psutil/rich) + pytest/ruff/mypy
```

### Перед коммитом (зеркалит CI)

```bash
pytest tests/ -x -q --tb=short                          # тесты
ruff check .                                             # линтер
ruff format --check .                                    # форматтер (проверка)
mypy src/stepik_grader scripts                                  # типы (строгость в [tool.mypy])
pytest tests/ --cov=. --cov-report=term-missing -q      # покрытие (информационно)
```

### Запуск

```bash
python -m stepik_grader.grader              # интерактивное меню (пункты 0-8)
python -m stepik_grader.downloader          # скачать задачу по URL Stepik
python -m stepik_grader.diagnostic_stepik   # диагностика API и токена
stepik-grader                               # то же, если пакет установлен
```

---

## 🐍 Стиль кода (Python 3.12+)

```python
from __future__ import annotations   # ОБЯЗАТЕЛЬНО в начале каждого нового файла
```

- **Типизация — union-синтаксис:** `def f(x: int | None = None) -> list[str]`.
  Никаких `Optional`/`List`/`Dict`/`Union` из `typing`.
- **Dataclasses:** изменяемые defaults только через `field(default_factory=...)`.
- **Пути — только `pathlib`**, не `os.path`.
- **Путь-параметры/возвраты в публичных сигнатурах — `Path`, не `str`:**
  функция/метод, принимающий или возвращающий путь к файлу или
  директории, типизируется `pathlib.Path`/`Path | None` — без обёртки в
  `str(...)` на входе/выходе и без защитного `pathlib.Path(...)` внутри тела
  (вызывающая сторона обязана передавать реальный `Path`). Не путь по смыслу
  (URL, идентификатор, хеш, голое имя файла без директории вроде
  `is_solution_file(file_name: str)`, содержимое кода/текста) — остаётся
  `str`. Правило действует на весь пакет, включая `web/`-слой; исключение —
  сетевая граница (HTTP query/JSON тела, `web/server.py`), где путь неизбежно
  приходит как `str` и конвертируется в `Path` один раз в точке входа
  (`argparse`, `_confined_path()`).
- **Subprocess-интерпретатор — `sys.executable`**, не `"python3"`/`"python"`.
- **Docstrings** для всех публичных функций (краткий формат).
- **`__all__`** — в каждом новом модуле.
- **Вывод — через `_console`** (rich) с graceful fallback на `print()`, не
  голый `print()` в логике модулей.
- **Никаких голых `except:`** — ловить `Exception as e` и логировать/re-raise.

Этот список — канон код-стайла. [`CONTRIBUTING.md`](CONTRIBUTING.md) на него
ссылается и добавляет только то, чего здесь нет (Google-style docstrings,
порядок импортов и констант, границы `# type: ignore`).

---

## 🏗️ Архитектурные инварианты

1. **DAG без циклов** — новые импорты не создают циклических зависимостей.
2. **Leaf-модули** — `storage.py`, `normalizers.py`, `glossary.py`,
   `atomic_io.py`, `db.py` не импортируют ничего из проекта. Не добавлять в них
   project-импорты. `atomic_io.py` (атомарный JSON-писатель) и `db.py`
   (общий SQLite-коннектор `connect`/`user_version`/`apply_schema`) —
   общие top-level leaf'ы вне `core/` намеренно: подпакеты `glossary/`/`rules/` не
   тянут `core/`, поэтому общие инфра-хелперы — на верхнем уровне, чтобы ими
   пользовались и они, и `core/*`, не порождая ребра `glossary → core` (ADR-0011).
   Потребители: `core/user_settings.py` → `atomic_io`; `core/history.py` и
   `glossary/json_provider.py` (очередь пополнения на SQLite/WAL) → `db` (их
   единственное проектное ребро — на stdlib-leaf).
3. **Graceful fallback** — `rich` опционален; весь вывод через `_console`.
4. **Sandbox — только opt-in** — по умолчанию `LocalRunner`
   запускает код в subprocess **без** изоляции ФС/сети; OS-изоляция включается
   явным `--sandbox` (`core/sandbox/`, три backend'а) — и в CLI
   (`--mode 1/2/3/4`), и в web (`--serve --sandbox`: `SandboxRunner`
   ставится активным runner'ом до старта, поэтому grade/playground/microbench
   изолируются разом). Исключение — пошаговый трейс: под `--sandbox` он
   недоступен (`core/tracer.py`). Недоступный backend — `parser.error`, а не
   молчаливый откат на `LocalRunner`. Дефолт «нет изоляции» документировать
   везде, где релевантно.
5. **Обратная совместимость** — все имена из `__all__` остаются доступными
   через `from stepik_grader.grader import X`.
6. **Истина глоссария** — полнота глоссария меряется относительно
   **официального Python/stdlib**, а не стороннего справочника. Внутренняя база
   Stepik-Python-Grader — источник истины контента; внешний
   [Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) — только цель
   экспорта/витрина, **никогда** не эталон полноты. Канон —
   [docs/glossary.md § Источники истины](docs/dev/glossary.md#источники-истины-роли).
   Односторонность касается и ссылок: **не ссылаться на внешнюю
   витрину** ни из данных карточек, ни из кода, ни из UI — ссылка из оригинала
   в его копию уводит на устаревший контент. Адрес карточки — её `id` как якорь
   своего раздела (`#/glossary/<id>`); наружу ведёт только `docs_url` на
   официальный `docs.python.org`.

---

## 📐 Форматы тест-кейсов (кратко)

Три автодетектируемых формата: `1`—Legacy (`N`, `N.clue`), `2`—Named
(`input_N.txt`, `expected_N.txt`), `3`—python-generation (`input.txt` +
`output.txt` с `# TEST_N:`). Канонический справочник —
[`docs/use/configuration.md`](docs/use/configuration.md#формат-тест-кейсов).

---

## 🔑 Формат коммитов

Conventional Commits — обязательно:

```
fix(executor): use sys.executable instead of platform string
feat(config): add GraderConfig dataclass with pyproject.toml support
refactor(grader): extract reporter.py with rich output logic
test(config): add tests for GraderConfig defaults
docs(claude): trim CLAUDE.md to agent contract
chore(deps): bump psutil upper bound
```

---

## 🏷️ Метки при заведении issue

Новый issue — **всегда** с метками навигации (полный список — `gh label list`):

- **`area/*`** — область: `area/cli|web|glossary|docs|sandbox|core|rules`.
- **`difficulty/*`** — сложность: `difficulty/easy|medium|hard`.
- **`good first issue`** / **`help wanted`** — только для изолированных задач с
  низким барьером входа: они питают онрамп «Первый вклад за 15 минут»
  ([CONTRIBUTING.md](CONTRIBUTING.md)), поэтому не вешать их на то, что реально
  не посильно новичку.

Плюс тип по смыслу (`enhancement`/`bug`/`tech-debt`/`security`/`documentation`).
Канон и расшифровка меток — [CONTRIBUTING.md § Метки при заведении issue](CONTRIBUTING.md).

---

## 🔢 Версионирование (кратко)

**Не SemVer.** Собственная схема (тег = MINOR+1, PATCH = число коммитов после
тега, все теги = `vX.Y.0`). Версия **динамическая, из git-тегов**
(`setuptools-scm`): на теге → `X.Y.0`, вне тега → `X.Y.0.postN+g<hash>`.
Статической `version` в `pyproject.toml` нет — вручную не править.

Ручную сверку версий делать не нужно: за дрейф отвечает CI
(`scripts/check_version_consistency.py`). Полная политика — в
[`docs/dev/versioning.md`](docs/dev/versioning.md).
UX-полировка вывода `--version` (dev vs release маркер) реализована
(реализовано; ср. § Открытая работа ниже); архивная постановка —
[`docs/agent/claude-handoff.md`](docs/agent/claude-handoff.md).

`scripts/version.py`'s "логическая" `X.Y.Z` (README `Version`-бейдж) считает
PATCH через `git rev-list --invert-grep`, исключая автокоммиты CI
`chore(ci): update badges [skip ci]` — иначе счётчик рос бы вдвое быстрее
реальных изменений. `setuptools-scm`-версия пакета (`X.Y.0.postN`)
это не затрагивает — у неё независимая логика без фильтрации по commit message.

---

## 📝 Обновление CHANGELOG.md / docs/archive/history.md — когда

- **`CHANGELOG.md`** (английский) — запись под `## [Unreleased]` в **каждом**
  смерженном PR, без исключений для "внутренних"/рефакторинговых PR
  (используйте `### Refactored`/`### Changed`/`### Internal` — прецеденты уже
  есть в файле). При релизе `[Unreleased]` переименовывается в
  `[X.Y.0] - ДАТА`, наверх добавляется новый пустой `[Unreleased]`.
  - **Краткость:** одна строка на изменение —
    `- <что изменилось> (#PR)`; детали/обоснование живут в PR/issue, не в
    changelog. Группировка `### Added`/`### Changed`/`### Fixed`/`### Internal`
    сохраняется. Многострочные абзацы-пересказы PR — антипаттерн (именно они
    раздули `[Unreleased]` перед v1.8.0). Действует с 1.9.0; ранее
    смерженные простыни (1.7.0/1.8.0) задним числом не переписываем.
  - **Ротация:** в `CHANGELOG.md` живут только `[Unreleased]` +
    **три последних MINOR**; при релизе самую старую версию переносим дословно в
    [`docs/archive/changelog-archive.md`](docs/archive/changelog-archive.md). CI-guard
    `scripts/check_docs_guardrails.py` не даёт числу версионных заголовков
    в `CHANGELOG.md` превысить 3.
- **`docs/archive/history.md`** (русский) — архивная запись на **каждый релиз**
  (новый git-тег `vX.Y.0`), не на каждый PR: сводка вошедшего в релиз, в
  стиле уже существующих записей (`**#NNN (дата):** ...`).
Не откладывать `CHANGELOG.md` "до конца фичи/спринта" — если PR смержен,
запись нужна сразу этим же PR, а не пост-фактум пачкой.


---

## 📚 Источники истины (не дублировать)

`docs/` разложена по четырём направлениям — **сначала выбери направление, потом
файл**: [`docs/use/`](docs/use/README.md) (как пользоваться) ·
[`docs/dev/`](docs/dev/README.md) (как устроено, включая `design/` —
спроектированное без кода) · [`docs/agent/`](docs/agent/README.md) (служебное для
Claude) · [`docs/archive/`](docs/archive/README.md) (всё историческое).
Новый документ создаётся внутри направления, а не в корне `docs/`.

| Тема | Канонический документ |
|---|---|
| Установка, OAuth, диагностика | [docs/use/installation.md](docs/use/installation.md) |
| Режимы, CLI-флаги, web/IDE, скачивание | [docs/use/grader-workflow.md](docs/use/grader-workflow.md) |
| Конфигурация, форматы тестов, безопасность | [docs/use/configuration.md](docs/use/configuration.md) |
| Архитектура (DAG, слои) | [docs/dev/architecture.md](docs/dev/architecture.md) |
| Контракт результата проверки (CLI/Web/API) | [docs/dev/result-contract.md](docs/dev/result-contract.md) |
| Дизайн server mode (Runner, API, sandbox) | [docs/dev/design/server-mode.md](docs/dev/design/server-mode.md) |
| Диагностика/логирование, редакция секретов | [docs/dev/logging.md](docs/dev/logging.md) |
| Цепочка поставок: инвентарь рантайма/ассетов, pip-audit | [docs/dev/supply-chain.md](docs/dev/supply-chain.md) |
| Архитектурные решения (ADR) | [docs/dev/adr/README.md](docs/dev/adr/README.md) |
| Дерево файлов | [docs/dev/project-structure.md](docs/dev/project-structure.md) |
| Версии, отличия от оригинала | [docs/use/versions.md](docs/use/versions.md) |
| Политика версионирования, код-стайл, workflow | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Кодекс поведения | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| Режим ответов: полный шаблон 13 ролей | [docs/agent/roles.md](docs/agent/roles.md) |
| Веб: разделы интерфейса / контракты веб-слоя / HTTP API / замыслы | [docs/use/web-interface.md](docs/use/web-interface.md), [docs/dev/web-contracts.md](docs/dev/web-contracts.md), [docs/dev/api.md](docs/dev/api.md), [docs/dev/design/web-design.md](docs/dev/design/web-design.md) |
| Очередь работ после крупного аудита (пустая — норма) | [docs/agent/claude-handoff.md](docs/agent/claude-handoff.md) |
| Всё историческое: история спринтов/релизов, архив CHANGELOG, разовые аудиты, отработанные постановки | [docs/archive/README.md](docs/archive/README.md) |
| Полный аудит v1.9.0 (архив) | [docs/archive/audit-2026-07-20.md](docs/archive/audit-2026-07-20.md) |
| Полный changelog (живой источник) | [CHANGELOG.md](CHANGELOG.md) |

---

## 🎯 Открытая работа: куда обращаться и как вести

**Порядок обращения — сверху вниз, первый непустой источник и есть план:**

1. **`gh issue list`** — единственный источник статусов. Никакой список issue
   не дублируется в файлы репозитория: он устаревает за спринт и начинает
   врать (прежняя редакция этой секции годами числила «открытыми» закрытые
   #97/#151, а handoff — семь закрытых issue).
2. **[`docs/audit/`](docs/audit/README.md)** — находки незакрытых аудитов, если
   папка непуста: что конкретно не так, с привязкой к `file:line`.
3. **[`docs/agent/claude-handoff.md`](docs/agent/claude-handoff.md)** — очередь работ, если
   она непуста: порядок и рёбра для связанного пласта задач после крупного
   аудита. Пустая очередь — нормальное состояние, тогда работаем по issue.
4. **[`CHANGELOG.md`](CHANGELOG.md)** — «что уже сделано», чтобы не
   переизобретать. Детальная история — [`docs/archive/`](docs/archive/README.md).

**Аудит и очередь — разные вещи, не путать.** Аудит даёт **находки** (что не так,
где именно) — они живут в `docs/audit/`. Очередь задаёт **порядок** разбора (что
за чем и почему) — она в `docs/agent/claude-handoff.md`. Один крупный аудит
обычно рождает и то, и другое.

**Жизненный цикл аудита:**

- **Начали аудит** — новый файл `docs/audit/ГГГГ-ММ-ДД-<тема>.md`: находки с
  `file:line` и явным состоянием каждой (открыта · закрыта, с номером PR ·
  отклонена, с причиной). Отклонённая находка тоже фиксируется: молча удалённая
  вернётся следующим аудитом.
- **Отработали** — когда все находки закрыты или отклонены, файл **целиком**
  переезжает в [`docs/archive/`](docs/archive/README.md) и вносится в её индекс.
  Не «помечаем ✅ и оставляем лежать»: `docs/audit/` держит только живое.
- **Пустая `docs/audit/`** — нормальное состояние: незакрытых аудитов нет.

**Как вести очередь (`docs/agent/claude-handoff.md`):**

- **Писать** — только после крупного аудита и только связанным пластом: когда
  есть жёсткие блокеры, «делать вместе, иначе фикс недоказуем» или общий файл.
  Плоский список независимых задач живёт в issue. В записи — обоснование
  порядка, которого нет в issue (что сломается при другом порядке, где
  escape-hatch), а не пересказ тела issue. Рёбра: `→` жёсткий блокер,
  `⤳` мягкий порядок, `✓` предпосылка выполнена.
- **Чистить** — по завершении волны её запись **удаляется**, а не помечается
  «✅ выполнена». Ценен исторически — целиком в `docs/archive/` отдельным
  файлом, а в очереди остаётся явное «сейчас пусто» с датой.
- **Чего там не бывает:** отчётов «мы это сделали», списка открытых issue,
  критериев приёмки. Пометки о сделанном — ровно то, что раздуло прошлую
  редакцию до 336 строк мёртвого журнала.

**Что спроектировано, но НЕ построено** (читать как контракты, дизайн не
переоткрывать): server mode — удалённый сервер, контейнерный sandbox с
квотами и PostgreSQL-модель данных. Дизайн лежит в
[server-mode.md](docs/dev/design/server-mode.md),
[server-sandbox-design.md](docs/dev/design/server-sandbox-design.md),
[server-data-model.md](docs/dev/design/server-data-model.md) + ADR-0001/0008/0009. Живого
issue на билд нет — направление держит только roadmap **#59**. Локальный
`SandboxRunner` (`--sandbox`), Runner-слой и контракт результата, наоборот,
**реализованы** (`core/sandbox/`, `core/runner.py`, `core/result.py`) — их не
переписывать.

---

## ✅ Чеклист перед PR

```
[ ] Ветка создана от свежего main (не коммитить в main напрямую)
[ ] pytest tests/ -x -q --tb=short   → все зелёные
[ ] ruff check .                      → 0 ошибок
[ ] ruff format --check .             → 0 ошибок
[ ] mypy src/stepik_grader scripts            → 0 ошибок (строгость в [tool.mypy])
[ ] Новые функции: type hints + docstring; новые модули: __all__
[ ] from __future__ import annotations в начале нового файла
[ ] Коммит в формате Conventional Commits
[ ] CHANGELOG.md: добавлена запись под ## [Unreleased] — в КАЖДОМ PR, без
    исключений для рефакторингов (см. § Обновление CHANGELOG.md/docs/archive/history.md)
[ ] docs/archive/history.md — НЕ на каждый PR, только на релиз (см. ту же секцию)
[ ] Версия не правится вручную — CI (check_version_consistency.py) сам следит
    за дрейфом; достаточно, чтобы верхняя запись CHANGELOG совпадала
    с последним git-тегом
```

---

## 📎 Связанный проект

**Glossary-Python** (`https://github.com/ArtVsMark/Glossary-Python`) —
статический HTML-глоссарий Python-терминов. Грейдер ссылается на него при RE
через `core/glossary.py`. НЕ трогать этот проект отсюда —
изменения только через отдельную задачу в самом Glossary-Python.

---

## 📊 Метрики (ориентиры — живой источник в бейджах README)

| Метрика | Значение |
|---|---|
| Версия | 1.10.0 (stable) |
| Python | 3.12 / 3.13 (3.14 — экспериментальная, только ubuntu в CI) |
| Тестов | бейдж/прогон CI — **числом здесь не фиксируется** |
| Покрытие | бейджи README `Coverage (ubuntu)` / `Coverage (all OS)` |
| Зависимостей runtime | 3 (requests, psutil, rich) |
| Глоссарий | бейдж `Glossary` в README; 0 черновиков |

> **Числа тестов/покрытия/глоссария в доках не хардкодятся — только бейджи.**
> Любая вписанная руками цифра устаревает к следующему PR и начинает
> противоречить соседнему файлу (именно так разошлись «1700+/2100+»). Живой
> источник покрытия — два бейджа в README (single-OS `coverage.json` + cross-OS
> `coverage-combined.json`, обновляются CI каждый прогон); карточки глоссария
> считает `scripts/generate_glossary_badge.py`, сверить локально —
> `python -m stepik_grader.glossary.coverage`. Исключение одно:
> строка `| Версия | X.Y.Z |` — её проверяет
> `scripts/check_version_consistency.py` (мягкое предупреждение при расхождении
> с последним git-тегом; обновлять при релизе MINOR). Эволюция метрик по
> релизам — в [docs/archive/history.md](docs/archive/history.md).

> **Два числа покрытия.** С `--sandbox` `core/sandbox/`
> содержит три ОС-специфичных backend'а — на любой одной машине/CI-job'е два из
> трёх всегда 0%. Поэтому `pytest`/локальный чек-лист и один job CI-матрицы
> видят только per-OS цифру (~90%+, порог `fail_under = 85` в
> `pyproject.toml` — НЕ поднимать глобально, иначе любой контрибьютор на одной
> ОС будет ложно падать). README держит **два** бейджа: single-OS
> (`.github/badges/coverage.json`, как раньше) и cross-OS combined
> (`coverage-combined.json`, `coverage combine` по трём job'ам матрицы,
> отдельный job `coverage-combine` в `ci.yml`, порог 90) — оба и есть живой
> источник для строки «Покрытие» выше.
