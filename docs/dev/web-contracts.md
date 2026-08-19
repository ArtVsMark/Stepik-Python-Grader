# Веб-слой: контракты, сценарии, устройство UI

> Контракт между фронтендом и бэкендом `src/stepik_grader/web/`: формы данных,
> сквозные сценарии, из которых собраны e2e-тесты, и устройство самого UI. Нужен,
> чтобы фронтенд и бэкенд менялись независимо, а UI не связывался с внутренностями
> CLI.
>
> Что пользователь видит в интерфейсе — [use/web-interface.md](../use/web-interface.md).
> Справочник HTTP API (эндпоинты, параметры, лимиты, коды) — [api.md](api.md).
> Нереализованные и отклонённые замыслы — [design/web-design.md](design/web-design.md).
> Дизайн будущего сетевого multi-tenant режима — [design/server-mode.md](design/server-mode.md).

**Принцип неинвазивности.** Ядро (`core/grader_core.py`, `core/test_loader.py`,
…) остаётся библиотекой. Веб-слой вызывает те же публичные функции, что и CLI
(`run_tests`, `run_benchmark`, `apply_relative_ranking`, `lookup_from_error`) —
новой бизнес-логики грейдинга в web UI нет (см.
[architecture.md](architecture.md), ребро `web → core` ациклично). `/api/grade`
остаётся обратно совместимым.

## Оглавление

