# Архитектура модулей

> Обзор проекта — в
> [README](../../README.md); дерево файлов — в
> [project-structure.md](project-structure.md); детальные инварианты и
> текущие задачи — в [`CLAUDE.md`](../../CLAUDE.md).

## Что умеет (модули и слои)

> Пакет живёт в `src/stepik_grader/` (src-layout). Пути ниже —
> относительно `src/stepik_grader/`.

| Модуль | Архитектурный слой | Что делает |
|---|---|---|
| `grader.py` | Application | Тонкий фасад обратной совместимости — реэкспортирует `core/grader_core.py`, `core/reporter.py`, `cli/__init__.py` |
| `cli/__init__.py` | Application / CLI | Интерактивное меню (пункты 0-8, зациклено до `0`), non-interactive argparse CLI, профили нагрузки, mutable i18n state (`_LANG`/`_MESSAGES`); реэкспортирует `cli/options.py`, `cli/rendering.py` и тонкие обёртки `_run_mode_1..4` над `cli/commands.py` для обратной совместимости фасада; строит `CliContext` заново на каждый вызов (`_build_cli_context()`), чтобы monkeypatch на facade-имена долетал до handlers; консольная команда `stepik-grader` |
| `cli/options.py` | Application / CLI (leaf) | argparse-парсер (`_build_arg_parser`) и разрешение `--verbose/--quiet`/`--cache` в конкретные bool (`_resolve_verbosity`, `_resolve_use_cache`), `_force_utf8_stdio`; не импортирует `cli/__init__.py`, реэкспортирован им как `cli._build_arg_parser` и т.д. |
| `cli/context.py` | Application / CLI (leaf) | `CliContext` (frozen dataclass) — явные зависимости для command/interactive handlers (`t`, `run_tests`, `run_benchmark`, `run_microbench_mode`, `resolve_test_dir_from_input`, `print_tabular`, `pick_path_via_dialog`, `ask_bench_profile`, `ask_micro_profile`, `run_mode_1..4`); не импортирует `cli/__init__.py`/`cli/commands.py`/`cli/interactive.py` |
| `cli/commands.py` | Application / CLI (leaf) | Реализация `_run_mode_1..4` и `_run_tests_maybe_cached`; принимают `CliContext` первым параметром вместо чтения module globals; не импортирует `cli/__init__.py`, вызывается из тонких обёрток фасада |
| `cli/rendering.py` | Application / CLI (leaf) | Табличный вывод csv/markdown: `_rows_to_csv`, `_rows_to_markdown`, `_print_tabular`; не импортирует `cli/__init__.py`, реэкспортирован им как `cli._print_tabular` и т.д.; `CliContext.print_tabular` получает `_print_tabular` через `_build_cli_context()` |
| `cli/interactive.py` | Application / CLI (leaf) | Интерактивное меню и prompt-хелперы: `_interactive_menu`, `_ask_bench_profile`/`_ask_micro_profile`/`_ask_number`, `_print_menu`, `_pick_path_via_dialog`/`_prompt_path`/`_resolve_cli_path_or_error`, `_BENCH_PROFILES`/`_MICRO_PROFILES`; принимают `CliContext` где нужна facade-патчимая зависимость (`pick_path_via_dialog`, `ask_bench_profile`, `ask_micro_profile`, `run_mode_1..4`); не импортирует `cli/__init__.py`. `_LANG`/`_MESSAGES`/`_LOCALE_MESSAGES`/`_t` намеренно НЕ перенесены — `_LANG` мутируется в `main()` (`global _LANG`), перенос сделал бы facade-реэкспорт снимком, а не живой ссылкой |
| `config.py` | Application / Configuration | `GraderConfig` (frozen dataclass) + ленивый `CONFIG` (module `__getattr__`, PEP 562) / `get_config()` — импорт модуля не читает `pyproject.toml`, чтение кэшируется при первом обращении; переопределяется через `[tool.stepik-grader]` |
| `downloader.py` | Application | Координатор загрузки задач: `build_task_directory`, `save_task_files` (выбор источника тестов), `process_step_url`, CLI `main`. Специализированные роли вынесены (см. ниже), их публичные имена реэкспортируются для обратной совместимости |
| `downloader_config.py` | Application | Конфиг `stepik_config.json` + интерактив загрузчика: `slugify`, `ask_value`, `create_or_update_config`, `load_or_create_config`, `normalize_config_paths`. Держится вне `core/` намеренно — `input()`-интерактив не место в чистых Domain-модулях |
| `diagnostic_stepik.py` | Application / Diagnostics | Диагностика: проверяет структуру ответа API и корректность токена авторизации |
| `web/server.py` | Application / Web | Каркас HTTP-сервера (stdlib `http.server`, `--serve`): собирает хендлер из миксинов, отдаёт статику (`static/` — `index.html`/`app.css`/`app.js` + `fonts/` + `vendor/codemirror-bundle@6.mjs`, читается при импорте), держит workspace/CORS; сами маршруты и хендлеры вынесены в `api_routes.py` |
| `web/api_routes.py` | Application / Web | `_ApiRoutesMixin` — декларативные таблицы маршрутов REST-API (`_API_GET/POST_EXACT/PREFIX`) и методы-хендлеры; **полный перечень эндпоинтов см. [api.md](api.md)** (канон, защищён контрактным тестом `test_web_api_contract.py`). Тонкий слой поверх `viewmodels.py`/адаптеров + `web/grading`, бизнес-логики не добавляет |
| `web/http_guards.py` | Application / Web | `_GuardMixin` + хелперы (`_json`/`_lang_from_query`/`_confined_path`, лимиты тела) — общий защитный слой хендлера: разбор запроса, конфайн путей в workspace, единый JSON-ответ; база для `_ApiRoutesMixin` |
| `web/grading.py` | Application / Web | Фасад грейдинга для web-слоя (ADR-0010): `viewmodels`/`runs`/`playground` берут `grade`/`bench`/`trace`/`RunSpec` отсюда, а не из `core/*` напрямую — единственная точка входа web→core по исполнению, закреплена boundary-guard тестом |
| `web/viewmodels.py` | Application / Web | Грейдинг → JSON: `grade_path`/`grade_benchmark`/`grade_microbench`/`list_solutions`/`read_source`/`save_solution`; ErrorCard-мэппинг (`_case_view`) с glossary-lookup и J7 missing-queue wiring |
| `web/downloader_adapter.py` | Application / Web | `download_task` — тонкий адаптер над `downloader.py`: OAuth без похода в браузер, раздел «Загрузчик задач» |
| `web/auth_adapter.py` | Application / Web | `auth_status`/`perform_browser_auth` — тонкий адаптер над `core/oauth_flow` для браузерного OAuth-мастера первого запуска в `--serve`; `perform_browser_auth` исполняется async-job'ой `kind="auth"` (см. `web/runs.py`) |
| `web/glossary_adapter.py` | Application / Web | `glossary_search`/`glossary_get`/`glossary_missing`/`code_terms` — тонкие адаптеры над `glossary/json_provider.py` (или fallback на компактный `core/glossary.py`) для разделов «Глоссарий»/«Функции в коде»; `code_terms` собирает inventory-driven наборы из `glossary/stdlib_inventory` |
| `web/rules_adapter.py` | Application / Web | `rules_search`/`rules_get` — тонкий адаптер над пакетом `rules/` (`bundled_rules`) для раздела «Правила (PEP)» |
| `web/insights_adapter.py` | Application / Web | `insights_cards`/`active_count` — адаптер над `core/insights`+`core/history` для раздела «Подучить» |
| `web/reference_adapter.py` | Application / Web | `import_reference` — тонкий адаптер над `core/stepik_reference` для кнопки «Найти эталонное решение» (импорт закреплённого решения Stepik в задачу); web-аутентификация без браузера, как `downloader_adapter` |
| `web/feedback_adapter.py` | Application / Web | `feedback_draft` — тонкий адаптер над `core/feedback` для `POST /api/feedback`: черновик обращения (баг/идея/задача) с prefilled-URL к GitHub Issue Forms и предпросмотром полей. Ничего не отправляет — issue публикует сам пользователь в браузере |
| `web/commands.py` | Application / Web (leaf) | Реестр команд (`COMMANDS`, `filter_commands`) для action cards разбора; не импортирует ничего из проекта |
| `web/runs.py` | Application / Web | Async job-модель для tests/bench/microbench/playground/trace/auth/hint/stepik_submit (`submit_job`/`get_job`/`cancel_job`; `kind="tests"` — грейд режима 1, `kind="auth"` — браузерный OAuth, `kind="hint"` — AI-объяснение кейса, `kind="stepik_submit"` — отправка решения на Stepik) — `POST /api/v1/runs`, альтернатива синхронному `GET /api/grade`; `ThreadPoolExecutor`-пул, module-level реестр job'ов под `threading.Lock`, TTL-уборка завершённых |
| `web/playground.py` | Application / Web | `run_playground` — запуск кода со stdin через `web/grading.run_spec` (активный `Runner`, а не `core/runner.LocalRunner` напрямую — ADR-0010; под `--serve --sandbox` это `SandboxRunner`); раздел «Песочница»; потребитель — `web/runs.py` |
| `web/i18n.py` | Application / Web | `message_id`-каталог веб-API: `resolve_lang`/`message_fields`/`render_message`; рендер поверх `core/i18n.load_locale_messages` (локали в `core/locales/<lang>.json`, **не** `web/locales/`); импортирует `core/i18n.py` — не leaf |
| `ide.py` | Application / IDE | IDE-интеграция `--init-vscode`: генерация конфигов VS Code (tasks/launch) |
| `launcher.py` | Application / GUI | GUI-лаунчер веб-интерфейса без командной строки: tkinter-окно (выбор запуска простой/с изоляцией `--sandbox`, порт, папка, Запустить/Остановить, статус) поднимает `--serve` **отдельным процессом**; gui-script `stepik-grader-gui`. Только stdlib — project-импортов нет (leaf) |
| `pytest_plugin.py` | Application / Plugin | pytest-плагин (`pytest --grader-mode`): запуск тест-кейсов грейдера как pytest-тестов |
| `core/cache.py` | Infrastructure / Utilities | Кэш результатов `.grader_cache/`: ключ по контенту решения+тестов, graceful degradation при битом/отсутствующем кэше |
| `core/glossary.py` | Infrastructure / Utilities (leaf) | Компактная встроенная карта исключений (`GlossaryEntry.anchor`, ~28 записей) для error cards при RE; адрес карточки — якорь своего глоссария, ссылок наружу нет; leaf-модуль, отдельная сущность от пакета `glossary/` |
| `core/error_glossary.py` | Application-facing helper | Единый RE-резолвер `resolve_error_hint` для CLI (`reporter`) и web (`viewmodels`): по имени исключения ищет карточку в комплектной JSON-базе (`glossary/data/`) и добирает пустоты из компактной `core/glossary.py`; отдаёт `ErrorHint` с якорем своей карточки (ссылок наружу нет); провайдер грузится лениво, ошибки graceful |
| `core/grader_core.py` | Application | Исполнение тест-кейса в subprocess и агрегация статистики: 4 режима работы (`run_tests`, `run_benchmark`, `run_microbench_mode`) |
| `core/test_loader.py` | Application | Обнаружение файлов-решений, загрузка тест-кейсов (`load_test_cases`), `resolve_test_dir` |
| `core/mode_detector.py` | Application | Детекция режима запуска stdin/function (`_detect_run_mode`, `is_function_only_solution`) |
| `core/wrapper_builder.py` | Application | Генерация wrapper-скриптов для function-mode запуска |
| `core/reporter.py` | Application / UI | rich-таблицы с цветами, вердикты AC/WA/TLE/RE, verbose-diff при WA, адаптивное форматирование времени (`fmt_time`) |
| `core/result.py` | Domain (leaf) | `TestResult` (frozen dataclass) + `Verdict` Literal — типизированная модель case result; `from_dict`/`to_dict` конвертируют форму, которую по-прежнему возвращает `run_single_test()` (`dict[str, Any]`, контракт не меняется — [result-contract.md](result-contract.md)); используется `core/reporter.print_case_verbose` вместо чтения произвольных dict-ключей |
| `core/runner.py` | Infrastructure | `Runner` Protocol + `RunSpec`/`RunOutcome` + `LocalRunner` — абстракция запуска кода (`docs/server-mode.md § Runner-слой`); `LocalRunner` — subprocess + best-effort лимит памяти (POSIX) + psutil-мониторинг RSS, то же поведение, что раньше жило внутри `run_single_test`. `SandboxRunner` (см. `core/sandbox/`) — тот же протокол, ОС-уровневая изоляция; инъекция через `grader_core.set_runner()` |
| `core/tracer.py` | Infrastructure | Пошаговый трассировщик `trace_code` (`sys.settrace` → JSON-трейс) для web-песочницы: исполнение в subprocess, нормализованные `obj_id`, лимит шагов. **Не leaf:** на загрузке импортирует `config` (лимиты) и `core/runner.py` (`RunSpec`), плюс лениво `core/grader_core.py` |
| `core/sandbox/` | Infrastructure | `SandboxRunner`/`SandboxUnavailableError` (`--sandbox`) — ОС-специфичный backend по платформе: `_linux.py` (bubblewrap), `_macos.py` (sandbox-exec/Seatbelt), `_windows.py` (Job Objects, ctypes); `_posix_bootstrap.py`/`_posix_common.py` — общий POSIX-код лимитов (CPU/FS/processes) для Linux и macOS; `_run_dir.py` — эфемерная run-директория. Реализует тот же `Runner`-протокол, что `LocalRunner` — см. [server-mode.md § Runner-слой](design/server-mode.md), гарантии по ОС — [SECURITY.md](../../SECURITY.md) |
| `core/stats.py` | Infrastructure / Utilities | Opt-in локальная статистика запусков: `record_run`/`read_summary`, JSON Lines `.grader_stats.jsonl`, best-effort (переживает битый/отсутствующий файл), size-based ротация |
| `core/history.py` | Infrastructure / Utilities | Opt-in SQLite-история прогонов: `record_run`/`read_recent_runs`/`read_task_progress`, база `.grader_history.db` (runs/case_results/lint_violations + агрегат `task_progress` «до первого зачёта», неуязвимый к retention), WAL + `user_version`-миграции, best-effort (но не молча: повреждённая база и откат версии схемы называются один раз за процесс — иначе они неотличимы от «истории нет»). Фундамент разделов «Правила»/«Подучить» |
| `core/mtime_cache.py` | Infrastructure / Utilities | Generic mtime-инвалидируемый кеш загрузки: `mtime_signature`/`MtimeCache[T]`; переиспользуется провайдерами glossary и rules (не копипаст) |
| `core/lint.py` | Infrastructure / Utilities | Opt-in PEP-проверка через ruff: `run_lint`→`list[Violation]`, `ruff_available`, `LintUnavailable`; extra `[lint]`, best-effort, НЕ влияет на вердикт |
| `core/insights.py` | Infrastructure / Utilities | Таксономия падений + затухание карточек «Подучить»: `failure_kind`/`classify_status`/`learning_cards` — чистые функции + агрегация из истории, статусы active/fading/archived/watch по номерам прогонов (не по календарю) |
| `core/history_recording.py` | Application-service | Сборка записей истории из результатов грейдинга: `cases_from_test_results`/`cases_from_bench_results`/`lint_records_from_violations`/`default_history_db_path`. Здесь же ЕДИНАЯ точка резолва пути к базе: настройка → существующая база рядом/выше → `~/.stepik-grader/history.db`. Вынесено из `cli/commands.py`, чтобы и CLI (режимы 1-4), и web (`web/viewmodels`) наполняли `.grader_history.db` одним кодом (web не импортирует cli) |
| `core/failure_context.py` | Application-service | Единая сборка `FailureContext` упавшего кейса: из case-dict + якорей (`insights.failure_kind`, `error_glossary.resolve_error_hint`). Вынесено из `cli/commands.py`, чтобы CLI-режимы 1–4 и web-слой строили один и тот же контекст, не дублируя связку |
| `core/ai_grounding.py` | Application-service | Retrieval-заземление AI-подсказки из офлайн-глоссария: по концептам кода (`scan_code_concepts`) достаёт top-k карточек комплектной базы в `FailureContext.grounding` — опционально, офлайн, без эмбеддингов; ребро `core→glossary` разорвано ленивым импортом |
| `core/ai_hints.py` | Application / Integration | Opt-in AI-объяснение падений WA/RE (`--ai-hints`, ADR-0003): BYOK, OpenAI-совместимый `{ai_base_url}/chat/completions` на голом `requests` (облако + `ollama` одним кодом, без новых зависимостей); off по умолчанию, network/timeout/invalid-key → тихий пропуск, грейдинг не падает |
| `core/progress_export.py` | Application-service | Экспорт прогресса в Markdown/HTML (`--export-progress`) над `core/history`/`core/insights`: `build_progress_report`/`render_markdown`/`render_html` — per-task TTFG и tallies решённого, без сети. Подписи отчёта берутся из `core/locales` по параметру `lang` (данные — `task_key`, коды вердиктов и `failure_kind` — не переводятся) |
| `core/user_settings.py` | Application / Configuration (leaf) | Персистентные пользовательские настройки CLI (`.grader_settings.json`) — тумблер записи истории из меню и т.п.; отдельный слой от frozen `config.py` (изменяемые пользователем настройки, не `pyproject.toml`); единственный project-импорт — top-level `atomic_io.atomic_write_json` (stdlib-leaf) |
| `core/stepik_reference.py` | Application | Импорт закреплённых/топовых решений Stepik как reference-competitor'ов для режимов 2–4 (`--import-reference`/`--import-top`): `import_references_from_task_dir`; НЕ часть grading-core (вторичный конкурент, не источник первичной проверки) |
| `rules/` (пакет) | Domain | Карточки правил PEP 8: `RuleCard` (`models.py`) + `JsonRulesProvider` (`json_provider.py`) + bundled `data/pep8_ru.json` (≥30 кодов E/W/F). По образцу `glossary/`; `json_provider` тянет `core/mtime_cache` — не leaf |
| `core/microbench_runner.py` | Infrastructure | Timeit-микробенчмарк через subprocess (`python -c`) + подавление stdout решения в `os.devnull`; peak memory через `tracemalloc` |
| `core/normalizers.py` | Infrastructure / Utilities | Нормализация вывода для сравнения: `normalize_floats` (округление float до 9 знаков), `sort_lines`, `normalize_whitespace` (experimental) |
| `core/storage.py` | Infrastructure / Utilities | Чтение и запись JSON-файлов (`load_json_file`, `save_json_file`, `save_secrets`); нет зависимостей от других модулей проекта |
| `atomic_io.py` (top-level) | Infrastructure / Utilities (leaf) | Общий атомарный JSON-писатель `atomic_write_json` (ADR-0011): temp в той же директории (`mkstemp`) + `os.replace`, опциональный `fsync`. Живёт вне `core/` намеренно — им пользуются и `core/user_settings`, и независимые от `core/` подпакеты (`glossary/`), без ребра `glossary → core`. Stdlib-only, project-импортов нет |
| `db.py` (top-level) | Infrastructure / Utilities (leaf) | Общий SQLite-коннектор (ADR-0011): `connect` (PRAGMA WAL/FK/`busy_timeout` + callback-миграция + close-on-fail) и примитивы `user_version`/`set_user_version`/`apply_schema` (версия в базе выше ожидаемой — `SchemaTooNewError`, потомок `sqlite3.DatabaseError`, а не молчаливый no-op). Вынесен из `core/history`; им пользуются и `core/history`, и очередь пополнения глоссария (`glossary/json_provider`, SQLite/WAL). Top-level (как `atomic_io`), чтобы `glossary/` не тянул `core/`. Stdlib-only (`sqlite3`), project-импортов нет |
| `core/stepik_client.py` | Infrastructure / HTTP | OAuth2-авторизация, `requests.Session`, GET-запросы к Stepik REST API, скачивание сабмишнов |
| `core/oauth_flow.py` | Infrastructure / Auth | OAuth2-фасад: единая точка входа для авторизации — `load_secrets`, `load_secrets_dict`, `token_is_valid`, `authorize_and_get_token`; устраняет дублирование между `downloader.py` и `diagnostic_stepik.py` |
| `core/parsers.py` | Infrastructure / Utilities | Парсинг тест-блоков (`# TEST_N:`) — единственный источник истины для `grader.py` и `downloader.py` |
| `core/task_page_parser.py` | Domain (leaf) | Разбор HTML текста задачи: `extract_tests_from_html` (таблица кейсов), `extract_external_test_links` (ZIP/GitHub-ссылки), `is_function_style` (stdin vs function по AST). Только stdlib, без project-импортов |
| `core/tests_writer.py` | Domain (leaf) | Запись форматов тест-кейсов: `save_tests` (Format 1 — `N`/`N.clue`/`N.type`), `write_testblock_tests` (Format 3 — `input.txt`/`output.txt` с `# TEST_N:`). Только stdlib |
| `core/test_source_fetcher.py` | Infrastructure | Скачивание тестов из внешних источников: `download_zip_tests` (Stepik ZIP), `download_github_tests` (GitHub Contents API) → Format 3; безопасность сторонних хостов через `stepik_client` |
| `core/step_content.py` | Domain (leaf) | Извлечение данных из ответов Stepik API: `parse_stepik_step_url`, `extract_python_code`, `extract_submission_code`, `extract_function_name`. Чистые `dict/str -> данные`, без сети/ФС |
| `core/i18n.py` | Infrastructure / Utilities (leaf) | `load_locale_messages(lang)` — JSON-локали `core/locales/<lang>.json`; аддитивный путь поверх статического `_MESSAGES` в `cli/__init__.py` — новые сообщения через JSON, без переписывания существующих; graceful degradation на отсутствующий/битый файл |
| `core/diag_log.py` | Infrastructure / Diagnostics (leaf) | Opt-in диагностическое логирование сети/OAuth с редакцией секретов: `configure_diagnostics`/`get_logger`/`register_secret`; подключён в `cli/__init__`, `downloader`, `diagnostic_stepik`, `core/stepik_client`, `core/oauth_flow`; только stdlib (`logging`/`re`/`pathlib`) |
| `core/feedback.py` | Core / Feedback | Канал обратной связи: `FeedbackKind`/`collect_environment`/`collect_commit`/`prepare_issue`/`scrub_paths` — сборка prefilled-URL к GitHub Issue Forms (`.github/ISSUE_TEMPLATE/*.yml`) с редакцией секретов, сворачиванием домашнего пути в `~` и укладыванием в лимит длины URL. Единственное проектное ребро — на leaf `core/diag_log.py`; версию читает через `importlib.metadata`, чтобы не появилось ребро `core → cli`. Ничего не отправляет и не открывает: браузер вызывает CLI/web-слой по явному подтверждению пользователя. Потребители — `cli/interactive.py` (пункт меню «Обратная связь») и `web/feedback_adapter.py` |
| `glossary/models.py` | Domain (leaf) | Типизированные модели локального глоссария: `GlossaryCard`, `GlossaryMissingEntry` |
| `glossary/json_provider.py` | Domain | `JsonGlossaryProvider` (загрузка/поиск локальной JSON-базы карточек) + очередь пополнения |
| `glossary/detector.py` | Domain | `MissingConceptDetector` — консервативный AST-детектор недостающих функций/конструкций/исключений |
| `glossary/stdlib_inventory.py` | Domain (leaf) | Офлайн-инвентарь официального Python/stdlib через интроспекцию (`build_stdlib_inventory`, `StdlibItem`, `NOTABLE_STDLIB_MODULES`) — source-driven сторона покрытия; только stdlib, не тянет `core/*`. Обход `BaseException` фильтруется `_is_official_stdlib_exception` (по `sys.stdlib_module_names` + отсев приватных модулей/классов), чтобы в инвентарь не попадали исключения стороннего/приватного/собственного кода, случайно загруженного в процесс |
| `glossary/coverage.py` | Domain | Сопоставление инвентаря с локальной базой (`build_coverage_report`, `missing_entries_from_inventory`) + CLI `python -m stepik_grader.glossary.coverage`; зависит только от leaf-модулей пакета `glossary/` |

