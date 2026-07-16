# Полный аудит issue — Stepik-Python-Grader (2026-07-15)

> Разовый аудит **всех 253 issue** (244 закрытых + 9 открытых, #1–#390):
> закрытые сверены с фактическим кодом, а не с текстом issue. Для каждой
> закрытой issue помеченной не-DONE — adversarial re-check (скептик пытался
> доказать, что заявленное реализовано / пробел неактуален).

## 0. Методика

- 19 агентов-верификаторов × ~14 issue: Grep/Glob/Read по заявленным
  функциям, модулям, файлам + CHANGELOG/docs/tests. Закрытая issue ≠
  выполненная — проверялся код.
- adversarial re-check по каждому не-DONE: NEW_ISSUE / ALREADY_TRACKED /
  NO_ACTION.
- Ключевые находки перепроверены оркестратором вручную (file:line).

Ground-truth среза `main` (ветка `claude/friendly-hamilton-rl8i87`): версия
1.8.0; 74 тест-файла / 1364 теста; extras `watch`/`e2e`/`lint`; модули
`history.py`/`rules/`/`lint.py`/`insights.py`/`tracer.py`/`sandbox/*` на месте.

## 1. Сводный вердикт

| Вердикт | Кол-во | Значение |
|---|---:|---|
| ✅ DONE | 208 | Реализовано, подтверждено кодом/тестами/CHANGELOG |
| 🗂️ TRACKER | 31 | Эпик/трекер — сам кода не требует; суть закрыта корректно |
| 🟡 PARTIAL | 5 | Частично; часть AC не выполнена |
| ❌ NOT_DONE | 3 | Заявленное не сделано |
| 📐 DESIGN_ONLY | 2 | Дизайн-задача (docs/ADR) — deliverable выполнен, код фичи по замыслу нет |
| ⚪ OBSOLETE | 3 | Осознанно отменено |
| ♊ DUPLICATE | 1 | Дубль |
| **Итого** | **253** | |

**Итог:** проект в отличной инженерной форме. **239 из 244 закрытых
issue (98%) закрыты добросовестно.** Из «частично/не сделано» подавляющее
большинство — это либо (а) открытые v2.0-дизайн-задачи, которые честно
открыты, либо (б) осознанно отложенное/отменённое с задокументированной
причиной. **Реальных «закрыли, но забыли доделать» хвостов — всего два**
(§ 3), оба — дешёвая полировка.

## 2. Системные темы (что проверяли особо)

**«Двойники» из прошлого аудита (`docs/audit-2026-07.md`) — консолидированы:**
- **Два i18n → один (#355)** — ✅ хардкод-словарь `_MESSAGES` слит в
  `core/locales/{ru,en}.json`; парность стережёт `check_locale_guardrails.py`.
- **Два глоссария + три URL-стратегии → один (#356)** — ✅
  `core/error_glossary.py` объединяет богатую JSON-базу (~140 исключений) с
  компактной картой (~28) и все URL-стратегии в `card_url()`.
- **executor.py «не в проде» (#46)** — исходная находка устарела:
  `core/executor.py` штатно импортируется в `grader_core.py:66`; мёртвая
  обёртка `run_microbench_with_timeout()` **удалена** (#69, v1.4.0-post,
  `docs/history.md:101-104`).

**Server mode (v2.0)** — дизайн-deliverable'ы выполнены (#152/#156/#157 →
`docs/server-mode.md`, ADR-0001), сам сервер/PostgreSQL/аккаунты **не
реализованы и честно открыты** (#151/#153/#154/#155/#97). Это не дефект
аудита — это backlog.

**SQLite-persistence (эпик #130, OPEN)** — история прогонов реализована
(`core/history.py`, #344), но импорт карточек глоссария в SQLite (#133)
осознанно отложен (`glossary/json_provider.py:7` «SQLite отложен»; глоссарий
живёт на JSON-провайдере). Эпик #130 открыт — трекинг есть.

**Sandbox в web (#351)** — закрыт решением fail-fast: `--sandbox` вместе с
`--serve` даёт честную ошибку `parser.error(...)` (`cli/__init__.py:461-468`),
а не молча игнорируется. Реальный проброс SandboxRunner в web — отдельная
незаведённая задача (упомянута в § 3 как док-неточность CLAUDE.md).

## 3. Реальные хвосты закрытых issue (кандидаты в epic)

Только это — «реально не выполнено из закрытого, но полезно проекту»:

### 3.1 Остаток #384 (`fix(docs)`, закрыт + эпик #381 закрыт → не отслеживается)
Три дока-пробела на срезе `main`, все верифицированы file:line:
1. **ADR-0001 всё ещё `Proposed`** (`docs/adr/0001-server-mode.md:3`,
   индекс `docs/adr/README.md:29`) — а #384 требовал `Accepted`, т.к. фазы 1–2
   (Runner #140, result-contract #116) уже реализованы.
2. **`docs/trace-format.md:65–76` документирует heap-объекты только по `kind`**,
   без поля `type` — а `core/tracer.py` эмитит `"type"` на **каждом** объекте
   (строки 84,93,98,102,104,106,115,116). Строка 76 «глубже — `{"repr":...}`»
   тоже без `type`. (Нюанс: тело эпика #381 относило trace-format.md к «в
   порядке — не трогаем», т.е. пункт мог быть сознательно депроритизирован.)
3. **`CLAUDE.md:134` инвариант №4 ссылается на `issue #351` как на будущую
   работу** — но #351 **закрыт** (fail-fast). Ссылка на закрытую issue как на
   pending — фактическая неточность агент-контракта.

*Почему/что/как:* дешёвые точечные корректности док↔код; ценность — точность
документации (её читают и агент, и контрибьюторы). → **новая issue** ниже.

### 3.2 Мёртвая константа `WARMUP_RUNS` (из закрытого роадмапа #6, п. 2.1 «warmup»)
`WARMUP_RUNS = 3` определена (`microbench_runner.py:71`), экспортирована в
`__all__` (:54) и **тестируется на существование** (`test_microbench.py:69-71`,
`test_microbench_runner_module.py:159-160`) — но **не применяется** в цикле
замера: `bench_script` строит `timeit.repeat(repeat=5)` без прогрева
(микробенч гоняет `number×5` как один opaque-вызов, :225). Роадмап #6 отметил
warmup как «подтверждённую доработку», но в реальный цикл она не попала.

*Почему/что/как:* либо (а) реально прогревать (`for _ in range(WARMUP_RUNS):
runner()` перед `timeit.repeat`) — убирает искажение min/median на холодных
кэшах; либо (б) убрать константу + тесты как мёртвый код. Тест, проверяющий
только существование неиспользуемой константы, создаёт ложное ощущение
покрытия. → **новая issue** ниже.

## 4. Осознанно НЕ включено в epic (проверено, но не дефект)

- **#47 R-01 (per-call таймаут микробенча)** — реальный пробел, но
  **осознанно отложен**: настоящий per-call таймаут требует отказа от
  batch-исполнения `timeit.repeat()` либо `SIGALRM` (не работает на Windows —
  основная dev-платформа). Митигейт — общий 60s subprocess-таймаут +
  диагностика `number=` (`docs/changelog-archive.md:361-372`). reuse=false.
- **#133 (импорт глоссария в SQLite)** — осознанно отложен, покрыт открытым
  эпиком #130.
- **#156/#157 (server API contract / sandbox-требования)** — дизайн-deliverable
  выполнен полностью (`docs/server-mode.md`); реализация сервера — открытый
  #151.
- **#78 (standalone .exe PyInstaller)** — отменён: frozen `sys.executable`
  ломает грейдинг; pivot на PyPI/pipx (#70).
- **#55, #153, #154, #155, #363, #371** — открытые задачи, уже отслеживаются.
  (#55: авто-импорт pinned-решения признан технически несостоятельным —
  Stepik не публикует pinned; стоит рассмотреть рескоуп/закрытие открытой
  issue.)

## 5. Epic + новые issue (готово к постингу)

---

## EPIC — `[Epic] chore: хвосты закрытых issue по аудиту 2026-07-15`

**Labels:** `tech-debt`, `epic`, `documentation`

### Цель

Полный аудит всех 253 issue (2026-07-15) показал: 98% закрытых issue закрыты
добросовестно. Осталось два реальных «хвоста» — фрагменты, заявленные в уже
**закрытых** issue, но фактически не выполненные, при этом всё ещё полезные
проекту. Оба закрывающих трекера (#384, эпик #381; роадмап #6) закрыты как
completed, поэтому открытой issue на остаток нет — этот эпик её и заводит.

### Дочерние issue

- [ ] **A** — `fix(docs): дозакрыть остаток #384 — ADR-статус, type в trace-format, стейл-ссылка на #351`
- [ ] **B** — `fix(microbench): мёртвая константа WARMUP_RUNS — применить прогрев или удалить (остаток #6)`

### Не входит (проверено аудитом, дефектом не является)

- #47 R-01 per-call таймаут микробенча — осознанно отложен (Windows/SIGALRM,
  batch `timeit.repeat`), митигейт задокументирован.
- #133 импорт глоссария в SQLite — отложен, покрыт открытым эпиком #130.
- Server mode (#151/#153/#154/#155) — открытый v2.0-backlog.

### Acceptance criteria

- [ ] Обе дочерние issue закрыты.
- [ ] `ruff`/`mypy`/`pytest` зелёные; запись в `CHANGELOG.md` в каждом PR.

### Source

Создано по итогам полного аудита issue (2026-07-15), см. отчёт
`docs/issue-audit-2026-07-15.md`.

---

## ISSUE A — `fix(docs): дозакрыть остаток #384 — ADR-статус, type в trace-format, стейл-ссылка на #351`

**Labels:** `documentation`, `bug`

### Контекст

Аудит 2026-07-15 подтвердил на срезе `main` три дока-пробела, которые #384
заявлял, но не закрыл; #384 и эпик #381 закрыты — трекинга на остаток нет.

### Что сделать

1. **ADR-0001 → `Accepted`.** `docs/adr/0001-server-mode.md:3` и индекс
   `docs/adr/README.md:29` всё ещё `Proposed`. Фазы 1–2 направления (Runner
   #140, result-contract #116) реализованы — статус должен быть `Accepted`
   (или явно обосновать, почему остаётся `Proposed`).
2. **`type` в `docs/trace-format.md`.** Таблица heap-объектов (строки 65–76)
   описывает объекты только по `kind`, без поля `type`. Фактически
   `core/tracer.py` эмитит `"type"` на каждом объекте (строки
   84,93,98,102,104,106,115,116); депт-лимит-кейс — `{"type","repr"}`
   (`tracer.py:93`). Добавить `type` в документацию формата (колонка/пример),
   поправить строку 76.
3. **Стейл-ссылка на #351 в `CLAUDE.md:134`.** Инвариант №4 пишет «в web-слой
   пока не проброшена (issue #351)», но #351 **закрыт** (fail-fast:
   `--sandbox`+`--serve` → `parser.error`, `cli/__init__.py:461-468`).
   Переформулировать: проброс SandboxRunner в web — не проброшен, **открытой
   issue нет** (не ссылаться на закрытую как на будущую работу).

### Acceptance criteria

- [ ] ADR-0001 и его индекс: статус согласован с реальностью фаз 1–2.
- [ ] `trace-format.md` описывает поле `type` для heap-объектов; строка 76
      согласована с `tracer.py`.
- [ ] `CLAUDE.md` инвариант №4 не ссылается на закрытый #351 как на pending.
- [ ] `CHANGELOG.md` — запись под `[Unreleased]`.

### Links

- Остаток закрытой #384 (часть закрытого эпика #381).

---

## ISSUE B — `fix(microbench): мёртвая константа WARMUP_RUNS — применить прогрев или удалить (остаток #6)`

**Labels:** `bug`, `python`, `tech-debt`

### Контекст

Роадмап #6 (п. 2.1 «warmup») отметил прогрев как подтверждённую доработку.
Константа заведена, но в цикл замера не попала — на срезе `main`:

- `WARMUP_RUNS = 3` — `core/microbench_runner.py:71`, экспортирована в
  `__all__` (:54).
- Тесты проверяют только **существование** константы:
  `tests/test_microbench.py:69-71`, `tests/test_microbench_runner_module.py:159-160`.
- В `bench_script` прогрева нет: `timeit.repeat(repeat=5)` строится без
  предварительного `runner()` (см. комментарий `microbench_runner.py:225` —
  «number×5 как один opaque-вызов»).

Тест на существование неиспользуемой константы даёт ложное покрытие.

### Что сделать (одно из)

- **(a) Применить прогрев** — перед основным `timeit.repeat` выполнить
  `for _ in range(WARMUP_RUNS): runner()` (в теле `bench_script`), убрав
  искажение min/median на холодных кэшах; тест должен проверять **эффект**
  прогрева, а не только `isinstance(..., int)`.
- **(b) Удалить мёртвый код** — снять `WARMUP_RUNS` из `__all__`, определения
  и обоих тестов; при желании отметить в docstring, что прогрев осознанно не
  делается (и почему).

Предпочтителен вариант владельца; (a) ценнее для стабильности бенчмарка
режимов 3/4.

### Acceptance criteria

- [ ] `WARMUP_RUNS` либо реально используется в цикле замера, либо удалена
      вместе с тестами-на-существование.
- [ ] Тест отражает фактическое поведение (эффект прогрева / отсутствие
      константы), а не наличие символа.
- [ ] `pytest`/`ruff`/`mypy` зелёные; запись в `CHANGELOG.md`.

### Links

- Остаток закрытого роадмапа #6 (п. 2.1); связано с микробенчем #47 (R-01
  per-call таймаут в scope НЕ входит — осознанно отложен).

---

## Приложение A. Полная таблица вердиктов (все 253 issue)

| # | Вердикт | Заголовок | Доказательство / Пробел |
|---|---|---|---|
| 1 | ✅ DONE | refactor: выделить OAuth-логику в oauth_flow.py | src/stepik_grader/core/oauth_flow.py существует с публичным API (__all__: load_secrets, authorize_and_get_token, token_is_valid, make_session и др., строки 39-131); diagnostic_stepik.py:25 импортирует authorize_via_browser/load_secrets/make |
| 6 | 🗂️ TRACKER | Дорожная карта: подтверждённые доработки (июнь 2026) | Роадмап-трекер. 3.1 retry: stepik_client.py:119 Retry-adapter (issue #109); 3.2 cache: cached_get + CACHE_DIR/CACHE_TTL_SECONDS (stepik_client.py:490-511); 4.2 normalizers: normalize_floats/sort_lines/normalize_whitespace; 1.1 builtins: mic **⚠ Пробел:** 2.1 warmup: константа WARMUP_RUNS=3 определена, экспортирована и тестируется на существование, но НЕ применяется в реальном цикле замера (bench_script вызывает timeit.repeat(repeat |
| 9 | ✅ DONE | refactor: вынести _parse_testblock_file в parsers.py | parsers.py:20 def parse_testblock_file; test_loader.py:26 'from ...parsers import parse_testblock_file as _parse_testblock_file'; test_source_fetcher.py:22 импортирует из parsers; downloader.py не импортирует из grader (использует task_page |
| 10 | ✅ DONE | refactor: заменить os.path на pathlib в grader.py | grep -c 'os\.path' grader.py = 0 и core/grader_core.py = 0. Логика вынесена в core-модули на pathlib. |
| 11 | ✅ DONE | refactor: grader.py — выделить runner.py и reporter.py | core/runner.py (LocalRunner, _measure_peak_memory, _apply_memory_limit) и core/reporter.py (print_correctness_results, print_benchmark_results, format_*) существуют; grader_core.py реэкспортирует их. tests/test_runner.py присутствует. God O |
| 12 | ✅ DONE | fix: json.load напрямую минуя storage.py | Чтение meta.json идёт через load_json_file из storage: mode_detector.py:146 'meta = load_json_file(meta_path)'. grep 'json.load'/'open(' в grader.py и grader_core.py = пусто. |
| 13 | ✅ DONE | fix: заменить hashlib.md5 на sha256, убрать noqa S324 | stepik_client.py:505 'key = hashlib.sha256(key_data.encode()).hexdigest()'; cache.py:42,60 тоже sha256. Ни одного hashlib.md5 или noqa: S324 в репозитории. |
| 14 | ✅ DONE | chore: NamedTemporaryFile delete=False через delete_on_close | Закрыта как not_planned — осознанное решение. Паттерн delete=False + finally-cleanup сохранён в microbench_runner.py (~строка 155-163: NamedTemporaryFile(delete=False)) с обоснованием в docstring (subprocess должен видеть файл на диске). Ко |
| 15 | ✅ DONE | Аудит тестового покрытия: рекомендации | pyproject.toml:166 fail_under=85, :167 exclude_lines, :115 addopts с -v --tb=short --cov-report=term-missing; tests/test_formatters.py существует (24 test-функции); TLE-тест с реальным subprocess. Все ключевые пункты AC внедрены. |
| 16 | ✅ DONE | fix: benchmark table columns truncated (rich min_width) | core/reporter.py:259-261 в print_benchmark_results: table.add_column(name, justify='right', min_width=mw), Relative min_width=8, Verdict min_width=10 — ровно как в предложенном фиксе. |
| 18 | 🗂️ TRACKER | Эпик: план технического долга по итогам аудита v1.0.0 | Зонтичный трекер над #19/#20/#21. Все три дочерние закрыты и сущностно выполнены (см. соответствующие вердикты). Эпик закрыт корректно. |
| 19 | ✅ DONE | High: дублирование парсера, импорт-цикл, doc drift (#1-#3) | Находка#1 (дублированный _parse_testblock_file): устранена — единственная реализация в parsers.py, grader через test_loader реэкспорт. Находка#2 (локальный импорт downloader->grader): удалён, downloader на top-level импортах core-модулей. Н |
| 20 | ✅ DONE | Medium: рефакторинг grader.py, валидация codegen, ранжирование (#4-#6) | Находка#4: grader.py разбит на runner.py/reporter.py/cli/ + grader_core (тонкий фасад). Находка#5: wrapper_builder.py:48-51 валидирует function_name и module_stem через .isidentifier() с raise ValueError. Находка#6: единая логика ранжирован |
| 21 | ✅ DONE | Low: сужение except, покрытие меню, упрощения, threat model (#7-#10) | Находка#7: grep 'except Exception'/'except:' в microbench_runner.py = пусто (сужено). Находка#8: меню вынесено в cli/ (interactive.py), покрытие проще. Находка#9: token_is_valid (stepik_client.py:229) использует float(secrets.get('expires_a |
| 23 | ✅ DONE | ARCH-0: вынести внутренние модули в core/ | Внутренние модули живут в src/stepik_grader/core/ (executor.py, normalizers.py, parsers.py, storage.py, stepik_client.py, oauth_flow.py, microbench_runner.py — все присутствуют). Изначальный core/ в корне позже мигрирован под src/ (issue #3 |
| 24 | ✅ DONE | fix: режим 4 — форматирование времени (µs/ns) | src/stepik_grader/core/reporter.py:71 def fmt_time(t: float) с ветками s/ms/µs/ns (строка 85 'µs'); тесты в tests/test_formatters.py и tests/test_web.py. |
| 25 | ✅ DONE | feat: режим 4 — память через tracemalloc | core/microbench_runner.py:176 bench_script использует 'tracemalloc as _tm'; возвращает peak_memory_mb (строка 244); reporter.py:150-245 отдельный memory_header 'Py-heap' для режима 4 (issue #66); тесты test_microbench_runner_module.py. |
| 26 | ✅ DONE | refactor: перенести grader_core.py и reporter.py в core/ | src/stepik_grader/core/grader_core.py и src/stepik_grader/core/reporter.py оба присутствуют; grader.py — тонкий фасад (99 строк), реэкспорт из core.grader_core. |
| 27 | ✅ DONE | A1: CHECKPOINT.md перезаписать под v1.1.0 | CHECKPOINT.md актуален: 'Snapshot: v1.8.0 (stable)', 'Текущая версия: 1.8.0', упоминает src-layout (issue #35), core/, config.py — файл давно поддерживается, устаревшего v1.0.0 состояния нет. |
| 28 | ♊ DUPLICATE | CHECKPOINT.md перезаписать под v1.1.0 (дубль) | Тело идентично #27 (тот же заголовок, те же 12 нестыковок, тот же чеклист). Дубликат #27; суть выполнена (CHECKPOINT.md актуален на v1.8.0). |
| 29 | ✅ DONE | Bump версии 1.0.0 → 1.1.0 | grep '1.0.0'/'1.1.0' по pyproject.toml/grader.py/cli не находит хардкода; версия динамическая (setuptools-scm + importlib.metadata в cli/__init__.py:_resolve_version). Ручной bump как задача разово выполнен, механизм эволюционировал. |
| 30 | ✅ DONE | CHANGELOG.md: секция [1.1.0] | Секция '## [1.1.0] - 2026-07-02' присутствует в docs/changelog-archive.md:411 (перенесена туда по политике ротации issue #373 из CHANGELOG.md). |
| 31 | ✅ DONE | CLAUDE.md: обновить 3 устаревших места | CLAUDE.md не содержит 'заморожен'; метрика версии = 1.8.0; статус Glossary-Python описан как цель экспорта, а не заморожен. Все три места давно приведены в актуальное состояние. |
| 32 | ✅ DONE | README.md: синхронизировать со структурой core/ | README.md использует динамический version-badge (endpoint version.json, строка 5), отражает текущий src/stepik_grader-layout; структура давно синхронизирована и поддерживается CI-гардрейлами (check_docs_guardrails). |
| 33 | ✅ DONE | Нестыковка: grader.py не 8 строк — расследовать | Расследование разрешено: grader.py = 99 строк, начинается с docstring, явно поясняющего 'сам файл не содержит логики — она перенесена в три модуля'; далее __all__ + реэкспорты. Метрика 'тонкий фасад' задокументирована, противоречие снято. |
| 34 | ✅ DONE | Нестыковка: CHECKPOINT.md не 98 строк — проверить | Аудит-задача разрешена вместе с #27: CHECKPOINT.md перечитан и перезаписан (актуален на v1.8.0), актуальный контент не потерян. |
| 35 | ✅ DONE | Sprint 8.2: перейти на src/-layout | src/stepik_grader/__init__.py явно ссылается 'src/-layout (Issue #35)'; пакет живёт в src/stepik_grader/ с подпакетом core/; импорты вида from stepik_grader.core.X. |
| 36 | ✅ DONE | __version__ DRY через importlib.metadata | cli/__init__.py:98 _resolve_version() → importlib.metadata.version('stepik-python-grader') с fallback '0.0.0+unknown' на PackageNotFoundError; grader.py:58 реэкспортирует __version__ из cli; хардкода версии в .py нет. |
| 37 | ✅ DONE | Опечатка diagnostik_stepik.py → diagnostic_stepik.py | src/stepik_grader/diagnostic_stepik.py существует; grep 'diagnostik' по *.py/*.md даёт только docs/changelog-archive.md (историческая запись), актуальных ссылок нет |
| 38 | ✅ DONE | Glossary-Python: разморозить + мин. документация | grep 'заморожен\|frozen\|Sprint 6' по CLAUDE.md — совпадений нет; секция 'Связанный проект' в CLAUDE.md описывает Glossary-Python как доступный (через core/glossary.py). Пункты про CHANGELOG.md и GitHub Pages относятся к ДРУГОМУ репозиторию **⚠ Пробел:** Кросс-репо чеклист (CHANGELOG/CONTRIBUTING/CI в самом Glossary-Python) не проверить из этого репозитория |
| 43 | ✅ DONE | S-01/S-02: code injection и отсутствие OS-sandbox | S-01: best-effort memory cap (_apply_memory_limit) + opt-in --sandbox с RLIMIT_AS/CPU (core/sandbox/_linux.py, options.py:202, #266). S-02 закрыт как ДУБЛИКАТ S-01 (docs/changelog-archive.md:377-382, history.md:129): safe_input/call_block — |
| 44 | ✅ DONE | S-03: wildcard-импорты в _build_call_wrapper | wrapper_builder.py:109-160 — 'from collections import (Counter, OrderedDict, ...)' явные импорты с noqa F401 вместо wildcard; комментарий ссылается на issue #44; docs/history.md:119 |
| 45 | ✅ DONE | A-01/A-02/A-04: SRP и layering grader_core/cli | A-01: core/test_loader.py, mode_detector.py, wrapper_builder.py выделены. A-02: grader_core.run_tests принимает verbose_callback: Callable (grader_core.py:416,481-482), reporter не импортируется. A-04: resolve_test_dir/rich_track публичны б |
| 46 | ✅ DONE | A-03: executor.py не используется в production | Закрыт осознанным решением 'оставить как есть' без изменения кода (docs/history.md:135 'решено оставить как есть', docs/changelog-archive.md:385 'closed with no code change'); докстринг executor.py уточнён про отсутствие OS-sandbox |
| 47 | 🟡 PARTIAL | R-01/R-02/R-04: microbench timeout, эвристика, resolve_test_dir | R-04 DONE: resolve_test_dir возвращает None (test_loader.py:251). R-02 DONE: одиночный ast.Name трактуется как stdin (mode_detector.py:127-131). R-01 PARTIAL: реального per-call таймаута нет — только диагностическое сообщение с number= при  **⚠ Пробел:** R-01: генеральный per-call таймаут прерывания одной зависшей итерации не реализован (timeit гоняет number×5 как один непрерываемый вызов) |
| 48 | ✅ DONE | R-03/R-05: смешанные форматы и race в _measure_peak_memory | R-03: warnings.warn при смешанных форматах (test_loader.py:142,153). R-05: warnings.warn '_warn_unreliable' при быстром завершении процесса/NoSuchProcess (runner.py:183-189, обработка NoSuchProcess на 198/207/211) |
| 49 | ✅ DONE | C-01/C-02/Q-01/Q-02: Windows CI, mypy, coverage | ci.yml:40 os matrix [ubuntu,windows,macos] + 3.14 experimental include (49-46); mypy шаг ci.yml:72; RequestException mock-тесты в tests/test_test_source_fetcher.py |
| 50 | ✅ DONE | D-02/D-03/D-04/D-05: CONTRIBUTING, output json, verbose/quiet, диагнос | D-02: CONTRIBUTING.md существует. D-03: --verbose/--quiet (options.py:73,79 + resolve на 225-233). D-04: --output json/csv/markdown (options.py:90). D-05: информативные 'tests not found: {test_dir}' (commands.py:512-514) + resolve_test_dir→ |
| 51 | ✅ DONE | P-01/P-02/C-03/D-01: requirements.txt, upper bounds, release, язык CLI | P-01: requirements.txt удалён (ls: No such file). P-02: 'requests>=2.34.2,<3.0','psutil>=5.9,<8.0' в pyproject.toml. C-03: .github/workflows/release.yml существует. D-01: --lang флаг (options.py:66) + i18n (core/locales/ru.json,en.json) |
| 52 | ✅ DONE | Q-03: __all__ экспортирует внутренние константы | grader_core.py:54-59 комментарий 'TIMEOUT_SECONDS/ENCODING/... намеренно НЕ в __all__ (issue #52)'; константы реэкспортируются по имени для backward-compat, но убраны из __all__ |
| 53 | ✅ DONE | Feature: --output json/csv машиночитаемый вывод | options.py:90 choices=['text','json','csv','markdown']; cli/rendering.py:24 _rows_to_csv, _print_tabular; help ссылается на issue #53; commands.py формирует json_results |
| 54 | ✅ DONE | Feature: --watch автоперезапуск при изменении .py | pyproject.toml extra watch=['watchfiles>=0.21,<2.0']; --watch флаг options.py; реализация cli/__init__.py:364 'from watchfiles import watch' (opt-in зависимость) |
| 55 | 🟡 PARTIAL | feat(stepik): полуавтоматический импорт закреплённого решения из saved | REFERENCE-ранжирование (backend-задел) реализовано: web/viewmodels.py:471 _apply_reference_ranking, вердикт 'REFERENCE' в режимах 3/4 (CHANGELOG.md:31 «#55 backend groundwork kept» #369). Но авто-импорт pinned-решения не сделан: docs/histor **⚠ Пробел:** Ключевой AC — авто-импорт закреплённого решения (saved JSON → pinned solution → taskN_stepik_reference.py) — не реализован и признан технически несостоятельным (Stepik не публикует |
| 56 | ✅ DONE | .grader_cache/ — кэширование результатов | src/stepik_grader/core/cache.py существует; tests/test_cache.py; CLI-флаги --cache/--no-cache/--clear-cache в options.py |
| 57 | ✅ DONE | pytest plugin --grader-mode | src/stepik_grader/pytest_plugin.py + tests/test_pytest_plugin.py; pytest11 entry point. Отдельный PyPI-пакет осознанно не сделан (задекларировано в issue) |
| 58 | ✅ DONE | Фичи среднего потенциала: Web UI/VS Code/PyPI/--lang/export MD | --serve (options.py:170), --init-vscode+ide.py write_vscode_tasks, --lang (options.py:66), --output markdown (options.py:90-95), pypi-publish в release.yml — все пункты подтверждены кодом |
| 59 | 🗂️ TRACKER | [Roadmap] Долгосрочные идеи: Docker-sandbox, платформы, AI-подсказки,  | Roadmap-тред. Частично реализовано: sandbox (#266, core/sandbox/ — но локальный MVP, не Docker); дашборд/статистика (core/stats.py + core/insights.py, insights #347/#349). НЕ сделано: PlatformPlugin Codeforces/LeetCode (grep находит только  **⚠ Пробел:** PlatformPlugin (Codeforces/LeetCode) и AI-подсказки (--ai-hints) не реализованы; Docker-sandbox заменён локальным MVP #266 (не контейнеры). Roadmap корректно OPEN. |
| 60 | 🗂️ TRACKER | AUDIT v1.1.0 — сводный эпик | Эпик аудита; под-issue #43-54/#58 реализованы (cache, layering, prlimit и т.д. подтверждены в этом батче). Сам код не требует |
| 61 | ⚪ OBSOLETE | placeholder (удалить) | Пустой placeholder-issue без тела и меток; закрыт корректно как мусорная запись |
| 62 | ⚪ OBSOLETE | placeholder2 (delete) | Пустой placeholder-issue без тела; закрыт как мусорная запись |
| 64 | ✅ DONE | force UTF-8 stdio — UnicodeEncodeError в Git Bash | _force_utf8_stdio() в cli/options.py:280 (reconfigure encoding=utf-8 errors=replace), вызывается в cli/__init__.py:408; тест в tests/test_cli.py (grep utf8) |
| 65 | ✅ DONE | __main__.py — python -m stepik_grader | src/stepik_grader/__main__.py делегирует в cli.main(); tests/test_entrypoint.py проверяет python -m stepik_grader |
| 66 | ✅ DONE | переименовать колонку памяти mode-4 (tracemalloc = Py-heap) | core/reporter.py:150-156,240-245 memory_header 'Py-heap' vs 'Memory' с пояснением методики (issue #66); тесты в test_cli.py/test_formatters.py (grep memory_header) |
| 67 | ✅ DONE | prlimit вместо preexec_fn — thread-safety | preexec_fn отсутствует в grader_core.py и microbench_runner.py; _apply_memory_limit через resource.prlimit ПОСЛЕ spawn в core/runner.py:126-149 и microbench_runner.py:107-122; tests/test_runner.py |
| 68 | ✅ DONE | документировать схему версионирования | CONTRIBUTING.md §Версионирование (issue #68) строки 229+ с MAJOR/MINOR/PATCH и примером 1.2.17; scripts/version.py реализует расчёт; CLAUDE.md § Версионирование дублирует |
| 69 | ✅ DONE | вердикт по unwired run_microbench_with_timeout() | run_microbench_with_timeout отсутствует в src/ (grep пусто) — принято решение Remove (option B) и исполнено; issue была decision-needed, решение зафиксировано удалением |
| 70 | ✅ DONE | PyPI trusted publisher | Job pypi-publish в .github/workflows/release.yml:50-55 с OIDC id-token:write, environment pypi. Живой PyPI-релиз не проверяем офлайн, но механизм в коде присутствует |
| 71 | ✅ DONE | incremental re-run под --watch — только изменённые файлы | _resolve_use_cache в cli/__init__.py:67 (приоритет флагов→watch-инкрементальный дефолт→config); _watch_and_rerun:352; переиспользован дисковый кэш #56 (test_cache.py) |
| 72 | ✅ DONE | feat(glossary): link Python Glossary terms on RE/WA verdicts | src/stepik_grader/core/glossary.py:39 GLOSSARY_BASE_URL, :135 lookup_from_error; leaf-module __all__; tests test_glossary_module.py + test_web_glossary.py; CHANGELOG confirms ~28 exceptions map consumed by reporter+web |
| 73 | ✅ DONE | refactor(api): Path instead of str in public contracts | resolve_test_dir() -> pathlib.Path \| None (test_loader.py:219); CHANGELOG.md:551-563 documents breaking Path migration across package; CLAUDE.md enforces it as active invariant (issue #73). Closed as implemented despite v2.0/breaking label |
| 74 | ✅ DONE | chore(repo): post-migration sweep — remnants of pre-src layout | No legacy grader.py/executor.py at repo root (ls: No such file); package fully in src/stepik_grader/; checklist items were verification-only and clean. |
| 75 | 🗂️ TRACKER | chore(release): tag v1.2.0 | Release chore; CHANGELOG contains historical [1.2.0] entry. Git tags absent in clone is a fetch artifact, not a defect. Nothing code-verifiable remains. |
| 78 | ⚪ OBSOLETE | feat(dist): standalone .exe (PyInstaller) | Closed as not-planned: frozen sys.executable makes grading impossible in PyInstaller binary; deliberately pivoted to PyPI/pipx (#70). No code expected. **⚠ Пробел:** Standalone .exe never built — intentionally abandoned. |
| 79 | ✅ DONE | feat(cli): file dialog fallback (tkinter) | src/stepik_grader/cli/interactive.py:125 _pick_path_via_dialog with askopenfilename/askdirectory, try/except ImportError + TclError headless guard; tests in test_cli.py:586+ (dialog fallback, cancel graceful, headless). |
| 80 | 🗂️ TRACKER | [Epic] UX/Onboarding | Epic; children #77/#79/#70/#83/#86/#87/#88 delivered (tkinter dialog, --serve web, --init-vscode all present in code). Closed correctly. |
| 83 | 🗂️ TRACKER | [Owner action] Настроить PyPI trusted publishing | .github/workflows/release.yml:50-55 pypi-publish job with environment: pypi, id-token: write OIDC trusted publishing (pypa/gh-action-pypi-publish). Owner-side PyPI config unverifiable from repo but CI side is present. |
| 96 | 🗂️ TRACKER | [Epic] Web-оболочка → хостинг | Epic. Phase 1 (rich local web shell, error cards #72, file picker, run history) implemented in web/. Phase 2 (sandbox #266) implemented as opt-in. Phase 3 (hosting) intentionally deferred. **⚠ Пробел:** Phase 3 hosting/orchestration not implemented — explicitly out-of-scope future work. |
| 97 | 🗂️ TRACKER | [Epic] Анализ и развитие: CLI → WEB/Server IDE | Верхний эпик. Дети реально продвинуты: WEB MVP (#123) реализован (web/ полный слой, static, playground), logging (#146) реализован через #341 (core/diag_log.py), SQLite-персистентность (#130-русло) есть в core/history.py (issue #344). Незав **⚠ Пробел:** Остаётся открытым только серверный поток (#151 и дети #153/#154/#155). CLI→WEB завершён, Server IDE — нет. Эпик корректно OPEN. |
| 98 | 🗂️ TRACKER | [Epic][PR-1] Packaging hygiene | Epic; children #99/#100/#101 all verified done (LICENSE, py.typed, version sync). Closed correctly. |
| 99 | ✅ DONE | chore(release): синхронизировать версии | Version is dynamic via setuptools-scm; scripts/check_version_consistency.py guards drift; CHANGELOG.md:1244 notes version sync (#99) already satisfied by pre-merge version rule in CLAUDE.md. |
| 100 | ✅ DONE | chore(legal): LICENSE + license metadata | LICENSE file present; pyproject.toml:17 license = "MIT", :18 license-files = ["LICENSE"], setuptools>=77 for PEP 639. CHANGELOG:1239-1242 confirms. |
| 101 | ✅ DONE | chore(types): py.typed + publish type hints | src/stepik_grader/py.typed present; pyproject.toml:87-89 [tool.setuptools.package-data] declares py.typed (PEP 561). CHANGELOG:1241-1244 confirms downstream type-hint publication. |
| 102 | 🗂️ TRACKER | [Epic][PR-2] Documentation split | Epic; docs/project-structure.md, docs/architecture.md, docs/versions.md all present; README trimmed to showcase per CLAUDE.md source-of-truth table. Children #103-#107 delivered. |
| 103 | ✅ DONE | docs(readme): сократить README | README.md сейчас 131 строки (ещё сильнее ужат после PR #160); docs/ содержит вынесенные разделы; CONTRIBUTING §Документация ссылается на README-как-витрину |
| 104 | ✅ DONE | docs(structure): project-structure.md | docs/project-structure.md существует (11062 байт, дерево файлов) |
| 105 | ✅ DONE | docs(architecture): архитектурная карта + поток | docs/architecture.md существует (32954 байт, DAG + слои) |
| 106 | ✅ DONE | docs(versions): сравнение версий | docs/versions.md существует (6516 байт, таблица релизов + отличия от оригинала) |
| 107 | ✅ DONE | docs(contributing): правило README без раздувания | CONTRIBUTING.md:54 '## Документация: README как витрина, docs/ как база знаний'; :85 line-budget/link-check guard (issue #173) |
| 108 | 🗂️ TRACKER | [Epic][PR-3] Stepik client retry/backoff | Эпик; дочерние #109/#110/#111 все реально выполнены (см. ниже). CHANGELOG.md:933 'Stepik client retry/backoff (epic #108)' |
| 109 | ✅ DONE | feat(client): Retry/HTTPAdapter в make_session | stepik_client.py:119-127 Retry(total,backoff_factor,status_forcelist,respect_retry_after_header) + HTTPAdapter mount https/http; RETRY_STATUS_FORCELIST={429,500,502,503,504} |
| 110 | ✅ DONE | test(client): 429/5xx + backoff сценарии | tests/test_stepik_client_retry.py: test 429 retry, parametrize [500,502,503,504], does_not_retry_404, gives_up_after_max_retries, default_retries_is_three |
| 111 | ✅ DONE | docs(client): поведение при сетевых сбоях | docs/installation.md:240 '### Устойчивость к сетевым сбоям (issue #108/#109)' — какие статусы повторяются, backoff, Retry-After, что делать при постоянной ошибке |
| 112 | 🗂️ TRACKER | [Epic][PR-4] Result model TestResult+Verdict | Эпик; дочерние #113-#116 выполнены. CHANGELOG.md:939 'TestResult dataclass + Verdict Literal (epic #112)' |
| 113 | ✅ DONE | refactor(core): TestResult dataclass + Verdict Literal | core/result.py:21 Verdict Literal; :24 @dataclass(frozen=True) TestResult со всеми полями (passed,verdict,output,expected,diff,time,memory,error,timed_out) + from_dict/to_dict |
| 114 | ✅ DONE | refactor(reporter): чтение с dict на TestResult | core/reporter.py:18 import TestResult; :416 result = TestResult.from_dict(r) в print_case_verbose |
| 115 | ✅ DONE | test(core): характеризующие тесты модели результата | tests/test_result.py: test_from_dict full AC roundtrip, WA с diff, RE с error, TLE с timed_out, инференс verdict, дефолты |
| 116 | ✅ DONE | docs(api): контракт результата CLI/Web/API | docs/result-contract.md описывает поля case result (passed/verdict/diff/error/timed_out), инвариант согласованности verdict, JSON ViewModel маппинг + to_dict; docs/api.md ссылается на него |
| 117 | 🗂️ TRACKER | [Epic][PR-5] CLI decomposition: split cli.py into cli package | src/stepik_grader/cli/ package exists with __init__.py (facade), options.py, commands.py, interactive.py, rendering.py, context.py (CliContext). cli/__init__.py docstring cites issue #117/#119. CHANGELOG.md:1121 'cli.py decomposed into a pa |
| 118 | ✅ DONE | test(cli): характеризующие тесты четырёх режимов | tests/test_cli.py has test_mode_1..4_dispatches_to_run_mode_* and test_mode_N_requires_* (lines 149-217) plus monkeypatch.setattr(cli, ...) facade patches; public-surface routing coverage present. |
| 119 | ✅ DONE | refactor(cli): выделить parsing/options из cli.py | src/stepik_grader/cli/options.py __all__ = _build_arg_parser/_resolve_verbosity/_resolve_use_cache (leaf module); cli/__init__.py re-exports them as facade names for monkeypatch compat. |
| 120 | ✅ DONE | refactor(cli): выделить commands handlers | src/stepik_grader/cli/commands.py defines _run_mode_1..4 (lines 220-471) with CliContext dependency injection; cli/context.py has class CliContext (line 36). __init__ comment cites issue #120. |
| 121 | ✅ DONE | refactor(cli): выделить rendering/user interaction | src/stepik_grader/cli/rendering.py __all__=_print_tabular/_rows_to_csv/_rows_to_markdown; interactive.py holds menu/_ask_*/i18n (Phase 2), cli/__init__ cites issue #121 Phase 2 with CliContext. |
| 122 | ✅ DONE | chore(cli): сохранить backward compatibility entrypoint main | tests/test_entrypoint.py: test_console_script_prints_version, test_module_entrypoint_prints_version, test_console_script_runs_mode_1_end_to_end; cli/__init__ remains facade with main(). |
| 123 | 🗂️ TRACKER | [Epic][PR-6] WEB MVP: Проверка решений + Глоссарий | web/ package with server.py, playground.py, glossary_adapter.py etc; CHANGELOG:948 'WEB workspace (issue #125, epic #123)'; CHANGELOG:1168 'closing epic #123'. Children #124-129 all realized. |
| 124 | ✅ DONE | feat(web): двухраздельная оболочка Проверка + Глоссарий | docs/web-design.md + docs/web-current.md document two sections and navigation (switch_section, deep-link #/glossary/<id>); app.js implements section navigation. --serve preserved. |
| 125 | ✅ DONE | feat(web): рабочая область проверки решения | web/playground.py + static/app.js (code editor CodeMirror vendored #265, run + result cards); CHANGELOG:948/962/986 detail #125 workspace with editor/test-load/result. tests/test_web_playground.py. |
| 126 | ✅ DONE | feat(glossary): JsonGlossaryProvider | src/stepik_grader/glossary/json_provider.py class JsonGlossaryProvider (line 93) with from_file/from_directory/get/all/search; detector.py MissingConceptDetector + GlossaryMissingEntry; tests/test_glossary_module.py. CHANGELOG:1225. |
| 127 | ✅ DONE | feat(web): error cards для WA/RE/TLE | static/app.js: errorCard() (line 22), verdict badges WA/FAIL/TLE (line 82-84), is_failure tagging for WA/RE/TLE (line 148), error-code map (line 1009); can link to glossary. |
| 128 | ✅ DONE | feat(web): action cards, command palette, сценарные кнопки | static/app.js: command registry (line 140), command palette Ctrl+K/⌘K (line 231, paletteCommands), action-card rendering (line 198), explain_error/Copy/Fix commands (line 176). |
| 129 | ✅ DONE | test(web): user journeys локального WEB MVP | tests/test_web_journeys.py: test_downloaded_task_is_immediately_gradable, test_re_case_glossary_id_resolves_to_real_card, test_http_grade_then_glossary_lookup_chain, test_queued_entry_visible_through_glossary_missing_adapter. CHANGELOG:1168 |
| 130 | 🗂️ TRACKER | [Epic][PR-7] SQLite persistence: история запусков и учебные данные | Epic; SQLite substance realized in src/stepik_grader/core/history.py (sqlite3, CREATE TABLE runs/case_results/lint_violations, user_version migrations, record_run) via later history epic #344/#342 (CHANGELOG:429 lists 'opt-in SQLite history **⚠ Пробел:** Original child issues #131-135 shown unchecked in epic body, but their combined substance (schema+migrations, submissions/run_results, opt-in config) is delivered by core/history.p |
| 131 | ✅ DONE | SQLite schema и migrations | src/stepik_grader/core/history.py:74 _SCHEMA_V1 (CREATE TABLE runs/case_results/lint_violations) + :116 _migrate() идемпотентная миграция user_version 0→1; opt-in db_path (не создаётся без --history). Реализовано под эпиком #342/#344, docst |
| 132 | ✅ DONE | Сохранять submissions и run_results | history.py:146 record_run() пишет runs (solution_name/hash — submission) + case_results (run_results); :199 read_recent_runs() читает локально. Подключено в cli/interactive.py:213 и web/insights_adapter.py:17. |
| 133 | ❌ NOT_DONE | Import JSON glossary cards в SQLite | grep INSERT/CREATE TABLE по glossary/ и scripts/ пуст; glossary/json_provider.py:7 явно 'SQLite отложен', :50 'JSON сейчас, SQLite позже'. history.py — SQLite для истории прогонов, не для карточек глоссария. Односторонний импорт JSON→SQLite **⚠ Пробел:** Нет производного SQLite-слоя для карточек глоссария: импорта JSON→SQLite и защиты от дублей при повторе не существует. |
| 134 | ✅ DONE | SQLite как opt-in режим | config.py:62 record_history=False по умолчанию; cli/options.py:146 флаг --history/--no-history; history.py:18-20 db_path передаётся явно, файл не создаётся без флага; place — .grader_history.db (HISTORY_DB_NAME). |
| 135 | ✅ DONE | Тесты repository/миграции | tests/test_history.py: test_record_and_read_roundtrip (happy path), test_migration_sets_user_version_and_is_idempotent (повторная миграция), test_absent_db_reads_empty_without_creating, работа на tmp_path SQLite, конкурентная запись WAL. |
| 136 | 🗂️ TRACKER | [Epic] Runner Protocol + SubprocessRunner | Эпик; суть реализована — core/runner.py (Runner Protocol + LocalRunner) + tests/test_runner.py. Дочерние #137-#140 закрыты и подтверждены кодом. |
| 137 | ✅ DONE | Runner Protocol | core/runner.py:113 class Runner(Protocol) с @runtime_checkable (:34 import Protocol,runtime_checkable); контракт через RunSpec→RunOutcome, не завязан на subprocess; реализации LocalRunner/SandboxRunner проверяемы mypy. |
| 138 | ✅ DONE | Вынести subprocess в SubprocessRunner | core/runner.py:216 class LocalRunner — subprocess.Popen обёрнут за Runner-протоколом (issue #138 в docstring). Потребители зависят от Runner: web/playground.py:62, cli/__init__.py:464. Имя 'LocalRunner' вместо буквального 'SubprocessRunner' |
| 139 | ✅ DONE | Тесты timeout/tempdir/stdout/stderr | tests/test_runner.py: test_local_runner_times_out (:245), test_local_runner_script_in_nested_tempdir (:230), test_local_runner_captures_stdout_and_returncode (:198), test_local_runner_captures_stderr_on_nonzero_exit (:210), test_local_runne |
| 140 | ✅ DONE | docs SandboxRunner без Docker | docs/server-mode.md:76 таблица с SandboxRunner (неймспейсы/seccomp/квоты, сеть выключена, tmp-каталог), :88 границы гарантий, :98-110 раздел ограничений Windows (Job Objects, нет сетевой изоляции). Дизайн-issue; позже реализовано MVP #266. |
| 141 | 🗂️ TRACKER | [Epic] Lazy config + i18n foundation | Эпик; суть реализована — config.py get_config()/PEP562 __getattr__, dataclasses.fields, core/i18n.py + locales. Дочерние #142-#145 закрыты и подтверждены. |
| 142 | ✅ DONE | Lazy get_config вместо import-time CONFIG | config.py:151 get_config() ленивый+кеш (_cached_config), :164 __getattr__ (PEP 562) вычисляет CONFIG при первом обращении, а не при импорте; load_config() читает pyproject только при вызове. Тесты отсутствия import-side-effects в test_confi |
| 143 | ✅ DONE | dataclasses.fields вместо __dataclass_fields__ | config.py:143 valid_names = {f.name for f in dataclasses.fields(GraderConfig)}; __dataclass_fields__ не используется. tests/test_config.py:200 test_dataclass_fields_matches_known_field_set, :227 test_load_config_does_not_use_dunder_dataclas |
| 144 | ✅ DONE | i18n locales/*.json | src/stepik_grader/core/i18n.py + core/locales/ru.json и en.json; web/i18n.py дополнительно. Новые сообщения добавляются через JSON без массового переписывания. |
| 145 | ✅ DONE | test(config): проверить отсутствие побочных эффектов при импорте | tests/test_config.py:241 test_bare_import_does_not_read_pyproject_toml и :256 test_config_attribute_is_lazy_and_cached — покрывают import-без-I/O и lazy load |
| 146 | 🗂️ TRACKER | [Epic] Diagnostic logging for downloader/client/oauth | Эпик; суть реализована: core/diag_log.py (redact/register_secret/get_logger/configure_diagnostics) + инструментирование downloader/stepik_client/oauth_flow; дочерние #147-#150 закрыты и подтверждены кодом |
| 147 | ✅ DONE | chore(logging): добавить logging в downloader | src/stepik_grader/downloader.py:34 импорт diag_log, :115 _log=get_logger('downloader'), :277/:315 _log.info этапов разбора URL и тест-кейсов; :327 configure_diagnostics() opt-in |
| 148 | ✅ DONE | chore(logging): добавить logging в stepik_client | core/stepik_client.py:67 _log, :475/:478/:482 логирование GET/статусов/ретраев, register_secret(access_token/client_secret/refresh_token) на :113/:239/:251 — токены маскируются, не логируются |
| 149 | ✅ DONE | chore(logging): logging в oauth_flow с редактированием секретов | core/oauth_flow.py:26/:37 логгер; redaction через diag_log; tests/test_diag_log.py:35-55 test_bearer_header_redacted/test_query_token_params_redacted/test_json_token_fields_redacted/test_registered_secret_redacted_anywhere проверяют redacti |
| 150 | ✅ DONE | docs(logging): описать диагностический режим и log file | docs/logging.md: описаны флаг --diagnostic / STEPIK_GRADER_LOG, место лог-файла (log_dir), раздел 'Редакция секретов (обязательно)' — все 3 AC покрыты |
| 151 | 🗂️ TRACKER | [Epic][PR-11] Server mode: SandboxRunner + PostgreSQL + accounts | Эпик v2.0. Дизайн-дети #152/#156/#157 закрыты как дизайн (server-mode.md + ADR-0001). Дети #153/#154/#155 открыты и не реализованы (см. выше). Сам сервер не реализован — core/runner.py и core/result.py существуют как локальный фундамент, но **⚠ Пробел:** Собственно server mode (сервер, PostgreSQL, accounts, контейнерный sandbox) не реализован; выполнена только дизайн-часть и локальные заготовки. Эпик корректно OPEN. |
| 152 | ✅ DONE | docs(server): подготовить ADR серверного режима | docs/adr/0001-server-mode.md: секции Контекст/Решение/Альтернативы/Последствия/Миграция; явный отказ от преждевременной реализации. Все AC выполнены (статус Proposed — это норма для ADR) |
| 153 | 🟡 PARTIAL | feat(server): спроектировать SandboxRunner с контейнерами и resource l | Дизайн есть в docs/server-mode.md (SandboxRunner, требования #157, фазовая миграция). Локальный MVP реализован (#266): core/sandbox/ с RLIMIT/setrlimit (_posix_common,_linux,_windows) и OS-изоляцией (namespace/seccomp в _linux). Контейнерно **⚠ Пробел:** Серверный контейнерный SandboxRunner (Docker/контейнеры + очередь/квоты server mode) не реализован — сделан только локальный opt-in MVP с rlimit и Linux-неймспейсами; в web не подк |
| 154 | ❌ NOT_DONE | feat(server): спроектировать PostgreSQL schema поверх SQLite | PostgreSQL в docs упоминается лишь как будущая эволюция (docs/audit-2026-07.md:373,520 «PostgreSQL — только как эволюция схемы, issue #154»), самой схемы/дизайна нет. Локально существует SQLite (core/history.py, core/cache.py, core/stats.py **⚠ Пробел:** PostgreSQL schema поверх существующей SQLite-модели не спроектирована. Issue легитимно OPEN (v2.0-дизайн, не начат). |
| 155 | ❌ NOT_DONE | feat(server): спроектировать accounts/workspaces/courses модель | grep по docs/server-mode.md и docs/adr/0001-server-mode.md не находит ни account/Account, ни workspace/Workspace, ни модели курсов. server-mode.md проектирует только Runner(#140)/API(#156)/sandbox(#157). Дизайна моделей аккаунтов/воркспейсо **⚠ Пробел:** Модели user/account, workspace/course/task и прав доступа не спроектированы. Issue легитимно OPEN (v2.0-дизайн, не начат) — это не аудит-дефект закрытой issue, а нереализованная от |
| 156 | 📐 DESIGN_ONLY | feat(api): описать API contract для удалённого запуска кода | docs/server-mode.md:136 'Контракт API удалённого исполнения (#156)' с разделами Запрос/Жизненный цикл/Классы ошибок (:188, quota_exceeded 429). Контракт реализован частично: endpoint POST /api/v1/runs документирован (docs/api.md:327) и живё **⚠ Пробел:** Полный удалённый серверный API (#156-версия с очередью/квотами поверх SandboxRunner) не реализован — только локальный /api/v1/runs; это by-design (закрыто как дизайн) |
| 157 | 📐 DESIGN_ONLY | security(server): ограничения network-off, temp dirs, quotas | docs/server-mode.md:214 'Sandbox и сетевая изоляция (#157)' + фазовая миграция :252 (сеть off, tmp, квоты). Реализован локальный MVP: core/sandbox/ + --sandbox (issue #266), SECURITY.md **⚠ Пробел:** Серверные quotas/rate limits поверх API не реализованы (сам server/очередь отсутствуют) — осознанно, дизайн |
| 161 | 🗂️ TRACKER | [Epic] Versioning: единый источник истины + видимость dev/release | Эпик; все дочерние #162-#166 закрыты и подтверждены: динамическая версия (pyproject dynamic), dev/release маркер (cli/__init__.py), CI check_version_consistency |
| 162 | ✅ DONE | chore(version): динамическая версия из git-тегов | pyproject.toml:13 dynamic=['version'], :72 [tool.setuptools_scm], статической строки нет; scripts/check_version_consistency.py:74 _check_pyproject_dynamic; tests/test_cli.py:47 test_version_is_dynamic_in_pyproject |
| 163 | ✅ DONE | feat(version): различать dev и release в выводе --version | cli/__init__.py:115 _is_dev_build, :126 _format_version_for_display добавляет '(dev build, not a release)' off-tag; tests/test_cli.py:90/:94/:101 покрывают обе ветки on-tag/off-tag/dirty |
| 164 | ✅ DONE | docs(versioning): расширить CONTRIBUTING §Версионирование | CONTRIBUTING.md:277-278 таблица Release vs Dev с примерами 1.5.0 и 1.5.0.post3+g1a2b3c4; docs/versions.md:6/:44 ссылается на CONTRIBUTING; отдельного docs/versioning.md нет (проверено) |
| 165 | ✅ DONE | ci(release): check-version-consistency + исправить дрейф | scripts/check_version_consistency.py (baseline из git describe, проверка CHECKPOINT/CHANGELOG/CLAUDE/pyproject); tests/test_check_version_consistency.py:55 test_checkpoint_drift_is_flagged (намеренный дрейф падает); CLAUDE.md:178 ручная све |
| 166 | ✅ DONE | ci(readme): авто-бейдж релиза из git-тегов | README.md:4 `[![Release](https://img.shields.io/github/v/release/ArtVsMark/...)]` — авто-бейдж последнего релиза; строка `version-X.Y.0` ручная отсутствует (Version-бейдж строка 5 — тоже endpoint-автобейдж, не ручная). |
| 167 | 🗂️ TRACKER | [Epic] Documentation split phase 2: README как витрина | Эпик #97-child. Все дочерние (#168-#173,#178) реально закрыты; README.md=131 строк (< 220 budget), витрина с бейджами и разделом возможностей; docs/ содержит installation/grader-workflow/configuration/README index. |
| 168 | ✅ DONE | docs(install): создать docs/installation.md | docs/installation.md (339 строк) существует; README ссылается на installation guide, OAuth/secrets.json/диагностика вынесены. |
| 169 | ✅ DONE | docs(workflow): создать docs/grader-workflow.md | docs/grader-workflow.md (589 строк) существует; описывает режимы, скачивание, CLI-флаги, вердикты AC/WA/TLE/RE. |
| 170 | ✅ DONE | docs(architecture): перенести таблицу «Что умеет» в docs/architecture. | docs/architecture.md:8 `## Что умеет (модули и слои)` + таблица `\| Модуль \| Слой \| Что делает \|` (строка 13); grep по README.md `что умеет` — 0 совпадений (не дублируется). |
| 171 | ✅ DONE | docs(reference): создать docs/configuration.md | docs/configuration.md (319 строк) — конфигурация, форматы тест-кейсов, ограничения, security; канонический источник форматов (ссылается CLAUDE.md). |
| 172 | ✅ DONE | docs(index): docs/README.md + перекрёстные ссылки + сжать README | docs/README.md (70 строк) — индекс, 48 markdown-ссылок; README.md сжат до 131 строки с бейджами и возможностями. |
| 173 | ✅ DONE | chore(docs): README line-budget + markdown link-check в CI | scripts/check_docs_guardrails.py: README_LINE_BUDGET=220, check_readme_budget/check_markdown_links/check_docs_index_completeness; подключён в .github/workflows/ci.yml:22 `python scripts/check_docs_guardrails.py`; правило документировано в C |
| 174 | 🗂️ TRACKER | [Epic] CLAUDE.md hygiene: инварианты в корне, история в docs | Эпик. Дочерние #175/#176/#177/#178 закрыты; CLAUDE.md остаётся в корне как агентский контракт со ссылками на docs/history.md. |
| 175 | ✅ DONE | docs(claude): сократить CLAUDE.md до агентских инвариантов | CLAUDE.md — секции запретов/стиля/команд/инвариантов; длинная история вынесена (шапка строки 3-6 явно указывает: 'История спринтов ... вынесены в docs/history.md'). Файл — контракт со ссылками, не история. |
| 176 | ✅ DONE | docs(history): вынести sprint/roadmap history в docs/history.md | docs/history.md (409 строк) существует; docs/history.md:5 отмечает 'вынесено по issue #176 / эпик #174'; CLAUDE.md ссылается (строки 5,239), не копирует. |
| 177 | ✅ DONE | docs(claude): обновить рабочую ветку и versioning rules под PR-12 | CLAUDE.md § Версионирование: 'Ручную сверку версий делать не нужно ... CI (scripts/check_version_consistency.py)'; § Рабочая ветка актуальна (ветка от main → PR); ссылки на CONTRIBUTING/docs/versions.md. |
| 178 | ✅ DONE | docs(md): аудит Markdown-дублей и перекрёстных ссылок | CLAUDE.md § «Источники истины» — таблица канонических документов; check_docs_guardrails.py link-check + docs-index-completeness машинно защищают от дублей/битых ссылок. |
| 186 | ✅ DONE | feat(web): Downloader workflow для загрузки тестов Stepik | src/stepik_grader/web/downloader_adapter.py::download_task; web/server.py:369 endpoint `/api/download`; тесты tests/test_web_downloader.py покрывают success/no-tests/oauth-unavailable/network-error/bad-url; CHANGELOG.md:892 issue #186. Ссыл |
| 187 | ✅ DONE | feat(web): microbench / режим 4 в WEB UI | web/static/app.js:410,424,771,1605,1823 (renderMicrobench, режим 4 selector, POST /api/v1/runs async); index.html:53 кнопка 'Режим 4 Microbench'; web/__init__.py экспортирует grade_microbench; тесты tests/test_web_journeys.py, tests/test_ru |
| 190 | ✅ DONE | fix(glossary): validate MissingEntry kind/status on load | glossary/models.py:272-291 from_dict валидирует kind/status/origin по frozenset _MISSING_KINDS/_MISSING_STATUSES/_MISSING_ORIGINS с ValueError включающим имя поля и значение; тесты test_glossary_module.py from_dict/round-trip |
| 191 | ✅ DONE | fix(glossary): reduce false positives in exception-name detector | glossary/detector.py:402 _EXCEPTION_NAME_SUFFIXES=('Error','Exception','Warning'), :410-432 _last_exception_name ограничивает матчинг; тест test_glossary_module.py:510 test_detector_from_error_ignores_capitalized_word_false_positive проверя |
| 194 | ✅ DONE | docs(glossary): document official Python as glossary source of truth | docs/glossary.md:26-27 таблица источников истины (эталон полноты — официальный Python/stdlib; Glossary-Python — витрина, никогда не эталон); :33 practice-driven vs source-driven; :141-145 формат origin/module/qualname |
| 195 | ✅ DONE | refactor(glossary): add origin and module fields to GlossaryMissingEnt | glossary/models.py:256-258 origin/module/qualname поля с дефолтами; :284-305 from_dict/to_dict round-trip обратносовместимо; detector.py:502,536 проставляет origin='solution'/'error'; docs/glossary.md:141-145 |
| 196 | ✅ DONE | feat(glossary): stdlib inventory scanner | glossary/stdlib_inventory.py — leaf (только builtins/importlib/inspect/sys), build_stdlib_inventory, __all__, from __future__, docstrings; тест tests/test_glossary_stdlib_inventory.py |
| 197 | ✅ DONE | feat(glossary): coverage report and missing JSON generator | glossary/coverage.py build_coverage_report/missing_entries_from_inventory с origin='stdlib_scan', append_missing_entries идемпотентен; тесты test_glossary_coverage.py:50 origin, :149 test_missing_entries_write_is_idempotent |
| 198 | ✅ DONE | feat(glossary): CLI and menu entrypoint for coverage scan | glossary/coverage.py:241 _build_arg_parser с --cards/--missing-out/--modules, main([]); python -m stepik_grader.glossary.coverage; тесты test_glossary_coverage_cli.py:33 smoke, :54 writes JSON, :64 idempotent |
| 199 | ✅ DONE | docs(architecture): register glossary coverage modules in DAG | docs/architecture.md:72-73 stdlib_inventory.py/coverage.py как Domain(leaf) с DAG-описанием :137-156; docs/project-structure.md:86-87 оба файла добавлены |
| 200 | ✅ DONE | docs(claude): glossary coverage handoff and invariants | CLAUDE.md § Истина глоссария (инвариант: полнота по официальному Python/stdlib, Glossary-Python — только витрина); docs/claude-handoff.md:160-166 handoff-блоки #195-#199 помечены закрытыми |
| 201 | ✅ DONE | docs(security): add SECURITY.md | SECURITY.md в корне: Поддерживаемые версии (без хардкода), GitHub Private Vulnerability Reporting + @ArtVsMark fallback, ссылка на docs/configuration.md вместо дублирования threat model; README.md:119 и docs/README.md:29 ссылаются |
| 202 | ✅ DONE | docs(github): PR and issue templates | .github/PULL_REQUEST_TEMPLATE.md (checklist: Closes#, тесты, docs, changelog, секреты); .github/ISSUE_TEMPLATE/bug_report.md, feature_task.md, config.yml |
| 204 | ✅ DONE | docs(community): decide on CODE_OF_CONDUCT.md | Решение 'add now' реализовано: CODE_OF_CONDUCT.md (Contributor Covenant) в корне; связан из CONTRIBUTING.md:6; CHANGELOG.md:1164 фиксирует добавление |
| 213 | ✅ DONE | docs(executor): explicit no-OS-sandbox warning | core/executor.py:9-18 module docstring 'Безопасность (нет OS-sandbox)' уточняет что 'isolated' = только subprocess/namespace, не OS-изоляция, запускать только доверенные решения; :155-156 main() docstring повторяет; согласовано с README/web |
| 214 | ✅ DONE | Harden client-side attribute escaping in web UI | src/stepik_grader/web/static/app.js:24 HT map includes '"':'&quot;' and "'":'&#39;'; esc() used in href/action-card attrs; comment cites #214. Regression test in tests/test_web.py |
| 215 | ✅ DONE | Sync small v1.6.0 documentation drift | README.md:40-41 marks --watch as optional requiring extra [watch]/watchfiles; docs drift items addressed |
| 231 | ✅ DONE | PATCH counter double-counts badge-bot commits | scripts/version.py:35,70,78-81 uses --invert-grep --grep='chore(ci): update badges' --fixed-strings; consumed by generate_version_badge.py; CHANGELOG.md:1016-1019 records fix |
| 240 | ✅ DONE | Do not send Authorization to external URLs | src/stepik_grader/core/stepik_client.py:47-51,132-213 EXTERNAL_DOWNLOAD_ALLOWED_HOSTS (github.com/raw.githubusercontent.com), validate_external_url rejects private/loopback via ipaddress, external_download_get uses separate unauth session;  |
| 241 | ✅ DONE | Add state to OAuth callback flow | stepik_client.py:374 state=secrets_module.token_urlsafe(32); :286-292 callback rejects mismatched state as state_mismatch; tests test_oauth_flow.py:219,296 cover happy path + invalid/missing state |
| 242 | ✅ DONE | Validate Host/Origin for localhost API | src/stepik_grader/web/server.py:614-647 Host/Origin/Referer guard for /api/* returning 403 (issue #242, F-03); DNS-rebinding + cross-site protection |
| 243 | ✅ DONE | Restrict secrets.json file permissions | src/stepik_grader/core/storage.py:65-68 os.open with 0o600 atomically + forced chmod 0o600; Windows behavior documented in docstring; chmod tests in test_storage.py |
| 244 | ✅ DONE | Import stdlib before sys.path.insert in function wrapper | src/stepik_grader/core/wrapper_builder.py:54-56 stdlib imports emitted before sys.path.insert (issue #244, F-05); regression test test_grader_core.py:332 test_build_function_wrapper_not_shadowed_by_local_datetime_module |
| 245 | ✅ DONE | Safely parse EXECUTOR_TIMEOUT from env | src/stepik_grader/core/executor.py:56-77 _parse_executor_timeout wraps int() in try/except ValueError with default fallback; test in tests/test_executor.py |
| 246 | ✅ DONE | Validate input/output block count mismatch in test-loader | src/stepik_grader/core/test_loader.py:152-156 warns when len(input_blocks)!=len(output_blocks) (issue #246, F-07); tests test_grader_core.py:203,222 cover mismatch warning and equal-count no-warn |
| 247 | ✅ DONE | Sync architecture DAG with web/ and cli/ structure | docs/architecture.md:26-28 lists web/server.py, web/viewmodels.py, web/downloader_adapter.py; :18-21,100-105 lists cli/interactive.py, cli/rendering.py with DAG edges and leaf annotations |
| 248 | ✅ DONE | Update CLAUDE.md test metrics and invariants | CLAUDE.md:320-326 metrics table maintained (pegged to release snapshot v1.8.0, tests/coverage present); version-consistency guard referenced; metrics section actively updated past the v1.6 era the issue targeted |
| 258 | ✅ DONE | config.py finds pyproject.toml under wheel/pipx install | src/stepik_grader/config.py:29,83-114 _find_pyproject walks from cwd upward, STEPIK_GRADER_CONFIG env override, legacy __file__ fallback; docs/configuration.md:53-62 documents env→cwd-search→legacy order; tests in test_config.py |
| 259 | ✅ DONE | API input limits: body size and numeric params | src/stepik_grader/web/server.py:102-103 _MAX_BODY_BYTES=1MiB, _REPEATS_RANGE=(1,1000); :441-460 returns 413 body_too_large; :237-240,532-536 _clamp on repeats/number; :680 _clamp helper; negative tests in tests/test_web.py |
| 260 | ✅ DONE | Вендоринг шрифтов JetBrains Mono/Inter вместо Google Fonts CDN | static/fonts/ содержит 4 woff2 + LICENSE; @font-face в app.css; grep CDN в index.html пуст; pyproject package-data 'web/static/fonts/*' (стр.94); tests/test_web.py:800-813 проверяет Content-Type font/woff2 и отсутствие googleapis/gstatic |
| 261 | ✅ DONE | workspace root (--root) конфайнмент путей | web/server.py:129 _resolve_within_root с is_relative_to (стр.146), _confined_path (589); cli/options.py:184 --root, :193 --no-root-confinement; server.workspace дефолт cwd (719) |
| 262 | ✅ DONE | job model POST /api/v1/runs + polling, /api/grade deprecated | web/runs.py (Job/submit_job/cancel_job, ThreadPoolExecutor, 5 статусов); server.py:330 POST /api/v1/runs, :333 /cancel, :198 GET; app.js:889-999 async polling+cancel; /api/grade помечен sync-обёрткой (server.py:217) |
| 263 | ✅ DONE | Playwright-смоук + XSS-регресс | tests/e2e/ (test_journeys.py, test_xss_regression.py, conftest, _helpers); pyproject e2e extra playwright>=1.40 (стр.38); ci.yml:249 отдельный job e2e с playwright-cache |
| 264 | ✅ DONE | i18n каталог message_id вместо русских строк в viewmodels | core/locales/ru.json(8.7K)/en.json(6.2K) наполнены; scripts/check_locale_guardrails.py существует; viewmodels отдаёт message_id/message_params; tests/test_web.py:1279 тест ?lang=en; docs/result-contract.md:149 раздел локализации |
| 265 | ✅ DONE | CodeMirror 6 вместо textarea | static/vendor/codemirror-bundle@6.mjs + VERSIONS.md + LICENSE; app.js:13-18 импорт EditorState/EditorView, :448 фабрика редактора; package-data 'web/static/vendor/*' (pyproject:95); index.html:415 комментарий про версию |
| 266 | ✅ DONE | SandboxRunner MVP (Linux bubblewrap) opt-in --sandbox | core/sandbox/__init__.py:45 class SandboxRunner; _linux.py bwrap+RLIMIT; cli/options.py:202 --sandbox; tests/test_sandbox_runner.py:214 write-escape, :224 network, :237 fork-bomb, TLE/MLE тесты; SECURITY.md/docs/server-mode.md обновлены |
| 267 | ✅ DONE | docs/api.md + разделение web-mvp на current/design | docs/api.md, docs/web-current.md, docs/web-design.md, docs/changelog-archive.md существуют; docs/web-mvp.md удалён; CHANGELOG исторические снимки перенесены в changelog-archive.md (CHANGELOG.md:600-607) |
| 268 | ✅ DONE | локальная opt-in статистика запусков | core/stats.py (record_run); cli/options.py:125 --stats, :136 --stats-summary, :255 _resolve_record_stats с pyproject-дефолтом; commands.py:256/337 record_stats по режимам; tests/test_stats.py; путь .grader_stats.jsonl в запретах CLAUDE.md |
| 270 | ✅ DONE | test_pytest_plugin/test_packaging падают в нестандартном pytest-окруже | docs/installation.md:319-339 troubleshooting-раздел (pytest-of-user, --basetemp, --grader-mode, License-Expression); CHANGELOG:607 запись — все 3 находки подтверждены как stale editable install, чинятся --force-reinstall, не баг репо |
| 283 | ✅ DONE | cross-OS combined coverage badge + gate | ci.yml:165 job coverage-combine, :194 coverage combine, :196 --fail-under=90, :113 include-hidden-files; pyproject:127 [tool.coverage.paths]; README.md:6-7 два бейджа (ubuntu + all OS combined) |
| 286 | ✅ DONE | coverage-combined.json никогда не коммитился (git diff --quiet) | ci.yml:134/214 'git add .github/badges/' перед :140/221 'git diff --cached --quiet' — исправлено в обоих badge-степах |
| 289 | ✅ DONE | два coverage-бейджа с одинаковым label | generate_coverage_badge.py:76 build_badge_payload(label=...), :109 --label флаг; ci.yml:132 --label 'coverage (ubuntu)', :213 --label 'coverage (all OS)' |
| 293 | ✅ DONE | докстринг core/sandbox/__init__.py в соответствие с реализацией (nsjai | __init__.py:16 'nsjail как fallback — не реализован'; _linux.py:149 текст SandboxUnavailableError 'nsjail fallback не реализован в этом MVP'; SECURITY.md:134 и docs/server-mode.md:85 согласованы — выбран вариант (а) |
| 294 | ✅ DONE | ci: запас покрытия для платформо-специфичных sandbox-бэкендов vs fail_ | pyproject.toml:141-166 base omit list с комментарием issue #294 + fail_under=85; scripts/generate_ci_coveragerc.py per-OS coveragerc omits чужие sandbox backends (comment cites AC 'без двойного исключения'); CHANGELOG.md:447 |
| 295 | ✅ DONE | refactor(frontend): единый vendored CodeMirror бандл без node-шимов | web/static/vendor/codemirror-bundle@6.mjs (единый 360КБ файл), VERSIONS.md с esbuild-рецептом; index.html:414-419 комментарий подтверждает удаление importmap + node-shims; app.js импортит по URL напрямую |
| 296 | ✅ DONE | feat(web): отдельный статус cancelled в /api/v1/runs | web/runs.py:56 _STATUSES включает 'cancelled', :283 job.status='cancelled'; тесты test_runs.py:132/155/237; docs/api.md:376 контракт обновлён |
| 297 | ✅ DONE | feat(web): режим 1 через POST /api/v1/runs, dirty-индикатор, optimisti | app.js:59-60 baseline mtime, :712 markEditorSaved, :806 expected_mtime optimistic locking; save отделён от grade; CHANGELOG.md:221 |
| 298 | ✅ DONE | fix(a11y): aria-live для вердиктов, role=progressbar | index.html:166 aria-live result-announce; app.js:982-990 role=progressbar + aria-valuemin/max/now; CHANGELOG.md:196 |
| 299 | ✅ DONE | fix(core/web): статус 'тесты не найдены' вместо FAIL 0/0 | viewmodels.py:660/681 message_fields('tests_not_found_for'); locales ru/en содержат tests_not_found_for/short; cli/commands.py:516 симметрия; CHANGELOG.md:408 |
| 300 | ✅ DONE | docs: changelog-archive.md в индекс + guardrail полноты индекса | docs/README.md:26/60 changelog-archive упомянут; check_docs_guardrails.py check_docs_index_completeness (issue #300); tests test_check_docs_guardrails.py:117/125 (позитив+негатив) |
| 301 | ✅ DONE | feat(web): подсказка при не-UTF-8 байтах в выводе | viewmodels.py:142-147 '�'→render_message('output_invalid_utf8'); locales ru/en; тесты test_web.py:189/274/289 |
| 302 | ✅ DONE | refactor: SRP-разбиение downloader.py | downloader.py=15300 байт (14.9 KiB < 15КБ AC); созданы core/task_page_parser.py (extract_external_test_links) и core/tests_writer.py; CHANGELOG.md:117 |
| 303 | ✅ DONE | analysis: инвентаризация open issues + сверка с аудитами | Analysis-задача с deliverable-комментарием в meta-issue (или опц. docs-файл). В репо issues-sync-файла нет; docs/audit-2026-07.md — это отдельный 8-ролевой аудит. Deliverable — issue-комментарий, из репо не верифицируется **⚠ Пробел:** Файл docs/audit/issues-sync-2026-07.md отсутствует; классификация open issues, если сделана, живёт в GitHub-комментарии |
| 304 | ✅ DONE | analysis: обновить переменные project-audit-prompt.md | project-audit-prompt.md не хранится в этом репо (внешний файл промтов, по решению владельца). Обновление переменных/истории запусков не верифицируется из клона **⚠ Пробел:** Файла нет в репо — обновление, если сделано, во внешнем расположении |
| 314 | 🗂️ TRACKER | эпик: раздел 'Песочница' — код+stdin+вывод, пошаговое исполнение | Подзадачи реализованы: web/playground.py, core/tracer.py (settrace→JSON-трейс), UI-плеер (app.js trace-step-label); CHANGELOG.md:282/293/308/319 (#317-#321) |
| 315 | 🗂️ TRACKER | эпик: 'Функции в коде' — мини-карточки глоссария в режимах 1/2 | Endpoint /api/code-terms реализован: glossary_adapter.py:231 code_terms → detect_from_code; server.py:345 маршрут; CHANGELOG.md:252 (#322-#324) |
| 316 | 🗂️ TRACKER | эпик: контент-план глоссария — паритет полей, импорт 581, stdlib-полно | Подзадачи: glossary/models.py:98-101 поля docs_url/subcat (#325); scripts/import_glossary_python.py (#326), generate_draft_cards.py (#328); stdlib_inventory расширения |
| 317 | ✅ DONE | Песочница MVP — редактор + stdin + вывод | web/playground.py существует; web/runs.py:160,217 kind=="playground"; index.html:34 sidebar «Песочница»; tests/test_web_playground.py; CHANGELOG.md:319 |
| 318 | ✅ DONE | Трассировщик sys.settrace → JSON-трейс | core/tracer.py: settrace, _encode heap по obj_id, max_steps/truncated, run_trace() возвращает {steps,stdout,truncated,error}; tests/test_tracer.py; CHANGELOG.md:308 |
| 319 | ✅ DONE | Пошаговый плеер в песочнице | app.js:1084 mode="trace", showTracePlayer/tracePlayerShell/renderTraceStep (1186-1233), контролы ⏮◀▶⏭ + слайдер, подсветка строки; CHANGELOG.md:293 |
| 320 | ✅ DONE | Визуализация связей переменных (aliasing/heap) | app.js:1200 view table\|diagram, renderTraceDiagram (1439+), SVG mem-arrows, fmtRef/fmtObj по heap-id; CHANGELOG.md:281 |
| 321 | ✅ DONE | Интеграция глоссария в песочницу | app.js:367 loadCodeTerms обновляет «Функции в коде», trace-error-link (1142) error card deep-link #/glossary/; glossary_adapter.code_terms; CHANGELOG.md:268 |
| 322 | ✅ DONE | Endpoint /api/code-terms — AST → термины | detector.scan_code_concepts + CODE_TERM_BUILTINS, glossary_adapter.py:231 code_terms() возвращает {id,title,summary,kind,has_card,url,confidence}; server.py:345 POST /api/code-terms; queue_code_gaps practice-канал; tests/test_web_glossary.p |
| 323 | ✅ DONE | Панель «Функции в коде» в режимах 1/2 | index.html:136-140 замена мёртвого «Связанные термины» на «Функции в коде»; app.js:490 debounce checkTermsTimer, loadCodeTerms, режим 2 по {path} (app.js:2363); CHANGELOG.md:253 |
| 324 | ✅ DONE | Скрыть глоссарий в режимах 3/4 | app.js:414 «issue #324/#366: Функции в коде — только режим 1»; index.html:137 «Скрыт в bench/microbench»; CHANGELOG.md:257 panel hidden in modes 3/4 |
| 325 | ✅ DONE | GlossaryCard += syntax/docs_url/version/subcat | models.py:95-101 новые поля, from_dict:168-177 (docs алиас docs_url), to_dict:211-217; CHANGELOG.md:385 |
| 326 | ✅ DONE | Импортёр 581 карточки из Glossary-Python | scripts/import_glossary_python.py; glossary/data/*.json (~601 non-draft cards в builtin/str/seq/op/module/... ); tests/test_glossary_import.py; CHANGELOG.md:374 |
| 327 | ✅ DONE | Инвентаризация методов встроенных типов | stdlib_inventory.py:38 InventoryKind+"method", _type_method_items (174), qualname str.split; coverage.py:56 CATEGORIES включает "methods", _is_known спец. по qualname (72,82); tests/test_glossary_stdlib_inventory.py |
| 328 | ✅ DONE | Генератор draft-карточек из офиц. документации | scripts/generate_draft_cards.py (inspect.signature/getdoc, docs_url шаблоны, идемпотентно); glossary/data/drafts.json (787 черновиков status=draft); tests/test_glossary_draft_gen.py; CHANGELOG.md:333 |
| 329 | ✅ DONE | Редизайн раздела «Глоссарий» — фильтры/сортировка/deep-link | glossary_adapter.py:40 _SORTS az/section/version + _sort_cards; server.py:245-254 /api/glossary параметры section/kind/status/sort; index.html:232 glossary-filters chip-row; app.js deep-link #/glossary/; CHANGELOG.md:356 |
| 330 | ✅ DONE | Устранить дрейф docs web (4 режима, web.py стейл-ссылки) | grader-workflow.md:126 «все четыре режима» + «Загрузчик/Глоссарий/Песочница»; core/glossary.py:7 исправлено на «(пакет web/)» без web.py; CHANGELOG.md:188. Остаточные web.py в docs/history/archive/result-contract — исторические, вне scope |
| 331 | ✅ DONE | fix(web): UI-нестыковки — switch_section, бейдж CANCELLED, aria | app.js:181-184 циклическое переключение по order=[check,downloader,glossary,sandbox]; app.js:90-91 VERDICT_BADGE знает CANCELLED (neutral) и SANDBOX_VIOLATION (error); CHANGELOG:179-183 фиксирует все три находки, включая aria-disabled для в |
| 339 | ✅ DONE | perf(web): кешировать _all_cards глоссария по mtime | web/glossary_adapter.py:18 импорт MtimeCache, _all_cards() (стр.82-89) кеширует по mtime источника; CHANGELOG:211 подтверждает memoization keyed by mtime |
| 342 | 🗂️ TRACKER | [Epic] Учебные инсайты: Правила/PEP + Подучить на SQLite | Эпик; все дочерние #344-#349 реализованы (core/history.py, rules/, core/lint.py, core/insights.py, /api/rules+/api/insights, --insights/--lint). CHANGELOG:13-19 закрывает эпик #342 |
| 343 | 🗂️ TRACKER | [Epic] Гигиена по аудиту 2026-07 + релиз v1.8.0 | Эпик гигиены; дочерние #350/#351/#352/#353 реализованы (reporter fix, sandbox+serve error, threading.Lock, docs). Версия 1.8.0 в CHANGELOG/CLAUDE |
| 344 | ✅ DONE | feat(history): core/history.py SQLite-история (схема v1, WAL, миграции | src/stepik_grader/core/history.py существует; tests/test_history.py; CHANGELOG:13 — схема v1 runs/case_results/lint_violations, WAL, user_version-миграции, opt-in --history |
| 345 | ✅ DONE | feat(rules): пакет rules/ — карточки PEP 8 | src/stepik_grader/rules/ (models.py, json_provider.py, data/pep8_ru.json=36 карточек ≥30); mtime-кеш вынесен в core/mtime_cache; tests/test_rules.py; CHANGELOG:14 |
| 346 | ✅ DONE | feat(lint): core/lint.py + opt-in extra [lint] (ruff) | src/stepik_grader/core/lint.py; tests/test_lint.py; CHANGELOG:15 — opt-in extra [lint], best-effort, не влияет на вердикт, поле lint в контракте результата |
| 347 | ✅ DONE | feat(insights): таксономия падений + затухание карточек | src/stepik_grader/core/insights.py (failure_kind, classify_status, learning_cards); cli/commands.py:99 использует insights.failure_kind; tests/test_insights.py; CHANGELOG:16 — active/fading/archived по номерам прогонов, пороги N/T/K |
| 348 | ✅ DONE | feat(web): разделы Правила/Подучить + /api/rules, /api/insights | web/server.py:276-286 эндпоинты /api/rules, /api/insights, /api/rules/{code}; web/rules_adapter+insights_adapter; deep-link #/rules/<code>, единый hash-роутер; tests/test_web_rules_insights.py; CHANGELOG:18-19 |
| 349 | ✅ DONE | feat(cli): stepik-grader insights + флаг --lint | cli/__init__.py:437 args.insights + insights.learning_cards; interactive.py:247-260 пункт меню 5 Подучить; cli/commands.py lint-блок; tests/test_cli_insights.py; CHANGELOG:17 |
| 350 | ✅ DONE | fix(reporter): битый TYPE_CHECKING-импорт TestCase=Any | core/reporter.py:21 — 'from stepik_grader.core.grader_core import TestCase' (полный префикс, было core.grader_core); CHANGELOG:171 |
| 351 | ✅ DONE | fix(cli): --sandbox молча игнорируется при --serve | cli/__init__.py:461-468 — при args.serve и args.sandbox вызывается parser.error(_t('sandbox_serve_unsupported')) до возврата; CHANGELOG:164 |
| 352 | ✅ DONE | fix(core): гонки файловых записей (stats-ротация, очередь глоссария) | core/stats.py:49 _WRITE_LOCK=threading.Lock(); glossary/json_provider.py:227 _MISSING_QUEUE_LOCK=threading.Lock() с docstring о межпроцессной защите через #344; CHANGELOG:153-161 |
| 353 | ✅ DONE | docs: дрейф документации по аудиту 2026-07 | CLAUDE.md:131 инвариант №4 переформулирован 'Sandbox — только opt-in'; docs/project-structure.md:71-72,34 добавлены diag_log.py/tracer.py/playground.py; docs/rules-insights.md существует; CHANGELOG:132 |
| 354 | ✅ DONE | Гигиена по аудиту — __all__×8, print→_console, relpath→pathlib, дубли | __all__ присутствует во всех 8 модулях (grep -c =1 в downloader/diagnostic/pytest_plugin/executor/stepik_client/storage/microbench_runner/parsers); relpath→Path.relative_to(walk_up=True) в cli/commands.py:57, reporter.py:112/139/218/266, te |
| 355 | ✅ DONE | Два i18n-механизма → один | cli/__init__.py:149-152 _LOCALE_MESSAGES грузится через load_locale_messages из JSON-локалей; _t() (155-164) — тонкая обёртка; комментарий 144 «_MESSAGES слит в JSON (issue #355)»; guardrail check_locale_guardrails.py |
| 356 | ✅ DONE | Два глоссария → CLI-подсказки при RE из общей JSON-базы | core/error_glossary.py:50 card_url() единая стратегия URL; _bundled_index() (66-94) лениво грузит JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR); resolve_error_hint (99+) фолбэк на компактную карту; reporter.py:425 использует res |
| 357 | ✅ DONE | Заменить sleep-поллинг в web-тестах детерминированными ожиданиями | tests/_wait.py wait_until[T] helper; используется в test_web.py:1382/1474/1486/1630 и test_web_playground.py:96 (0 sleep в playground); оставшиеся time.sleep — только в телах решений-фикстур (sleep(30) TLE-кейсы и sleep(0.05) внутри _make_t |
| 358 | ✅ DONE | Выпустить v1.8.0 | CHANGELOG.md:58 '## [1.8.0] - 2026-07-14', новый пустой [Unreleased] сверху (стр.3); CLAUDE.md § Метрики 'Версия 1.8.0'; отсутствие git-тега в клоне — артефакт клона, не дефект |
| 362 | 🗂️ TRACKER | [Epic] UX-оптимизация раздела «Проверка решений» | Эпик; все дочерние #364-#370 закрыты и подтверждены кодом; CHANGELOG [Unreleased] строки 24-34 покрывают всю цепочку а–к; контракты /api не сломаны (аддитивно) |
| 363 | 🗂️ TRACKER | [Epic] довести 832 draft-карточки до ready — волны В1–В6 | Эпик. Фактически drafts.json = 787 draft-карточек (Counter({'draft':787})); из исходных 832 доведено 45 (str, #371). Фундамент двуязычных карточек и генератора реализован (CHANGELOG.md:20-21, glossary/data/*.json). **⚠ Пробел:** 787 из 832 карточек ещё в статусе draft; волны В2–В6 не выполнены. Эпик корректно OPEN. |
| 364 | ✅ DONE | Sidebar-чистка, раздел «Настройки», недавние пути → подсказки | index.html:36-38 рабочий раздел «Настройки» (a data-section=settings) вместо disabled-заглушки; метка «Рабочее пространство» убрана; index.html:71/76 datalist recent-paths-datalist у поля #path; страница «Настройки» index.html:374 |
| 365 | ✅ DONE | Убрать блок «История» из конфиг-панели | app.js: 0 вхождений grader_history/renderHistory/addHistoryEntry (мёртвый код удалён); оставшиеся «История» в index.html:398-399 — это заглушка «История прогонов (#342)» внутри раздела «Настройки», а не блок конфиг-панели |
| 366 | ✅ DONE | Конфиг-панель по режимам — параметры инлайн, «Функции в коде» только р | app.js: 0 вхождений configTab/setConfigTab/updateParamsTabAvailability (конфиг-вкладки удалены); единственный panel-tabs в index.html:150 — это result-панель (Таблица/Разбор), не конфиг-панель; CHANGELOG стр.25 |
| 367 | ✅ DONE | Полнота «Функций в коде» — инвентарь, конструкции, fix os.path.join | detector.py:358 scan_code_concepts(methods=,notable_builtins=); construct-детекторы 302-339 (comprehensions/lambda/ternary/walrus/f-string/slice/unpacking/decorator); glossary_adapter.py:209-247 _inventory_sets из build_stdlib_inventory с _ |
| 368 | ✅ DONE | Объединить «Детали»+«Лог» в «Разбор»; действия по режимам | app.js:385 '-- Result-panel tabs (Таблица / Разбор)'; restab-log/renderLogTab удалены (лишь комментарии #368); index.html:152 две вкладки Таблица/Разбор; app.js:213-214 режим-2 action cards = только copy input/output; CHANGELOG стр.26 |
| 369 | ✅ DONE | Убрать вкладку «Эталон» — REFERENCE-строка таблиц 3/4 | 0 вхождений restab-reference/renderReferenceTab/Эталон в app.js и index.html; бэкенд не тронут — viewmodels.py:471 _apply_reference_ranking + grade_benchmark(reference=) сохранены; disabled find-reference-btn index.html:110 оставлена по пла |
| 370 | ✅ DONE | Режимы 3/4 — вертикальная раскладка + унификация таблиц с CLI | split-pane--stacked в app.js + app.css (5 hits); renderBench app.js:1772 полный набор колонок Файл/Runs/Min/Median/Mean/Max/Std dev/Память (заголовки 1798-1799), fields включает mean/max/stdev; CHANGELOG стр.27 'Mean/Max/Std dev added to mo |
| 371 | 🟡 PARTIAL | feat(glossary): волна В1 «ядро типов» — 110 карточек до ready | CHANGELOG.md:21 — «волна В1 «Строки (str)» (#371): 45 методов str доведены до ready». drafts.json содержит 787 draft-карточек (832-45=787), str.json=61 ready. Идемпотентность закреплена тестом tests/test_glossary_draft_gen.py:83 test_run_ge **⚠ Пробел:** Из 110 карточек волны сделаны только str (45). Не доведены до ready: set 25, числа 13, list 11, dict 10, builtins 4, tuple 2 (65 карточек). Issue корректно остаётся OPEN. |
| 373 | ✅ DONE | Политика краткости changelog + ротация старых версий в архив | CHANGELOG.md содержит ровно 3 версии: 1.8.0/1.7.0/1.6.0; docs/changelog-archive.md:21 «Архив версионированных релизов (1.1.0 – 1.5.0)»; guardrail check_docs_guardrails.py:59 CHANGELOG_MAX_VERSIONS=3 + check_changelog_version_budget:191; пол |
| 381 | 🗂️ TRACKER | [Epic] docs: аудит Markdown-документации после v1.8.0 | Эпик-зонтик над #382–#386; CHANGELOG.md [Unreleased] содержит все 5 дочерних записей (#382–#386). Дочерние проверены индивидуально — суть эпика реализована. **⚠ Пробел:** Мелкие остатки внутри #384/#386 (см. ниже), но AC эпика в целом закрыты: versions.md имеет колонку v1.8.0, меню 0-5 согласовано, architecture.md актуализирован, CI-guard добавлен. |
| 382 | ✅ DONE | fix(docs): дозакрыть переход на v1.8.0 | docs/versions.md:28 таблица теперь содержит колонку **v1.8.0**; CHECKPOINT.md:79 «CHANGELOG § [1.8.0]»; CHECKPOINT.md:91 «#130/#146 — закрыты» (убраны из открытых фронтов). CHANGELOG (#382). |
| 383 | ✅ DONE | docs: синхронизация с эпиком #342 (меню 0-5, web-разделы, insights_*,  | «режимы 0-5» в CLAUDE.md:84, architecture.md:16, project-structure.md:16; локали ru.json:60/en.json:60 «[0-5]»; configuration.md:94-96 три ключа insights_*; web-current.md:44/123 «шесть разделов»; README.md:47-49 добавлены --insights/--lint |
| 384 | 🟡 PARTIAL | fix(docs): фактические ошибки в доках + wiring lint | Исправлено: logging.md таблица активации теперь только --diagnostic (не --verbose); server-mode.md:149 статус cancelled добавлен (#296); grader-workflow.md:266 и options.py:103 «stepik-python-grader[watch]»; result-contract.md:120 поле lint **⚠ Пробел:** НЕ сделано: (1) ADR docs/adr/0001-server-mode.md:3 и README.md:29 всё ещё «Proposed» вместо «Accepted»; (2) trace-format.md:65-76 не добавлено поле `type` у heap-объектов (осталось |
| 385 | ✅ DONE | docs(architecture): актуализация architecture.md и project-structure.m | architecture.md содержит все 5 ранее отсутствовавших модулей: diag_log.py:68, tracer.py:48, playground.py:34, rules_adapter.py:30, insights_adapter.py:31, плюс core/insights.py:54; DAG-рёбра добавлены (:107,:114-118,:123,:140); ложный клейм |
| 386 | ✅ DONE | chore(docs): CI-guard versions.md, релизный чеклист, архивация claude- | scripts/check_version_consistency.py:150 _check_versions_md проверяет колонку последнего релиза; docs/claude-handoff.md заархивирован (баннер «Статус (2026-07-14): архив»); docs/audit-2026-07.md:10-13 баннер «P0 (§10) выполнен v1.8.0; §9 →  **⚠ Пробел:** Мелко: баннер «Часть II реализована» после закрытия #362 в web-glossary-optimization-2026-07.md:137 не проставлен; guard versions.md — мягкое предупреждение (согласовано с CHECKPOI |
