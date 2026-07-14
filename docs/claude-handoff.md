# Claude handoff — архив постановок для реализации

> **Статус (2026-07-14): архив.** Все постановки ниже **закрыты и реализованы**,
> открытых задач нет (это же фиксирует `CLAUDE.md` § Открытая работа). Документ
> сохранён как архив scope/non-goals реализованных фич; за актуальными открытыми
> задачами — `gh issue list`.
>
> **Что это.** Постановки задач (scope / non-goals / проверки) для будущих
> сессий Claude Code по нерешённым реализационным issue. Цель — чтобы агент мог
> начать работу без повторного разбора контекста.
>
> **Что это НЕ.** Это **не** канонический продуктовый спец. Каноничная
> спецификация WEB MVP — [`web-current.md`](web-current.md) (что реализовано)
> и [`web-design.md`](web-design.md) (замыслы); при расхождении они
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

> **#163 (`--version`: различать dev и release, эпик #161) — закрыт.**
> `cli._format_version_for_display()`/`cli._is_dev_build()` помечают off-tag
> сборки явным `(dev build, not a release)`; on-tag остаётся чистым `X.Y.0`.
> Политика — [CONTRIBUTING.md § Версионирование](../CONTRIBUTING.md#версионирование-issue-68).

---

## #125 — WEB workspace проверки решений (✅ реализован)

> **Статус: закрыт.** Split-pane workspace (sidebar/result/detail), расширенные
> ErrorCard-поля (WA/RE/TLE), 5 MVP action cards, command palette (Ctrl+K),
> сценарные кнопки и раздел «Глоссарий» (поиск/карточка/backlog очереди
> пополнения, J7) реализованы в `src/stepik_grader/web/` — пакет, эволюция
> бывшего одиночного `web.py` (`server.py`/`viewmodels.py`/
> `glossary_adapter.py`/`commands.py`/`static/{index.html,app.css,app.js}`).
> Публичный API (`grade_benchmark`/`grade_path`/`run_server`) не менялся;
> `/api/grade` расширен только аддитивно. **Не реализовывать заново.**

Что уже есть (не переделывать):
- `GET /api/grade` — `cases[]` несёт `case_n`/`severity`/`stdin`/`expected`/
  `actual`/`stderr`/`exit_code`/`timeout_s`/`suggestions`/`glossary_ids`/
  `actions` поверх старых `n`/`verdict`/`time`/`error`/`diff`/`glossary`.
- `GET /api/glossary`, `GET /api/glossary/<id>`, `GET /api/glossary/missing` —
  тонкие адаптеры (`web/glossary_adapter.py`) над `JsonGlossaryProvider` с
  fallback на компактный `core/glossary.py`, когда `GraderConfig.glossary_store`
  не настроен.
- `GET /api/commands` — реестр `web/commands.py` (7 MVP-команд, фиксированный
  словарь тегов `when` вместо predicate-DSL — сознательное упрощение).
- Фронтенд (`static/app.js`) — единая `contextTags()`/`visibleCommands()`
  фильтрация, питающая палитру/action cards/сценарные кнопки из одного места;
  `ACTION_HANDLERS` — единая точка диспетчеризации команд.

**Осознанно оставлено вне #125** (design-only / other issues, не путать с
недоделкой): `create_test`/`compare_solutions` action cards, URL-hash
deep-linking (`open_glossary` работает in-memory), экспорт в Glossary-Python
(#126-follow-up), Downloader-блок (#186), микро-бенчмарк в web (#187), полный
a11y-аудит, true fuzzy-поиск в палитре (substring вместо этого).

**Проверки/тесты.** `tests/test_web.py` (ErrorCard-поля, J7 wiring) +
`tests/test_web_glossary.py` (glossary endpoints, command registry) уже
покрывают журналы J1–J5/J7 из web-current.md на уровне HTTP/Python-функций;
фронтенд-логика (палитра/resize/scenario buttons) проверена вручную через
запущенный сервер (нет JS test runner в проекте — см. non-goals ниже).
Оставшиеся для #129 журналы — J0 (download) и J6 (микробенч): оба
разблокированы (#186/#187 закрыты), реализация — в #129.

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

> **#190** (валидация `kind`/`status` при загрузке карточек) и **#191**
> (снижение false-positive `_last_exception_name` — конвенция именования
> Error/Exception/Warning + allowlist non-suffix builtin-исключений) —
> **закрыты**; открытой доводки #126 сейчас нет.

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

## #129 — тесты web MVP (user journeys) — ✅ закрыт

> **Статус: закрыт.** J1-J5/J7, просмотр карточки глоссария и command
> registry уже были покрыты `tests/test_web.py`/`tests/test_web_glossary.py`/
> `tests/test_web_downloader.py`; J6 (микробенч) добавлен вместе с #187.
> Комментарий к issue после PR #185 явно требовал не закрывать #129 по
> одному только исходному чек-листу — новый `tests/test_web_journeys.py`
> закрывает разрыв между независимо протестированными адаптерами тремя
> сквозными цепочками: Downloader→grade (скачанный `download_task()`-путь
> реально грейдится `grade_path()`), error-card→glossary (`glossary_ids`
> RE-кейса резолвятся в реальную карточку через `glossary_adapter`/HTTP, а
> не в 404) и missing-queue→adapter (запись, поставленная в очередь
> `grade_path(missing_queue_path=...)`, видна через тот же
> `glossary_adapter.glossary_missing()`, что дёргает
> `GET /api/glossary/missing`, а не только через низкоуровневый
> `json_provider`). Command-palette keyboard flows (Ctrl+K/стрелки/Enter/
> Escape) проверены вручную через запущенный сервер — не JS-тестами (нет
> test runner, non-goal ниже), тот же компромисс, что и в #125.

**Scope (по критериям #129 и § User journeys в web-current.md).**
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

> **#195–#198 (Glossary coverage относительно официального Python/stdlib) —
> закрыты.** Цепочка `origin`-поля модели → `stdlib_inventory.py` →
> `coverage.py` (report + missing JSON) → CLI-точка входа
> (`python -m stepik_grader.glossary.coverage`) полностью реализована —
> детали в [glossary.md](glossary.md). Docs-follow-up **#199** (регистрация
> модулей покрытия в DAG/архитектуре) — **закрыт**: `stdlib_inventory.py`/
> `coverage.py` описаны в [architecture.md](architecture.md) и
> [project-structure.md](project-structure.md). Практический детектор: **#190**
> (валидация `kind`/`status`) и **#191** (снижение false-positive
> `_last_exception_name`) — оба **закрыты**.

---

## Контракты и server mode — дизайн готов, реализация открыта

Дизайн-часть закрыта документами; реализация — отдельные issue. **Читать доки
как источник контрактов, не переопределять их заново.**

- **#116 — контракт результата.** Поля case/solution/run result, семантика
  вердиктов, форма ошибки/таймаута, стабильность — [result-contract.md](result-contract.md).
  Типизированный `TestResult` (`core/result.py`, эпик #112/#113/#114) уже
  реализован и сохраняет имена полей контракта; `run_single_test()` и весь
  dict-контракт CLI/Web/API не менялись.
> **#140 — Runner-слой (эпик #136/#137/#138/#139) — закрыт.** `Runner`
> Protocol + `LocalRunner` реализованы в `core/runner.py`; `grader_core.
> run_single_test()` делегирует subprocess-запуск без изменения поведения —
> [server-mode.md § Runner-слой](server-mode.md#runner-слой-issue-140-реализация--136137138).
- **#156 — API удалённого исполнения.** Контракт `/api/v1/runs` (async,
  классы ошибок, версионирование) — [server-mode.md § Контракт API](server-mode.md#контракт-api-удалённого-исполнения-issue-156).
  **Сервер не реализуется** — только контракт.
- **#157 — sandbox/сеть-off/квоты.** Требования к `SandboxRunner` —
  [server-mode.md § Sandbox](server-mode.md#sandbox-и-сетевая-изоляция-issue-157).
- **#152 — ADR server mode.** Решение и альтернативы — [adr/0001-server-mode.md](adr/0001-server-mode.md).
- **#150 — диагностика/логирование (✅ реализовано, эпик #146 / #341).** Opt-in
  лог-файл с редакцией секретов — [logging.md](logging.md); реализация —
  `core/diag_log.py`, подключена в `stepik_client`/`oauth_flow`/`downloader`
  (дочерние #147–#149 закрыты).

## Порядок и зависимости

`#126` (glossary foundation) закрыт, включая доводку `#190`/`#191`; `#199`
(DAG-документация для glossary coverage) тоже закрыт. `#125` (workspace
проверки решений), `#186` (Downloader web), `#187` (микро-бенчмарк web) и
`#129` (тесты web MVP) — **все закрыты**, реализация в
`src/stepik_grader/web/` и `tests/test_web*.py`. **Эпик #123 закрыт**;
реализация зафиксирована документом [web-current.md](web-current.md).
