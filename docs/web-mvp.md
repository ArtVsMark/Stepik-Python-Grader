# WEB MVP — Проверка решений + Глоссарий

> Продуктово-архитектурная спецификация локальной веб-оболочки (эпик #123,
> PR-6). Дизайн-документ, **не реализация**: описывает целевой UX, модель
> данных и границы будущей архитектуры, не меняя текущий Python-код.
> Реализационные задачи — #125 (workspace проверки решений), #126
> (`JsonGlossaryProvider`), #129 (тесты web MVP) — остаются открытыми.
>
> Текущий веб-интерфейс (`--serve`) описан в
> [grader-workflow.md § Веб-интерфейс](grader-workflow.md#веб-интерфейс---serve);
> его реализация — `src/stepik_grader/web.py`. Этот документ показывает, куда
> он эволюционирует, и фиксирует контракт, чтобы будущий фронтенд/бэкенд
> строились без связывания UI с внутренностями CLI.

## Оглавление

- [Цель WEB MVP](#цель-web-mvp)
- [Два раздела: Проверка решений и Глоссарий](#два-раздела-проверка-решений-и-глоссарий)
- [Навигация и UX-схема](#навигация-и-ux-схема)
- [Layout: split-pane workspace](#layout-split-pane-workspace)
- [User journeys](#user-journeys)
- [Модель error cards (WA/RE/TLE)](#модель-error-cards-waretle)
- [Action cards](#action-cards)
- [Command palette (Ctrl+K)](#command-palette-ctrlk)
- [Scenario buttons](#scenario-buttons)
- [Архитектура будущего web UI](#архитектура-будущего-web-ui)
- [Контракты данных](#контракты-данных)
- [MVP vs v1 vs later](#mvp-vs-v1-vs-later)
- [Доступность, клавиатура, тёмная тема](#доступность-клавиатура-тёмная-тема)
- [Безопасность и локальное исполнение](#безопасность-и-локальное-исполнение)

---

## Цель WEB MVP

WEB MVP — это **локальная оболочка поверх существующего ядра и CLI**, а не
новый продукт. Цели:

1. **Снизить барьер входа.** Консольное меню (`stepik-grader`, режимы 0–4) —
   препятствие для новичков и тех, кто работает из IDE. Веб-оболочка даёт то
   же самое в браузере: указал путь → увидел вердикты AC/WA/TLE/RE, diff,
   бенчмарк.
2. **Связать проверку с обучением.** Когда решение падает, пользователь не
   уходит гуглить — рядом открывается карточка глоссария по типу ошибки
   ([Glossary-Python](https://github.com/ArtVsMark/Glossary-Python), issue
   #72 / эпик #96).
3. **Не ломать `--serve`.** Текущий `web.py` (stdlib `http.server`, один
   inline-HTML, две таблицы) продолжает работать как есть. MVP описывает
   эволюцию, а не замену: любой шаг обязан сохранять обратную совместимость
   команды `stepik-grader --serve` и endpoint `/api/grade`.

**Принцип неинвазивности.** Ядро (`core/grader_core.py`,
`core/test_loader.py`, …) остаётся библиотекой. Веб-слой вызывает те же
публичные функции, что и CLI (`run_tests`, `run_benchmark`,
`apply_relative_ranking`, `lookup_from_error`) — новой бизнес-логики грейдинга
в web UI не появляется. Это уже соблюдается в `web.py`
(см. [architecture.md](architecture.md), ребро `web → core` ациклично).

---

## Два раздела: Проверка решений и Глоссарий

Оболочка делится на два верхнеуровневых раздела, переключаемых в topbar:

| Раздел | Что делает | Источник данных |
|---|---|---|
| **Проверка решений** | Прогон файла/папки на корректность (режим 1/2) и бенчмарк (режим 3); таблица вердиктов, diff при WA, error cards при RE/TLE | `grade_path()` / `grade_benchmark()` → `run_tests`/`run_benchmark` |
| **Глоссарий** | Справочник Python-исключений и терминов: поиск, карточка с пояснением и ссылкой на полный глоссарий | `GlossaryProvider` (см. #126, `JsonGlossaryProvider`) поверх `core/glossary.py` |

Разделы связаны: из error card в «Проверке решений» есть переход **Open
glossary** прямо на нужную карточку раздела «Глоссарий» (deep-link по
`glossary_id`). Обратной жёсткой связи нет — «Глоссарий» самодостаточен и
может читаться отдельно.

---

## Навигация и UX-схема

```
┌──────────────────────────────────────────────────────────────────────┐
│ TOPBAR                                                                  │
│  🐍 Stepik Grader   [ Проверка решений ] [ Глоссарий ]      🌙  ⌘K     │
├───────────────┬────────────────────────────────────────────────────────┤
│  SIDEBAR      │  MAIN (зависит от активного раздела)                    │
│               │                                                          │
│  Проверка:    │   — см. § Layout (split-pane) для «Проверки решений»    │
│   • История   │   — для «Глоссария»: список карточек + панель карточки  │
│   • Пути      │                                                          │
│  Глоссарий:   │                                                          │
│   • Разделы   │                                                          │
│   • Избранное │                                                          │
└───────────────┴────────────────────────────────────────────────────────┘
```

- **Topbar**: логотип, переключатель разделов (сегмент-контрол, как текущий
  переключатель Корректность/Бенчмарк), тумблер темы, кнопка Command palette
  (`⌘K` / `Ctrl+K`).
- **Sidebar**: контекст-зависима. В «Проверке» — история прогонов и
  недавние пути; в «Глоссарии» — разделы и избранное. Сворачивается
  (клавиатура-доступно) для узких экранов.
- **Deep-linking**: состояние раздела/пути/карточки отражается в URL-хэше
  (`#/check?path=…&mode=tests` / `#/glossary/recursionerror`), чтобы
  action card «Open glossary» и history работали как обычные ссылки.

Навигация — «раздел → контекст → деталь»: выбор раздела в topbar, выбор
объекта (путь / карточка) в sidebar или main, раскрытие детали
(тест-кейс / error card) inline.

---

## Layout: split-pane workspace

Раздел «Проверка решений» — IDE-подобное рабочее пространство с
разделяемыми панелями:

```
┌─────────────┬──────────────────────────────┬───────────────────────────┐
│  SIDEBAR    │  RESULT PANEL                 │  DETAIL / GLOSSARY PANEL  │
│             │                               │                           │
│  История    │  ┌─ command bar ───────────┐ │  Контекстная деталь:      │
│  прогонов   │  │ путь  [tests|bench] ▶   │ │   • error card (WA/RE/TLE)│
│             │  └─────────────────────────┘ │   • diff expected/actual  │
│  Недавние   │  Таблица решений:            │   • glossary card         │
│  пути       │   file  passed  verdict  t   │   • action cards          │
│             │   ▸ раскрытие тест-кейсов    │                           │
│             │  Итоги: OK / FAIL / bar      │  (пусто → подсказка)      │
└─────────────┴──────────────────────────────┴───────────────────────────┘
```

- **Result panel** (центр) — command bar (путь + режим + запуск) и таблица
  результатов; повторяет текущий `web.py`, но с раскрытием строки не inline,
  а в правую панель.
- **Detail panel** (справа) — контекстная: показывает разбор выбранного
  тест-кейса (diff, error card, action cards, связанная glossary card). Если
  ничего не выбрано — краткая подсказка/сценарные кнопки.
- **Разделители** перетаскиваются; ширины запоминаются (localStorage, как
  сейчас путь/режим). На узком экране (`max-width`) панели схлопываются в
  вертикальный стек (detail → под таблицей, как сегодня).

Раздел «Глоссарий» использует упрощённый двухпанельный вариант: список
карточек слева-центре, панель карточки справа.

---

## User journeys

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
3. Под ним — **error card** с `expected` / `actual` (diff), stdin кейса.
4. Action cards: **Copy input**, **Copy output**, **Run again**.

### J3. Увидеть RE и открыть карточку глоссария

1. Решение падает с исключением → вердикт **RE**.
2. error card показывает трейсбек (stderr, exit code) и — по
   `lookup_from_error()` — блок глоссария: тип исключения + однострочный hint.
3. Action card **Open glossary** → раздел «Глоссарий», карточка
   (напр. `RecursionError → #recursionerror`), без ухода из оболочки.
4. Action card **Explain error** — разворачивает hint + типичные причины
   inline, не переключая раздел.

### J4. Увидеть TLE

1. Решение не укладывается в таймаут (`CONFIG.timeout_seconds`) → вердикт
   **TLE**.
2. error card: severity `warning`, поле `timeout_s`, частичный stdout,
   подсказка «превышен лимит времени; проверьте сложность алгоритма».
3. Action card **Run again** (повторить), **Create test** (сузить кейс).

### J5. Скопировать → исправить → повторить

1. В любой error card — **Copy input** (stdin кейса в буфер).
2. Пользователь правит решение в редакторе.
3. Возврат в оболочку → **Run again** (тот же путь/режим/кейс) —
   не перевбивая путь. Строка обновляется на месте.

---

## Модель error cards (WA/RE/TLE)

Единая карточка ошибки для трёх вердиктов. Строится на бэкенде из результата
`run_single_test` (уже содержит `verdict`, `error`, `diff`, `time`) плюс
lookup по глоссарию. Текущий `web.py._case_view()` — минимальная её версия
(`verdict` + `error`/`diff` + `glossary`); MVP расширяет поля.

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
| `actions` | `CommandAction[]` | ✓ | ✓ | ✓ | Доступные action cards (см. ниже) |

- **Severity** управляет только представлением (цвет рамки/бейджа), не
  логикой. TLE — `warning` (это не крэш кода, а превышение лимита), WA/RE —
  `error`.
- **`glossary_ids`** заполняется из `lookup_from_error()` (RE — по типу
  исключения; TLE — опционально карточка «TimeoutError»/«производительность»).
  Пусто → блок глоссария в карточке не рисуется.
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
  "actions": ["run_again", "copy_input", "explain_error", "open_glossary", "create_test"]
}
```

---

## Action cards

Контекстные действия, привязанные к результату/кейсу. Вместо «россыпи
кнопок» карточка/кейс сам объявляет доступные действия (`actions: []`), а UI
рендерит только их (см. [Scenario buttons](#scenario-buttons)).

| Действие | `id` | Контекст | Что делает |
|---|---|---|---|
| Run again | `run_again` | любой результат/кейс | Повторить прогон с тем же путём/режимом/кейсом |
| Copy input | `copy_input` | кейс с `stdin` | Копирует stdin кейса в буфер |
| Copy output | `copy_output` | кейс с `actual`/`expected` | Копирует фактический (или ожидаемый) вывод |
| Explain error | `explain_error` | WA/RE/TLE | Разворачивает `suggestions` + hint inline |
| Open glossary | `open_glossary` | есть `glossary_ids` | Deep-link в раздел «Глоссарий» на карточку |
| Create test | `create_test` | любой кейс | Заготовка нового тест-кейса (`N`/`N.clue`) из stdin/expected — **дизайн; реализация в #125** |
| Compare solutions | `compare_solutions` | режим bench / ≥2 решения | Открыть сравнение (таблица bench уже ранжирует по медиане) |

- Каждое действие — **декларативное** (`CommandAction`, см. контракты), а не
  захардкоженная кнопка. Одно и то же действие доступно и как action card в
  detail panel, и как команда в palette.
- **MVP**: `copy_input`, `copy_output`, `open_glossary`, `run_again`,
  `explain_error` — чистый фронтенд поверх уже возвращаемых данных.
  `create_test`, `compare_solutions` — дизайн сейчас, реализация позже (#125).

---

## Command palette (Ctrl+K)

Единая точка запуска команд — как в VS Code. `Ctrl+K` (или `⌘K`) открывает
fuzzy-поиск по **реестру команд**.

```
┌── ⌘K ────────────────────────────────────┐
│ > run again                                │
├────────────────────────────────────────────┤
│ ▶ Повторить проверку              Run again │
│ ⧉ Скопировать вход              Copy input  │
│ 📖 Открыть глоссарий          Open glossary │
│ 🌙 Переключить тему             Toggle theme│
│ ↔ Переключить раздел            Проверка⇄Гл.│
└────────────────────────────────────────────┘
```

**Command registry model** — единый реестр, который питает и palette, и
action cards, и сценарные кнопки:

```jsonc
// CommandAction
{
  "id": "open_glossary",          // стабильный идентификатор
  "title": "Открыть глоссарий",   // подпись (i18n: ru/en, как cli._MESSAGES)
  "icon": "book",                  // имя иконки (опц.)
  "keywords": ["glossary","help"], // для fuzzy-поиска
  "when": "case.glossary_ids != []", // контекст-предикат доступности
  "shortcut": "g",                 // опц. хоткей внутри контекста
  "payload_schema": "GlossaryRef"  // тип аргумента (см. контракты)
}
```

- **Расширяемость.** Новая команда = новая запись в реестре + один обработчик;
  UI (palette/cards/buttons) не трогается. `when`-предикат решает, где команда
  видна — это же поле управляет и сценарными кнопками.
- **i18n.** `title`/описания — по той же схеме, что `cli._MESSAGES` (ключ →
  `{ru, en}`), чтобы не расходиться с CLI (Sprint E.1, issue #51 D-01).
- **MVP**: палитра с фиксированным набором (run/copy/glossary/theme/section).
  Внешние плагины команд — later.

---

## Scenario buttons

Проблема «button soup»: набор из десятка кнопок, большинство из которых
неприменимы к текущему состоянию, перегружает и путает. Решение —
**сценарные (контекст-зависимые) кнопки**: UI показывает только те действия,
чей `when`-предикат истинен для текущего объекта.

| Состояние | Показываемые кнопки |
|---|---|
| Пусто (ничего не проверено) | `Проверить`, `Открыть глоссарий` |
| Все AC | `Run again`, `Benchmark`, `Compare solutions` (если ≥2) |
| Есть WA | `Explain error`, `Copy input`, `Copy output`, `Run again` |
| Есть RE | `Explain error`, `Open glossary`, `Copy input`, `Run again` |
| Есть TLE | `Explain error`, `Run again`, `Create test` |
| Режим bench | `Compare solutions`, `Run again` |

- Кнопки — тот же `CommandAction`-реестр, отфильтрованный по `when`. Никакой
  отдельной логики «какую кнопку показать»: одна модель для palette, cards и
  buttons.
- Это устраняет и дублирование, и «мёртвые» кнопки: если действие
  недоступно — его просто нет, а не disabled-«призрак».

---

## Архитектура будущего web UI

Эволюция без переписывания ядра. Три слоя, граница между ними — стабильный
API-контракт (см. [контракты](#контракты-данных)).

```
Сегодня (web.py):
  ┌──────────────────────────────────────────┐
  │  http.server (_Handler)                    │
  │   GET / → inline HTML+CSS+JS               │
  │   GET /api/grade → grade_path/grade_bench  │
  │        └── core (run_tests, glossary, …)   │
  └──────────────────────────────────────────┘

Целевое (MVP → v1):
  ┌── Frontend (SPA или прогрессивный inline) ─┐
  │   разделы, split-pane, palette, cards       │
  └───────────────▲─────────────────────────────┘
                  │  HTTP JSON API (стабильный контракт)
  ┌───────────────┴─────────────────────────────┐
  │  Web API / Adapters (web.py → web/…)         │
  │   /api/grade, /api/glossary, /api/commands   │
  │   ViewModel-мэпперы: core dict → ResultVM    │
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
   API, не импортирует `cli.py` и не парсит его вывод. Adapters-слой
   (эволюция `web.py`) — единственное место, где core-`dict` превращается в
   `ResultViewModel`/`ErrorCard`. CLI и Web — два независимых адаптера над
   одним ядром.
3. **Контракт — граница.** Меняя core, обновляем мэппер в adapters, а не
   фронтенд. Endpoint `/api/grade` сохраняет обратную совместимость (текущий
   `web.py` уже его отдаёт).
4. **Прогрессивность.** MVP может остаться на stdlib `http.server` + inline —
   переход на выделенный бэкенд (FastAPI и т.п.) или SPA-фронтенд не
   требуется для дизайна и не должен ломать `--serve`. Любая тяжёлая
   зависимость — только по явному решению (см. запрет в `CLAUDE.md` на новые
   зависимости без указания).

**Новые endpoint'ы (дизайн, реализация — #125/#126):**

| Endpoint | Отдаёт | Слой |
|---|---|---|
| `GET /api/grade?path=&mode=` | `ResultViewModel` (уже есть в `web.py`) | adapters → grader_core |
| `GET /api/glossary?q=` | `GlossaryCard[]` (поиск) | adapters → GlossaryProvider (#126) |
| `GET /api/glossary/{id}` | `GlossaryCard` | adapters → GlossaryProvider (#126) |
| `GET /api/commands?context=` | `CommandAction[]` (реестр, отфильтр.) | adapters (реестр команд) |

---

## Контракты данных

Псевдо-схемы (Markdown/JSONC). Имена — целевые типы adapters-слоя; часть уже
существует как `dict` в `web.py` (помечено).

### ResultViewModel

Возврат `/api/grade`. Сегодня — `grade_path()`/`grade_benchmark()` dict.

```jsonc
// ResultViewModel
{
  "kind": "file" | "dir" | "error",
  "mode": "tests" | "bench",
  "base": "путь-база для относительных имён",
  "rows": [ SolutionRow ],       // по одному решению
  "message": "текст ошибки"       // только при kind=error
}

// SolutionRow (mode=tests) — есть в web.py сегодня
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
  "error": "…",                  // stderr, если есть (web.py)
  "diff": "…",                   // при WA (web.py)
  "glossary": { "exception": "…", "hint": "…", "url": "…" } // web.py, issue #72
}
```

### ErrorCard

Надстройка над `CaseView` для WA/RE/TLE — поля из
[таблицы выше](#модель-error-cards-waretle). Строится adapters-мэппером из
`run_single_test` + `lookup_from_error`.

### CommandAction

Единая запись реестра команд (палитра + action cards + сценарные кнопки) —
см. [Command palette](#command-palette-ctrlk). Поля: `id`, `title` (i18n),
`icon?`, `keywords[]`, `when`, `shortcut?`, `payload_schema?`.

### GlossaryCard / GlossaryRef

Карточка раздела «Глоссарий». Источник — `core/glossary.py` (`GlossaryEntry`)
для встроенных исключений; полный контент — через `GlossaryProvider`
(#126, `JsonGlossaryProvider`) поверх данных проекта
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python).

```jsonc
// GlossaryRef — аргумент команды open_glossary
{ "id": "recursionerror" }       // = GlossaryEntry.anchor

// GlossaryCard
{
  "id": "recursionerror",        // якорь (slug), = GlossaryEntry.anchor
  "title": "RecursionError",
  "hint": "однострочное пояснение (RU)",   // из core/glossary.py
  "body": "расширенное описание",           // из GlossaryProvider (#126), опц.
  "url": "https://artvsmark.github.io/Glossary-Python/#recursionerror",
  "section": "Исключения",
  "related": ["stackoverflow", "maximum-recursion-depth"]
}
```

> `id`/`url` уже выводимы из `core/glossary.py` (`GlossaryEntry.anchor` /
> `.url`, `GLOSSARY_BASE_URL`). `body`/`section`/`related` — расширение,
> которое отдаст `JsonGlossaryProvider` (#126); MVP может работать только на
> компактном наборе из `core/glossary.py` (офлайн, ~30 исключений).

---

## MVP vs v1 vs later

| Возможность | MVP (сейчас: дизайн) | v1 | Later |
|---|---|---|---|
| `--serve`, две таблицы (tests/bench) | ✅ есть (`web.py`) | | |
| Error card RE + glossary-ссылка | ✅ есть (issue #72) | | |
| Split-pane workspace | дизайн | реализация (#125) | |
| Error cards WA/TLE (расширенные поля) | дизайн (спека) | реализация (#125) | |
| Action cards (copy/run/explain/open) | дизайн | copy/run/explain/open (#125) | create_test, compare |
| Command palette (Ctrl+K) | дизайн | базовый реестр (#125) | плагины команд |
| Scenario buttons | дизайн | реализация (#125) | |
| Раздел «Глоссарий» (поиск/карточки) | дизайн | `JsonGlossaryProvider` (#126) | синхронизация с полным глоссарием |
| Тесты web MVP | — | реализация (#129) | |
| Выделенный бэкенд/SPA (FastAPI и т.п.) | не требуется | не требуется | по явному решению |

**Что остаётся реализационными issue после этого дизайн-PR:**

- **#125** — workspace проверки решений (split-pane, error cards WA/TLE,
  action cards, palette, scenario buttons).
- **#126** — `JsonGlossaryProvider` и раздел «Глоссарий».
- **#129** — тесты web MVP.

Этот документ закрывает дизайн-часть эпика #123 (issue #124/#127/#128); сам
эпик остаётся открытым до реализации #125/#126/#129.

---

## Доступность, клавиатура, тёмная тема

- **Тёмная тема.** Уже есть через `prefers-color-scheme` (CSS-переменные в
  `web.py`). MVP добавляет явный тумблер (topbar) с сохранением выбора
  (localStorage), с дефолтом «системная».
- **Клавиатура.**
  - `Ctrl+K` / `⌘K` — command palette.
  - `Enter` в поле пути — запуск (уже есть).
  - `Tab`-навигация по интерактивным элементам; видимый focus-ring.
  - Внутри palette — `↑`/`↓` выбор, `Enter` запуск, `Esc` закрыть.
  - Хоткеи действий из реестра (`CommandAction.shortcut`) активны только в
    их контексте (`when`).
- **Доступность (a11y).**
  - Семантическая разметка: таблицы результатов — настоящие `<table>` с
    `<th scope>`; вердикты дублируются текстом, не только цветом (цвет +
    подпись AC/WA/RE/TLE — уже так в `web.py`).
  - Панели/диалоги — с `role`/`aria-label`; палитра — `role="dialog"` с
    возвратом фокуса при закрытии.
  - Контраст бейджей вердиктов — не полагаться только на цвет (важно для
    дальтонизма): иконка/буквенный код рядом.

Это базовые требования; полный a11y-аудит — задача этапа реализации (#125).

---

## Безопасность и локальное исполнение

- **Только localhost.** Сервер слушает `127.0.0.1` и в сеть не торчит (уже
  так в `web.py`, `run_server`). MVP не вводит сетевого доступа.
- **Нет OS-sandbox.** Решения исполняются в subprocess **без** изоляции
  ФС/сети — тот же threat model, что у CLI. Есть таймаут (всегда) и
  best-effort лимит памяти на POSIX; полноценный sandbox **в этом документе
  не решается**. Запускай только доверенные решения (свои / скачанные из
  Stepik as-is).
- **XSS в выводе.** Веб-оболочка показывает stdout/stderr решения — весь
  такой контент обязан экранироваться при рендере (в текущем `web.py` — `esc`
  на клиенте и `html.escape` на сервере; сохранить это правило для новых
  полей error card: `stdin`, `actual`, `stderr`, `diff`).
- Подробная threat model и настройки лимитов —
  [configuration.md § Ограничения и безопасность](configuration.md#ограничения-и-безопасность).
  Sandbox как отдельная большая работа — вне рамок WEB MVP (см. запрет в
  [`CLAUDE.md`](../CLAUDE.md): «НЕ запускать executor.py с untrusted-кодом»).
</content>
</invoke>
