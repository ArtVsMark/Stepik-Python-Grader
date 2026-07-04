# CHECKPOINT — Stepik-Python-Grader

Файл фиксирует текущее состояние проекта: что сделано, что в работе, что запланировано.
Историю изменений по версиям см. в `CHANGELOG.md`.

---

## Текущая версия: 1.2.0 (2026-07-04)

### Статус: ✅ Стабильный

- Тестов: 591
- Покрытие: 96%
- Python: 3.12 / 3.13 / 3.14
- CI: GitHub Actions (ruff + mypy + pytest), матрица ubuntu/windows/macos
  × 3.12/3.13 + ubuntu 3.14-experimental (Sprint D, 2026-07-03), зелёный
- Эпик #18 (issues #19/#20/#21/#23) и issues #24/#25/#26 — закрыты, смержены в `main`
- Issue #35 (Sprint 8.2, src/-layout) — закрыт (2026-07-03)
- Эпик #60 (аудит v1.1.0), Sprints A–E: issues #43–#54, #58 (частично) —
  закрыты, вошли в v1.2.0
- Второй раунд аудита (2026-07-04): #64 (UTF-8 stdio), #65
  (`python -m stepik_grader`), #66 (колонка `Py-heap` в режиме 4), #68
  (схема версионирования + `scripts/version.py`) — вошли в v1.2.0 (PR #76)

---

## Архитектура

> src/-layout (Issue #35, 2026-07-03): весь пакет перенесён в
> `src/stepik_grader/`. Запуск только через `python -m stepik_grader.X` или
> консольную команду `stepik-grader` (после `pip install -e .`).

```
Stepik-Python-Grader/
├── src/
│   └── stepik_grader/
│       ├── grader.py          # Тонкий фасад обратной совместимости (93 строки,
│       │                      # 7 исполняемых Stmts по pytest-cov)
│       ├── cli.py             # Интерактивное меню (режимы 0-4) + argparse CLI, entry point stepik-grader
│       ├── config.py          # GraderConfig/CONFIG — единая конфигурация
│       ├── downloader.py      # Скачивание задач, ZIP/HTML, slugify
│       ├── diagnostic_stepik.py  # Диагностика API и токена
│       └── core/              # Все внутренние модули (Issues #23, #26)
│           ├── grader_core.py         # Загрузка тест-кейсов, исполнение решений
│           ├── reporter.py             # rich-таблицы, вывод, verbose-diff
│           ├── executor.py             # compile + exec с таймаутом
│           ├── microbench_runner.py    # timeit-микробенчмарк через subprocess
│           ├── normalizers.py          # Нормализация float-вывода
│           ├── storage.py              # load/save JSON
│           ├── stepik_client.py        # HTTP-клиент Stepik API
│           ├── oauth_flow.py           # OAuth2-фасад
│           └── parsers.py              # Парсинг тест-блоков (# TEST_N:)
├── conftest.py             # sys.path.insert(0, "src") — тесты без install
├── tests/                  # 523 теста
├── .github/workflows/      # ci.yml, claude.yml, claude-code-review.yml
├── CLAUDE.md / CHECKPOINT.md / CHANGELOG.md / CONTRIBUTING.md
└── pyproject.toml          # packages.find where=["src"], entry point stepik-grader
```

Только `grader.py`, `cli.py`, `config.py`, `downloader.py` и
`diagnostic_stepik.py` (все внутри `src/stepik_grader/`) — публичные точки
входа; всё остальное внутреннее живёт в `core/`. Правило зафиксировано в
`CONTRIBUTING.md` ("Правила размещения файлов").

---

## Реализованные возможности

### Режим 1 — Проверка одного файла
- `run_tests(solution_path, test_dir, verbose=True)`
- Verbose-вывод с diff при WA
- Поддержка stdin и function-mode

### Режим 2 — Сравнение всех решений в папке
- `find_all_solution_files()` + `run_tests()` для каждого
- Rich progress bar (при наличии `rich`)
- Таблица корректности с сортировкой

### Режим 3 — Subprocess-бенчмарк
- `run_benchmark()` с профилями нагрузки (5/15/50/custom повторений)
- Статистика: min, median, mean, max, stdev, peak memory (psutil RSS)
- Вердикты: SIMILAR / SLOWER / MUCH_SLOWER
- Адаптивное форматирование времени (`fmt_time`: s/ms/µs/ns) — Issue #24

### Режим 4 — Timeit micro-benchmark
- `run_microbench_mode()` + `run_microbench()` из `core/microbench_runner.py`
- Профили: 500 / 1K / 5K / 50K / 100K / custom итераций
- Группировка по папкам (`collect_grouped_files()`)
- Peak memory через `tracemalloc` (Python-heap, не RSS) — Issue #25

### Non-interactive CLI (Sprint 8.1)
```bash
stepik-grader --mode 1 --file path/to/task.py
stepik-grader --mode 2 --dir path/to/folder
stepik-grader --mode 3 --dir path/to/folder --repeats 15
stepik-grader --mode 4 --dir path/to/folder --number 1000
stepik-grader --version
```
Без `--mode` — обычное интерактивное меню. Эквивалентно через
`python -m stepik_grader.grader --mode ...`.

### Форматы тест-кейсов
- **Формат 1** — legacy downloader (`1`, `1.clue`, `1.type`)
- **Формат 2** — новый (`input_1.txt`, `expected_1.txt`)
- **Формат 3** — python-generation (`input.txt` + `output.txt` с блоками `# TEST_N:`)

### Автодетекция режима
- `_detect_run_mode()` — единая точка: meta.json → .type-файлы → AST-анализ
- `_apply_run_mode_override()` — синхронизирует все test_cases

### Конфигурация (Sprint 6.3)
- `config.py`: `GraderConfig` (frozen dataclass) + `CONFIG` singleton
- Переопределяется через `[tool.stepik-grader]` в `pyproject.toml`
- `core/grader_core.py` и `core/executor.py` читают константы из `CONFIG`

### Вывод
- Rich-таблицы (при наличии `rich`) с цветными вердиктами
- Graceful fallback на plain-text

---

## Зависимости

| Пакет | Тип | Назначение |
|---|---|---|
| `psutil` | Обязательная | Замер памяти дочернего процесса |
| `rich` | Опциональная | Цветные таблицы, progress bar |
| `requests` | Обязательная (downloader) | HTTP-запросы к Stepik API |

---

## ⚠️ Активный tech debt (некритично)

| Проблема | Приоритет | Issue |
|---|---|---|
| `run_microbench_with_timeout()` добавлена, но не подключена (см. докстринг — существующий `subprocess.run(timeout=60)` уже достаточен) | Низкий | — |
| Glossary-Python (смежный проект) разморожен, но без документации | Низкий | #38 |

---

## Следующие шаги (backlog)

- [x] #31 — CLAUDE.md: обновить оставшиеся устаревшие места (2026-07-03)
- [x] #32 — README.md: полная синхронизация со структурой `core/` (2026-07-03)
- [x] #34 — подтвердить точность метрик документации после рерайта (2026-07-03)
- [x] #37 — переименовать `diagnostik_stepik.py` → `diagnostic_stepik.py` (2026-07-03)
- [x] #36 — `__version__` через `importlib.metadata.version()` (DRY, 2026-07-03)
- [x] #35 — Sprint 8.2: `src/`-layout (`src/stepik_grader/`, console-script `stepik-grader`, 2026-07-03)
- [x] #44 — Sprint A: заменить wildcard-импорты в `_build_call_wrapper` на явные (2026-07-03)
- [x] #43 — Sprint A: best-effort `RLIMIT_AS` memory cap (`GraderConfig.max_memory_mb`,
  POSIX-only); S-02 закрыт как дубликат S-01 — см. CHANGELOG (2026-07-03)
- [x] #52 — Sprint B: убрать константы из `grader_core.__all__` (Q-03, 2026-07-03)
- [x] #45 — Sprint B: A-02 (verbose_callback вместо импорта reporter), A-04
  (`resolve_test_dir`/`rich_track`/`print_case_verbose` — убраны `_`-префиксы) (2026-07-03)
- [x] #46 — Sprint B: A-03 — решено оставить `executor.py` как есть (не
  test-only, не unified runner) — см. CLAUDE.md Sprint B.2 (2026-07-03)
