# Архитектура модулей

> Вынесено из README (issue #105, #170 / эпик #102). Обзор проекта — в
> [README](../README.md); дерево файлов — в
> [project-structure.md](project-structure.md); детальные инварианты и
> текущие задачи — в [`CLAUDE.md`](../CLAUDE.md).

## Что умеет (модули и слои)

> Пакет живёт в `src/stepik_grader/` (Issue #35, src-layout). Пути ниже —
> относительно `src/stepik_grader/`.

| Модуль | Архитектурный слой | Что делает |
|---|---|---|
| `grader.py` | Application | Тонкий фасад обратной совместимости — реэкспортирует `core/grader_core.py`, `core/reporter.py`, `cli.py` |
| `cli.py` | Application / CLI | Интерактивное меню (режимы 0-4) и non-interactive argparse CLI, профили нагрузки; консольная команда `stepik-grader` |
| `config.py` | Application / Configuration | `GraderConfig` (frozen dataclass) + `CONFIG` singleton; переопределяется через `[tool.stepik-grader]` в `pyproject.toml` |
| `downloader.py` | Domain / Application | Управление конфигом и secrets, разбор URL шага, построение директорий задач (`slugify`, `build_task_directory`), сохранение файлов задачи, **автоизвлечение тест-кейсов** из HTML-таблицы и ZIP-архивов, оркестрация вызовов API |
| `diagnostic_stepik.py` | Application / Diagnostics | Диагностика: проверяет структуру ответа API и корректность токена авторизации |
| `core/grader_core.py` | Application | Исполнение тест-кейса в subprocess и агрегация статистики: 4 режима работы (`run_tests`, `run_benchmark`, `run_microbench_mode`) |
| `core/test_loader.py` | Application | Обнаружение файлов-решений, загрузка тест-кейсов (`load_test_cases`), `resolve_test_dir` (Issue #45 A-01) |
| `core/mode_detector.py` | Application | Детекция режима запуска stdin/function (`_detect_run_mode`, `is_function_only_solution`) (Issue #45 A-01) |
| `core/wrapper_builder.py` | Application | Генерация wrapper-скриптов для function-mode запуска (Issue #45 A-01) |
| `core/reporter.py` | Application / UI | rich-таблицы с цветами, вердикты AC/WA/TLE/RE, verbose-diff при WA, адаптивное форматирование времени (`fmt_time`) |
| `core/executor.py` | Infrastructure | Запускатель решений: `compile + exec` с таймаутом и изолированным namespace |
| `core/microbench_runner.py` | Infrastructure | Timeit-микробенчмарк через subprocess (`python -c`) + подавление stdout решения в `os.devnull`; peak memory через `tracemalloc` |
| `core/normalizers.py` | Infrastructure / Utilities | Нормализация вывода для сравнения: `normalize_floats` (округление float до 9 знаков), `sort_lines`, `normalize_whitespace` (experimental) |
| `core/storage.py` | Infrastructure / Utilities | Чтение и запись JSON-файлов (`load_json_file`, `save_json_file`, `save_secrets`); нет зависимостей от других модулей проекта |
| `core/stepik_client.py` | Infrastructure / HTTP | OAuth2-авторизация, `requests.Session`, GET-запросы к Stepik REST API, скачивание сабмишнов |
| `core/oauth_flow.py` | Infrastructure / Auth | OAuth2-фасад: единая точка входа для авторизации — `load_secrets`, `load_secrets_dict`, `token_is_valid`, `authorize_and_get_token`; устраняет дублирование между `downloader.py` и `diagnostic_stepik.py` |
| `core/parsers.py` | Infrastructure / Utilities | Парсинг тест-блоков (`# TEST_N:`) — единственный источник истины для `grader.py` и `downloader.py` |
| `glossary/models.py` | Domain (leaf) | Типизированные модели локального глоссария: `GlossaryCard`, `GlossaryMissingEntry` (issue #126) |
| `glossary/json_provider.py` | Domain | `JsonGlossaryProvider` (загрузка/поиск локальной JSON-базы карточек) + очередь пополнения (issue #126) |
| `glossary/detector.py` | Domain | `MissingConceptDetector` — консервативный AST-детектор недостающих функций/конструкций/исключений (issue #126) |

Основные возможности (пользовательский взгляд) — в [README](../README.md);
пошаговые сценарии работы — в [grader-workflow.md](grader-workflow.md).

## Граф зависимостей

Граф зависимостей — DAG без циклов (все модули живут в `src/stepik_grader/`):

```
downloader.py          ──→  core/storage.py
downloader.py          ──→  core/stepik_client.py
downloader.py          ──→  core/parsers.py
core/stepik_client.py ──→  core/storage.py
grader.py              ──→  core/grader_core.py, core/reporter.py, cli.py  (тонкий фасад)
core/grader_core.py    ──→  core/executor.py, core/microbench_runner.py, core/normalizers.py
core/grader_core.py    ──→  core/test_loader.py, core/mode_detector.py, core/wrapper_builder.py
core/test_loader.py    ──→  core/mode_detector.py, core/parsers.py
core/mode_detector.py  ──→  core/storage.py
cli.py                 ──→  core/grader_core.py, core/reporter.py, core/microbench_runner.py
diagnostic_stepik.py ──→  core/stepik_client.py
diagnostic_stepik.py ──→  downloader.py       ← parse_stepik_step_url
downloader.py        ──→  core/oauth_flow.py
diagnostic_stepik.py ──→  core/oauth_flow.py
core/oauth_flow.py    ──→  core/stepik_client.py
core/oauth_flow.py    ──→  core/storage.py
glossary/json_provider.py ──→  glossary/models.py
glossary/detector.py      ──→  glossary/models.py
```

Подпакет `glossary/` (issue #126) — самодостаточный островок: зависит только
от stdlib и собственных `glossary/models.py`, не импортирует `core/*` и не
импортируется из него. Это сохраняет DAG ацикличным; будущий web-слой
(#125/#129) станет его потребителем, как `web → core`.

downloader.py больше не импортирует grader.py: дублирующая копия
`_parse_testblock_file` в grader.py устранена (Issue #19) — оба модуля
читают `parse_testblock_file` из `core/parsers.py`.

Слои (снизу вверх):

```
┌───────────────────────────────────────────────────────────────┐
│  Domain / Application  (src/stepik_grader/ — точки входа)      │
│  downloader.py  │  grader.py (facade)  │  diagnostic_stepik   │
├───────────────────────────────────────────────────────────────┤
│  Application  (core/, грейдер разбит по SRP — Sprint 7, A-01) │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
│  core/test_loader.py │ core/mode_detector.py │ wrapper_builder │
│  cli.py (меню, публичная точка входа — stepik-grader)          │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure  (core/)                                       │
│  core/stepik_client.py  │  core/executor.py                    │
│  core/microbench_runner.py  │  core/oauth_flow.py              │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (core/, leaf, no deps)            │
│  core/storage.py  │  core/normalizers.py                       │
└───────────────────────────────────────────────────────────────┘
```

`core/storage.py` и `core/normalizers.py` — leaf-модули: не импортируют ничего из проекта, легко тестируются изолированно.
