# История разработки (архив)

> **Архивный документ.** Здесь собрана история спринтов, roadmap-партий и
> подробные примечания к issue, которые раньше жили в корневом `CLAUDE.md`
> (вынесено по issue #176 / эпик #174). Это справочная память «как мы сюда
> пришли», а не действующие инструкции.
>
> Действующий агентский контракт — в [`../CLAUDE.md`](../../CLAUDE.md).
> Актуальная архитектура — в [architecture.md](../dev/architecture.md) и
> [project-structure.md](../dev/project-structure.md). Полный список изменений — в
> [`../CHANGELOG.md`](../../CHANGELOG.md). Политика версионирования — в
> [`../CONTRIBUTING.md`](../../CONTRIBUTING.md).
>
> Ссылки на конкретные строки/размеры файлов и «текущие» метрики в тексте
> ниже отражают состояние на момент соответствующего спринта и намеренно **не
> обновляются** — это фотография прошлого.

---

## Ключевые архитектурные вехи (по issue)

- **Issue #19 (2026-07):** устранена дублирующая копия `_parse_testblock_file`
  в `grader.py` и локальный импорт `downloader → grader`, который её
  маскировал. Оба модуля импортируют `parse_testblock_file` напрямую из
  `core/parsers.py` — единственного источника истины. `downloader.py` больше
  не зависит от `grader.py`.

- **Issue #20 finding #4 / Sprint 7 (2026-07):** `grader.py` (1460 строк)
  разбит на `grader_core.py` (бизнес-логика), `reporter.py` (rich-вывод) и
  `cli.py` (меню). `grader.py` стал тонким фасадом — `from grader_core import
  *`, `from reporter import *`, явные реэкспорты приватных имён (`_verdict`,
  `_console`, `_RICH`, …), на которые опирается тестовый набор. `__all__` не
  изменился — обратная совместимость сохранена.

- **Issue #23 / #26 (2026-07):** `grader_core.py` и `reporter.py` перенесены в
  `core/` (теперь ВСЕ внутренние модули живут в `core/`; в корне пакета
  остаются точки входа `grader.py`/`cli.py`/`downloader.py`/
  `diagnostic_stepik.py` и `config.py`). Тесты, обращавшиеся к этим модулям
  напрямую, обновлены: `import grader_core`/`import reporter` → `from core
  import grader_core`/`from core import reporter`; `patch("reporter.X")` →
  `patch("core.reporter.X")`.

- **Issue #35 / Sprint 8.2 (2026-07):** все 16 исходных файлов перенесены в
  `src/stepik_grader/` (src-layout) — `git mv` сохранил историю. Каждый
  внутренний импорт получил префикс `stepik_grader.`. `pyproject.toml`:
  `[tool.setuptools.packages.find] where = ["src"]`, новый entry point
  `[project.scripts] stepik-grader = "stepik_grader.cli:main"`. `conftest.py`
  добавляет `src/` в `sys.path`, чтобы тесты работали без `pip install -e .`.
  `load_config()` резолвит `pyproject.toml` тремя уровнями выше своего
  `__file__`. Прямой запуск `python grader.py` из корня удалён — только
  `python -m stepik_grader.X` или `stepik-grader`. Все 523 теста прошли после
  экзаустивного grep-аудита импортов перед миграцией.

- **Issue #45 A-01 (2026-07):** `grader_core.py` (1200+ строк) разбит на
  `test_loader.py` (обнаружение файлов-решений, `load_test_cases`,
  `resolve_test_dir`), `mode_detector.py` (`_detect_run_mode`,
  `is_function_only_solution`, `_is_python_code_block`) и `wrapper_builder.py`
  (`_build_function_wrapper`, `_build_call_wrapper`). `grader_core.py` сохранил
  `run_single_test`/`run_tests`/`run_benchmark`/`run_microbench_mode` и
  реэкспортирует все 16 перенесённых имён по имени. Единственное направление
  зависимости между новыми модулями: `test_loader.py → mode_detector.py` —
  циклов нет. Перед разбиением отдельный агент проаудировал тесты на
  `monkeypatch`/`mock.patch`, нацеленные на перемещаемые имена — таких не
  нашлось, правки тестов не потребовались.

- **Issue #45 A-02 / A-04 (Sprint B, 2026-07):** устранён обратный импорт
  `core/grader_core.py → core/reporter.py`. `run_tests()` получил параметр
  `verbose_callback: Callable[[TestCase, dict], None] | None`; печать
  verbose-кейса теперь ответственность вызывающей стороны — `cli.py` передаёт
  `reporter.print_case_verbose` явно. A-04: `_resolve_test_dir` →
  `resolve_test_dir`, `_rich_track` → `rich_track`, `_print_case_verbose` →
  `print_case_verbose`; все три добавлены в `__all__` своих модулей — `cli.py`
  больше не импортирует приватные (`_`-префиксные) имена из других модулей.

---

## Спринты 6–8: критические исправления, рефакторинг, PyPI-ready

### Sprint 6 — критические исправления ✅ (2026-07-02)

- **6.1** `executor.py`: `_PYTHON_CMD` = `sys.executable` вместо
  платформо-зависимой строки `"python3"`/`"python"` (могла указать на
  системный Python вне venv на Windows).
- **6.2** `core/normalizers.py`: мёртвый код `sort_lines()` /
  `normalize_whitespace()` добавлен в `__all__`, докстринги помечают их
  «experimental» (не подключены ни к одному режиму), тесты уже существовали.
- **6.3** Создан `config.py` — единая конфигурация `GraderConfig`
  (`frozen=True`), читаемая из `[tool.stepik-grader]` в `pyproject.toml` через
  `tomllib`, с graceful fallback на дефолты. `grader_core.py` и `executor.py`
  читают значения из `CONFIG` при импорте (у `executor.py` — fallback на
  литерал `10`, т.к. как subprocess-скрипт он не видит `config.py`).

### Sprint 7 — рефакторинг `grader.py` ✅ (2026-07)

- **7.1** `grader.py` (1489 строк) разбит на `reporter.py` + `grader_core.py` +
  `cli.py` (перемещение, не переписывание); все тесты прошли без изменений;
  `grader.py` — тонкий фасад.
- **7.2** `BenchStats` dataclass в `grader_core.py` — унифицировал вычисление
  min/median/mean/stdev/max для режимов 3 и 4. Обе функции по-прежнему
  возвращают `dict` (внешний контракт не менялся).
- **7.3** `run_microbench_with_timeout()` добавлена в `microbench_runner.py`,
  но не подключена (обёртка поверх уже subprocess-таймаут-защищённого вызова
  не давала реальной защиты). **Issue #69 (v1.4.0-post):** функция удалена как
  вводящий в заблуждение мёртвый код — два релиза без production-вызова.

### Sprint 8 — CLI и PyPI-ready ✅ (2026-07-02 / 2026-07)

- **8.1** argparse CLI: `--version`, `--mode 1..4`, `--file`/`--dir`,
  `--repeats`/`--number`. `_run_mode_1..4()` — извлечённые тела режимов,
  переиспользуются меню и `main()`. `main(argv=None)` — явный `argv`-параметр.
- **8.2** src-layout (см. Issue #35 выше).

---

## Спринты A–E: аудит v1.1.0 (эпик #60)

### Sprint A — безопасность ✅ (2026-07-03)

- **A.1 (#44 S-03):** wildcard-импорты в `_build_call_wrapper` заменены явными
  импортами полного документированного публичного API `collections`/
  `datetime`/`itertools`/`functools`.
- **A.2 (#43 S-01):** best-effort memory cap для дочернего процесса.
  `GraderConfig.max_memory_mb` (дефолт 1024). **Issue #67 (v1.4.0-post):**
  `preexec_fn`+`_make_memory_limiter()` заменены на `_apply_memory_limit(pid,
  …)` через `resource.prlimit` ПОСЛЕ spawn — `preexec_fn` небезопасен в
  многопоточном родителе (грейдер держит psutil-поток мониторинга).
  `run_microbench` переведён с `subprocess.run` на
  `Popen`+`communicate(timeout=60)` ради pid. `prlimit` — Linux-only; cap
  best-effort, thread-safety важнее. **#43 S-02** закрыт как дубликат S-01.

### Sprint B — архитектура 🟡 частично (2026-07-03)

- **B.1 (#45 A-02, A-04):** layering и приватные кросс-модульные импорты (см.
  вехи выше).
- **B.2 (#46 A-03):** судьба `executor.py` — решено оставить как есть
  (тестируемый, но не production-задействованный модуль). Интеграция как
  unified runner невозможна без регресса (нет psutil/function-mode, `SIGALRM`
  не работает на Windows); перенос в `tests/helpers/` ломает существующие
  тесты, запускающие `executor.main()` из установленного пакета.
- **B.3 (#45 A-01):** разбиение `grader_core.py` — отложено, сделано отдельным
  заходом (см. вехи выше).

### Sprint C — надёжность ✅ (2026-07-03)

- **C.1 (#47 R-04):** `resolve_test_dir()` возвращает `None` вместо
  «призрачного» несуществующего пути; `cli.py` проверяет `is None` перед
  `Path(...).is_dir()`.
- **C.2 (#47 R-02):** голое имя без вызова/присваивания
  (`ast.Expr(ast.Name(...))`) → `_is_python_code_block()` возвращает `False`
  (намеренно узкая правка, не переписывание эвристики).
- **C.3 (#47 R-01):** 🟡 диагностика таймаута microbench включает `number=<N>`;
  настоящий per-call таймаут внутри `timeit.repeat()` не сделан (потребовал бы
  отказа от `timeit.repeat()` или `SIGALRM`, недоступного на Windows).
- **C.4 (#48 R-03, R-05):** `warnings.warn()` вместо тихих fallback'ов при
  «осиротевших» файлах смешанных форматов и при
  `NoSuchProcess`/`AccessDenied`/`ZombieProcess` в `_measure_peak_memory()`.

### Sprint D — CI/CD и качество ✅ (2026-07-03)

- **D.1 (#49 C-01):** matrix.os = ubuntu/windows/macos для Python 3.12/3.13
  (3.14-experimental — Ubuntu-only).
- **D.2 (#49 C-02):** mypy в CI (`mypy src/stepik_grader
  --ignore-missing-imports`), после ruff, перед pytest. Первый прогон вскрыл
  ~12 ошибок — все устранены перед включением шага (лишний `str()` в
  `_read_meta_function_name`; `str | None` из `resolve_test_dir` →
  `assert`/`is None` проверки в `cli.py`; точечные `# type:
  ignore[attr-defined]` на `signal.alarm`/`resource.setrlimit` для win32-стабов;
  `# type: ignore[misc]` на fallback-`rich_track`).
- **D.3 (#49 Q-01):** mock-тесты для ошибок GitHub API в `test_downloader.py`
  (ConnectionError, raise_for_status 404/500, нераспознанные файлы). Покрытие
  `downloader.py` 98% → 99%.

### Sprint E — UX/документация/зависимости ✅ (2026-07-03)

- **E.1 (#51 D-01):** i18n меню и CLI-сообщений — `_LANG` (дефолт "ru"),
  `_MESSAGES`, `_t(key, **kwargs)`, флаг `--lang {ru,en}`. Минимальный словарь
  вместо gettext (~30 сообщений). autouse-фикстура `_force_english` для старых
  тестов; новый `test_cli_sprint_e.py` проверяет русский дефолт и `--lang`.
- **E.2 (#50 D-03):** взаимоисключающие `--verbose`/`--quiet`.
- **E.3 (#50 D-04):** `--output {text,json}` во всех 4 режимах (сериализуются
  уже существующие dict'ы).
- **E.4 (#50 D-05):** содержательная диагностика «тесты не найдены».
- **E.5 (#50 D-02):** `CONTRIBUTING.md` уже существовал — точечно исправлены
  устаревшие места (Python 3.10+ → 3.12+, «опциональный rich», шаг mypy).
- **E.6 (#51 P-01):** удалён `requirements.txt` — `pyproject.toml`
  единственный источник зависимостей.
- **E.7 (#51 P-02):** верхние границы версий зависимостей
  (`requests>=2.34.2,<3.0`, `psutil>=5.9,<8.0`, `rich>=13.0,<16.0`) —
  выставлены над реально проверенными версиями, а не по устаревшему примеру
  из аудита.
- **E.8 (#51 C-03):** `.github/workflows/release.yml` (триггер: push тега
  `v*`) — sdist+wheel + GitHub Release. PyPI-публикация не включена (требует
  trusted publisher, настраивается владельцем репозитория).

---

## Roadmap-партии (#53–#72)

- **#53 / #58 (2026-07-03):** `--output csv`/`md` — тот же механизм, что
  `--output json`; `_rows_to_csv()`/`_rows_to_markdown()` пишут в stdout
  (единообразие с json/csv важнее буквального «сохраняет RESULTS.md»).
- **#54 (2026-07-03):** `--watch` — опциональная зависимость `watchfiles`
  (`stepik-grader[watch]`), только для `--mode 1/2`. Перезапускает весь режим
  на любое изменение.
- **#56 (2026-07-05):** opt-in кэш результатов. Leaf-модуль `core/cache.py`
  (stdlib `hashlib` + `core/storage.py`). Ключ — пара sha256 (содержимое
  решения + все файлы тест-директории). Хранилище — `.grader_cache/
  results.json` в CWD (в `.gitignore`). Флаги `--cache`/`--no-cache`
  (`BooleanOptionalAction`) и `--clear-cache`. Только `--mode 1/2`.
- **#55 (2026-07-05):** закрыт как `not_planned` — Stepik не публикует
  эталонный `solution.py`, сравнивать не с чем.
- **#57 (2026-07-06):** pytest-плагин. `pytest_plugin.py`, зарегистрирован как
  `pytest11` entry point. `pytest --grader-mode StepikTasks/` собирает каждый
  `task*.py` как `pytest.File`, по одному `pytest.Item` на тест-кейс, через тот
  же `run_single_test`. По умолчанию no-op. Импорты ядра — ленивые (внутри
  хуков): entry-point-плагины грузятся до старта coverage.py. Сам плагин
  исключён из измерения coverage; тела покрыты 16 тестами через `pytester`.
- **#71 (2026-07-06):** `--watch --mode 2` стал инкрементальным через кэш #56 —
  на событие перепрогоняется только изменённый файл. Под `--watch` кэш
  включается автоматически (`_resolve_use_cache(args,
  incremental=args.watch)`). Дефолт `--cache` изменён на `None`, чтобы
  отличать явные `--cache`/`--no-cache` от «не задано».
- **IDE launch fix (2026-07-06):** `--init-vscode` и рецепт PyCharm запускают
  грейдер через выбранный в IDE интерпретатор
  (`${command:python.interpreterPath} -m stepik_grader.grader …` /
  `$PyInterpreterDirectory$/python …`) вместо голого `stepik-grader` (был в
  PATH только при активированном venv).
- **#72 (2026-07-06, первый кирпич эпика #96):** ссылки на Glossary-Python при
  RE. Leaf-модуль `core/glossary.py` — курированная карта ~28 встроенных
  исключений → `{hint, url}`. Единый источник для `reporter.print_case_verbose`
  (CLI verbose) и `web._case_view` (карточка ошибки). `lookup_from_error`
  парсит имя исключения из последней строки трейсбека. DAG: `reporter →
  glossary`, `web → glossary` (glossary — leaf, циклов нет).

---

## Релиз v1.6.0 (2026-07-08)

- **Эпик #98 — packaging hygiene (PR-1):** явный MIT `LICENSE` в корне +
  PEP 639 SPDX-метаданные лицензии в `pyproject.toml` (issue #100); PEP 561
  `py.typed`-маркер для тайпчекеров downstream-потребителей (issue #101).
  `setuptools>=77` для поддержки SPDX.
- **Эпик #102 — документация (PR-2):** README сведён к лаконичной витрине,
  тяжёлые технические разделы вынесены в `docs/` — `architecture.md` (DAG +
  слои, #105), `project-structure.md` (дерево файлов, #104), `versions.md`
  (сравнение релизов, #106). CONTRIBUTING получил правило «README —
  витрина, docs/ — база знаний» (#107).
- **Эпик #123 — локальный глоссарий:**
  - **#126:** foundation `stepik_grader.glossary` — `GlossaryCard`/
    `GlossaryMissingEntry`, `JsonGlossaryProvider`, `MissingConceptDetector`
    (консервативный AST-детектор пропущенных концепций, не исполняет код).
  - **#194/#200:** зафиксирован инвариант источников истины — внутренняя
    база грейдера полнота контента, официальный Python/stdlib — полнота
    покрытия, внешний Glossary-Python — только витрина экспорта, никогда
    не эталон.
  - **#195–#198:** source-driven покрытие. `GlossaryMissingEntry` получил
    `origin`/`module`/`qualname`; leaf-модуль `stdlib_inventory.py` —
    офлайн-инвентарь builtins/exceptions/stdlib без сети и исполнения кода;
    `coverage.py` сопоставляет инвентарь с локальной базой карточек, CLI
    `python -m stepik_grader.glossary.coverage`.
- **Эпик #161/#163:** `--version` различает dev-сборки и релизы — вне тега
  добавляется суффикс `(dev build, not a release)` к строке
  `setuptools-scm`, на теге вывод не меняется.
- **Живые README-бейджи:** `scripts/generate_coverage_badge.py`/
  `generate_version_badge.py` пишут shields.io endpoint-JSON из реального
  `pytest --cov` и логической версии (`scripts/version.py`); CI
  (ubuntu-latest/3.12, push в main) коммитит их после каждого прогона —
  заменили статический coverage-бейдж, который тихо разошёлся с
  реальностью.
- **#201/#202:** политика ответственного раскрытия уязвимостей
  (`SECURITY.md`), GitHub PR/issue-шаблоны.

Полное содержание релиза — в [`changelog-archive.md`](changelog-archive.md#160---2026-07-08).

---

## Релиз v1.7.0 (2026-07-12)

Самый крупный релиз с момента появления версионирования — `[Unreleased]` не
резался с v1.6.0, поэтому сюда вошло сразу несколько эпиков.

- **Эпик #123 — WEB workspace (закрыт):** split-pane UI (sidebar/result/
  detail), расширенные ErrorCard-поля, command palette (Ctrl+K), раздел
  «Глоссарий» (поиск/карточка/backlog пополнения) — **#125**. Загрузчик
  задач как отдельный раздел навигации, OAuth без похода в браузер —
  **#186**. Микро-бенчмарк (режим 4) в вебе — **#187**. Сквозные
  user-journey тесты между адаптерами (Downloader→grade, error-card→glossary,
  missing-queue→adapter) — **#129**. Редактируемое окно кода режима 1
  (`POST /api/save-solution`) и режим «Сравнение» (`grade_benchmark(reference=)`,
  пока не подключён к фронту) — доделки #125.
- **`--sandbox` — SandboxRunner MVP (issue #266):** опциональная ОС-уровневая
  изоляция исполнения решений — bubblewrap на Linux, `sandbox-exec` на
  macOS, Job Objects на Windows (`core/sandbox/`). Гарантии асимметричны по
  ОС (документировано, не баг) — таблица и именованные пробелы в
  [`../SECURITY.md`](../../SECURITY.md). Новый вердикт `SANDBOX_VIOLATION`.
  Backend выбирается по ОС при старте; недоступность — явная ошибка, без
  тихого отката на `LocalRunner`.
- **Async job-модель для веб-бенчмарка (issue #262):** `POST /api/v1/runs` +
  `GET /api/v1/runs/{id}` + cancel — бенчмарк/микробенч в вебе больше не
  держат HTTP-запрос открытым на всё время прогона; прогресс-бар и кнопка
  отмены во фронте. `GET /api/grade` остаётся, но deprecated для bench/
  microbench.
- **Веб-безопасность (эпики #146/#151/#97, issues #240–#246, #259, #261,
  #242):** security-аудит нашёл и закрыл утечку OAuth-токена на сторонние
  хосты при скачивании тестов, Login-CSRF в OAuth-редиректе, отсутствие
  лимитов на размер тела запроса/числовые параметры (DoS), права `secrets.json`
  (0600 на POSIX), порядок импортов в wrapper-скрипте (shadowing
  stdlib-модуля), молчаливое усечение Format-3 тест-кейсов при несовпадении
  блоков. Добавлены path-confinement (`--root`/`--no-root-confinement`) и
  Host/Origin guard для `/api/*` — оба задокументированы в новом
  [`api.md`](../dev/api.md).
- **Локальная статистика запусков (issue #268):** opt-in `--stats`/
  `--stats-summary` — `.grader_stats.jsonl`, без сети, только на диске.
- **Path вместо str в публичных контрактах (issue #73, breaking):** все
  путь-параметры/возвраты в `core/`/`cli/`/`web/` теперь типизированы
  `pathlib.Path`, а не `str` — закрывает половинчатую типизацию, где внутри
  уже был `Path`, а на границе функции — `str`.
- **Документация — справочник HTTP API + разделение web-mvp.md (issue
  #267):** новый [`api.md`](../dev/api.md) — канонический справочник эндпоинтов
  `--serve`. `web-mvp.md` разделён на [`web-contracts.md`](../dev/web-contracts.md)
  (реализовано) и [`web-design.md`](../dev/design/web-design.md) (замыслы/отложенное/
  отклонённое). Исторические до-версионные записи `CHANGELOG.md` вынесены в
  [`changelog-archive.md`](changelog-archive.md). Плюс отдельный аудит
  точности документации (architecture.md/project-structure.md не знали про
  `core/sandbox/`/`web/runs.py`/`core/stats.py`; устаревшие версии в
  installation.md/CLAUDE.md и т.п.).
- **CI — честное покрытие по всей OS-матрице (issue #283, #286, #289):**
  `core/sandbox/`'s три ОС-специфичных backend'а сделали одиночный
  CI-job структурно неспособным показать больше ~86–90% (два backend'а из
  трёх всегда 0% на любой одной машине) — новый job `coverage-combine`
  склеивает покрытие всех трёх ОС в одну честную цифру (~93%). README
  показывает оба бейджа с разными подписями на самой картинке (не только в
  alt-тексте, issue #289), с `cacheSeconds=300` против затянутого
  camo/shields.io кэша.
- Меньшие темы: локализация web-API через `message_id`-каталог (issue
  #264), CodeMirror 6 вместо `<textarea>` в редакторе (issue #265),
  Playwright e2e-сьют (issue #263), шрифты/иконки без внешнего CDN (issue
  #260), Runner Protocol как реальная абстракция (`core/runner.py`, эпики
  #136–139), ленивый `CONFIG` + JSON-локали (эпики #141–145), retry/backoff
  в Stepik-клиенте (эпики #108–111), `TestResult`/`Verdict` как типизированная
  модель (эпики #112–115), `cli.py`/`web.py` разложены на пакеты (эпики
  #117–122).

Полное содержание релиза — в [changelog-archive.md](changelog-archive.md#170---2026-07-12).

---

## Динамическая версия (issue #162, PR #183)

- **#68:** задокументирована собственная схема версионирования (тег =
  MINOR+1, PATCH = число коммитов после тега, все теги = `vX.Y.0`), добавлен
  `scripts/version.py`.
- **#162 / PR #183:** сборка больше не объявляет `version` статически — она
  вычисляется из git-тегов через `setuptools-scm` (`dynamic = ["version"]`,
  `version_scheme = "post-release"`). На теге → `X.Y.0`, вне тега →
  `X.Y.0.postN+g<hash>`. `scripts/version.py` остаётся справочным
  человекочитаемым счётчиком.
- **#165:** `scripts/check_version_consistency.py` (CI) — guard против
  возврата статического source-of-truth в `pyproject.toml` и против дрейфа
  «текущей версии» в `CHECKPOINT.md`/`CHANGELOG.md`/`CLAUDE.md`.

Действующая политика версионирования — в
[`../CONTRIBUTING.md` § Версионирование](../dev/versioning.md).

---

## v1.8.0 (2026-07-14) — гигиена по аудиту 2026-07 (эпик #343)

Закрывающий релиз гигиена-эпика #343 (по итогам мультиролевого аудита #359)
перед стартом фич-эпика #342. Одним PR (#360) закрыты восемь дочерних issue:

- **#350–#352 (баги):** битый `TYPE_CHECKING`-импорт в `core/reporter.py`
  (`TestCase` был `Any`); `--sandbox` при `--serve` теперь честно отклоняется,
  а не игнорируется; гонки файловых записей (`core/stats.py`,
  `glossary/json_provider.py`) сериализованы `threading.Lock`.
- **#353 (доки):** точечный дрейф документации (sandbox-инвариант `CLAUDE.md`,
  статусы #146, `project-structure.md`, web-доки, `app.js`).
- **#354–#356 (консолидация «двойников»):** `__all__`×8 / `print`→`_console` /
  `relpath`→pathlib / фабрики-дедупы; два i18n-механизма сведены в единый
  JSON-каталог `core/locales/*.json`; два глоссария — в единый RE-резолвер
  `core/error_glossary.py` (bundled JSON → компактная карта fallback), общий
  для CLI и web.
- **#357 (тесты):** sleep-поллинг в async web-тестах заменён детерминированным
  `wait_until` (`tests/_wait.py`).

Метрики: 1179 → 1317 тестов. Квалити-гейты зелёные; CI-матрица (3 ОС ×
3.12/3.13/3.14) зелёная.

---

## v1.9.0 (2026-07-20) — наполнение оболочек: AI-подсказки, i18n web, разделы обучения, границы web↔core

Крупный фич-релиз поверх WEB workspace (#123): не новые «оболочки», а их
наполнение. Ключевое:

- **AI-подсказки (эпик E3, #435/#542–#544):** объяснение падающих WA/RE-кейсов
  через BYOK OpenAI-совместимый endpoint на голом `requests` — в CLI
  (`--ai-hints`, режимы 1–4) и в web (`POST /api/v1/hint`). Обязательный
  разовый consent-gate (код и ввод-вывод уходят провайдеру — приватность),
  grounding через локальный глоссарий (`core/ai_grounding`, AST без
  исполнения) для снижения галлюцинаций. Инвариант трёх зависимостей сохранён.
- **Полная локализация web-UI (эпик E4, #545–#547):** каталог
  `web/static/locales/ui.json` (ru/en), `data-i18n*`-атрибуты + `t()/tp()`
  в JS, CI-guard `check_ui_locale_guardrails.py` (паритет ключей ru↔en, нет
  голой кириллицы вне каталога).
- **Разделы обучения в web (эпики #342/#348):** «Правила» (PEP 8), «Подучить»
  (затухающие карточки частых ошибок), «Прогресс» (KPI, achievements,
  time-to-first-green) поверх opt-in истории прогонов на SQLite
  (`core/history.py`, `--history`); lint через ruff (`core/lint.py`,
  best-effort, не влияет на вердикт).
- **Границы web↔core (эпики E5/E6, ADR-0010/0011, #548–#553):** тонкий фасад
  `web/grading`, декларативный `Runner.supports_project_imports`, общий
  top-level `core/db.py` (SQLite/WAL) и `atomic_io.py` (атомарная запись JSON),
  очередь пополнения глоссария переехала JSON → SQLite.
- **Глоссарий #363 завершён:** 832 авточерновика → 0, ~1333 `ready`-карточки
  против официального Python/stdlib (волны В1–В6 «строки/числа/коллекции/
  байты», батчи 1–20 модулей/операторов/типизации/системных модулей); фильтр
  инвентаря по `sys.stdlib_module_names`; живой бейдж числа карточек (#398).
- **Витрина README + онрамп (эпик E9, #560–#562):** сравнительная таблица
  «чем отличается от оригинала», блок «Прозрачность и доверие», раздел «Первый
  вклад за 15 минут»; визуальные ассеты и community-настройка вынесены в
  отдельные maintainer-задачи (#601/#602).
- **Разное:** web `--serve --sandbox` изолирует исполнение (#396); импорт
  эталонного решения Stepik (`--import-reference`, #55); удалён мёртвый
  `core/executor.py` (#406); mypy-строгость в конфиге (#441); back-pressure
  `POST /api/v1/runs` (#429); `app.js` разбит на ES-модули (#426).

Метрики: 1317 → 1600+ тестов (точное число — из CI-прогона). Квалити-гейты
зелёные; CI-матрица (3 ОС × 3.12/3.13 + ubuntu 3.14-experimental) зелёная.

---

## v1.10.0 (2026-07-30) — канал обратной связи, глоссарий по официальному Python, периметр безопасности

Первый релиз, у которого есть **обратная связь**: до него пользователь, нашедший
баг, не имел точки входа ни в CLI, ни в вебе. Плюс завершение большой работы по
глоссарию и по безопасности исполнения чужого кода.

- **Канал обратной связи (эпик #751, #752–#754):** пункт `9` интерактивного меню
  и кнопка 💬 в topbar веба открывают **заполненную** форму issue на GitHub.
  Шаблоны переведены на YAML Issue Forms и дополнены типами «Идея» и «Задача
  Stepik проверяется неправильно»; `config.yml` уводит вопросы в Discussions.
  `core/feedback.py` собирает prefilled-URL: окружение (версия, ОС, Python,
  состояние `--sandbox`, коммит из git-клона), редакция секретов через
  `core/diag_log`, домашний путь свёрнут в `~`, укладывание в лимит длины URL с
  явным списком усечённого. Грейдер ничего не отправляет — Submit жмёт сам
  пользователь; код решения и имя машины не уходят никогда. В вебе —
  `POST /api/feedback` и модалка с обязательным предпросмотром.
- **Глоссарий доведён до официального Python (#684, #702, #704, #722,
  #743–#746, #762, #763):** покрытие stdlib 873/909 → 908/909, `body` 21% →
  77%, тысячи связей `related`/`related_errors`, `version` по маркеру «Added in
  version», примеры доведены до 3–6 содержательных и прогнаны интерпретатором.
  Слиты 30 пар дублей двух поколений импорта, 30 бандлов разобраны по принципу
  «одна концепция = одна карточка», сняты ссылки на внешнюю витрину (адрес
  карточки — свой якорь, наружу только `docs_url`). Аудит примеров стал
  машинным: вердикт не зависит от прогона и от машины, POSIX-only API помечен
  `platform:posix`, пропуск тега и проза вместо результата ловятся разбором AST,
  известный долг перечислен поимённо в `glossary_audit_known_issues.txt`.
- **Полировка веба (#601, #633–#637, #643–#645, #658–#661, #670, #683–#686,
  #723–#731):** стартовый онбординг, тосты, посимвольный diff при WA,
  GUI-лаунчер `stepik-grader-gui` без командной строки, `RU|EN` в topbar,
  навигация глоссария семействами разделов, панель «Доступ к Stepik» в
  загрузчике, отправка решения на Stepik из режима 1, диаграмма сравнения
  решений в режимах 3/4 и пре-флайт корректности перед замером скорости,
  focus-trap/Escape в модалках, перевод заголовков таблиц.
- **Периметр безопасности (#627–#632, #648, #691–#693):** fuzz входного тракта
  Stepik (hypothesis), исполняемые escape-PoC для всех трёх sandbox-backend'ов,
  лимит вывода решения на **всех** путях исполнения, обязательный consent перед
  отправкой кода AI-провайдеру и в CLI, атомарная запись `secrets.json`, чистка
  секретов из окружения дочернего процесса, запрет встраивания в iframe,
  инвентарь цепочки поставок с `pip-audit` в CI.
- **Подготовка к серверному пивоту (#638–#642, ADR-0009):** `RunSpec` стал
  сериализуемым (`code: bytes`, инвариант «path или code»), история прогонов
  получила `RunRecord` и абстракцию `HistoryRepository` (PostgreSQL-бэкенд
  встанет рядом, а не переписыванием), `Runner` выбирается в единственной точке
  `grader_core.run_spec()`, у истории появился retention, у `POST /api/v1/runs`
  — per-run `limits`.
- **Документация и её гейты (#700, #757–#761):** `docs/` разложена по читателю
  на четыре направления (`use/`, `dev/`, `agent/`, `archive/`) плюс `audit/` под
  незакрытые аудиты; пять CI-защит вместо одной — полнота индексов, направления,
  бюджет README, бюджет версий CHANGELOG и запрет журнала работ (`#NNN`) в
  объясняющих документах; `CHECKPOINT.md` удалён как дубль CHANGELOG,
  CONTRIBUTING сжат 438 → 306 строк.

Метрики: 1600+ → 2253 теста, покрытие 94.8% (кросс-OS combined) / 92.6%
(ubuntu). Квалити-гейты зелёные; CI-матрица (3 ОС × 3.12/3.13/3.14) зелёная.

---

## Эволюция метрик проекта

> Архивный снимок. Канонический (живой) источник этой таблицы —
> [versions.md](../use/versions.md); при расхождении верна она.

| Релиз | Тестов | Покрытие | Ключевое |
|---|---|---|---|
| v1.0.0 | 260 | 59% | Первый стабильный форк |
| v1.1.0 | 523 | 95% | src-layout, зрелая архитектура |
| v1.2.0 | 591 | 96% | Безопасность, кроссплатформа, дистрибуция |
| v1.3.0 | 599 | 95% | Онбординг + PyPI |
| v1.4.0 | 622 | 95% | Web UI (`--serve`) + IDE-интеграция |
| v1.5.0 | 660 | 95% | Кэш, pytest-плагин, инкрементальный watch |
| v1.6.0 | 784 | 95% | Локальный глоссарий + source-driven покрытие, packaging hygiene, README-витрина |
| v1.7.0 | 1179 | 93% (кросс-OS combined) | WEB workspace (эпик #123), `--sandbox`, async web-бенчмарк, security-аудит, Path-контракты, `docs/api.md` |
| v1.8.0 | 1317 | 93% (кросс-OS combined) | Гигиена аудита 2026-07 (эпик #343): багфиксы, гонки записей, дрейф доков, единый i18n-каталог, единый RE-резолвер глоссария, детерминированные web-тесты |
| v1.9.0 | 1600+ | ~93% (кросс-OS combined) | Наполнение оболочек: AI-подсказки (E3), i18n web-UI (E4), разделы обучения Прогресс/Правила/Подучить (#342/#348), границы web↔core (ADR-0010/0011), глоссарий #363 завершён (0 черновиков) |
| v1.10.0 | 2253 | 94.8% (кросс-OS combined) | Канал обратной связи (эпик #751), глоссарий по официальному Python (stdlib 908/909, машинный аудит примеров), периметр безопасности (fuzz, escape-PoC, лимит вывода), подготовка к серверу (сериализуемый `RunSpec`, `HistoryRepository`), docs по четырём направлениям с пятью гейтами |

> Подробное сравнение релизов и отличия от оригинала — в
> [versions.md](../use/versions.md).