- [x] #47 — Sprint C: R-04 (`resolve_test_dir` → `str | None`), R-02 (голое
  имя без вызова/присваивания → False), R-01 (диагностика таймаута
  microbench — номер итерации; настоящий per-call таймаут не сделан, см.
  CLAUDE.md Sprint C.3) (2026-07-03)
- [x] #48 — Sprint C: R-03 (warning при смешанных форматах 3+1/2), R-05
  (warning при NoSuchProcess в `_measure_peak_memory`) (2026-07-03)
- [x] #49 — Sprint D: C-01 (Windows/macOS в CI-матрице), C-02 (mypy в CI +
  dev-зависимостях, ~12 ошибок исправлено), Q-01 (mock-тесты для GitHub API
  errors, `downloader.py` 98% → 99%) (2026-07-03)
- [x] #50 — Sprint E: D-01 (i18n, ru/en), D-03 (`--verbose`/`--quiet`), D-04
  (`--output json` для режимов 1-4), D-05 (содержательная диагностика
  "тесты не найдены"); D-02 — устаревшее утверждение аудита,
  CONTRIBUTING.md уже существовал (2026-07-03)
- [x] #51 — Sprint E: P-01 (удалён `requirements.txt`), P-02 (верхние
  границы зависимостей — скорректированы под реально установленные версии,
  не буквально по issue), C-03 (`release.yml`, только GitHub Release, без
  PyPI — нужен trusted publisher, который агент не настроит) (2026-07-03)
- [x] #53 — Roadmap: `--output csv` (тот же механизм, что json) (2026-07-03)
- [x] #54 — Roadmap: `--watch` для `--mode 1/2` (опциональная зависимость
  `watchfiles`); перезапускает весь режим, не только изменённый файл
  (2026-07-03)
- [x] #58 (частично) — Roadmap: экспорт в Markdown (`--output markdown`);
  Web UI / VS Code / PyPI — не взяты (2026-07-03)
- [x] #45 A-01 — разбит `grader_core.py` (1200+ строк) на `test_loader.py`,
  `mode_detector.py`, `wrapper_builder.py`; все 16 перенесённых имён
  реэкспортированы из `grader_core.py` по имени — `__all__`/`grader.py`/
  `cli.py` не изменились; правок тестов не потребовалось (агент
  предварительно проверил, что monkeypatch/patch не целятся в эти имена)
  (2026-07-03)
- [ ] #55 — Roadmap: сравнение с `solution.py` Stepik как baseline
- [ ] #56 — Roadmap: `.grader_cache/` — кэширование результатов
- [ ] #57 — Roadmap: pytest-плагин (`pytest --grader-mode`) — по сути
  отдельный пакет
- [ ] #59 — Roadmap: Docker-sandbox, другие платформы, AI-подсказки
  (нужен внешний API-ключ), дашборд прогресса (зависит от #56)
- [ ] #38 — Glossary-Python: минимальная документация (отдельный репозиторий)
- [ ] Расширить покрытие тестами (особенно `downloader.py`)