Основные возможности (пользовательский взгляд) — в [README](../../README.md);
пошаговые сценарии работы — в [grader-workflow.md](../use/grader-workflow.md).

## Граф зависимостей

Граф зависимостей — DAG без циклов (все модули живут в `src/stepik_grader/`):

```
downloader.py          ──→  core/storage.py, core/stepik_client.py, core/oauth_flow.py
downloader.py          ──→  core/task_page_parser.py, core/tests_writer.py, core/test_source_fetcher.py, core/step_content.py  (реэкспорт публичных имён)
downloader.py          ──→  downloader_config.py  (конфиг+интерактив)
downloader_config.py   ──→  core/storage.py
core/test_source_fetcher.py ──→  core/stepik_client.py, core/parsers.py, core/tests_writer.py  (НЕ импортирует downloader)
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
cli/__init__.py        ──→  cli/options.py  (реэкспорт для backward-compatible facade)
cli/__init__.py        ──→  cli/commands.py, cli/context.py  (тонкие обёртки _run_mode_1..4 + _build_cli_context())
cli/__init__.py        ──→  cli/rendering.py  (реэкспорт _print_tabular/_rows_to_csv/_rows_to_markdown)
cli/__init__.py        ──→  cli/interactive.py  (тонкие обёртки _interactive_menu/_ask_*/_pick_path_via_dialog/_prompt_path/_resolve_cli_path_or_error/_print_menu)
cli/options.py         ──→  config.py  (CONFIG.use_cache в _resolve_use_cache; leaf — не импортирует cli/__init__.py)
cli/commands.py        ──→  core/grader_core.py, core/cache.py, core/reporter.py, core/microbench_runner.py  (leaf — не импортирует cli/__init__.py, зависимости через CliContext)
cli/context.py         ──→  (ничего в проекте; чистый leaf с dataclass CliContext)
cli/rendering.py       ──→  (ничего в проекте; чистый leaf, только stdlib csv/io)
cli/interactive.py     ──→  core/grader_core.py  (find_all_solution_files/collect_grouped_files), cli/context.py  (leaf — не импортирует cli/__init__.py, зависимости через CliContext), core/feedback.py  (пункт меню «Обратная связь»)
web/server.py          ──→  web/api_routes.py, web/http_guards.py, web/viewmodels.py, web/i18n.py, core/user_settings.py  (каркас: собирает хендлер из миксинов, отдаёт статику, инжектит onboarding_seen в index.html; core/sandbox + grading.set_runner — ленивые импорты в теле под --serve --sandbox)
web/api_routes.py      ──→  web/http_guards.py, web/commands.py, web/downloader_adapter.py, web/auth_adapter.py, web/glossary_adapter.py, web/rules_adapter.py, web/insights_adapter.py, web/reference_adapter.py, web/viewmodels.py, web/runs.py, web/i18n.py  (маршруты REST-API поверх адаптеров)
web/grading.py         ──→  core/grader_core.py, core/microbench_runner.py, core/runner.py (RunSpec), core/tracer.py, core/reporter.py, core/test_loader.py, core/cache.py  (фасад web→core по исполнению — единственная точка, ADR-0010; allowlist публичной поверхности core под guard)
web/viewmodels.py      ──→  web/grading.py  (grade/bench/microbench/RunSpec/загрузка тестов через фасад, а не из core/* напрямую — ADR-0010)
web/viewmodels.py      ──→  core/error_glossary.py  (resolve_error_hint для error card при RE)
web/viewmodels.py      ──→  glossary/detector.py, glossary/json_provider.py  (MissingConceptDetector + J7 missing-queue)
web/viewmodels.py      ──→  config.py
web/viewmodels.py      ──→  core/history.py, core/lint.py, core/mtime_cache.py, rules/  (запись прогонов, блок «Стиль», кеш по mtime, карточки правил)
web/downloader_adapter.py ──→  downloader.py, core/oauth_flow.py, core/storage.py, core/test_loader.py
web/auth_adapter.py       ──→  core/oauth_flow.py, core/storage.py  (браузерный OAuth-мастер --serve)
web/glossary_adapter.py   ──→  core/glossary.py, core/mtime_cache.py, glossary/json_provider.py, glossary/models.py, glossary/detector.py, glossary/stdlib_inventory.py, config.py  (stdlib_inventory для code_terms)
web/rules_adapter.py       ──→  rules/  (bundled_rules), core/history_recording.py  (резолв пути БД), core/insights.py  (подсветка лично нарушенных правил)
web/insights_adapter.py    ──→  core/history_recording.py  (резолв пути БД), core/insights.py, core/progress_export.py, config.py  (отчёт «Прогресс» — тот же движок, что у CLI --export-progress)
web/commands.py            (только stdlib — реестр команд, project-импортов нет)
web/runs.py            ──→  web/grading.py  (find_all_solution_files/trace_code через фасад, а не core/test_loader.py и core/tracer.py напрямую — ADR-0010), web/viewmodels.py, web/i18n.py, web/playground.py, core/ai_hints.py, core/failure_context.py  (kind="hint"), web/auth_adapter.py (ленивый, kind="auth"), core/stepik_client.py + core/oauth_flow.py (ленивые, kind="stepik_submit"), config.py  (async job-модель: песочница/трейс/OAuth/AI-подсказка/submit)
web/playground.py      ──→  web/grading.py  (RunSpec/run_spec — исполнение активным Runner'ом фасада, а не core/runner.py напрямую — ADR-0010; под --serve --sandbox это SandboxRunner), config.py
web/i18n.py            ──→  core/i18n.py  (load_locale_messages — рендер поверх core-локалей core/locales/<lang>.json)
core/sandbox/          ──→  core/runner.py  (реализует Runner-протокол: RunSpec/RunOutcome)
core/tracer.py         ──→  core/runner.py  (RunSpec), config.py; core/grader_core.py — ленивый импорт в теле (НЕ leaf, вопреки прежней редакции этой доки)
cli/__init__.py        ──→  core/sandbox/  (--sandbox: импорт SandboxRunner/SandboxUnavailableError + grader_core.set_runner() — точка инъекции Runner; сам grader_core НЕ зависит от sandbox)
__main__.py            ──→  cli/  (точка входа `python -m stepik_grader`)
cli/__init__.py        ──→  core/reporter.py, core/stats.py  (печать сводок и локальная статистика)
cli/commands.py        ──→  core/stats.py  (record_run для --stats)
cli/commands.py        ──→  core/failure_context.py, core/user_settings.py, rules/  (контекст падения для AI-подсказки, тумблер истории, персональные правила)
cli/commands.py        ──→  core/history.py, core/lint.py  (--history/--lint; карточки «Подучить» и подсказки глоссария приходят через core/failure_context.py, напрямую cli/commands.py их больше не импортирует)
cli/commands.py        ──→  core/ai_hints.py, core/history_recording.py  (--ai-hints + наполнение истории)
cli/__init__.py        ──→  core/progress_export.py  (--export-progress), core/stepik_reference.py  (--import-reference/--import-top)
cli/interactive.py     ──→  core/user_settings.py  (тумблер записи истории из меню)
web/api_routes.py      ──→  web/reference_adapter.py  (POST /api/import-reference)
web/api_routes.py      ──→  core/stepik_reference.py, core/user_settings.py  (импорт эталонных решений и настройки пользователя)
web/api_routes.py      ──→  web/feedback_adapter.py  (POST /api/feedback — черновик обращения)
web/feedback_adapter.py ──→  core/feedback.py  (та же сборка prefilled-URL, что у пункта меню CLI — логика не дублируется в web/JS)
core/feedback.py       ──→  core/diag_log.py  (redact — единственное проектное ребро; версия через importlib.metadata, чтобы не появилось ребро core → cli)
web/viewmodels.py      ──→  core/history_recording.py  (наполнение .grader_history.db из web-грейдинга)
web/reference_adapter.py ──→  core/stepik_reference.py, core/oauth_flow.py, web/downloader_adapter.py  (import-reference без браузера)
core/history_recording.py ──→  core/history.py, core/insights.py, core/glossary.py, config.py  (записи RunRecord/CaseRecord/LintRecord; config — путь БД из [tool.stepik-grader] history_db_path)
core/progress_export.py   ──→  core/history.py, core/insights.py  (агрегаты прогресса)
core/ai_hints.py          ──→  core/diag_log.py  (редакция ключа; config передаётся вызывающим, requests — вне проекта)
core/stepik_reference.py  ──→  core/oauth_flow.py, core/stepik_client.py, core/step_content.py, core/storage.py, core/diag_log.py
core/user_settings.py     ──→  atomic_io.py  (.grader_settings.json атомарно; иначе stdlib-leaf)
core/stats.py             ──→  atomic_io.py  (ротация .grader_stats.jsonl атомарной заменой)
downloader.py / downloader_config.py  ──→  core/i18n.py  (сообщения мастера скачивания на языке меню)
core/history.py           ──→  db.py  (.grader_history.db через общий SQLite-коннектор; иначе stdlib-leaf)
pytest_plugin.py       ──→  core/grader_core.py, core/test_loader.py  (импорты отложены в функции)
core/reporter.py       ──→  core/error_glossary.py  (resolve_error_hint: glossary-блок при RE)
core/reporter.py       ──→  core/result.py  (TestResult.from_dict в print_case_verbose)
core/error_glossary.py ──→  core/glossary.py, glossary/json_provider.py  (bundled JSON → компактная карта fallback, лениво; glossary/ не тянет core/, ацикл)
ide.py                 (только stdlib — генерация конфигов VS Code; project-импортов нет)
launcher.py            (только stdlib + tkinter/subprocess — поднимает --serve отдельным процессом; project-импортов нет, leaf)
diagnostic_stepik.py ──→  core/stepik_client.py
diagnostic_stepik.py ──→  downloader.py       ← parse_stepik_step_url
downloader.py        ──→  core/oauth_flow.py
diagnostic_stepik.py ──→  core/oauth_flow.py
core/oauth_flow.py    ──→  core/stepik_client.py
core/oauth_flow.py    ──→  core/storage.py
glossary/json_provider.py ──→  glossary/models.py, db.py  (очередь пополнения на SQLite/WAL; top-level db — НЕ тянет core/)
glossary/detector.py      ──→  glossary/models.py
glossary/stdlib_inventory.py  (только stdlib — интроспекция builtins/исключений/курируемых модулей; project-импортов нет)
glossary/coverage.py      ──→  glossary/stdlib_inventory.py, glossary/models.py, glossary/json_provider.py
rules/json_provider.py    ──→  core/mtime_cache.py, rules/models.py  (кеш bundled-базы; пакет rules/ не leaf)
cli/__init__.py / downloader.py / diagnostic_stepik.py / core/stepik_client.py / core/oauth_flow.py  ──→  core/diag_log.py  (opt-in диаг-логирование с редакцией секретов; сам diag_log — leaf, только stdlib)
```

