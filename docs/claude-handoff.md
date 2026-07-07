# Claude handoff — готовые задачи для будущей реализации

> **Что это.** Постановки задач (scope / non-goals / проверки) для будущих
> сессий Claude Code по нерешённым реализационным issue. Цель — чтобы агент мог
> начать работу без повторного разбора контекста.
>
> **Что это НЕ.** Это **не** канонический продуктовый спец. Каноничная
> спецификация WEB MVP — [`web-mvp.md`](web-mvp.md); при расхождении она
> главнее. Здесь — рабочие ориентиры «как подступиться», а критерии приёмки —
> в самих GitHub issue.
>
> **Общие правила для любой из задач ниже** (полностью — в
> [`../CLAUDE.md`](../CLAUDE.md) и [`../CONTRIBUTING.md`](../CONTRIBUTING.md)):
> ветвиться от свежего `main`, PR — в `main`; Python 3.12+ union-типы;
> `from __future__ import annotations`; `__all__` в новых модулях; не добавлять
> зависимости без явного указания; не ломать `--serve`, `/api/grade` и DAG без
> циклов; прогонять `pytest` + `ruff` + `mypy` перед PR.

---

## #163 — `--version`: различать dev и release (эпик #161)

**Проблема.** Сейчас `stepik-grader --version` печатает сырые метаданные
пакета (`importlib.metadata`). Вне тега `setuptools-scm` даёт
`X.Y.0.postN+g<hash>` — читаемость страдает, пользователю неочевидно, что это
не официальный релиз.

**Scope.**
- Форматировать вывод `--version` так, чтобы off-tag сборка явно помечалась
  local/dev-сегментом (`+g<hash>` или `.devN` — то, что уже даёт
  `setuptools-scm`), а on-tag — чистым `X.Y.0` без суффикса.
