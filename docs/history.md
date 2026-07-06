# История разработки (архив)

> **Архивный документ.** Здесь собрана история спринтов, roadmap-партий и
> подробные примечания к issue, которые раньше жили в корневом `CLAUDE.md`
> (вынесено по issue #176 / эпик #174). Это справочная память «как мы сюда
> пришли», а не действующие инструкции.
>
> Действующий агентский контракт — в [`../CLAUDE.md`](../CLAUDE.md).
> Актуальная архитектура — в [architecture.md](architecture.md) и
> [project-structure.md](project-structure.md). Полный список изменений — в
> [`../CHANGELOG.md`](../CHANGELOG.md). Политика версионирования — в
> [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
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
[`../CONTRIBUTING.md` § Версионирование](../CONTRIBUTING.md#версионирование-issue-68).

---

## Эволюция метрик проекта

| Релиз | Тестов | Покрытие | Ключевое |
|---|---|---|---|
| v1.0.0 | 260 | 59% | Первый стабильный форк |
| v1.1.0 | 523 | 95% | src-layout, зрелая архитектура |
| v1.2.0 | 591 | 96% | Безопасность, кроссплатформа, дистрибуция |
| v1.3.0 | 599 | 95% | Онбординг + PyPI |
| v1.4.0 | 622 | 95% | Web UI (`--serve`) + IDE-интеграция |
| v1.5.0 | 660 | 95% | Кэш, pytest-плагин, инкрементальный watch |

> Подробное сравнение релизов и отличия от оригинала — в
> [versions.md](versions.md).
