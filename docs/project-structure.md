# Структура проекта

> Вынесено из README (issue #104 / эпик #102). Обзор проекта — в
> [README](../README.md). Полное дерево `core/` с зависимостями и
> инвариантами поддерживается в [`CLAUDE.md`](../CLAUDE.md).

```
Stepik-Python-Grader/
├── src/
│   └── stepik_grader/            # src-layout (Issue #35 / CLAUDE.md Sprint 8.2)
│       ├── __init__.py
│       ├── py.typed              # PEP 561 маркер типов (issue #101)
│       ├── grader.py              # Тонкий фасад обратной совместимости (Sprint 7)
│       ├── cli.py                 # Интерактивное меню (режимы 0-4) + stepik-grader entry point
│       ├── config.py              # GraderConfig, CONFIG — единая конфигурация
│       ├── web.py                 # Локальный веб-интерфейс (--serve)
│       ├── ide.py                 # Генерация .vscode/tasks.json (--init-vscode)
│       ├── pytest_plugin.py       # pytest11 entry point (--grader-mode)
│       ├── downloader.py         # Domain: конфиг, slugify, построение папок, оркестрация API
│       ├── diagnostic_stepik.py  # Диагностика API и токена
│       └── core/                  # Internal Infrastructure/Utility модули (Issue #23, #26)
│           ├── __init__.py
│           ├── grader_core.py    # Исполнение тест-кейса в subprocess, агрегация статистики
│           ├── test_loader.py    # Обнаружение файлов-решений, загрузка тест-кейсов (Issue #45 A-01)
│           ├── mode_detector.py  # Детекция режима stdin/function (Issue #45 A-01)
│           ├── wrapper_builder.py # Генерация wrapper-скриптов для function-mode (Issue #45 A-01)
│           ├── reporter.py       # rich-таблицы, вывод, verbose-diff
│           ├── executor.py       # Запускатель решений: compile + exec с таймаутом
│           ├── microbench_runner.py  # Timeit-микробенчмарк через subprocess + os.devnull
│           ├── normalizers.py    # Нормализация вывода: округление float, sort/whitespace
│           ├── glossary.py       # Карта исключений → подсказка + ссылка (issue #72)
│           ├── cache.py          # Opt-in кэш результатов (issue #56)
│           ├── stepik_client.py  # Infrastructure: OAuth2, requests.Session, Stepik API
│           ├── oauth_flow.py     # Infrastructure/Auth: OAuth2-фасад поверх stepik_client
│           ├── parsers.py        # Парсинг тест-блоков (# TEST_N:)
│           └── storage.py        # Utilities: load/save JSON, save_secrets (нет project-зависимостей)
│       └── glossary/             # Domain: локальный knowledge-модуль глоссария (issue #126)
│           ├── __init__.py       # Публичный API пакета glossary
│           ├── models.py         # GlossaryCard, GlossaryMissingEntry (leaf, только stdlib)
│           ├── json_provider.py  # JsonGlossaryProvider + очередь пополнения (JSON-first)
│           ├── detector.py       # MissingConceptDetector — AST-детект пробелов без исполнения
│           ├── stdlib_inventory.py # Офлайн-инвентарь официального Python/stdlib (leaf, issue #196)
│           └── coverage.py       # Coverage-отчёт + missing JSON + CLI (issue #197/#198)
├── conftest.py                 # Добавляет src/ в sys.path для тестов; включает pytester
├── tests/                     # 660+ тестов (pytest)
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
