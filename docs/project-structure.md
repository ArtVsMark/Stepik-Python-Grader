# Структура проекта

> Вынесено из README (issue #104 / эпик #102). Обзор проекта — в
> [README](../README.md). Граф зависимостей (DAG), слои и роли модулей — в
> [`architecture.md`](architecture.md); это дерево — канонический перечень
> файлов (CLAUDE.md § Структура делегирует его сюда).

```
Stepik-Python-Grader/
├── src/
│   └── stepik_grader/            # src-layout (Issue #35 / CLAUDE.md Sprint 8.2)
│       ├── __init__.py
│       ├── __main__.py            # python -m stepik_grader → grader.main()
│       ├── py.typed              # PEP 561 маркер типов (issue #101)
│       ├── grader.py              # Тонкий фасад обратной совместимости (Sprint 7)
│       ├── cli/                   # Интерактивное меню (режимы 0-7) + stepik-grader entry point
│       │   ├── __init__.py        # Compatibility facade + main() (issue #117/#119/#120/#121)
│       │   ├── options.py         # argparse-парсер, --verbose/--cache resolution (leaf, issue #119)
│       │   ├── context.py         # CliContext — явные зависимости для handlers (leaf, issue #120)
│       │   ├── commands.py        # _run_mode_1..4, _run_tests_maybe_cached (leaf, issue #120)
│       │   ├── rendering.py       # csv/markdown table output (leaf, issue #121 Phase 1)
│       │   └── interactive.py     # _interactive_menu, _ask_*, _pick_path_via_dialog (leaf, issue #121 Phase 2)
│       ├── config.py              # GraderConfig, CONFIG — единая конфигурация
│       ├── atomic_io.py           # Utilities: atomic_write_json (temp+os.replace, leaf, issue #551/ADR-0011)
│       ├── db.py                  # Utilities: общий SQLite-коннектор connect/user_version/apply_schema (leaf, issue #552/ADR-0011)
│       ├── web/                   # Локальный веб-интерфейс (--serve), issue #58/#125/#186/#187
│       │   ├── __init__.py        # Публичный API пакета (реэкспорт для back-compat)
│       │   ├── server.py          # HTTP-хендлер (http.server), роутинг /api/*
│       │   ├── viewmodels.py      # grade_path/grade_benchmark/grade_microbench/save_solution → JSON
│       │   ├── downloader_adapter.py # download_task — адаптер над downloader.py (issue #186)
│       │   ├── auth_adapter.py       # auth_status/perform_browser_auth — браузерный OAuth в --serve (issue #402)
│       │   ├── glossary_adapter.py   # glossary_search/get/missing/code_terms — адаптеры над glossary/
│       │   ├── rules_adapter.py      # rules_search/rules_get — адаптер над пакетом rules/ (issue #379)
│       │   ├── insights_adapter.py   # insights_cards — адаптер над core/insights+history (issue #379)
│       │   ├── reference_adapter.py  # import_reference — адаптер над core/stepik_reference (кнопка «эталон», issue #55)
│       │   ├── commands.py        # Реестр команд для action cards (leaf)
│       │   ├── runs.py            # Async job-модель: bench/microbench/playground/trace/auth (issue #262/#402)
│       │   ├── playground.py      # Песочница: запуск кода со stdin, вывод/статус (issue #317)
│       │   ├── i18n.py            # message_id-каталог веб-API (issue #264)
│       │   └── static/            # index.html/app.css/app.js + fonts/ + vendor/ (codemirror @6) — без build-шага (issue #362)
│       ├── ide.py                 # Генерация .vscode/tasks.json (--init-vscode)
│       ├── pytest_plugin.py       # pytest11 entry point (--grader-mode)
│       ├── downloader.py         # Application: координатор загрузки задач (issue #302)
│       ├── downloader_config.py  # Application: конфиг stepik_config.json + интерактив (issue #302)
│       ├── diagnostic_stepik.py  # Диагностика API и токена
│       ├── launcher.py           # GUI-лаунчер веб-интерфейса (tkinter; --serve отдельным процессом), leaf, issue #661
│       ├── core/                  # Internal Infrastructure/Utility модули (Issue #23, #26)
│           ├── __init__.py
│           ├── grader_core.py    # Исполнение тест-кейса в subprocess, агрегация статистики
│           ├── test_loader.py    # Обнаружение файлов-решений, загрузка тест-кейсов (Issue #45 A-01)
│           ├── mode_detector.py  # Детекция режима stdin/function (Issue #45 A-01)
│           ├── wrapper_builder.py # Генерация wrapper-скриптов для function-mode (Issue #45 A-01)
│           ├── reporter.py       # rich-таблицы, вывод, verbose-diff
│           ├── result.py         # TestResult (frozen dataclass) + Verdict Literal (leaf, issue #112/#113)
│           ├── runner.py         # Runner Protocol + LocalRunner — абстракция запуска кода (issue #136-138)
│           ├── microbench_runner.py  # Timeit-микробенчмарк через subprocess + os.devnull
│           ├── normalizers.py    # Нормализация вывода: округление float, sort/whitespace
│           ├── glossary.py       # Компактная карта исключений → подсказка + ссылка, leaf (issue #72)
│           ├── error_glossary.py # Единый RE-резолвер: bundled JSON-база → компактная карта fallback (issue #356)
│           ├── cache.py          # Opt-in кэш результатов (issue #56)
│           ├── stepik_client.py  # Infrastructure: OAuth2, requests.Session, Stepik API
│           ├── oauth_flow.py     # Infrastructure/Auth: OAuth2-фасад поверх stepik_client
│           ├── parsers.py        # Парсинг тест-блоков (# TEST_N:)
│           ├── task_page_parser.py   # Разбор HTML текста задачи: таблица кейсов, ссылки (issue #302, leaf)
│           ├── tests_writer.py       # Запись Format 1/3 тест-кейсов на диск (issue #302, leaf)
│           ├── test_source_fetcher.py # Скачивание тестов из ZIP/GitHub → Format 3 (issue #302)
│           ├── step_content.py       # Разбор Stepik API-контента и URL шага (issue #302, leaf)
│           ├── storage.py        # Utilities: load/save JSON, save_secrets (нет project-зависимостей)
│           ├── i18n.py           # Загрузка JSON-локалей меню/CLI (issue #144)
│           ├── locales/          # JSON-локали меню/CLI: en.json, ru.json (читает i18n.py, issue #144)
│           ├── stats.py          # Opt-in локальная статистика запусков (issue #268)
│           ├── history.py        # Opt-in SQLite-история прогонов (issue #344, эпик #342)
│           ├── mtime_cache.py    # Generic mtime-кеш загрузки (issue #345, вынос из glossary_adapter)
│           ├── lint.py           # Opt-in PEP-проверка через ruff, extra [lint] (issue #346)
│           ├── insights.py       # Таксономия падений + затухание карточек «Подучить» (issue #347)
│           ├── history_recording.py # Сборка записей истории из грейдинга для CLI+web (issue #395)
│           ├── ai_hints.py       # Opt-in AI-подсказки при WA/RE (--ai-hints, BYOK на requests, issue #435/ADR-0003)
│           ├── progress_export.py # Экспорт прогресса в Markdown/HTML (--export-progress, issue #432)
│           ├── user_settings.py  # Персистентные настройки CLI (.grader_settings.json, leaf, issue #430)
│           ├── stepik_reference.py # Импорт закреплённых решений Stepik как reference (--import-reference, issue #55)
│           ├── diag_log.py       # Opt-in диагностическое логирование сети/OAuth с редакцией секретов (issue #146)
│           ├── tracer.py         # Пошаговый трассировщик кода (sys.settrace → JSON-трейс) для песочницы (issue #318)
│           └── sandbox/          # SandboxRunner: OS-изолированный запуск, --sandbox (issue #266)
│               ├── __init__.py   # SandboxRunner, SandboxUnavailableError, выбор backend'а по ОС
│               ├── _linux.py     # bubblewrap (bwrap) backend
│               ├── _macos.py     # sandbox-exec (Seatbelt) backend
│               ├── _windows.py   # Job Objects (ctypes) backend
│               ├── _posix_bootstrap.py # Общий POSIX-бутстрап лимитов (CPU/FS/processes)
│               ├── _posix_common.py    # Общий POSIX subprocess-раннер с лимитами
│               └── _run_dir.py   # Эфемерная run-директория для копии решения
│       ├── glossary/             # Domain: локальный knowledge-модуль глоссария (issue #126)
│           ├── __init__.py       # Публичный API пакета glossary
│           ├── models.py         # GlossaryCard, GlossaryMissingEntry (leaf, только stdlib)
│           ├── json_provider.py  # JsonGlossaryProvider + очередь пополнения (JSON-first)
│           ├── detector.py       # MissingConceptDetector — AST-детект пробелов без исполнения
│           ├── stdlib_inventory.py # Офлайн-инвентарь официального Python/stdlib (leaf, issue #196)
│           └── coverage.py       # Coverage-отчёт + missing JSON + CLI (issue #197/#198)
│       └── rules/                # Domain: карточки правил PEP 8 (issue #345, эпик #342)
│           ├── __init__.py       # Публичный API пакета rules
│           ├── models.py         # RuleCard (leaf, только stdlib)
│           ├── json_provider.py  # JsonRulesProvider + bundled_rules() (кеш core/mtime_cache)
│           └── data/pep8_ru.json # Комплектная база ≥30 карточек правил (package-data)
├── conftest.py                 # Добавляет src/ в sys.path для тестов; включает pytester
├── tests/                     # pytest-набор (число — в CI-прогоне / бейджах README)
├── docs/                      # База знаний (архитектура, структура, версии) — эпик #102
├── .github/workflows/ci.yml   # CI: pytest + ruff + mypy на Python 3.12/3.13/3.14
├── .pre-commit-config.yaml    # Pre-commit хуки (ruff check + ruff format)
├── pyproject.toml             # Конфигурация проекта (ruff, mypy, pytest, зависимости, packages.find where=["src"])
├── LICENSE                    # MIT (issue #100)
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
