# Структура проекта

> Обзор проекта — в
> [README](../../README.md). Граф зависимостей (DAG), слои и роли модулей — в
> [`architecture.md`](architecture.md); это дерево — канонический перечень
> файлов (CLAUDE.md § Структура делегирует его сюда).

```
Stepik-Python-Grader/
├── src/
│   └── stepik_grader/            # src-layout
│       ├── __init__.py
│       ├── __main__.py            # python -m stepik_grader → grader.main()
│       ├── py.typed              # PEP 561 маркер типов
│       ├── grader.py              # Тонкий фасад обратной совместимости
│       ├── cli/                   # Интерактивное меню (пункты 0-8) + stepik-grader entry point
│       │   ├── __init__.py        # Compatibility facade + main()
│       │   ├── options.py         # argparse-парсер, --verbose/--cache resolution (leaf)
│       │   ├── context.py         # CliContext — явные зависимости для handlers (leaf)
│       │   ├── commands.py        # _run_mode_1..4, _run_tests_maybe_cached (leaf)
│       │   ├── rendering.py       # csv/markdown table output (leaf)
│       │   └── interactive.py     # _interactive_menu, _ask_*, _pick_path_via_dialog (leaf)
│       ├── config.py              # GraderConfig, CONFIG — единая конфигурация
│       ├── atomic_io.py           # Utilities: atomic_write_json (temp+os.replace, leaf, ADR-0011)
│       ├── db.py                  # Utilities: общий SQLite-коннектор connect/user_version/apply_schema (leaf, ADR-0011)
│       ├── web/                   # Локальный веб-интерфейс (--serve)
│       │   ├── __init__.py        # Публичный API пакета (реэкспорт для back-compat)
│       │   ├── server.py          # Каркас HTTP-сервера (http.server): собирает хендлер из миксинов, статика
│       │   ├── api_routes.py      # _ApiRoutesMixin — таблицы маршрутов REST-API + хендлеры
│       │   ├── http_guards.py     # _GuardMixin — разбор запроса, конфайн путей, JSON-ответ, лимиты тела
│       │   ├── grading.py         # Фасад web→core по исполнению: grade/bench/trace/RunSpec (ADR-0010)
│       │   ├── viewmodels.py      # grade_path/grade_benchmark/grade_microbench/save_solution → JSON (через web/grading)
│       │   ├── downloader_adapter.py # download_task — адаптер над downloader.py
│       │   ├── auth_adapter.py       # auth_status/perform_browser_auth — браузерный OAuth в --serve
│       │   ├── glossary_adapter.py   # glossary_search/get/missing/code_terms — адаптеры над glossary/
│       │   ├── rules_adapter.py      # rules_search/rules_get — адаптер над пакетом rules/
│       │   ├── insights_adapter.py   # insights_cards — адаптер над core/insights+history
│       │   ├── reference_adapter.py  # import_reference — адаптер над core/stepik_reference (кнопка «эталон»)
│       │   ├── feedback_adapter.py   # feedback_draft — адаптер над core/feedback (POST /api/feedback)
│       │   ├── commands.py        # Реестр команд для action cards (leaf)
│       │   ├── runs.py            # Async job-модель: bench/microbench/playground/trace/auth
│       │   ├── playground.py      # Песочница: запуск кода со stdin, вывод/статус
│       │   ├── i18n.py            # message_id-каталог веб-API
│       │   └── static/            # index.html/app.css/app.js + fonts/ + vendor/ (codemirror @6) — без build-шага
│       ├── ide.py                 # Генерация .vscode/tasks.json (--init-vscode)
│       ├── pytest_plugin.py       # pytest11 entry point (--grader-mode)
│       ├── downloader.py         # Application: координатор загрузки задач
│       ├── downloader_config.py  # Application: конфиг stepik_config.json + интерактив
│       ├── diagnostic_stepik.py  # Диагностика API и токена
│       ├── launcher.py           # GUI-лаунчер веб-интерфейса (tkinter; --serve отдельным процессом), leaf
│       ├── core/                  # Internal Infrastructure/Utility модули
│           ├── __init__.py
│           ├── grader_core.py    # Исполнение тест-кейса в subprocess, агрегация статистики
│           ├── test_loader.py    # Обнаружение файлов-решений, загрузка тест-кейсов
│           ├── mode_detector.py  # Детекция режима stdin/function
│           ├── wrapper_builder.py # Генерация wrapper-скриптов для function-mode
│           ├── reporter.py       # rich-таблицы, вывод, verbose-diff
│           ├── result.py         # TestResult (frozen dataclass) + Verdict Literal (leaf)
│           ├── runner.py         # Runner Protocol + LocalRunner — абстракция запуска кода
│           ├── microbench_runner.py  # Timeit-микробенчмарк через subprocess + os.devnull
│           ├── normalizers.py    # Нормализация вывода: округление float, sort/whitespace
│           ├── glossary.py       # Компактная карта исключений → подсказка + ссылка, leaf
│           ├── error_glossary.py # Единый RE-резолвер: bundled JSON-база → компактная карта fallback
│           ├── cache.py          # Opt-in кэш результатов
│           ├── stepik_client.py  # Infrastructure: OAuth2, requests.Session, Stepik API
│           ├── oauth_flow.py     # Infrastructure/Auth: OAuth2-фасад поверх stepik_client
│           ├── parsers.py        # Парсинг тест-блоков (# TEST_N:)
│           ├── task_page_parser.py   # Разбор HTML текста задачи: таблица кейсов, ссылки (leaf)
│           ├── tests_writer.py       # Запись Format 1/3 тест-кейсов на диск (leaf)
│           ├── test_source_fetcher.py # Скачивание тестов из ZIP/GitHub → Format 3
│           ├── step_content.py       # Разбор Stepik API-контента и URL шага (leaf)
│           ├── storage.py        # Utilities: load/save JSON, save_secrets (нет project-зависимостей)
│           ├── i18n.py           # Загрузка JSON-локалей меню/CLI
│           ├── locales/          # JSON-локали меню/CLI: en.json, ru.json (читает i18n.py)
│           ├── stats.py          # Opt-in локальная статистика запусков
│           ├── history.py        # Opt-in SQLite-история прогонов
│           ├── mtime_cache.py    # Generic mtime-кеш загрузки
│           ├── lint.py           # Opt-in PEP-проверка через ruff, extra [lint]
│           ├── insights.py       # Таксономия падений + затухание карточек «Подучить»
│           ├── history_recording.py # Сборка записей истории из грейдинга для CLI+web
│           ├── failure_context.py # Единая сборка FailureContext упавшего кейса для CLI+web
│           ├── ai_grounding.py   # Retrieval-заземление AI-подсказки из офлайн-глоссария по концептам кода
│           ├── ai_hints.py       # Opt-in AI-подсказки при WA/RE (--ai-hints, BYOK на requests, ADR-0003)
│           ├── progress_export.py # Экспорт прогресса в Markdown/HTML (--export-progress)
│           ├── user_settings.py  # Персистентные настройки CLI (.grader_settings.json, leaf)
│           ├── stepik_reference.py # Импорт закреплённых решений Stepik как reference (--import-reference)
│           ├── diag_log.py       # Opt-in диагностическое логирование сети/OAuth с редакцией секретов
│           ├── feedback.py       # Обратная связь: prefilled-URL к GitHub Issue Forms, редакция секретов
│           ├── tracer.py         # Пошаговый трассировщик кода (sys.settrace → JSON-трейс) для песочницы
│           └── sandbox/          # SandboxRunner: OS-изолированный запуск, --sandbox
│               ├── __init__.py   # SandboxRunner, SandboxUnavailableError, выбор backend'а по ОС
│               ├── _linux.py     # bubblewrap (bwrap) backend
│               ├── _macos.py     # sandbox-exec (Seatbelt) backend
│               ├── _windows.py   # Job Objects (ctypes) backend
│               ├── _posix_bootstrap.py # Общий POSIX-бутстрап лимитов (CPU/FS/processes)
│               ├── _posix_common.py    # Общий POSIX subprocess-раннер с лимитами
│               └── _run_dir.py   # Эфемерная run-директория для копии решения
│       ├── glossary/             # Domain: локальный knowledge-модуль глоссария
│           ├── __init__.py       # Публичный API пакета glossary
│           ├── models.py         # GlossaryCard, GlossaryMissingEntry (leaf, только stdlib)
│           ├── json_provider.py  # JsonGlossaryProvider + очередь пополнения (JSON-first)
│           ├── detector.py       # MissingConceptDetector — AST-детект пробелов без исполнения
│           ├── stdlib_inventory.py # Офлайн-инвентарь официального Python/stdlib (leaf)
│           └── coverage.py       # Coverage-отчёт + missing JSON + CLI
│       └── rules/                # Domain: карточки правил PEP 8
│           ├── __init__.py       # Публичный API пакета rules
│           ├── models.py         # RuleCard (leaf, только stdlib)
│           ├── json_provider.py  # JsonRulesProvider + bundled_rules() (кеш core/mtime_cache)
│           └── data/pep8_ru.json # Комплектная база ≥30 карточек правил (package-data)
├── conftest.py                 # Добавляет src/ в sys.path для тестов; включает pytester
├── tests/                     # pytest-набор (число — в CI-прогоне / бейджах README)
├── docs/                      # База знаний (архитектура, структура, версии)
├── .github/workflows/ci.yml   # CI: pytest + ruff + mypy на Python 3.12/3.13/3.14
├── .pre-commit-config.yaml    # Pre-commit хуки (ruff check + ruff format)
├── pyproject.toml             # Конфигурация проекта (ruff, mypy, pytest, зависимости, packages.find where=["src"])
├── LICENSE                    # MIT
├── secrets.json.example       # Шаблон файла с OAuth-токеном
├── stepik_config.json.example # Шаблон конфига Stepik
├── CHANGELOG.md               # История изменений
└── README.md
```

Локально обычно появляются:

```text
StepikTasks/
stepik_config.json
secrets.json
errors.txt
stepik_diagnostics/
.grader_cache/
```

Эти файлы и папки держи в `.gitignore`.