Подпакет `glossary/` — самодостаточный островок: зависит только
от stdlib и собственных `glossary/models.py`, не импортирует `core/*` и не
импортируется из него. Это сохраняет DAG ацикличным; веб-слой (пакет `web/`) —
его потребитель, как `web → core`.

**Модули покрытия глоссария (source-driven).**
`glossary/stdlib_inventory.py` — leaf: строит офлайн-инвентарь официального
Python/stdlib интроспекцией running-интерпретатора (без сети, без исполнения
пользовательского кода), не импортируя ничего из проекта. Рекурсивный обход
`BaseException.__subclasses__()` видит исключения всех загруженных в процесс
модулей, поэтому фильтруется `_is_official_stdlib_exception`: остаются только
`builtins` и модули из `sys.stdlib_module_names` без приватных сегментов/имён —
сторонний (`rich`), приватный (`_pickle`) и собственный (`stepik_grader`) код в
инвентарь и, как следствие, в очередь черновиков не попадает (инвариант истины
глоссария).
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
[glossary.md § Coverage-отчёт](glossary.md#coverage-отчёт-и-missing-json-coverage);
здесь не дублируются.

downloader.py больше не импортирует grader.py: дублирующая копия
`_parse_testblock_file` в grader.py устранена — оба модуля
читают `parse_testblock_file` из `core/parsers.py`.

Слои (снизу вверх):

```
┌───────────────────────────────────────────────────────────────┐
│  Domain / Application  (src/stepik_grader/ — точки входа)      │
│  downloader.py  │  grader.py (facade)  │  diagnostic_stepik   │
│  web/ (--serve, + web/runs.py async jobs)  │  ide.py           │
│  pytest_plugin.py                                              │
├───────────────────────────────────────────────────────────────┤
│  Application  (core/, грейдер разбит по SRP)                  │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
│  core/test_loader.py │ core/mode_detector.py │ wrapper_builder │
│  cli/ (меню, публичная точка входа — stepik-grader)             │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure  (core/)                                       │
│  core/stepik_client.py  │  core/runner.py                      │
│  core/microbench_runner.py  │  core/oauth_flow.py              │
│  core/cache.py (.grader_cache/)  │  core/stats.py               │
│  core/history.py (.grader_history.db, WAL)                     │
│  core/sandbox/ (SandboxRunner, --sandbox)                      │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (leaf, no deps)                  │
│  core/storage.py  │  core/normalizers.py  │  core/glossary.py  │
│  core/i18n.py  │  atomic_io.py  │  db.py                       │
└───────────────────────────────────────────────────────────────┘
```

`core/storage.py`, `core/normalizers.py`, `core/glossary.py` и top-level
`atomic_io.py`/`db.py` — leaf-модули: не импортируют ничего из проекта, легко
тестируются изолированно. `atomic_io.py` (атомарный JSON-писатель) и `db.py`
(общий SQLite-коннектор) держатся на верхнем уровне, а не в `core/`, чтобы
ими могли пользоваться независимые от `core/` подпакеты (`glossary/`), не порождая
ребра `glossary → core` (ADR-0011). Пакет
`glossary/` — самостоятельный островок и НЕ то же самое, что
leaf-модуль `core/glossary.py`: первый — расширенный knowledge-модуль
(карточки/детектор/очередь), второй — компактная карта исключений для error
cards. Оба не тянут `core/*` бизнес-логику.
