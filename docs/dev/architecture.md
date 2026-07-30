# Архитектура модулей

> Вынесено из README (issue #105, #170 / эпик #102). Обзор проекта — в
> [README](../../README.md); дерево файлов — в
> [project-structure.md](project-structure.md); детальные инварианты и
> текущие задачи — в [`CLAUDE.md`](../../CLAUDE.md).

## Что умеет (модули и слои)

> Пакет живёт в `src/stepik_grader/` (Issue #35, src-layout). Пути ниже —
> относительно `src/stepik_grader/`.

| Модуль | Архитектурный слой | Что делает |
|---|---|---|
| `grader.py` | Application | Тонкий фасад обратной совместимости — реэкспортирует `core/grader_core.py`, `core/reporter.py`, `cli/__init__.py` |
| `cli/__init__.py` | Application / CLI | Интерактивное меню (пункты 0-8, зациклено до `0`; issue #445/#430), non-interactive argparse CLI, профили нагрузки, mutable i18n state (`_LANG`/`_MESSAGES`); реэкспортирует `cli/options.py`, `cli/rendering.py` и тонкие обёртки `_run_mode_1..4` над `cli/commands.py` для обратной совместимости фасада; строит `CliContext` заново на каждый вызов (`_build_cli_context()`), чтобы monkeypatch на facade-имена долетал до handlers; консольная команда `stepik-grader` (issue #117/#119/#120/#121) |
| `cli/options.py` | Application / CLI (leaf) | argparse-парсер (`_build_arg_parser`) и разрешение `--verbose/--quiet`/`--cache` в конкретные bool (`_resolve_verbosity`, `_resolve_use_cache`), `_force_utf8_stdio`; не импортирует `cli/__init__.py`, реэкспортирован им как `cli._build_arg_parser` и т.д. (issue #119, Stage 1 эпика #117) |
| `cli/context.py` | Application / CLI (leaf) | `CliContext` (frozen dataclass) — явные зависимости для command/interactive handlers (`t`, `run_tests`, `run_benchmark`, `run_microbench_mode`, `resolve_test_dir_from_input`, `print_tabular`, `pick_path_via_dialog`, `ask_bench_profile`, `ask_micro_profile`, `run_mode_1..4`); не импортирует `cli/__init__.py`/`cli/commands.py`/`cli/interactive.py` (issue #120, расширено issue #121 Phase 2) |
| `cli/commands.py` | Application / CLI (leaf) | Реализация `_run_mode_1..4` и `_run_tests_maybe_cached`; принимают `CliContext` первым параметром вместо чтения module globals; не импортирует `cli/__init__.py`, вызывается из тонких обёрток фасада (issue #120, Stage 2 эпика #117) |
| `cli/rendering.py` | Application / CLI (leaf) | Табличный вывод csv/markdown: `_rows_to_csv`, `_rows_to_markdown`, `_print_tabular`; не импортирует `cli/__init__.py`, реэкспортирован им как `cli._print_tabular` и т.д.; `CliContext.print_tabular` получает `_print_tabular` через `_build_cli_context()` (issue #121 Phase 1, Stage 1 эпика #117) |
| `cli/interactive.py` | Application / CLI (leaf) | Интерактивное меню и prompt-хелперы: `_interactive_menu`, `_ask_bench_profile`/`_ask_micro_profile`/`_ask_number`, `_print_menu`, `_pick_path_via_dialog`/`_prompt_path`/`_resolve_cli_path_or_error`, `_BENCH_PROFILES`/`_MICRO_PROFILES`; принимают `CliContext` где нужна facade-патчимая зависимость (`pick_path_via_dialog`, `ask_bench_profile`, `ask_micro_profile`, `run_mode_1..4`); не импортирует `cli/__init__.py`. `_LANG`/`_MESSAGES`/`_LOCALE_MESSAGES`/`_t` намеренно НЕ перенесены — `_LANG` мутируется в `main()` (`global _LANG`), перенос сделал бы facade-реэкспорт снимком, а не живой ссылкой (issue #121 Phase 2, Stage 2 эпика #117) |
| `config.py` | Application / Configuration | `GraderConfig` (frozen dataclass) + ленивый `CONFIG` (module `__getattr__`, PEP 562) / `get_config()` — импорт модуля не читает `pyproject.toml`, чтение кэшируется при первом обращении (issue #141/#142); переопределяется через `[tool.stepik-grader]` |
| `downloader.py` | Application | Координатор загрузки задач (issue #302, после SRP-разбиения): `build_task_directory`, `save_task_files` (выбор источника тестов), `process_step_url`, CLI `main`. Специализированные роли вынесены (см. ниже), их публичные имена реэкспортируются для обратной совместимости |
| `downloader_config.py` | Application | Конфиг `stepik_config.json` + интерактив загрузчика (issue #302): `slugify`, `ask_value`, `create_or_update_config`, `load_or_create_config`, `normalize_config_paths`. Держится вне `core/` намеренно — `input()`-интерактив не место в чистых Domain-модулях |
| `diagnostic_stepik.py` | Application / Diagnostics | Диагностика: проверяет структуру ответа API и корректность токена авторизации |
| `web/server.py` | Application / Web | Каркас HTTP-сервера (stdlib `http.server`, `--serve`): собирает хендлер из миксинов, отдаёт статику (`static/` — `index.html`/`app.css`/`app.js` + `fonts/` + `vendor/codemirror-bundle@6.mjs`, читается при импорте), держит workspace/CORS; сами маршруты и хендлеры вынесены в `api_routes.py` после декомпозиции `_Handler` (#647) |
| `web/api_routes.py` | Application / Web | `_ApiRoutesMixin` — декларативные таблицы маршрутов REST-API (`_API_GET/POST_EXACT/PREFIX`) и методы-хендлеры; **полный перечень эндпоинтов см. [api.md](api.md)** (канон, защищён контрактным тестом `test_web_api_contract.py`). Тонкий слой поверх `viewmodels.py`/адаптеров + `web/grading`, бизнес-логики не добавляет (#647) |
| `web/http_guards.py` | Application / Web | `_GuardMixin` + хелперы (`_json`/`_lang_from_query`/`_confined_path`, лимиты тела) — общий защитный слой хендлера: разбор запроса, конфайн путей в workspace, единый JSON-ответ; база для `_ApiRoutesMixin` (#647) |
| `web/grading.py` | Application / Web | Фасад грейдинга для web-слоя (ADR-0010): `viewmodels`/`runs`/`playground` берут `grade`/`bench`/`trace`/`RunSpec` отсюда, а не из `core/*` напрямую — единственная точка входа web→core по исполнению, закреплена boundary-guard тестом (#549/#550) |
| `web/viewmodels.py` | Application / Web | Грейдинг → JSON: `grade_path`/`grade_benchmark`/`grade_microbench`/`list_solutions`/`read_source`/`save_solution`; ErrorCard-мэппинг (`_case_view`) с glossary-lookup и J7 missing-queue wiring (issue #125/#186/#187) |
| `web/downloader_adapter.py` | Application / Web | `download_task` — тонкий адаптер над `downloader.py`: OAuth без похода в браузер, раздел «Загрузчик задач» (issue #186) |
| `web/auth_adapter.py` | Application / Web | `auth_status`/`perform_browser_auth` — тонкий адаптер над `core/oauth_flow` для браузерного OAuth-мастера первого запуска в `--serve` (issue #402); `perform_browser_auth` исполняется async-job'ой `kind="auth"` (см. `web/runs.py`) |
| `web/glossary_adapter.py` | Application / Web | `glossary_search`/`glossary_get`/`glossary_missing`/`code_terms` — тонкие адаптеры над `glossary/json_provider.py` (или fallback на компактный `core/glossary.py`) для разделов «Глоссарий»/«Функции в коде» (issue #125); `code_terms` собирает inventory-driven наборы из `glossary/stdlib_inventory` (issue #367) |
| `web/rules_adapter.py` | Application / Web | `rules_search`/`rules_get` — тонкий адаптер над пакетом `rules/` (`bundled_rules`) для раздела «Правила (PEP)» (issue #379) |
| `web/insights_adapter.py` | Application / Web | `insights_cards`/`active_count` — адаптер над `core/insights`+`core/history` для раздела «Подучить» (issue #379) |
| `web/reference_adapter.py` | Application / Web | `import_reference` — тонкий адаптер над `core/stepik_reference` для кнопки «Найти эталонное решение» (импорт закреплённого решения Stepik в задачу, issue #55); web-аутентификация без браузера, как `downloader_adapter` |
| `web/commands.py` | Application / Web (leaf) | Реестр команд (`COMMANDS`, `filter_commands`) для action cards разбора (палитра снята в #658); не импортирует ничего из проекта |
| `web/runs.py` | Application / Web | Async job-модель для tests/bench/microbench/playground/trace/auth/hint/stepik_submit (`submit_job`/`get_job`/`cancel_job`, issue #262; `kind="tests"` — грейд режима 1 #297, `kind="auth"` — браузерный OAuth #402, `kind="hint"` — AI-объяснение кейса #543, `kind="stepik_submit"` — отправка решения на Stepik #683) — `POST /api/v1/runs`, альтернатива синхронному `GET /api/grade`; `ThreadPoolExecutor`-пул, module-level реестр job'ов под `threading.Lock`, TTL-уборка завершённых |
| `web/playground.py` | Application / Web | `run_playground` — запуск кода со stdin через `web/grading.run_spec` (активный `Runner`, а не `core/runner.LocalRunner` напрямую — ADR-0010; под `--serve --sandbox` это `SandboxRunner`, issue #396); issue #317, раздел «Песочница»; потребитель — `web/runs.py` |
| `web/i18n.py` | Application / Web | `message_id`-каталог веб-API (issue #264): `resolve_lang`/`message_fields`/`render_message`; рендер поверх `core/i18n.load_locale_messages` (локали в `core/locales/<lang>.json`, **не** `web/locales/`); импортирует `core/i18n.py` — не leaf |
| `ide.py` | Application / IDE | IDE-интеграция `--init-vscode`: генерация конфигов VS Code (tasks/launch) |
| `launcher.py` | Application / GUI | GUI-лаунчер веб-интерфейса без командной строки (issue #661): tkinter-окно (выбор запуска простой/с изоляцией `--sandbox`, порт, папка, Запустить/Остановить, статус) поднимает `--serve` **отдельным процессом**; gui-script `stepik-grader-gui`. Только stdlib — project-импортов нет (leaf) |
| `pytest_plugin.py` | Application / Plugin | pytest-плагин (`pytest --grader-mode`, issue #57): запуск тест-кейсов грейдера как pytest-тестов |
| `core/cache.py` | Infrastructure / Utilities | Кэш результатов `.grader_cache/` (issue #56): ключ по контенту решения+тестов, graceful degradation при битом/отсутствующем кэше |
| `core/glossary.py` | Infrastructure / Utilities (leaf) | Компактная встроенная карта исключений (`GlossaryEntry.anchor`, ~28 записей) для error cards при RE; адрес карточки — якорь своего глоссария, ссылок наружу нет (issue #684); leaf-модуль, отдельная сущность от пакета `glossary/` (issue #72) |
| `core/error_glossary.py` | Application-facing helper | Единый RE-резолвер `resolve_error_hint` для CLI (`reporter`) и web (`viewmodels`): по имени исключения ищет карточку в комплектной JSON-базе (`glossary/data/`) и добирает пустоты из компактной `core/glossary.py`; отдаёт `ErrorHint` с якорем своей карточки (ссылок наружу нет, issue #684); провайдер грузится лениво, ошибки graceful (issue #356) |
| `core/grader_core.py` | Application | Исполнение тест-кейса в subprocess и агрегация статистики: 4 режима работы (`run_tests`, `run_benchmark`, `run_microbench_mode`) |
| `core/test_loader.py` | Application | Обнаружение файлов-решений, загрузка тест-кейсов (`load_test_cases`), `resolve_test_dir` (Issue #45 A-01) |
| `core/mode_detector.py` | Application | Детекция режима запуска stdin/function (`_detect_run_mode`, `is_function_only_solution`) (Issue #45 A-01) |
| `core/wrapper_builder.py` | Application | Генерация wrapper-скриптов для function-mode запуска (Issue #45 A-01) |
| `core/reporter.py` | Application / UI | rich-таблицы с цветами, вердикты AC/WA/TLE/RE, verbose-diff при WA, адаптивное форматирование времени (`fmt_time`) |
| `core/result.py` | Domain (leaf) | `TestResult` (frozen dataclass) + `Verdict` Literal — типизированная модель case result (issue #112/#113); `from_dict`/`to_dict` конвертируют форму, которую по-прежнему возвращает `run_single_test()` (`dict[str, Any]`, контракт не меняется — [result-contract.md](result-contract.md)); используется `core/reporter.print_case_verbose` вместо чтения произвольных dict-ключей |
| `core/runner.py` | Infrastructure | `Runner` Protocol + `RunSpec`/`RunOutcome` + `LocalRunner` — абстракция запуска кода (issue #136/#137/#138, `docs/server-mode.md § Runner-слой`); `LocalRunner` — subprocess + best-effort лимит памяти (POSIX) + psutil-мониторинг RSS, то же поведение, что раньше жило внутри `run_single_test`. `SandboxRunner` (issue #266, реализован, см. `core/sandbox/`) — тот же протокол, ОС-уровневая изоляция; инъекция через `grader_core.set_runner()` |
| `core/tracer.py` | Infrastructure (leaf) | Пошаговый трассировщик `trace_code` (`sys.settrace` → JSON-трейс) для web-песочницы (issue #318): исполнение в subprocess, нормализованные `obj_id`, лимит шагов; только stdlib, project-импортов нет |
| `core/sandbox/` | Infrastructure | `SandboxRunner`/`SandboxUnavailableError` (issue #266, `--sandbox`) — ОС-специфичный backend по платформе: `_linux.py` (bubblewrap), `_macos.py` (sandbox-exec/Seatbelt), `_windows.py` (Job Objects, ctypes); `_posix_bootstrap.py`/`_posix_common.py` — общий POSIX-код лимитов (CPU/FS/processes) для Linux и macOS; `_run_dir.py` — эфемерная run-директория. Реализует тот же `Runner`-протокол, что `LocalRunner` — см. [server-mode.md § Runner-слой](design/server-mode.md), гарантии по ОС — [SECURITY.md](../../SECURITY.md) |
| `core/stats.py` | Infrastructure / Utilities | Opt-in локальная статистика запусков (issue #268): `record_run`/`read_summary`, JSON Lines `.grader_stats.jsonl`, best-effort (переживает битый/отсутствующий файл), size-based ротация |
| `core/history.py` | Infrastructure / Utilities | Opt-in SQLite-история прогонов (issue #344, эпик #342): `record_run`/`read_recent_runs`, база `.grader_history.db` (runs/case_results/lint_violations), WAL + `user_version`-миграции, best-effort. Фундамент разделов «Правила»/«Подучить» |
| `core/mtime_cache.py` | Infrastructure / Utilities | Generic mtime-инвалидируемый кеш загрузки (issue #345): `mtime_signature`/`MtimeCache[T]`. Вынесено из `web/glossary_adapter` (#339), переиспользуется провайдерами glossary и rules (не копипаст) |
| `core/lint.py` | Infrastructure / Utilities | Opt-in PEP-проверка через ruff (issue #346, эпик #342): `run_lint`→`list[Violation]`, `ruff_available`, `LintUnavailable`; extra `[lint]`, best-effort, НЕ влияет на вердикт |
| `core/insights.py` | Infrastructure / Utilities | Таксономия падений + затухание карточек «Подучить» (issue #347, эпик #342): `failure_kind`/`classify_status`/`learning_cards` — чистые функции + агрегация из истории, статусы active/fading/archived/watch по номерам прогонов (не по календарю) |
| `core/history_recording.py` | Application-service | Сборка записей истории из результатов грейдинга (issue #395): `cases_from_test_results`/`cases_from_bench_results`/`lint_records_from_violations`/`default_history_db_path`. Вынесено из `cli/commands.py`, чтобы и CLI (режимы 1-4), и web (`web/viewmodels`) наполняли `.grader_history.db` одним кодом (web не импортирует cli) |
| `core/failure_context.py` | Application-service | Единая сборка `FailureContext` упавшего кейса (issue #542): из case-dict + якорей (`insights.failure_kind`, `error_glossary.resolve_error_hint`). Вынесено из `cli/commands.py`, чтобы CLI-режимы 1–4 и web-слой строили один и тот же контекст, не дублируя связку |
| `core/ai_grounding.py` | Application-service | Retrieval-заземление AI-подсказки из офлайн-глоссария (issue #544, эпик E3): по концептам кода (`scan_code_concepts`) достаёт top-k карточек комплектной базы в `FailureContext.grounding` — опционально, офлайн, без эмбеддингов; ребро `core→glossary` разорвано ленивым импортом |
| `core/ai_hints.py` | Application / Integration | Opt-in AI-объяснение падений WA/RE (`--ai-hints`, issue #435, ADR-0003): BYOK, OpenAI-совместимый `{ai_base_url}/chat/completions` на голом `requests` (облако + `ollama` одним кодом, без новых зависимостей); off по умолчанию, network/timeout/invalid-key → тихий пропуск, грейдинг не падает |
| `core/progress_export.py` | Application-service | Экспорт прогресса в Markdown/HTML (`--export-progress`, issue #432) над `core/history`/`core/insights`: `build_progress_report`/`render_markdown`/`render_html` — per-task TTFG и tallies решённого, без сети |
| `core/user_settings.py` | Application / Configuration (leaf) | Персистентные пользовательские настройки CLI (issue #430, `.grader_settings.json`) — тумблер записи истории из меню и т.п.; отдельный слой от frozen `config.py` (изменяемые пользователем настройки, не `pyproject.toml`); единственный project-импорт — top-level `atomic_io.atomic_write_json` (issue #551, stdlib-leaf) |
| `core/stepik_reference.py` | Application | Импорт закреплённых/топовых решений Stepik как reference-competitor'ов для режимов 2–4 (`--import-reference`/`--import-top`, issue #55): `import_references_from_task_dir`; НЕ часть grading-core (вторичный конкурент, не источник первичной проверки) |
| `rules/` (пакет) | Domain | Карточки правил PEP 8 (issue #345, эпик #342): `RuleCard` (`models.py`) + `JsonRulesProvider` (`json_provider.py`) + bundled `data/pep8_ru.json` (≥30 кодов E/W/F). По образцу `glossary/`; `json_provider` тянет `core/mtime_cache` — не leaf |
| `core/microbench_runner.py` | Infrastructure | Timeit-микробенчмарк через subprocess (`python -c`) + подавление stdout решения в `os.devnull`; peak memory через `tracemalloc` |
| `core/normalizers.py` | Infrastructure / Utilities | Нормализация вывода для сравнения: `normalize_floats` (округление float до 9 знаков), `sort_lines`, `normalize_whitespace` (experimental) |
| `core/storage.py` | Infrastructure / Utilities | Чтение и запись JSON-файлов (`load_json_file`, `save_json_file`, `save_secrets`); нет зависимостей от других модулей проекта |
| `atomic_io.py` (top-level) | Infrastructure / Utilities (leaf) | Общий атомарный JSON-писатель `atomic_write_json` (issue #551, ADR-0011): temp в той же директории (`mkstemp`) + `os.replace`, опциональный `fsync`. Живёт вне `core/` намеренно — им пользуются и `core/user_settings`, и независимые от `core/` подпакеты (`glossary/`), без ребра `glossary → core`. Stdlib-only, project-импортов нет |
| `db.py` (top-level) | Infrastructure / Utilities (leaf) | Общий SQLite-коннектор (issue #552, ADR-0011): `connect` (PRAGMA WAL/FK/`busy_timeout` + callback-миграция + close-on-fail) и примитивы `user_version`/`set_user_version`/`apply_schema`. Вынесен из `core/history`; им пользуются и `core/history`, и очередь пополнения глоссария (`glossary/json_provider`, SQLite/WAL). Top-level (как `atomic_io`), чтобы `glossary/` не тянул `core/`. Stdlib-only (`sqlite3`), project-импортов нет |
| `core/stepik_client.py` | Infrastructure / HTTP | OAuth2-авторизация, `requests.Session`, GET-запросы к Stepik REST API, скачивание сабмишнов |
| `core/oauth_flow.py` | Infrastructure / Auth | OAuth2-фасад: единая точка входа для авторизации — `load_secrets`, `load_secrets_dict`, `token_is_valid`, `authorize_and_get_token`; устраняет дублирование между `downloader.py` и `diagnostic_stepik.py` |
| `core/parsers.py` | Infrastructure / Utilities | Парсинг тест-блоков (`# TEST_N:`) — единственный источник истины для `grader.py` и `downloader.py` |
| `core/task_page_parser.py` | Domain (leaf) | Разбор HTML текста задачи (issue #302): `extract_tests_from_html` (таблица кейсов), `extract_external_test_links` (ZIP/GitHub-ссылки), `is_function_style` (stdin vs function по AST). Только stdlib, без project-импортов |
| `core/tests_writer.py` | Domain (leaf) | Запись форматов тест-кейсов (issue #302): `save_tests` (Format 1 — `N`/`N.clue`/`N.type`), `write_testblock_tests` (Format 3 — `input.txt`/`output.txt` с `# TEST_N:`). Только stdlib |
| `core/test_source_fetcher.py` | Infrastructure | Скачивание тестов из внешних источников (issue #302): `download_zip_tests` (Stepik ZIP), `download_github_tests` (GitHub Contents API) → Format 3; безопасность сторонних хостов через `stepik_client` (issue #240) |
| `core/step_content.py` | Domain (leaf) | Извлечение данных из ответов Stepik API (issue #302): `parse_stepik_step_url`, `extract_python_code`, `extract_submission_code`, `extract_function_name`. Чистые `dict/str -> данные`, без сети/ФС |
| `core/i18n.py` | Infrastructure / Utilities (leaf) | `load_locale_messages(lang)` — JSON-локали `core/locales/<lang>.json` (issue #141/#144); аддитивный путь поверх статического `_MESSAGES` в `cli/__init__.py` — новые сообщения через JSON, без переписывания существующих; graceful degradation на отсутствующий/битый файл |
| `core/diag_log.py` | Infrastructure / Diagnostics (leaf) | Opt-in диагностическое логирование сети/OAuth с редакцией секретов (issue #146/#341): `configure_diagnostics`/`get_logger`/`register_secret`; подключён в `cli/__init__`, `downloader`, `diagnostic_stepik`, `core/stepik_client`, `core/oauth_flow`; только stdlib (`logging`/`re`/`pathlib`) |
| `glossary/models.py` | Domain (leaf) | Типизированные модели локального глоссария: `GlossaryCard`, `GlossaryMissingEntry` (issue #126) |
| `glossary/json_provider.py` | Domain | `JsonGlossaryProvider` (загрузка/поиск локальной JSON-базы карточек) + очередь пополнения (issue #126) |
| `glossary/detector.py` | Domain | `MissingConceptDetector` — консервативный AST-детектор недостающих функций/конструкций/исключений (issue #126) |
| `glossary/stdlib_inventory.py` | Domain (leaf) | Офлайн-инвентарь официального Python/stdlib через интроспекцию (`build_stdlib_inventory`, `StdlibItem`, `NOTABLE_STDLIB_MODULES`) — source-driven сторона покрытия (issue #196); только stdlib, не тянет `core/*`. Обход `BaseException` фильтруется `_is_official_stdlib_exception` (по `sys.stdlib_module_names` + отсев приватных модулей/классов), чтобы в инвентарь не попадали исключения стороннего/приватного/собственного кода, случайно загруженного в процесс (#363) |
| `glossary/coverage.py` | Domain | Сопоставление инвентаря с локальной базой (`build_coverage_report`, `missing_entries_from_inventory`) + CLI `python -m stepik_grader.glossary.coverage` (issue #197/#198); зависит только от leaf-модулей пакета `glossary/` |

Основные возможности (пользовательский взгляд) — в [README](../../README.md);
пошаговые сценарии работы — в [grader-workflow.md](../use/grader-workflow.md).

## Граф зависимостей

Граф зависимостей — DAG без циклов (все модули живут в `src/stepik_grader/`):

```
downloader.py          ──→  core/storage.py, core/stepik_client.py, core/oauth_flow.py
downloader.py          ──→  core/task_page_parser.py, core/tests_writer.py, core/test_source_fetcher.py, core/step_content.py  (issue #302 — реэкспорт публичных имён)
downloader.py          ──→  downloader_config.py  (конфиг+интерактив)
downloader_config.py   ──→  core/storage.py
core/test_source_fetcher.py ──→  core/stepik_client.py, core/parsers.py, core/tests_writer.py  (НЕ импортирует downloader — issue #302 AC)
core/task_page_parser.py / core/tests_writer.py / core/step_content.py  ──→  (ничего в проекте; чистые leaf, только stdlib)
core/stepik_client.py ──→  core/storage.py
grader.py              ──→  core/grader_core.py, core/reporter.py, cli/__init__.py  (тонкий фасад)
core/grader_core.py    ──→  core/microbench_runner.py, core/normalizers.py, core/runner.py
core/grader_core.py    ──→  core/test_loader.py, core/mode_detector.py, core/wrapper_builder.py
core/test_loader.py    ──→  core/mode_detector.py, core/parsers.py
core/mode_detector.py  ──→  core/storage.py
cli/__init__.py        ──→  core/grader_core.py  (run_tests/run_benchmark/run_microbench_mode/resolve_test_dir)
cli/__init__.py        ──→  core/cache.py  (GraderCache для --clear-cache)
cli/__init__.py        ──→  core/i18n.py  (JSON-локали поверх статического _MESSAGES)
cli/__init__.py        ──→  cli/options.py  (реэкспорт для backward-compatible facade, issue #119)
cli/__init__.py        ──→  cli/commands.py, cli/context.py  (тонкие обёртки _run_mode_1..4 + _build_cli_context(), issue #120)
cli/__init__.py        ──→  cli/rendering.py  (реэкспорт _print_tabular/_rows_to_csv/_rows_to_markdown, issue #121 Phase 1)
cli/__init__.py        ──→  cli/interactive.py  (тонкие обёртки _interactive_menu/_ask_*/_pick_path_via_dialog/_prompt_path/_resolve_cli_path_or_error/_print_menu, issue #121 Phase 2)
cli/options.py         ──→  config.py  (CONFIG.use_cache в _resolve_use_cache; leaf — не импортирует cli/__init__.py)
cli/commands.py        ──→  core/grader_core.py, core/cache.py, core/reporter.py, core/microbench_runner.py  (leaf — не импортирует cli/__init__.py, зависимости через CliContext)
cli/context.py         ──→  (ничего в проекте; чистый leaf с dataclass CliContext)
cli/rendering.py       ──→  (ничего в проекте; чистый leaf, только stdlib csv/io)
cli/interactive.py     ──→  core/grader_core.py  (find_all_solution_files/collect_grouped_files), cli/context.py  (leaf — не импортирует cli/__init__.py, зависимости через CliContext)
web/server.py          ──→  web/api_routes.py, web/http_guards.py, web/viewmodels.py, web/i18n.py, core/user_settings.py  (каркас: собирает хендлер из миксинов, отдаёт статику, инжектит onboarding_seen в index.html — issue #660; core/sandbox + grading.set_runner — ленивые импорты в теле под --serve --sandbox, #396/#647)
web/api_routes.py      ──→  web/http_guards.py, web/commands.py, web/downloader_adapter.py, web/auth_adapter.py, web/glossary_adapter.py, web/rules_adapter.py, web/insights_adapter.py, web/reference_adapter.py, web/viewmodels.py, web/runs.py, web/i18n.py  (маршруты REST-API поверх адаптеров, #647)
web/grading.py         ──→  core/grader_core.py, core/microbench_runner.py, core/runner.py (RunSpec), core/tracer.py, core/reporter.py, core/test_loader.py, core/cache.py  (фасад web→core по исполнению — единственная точка, ADR-0010; allowlist публичной поверхности core под guard #549/#550)
web/viewmodels.py      ──→  web/grading.py  (grade/bench/microbench/RunSpec/загрузка тестов через фасад, а не из core/* напрямую — ADR-0010)
web/viewmodels.py      ──→  core/error_glossary.py  (resolve_error_hint для error card при RE)
web/viewmodels.py      ──→  glossary/detector.py, glossary/json_provider.py  (MissingConceptDetector + J7 missing-queue)
web/viewmodels.py      ──→  config.py
web/downloader_adapter.py ──→  downloader.py, core/oauth_flow.py, core/storage.py, core/test_loader.py
web/auth_adapter.py       ──→  core/oauth_flow.py, core/storage.py  (браузерный OAuth-мастер --serve, issue #402)
web/glossary_adapter.py   ──→  core/glossary.py, core/mtime_cache.py, glossary/json_provider.py, glossary/models.py, glossary/detector.py, glossary/stdlib_inventory.py, config.py  (issue #367 — stdlib_inventory для code_terms)
web/rules_adapter.py       ──→  rules/  (bundled_rules)
web/insights_adapter.py    ──→  core/history.py, core/insights.py, config.py
web/commands.py            (только stdlib — реестр команд, project-импортов нет)
web/runs.py            ──→  web/grading.py  (find_all_solution_files/trace_code через фасад, а не core/test_loader.py и core/tracer.py напрямую — ADR-0010), web/viewmodels.py, web/i18n.py, web/playground.py, core/ai_hints.py, core/failure_context.py  (kind="hint", issue #543/#542), web/auth_adapter.py (ленивый, kind="auth"), core/stepik_client.py + core/oauth_flow.py (ленивые, kind="stepik_submit", issue #683), config.py  (async job-модель: песочница/трейс/OAuth/AI-подсказка/submit, issue #262/#317/#318/#402)
web/playground.py      ──→  web/grading.py  (RunSpec/run_spec — исполнение активным Runner'ом фасада, а не core/runner.py напрямую — ADR-0010; под --serve --sandbox это SandboxRunner, issue #396), config.py
web/i18n.py            ──→  core/i18n.py  (load_locale_messages — рендер поверх core-локалей core/locales/<lang>.json, issue #264)
core/sandbox/          ──→  core/runner.py  (реализует Runner-протокол: RunSpec/RunOutcome)
cli/__init__.py        ──→  core/sandbox/  (--sandbox: импорт SandboxRunner/SandboxUnavailableError + grader_core.set_runner() — точка инъекции Runner; сам grader_core НЕ зависит от sandbox, issue #266)
cli/commands.py        ──→  core/stats.py  (record_run для --stats, issue #268)
cli/commands.py        ──→  core/history.py, core/insights.py, core/lint.py, core/glossary.py  (--history/--insights/--lint + glossary-подсказки, эпик #342)
cli/commands.py        ──→  core/ai_hints.py, core/history_recording.py  (--ai-hints + наполнение истории, issue #435/#395)
cli/__init__.py        ──→  core/progress_export.py  (--export-progress, issue #432), core/stepik_reference.py  (--import-reference/--import-top, issue #55)
cli/interactive.py     ──→  core/user_settings.py  (тумблер записи истории из меню, issue #430)
web/api_routes.py      ──→  web/reference_adapter.py  (POST /api/import-reference, маршрут переехал из server.py в #647; issue #55)
web/viewmodels.py      ──→  core/history_recording.py  (наполнение .grader_history.db из web-грейдинга, issue #395)
web/reference_adapter.py ──→  core/stepik_reference.py, core/oauth_flow.py, web/downloader_adapter.py  (import-reference без браузера, issue #55)
core/history_recording.py ──→  core/history.py, core/insights.py, core/glossary.py  (записи RunRecord/CaseRecord/LintRecord)
core/progress_export.py   ──→  core/history.py, core/insights.py  (агрегаты прогресса, issue #432)
core/ai_hints.py          ──→  core/diag_log.py  (редакция ключа; config передаётся вызывающим, requests — вне проекта)
core/stepik_reference.py  ──→  core/oauth_flow.py, core/stepik_client.py, core/step_content.py, core/storage.py, core/diag_log.py  (issue #55)
core/user_settings.py     ──→  atomic_io.py  (.grader_settings.json атомарно, issue #430/#551; иначе stdlib-leaf)
core/history.py           ──→  db.py  (.grader_history.db через общий SQLite-коннектор, issue #344/#552; иначе stdlib-leaf)
pytest_plugin.py       ──→  core/grader_core.py, core/test_loader.py  (импорты отложены в функции)
core/reporter.py       ──→  core/error_glossary.py  (resolve_error_hint: glossary-блок при RE)
core/reporter.py       ──→  core/result.py  (TestResult.from_dict в print_case_verbose)
core/error_glossary.py ──→  core/glossary.py, glossary/json_provider.py  (bundled JSON → компактная карта fallback, лениво; issue #356 — glossary/ не тянет core/, ацикл)
ide.py                 (только stdlib — генерация конфигов VS Code; project-импортов нет)
launcher.py            (только stdlib + tkinter/subprocess — поднимает --serve отдельным процессом; project-импортов нет, leaf)
diagnostic_stepik.py ──→  core/stepik_client.py
diagnostic_stepik.py ──→  downloader.py       ← parse_stepik_step_url
downloader.py        ──→  core/oauth_flow.py
diagnostic_stepik.py ──→  core/oauth_flow.py
core/oauth_flow.py    ──→  core/stepik_client.py
core/oauth_flow.py    ──→  core/storage.py
glossary/json_provider.py ──→  glossary/models.py, db.py  (очередь пополнения на SQLite/WAL, issue #552; top-level db — НЕ тянет core/)
glossary/detector.py      ──→  glossary/models.py
glossary/stdlib_inventory.py  (только stdlib — интроспекция builtins/исключений/курируемых модулей; project-импортов нет)
glossary/coverage.py      ──→  glossary/stdlib_inventory.py, glossary/models.py, glossary/json_provider.py
rules/json_provider.py    ──→  core/mtime_cache.py, rules/models.py  (кеш bundled-базы; пакет rules/ не leaf)
cli/__init__.py / downloader.py / diagnostic_stepik.py / core/stepik_client.py / core/oauth_flow.py  ──→  core/diag_log.py  (opt-in диаг-логирование с редакцией секретов, issue #146/#341; сам diag_log — leaf, только stdlib)
```

Подпакет `glossary/` (issue #126) — самодостаточный островок: зависит только
от stdlib и собственных `glossary/models.py`, не импортирует `core/*` и не
импортируется из него. Это сохраняет DAG ацикличным; веб-слой (пакет `web/`,
issue #125/#129 закрыты) — его потребитель, как `web → core`.

**Модули покрытия глоссария (source-driven, issue #195–#198).**
`glossary/stdlib_inventory.py` — leaf: строит офлайн-инвентарь официального
Python/stdlib интроспекцией running-интерпретатора (без сети, без исполнения
пользовательского кода), не импортируя ничего из проекта. Рекурсивный обход
`BaseException.__subclasses__()` видит исключения всех загруженных в процесс
модулей, поэтому фильтруется `_is_official_stdlib_exception`: остаются только
`builtins` и модули из `sys.stdlib_module_names` без приватных сегментов/имён —
сторонний (`rich`), приватный (`_pickle`) и собственный (`stepik_grader`) код в
инвентарь и, как следствие, в очередь черновиков не попадает (инвариант истины
глоссария, эпик #363).
`glossary/coverage.py` сопоставляет этот инвентарь с известными терминами
локальной базы (`JsonGlossaryProvider.known_terms()`) и строит `CoverageReport`
плюс список `GlossaryMissingEntry(origin="stdlib_scan")` для очереди пополнения
(запись — идемпотентный `append_missing_entries`). Оба остаются внутри острова
`glossary/` (`coverage → stdlib_inventory, models, json_provider`) и **не
тянут** `core/*` — DAG ацикличен. Если локальная JSON-база отсутствует или её
не передали, покрытие считается относительно пустого набора known-терминов
(все сущности инвентаря попадают в «недостающее»), а `JsonGlossaryProvider`
поднимает `GlossaryError` на битой/несуществующей базе — грейдер не падает.
Продуктовые роли (истина контента vs истина полноты) и формат хранения — в
[glossary.md § Источники истины](glossary.md#источники-истины-роли) и
[glossary.md § Coverage-отчёт](glossary.md#coverage-отчёт-и-missing-json-coverage-issue-197);
здесь не дублируются.

downloader.py больше не импортирует grader.py: дублирующая копия
`_parse_testblock_file` в grader.py устранена (Issue #19) — оба модуля
читают `parse_testblock_file` из `core/parsers.py`.

Слои (снизу вверх):

```
┌───────────────────────────────────────────────────────────────┐
│  Domain / Application  (src/stepik_grader/ — точки входа)      │
│  downloader.py  │  grader.py (facade)  │  diagnostic_stepik   │
│  web/ (--serve, + web/runs.py async jobs)  │  ide.py           │
│  pytest_plugin.py                                              │
├───────────────────────────────────────────────────────────────┤
│  Application  (core/, грейдер разбит по SRP — Sprint 7, A-01) │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
│  core/test_loader.py │ core/mode_detector.py │ wrapper_builder │
│  cli/ (меню, публичная точка входа — stepik-grader)             │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure  (core/)                                       │
│  core/stepik_client.py  │  core/runner.py                      │
│  core/microbench_runner.py  │  core/oauth_flow.py              │
│  core/cache.py (.grader_cache/, #56)  │  core/stats.py (#268)   │
│  core/history.py (.grader_history.db, WAL, #344)               │
│  core/sandbox/ (SandboxRunner, --sandbox, #266)                │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (leaf, no deps)                  │
│  core/storage.py  │  core/normalizers.py  │  core/glossary.py  │
│  core/i18n.py  │  atomic_io.py (#551)  │  db.py (#552)         │
└───────────────────────────────────────────────────────────────┘
```

`core/storage.py`, `core/normalizers.py`, `core/glossary.py` и top-level
`atomic_io.py`/`db.py` — leaf-модули: не импортируют ничего из проекта, легко
тестируются изолированно. `atomic_io.py` (атомарный JSON-писатель, #551) и `db.py`
(общий SQLite-коннектор, #552) держатся на верхнем уровне, а не в `core/`, чтобы
ими могли пользоваться независимые от `core/` подпакеты (`glossary/`), не порождая
ребра `glossary → core` (ADR-0011). Пакет
`glossary/` (issue #126) — самостоятельный островок и НЕ то же самое, что
leaf-модуль `core/glossary.py`: первый — расширенный knowledge-модуль
(карточки/детектор/очередь), второй — компактная карта исключений для error
cards. Оба не тянут `core/*` бизнес-логику.
