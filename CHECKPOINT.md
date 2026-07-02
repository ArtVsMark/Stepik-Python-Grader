# CHECKPOINT — Stepik-Python-Grader

Файл фиксирует текущее состояние проекта: что сделано, что в работе, что запланировано.

---

## Текущая версия: 1.0.0

### Статус: ✅ Стабильный

---

## Аудит 2026-06-25

### ✅ Подтверждено работающим

| Компонент | Статус | Примечание |
|---|---|---|
| `_SOLUTION_FILE_RE` | ✅ Корректен | Матчит `task_1.py`, `task1_2.py`, `task.py` |
| `difflib` import | ✅ Top-level | Находится в начале файла, не lazy |
| Type hints | ✅ Полные | Все публичные функции покрыты |
| `__all__` | ✅ Есть | Экспортирует только публичное API |
| `_apply_run_mode_override` | ✅ Работает | Устраняет рассинхронизацию режимов |
| `PYTHONIOENCODING` / `PYTHONUTF8` | ✅ Есть | Решает кодировку на Windows |
| `contextlib.suppress(OSError)` | ✅ Есть | Безопасное удаление temp-файлов |
| `strict=True` в `zip()` | ✅ Есть | Ловит рассинхронизацию блоков |
| pre-commit | ✅ Настроен | `.pre-commit-config.yaml` |
| pytest | ✅ Настроен | `conftest.py`, `tests/` |

### ⚠️ Tech debt (некритично)

| Проблема | Приоритет | Решение |
|---|---|---|
| Дублирование статистики в `_micro_stats()` и `run_benchmark()` | Низкий | Уже частично через `_micro_stats()` |
| Глобальный `_console` singleton | Низкий | Допустимо для CLI-инструмента (теперь в `reporter.py`) |
| Нет `src/`-layout | Низкий | Рефакторинг при публикации на PyPI |

### ✅ Исправлено в сессии 2026-07-02

- **Issue #23** — внутренние модули (`executor.py`, `normalizers.py`, `parsers.py`,
  `storage.py`, `stepik_client.py`, `oauth_flow.py`, `microbench_runner.py`)
  перенесены в `core/`
- **Issue #19** — устранена дублирующая копия `_parse_testblock_file` в
  `grader.py`; `downloader.py` больше не импортирует `grader.py`
- **Issue #21** — `except Exception` в `microbench_runner.py` сужен до
  `(OSError, ValueError)`; редундантные `float(str(x or 0))` упрощены в
  `stepik_client.py`; добавлен `tests/test_cli.py` (покрытие `cli.py`:
  40% → 97%); переписана секция "Ограничения и безопасность" в README.md
  — **эпик #18 (issues #19/#20/#21) полностью закрыт**
- **Issue #20 finding #5** — валидация identifiers (`function_name`,
  module stem) перед интерполяцией в generated-код (`_build_function_wrapper`)
- **Issue #20 finding #6** — дублирующаяся ranking-логика (relative/verdict)
  вынесена в `core/microbench_runner.apply_relative_ranking()`
- **Issue #20 finding #4 / Sprint 7** — `grader.py` (1460 строк) разбит на
  `grader_core.py` + `reporter.py` + `cli.py`; `grader.py` стал тонким
  фасадом обратной совместимости

### ✅ Исправлено в предыдущей сессии

- Добавлен `CONTRIBUTING.md` с архитектурой, форматами тестов, соглашениями по коду
- Обновлён `CHECKPOINT.md` с результатами аудита

---

## Реализованные возможности

### Режим 1 — Проверка одного файла
- Запуск через `run_tests(solution_path, test_dir, verbose=True)`
- Verbose-вывод с diff при WA
- Поддержка stdin и function-mode

### Режим 2 — Сравнение всех решений в папке
- `find_all_solution_files()` + `run_tests()` для каждого
- Rich progress bar (при наличии `rich`)
- Таблица корректности с сортировкой

### Режим 3 — Subprocess-бенчмарк
- `run_benchmark()` с профилями нагрузки (5/15/50/custom повторений)
- Статистика: min, median, mean, max, stdev, peak memory
- Вердикты: SIMILAR / SLOWER / MUCH_SLOWER

### Режим 4 — Timeit micro-benchmark
- `run_microbench_mode()` + `run_microbench()` из `microbench_runner.py`
- Профили: 500 / 1K / 5K / 50K / 100K / custom итераций
- Группировка по папкам (`collect_grouped_files()`)

### Форматы тест-кейсов
- **Формат 1** — legacy downloader (`1`, `1.clue`, `1.type`)
- **Формат 2** — новый (`input_1.txt`, `expected_1.txt`)
- **Формат 3** — python-generation (`input.txt` + `output.txt` с блоками `# TEST_N:`)

### Автодетекция режима
- `_detect_run_mode()` — единая точка: meta.json → .type-файлы → AST-анализ
- `_apply_run_mode_override()` — синхронизирует все test_cases

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

## Следующие шаги (backlog)

- [ ] Расширить покрытие тестами (особенно `downloader.py`, `cli.py`)
- [x] Выделить `cli.py` и `reporter.py` из `grader.py` (2026-07-02, Sprint 7)
- [x] Добавить GitHub Actions CI (pytest + ruff)
- [ ] Рассмотреть `src/`-layout для возможной публикации на PyPI
- [ ] Issue #21 (Low): narrow `except Exception` в `microbench_runner.py:160`,
      покрытие меню `cli.py`, `float(str(x or 0))` → `float(x or 0)`,
      документировать security-допущения (no sandbox)