- Логика — в `cli.py` (где уже читается `__version__`); не менять способ
  вычисления версии (git-теги / `setuptools-scm`, issue #162 / PR #183).
- Опционально: добавить `__version__` в `src/stepik_grader/__init__.py` (сейчас
  живёт в `cli.py`) — если делать, сохранить единый источник, не плодить
  расходящиеся определения.
- Тест на обе ветки: on-tag (без суффикса) и off-tag (с dev/local-сегментом).

**Non-goals.**
- НЕ менять схему версионирования и `pyproject.toml`/`setuptools-scm`.
- НЕ трогать `scripts/version.py` и `scripts/check_version_consistency.py`.

**Проверки.** `stepik-grader --version` даёт ожидаемый формат в обоих случаях;
новый тест зелёный; `mypy`/`ruff` чистые. Приёмка — по критериям #163.

**Ссылки.** Политика версий — [CONTRIBUTING.md § Версионирование](../CONTRIBUTING.md#версионирование-issue-68).

---

## #125 — WEB workspace проверки решений

**Каноничный дизайн.** [`web-mvp.md`](web-mvp.md) — split-pane workspace, error
cards (WA/RE/TLE), action cards, command palette (Ctrl+K), scenario buttons.
Читать его как источник UX/контрактов; ниже — только границы.

**Scope (по критериям #125 и разделу «v1» таблицы MVP в web-mvp.md).**
- Раздел «Проверка решений»: указание пути к файлу/папке, выбор режима
  (корректность / бенчмарк / **микро-бенчмарк**), запуск и отображение
  результата.
- Расширить `web.py` (или выделить `web/`-подпакет как в § «Архитектура
  будущего web UI»): result panel + detail panel; error cards WA/TLE с полями
  из таблицы web-mvp.md § «Модель error cards».
- **Границы скоупа #125.** Микро-бенчмарк в web и Downloader-блок вынесены в
  отдельные open issues — **не тащить их в #125**:
  - **#187 — микро-бенчмарк (режим 4) в web.** Вывод существующего
    `run_microbench_mode` (`core/grader_core.py`; нижнеуровневый прогон —
    `core/microbench_runner.py::run_microbench`) как третьего сегмента: отдельный
    ViewModel с µs-метриками и колонкой **`Py-heap`** (не `Memory`) — см.
    web-mvp.md § «Режимы проверки и микро-бенчмарк». Сейчас в `web.py` его нет.
  - **#186 — Downloader-блок.** Web-адаптер над `downloader.py`
    (`parse_stepik_step_url`, `build_task_directory`, автоизвлечение тестов) +
    `core/oauth_flow.py`: панель «Загрузить из Stepik», endpoint
    `POST /api/download` → `DownloadedTask`, автоподстановка пути в command bar
    (журнал J0). В MVP первичный OAuth может оставаться за CLI, web показывает
    понятную ошибку.
- Action cards MVP-уровня (`copy_input`, `copy_output`, `open_glossary`,
  `run_again`, `explain_error`) — чистый фронтенд поверх уже возвращаемых
  данных.
- Веб-слой вызывает существующие публичные функции ядра (`run_tests`,
  `run_benchmark`, `run_microbench_mode`, `apply_relative_ranking`,
  `lookup_from_error`) — новой бизнес-логики грейдинга в web НЕ добавлять.

**Non-goals.**
- НЕ ломать `stepik-grader --serve` и endpoint `/api/grade` (обратная
  совместимость обязательна).
- НЕ переписывать ядро (`core/*` остаётся библиотекой; `web → core` — ацикличное
  ребро DAG).
- НЕ вводить тяжёлых зависимостей (FastAPI/SPA-фреймворк) — stdlib
  `http.server` + inline достаточно для MVP; любая новая зависимость — только
  по явному решению.
- XSS: весь stdout/stderr решения экранировать (`html.escape` на сервере, `esc`
  на клиенте) — правило распространить на новые поля error card (`stdin`,
  `actual`, `stderr`, `diff`).

**Проверки/тесты.** См. #129 (журналы J1/J2/J3/J4/J5 из web-mvp.md).

---

## #126 — глоссарий как локальный knowledge-модуль (✅ foundation реализован)

> **Статус: закрыт.** Foundation уже в репозитории — пакет
> `src/stepik_grader/glossary/` (`GlossaryCard`/`GlossaryMissingEntry`,
> `JsonGlossaryProvider`, `MissingConceptDetector`, очередь пополнения с
> дедупом). Формат хранения и Python-API — канонично в
> [glossary.md](glossary.md). **Не реализовывать заново.**

Что уже есть (не переделывать):
- `JsonGlossaryProvider.load()` — загрузка базы карточек (файл или директория),
  поиск по `id/title/aliases/keywords/tags`, фильтры по `status`/`tag`,
  `GlossaryError` на битой/отсутствующей базе.
- `MissingConceptDetector` — консервативный AST-детектор (без исполнения кода):
  stdlib-вызовы, notable builtins, `match/case`, исключения из трейсбеков.
- Очередь пополнения (`append_missing_entries` с дедупом по `concept`).

**Открытая доводка (follow-up quality tasks, не implementation-с-нуля):**
- **#190** — валидация `kind`/`status` при загрузке карточек.
- **#191** — снижение false-positive детектора.

**Остаётся за рамками #126 (в других issue):**
- WEB UI и endpoint'ы `/api/glossary*` — в #125/#129.
- Экспортёр `ready`-карточек во внешний Glossary-Python (`POST /api/glossary/export`) —
  отдельный #126-follow-up (см. [glossary.md § Границы](glossary.md#границы-что-не-входит)).
- SQLite-хранилище (#130+) — сейчас JSON-first, провайдер абстрагирует источник.

**Инварианты при доводке.** `core/glossary.py` остаётся leaf-модулем (без
project-импортов); пакет `glossary/` не тянет `core/*` и не импортируется из
него — DAG ацикличен. Внешний
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) не редактируется
из грейдера напрямую (см. [`../CLAUDE.md`](../CLAUDE.md) § «Связанный проект»).

---

## #129 — тесты web MVP (user journeys)

**Scope (по критериям #129 и § User journeys в web-mvp.md).**
- Покрыть основные сценарии веб-оболочки:
  - **Загрузка из Stepik** (J0): `POST /api/download` → `DownloadedTask`,
    автоподстановка пути (мокать сеть/OAuth; проверять извлечение тестов и
    структуру папки).
  - **Корректное решение** (J1): запуск → статус OK, passed N/N.
  - **Решение с ошибкой** (J2/J3/J4): WA (diff), RE (трейсбек + glossary-блок),
    по возможности TLE (превышение таймаута).
  - **Микро-бенчмарк** (J6): режим 4 → ViewModel с µs-метриками и колонкой
    `Py-heap`.
  - **Просмотр карточки глоссария** (раздел «Глоссарий» / deep-link
    `open_glossary`).
  - **Пробел в глоссарии** (J7): `MissingConceptDetector` → `GlossaryMissingEntry`
    в очереди (`GET /api/glossary/missing`).
- Тестировать через публичный слой (endpoint'ы `/api/grade`, `/api/download`,
  `/api/glossary`, `/api/glossary/missing`) и адаптеры, не привязываясь к
  деталям вёрстки.
- Разместить в `tests/` (`test_web*.py`); использовать существующие фикстуры и
  временные тест-директории.

**Non-goals.**
- НЕ дублировать тесты ядра (`run_tests`/`run_benchmark` уже покрыты) — здесь
  проверяется web-слой/адаптеры и сборка error/glossary cards.
- НЕ вводить браузерные E2E-зависимости (Selenium/Playwright) без явного
  решения — достаточно проверки HTTP-ответов и ViewModel-мэпперов.

**Проверки.** Новые тесты зелёные; покрытие web-слоя не деградирует; `pytest`/
`ruff`/`mypy` чистые.

---

## #195–#198 — Glossary coverage относительно официального Python/stdlib

> **Общий инвариант (см. [`../CLAUDE.md`](../CLAUDE.md) § Архитектурные
> инварианты, п. 6).** Полнота глоссария меряется относительно **официального
> Python/stdlib**. Внутренняя база Stepik-Python-Grader — источник истины
> контента; внешний Glossary-Python — только цель экспорта, **никогда** не
> эталон полноты. Роли и два источника пробелов (practice-driven /
> source-driven) — [glossary.md § Источники истины](glossary.md#источники-истины-роли).
>
> **Общие non-goals для всей цепочки #195–#198:**
> - **Без сетевого скана** — инвентарь Python/stdlib строится через локальный
>   `sys.stdlib_module_names` / `importlib` / интроспекцию, не через обращение к
>   docs.python.org или иным сайтам.
> - **Glossary-Python — не эталон.** Ни на одном шаге он не используется как
>   мера полноты и не редактируется из грейдера.
> - **Не менять семантику practice-детектора** (`MissingConceptDetector`) без
>   явного запроса — source-driven путь дополняет его, а не заменяет.

### #195 — origin-поля модели (карточка/пробел)

**Scope.** Ввести различение источника карточки/пробела: практика vs
покрытие stdlib. Добавить поле origin (напр. `origin: "practice" | "stdlib"`) к
`GlossaryMissingEntry` (и, при необходимости, к `GlossaryCard`) в пакете
`stepik_grader.glossary`. Дедуп `append_missing_entries()` учитывает origin.
Обновить [glossary.md](glossary.md) (таблицы полей) — контракт каноничен там.

**Non-goals.** Не менять существующие поля/поведение практического детектора;
не вводить БД (JSON-first, как сейчас).

### #196 — инвентаризатор stdlib

**Scope.** Модуль, который строит локальный инвентарь официального Python/stdlib
(модули, публичные функции/классы/исключения) через интроспекцию
(`sys.stdlib_module_names`, `importlib`, `inspect`) — **без сети**. Leaf-модуль:
не тянет `core/*`, DAG ацикличен. `__all__`, union-типы, `from __future__`.

**Non-goals.** Не сканировать сеть; не покрывать сторонние пакеты (только
stdlib+builtins); не исполнять сторонний код.

### #197 — генератор coverage-отчёта + missing JSON

**Scope.** Сопоставить инвентарь (#196) с локальной базой карточек
(`JsonGlossaryProvider.known_terms()`) и сгенерировать: (1) человекочитаемый
отчёт покрытия (сколько stdlib-сущностей описано), (2) JSON недостающих записей
(`GlossaryMissingEntry` с `origin: "stdlib"`) в очередь пополнения через
`append_missing_entries()`.

**Non-goals.** Не наполнять карточки автоматически (только регистрировать
пробелы); не трогать practice-ветку очереди.

### #198 — CLI/меню-точка входа

**Scope.** Точка запуска coverage-скана: подкоманда CLI и/или пункт
интерактивного меню (режимы `grader.py`), запускающая #196→#197. Вывод — через
`_console` (rich) с fallback. Уважать существующие флаги/структуру `cli.py`.

**Non-goals.** Не добавлять веб-endpoint (web-интеграция — отдельно, в русле
#125/#129); не вводить зависимости.

---

## Порядок и зависимости

`#163` (эпик #161) независима (версионирование). `#126` (glossary foundation)
уже закрыт — остаётся его доводка #190/#191. WEB-цепочка: `#125` (workspace),
`#186` (Downloader web), `#187` (микро-бенчмарк web) можно вести параллельно, но
`#129` (тесты) логично завершать после появления реализуемых журналов из
#125/#186/#187. Эпик #123 остаётся открытым до закрытия #125/#129 (+ #186/#187);
дизайн-часть уже закрыта документом [web-mvp.md](web-mvp.md).

Glossary coverage (source-driven) — линейная по данным цепочка
`#195 → #196 → #197 → #198`. `#195` (origin-поля модели) логично вести вместе
с доводкой `#190` (валидация `kind`/`status` при загрузке): обе трогают модель и
загрузку карточек. `#196` (инвентарь stdlib) и `#197` (отчёт + missing JSON) —
ядро цепочки; после них `#199` (регистрация модулей покрытия в DAG/архитектуре) —
docs/DAG-follow-up, не отдельная код-фича. `#198` (CLI/меню-точка входа)
завершает цепочку.
