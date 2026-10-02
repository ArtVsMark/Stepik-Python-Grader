# Дорожная карта: генеральный эпик, эпики и issue (2026-07-18)

> Основано на сводной оценке: проект инженерно зрел (1645 passed / 23 skipped, single-OS покрытие 89.21% при пороге 85 — перепроверено в этом снимке на Python 3.12.3; mypy/ruff чисто, DAG без циклов, периметр localhost-сервиса выстроен грамотно). Главная проблема — не инженерная, а разрыв «зрелость есть, а упаковки и вывода на витрину нет»: три стратегических актива (глоссарий 912 ready, retention-контур, AI-репетитор) уже готовы в коде, но не доведены до пользователя. Ряд тезисов прошлых аудитов устарел и подтверждён кодом как сделанный (#396, #395, #430) — они переклассифицированы из «реализовать» в «усилить видимостью».

---

## 🏛 Генеральный эпик — Stepik-Python-Grader: локальный максимум

**Заголовок:** вывести готовое на витрину, укрепить фундамент, растянуть воронку.

**Цель.** За один релизный цикл превратить инженерно зрелый, но недопродающий себя грейдер в продукт с видимой ценностью и живой воронкой сообщества: довести до пользователя уже написанные активы (глоссарий, дашборд прогресса, AI-репетитор, локализация), закрыть точечные дефекты потери данных и устойчивости, консолидировать персистентность и качество гейтов, и подготовить (но не строить) фундамент под server mode — не тратя усилия на уже закрытые прошлые P1.

**Зачем.** Разрыв между зрелостью и восприятием: три стратегических актива (912-карточный глоссарий, retention-контур `insights`/`progress_export`, AI-репетитор `ai_hints`) полностью существуют в коде, но не выведены в основной UX-канал (web) или занижены в витрине. Дешёвый surfacing готового даёт кратно больший продуктовый и growth-эффект на единицу кода, чем дорогие инфраструктурные инициативы, поэтому его нужно сделать первым. Параллельно накопились реальные дефекты (потеря решения студента при повторном скачивании, EOFError на пайпе, недостоверный замер памяти, масочные тесты) и структурные долги владельца (граница web↔core, SQL-унификация, полная i18n), которые дешевле закрыть до роста аудитории.

**Состав (эпики).**
- **E1** Глоссарий: витрина, контент-волны как good-first-issue, полнотекстовый поиск
- **E2** Retention: web-дашборд прогресса, достижения, активация истории, фикс task_key
- **E3** AI-репетитор: охват web/playground/режимы 3-4 + consent-gate + RAG-заземление
- **E4** Полная локализация UI (data-i18n, ui.json, t(), guardrail, e2e)
- **E5** Архитектурная граница web↔core, ADR-дисциплина, prerequisites server mode
- **E6** Унификация персистентности (atomic-writer, core/db.py, hash-дедуп, N+1, прунинг)
- **E7** Надёжность CLI/исполнения и корректность sandbox-измерений
- **E8** Гейты качества/CI (version.py, mypy scope, sandbox-skip mac/win, coverage-decoupling)
- **E9** Витрина, документация и сообщество (визуалы, метрики, архивы, воронка)
- **E10** Безопасность: эшелоны defense-in-depth для web

**Метрики успеха программы.**
- README/CHANGELOG/докстринги показывают фактическое число ready-карточек из coverage-CLI (912+), дрейф числа невозможен (авто-бейдж); ни одного хардкод-числа глоссария.
- Web имеет раздел «Прогресс» на `build_progress_report`; AI-подсказка доступна в web-грейде и playground с обязательным consent; оба — на тех же данных/движке без дублирования логики.
- Все 7 разделов web полностью локализуются при выборе English (0 кириллических узлов вне ui.json на en); `check_ui_locale_guardrails` + e2e зелёные.
- Повторное скачивание шага НЕ затирает непустой `task{N}_1.py` (регресс-тест); пайповый ввод не даёт трейсбека; `save_missing_queue` атомарна.
- stats/cache/missing-queue durability и межпроцессная гонка адресованы согласно решению (missing-queue на общий писатель/SQLite; stats остаётся JSONL); один `hash_solution` питает `solution_hash`.
- pytest зелёный, покрытие ≥85 single-OS; mypy покрывает src+scripts; `version.py` даёт осмысленный MAJOR.MINOR в tagless-клоне; mac/win sandbox-skip guard есть.
- Заведены good-first-issue метки, #363 нарезан на атомарные задачи; README содержит ≥1 демо-визуал и сравнительную таблицу против оригинала.
- Два Proposed-ADR (граница web↔core, персистентность) приняты до реализации; boundary-тест в `test_import_dag`; неверная премиса ContentProvider исправлена в доках.
- В web-ответах присутствуют CSP+nosniff; read-timeout выставлен; редиректы внешних загрузок ревалидируются; CHANGELOG-запись в каждом PR.

**Критерии готовности программы (Definition of Done).**
- Все P1-эпики (E1, E2, E7, E9) закрыты; P2/P3 — либо закрыты, либо явно перенесены с обоснованием.
- Каждый смерженный PR несёт запись в `CHANGELOG.md` под `[Unreleased]`.
- Инварианты CLAUDE.md соблюдены (union-типы, pathlib.Path в публичных сигнатурах, `__all__`, `from __future__ import annotations`, `_console`, sandbox только opt-in, `__all__` фасада не сломан).
- CI-матрица зелёная; версия не правлена вручную.

---

## Граф зависимостей эпиков

```
E1  ← (нет зависимостей)   базовый surfacing глоссария
E2  ← (нет зависимостей)   retention-дашборд
E3  ← E1                    AI-заземление тянет глоссарийный поиск/контент из E1
E4  ← (нет зависимостей)   i18n
E5  ← (нет зависимостей)   ADR/граница — предшествует E6
E6  ← E5                    персистентность реализуется после ADR-0011
E7  ← (нет зависимостей)   надёжность/дефекты
E8  ← (нет зависимостей)   гейты CI
E9  ← E1                    витрина использует авто-счётчик глоссария из E1
E10 ← (нет зависимостей)   безопасность web
```

Рекомендуемый порядок волн:
1. **Волна 1 (дешёвый surfacing + критичные дефекты):** E1, E2, E7.
2. **Волна 2 (расширения на готовом фундаменте):** E3, E4, E9, E10.
3. **Волна 3 (инфраструктурные консолидации):** E5 → E6, E8.

Внутриэпиковые зависимости issue указаны в блоках «Зависит от» каждого issue.

---

## Эпики
### [Epic] E1: Глоссарий — витрина, контент-волны и полнотекстовый поиск

- **Цель.** Привести витрину глоссария к фактическим 912 ready карточкам, устранить класс ручного дрейфа числа, нарезать контентный долг #363 в good-first-issue и добавить поиск по телу карточки.
- **Приоритет.** P1
- **Зависит от.** —
- **Обоснование.** Главный дифференциатор занижен в README/CHANGELOG/докстрингах на 311 карточек в нарушение CLAUDE.md #398 (подтверждено подсчётом бандла: 912/450/1362). Дешёвый surfacing даёт максимальный product/growth-эффект; контент-волны — идеальный community-актив, а не соло-грайнд.

#### `feat(glossary): генерировать число ready-карточек бейджем из coverage-CLI`

**Проблема / Контекст.** `README.md:44` («601»), `README.en.md:86` («~600»), `CLAUDE.md`, `CHANGELOG` («787 hidden»), докстринги `json_provider.py:36`/`core/glossary.py:12` («581») хардкодят устаревшие числа; факт — 912 ready. CLAUDE.md #398 прямо требует брать число из `python -m stepik_grader.glossary.coverage`, а не хардкодить.

**Acceptance criteria.**
- [ ] Новый генератор поверх `glossary.coverage` пишет `.github/badges/glossary.json` (зеркало `generate_version_badge.py`).
- [ ] `README.md`/`README.en.md`/`docs/glossary.md` показывают живой бейдж/подстановку вместо хардкод-числа.
- [ ] CHANGELOG-строка #436 обновлена под фактический draft-счётчик в этом же PR.
- [ ] Докстринги `json_provider.py:36` и `core/glossary.py:12` не содержат конкретного числа (ссылка на coverage-CLI).
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Тест генератора: число из бейджа совпадает с `coverage.build_coverage_report` по ready; не хардкод.
**Labels.** `docs`, `glossary`, `area:docs`
**Зависит от.** —
**Оценка (effort).** M

#### `feat(glossary): полнотекстовый поиск по summary/body карточки`

**Проблема / Контекст.** `GlossaryCard.matches` (`models.py:130-135`) ищет только по `search_terms` (id/title/aliases/keywords/tags); summary/body в поиск не входят — запрос естественным языком не находит релевантную карточку, справочник ощущается пустым.

**Acceptance criteria.**
- [ ] `matches`/поиск учитывает summary и body (in-memory, офлайн, без новых зависимостей).
- [ ] web `glossary_search` и CLI используют расширенный поиск единообразно.
- [ ] Существующий поиск по терминам не регрессирует.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Запрос по слову из body находит карточку, которой нет по title/aliases; порядок релевантности стабилен.
**Labels.** `enhancement`, `glossary`
**Зависит от.** —
**Оценка (effort).** M

#### `chore(glossary): нарезать волны #363 В4–В6 в good-first-issue`

**Проблема / Контекст.** 450 draft ждут промоушена в ready; #363 — единственный крупный контентный долг, но планируется соло. Волны В4–В6 по шаблону + конвейеру `glossary_draft_pipeline.py` (#438) — идеальный первый вклад, но нет ни одного good-first-issue.

**Acceptance criteria.**
- [ ] Заведены метки `good first issue` / `help wanted` / `difficulty:easy` / `area:glossary`.
- [ ] Волны В4–В6 разбиты на issue пачками ~15–25 карточек с чеклистом и ссылкой на конвейер #438.
- [ ] `CONTRIBUTING.md` содержит раздел «Добавь свою карточку» (3 команды конвейера, human-review обязателен).

**Тесты.** Не применимо (организационная задача); каждая карточка-PR проходит валидацию примеров конвейера.
**Labels.** `glossary`, `good first issue`, `area:community`
**Зависит от.** —
**Оценка (effort).** M

---
### [Epic] E2: Retention — web-дашборд прогресса, достижения и активация

- **Цель.** Вывести существующий retention-контур (TTFG, вердикты, streak, learning-карточки) в web как раздел «Прогресс», добавить видимые достижения и мягкую активацию истории, починив достоверность `task_key`.
- **Приоритет.** P1
- **Зависит от.** —
- **Обоснование.** `insights.py`/`progress_export.py` уже дают агрегаты и HTML-рендер; в web выведены только learning-карточки. Самый выгодный шаг «эффект/код» (M/L, не XL). Дашборд наполнен только при включённой истории и достоверном `task_key`.

#### `feat(web): раздел «Прогресс» на build_progress_report`

**Проблема / Контекст.** `progress_export.build_progress_report`/`insights.time_to_first_green` существуют и отдаются только через CLI `--export-progress`; web `SECTIONS` не содержит «Прогресс», раздел insights показывает лишь learning-карточки.

**Acceptance criteria.**
- [ ] `GET /api/progress` → тонкая viewmodel-обёртка над `build_progress_report(db_path)` (по образцу `insights_adapter`, без дублирования логики core).
- [ ] Web-раздел «Прогресс» рендерит KPI (solved/total, TTFG, вердикты) на существующих `kpiCard`/`kpiGrid`.
- [ ] Раздел «Правила» подсвечивает персонально нарушенные коды (`violated_rule_codes`).
- [ ] Ошибка БД/пустая история → graceful пустое состояние, не 500.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Тест viewmodel: агрегаты совпадают с `build_progress_report`; e2e: раздел открывается, при непустой истории показывает KPI.
**Labels.** `enhancement`, `web`, `area:web`
**Зависит от.** —
**Оценка (effort).** L

#### `fix(web): считать task_key относительно server.workspace, а не cwd`

**Проблема / Контекст.** `grade_path`/`grade_benchmark`/`grade_microbench` формируют `task_key` через `_rel(..., Path.cwd())` (`viewmodels.py`), тогда как конфайнмент идёт от `server.workspace` (`--root`). При `--root≠cwd` ключи коллизируют/нестабильны → искажение TTFG и «Подучить».

**Acceptance criteria.**
- [ ] `task_key` вычисляется относительно `server.workspace` во всех трёх grade-путях.
- [ ] При `--root≠cwd` ключ инвариантен к cwd и стабилен между повторными прогонами.
- [ ] Path-параметры остаются `pathlib.Path` (issue #73).
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** `test_web_history`: server(workspace=ws) при chdir(other), грейд файла под ws → `task_key` относителен ws, совпадает между прогонами.
**Labels.** `bug`, `web`
**Зависит от.** —
**Оценка (effort).** S

#### `feat(web): лёгкий слой достижений и активация истории`

**Проблема / Контекст.** `clean_streak` (`insights.py:123`) вычисляется, но не показывается пользователю; `record_history=False` по умолчанию (CLI-меню) → пустой «Подучить» на первой сессии без объяснения.

**Acceptance criteria.**
- [ ] Счётчик текущей серии + 3–5 простых бейджей (первая AC, серия 3/7, N решённых) — чистые функции из истории, без нового состояния.
- [ ] One-time onboarding-нудж при первом пустом дашборде: предложение включить локальную историю через существующий тумблер `user_settings`.
- [ ] Инвариант приватности не меняется глобально (ADR-0002); дефолт остаётся opt-in.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** streak/бейджи считаются из истории таблично; e2e: включение тумблера наполняет «Подучить» в том же сеансе.
**Labels.** `enhancement`, `web`, `area:community`
**Зависит от.** E2 (раздел «Прогресс»)
**Оценка (effort).** M

---
### [Epic] E3: AI-репетитор — охват web/playground/режимы 3-4 + consent + заземление

- **Цель.** Довести уже реализованный AI-дифференциатор (`ai_hints`, ADR-0003) до браузерного UX и режимов 3/4 с обязательным consent-gate и retrieval-заземлением, сохранив BYOK-инвариант.
- **Приоритет.** P2
- **Зависит от.** E1
- **Обоснование.** `explain_failure` заперт в CLI-режимах 1/2 (`commands.py`); web/ его не знает — фича невидима для растущей аудитории. Заземление плоское (одна карточка). Расширение без consent — регресс приватности несовершеннолетних.

#### `refactor(ai): вынести сборку FailureContext в переиспользуемый core-хелпер`

**Проблема / Контекст.** `FailureContext` собирается только в `cli/commands.py` (режимы 1/2); web и режимы 3/4 не могут переиспользовать логику без дублирования.

**Acceptance criteria.**
- [ ] `FailureContext`-сборка вынесена в core-хелпер (`TestResult`+`insights.failure_kind`+`error_glossary`).
- [ ] CLI-режимы 1/2 используют хелпер без изменения поведения.
- [ ] Режимы 3/4 CLI получают `--ai-hints` через тот же хелпер.
- [ ] `from __future__ import annotations`, `__all__`, union-типы; `CHANGELOG.md` запись.

**Тесты.** Хелпер строит идентичный контекст для одного WA-кейса в CLI и через web-путь.
**Labels.** `refactor`, `ai`
**Зависит от.** —
**Оценка (effort).** M

#### `feat(web): AI-подсказка в грейде и playground с consent-gate`

**Проблема / Контекст.** grep по `web/` на `ai_hints` пуст; в браузере барьер запуска ниже, а объяснение WA/RE ценнее всего. При этом код и тест-ввод уходят третьей стороне без согласия.

**Acceptance criteria.**
- [ ] `POST /api/v1/hint` (async job поверх `runs.py`) отдаёт подсказку отдельным полем контракта; grade/playground error-card имеют кнопку «Объяснить».
- [ ] Однократный явный consent перед первым запросом (код и IO уходят на `ai_base_url`; рекомендация локального ollama; для несовершеннолетних — согласие представителя).
- [ ] Не настроен провайдер → graceful skip, грейдинг не падает.
- [ ] LLM только opt-in HTTP (`requests`), без провайдерского SDK/runtime-зависимости.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Без consent запрос не уходит; при skip грейд возвращается без AI-поля; e2e кнопки «Объяснить».
**Labels.** `enhancement`, `web`, `ai`, `area:security`
**Зависит от.** E3 (core-хелпер)
**Оценка (effort).** L

#### `feat(ai): retrieval-заземление подсказки из глоссария`

**Проблема / Контекст.** `card_text` заполняется одной карточкой; база 1362 карточек в grounding не подтягивается — потолок качества и риск галлюцинаций на логических WA.

**Acceptance criteria.**
- [ ] По `failure_kind` + concepts из detector достаётся top-k карточек глоссария/правил, их summary/body вкладываются в промпт как контекст.
- [ ] Без внешних эмбеддингов (офлайн, инвариант 3 зависимостей).
- [ ] Заземление опционально и деградирует к плоскому промпту при пустом результате.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Для WA с известным concept в промпт попадает релевантная карточка; при отсутствии — плоский промпт.
**Labels.** `enhancement`, `ai`, `glossary`
**Зависит от.** E3 (core-хелпер), E1 (полнотекстовый поиск)
**Оценка (effort).** L

---
### [Epic] E4: Полная локализация UI (i18n оболочки)

- **Цель.** Сделать контрол «Язык интерфейса» настоящим: локализовать всю web-оболочку (разметку и JS-рендеры), закрыть guardrail'ом и e2e.
- **Приоритет.** P2
- **Зависит от.** —
- **Обоснование.** `index.html` имеет 0 `data-i18n` и ~105 кириллических узлов; ~170 литералов в JS-рендерах; `setLang` перерисовывает только глоссарий. Backend уже ru/en — разрыв только в UI.

#### `feat(web): каталог ui.json и applyUiLocale для статической разметки`

**Проблема / Контекст.** Весь chrome `index.html` захардкожен на русском (`<html lang=ru>`, 0 `data-i18n`); выбор English оставляет метки/кнопки/заголовки по-русски.

**Acceptance criteria.**
- [ ] `web/static/locales/ui.json` (ru/en) с идентичным набором ключей.
- [ ] `data-i18n` на всех видимых текст-узлах `index.html`; `applyUiLocale(lang)` проходит по `[data-i18n]` и переключает `<html lang>`.
- [ ] `setLang()` вызывает `applyUiLocale` и перерисовывает ВСЕ инициализированные разделы, не только глоссарий.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** e2e: выбор English → во всех 7 разделах нет кириллицы вне ui.json; повторный вход по deep-link сохраняет язык.
**Labels.** `enhancement`, `web`, `i18n`, `area:web`
**Зависит от.** —
**Оценка (effort).** L

#### `feat(web): централизованный t(key) для литералов в JS-рендерах`

**Проблема / Контекст.** ~170 русских литералов в `content`/`grade`/`sandbox`/`downloader`/`trace-player`/`core.js`; `data-i18n` их не покрывает.

**Acceptance criteria.**
- [ ] `t(key,params)`-хелпер в `core.js`, литералы заменены на `t(...)` во всех модулях-рендерах.
- [ ] Empty-state/статусы/тултипы локализуются.
- [ ] Отсутствующий ключ → видимый маркер/падение guardrail, а не тихий RU-fallback.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** e2e: динамические сообщения (грейд/трейс) на en не содержат кириллицы.
**Labels.** `enhancement`, `web`, `i18n`
**Зависит от.** E4 (каталог ui.json)
**Оценка (effort).** L

#### `test(ci): guardrail check_ui_locale_guardrails + зеркальный тест`

**Проблема / Контекст.** `check_locale_guardrails` стережёт только web-API каталог, не UI-оболочку; регресс локализации невидим.

**Acceptance criteria.**
- [ ] `scripts/check_ui_locale_guardrails.py` (stdlib, ast/html): каждый видимый узел несёт `data-i18n`; паритет ключей ru/en в ui.json; нет голых кириллических литералов вне ui.json.
- [ ] Зеркальный `test_check_ui_locale_guardrails` по образцу существующих guard-the-guard.
- [ ] Скрипт детерминирован и кроссплатформенен.

**Тесты.** Синтетический недостающий ключ/голый литерал → скрипт краснеет; полный каталог → зелёный.
**Labels.** `test`, `ci`, `i18n`
**Зависит от.** E4 (каталог ui.json)
**Оценка (effort).** M

---
### [Epic] E5: Архитектурная граница web↔core и ADR-дисциплина

- **Цель.** Зафиксировать границу web↔core фасадом и структурным guard'ом, исправить неверную премису ContentProvider, подготовить ADR под персистентность и prerequisites server mode.
- **Приоритет.** P2
- **Зависит от.** —
- **Обоснование.** `viewmodels`/`runs`/`playground` тянут core-внутренности напрямую без структурного правила; премиса «нет общего ContentProvider» неверна (`GlossaryProvider`/`RulesProvider` уже есть, `runtime_checkable`); крупные решения владельца без ADR. Server mode заблокирован процессными синглтонами, а не отложенностью.

#### `docs(adr): ADR-0010 граница web↔core + ADR-0011 персистентность`

**Проблема / Контекст.** Service-слой и SQL-унификация — решения уровня ADR (границы, durability, дорогой откат), но фиксируются в коде без зафиксированного контекста; премиса «нет общего ContentProvider» ложна — существуют два раздельных `@runtime_checkable`-протокола в разных подпакетах: `RulesProvider` (`rules/json_provider.py:41`) и `GlossaryProvider` (`glossary/json_provider.py:49`), а не единый `json_provider.py`.

**Acceptance criteria.**
- [ ] ADR-0010 (Proposed): адаптеры = сервисный слой, отдельный тяжёлый Service-слой не вводим; общий ContentProvider НЕ вводим (правило трёх); web-фасад для grade/bench/microbench.
- [ ] ADR-0011 (Proposed): общий `core/db.py`, что мигрирует, что остаётся JSON, graceful degradation.
- [ ] Неверная формулировка про ContentProvider исправлена в аудит-доках.
- [ ] docs/README индекс дополнен; `check_docs_guardrails` зелёный.

**Тесты.** Не применимо (ADR); проверяется docs-guardrail (ссылки/индекс).
**Labels.** `docs`, `architecture`, `area:docs`
**Зависит от.** —
**Оценка (effort).** M

#### `refactor(web): фасад web/grading + boundary-guard в test_import_dag`

**Проблема / Контекст.** Ничто структурно не мешает web дотянуться до приватных хелперов core; при server mode прямые импорты станут связностью для перемаршрутизации.

**Acceptance criteria.**
- [ ] grade/bench/microbench-вход web сведён в один фасад `web/grading`; `viewmodels` — чистый JSON-маппер над ним.
- [ ] `test_import_dag`: allowlist-проверка, что web импортирует только публичную поверхность core (не приватные `_`-хелперы).
- [ ] DAG остаётся ацикличным; `__all__` фасадов не сломан.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** `test_import_dag` краснеет на синтетическом импорте приватного core-хелпера из web.
**Labels.** `refactor`, `web`, `architecture`
**Зависит от.** E5 (ADR-0010)
**Оценка (effort).** M

#### `refactor(core): capability на Runner Protocol вместо stringly-typed sandbox-детекта`

**Проблема / Контекст.** `tracer.py:280` сравнивает `type(_RUNNER).__name__=='SandboxRunner'`; будущий `DockerRunner`/`RemoteRunner` обойдёт guard; `RunSpec` несёт несериализуемый `threading.Event`.

**Acceptance criteria.**
- [ ] Runner Protocol несёт способность (напр. `supports_project_imports`/`provides_isolation`); tracer консультирует её вместо `__name__`.
- [ ] `LocalRunner=True`, `SandboxRunner=False` объявлены явно.
- [ ] `RunSpec` задокументирован двухслойно: сериализуемое ядро + локальный-only cancel-канал.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Подкласс/обёртка Runner без `__name__=='SandboxRunner'` корректно распознаётся по способности.
**Labels.** `refactor`, `core`, `architecture`
**Зависит от.** E5 (ADR-0010)
**Оценка (effort).** M

---
### [Epic] E6: Унификация локальной персистентности

- **Цель.** Свести JSON-писатели к одному атомарному leaf-хелперу, ввести общий `core/db.py` и мигрировать missing-queue, устранить дубль `hash_solution` и N+1, добавить прунинг кэша.
- **Приоритет.** P2
- **Зависит от.** E5
- **Обоснование.** `save_missing_queue` неатомарна (подтверждено), три писателя с разной durability, межпроцессная гонка CLI+web, два `hash_solution` на одну колонку, N+1 в `read_recent_runs`, неограниченный рост кэша.

#### `feat(core): общий leaf-хелпер atomic_write_json`

**Проблема / Контекст.** `storage` атомарен+fsync, `user_settings` атомарен без fsync, `json_provider.save_missing_queue` — голый `open('w')` (truncate) → обрыв рвёт backlog #363; `glossary/` не может импортить `core/storage` (leaf).

**Acceptance criteria.**
- [ ] Stdlib-only leaf-модуль `atomic_write_json` (`mkstemp` в той же директории + `os.replace`, опц. fsync). **Размещение фиксируется ADR-0011 до реализации:** либо top-level shared-leaf вне `core/` (тогда ребро `glossary→core` не возникает — сегодня `glossary/` не импортирует проектных модулей, проверено `test_import_dag`), либо в `core/` с явным принятием и документированием нового ребра `glossary→core` (обновить инвариант leaf-модулей в CLAUDE.md и `test_import_dag`).
- [ ] `json_provider.save_missing_queue` и `user_settings.save_settings` используют его.
- [ ] `append_missing_entries` в `coverage.py:302` обёрнут в `try/except (GlossaryError, OSError)`.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Имитация обрыва между truncate и завершением не оставляет усечённый файл; coverage-CLI не падает на битой очереди.
**Labels.** `enhancement`, `core`, `area:reliability`
**Зависит от.** —
**Оценка (effort).** M

#### `feat(core): core/db.py и миграция missing-queue на SQLite/WAL`

**Проблема / Контекст.** Межпроцессную гонку закрывает только SQLite/WAL (закрыта лишь для history); stats/missing-queue на JSON с process-only Lock.

**Acceptance criteria.**
- [ ] `core/db.py` (`_connect`+PRAGMA WAL+`busy_timeout`+`user_version` по шаблону `history.py`).
- [ ] missing-queue мигрирована (durability+межпроцессная гонка закрыты разом); битая БД → тихий пропуск.
- [ ] `stats.jsonl` НЕ мигрируется (append-only JSONL — сознательная устойчивость к обрыву); история и кэш — разные файлы, не единый `.grader.db`.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Межпроцессная запись missing-queue через WAL без потери записей (по образцу `test_history` concurrent).
**Labels.** `enhancement`, `core`, `sql`
**Зависит от.** E6 (`atomic_write_json` / решения ADR-0011)
**Оценка (effort).** L

#### `refactor(core): свести hash_solution, убрать N+1, прунить кэш`

**Проблема / Контекст.** `history.hash_solution(str)` мёртв и семантически расходится с `cache.hash_solution(Path→bytes)` на одной колонке `solution_hash`; `read_recent_runs` делает 1+2N SELECT; кэш растёт неограниченно.

**Acceptance criteria.**
- [ ] Колонку `solution_hash` питает одна функция (`cache.hash_solution`); неиспользуемая история-версия удалена/сведена к re-экспорту без слома `__all__`.
- [ ] `read_recent_runs` использует `WHERE run_id IN(...)` (или LEFT JOIN), контракт возврата неизменён.
- [ ] cache при `_load`/`save` отбрасывает записи с несуществующим `solution_path` (или size-cap).
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Один и тот же solution даёт консистентный хеш; `read_recent_runs` эквивалентен старому на фикстуре; удалённый файл выпадает из кэша.
**Labels.** `refactor`, `core`, `efficiency`
**Зависит от.** —
**Оценка (effort).** M

---
### [Epic] E7: Надёжность CLI/исполнения и корректность sandbox-измерений

- **Цель.** Закрыть потерю данных студента, EOFError на пайпе и недостоверность измерения памяти/частичного вывода под sandbox.
- **Приоритет.** P1
- **Зависит от.** —
- **Обоснование.** downloader безусловно затирает `task{N}_1.py` (подтверждено), EOFError вылетает трейсбеком на вложенных prompt'ах, RSS bwrap нерепрезентативен, violation/RE-ветки теряют вывод.

#### `fix(downloader): не затирать непустое решение при повторном скачивании`

**Проблема / Контекст.** `downloader.py:184-185` `main_file.write_text` безусловно (template или пустая строка); `alt_file` защищён `if not exists` — асимметрия, потеря написанного решения.

**Acceptance criteria.**
- [ ] Существующий непустой `task{N}_1.py` не перезаписывается (`if not exists or not read_text().strip()`), либо явный overwrite-флаг/`.bak`.
- [ ] Первичное скачивание по-прежнему создаёт файл из шаблона.
- [ ] Path-параметры остаются `pathlib.Path`.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Регресс-тест: непустой `task{N}_1.py` переживает повторный `save_task_files`.
**Labels.** `bug`, `downloader`, `area:reliability`
**Зависит от.** —
**Оценка (effort).** S

#### `fix(cli): единый EOFError/OSError-контур интерактивного меню`

**Проблема / Контекст.** EOFError перехвачен только для выбора режима (`interactive.py:278-283`); вложенные `input` в `_prompt_path`/`_ask_*` вылетают трейсбеком на пайпе; пункт 6 роняет меню при занятом порте.

**Acceptance criteria.**
- [ ] EOF/Ctrl+D на любом вложенном prompt даёт корректный goodbye, не трейсбек.
- [ ] `printf '1\n' | python -m stepik_grader.grader` завершается штатно.
- [ ] Пункт 6 (`run_server`) ловит `OSError` (занятый порт), не только `KeyboardInterrupt`.
- [ ] Уважён late-binding monkeypatch-контракт (`CliContext`); `CHANGELOG`-запись.

**Тесты.** Пайповый ввод: EOF на prompt пути → goodbye/return, код выхода корректен.
**Labels.** `bug`, `cli`
**Зависит от.** —
**Оценка (effort).** S

#### `fix(sandbox): достоверный замер памяти дерева и частичный вывод violation/RE`

**Проблема / Контекст.** `_poll_memory` меряет RSS bwrap (`proc.pid`), а решение — внук в PID-namespace; violation-ветки отбрасывают stdout (в отличие от TLE); RE-ветка теряет stdout решения.

**Acceptance criteria.**
- [ ] Общий helper `sample_tree_rss(pid)` (`children(recursive=True)`+self, единая обработка psutil-исключений) используется в runner и sandbox; на Linux предпочтительно cgroup v2 `memory.peak`.
- [ ] Docstring честно фиксирует enforcement памяти под Linux-sandbox.
- [ ] output_size/memory-violation и RE прикладывают накопленный частичный stdout.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** memory-детектор проверяется отдельно от RLIMIT_AS-ветки (xfail для Linux-sandbox до фикса); RE-исход содержит частичный stdout.
**Labels.** `bug`, `core`, `sandbox`
**Зависит от.** —
**Оценка (effort).** M

---
### [Epic] E8: Гейты качества и CI

- **Цель.** Устранить масочный тест версии и мёртвый fallback, расширить типовой гейт, закрыть асимметрию sandbox-skip и хрупкость coverage-gate.
- **Приоритет.** P2
- **Зависит от.** —
- **Обоснование.** `version.py` fallback мёртв (#162) и тест масочный; mypy только src/; anti-silent-skip guard только Linux; combined-gate висит на flaky privileged-джобе; релиз собирает артефакты дважды.

> **Внимание.** Правки `.github/workflows/` требуют явной задачи (запрет CLAUDE.md) — каждый CI-issue этого эпика оформляется как явно санкционированная работа по workflows. Глобальный `fail_under` НЕ поднимать выше 85.

#### `fix(scripts): починить version.py fallback и усилить его тест`

**Проблема / Контекст.** `_major_minor_from_pyproject` читает удалённый `[project].version` → tagless даёт `0.0.N`; `test_fallback_when_no_tags` ассертит только суффикс `.42`, не MAJOR.MINOR (зелёный на `0.0.42`).

**Acceptance criteria.**
- [ ] MAJOR.MINOR берётся из setuptools-scm/`importlib.metadata`, а не удалённого `[project].version`; докстринг согласован с #162.
- [ ] `test_fallback_when_no_tags` явно ассертит ожидаемые MAJOR.MINOR (или `not startswith '0.0.'`).
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** tagless-источник даёт осмысленный MAJOR.MINOR; тест краснеет при деградации в `0.0`.
**Labels.** `bug`, `ci`, `area:ci`
**Зависит от.** —
**Оценка (effort).** S

#### `chore(ci): mypy на scripts/ и mac/win anti-silent-skip guard`

**Проблема / Контекст.** `scripts/` (2097 строк релиз-логики) без типового гейта; anti-silent-skip только Linux (#420) — mac/win sandbox-backend могут тихо стать no-op.

**Acceptance criteria.**
- [ ] mypy покрывает src+scripts (конфиг `[tool.mypy]` готов).
- [ ] `test_macos/windows_sandbox_not_silently_skipped` под `STEPIK_REQUIRE_SANDBOX_TESTS` в соответствующих job'ах матрицы.
- [ ] Вне CI (dev-машина) — обычный skip.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** На ОС-специфичном job'е отсутствие/поломка backend'а = падение, не skip; `mypy scripts` зелёный.
**Labels.** `ci`, `test`
**Зависит от.** —
**Оценка (effort).** M

#### `chore(ci): развязать combined-gate/бейдж от flaky sandbox-linux, собрать релиз один раз`

**Проблема / Контекст.** `coverage-combine needs [test, sandbox-linux]` жёстко → падение privileged-джоба замораживает cross-OS gate 90% и бейдж; `release.yml` собирает dist дважды.

**Acceptance criteria.**
- [ ] Падение sandbox-linux даёт явный красный/warning на combined-джобе, не немой skip; бейдж не пушится с устаревшими данными без пометки.
- [ ] Релизные dist собираются один раз и передаются через `upload/download-artifact` в GitHub Release и PyPI.
- [ ] Правки `.github/workflows` согласованы как явная задача (снятие запрета CLAUDE.md).

**Тесты.** Симуляция отсутствия `.coverage._linux` → combine деградирует с сигналом, а не проглатывается.
**Labels.** `ci`
**Зависит от.** —
**Оценка (effort).** M

---
### [Epic] E9: Витрина, документация и сообщество

- **Цель.** Устранить визуальный и числовой дрейф витрины, растянуть воронку контрибьюторов и навести порядок в docs-архивах.
- **Приоритет.** P1
- **Зависит от.** E1
- **Обоснование.** README без продуктовых визуалов, сравнительная таблица недосказана, метрики разъехались, CHANGELOG на потолке 3/3, аудиты лежат плоско; 0 good-first-issue при идеальном пуле.

#### `docs(readme): добавить демо-визуалы, сравнительную таблицу и блок доверия`

**Проблема / Контекст.** README содержит только бейджи (0 скриншотов/GIF); `docs/versions.md` сравнительная таблица не перечисляет глоссарий/insights/sandbox/двуязычность/историю; сигналы доверия занижены («1000+ тестов» vs 1645).

**Acceptance criteria.**
- [ ] Hero-GIF основного потока (`--serve` грейд→вердикт→diff) + 2–3 скрина в `docs/assets/`.
- [ ] Сравнительная таблица дополнена 5 строками (глоссарий/PEP8-Подучить/OS-песочница/двуязычность/история) и продублирована в README/README.en.
- [ ] Блок «Прозрачность и доверие» (1600+ тестов, mypy-strict, SECURITY.md, OIDC); число тестов актуализировано.
- [ ] Line-budget README не превышен (guardrail зелёный); `CHANGELOG`-запись.

**Тесты.** `check_docs_guardrails` зелёный (line-budget, ссылки, индекс).
**Labels.** `docs`, `growth`, `area:docs`
**Зависит от.** E1 (авто-счётчик глоссария)
**Оценка (effort).** M

#### `chore(community): метки good-first-issue, topics, Discussions, quickstart`

**Проблема / Контекст.** 0 good-first-issue, метки только bug/enhancement, topics Stepik-центричны, Discussions пусты, CONTRIBUTING без человеческого on-ramp.

**Acceptance criteria.**
- [ ] Заведены метки (`good first issue`/`help wanted`/`difficulty`/`area`); GitHub topics расширены (autograder/grader/education/learn-python/cli/sandbox/pep8).
- [ ] README/CONTRIBUTING содержат «Первый вклад за 15 минут» со ссылками на существующие каноны.
- [ ] Засеяны 3–4 Discussion (Q&A/Show&tell/Идеи), закреплены в README.

**Тесты.** Не применимо (организационная задача).
**Labels.** `community`, `good first issue`, `area:community`
**Зависит от.** E1
**Оценка (effort).** S

#### `docs(meta): устранить дрейф метрик, ротация CHANGELOG и архив аудитов`

**Проблема / Контекст.** Тесты/покрытие разъехались (1600+/1317/1150+ vs 1645; ~92-95/93/87.5 vs 89.26); `project-structure.md:44` ложный глиф у `core/`; CHANGELOG 3/3; 5 аудитов плоско в `docs/`.

**Acceptance criteria.**
- [ ] Числа тестов/покрытия в CHECKPOINT/project-structure заменены на «см. бейджи README» или CI-подстановку.
- [ ] `project-structure.md:44` `└── core/` → `├── core/` (`rules/` остаётся `└──`).
- [ ] Явный блокирующий пункт ротации CHANGELOG перед тегом в CONTRIBUTING; `docs/archive/` создан, `*audit*.md` + `role-*.md` перенесены, индекс ссылается одной строкой.
- [ ] index-completeness-guardrail расширен на рекурсию; `CHANGELOG`-запись.

**Тесты.** `check_docs_guardrails` зелёный после переноса; версионный бюджет CHANGELOG в норме.
**Labels.** `docs`, `area:docs`
**Зависит от.** —
**Оценка (effort).** S

---
### [Epic] E10: Безопасность — эшелоны defense-in-depth для web

- **Цель.** Добавить второй эшелон защиты web (CSP, read-timeout, ревалидация редиректов, гонка diag_log, видимость статуса изоляции), не меняя честный дефолт.
- **Приоритет.** P2
- **Зависит от.** —
- **Обоснование.** Критических уязвимостей нет; все находки второго эшелона, но дёшевы и уместны для сервиса, исполняющего недоверенный код.

#### `fix(web): CSP + X-Content-Type-Options и read-timeout`

**Проблема / Контекст.** `_send` (`server.py:892`) отдаёт только Content-Type/Length — XSS-защита держится на ручном `esc()`; нет read-timeout (slow-client держит воркер-поток).

**Acceptance criteria.**
- [ ] `_send` добавляет CSP `default-src 'self'; base-uri 'none'; object-src 'none'` (+style/font `'self'`) на HTML и `X-Content-Type-Options: nosniff` на все ответы.
- [ ] `_Handler.timeout` (~30с) и/или socket read timeout выставлены.
- [ ] Функциональность страницы (self-contained ассеты) не ломается.
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Ответы несут CSP/nosniff; медленный клиент разрывается по таймауту.
**Labels.** `security`, `web`, `area:security`
**Зависит от.** —
**Оценка (effort).** S

#### `fix(net): ревалидация редиректов внешних загрузок и гонка diag_log`

**Проблема / Контекст.** `external_download_get` следует редиректам без ревалидации хопа (обход SSRF-allowlist); `diag_log.redact` итерирует `_SECRETS` без блокировки (RuntimeError под многопоточным web).

**Acceptance criteria.**
- [ ] `external_download_get`: `allow_redirects=False` + ручная ревалидация каждого `Location` через `validate_external_url` с лимитом хопов.
- [ ] `redact` итерирует `tuple(_SECRETS)` (или короткий Lock).
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** Редирект allowlist-хоста на private-IP отклоняется; конкурентная `register_secret`+`redact` не бросает.
**Labels.** `security`, `core`
**Зависит от.** —
**Оценка (effort).** S

#### `feat(web): видимый статус OS-изоляции и уведомление о сборе истории`

**Проблема / Контекст.** Дефолт `--serve` без OS-изоляции честен (SECURITY.md), но браузерный UX снижает барьер запуска недоверенного кода; сбор учебной аналитики под `--serve` включён без первичного уведомления.

**Acceptance criteria.**
- [ ] Ненавязчивый баннер/бейдж «код исполняется без OS-изоляции; для недоверенного — `--sandbox`».
- [ ] Первичное уведомление о локальном сборе истории (хранится sha256, не исходник; отключается в Настройках).
- [ ] `docs/web-current.md`/SECURITY.md обновлены (принятые риски Sec-Fetch/CSRF зафиксированы).
- [ ] `CHANGELOG.md`: запись под `[Unreleased]`.

**Тесты.** e2e: баннер режима исполнения виден; уведомление о истории показывается однократно.
**Labels.** `security`, `web`, `area:security`
**Зависит от.** —
**Оценка (effort).** S

---

## Сводная таблица

| Приоритет | Эпик | Issue | Effort |
|---|---|---|---|
| P1 | E1 Глоссарий | feat(glossary): бейдж ready-карточек из coverage-CLI | M |
| P1 | E1 Глоссарий | feat(glossary): полнотекстовый поиск по summary/body | M |
| P1 | E1 Глоссарий | chore(glossary): нарезать волны #363 В4–В6 в good-first-issue | M |
| P1 | E2 Retention | feat(web): раздел «Прогресс» на build_progress_report | L |
| P1 | E2 Retention | fix(web): task_key относительно server.workspace | S |
| P1 | E2 Retention | feat(web): слой достижений и активация истории | M |
| P2 | E3 AI-репетитор | refactor(ai): FailureContext в core-хелпер | M |
| P2 | E3 AI-репетитор | feat(web): AI-подсказка в грейде/playground + consent-gate | L |
| P2 | E3 AI-репетитор | feat(ai): retrieval-заземление из глоссария | L |
| P2 | E4 i18n | feat(web): каталог ui.json + applyUiLocale | L |
| P2 | E4 i18n | feat(web): централизованный t(key) в JS-рендерах | L |
| P2 | E4 i18n | test(ci): check_ui_locale_guardrails + зеркальный тест | M |
| P2 | E5 Граница web↔core | docs(adr): ADR-0010 граница + ADR-0011 персистентность | M |
| P2 | E5 Граница web↔core | refactor(web): фасад web/grading + boundary-guard | M |
| P2 | E5 Граница web↔core | refactor(core): capability на Runner Protocol | M |
| P2 | E6 Персистентность | feat(core): atomic_write_json | M |
| P2 | E6 Персистентность | feat(core): core/db.py + миграция missing-queue на WAL | L |
| P2 | E6 Персистентность | refactor(core): свести hash_solution, N+1, прунинг кэша | M |
| P1 | E7 Надёжность | fix(downloader): не затирать непустое решение | S |
| P1 | E7 Надёжность | fix(cli): единый EOFError/OSError-контур меню | S |
| P2 | E7 Надёжность | fix(sandbox): замер памяти дерева + частичный вывод | M |
| P2 | E8 Гейты/CI | fix(scripts): version.py fallback + усилить тест | S |
| P2 | E8 Гейты/CI | chore(ci): mypy scripts/ + mac/win anti-silent-skip | M |
| P2 | E8 Гейты/CI | chore(ci): развязать combined-gate, собрать релиз один раз | M |
| P1 | E9 Витрина | docs(readme): визуалы, сравнительная таблица, блок доверия | M |
| P1 | E9 Витрина | chore(community): метки, topics, Discussions, quickstart | S |
| P3 | E9 Витрина | docs(meta): дрейф метрик, ротация CHANGELOG, архив аудитов | S |
| P2 | E10 Безопасность | fix(web): CSP + nosniff + read-timeout | S |
| P2 | E10 Безопасность | fix(net): ревалидация редиректов + гонка diag_log | S |
| P2 | E10 Безопасность | feat(web): статус OS-изоляции + уведомление о истории | S |

---

## Заметка об инвариантах CLAUDE.md

Все issue выше уважают действующий агентский контракт проекта:

- **CHANGELOG в каждом PR** — запись под `## [Unreleased]` обязательна в каждом смерженном PR без исключений для рефакторингов; `docs/history.md`/`CHECKPOINT.md` обновляются на релиз, не на PR.
- **Версию не править вручную** — версия динамическая из git-тегов (setuptools-scm); за дрейф отвечает CI (`check_version_consistency.py`).
- **Код-стайл Python 3.12+** — `from __future__ import annotations` в начале каждого нового файла; union-типы (`X | None`, `list[str]`), не `Optional/List/Dict`; пути — `pathlib.Path` в публичных сигнатурах (issue #73); `sys.executable`, не строка платформы; docstring и `__all__` в новых модулях; вывод через `_console`, без голых `except:`.
- **Sandbox только opt-in** — дефолт `LocalRunner` без OS-изоляции; изоляция включается явным `--sandbox`; недоступный backend → `parser.error`, не молчаливый откат.
- **Не ломать обратную совместимость `__all__`** — все имена фасада `grader.py` остаются доступны; фасады новых слоёв (`web/grading`) не ломают `__all__`.
- **DAG без циклов, leaf-модули чисты** — `storage.py`/`normalizers.py`/`glossary.py` не импортируют проектные модули; новый `atomic_write_json` — leaf, а его размещение (top-level shared-leaf вне `core/` либо явно принятое и задокументированное ребро `glossary→core`) фиксируется ADR-0011 до реализации (E6).
- **Правки `.github/workflows/` — только по явной задаче** — issue E8/E10, затрагивающие workflows, оформлены как явно санкционированная работа; глобальный `fail_under` не поднимается выше 85.
- **Роли** — задачи по архитектуре/продукту/безопасности при реализации сопровождаются ответами от релевантных ролей согласно матрице подключения.