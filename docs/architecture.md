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
| `config.py` | Application / Configuration | `GraderConfig` (frozen dataclass) + ленивый `CONFIG` (module `__getattr__`, PEP 562) / `get_config()` — импорт модуля не читает `pyproject.toml`, чтение кэшируется при первом обращении (issue #141/#142); переопределяется через `[tool.stepik-grader]` |
| `downloader.py` | Domain / Application | Управление конфигом и secrets, разбор URL шага, построение директорий задач (`slugify`, `build_task_directory`), сохранение файлов задачи, **автоизвлечение тест-кейсов** из HTML-таблицы и ZIP-архивов, оркестрация вызовов API |
| `diagnostic_stepik.py` | Application / Diagnostics | Диагностика: проверяет структуру ответа API и корректность токена авторизации |
| `web.py` | Application / Web | Локальная веб-оболочка `--serve` на stdlib `http.server` (endpoint `/api/grade`, `ResultViewModel`); тонкий слой поверх ядра, бизнес-логики грейдинга не добавляет. Текущая реализация — корректность + бенчмарк; дизайн будущего WEB MVP — [web-mvp.md](web-mvp.md) |
| `ide.py` | Application / IDE | IDE-интеграция `--init-vscode`: генерация конфигов VS Code (tasks/launch) |
| `pytest_plugin.py` | Application / Plugin | pytest-плагин (`pytest --grader-mode`, issue #57): запуск тест-кейсов грейдера как pytest-тестов |
| `core/cache.py` | Infrastructure / Utilities | Кэш результатов `.grader_cache/` (issue #56): ключ по контенту решения+тестов, graceful degradation при битом/отсутствующем кэше |
| `core/glossary.py` | Infrastructure / Utilities (leaf) | Компактная встроенная карта исключений (`GlossaryEntry.anchor`/`.url`, `GLOSSARY_BASE_URL`, ~28 записей) для error cards при RE; leaf-модуль, отдельная сущность от пакета `glossary/` (issue #72) |
| `core/grader_core.py` | Application | Исполнение тест-кейса в subprocess и агрегация статистики: 4 режима работы (`run_tests`, `run_benchmark`, `run_microbench_mode`) |
| `core/test_loader.py` | Application | Обнаружение файлов-решений, загрузка тест-кейсов (`load_test_cases`), `resolve_test_dir` (Issue #45 A-01) |
| `core/mode_detector.py` | Application | Детекция режима запуска stdin/function (`_detect_run_mode`, `is_function_only_solution`) (Issue #45 A-01) |
| `core/wrapper_builder.py` | Application | Генерация wrapper-скриптов для function-mode запуска (Issue #45 A-01) |
| `core/reporter.py` | Application / UI | rich-таблицы с цветами, вердикты AC/WA/TLE/RE, verbose-diff при WA, адаптивное форматирование времени (`fmt_time`) |
| `core/result.py` | Domain (leaf) | `TestResult` (frozen dataclass) + `Verdict` Literal — типизированная модель case result (issue #112/#113); `from_dict`/`to_dict` конвертируют форму, которую по-прежнему возвращает `run_single_test()` (`dict[str, Any]`, контракт не меняется — [result-contract.md](result-contract.md)); используется `core/reporter.print_case_verbose` вместо чтения произвольных dict-ключей |
| `core/runner.py` | Infrastructure | `Runner` Protocol + `RunSpec`/`RunOutcome` + `LocalRunner` — абстракция запуска кода (issue #136/#137/#138, `docs/server-mode.md § Runner-слой`); `LocalRunner` — subprocess + best-effort лимит памяти (POSIX) + psutil-мониторинг RSS, то же поведение, что раньше жило внутри `run_single_test`. Будущий `SandboxRunner` (issue #157) — тот же протокол, другая изоляция |
| `core/executor.py` | Infrastructure | Запускатель решений: `compile + exec` с таймаутом и изолированным namespace |
| `core/microbench_runner.py` | Infrastructure | Timeit-микробенчмарк через subprocess (`python -c`) + подавление stdout решения в `os.devnull`; peak memory через `tracemalloc` |
| `core/normalizers.py` | Infrastructure / Utilities | Нормализация вывода для сравнения: `normalize_floats` (округление float до 9 знаков), `sort_lines`, `normalize_whitespace` (experimental) |
| `core/storage.py` | Infrastructure / Utilities | Чтение и запись JSON-файлов (`load_json_file`, `save_json_file`, `save_secrets`); нет зависимостей от других модулей проекта |
| `core/stepik_client.py` | Infrastructure / HTTP | OAuth2-авторизация, `requests.Session`, GET-запросы к Stepik REST API, скачивание сабмишнов |
| `core/oauth_flow.py` | Infrastructure / Auth | OAuth2-фасад: единая точка входа для авторизации — `load_secrets`, `load_secrets_dict`, `token_is_valid`, `authorize_and_get_token`; устраняет дублирование между `downloader.py` и `diagnostic_stepik.py` |
| `core/parsers.py` | Infrastructure / Utilities | Парсинг тест-блоков (`# TEST_N:`) — единственный источник истины для `grader.py` и `downloader.py` |
| `core/i18n.py` | Infrastructure / Utilities (leaf) | `load_locale_messages(lang)` — JSON-локали `core/locales/<lang>.json` (issue #141/#144); аддитивный путь поверх статического `_MESSAGES` в `cli.py` — новые сообщения через JSON, без переписывания существующих; graceful degradation на отсутствующий/битый файл |
| `glossary/models.py` | Domain (leaf) | Типизированные модели локального глоссария: `GlossaryCard`, `GlossaryMissingEntry` (issue #126) |
| `glossary/json_provider.py` | Domain | `JsonGlossaryProvider` (загрузка/поиск локальной JSON-базы карточек) + очередь пополнения (issue #126) |
| `glossary/detector.py` | Domain | `MissingConceptDetector` — консервативный AST-детектор недостающих функций/конструкций/исключений (issue #126) |
| `glossary/stdlib_inventory.py` | Domain (leaf) | Офлайн-инвентарь официального Python/stdlib через интроспекцию (`build_stdlib_inventory`, `StdlibItem`, `NOTABLE_STDLIB_MODULES`) — source-driven сторона покрытия (issue #196); только stdlib, не тянет `core/*` |
| `glossary/coverage.py` | Domain | Сопоставление инвентаря с локальной базой (`build_coverage_report`, `missing_entries_from_inventory`) + CLI `python -m stepik_grader.glossary.coverage` (issue #197/#198); зависит только от leaf-модулей пакета `glossary/` |

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
core/grader_core.py    ──→  core/executor.py, core/microbench_runner.py, core/normalizers.py, core/runner.py
core/grader_core.py    ──→  core/test_loader.py, core/mode_detector.py, core/wrapper_builder.py
core/test_loader.py    ──→  core/mode_detector.py, core/parsers.py
core/mode_detector.py  ──→  core/storage.py
cli.py                 ──→  core/grader_core.py, core/reporter.py, core/microbench_runner.py
cli.py                 ──→  core/cache.py
cli.py                 ──→  core/i18n.py  (JSON-локали поверх статического _MESSAGES)
web.py                 ──→  core/grader_core.py, core/reporter.py, core/microbench_runner.py, core/test_loader.py  (web → core, ациклично)
web.py                 ──→  core/glossary.py  (lookup_from_error для error card при RE)
pytest_plugin.py       ──→  core/grader_core.py, core/test_loader.py  (импорты отложены в функции)
core/reporter.py       ──→  core/glossary.py  (glossary-блок в error card при RE)
core/reporter.py       ──→  core/result.py  (TestResult.from_dict в print_case_verbose)
ide.py                 (только stdlib — генерация конфигов VS Code; project-импортов нет)
diagnostic_stepik.py ──→  core/stepik_client.py
diagnostic_stepik.py ──→  downloader.py       ← parse_stepik_step_url
downloader.py        ──→  core/oauth_flow.py
diagnostic_stepik.py ──→  core/oauth_flow.py
core/oauth_flow.py    ──→  core/stepik_client.py
core/oauth_flow.py    ──→  core/storage.py
glossary/json_provider.py ──→  glossary/models.py
glossary/detector.py      ──→  glossary/models.py
glossary/stdlib_inventory.py  (только stdlib — интроспекция builtins/исключений/курируемых модулей; project-импортов нет)
glossary/coverage.py      ──→  glossary/stdlib_inventory.py, glossary/models.py, glossary/json_provider.py
```

Подпакет `glossary/` (issue #126) — самодостаточный островок: зависит только
от stdlib и собственных `glossary/models.py`, не импортирует `core/*` и не
импортируется из него. Это сохраняет DAG ацикличным; будущий web-слой
(#125/#129) станет его потребителем, как `web → core`.

**Модули покрытия глоссария (source-driven, issue #195–#198).**
`glossary/stdlib_inventory.py` — leaf: строит офлайн-инвентарь официального
Python/stdlib интроспекцией running-интерпретатора (без сети, без исполнения
пользовательского кода), не импортируя ничего из проекта.
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
│  web.py (--serve)  │  ide.py (--init-vscode)  │ pytest_plugin  │
├───────────────────────────────────────────────────────────────┤
│  Application  (core/, грейдер разбит по SRP — Sprint 7, A-01) │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
│  core/test_loader.py │ core/mode_detector.py │ wrapper_builder │
│  cli.py (меню, публичная точка входа — stepik-grader)          │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure  (core/)                                       │
│  core/stepik_client.py  │  core/executor.py                    │
│  core/microbench_runner.py  │  core/oauth_flow.py              │
│  core/cache.py (.grader_cache/, #56)                           │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (core/, leaf, no deps)            │
│  core/storage.py  │  core/normalizers.py  │  core/glossary.py  │
└───────────────────────────────────────────────────────────────┘
```

`core/storage.py`, `core/normalizers.py` и `core/glossary.py` — leaf-модули: не
импортируют ничего из проекта, легко тестируются изолированно. Пакет
`glossary/` (issue #126) — самостоятельный островок и НЕ то же самое, что
leaf-модуль `core/glossary.py`: первый — расширенный knowledge-модуль
(карточки/детектор/очередь), второй — компактная карта исключений для error
cards. Оба не тянут `core/*` бизнес-логику.
