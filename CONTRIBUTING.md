# Contributing to Stepik-Python-Grader

Спасибо за интерес к проекту! Это руководство поможет быстро начать работу.

---

## Архитектура проекта

```
Stepik-Python-Grader/
├── grader.py            # Тонкий фасад обратной совместимости (Sprint 7)
├── cli.py                # Интерактивное меню (режимы 0-4)
├── config.py            # Конфигурация уровня проекта
├── downloader.py        # Загрузка задач/тестов со Stepik API
├── diagnostic_stepik.py # Диагностика и отладка API
├── core/                # Все внутренние модули проекта
│   ├── grader_core.py       # Загрузка тест-кейсов, исполнение решений
│   ├── reporter.py          # rich-таблицы, вывод, verbose-diff
│   ├── executor.py          # Запуск кода из строки (run_solution)
│   ├── microbench_runner.py # timeit-бенчмарк (run_microbench)
│   ├── normalizers.py       # Нормализация float-вывода
│   ├── storage.py           # Чтение JSON-файлов
│   ├── stepik_client.py     # HTTP-клиент Stepik API
│   ├── oauth_flow.py        # OAuth 2.0 авторизация
│   └── parsers.py           # Парсинг тест-блоков (# TEST_N:)
├── conftest.py          # pytest fixtures
└── tests/               # Автотесты
```

### Слои и зоны ответственности

| Модуль | Слой | Зона ответственности |
|---|---|---|
| `grader.py` | Application | Фасад — реэкспортирует grader_core/reporter/cli |
| `core/grader_core.py` | Application | Загрузка тест-кейсов, исполнение решений |
| `core/reporter.py` | Application / UI | rich-таблицы, вердикты, verbose-diff |
| `cli.py` | Application / CLI | Интерактивное меню, профили нагрузки |
| `core/executor.py` | Infrastructure | Subprocess-запуск кода из строки |
| `core/microbench_runner.py` | Infrastructure | timeit-замеры |
| `core/normalizers.py` | Domain | Нормализация float |
| `core/storage.py` | Infrastructure | I/O JSON |
| `downloader.py` | Application | Загрузка данных Stepik |
| `core/stepik_client.py` | Infrastructure | HTTP Stepik API |

---

## Правила размещения файлов

> **Корень проекта — не свалка. Только точки входа и инфраструктура проекта.**

### В корне остаются

| Файл / паттерн | Причина |
|---|---|
| `grader.py`, `cli.py` | Точки входа — запускаются пользователем напрямую |
| `config.py` | Project-level конфигурация; импортируется из `core/*` (перенос вызовет circular import) |
| `downloader.py`, `diagnostic_stepik.py` | Самостоятельные пользовательские утилиты |
| `conftest.py`, `pyproject.toml` | Инфраструктура тестирования и сборки |
| `*.md`, `*.txt`, `*.toml`, `*.json.example` | Документация и шаблоны конфигурации |

### В `core/` — всё остальное

Любой новый **внутренний модуль** (библиотечный код, не запускаемый пользователем напрямую) создаётся в `core/`, а не в корне.

**Правило одной строки:**
> Если файл не запускается пользователем напрямую и не является конфигурацией
> уровня проекта — его место в `core/`, а не в корне.

### В `tests/` — все тесты

Файлы `test_*.py` и `*_test.py` — только в `tests/`. Никаких тестовых файлов в корне.

---

## Быстрый старт

### Требования

- Python 3.10+
- Windows / macOS / Linux
- `.venv` (рекомендуется)

### Установка

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS/Linux

pip install -r requirements.txt
pip install rich             # опционально — цветной вывод
pip install -e .             # обязательно: cli.__version__ читается через
                              # importlib.metadata из package-метаданных
                              # (Issue #36) — без editable install падает
                              # на fallback "0.0.0+unknown"
```

> После bump'а версии в `pyproject.toml` перезапусти `pip install -e .`,
> иначе `cli.__version__`/`python grader.py --version` останутся
> показывать старое значение (package-метаданные не обновляются
> автоматически).

### Запуск тестов

```bash
pytest tests/ -v
```

### Pre-commit хуки

```bash
pip install pre-commit
pre-commit install
```

---

## Форматы тест-кейсов

Grader поддерживает три формата (в порядке приоритета):

### Формат 3 — Python-generation (приоритет 1)
```
tests/
  input.txt   # блоки с маркерами # TEST_1:, # TEST_2: ...
  output.txt  # блоки с маркерами # TEST_1:, # TEST_2: ...
```

### Формат 2 — новый (приоритет 2)
```
tests/
  input_1.txt    expected_1.txt
  input_2.txt    expected_2.txt
```

### Формат 1 — legacy downloader (приоритет 3)
```
tests/
  1      1.clue    (1.type — опционально, "function")
  2      2.clue
```

---

## Имена файлов решений

Grader автоматически ищет файлы по паттерну:

```
task.py        # базовый
task1.py       # с номером задачи
task1_2.py     # задача + номер решения
task_1.py      # стиль downloader.py
task_12.py     # двузначный суффикс
```

---

## Соглашения по коду

- **Type hints обязательны** для всех публичных функций
- **Docstrings** для всех публичных функций (краткий формат Google-style)
- **PEP 8** — форматирование через `ruff format` (настроен в `pyproject.toml`)
- **Линтинг** через `ruff check`
- Все импорты в начале файла (никаких lazy imports без явной причины)
- Константы модуля — в начале файла, до функций

---

## Процесс внесения изменений

1. Создайте ветку: `git checkout -b feat/your-feature`
2. Внесите изменения с тестами
3. Прогоните `pytest tests/ -v`
4. Прогоните `pre-commit run --all-files`
5. Создайте Pull Request в `main`

### Правила коммитов (Conventional Commits)

```
feat:  новая функциональность
fix:   исправление бага
refactor: рефакторинг без изменения поведения
test:  добавление/изменение тестов
docs:  документация
chore: инфраструктура, зависимости
```

---

## Известные ограничения

- Отсутствует `src/`-layout — возможны конфликты импортов при установке как пакета (Issue #35, опционально).
- `__version__` дублируется в `pyproject.toml` и `cli.py` вместо единого источника через `importlib.metadata` (Issue #36).
- Memory measurement через `psutil` может давать нулевые значения для очень быстрых процессов.
