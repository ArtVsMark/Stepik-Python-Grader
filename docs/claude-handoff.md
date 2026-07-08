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

> **#163 (`--version`: различать dev и release, эпик #161) — закрыт.**
> `cli._format_version_for_display()`/`cli._is_dev_build()` помечают off-tag
> сборки явным `(dev build, not a release)`; on-tag остаётся чистым `X.Y.0`.
> Политика — [CONTRIBUTING.md § Версионирование](../CONTRIBUTING.md#версионирование-issue-68).

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
- **#150 — диагностика/логирование.** Opt-in лог-файл, редакция секретов —
  [logging.md](logging.md); реализация в `stepik_client`/`oauth_flow`/`downloader`.

## Порядок и зависимости

`#126` (glossary foundation) закрыт, включая доводку `#190`/`#191`; `#199`
(DAG-документация для glossary coverage) тоже закрыт. WEB-цепочка: `#125`
(workspace), `#186` (Downloader web), `#187`
(микро-бенчмарк web) можно вести параллельно, но `#129` (тесты) логично
завершать после появления реализуемых журналов из #125/#186/#187. Эпик #123
остаётся открытым до закрытия #125/#129 (+ #186/#187); дизайн-часть уже
закрыта документом [web-mvp.md](web-mvp.md).
