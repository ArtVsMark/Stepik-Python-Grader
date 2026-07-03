# CHECKPOINT — Stepik-Python-Grader

Файл фиксирует текущее состояние проекта: что сделано, что в работе, что запланировано.
Историю изменений по версиям см. в `CHANGELOG.md`.

---

## Текущая версия: 1.1.0 (2026-07-03)

### Статус: ✅ Стабильный

- Тестов: 523 (3 skipped)
- Покрытие: 95%
- Python: 3.12 / 3.13 / 3.14
- CI: GitHub Actions (pytest + ruff), зелёный
- Эпик #18 (issues #19/#20/#21/#23) и issues #24/#25/#26 — закрыты, смержены в `main`
- Issue #35 (Sprint 8.2, src/-layout) — закрыт (2026-07-03)

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
- [ ] #38 — Glossary-Python: минимальная документация (отдельный репозиторий)
- [ ] Расширить покрытие тестами (особенно `downloader.py`)