- [User journeys](#user-journeys)
- [Модель error cards (WA/RE/TLE)](#модель-error-cards-waretle)
- [Action cards](#action-cards)
- [Реестр команд](#реестр-команд)
- [Архитектура web UI](#архитектура-web-ui)
- [Контракты данных](#контракты-данных)
- [Безопасность и локальное исполнение](#безопасность-и-локальное-исполнение)

---

## User journeys

### J0. Скачать задачу и тесты из Stepik, затем проверить

1. Отдельный раздел sidebar **«Загрузчик задач»** → форма загрузки.
2. Ввести URL шага (`https://stepik.org/lesson/.../step/...`), выбрать
   корневую папку (по умолчанию `StepikTasks`), нажать ▶.
3. Downloader создаёт папку задачи (`task{N}_1.py`, `tests/`, `task.md`,
   `meta.json`) и извлекает тест-кейсы; UI показывает, сколько кейсов и из
   какого источника (ZIP / HTML-таблица / GitHub-ссылка / не найдено).
4. По кнопке **«Перейти к проверке»** (по клику, **не автоматически**) путь
   скачанной папки подставляется в поле пути раздела «Проверка решений» и
   происходит переход туда → пользователь нажимает ▶ (J1/J2).
5. Если нет авторизации — понятная ошибка «нужен OAuth» со ссылкой на
   [installation.md](../use/installation.md#работа-с-api-stepik-oauth) (первичный
   OAuth выполняется через CLI).

### J1. Запустить проверку и увидеть AC

1. Открыть `stepik-grader --serve`, раздел «Проверка решений».
2. В command bar — путь к файлу/папке (по умолчанию — папка запуска), режим
   «Корректность», Enter / кнопка ▶.
3. Result panel: строка(и) со статусом **OK**, passed `N/N`, время, память.
4. Клик по строке → detail panel с раскладкой тест-кейсов (все AC).

### J2. Увидеть WA и разобраться

1. J1, но одно решение — **FAIL**.
2. Клик по строке → detail panel показывает тест-кейсы; непрошедший помечен
   **WA**.
3. Под ним — сравнение `expected` / `actual` (diff), stdin кейса, коллапс
   сырого вывода (вкладка «Разбор»).
4. Action cards (режим 2): **Copy input**, **Copy output**. Повтор — главной
   кнопкой «▶ Запустить».

### J3. Увидеть RE и открыть карточку глоссария

1. Решение падает с исключением → вердикт **RE**.
2. error card показывает трейсбек (stderr, exit code) и — по
   `lookup_from_error()` — блок глоссария: тип исключения + однострочный hint.
3. Ссылка «открыть карточку в глоссарии →» внутри error card (и action card
   **Open glossary**) → раздел «Глоссарий», карточка
   (напр. `RecursionError → #recursionerror`), без ухода из оболочки.
4. Команда **Explain error** (action card) —
   разворачивает hint + типичные причины inline, не переключая раздел.

### J4. Увидеть TLE

1. Решение не укладывается в таймаут (`CONFIG.timeout_seconds`) → вердикт
   **TLE**.
2. error card: severity `warning`, поле `timeout_s`, частичный stdout,
   подсказка «превышен лимит времени; проверьте сложность алгоритма».
3. Повтор прогона — action card **Run again** в режиме 1; в режиме 2 —
   главной кнопкой «▶ Запустить».

### J5. Скопировать → исправить → повторить

1. В любой error card — **Copy input** (stdin кейса в буфер).
2. Пользователь правит решение в редакторе.
3. Возврат в оболочку → **Run again** (тот же путь/режим/кейс) —
   не перевбивая путь. Строка обновляется на месте.

### J6. Микро-бенчмарк (режим 4)

1. В command bar — сегмент **Микро-бенчмарк**, путь к папке с ≥1 решением,
   профиль calls-per-run (fast/normal/…), ▶.
2. Result panel: таблица микробенча — метрики в **µs**
   (`Min/Median/Mean/Max/Std dev`), `Relative`, `Verdict`
   (`SIMILAR`/`SLOWER`/`MUCH SLOWER`) и колонка **`Py-heap`** (не `Memory`).
3. Клик по строке → detail panel с разбором (design-only, см.
   [web-design.md](design/web-design.md#per-block-detail-разбор-микробенча)).

### J7. Пробел в глоссарии → очередь пополнения

1. Решение падает с RE/WA на конструкции, для которой **нет карточки** в
   локальной базе (напр. незнакомое исключение или функция).
2. `MissingConceptDetector` фиксирует пробел → карточка **`GlossaryMissingEntry`**
   со статусом `new` попадает в очередь пополнения (раздел «Глоссарий» →
   «Недостающее»/backlog).
3. error card показывает бейдж «нет карточки — добавлено в очередь» вместо
   мёртвой ссылки.
4. Позже карточка наполняется (`draft` → `ready`) и может быть
   экспортирована во внешний Glossary-Python (экспорт — design-only).

---

## Модель error cards (WA/RE/TLE)

Единая карточка ошибки для трёх вердиктов. Строится на бэкенде из результата
`run_single_test` (уже содержит `verdict`, `error`, `diff`, `time`) плюс
lookup по глоссарию (`web/viewmodels.py::_case_view`).

| Поле | Тип | WA | RE | TLE | Описание |
|---|---|:--:|:--:|:--:|---|
| `verdict` | `"WA"\|"RE"\|"TLE"` | ✓ | ✓ | ✓ | Вердикт кейса |
| `severity` | `"error"\|"warning"\|"info"` | error | error | warning | Для цвета/иконки |
| `case_n` | `int` | ✓ | ✓ | ✓ | Номер тест-кейса |
| `stdin` | `string` | ✓ | ✓ | ✓ | Вход кейса (для Copy input) |
| `expected` | `string` | ✓ | — | — | Ожидаемый вывод |
| `actual` | `string` | ✓ | ± | ± | Фактический stdout |
| `diff` | `string` | ✓ | — | — | Готовый diff (как в CLI verbose) |
| `stderr` | `string` | — | ✓ | ± | Трейсбек / диагностика |
| `exit_code` | `int \| null` | — | ✓ | ± | Код возврата процесса |
| `timeout_s` | `float \| null` | — | — | ✓ | Лимит, который был превышен |
| `time` | `float` | ✓ | ✓ | ✓ | Затраченное время (с) |
| `suggestions` | `string[]` | ± | ✓ | ✓ | Короткие подсказки |
| `glossary_ids` | `string[]` | — | ✓ | ± | Якоря карточек (`recursionerror`, …) |
| `failure_kind` | `string` | ✓ | ✓ | ✓ | Ключ ошибки — тот же, что в истории (`runtime-error:IndexError`) |
| `actions` | `CommandAction[]` | ✓ | ✓ | ✓ | Доступные action cards (см. ниже) |

- **Severity** управляет только представлением (цвет рамки/бейджа), не
  логикой. TLE — `warning` (это не крэш кода, а превышение лимита), WA/RE —
  `error`.
- **`glossary_ids`** заполняется из `lookup_from_error()` (RE — по типу
  исключения). Пусто → блок глоссария в карточке не рисуется.
- **`failure_kind`** — та же классификация исхода, под которой
  кейс лёг в историю (`core/insights.failure_kind`); есть только у непройденных
  кейсов. Нужен браузеру, чтобы сказать серверу, ИЗ ЧЕГО человек ушёл в
  глоссарий (`POST /api/glossary/hit`): без общего ключа переход не соединяется
  с карточкой «Подучить», хотя обе стороны говорят про одну и ту же ошибку.
- **`suggestions`** — курируемые односрочники (не AI): для TLE — про
  сложность; для RE — hint из `core/glossary.py`; для WA — «сравните
  хвостовые пробелы/перевод строки» при подозрении на форматирование.

Пример (RE):

```json
{
  "verdict": "RE",
  "severity": "error",
  "case_n": 3,
  "stdin": "1000000\n",
  "actual": "",
  "stderr": "Traceback (most recent call last):\n  ...\nRecursionError: maximum recursion depth exceeded",
  "exit_code": 1,
  "time": 0.42,
  "suggestions": ["Похоже на бесконечную/глубокую рекурсию — проверьте базовый случай."],
  "glossary_ids": ["recursionerror"],
  "actions": ["run_again", "copy_input", "explain_error", "open_glossary"]
}
```

---

## Action cards

Контекстные действия, привязанные к результату/кейсу. Карточка/кейс сам
объявляет доступные действия (`actions: []`), а UI рендерит только их.

| Действие | `id` | Контекст | Что делает |
|---|---|---|---|
| Run again | `run_again` | любой результат/кейс | Повторить прогон с тем же путём/режимом/кейсом |
| Copy input | `copy_input` | кейс с `stdin` | Копирует stdin кейса в буфер |
| Copy output | `copy_output` | кейс с `actual`/`expected` | Копирует фактический (или ожидаемый) вывод |
| Explain error | `explain_error` | WA/RE/TLE | Разворачивает `suggestions` + hint inline |
| Open glossary | `open_glossary` | есть `glossary_ids` | Deep-link в раздел «Глоссарий» на карточку |

Это MVP-набор — реализован целиком, чистый фронтенд поверх уже
возвращаемых данных. `create_test`/`compare_solutions` остаются design-only,
см. [web-design.md § Action cards, отложенные](design/web-design.md#action-cards-отложенные).

Каждое действие — **декларативное** (`CommandAction`, см. контракты), а не
захардкоженная кнопка. Одно и то же действие доступно и как action card в
detail panel, и как команда в palette.

---

## Реестр команд

**Реестр команд** (`src/stepik_grader/web/commands.py`) — источник для
[action cards](#action-cards) в разборе кейса.

> **Палитра команд (`Ctrl+K`) удалена.** Все её семь команд уже
> были доступны кнопками: `run_again` — главной «▶ Запустить», копирование
> входа/выхода, «Объяснить ошибку» и «Открыть глоссарий» — карточками
> действий, тема — тумблером в topbar, переключение раздела — sidebar.
> Уникальной возможности у палитры не было ни одной, а стоила она модалки без
> focus-trap, кнопки `⌘K` в topbar и отдельного слоя рендера. Паттерн окупается
> при десятках команд (VS Code), а не при семи, каждая из которых уже кнопка.
> **Сам реестр сохранён** — на нём держатся action cards.

**Command registry model:**

```jsonc
// CommandAction
{
  "id": "open_glossary",          // стабильный идентификатор
  "title": "Открыть глоссарий",   // подпись (i18n: ru/en, как cli._MESSAGES)
  "icon": "book",                  // имя иконки (опц.)
  "keywords": ["glossary","help"], // для substring-поиска
  "when": ["glossary_ids"],        // фикс. словарь тегов контекста (не строка-предикат)
  "shortcut": "g",                 // опц. хоткей внутри контекста
  "payload_schema": "GlossaryRef"  // тип аргумента (см. контракты)
}
```

- **Расширяемость.** Новая команда = новая запись в реестре + один обработчик;
  UI (action cards) не трогается.
- **i18n.** `title`/описания — по той же схеме, что `cli._MESSAGES` (ключ →
  `{ru, en}`), чтобы не расходиться с CLI.
- **Реализация.** Фиксированный набор
  команд, `when` — фиксированный словарь тегов контекста (не predicate-DSL,
  как в изначальном дизайне — сознательное упрощение при реализации). Внешние
  плагины команд остаются design-only, см.
  [web-design.md § Command palette, отложенное](design/web-design.md#command-palette-отложенное).

---

## Архитектура web UI

Эволюция без переписывания ядра. Три слоя, граница между ними — стабильный
API-контракт (см. [контракты](#контракты-данных) и [api.md](api.md)).

```
┌── Frontend (SPA/vanilla JS) ────────────────┐
│   разделы, split-pane, palette, cards       │
└───────────────▲─────────────────────────────┘
                │  HTTP JSON API — см. api.md
┌───────────────┴─────────────────────────────┐
│  Web API / Adapters (src/stepik_grader/web/) │
│   viewmodels.py, downloader_adapter.py,      │
│   glossary_adapter.py, commands.py, runs.py  │
└───────────────▲─────────────────────────────┘
                │  вызовы публичных функций (как CLI)
┌───────────────┴─────────────────────────────┐
│  Core (библиотека, НЕ меняется)              │
│   grader_core, test_loader, glossary, …      │
└──────────────────────────────────────────────┘
```

**Инварианты архитектуры:**

1. **Core остаётся библиотекой.** Web-слой — потребитель `run_tests`,
   `run_benchmark`, `lookup_from_error`, как и `cli.py`. Никакой web-специфики
   в `core/` (DAG без циклов сохраняется, `web → core` — существующее ребро,
   см. [architecture.md](architecture.md)).
2. **UI не связан с внутренностями CLI.** Фронтенд общается только с HTTP
   API, не импортирует `cli.py` и не парсит его вывод. Adapters-слой —
   единственное место, где core-`dict` превращается в
   `ResultViewModel`/`ErrorCard`. CLI и Web — два независимых адаптера над
   одним ядром.
3. **Контракт — граница.** Меняя core, обновляем мэппер в adapters, а не
   фронтенд.
4. **Прогрессивность.** Остаётся на stdlib `http.server` + vanilla JS —
   переход на выделенный бэкенд (FastAPI и т.п.) или полноценный SPA-фреймворк
   не требуется. Любая тяжёлая зависимость — только по явному решению (см.
   запрет в `CLAUDE.md` на новые зависимости без указания).

**Реализованные адаптеры:**

- **Downloader-адаптер** — `web/downloader_adapter.py`, тонкая
  web-обёртка над `downloader.py::process_step_url` +
  `core/oauth_flow.try_create_session_without_browser`. Отдаёт
  `DownloadedTask`. Ребро DAG: `web → downloader → core` (ациклично).
- **Glossary provider/store** — локальная база карточек
  (JSON→SQLite), расширяет `core/glossary.py`. Отдаёт `GlossaryCard`.
  Реализован в `stepik_grader.glossary` — см. [glossary.md](glossary.md).
- **`MissingConceptDetector`** — анализ решений/ошибок →
  `GlossaryMissingEntry` в очередь пополнения.
- **`runs.py`** — async job-модель для bench/microbench
  (`POST /api/v1/runs`), альтернатива синхронному `GET /api/grade` — см.
  [api.md](api.md).

Экспорт/bridge во внешний Glossary-Python остаётся design-only, см.
[web-design.md](design/web-design.md#экспортсинхронизация-глоссария-во-внешний-проект).

**Полный справочник эндпоинтов** (методы, параметры, лимиты, коды ответов,
curl-примеры) — [api.md](api.md), не здесь.

---

## Контракты данных

Псевдо-схемы (Markdown/JSONC). Имена — типы adapters-слоя.

### ResultViewModel

Возврат `GET /api/grade` (см. [api.md](api.md)). Строится
`grade_path()`/`grade_benchmark()`/`grade_microbench()`.

```jsonc
// ResultViewModel
{
  "kind": "file" | "dir" | "error",
  "mode": "tests" | "bench" | "microbench",
  "base": "путь-база для относительных имён",
  "rows": [ SolutionRow ],       // по одному решению
  "message": "текст ошибки"       // только при kind=error
}

// SolutionRow (mode=tests)
{
  "file": "task1.py",
  "status": "OK" | "FAIL" | "NO TESTS",
  "passed": 5, "total": 5,
  "total_time": 0.12, "avg_time": 0.02, "memory_mb": 8.3,
  "cases": [ CaseView ]          // раскрытие
}

// CaseView → расширяется до ErrorCard при непрохождении
{
  "n": 3,
  "verdict": "AC" | "WA" | "RE" | "TLE",
  "time": 0.42,
  "error": "…",                  // stderr, если есть
  "diff": "…",                   // при WA
  "glossary": { "exception": "…", "hint": "…", "anchor": "…" }
}
```

### ErrorCard

Надстройка над `CaseView` для WA/RE/TLE — поля из
[таблицы выше](#модель-error-cards-waretle). Строится adapters-мэппером из
`run_single_test` + `lookup_from_error`.

### CommandAction

Единая запись реестра команд (питает action cards) — см.
[Реестр команд](#реестр-команд). Поля: `id`, `title` (i18n),
`icon?`, `keywords[]`, `when`, `shortcut?`, `payload_schema?`.

### DownloaderRequest / DownloadedTask / TestCaseSet

Контракты блока [Downloader](../use/web-interface.md#downloader--загрузка-тестов). Строятся
adapters-слоем над `downloader.py`.

```jsonc
// DownloaderRequest — аргумент POST /api/download
{
  "url": "https://stepik.org/lesson/569749/step/4?unit=564263",
  "root": "StepikTasks"           // корневая папка (опц., default из конфига)
}

// DownloadedTask — результат загрузки
{
  "ok": true,
  "path": "StepikTasks/курс/секция/урок/04-slug",  // куда положено (→ command bar)
  "files": ["task4_1.py", "task4_2.py", "task.md", "meta.json"],
  "tests": TestCaseSet,
  "message": "текст ошибки/предупреждения"  // при ok=false или ⚠️ (нет тестов)
}

// TestCaseSet — извлечённые тест-кейсы
{
  "count": 5,
  "source": "zip" | "html_table" | "github_link" | "none",  // 4 приоритета downloader.py
  "format": "legacy" | "named" | "python_generation"        // автодетект формата
}
```

### GlossaryMissingEntry

Элемент очереди пополнения (см.
[use/web-interface.md § Глоссарий](../use/web-interface.md#глоссарий-как-локальный-knowledge-модуль)). Пишется
`MissingConceptDetector` при обнаружении конструкции без карточки.

```jsonc
// GlossaryMissingEntry
{
  "concept": "functools.reduce",   // недостающая функция/конструкция/исключение
  "kind": "function" | "exception" | "construct",
  "status": "new" | "draft",       // жизненный цикл до появления GlossaryCard
  "seen_in": ["task3_1.py"],       // где встретилось (решение/ошибка)
  "verdict": "RE" | "WA" | null,   // вердикт, если пробел найден из ошибки
  "first_seen": "2026-07-06"
}
```

### GlossaryCard / GlossaryRef

Карточка **локальной базы** раздела «Глоссарий» (см.
[use/web-interface.md § Глоссарий](../use/web-interface.md#глоссарий-как-локальный-knowledge-модуль)). Компактный
источник — `core/glossary.py` (`GlossaryEntry`) для встроенных исключений;
расширенный контент и хранение — через `GlossaryProvider`/store в
локальной JSON/SQLite-базе проекта. Обратной ссылки во внешний
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) у карточки
нет: экспорт туда односторонний, витрина — копия, а **истина хранится
локально**. Адрес карточки — её `id` как якорь своего раздела
(`#/glossary/<id>`).

```jsonc
// GlossaryRef — аргумент команды open_glossary
{ "id": "recursionerror" }       // = GlossaryEntry.anchor

// GlossaryCard
{
  "id": "recursionerror",        // якорь (slug), = GlossaryEntry.anchor
  "title": "RecursionError",
  "kind": "exception" | "function" | "construct" | "term",
  "hint": "однострочное пояснение (RU)",   // из core/glossary.py
  "body": "расширенное описание",           // из локального store, опц.
  "status": "draft" | "ready" | "exported", // жизненный цикл карточки
  "docs_url": "https://docs.python.org/3/library/exceptions.html#RecursionError",
  "section": "Исключения",
  "related": ["stackoverflow", "maximum-recursion-depth"]
}
```

> `id` уже выводим из `core/glossary.py` (`GlossaryEntry.anchor`).
> `kind`/`body`/`section`/`related`/`status` — расширение локального store.
> `status` связывает карточку с очередью пополнения
> (`GlossaryMissingEntry`) и экспортом. Единственная внешняя ссылка карточки —
> `docs_url` на официальный `docs.python.org`.

---

## Безопасность и локальное исполнение

- **Только localhost.** Сервер слушает `127.0.0.1` и в сеть не торчит.
- **OS-sandbox для веб-запуска — opt-in.** По умолчанию решения исполняются в
  subprocess **без** изоляции ФС/сети — тот же threat model, что у CLI без
  `--sandbox`. С `--serve --sandbox` `SandboxRunner`
  ставится активным runner'ом до старта, и grade/playground/microbench
  изолируются разом; пошаговый трейс под `--sandbox` недоступен. Есть таймаут
  (всегда) и best-effort лимит памяти на POSIX. Без `--sandbox` запускай только
  доверенные решения (свои / скачанные из Stepik as-is).
- **Видимость режима исполнения.** В шапке — бейдж статуса
  OS-изоляции («⚠ Без OS-изоляции» при дефолтном `LocalRunner`, «🔒 OS-изоляция»
  под `--sandbox`); при первом запуске с включённой историей — однократное
  уведомление о локальном сборе аналитики (хранится sha256 решения, не исходный
  код; отключается перезапуском с `--no-history`). Флаги режима приходят с
  сервера в `data-`атрибутах `<body>`, текущий статус истории виден в
  «Настройках».
- **AI-подсказка + согласие.** На упавшем кейсе грейда и
  ошибке исполнения в песочнице — кнопка «Объяснить (AI)» (`POST /api/v1/hint`,
  async-job поверх `runs.py`, контекст через общий `build_failure_context`).
  Приватность: подсказка отправляет код решения и его ввод-вывод AI-провайдеру,
  поэтому первый запрос гейтится обязательной однократной модалкой согласия (без
  неё — **403** `consent_required`, наружу ничего не уходит); согласие помнится
  (server-side `.grader_settings.json` `ai_hint_consent` + клиентский localStorage).
  Провайдер не настроен → подсказка деградирует до сообщения, грейд не затронут.
- **XSS в выводе.** Веб-оболочка показывает stdout/stderr решения — весь
  такой контент экранируется при рендере (`esc` на клиенте, `html.escape` на
  сервере) — правило действует и на новые поля error card (`stdin`,
  `actual`, `stderr`, `diff`).
- **Заголовки и анти-SSRF.** Все ответы несут `nosniff`; HTML
  — строгий CSP (`default-src 'self'`, `script` строго self-hosted); соединение
  получает 30-секундный read-timeout; внешние загрузки ревалидируют каждый
  редирект-hop против allowlist. Принятые остаточные риски Sec-Fetch/CSRF —
  [SECURITY.md § Веб-оболочка](../../SECURITY.md).
- Подробная threat model и настройки лимитов —
  [configuration.md § Ограничения и безопасность](../use/configuration.md#ограничения-и-безопасность),
  [api.md § Общие правила](api.md#общие-правила-для-всех-api).
  Полноценный multi-tenant sandbox — вне рамок этого документа, см.
  [server-mode.md](design/server-mode.md) и запрет в
  [`CLAUDE.md`](../../CLAUDE.md): «НЕ запускать untrusted-код через LocalRunner».
