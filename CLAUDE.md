# CLAUDE.md — Stepik-Python-Grader

> Этот файл читает Claude Code перед каждым действием.
> Он описывает архитектуру, инварианты, стиль кода и текущие задачи.
> Не удалять. Не сокращать без согласования.

---

## 🚦 КРИТИЧЕСКИЕ ЗАПРЕТЫ (читать первым)

```
❌ НЕ вносить изменения в ветку main напрямую
❌ НЕ удалять и НЕ переименовывать существующие публичные функции без PR
❌ НЕ ломать обратную совместимость __all__ в grader.py
❌ НЕ использовать Optional[X], List[X], Dict[X,Y] — проект на Python 3.12+
❌ НЕ добавлять новые зависимости в requirements.txt без явного указания
❌ НЕ коммитить secrets.json, stepik_config.json, StepikTasks/
❌ НЕ запускать executor.py с untrusted-кодом — нет sandbox на уровне ОС
❌ НЕ трогать .github/workflows/ci.yml без явной задачи
```

---

## 📍 РАБОЧАЯ ВЕТКА

```bash
# Всегда работать в этой ветке:
git checkout ArtVsMark-patch-1

# Если ветка не существует — создать от main:
git checkout main && git pull && git checkout -b ArtVsMark-patch-1
```

---

## 🗂️ СТРУКТУРА ПРОЕКТА

```
Stepik-Python-Grader/
│
├── grader.py                 # Тонкий фасад обратной совместимости (Sprint 7 ✅)
│                             # реэкспортирует core/grader_core.py / core/reporter.py / cli.py
├── cli.py                     # Application/CLI: интерактивное меню (режимы 0-4)
├── config.py                  # Application/Configuration: GraderConfig, CONFIG (Sprint 6.3 ✅)
│
├── downloader.py             # Domain: скачивание задач, ZIP/HTML, slugify
├── diagnostik_stepik.py      # Application: диагностика API и токена
│
├── core/                     # Internal Infrastructure/Utility модули (Issue #23, #26)
│   ├── __init__.py
│   ├── grader_core.py        # Application: загрузка тест-кейсов, исполнение решений
│   ├── reporter.py           # Application/UI: rich-таблицы, _console, verbose-diff
│   ├── executor.py           # Infrastructure: compile+exec в subprocess
│   ├── microbench_runner.py  # Infrastructure: timeit через subprocess
│   ├── normalizers.py        # Utilities: normalize_floats (leaf, нет зависимостей)
│   ├── storage.py            # Utilities: load/save JSON (leaf, нет зависимостей)
│   ├── stepik_client.py      # Infrastructure: OAuth2, requests.Session
│   ├── oauth_flow.py         # Infrastructure: фасад авторизации
│   └── parsers.py            # Infrastructure: парсинг тест-блоков (# TEST_N:)
│
├── conftest.py               # pytest: collect_ignore для grader.py
│
├── tests/                    # 461 тест (pytest), покрытие 88%
│   ├── test_grader_core.py
│   ├── test_executor.py
│   ├── test_normalizers.py
│   ├── test_storage.py
│   └── ...
│
├── CLAUDE.md                 # ← этот файл
├── CHECKPOINT.md             # Состояние проекта: что сделано, что в работе
├── CHANGELOG.md              # История изменений
├── CONTRIBUTING.md           # Архитектура, форматы тестов, соглашения
├── pyproject.toml            # ruff, pytest, зависимости
└── requirements.txt          # Runtime: requests, psutil, rich
```

### Граф зависимостей (DAG, без циклов)

```
grader.py ──→ core/grader_core.py
grader.py ──→ core/reporter.py
grader.py ──→ cli.py

core/grader_core.py ──→ core/reporter.py   # _print_case_verbose (run_tests verbose)
core/grader_core.py ──→ config.py          # CONFIG (TIMEOUT_SECONDS и т.д.)
core/grader_core.py ──→ core/executor.py
core/grader_core.py ──→ core/microbench_runner.py
core/grader_core.py ──→ core/normalizers.py
core/grader_core.py ──→ core/parsers.py
core/grader_core.py ──→ core/storage.py

cli.py ──→ core/grader_core.py
cli.py ──→ core/reporter.py
cli.py ──→ core/microbench_runner.py  # apply_relative_ranking

core/executor.py ──→ config.py       # CONFIG.executor_timeout (graceful fallback
                                      # к литералу 10, если запущен как subprocess-
                                      # скрипт: sys.path[0] == core/, config.py не виден)

downloader.py ──→ core/stepik_client.py
downloader.py ──→ core/oauth_flow.py
downloader.py ──→ core/storage.py
downloader.py ──→ core/parsers.py

core/oauth_flow.py ──→ core/stepik_client.py
core/oauth_flow.py ──→ core/storage.py

diagnostik_stepik.py ──→ core/stepik_client.py
diagnostik_stepik.py ──→ core/oauth_flow.py
diagnostik_stepik.py ──→ downloader.py  # только parse_stepik_step_url

core/stepik_client.py ──→ core/storage.py
```

> Issue #19 (2026-07): устранена дублирующая копия `_parse_testblock_file` в
> grader.py и локальный импорт `downloader → grader`, который её маскировал.
> Оба модуля теперь импортируют `parse_testblock_file` напрямую из
> `core/parsers.py` — единственного источника истины. downloader.py больше
> не зависит от grader.py.

> Issue #20 finding #4 / Sprint 7 (2026-07): grader.py (1460 строк) разбит на
> grader_core.py (бизнес-логика), reporter.py (rich-вывод) и cli.py (меню).
> grader.py стал тонким фасадом — `from grader_core import *`, `from reporter
> import *`, явные реэкспорты приватных имён (`_verdict`, `_console`, `_RICH`,
> и т.д.), на которые опирается тестовый набор. `__all__` не изменился —
> обратная совместимость сохранена.

> Issue #26 (2026-07): grader_core.py и reporter.py перенесены в `core/`
> (продолжение #23 — теперь ВСЕ внутренние модули живут в `core/`, в корне
> остаются только точки входа `grader.py`/`cli.py`/`downloader.py`/
> `diagnostik_stepik.py` и `config.py`). `grader.py` и `cli.py` импортируют
> `from core.grader_core import ...` / `from core.reporter import ...`.
> Тесты, обращавшиеся к этим модулям напрямую (не через фасад `grader.py`),
> обновлены: `import grader_core`/`import reporter` → `from core import
> grader_core`/`from core import reporter`; `patch("reporter.X")` →
> `patch("core.reporter.X")`.

---

## ⚙️ ОКРУЖЕНИЕ И КОМАНДЫ

### Установка

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
pip install -e ".[dev]"   # pytest, ruff, pytest-cov
```

### Обязательные команды перед коммитом

```bash
# 1. Тесты — всегда запускать полный сьют
pytest tests/ -x -q --tb=short

# 2. Линтер
ruff check .

# 3. Форматтер (проверка, не правка)
ruff format --check .

# 4. Покрытие (информационно)
pytest tests/ --cov=. --cov-report=term-missing -q
```

### Запуск грейдера

```bash
python grader.py          # интерактивное меню (режимы 0-4)
python downloader.py      # скачать задачу по URL Stepik
python diagnostik_stepik.py  # диагностика API и токена
```

---

## 🐍 СТИЛЬ КОДА — ОБЯЗАТЕЛЬНО

### Python версия: 3.12+

```python
# ОБЯЗАТЕЛЬНО в начале каждого нового файла:
from __future__ import annotations
```

### Типизация

```python
# ✅ ПРАВИЛЬНО (Python 3.10+ union syntax):
def foo(x: int | None = None) -> list[str]: ...

# ❌ НЕПРАВИЛЬНО (старый стиль, не использовать):
from typing import Optional, List, Dict, Union
def foo(x: Optional[int] = None) -> List[str]: ...
```

### Dataclasses

```python
# ✅ Правильно — изменяемые defaults через field():
from dataclasses import dataclass, field

@dataclass
class Config:
    items: list[str] = field(default_factory=list)
    meta: dict[str, object] = field(default_factory=dict)

# ❌ Антипаттерн — изменяемый default напрямую:
@dataclass
class Config:
    items: list[str] = []  # НИКОГДА так не писать
```

### Docstrings

```python
def run_tests(
    solution_path: str,
    test_dir: str,
    verbose: bool = True,
) -> dict[str, object]:
    """Запускает решение против набора тест-кейсов.

    Parameters
    ----------
    solution_path:
        Путь к файлу решения (.py).
    test_dir:
        Папка с тест-кейсами (файлы N, N.clue, опционально N.type).
    verbose:
        Если True — выводить diff при WA.

    Returns
    -------
    dict с ключами: passed, total, status, fail_test, elapsed.
    """
```

### Экспорт модуля

```python
# Каждый новый модуль должен содержать __all__:
__all__ = [
    "PublicClass",
    "public_function",
    "CONSTANT",
]
```

### Пути — только pathlib

```python
# ✅ Правильно:
from pathlib import Path
config_path = Path(__file__).parent / "stepik_config.json"

# ❌ Не использовать:
import os
config_path = os.path.join(os.path.dirname(__file__), "stepik_config.json")
```

### Интерпретатор в subprocess

```python
# ✅ Правильно — тот же venv, те же пакеты:
import sys
_PYTHON_CMD = sys.executable

# ❌ Хрупко — может попасть в системный Python:
_PYTHON_CMD = "python3" if sys.platform == "linux" else "python"
```

---

## 🏗️ АРХИТЕКТУРНЫЕ ИНВАРИАНТЫ

1. **DAG без циклов** — новые импорты не должны создавать циклические зависимости
2. **Leaf-модули** — `storage.py` и `normalizers.py` не импортируют ничего из проекта. Не добавлять project-импорты в эти файлы
3. **Graceful fallback** — `rich` является опциональной зависимостью. Весь вывод через `_console` с fallback на `print()`
4. **Нет sandbox** — `executor.py` запускает код в subprocess без изоляции ФС. Документировать везде, где это релевантно
5. **Обратная совместимость** — после рефакторинга `grader.py` все имена из `__all__` должны оставаться доступными через `from grader import X`

---

## 📋 ТЕКУЩИЙ БЭКЛОГ (по приоритету)

### 🔴 Sprint 6 — Критические исправления ✅ ЗАВЕРШЁН (2026-07-02)

#### 6.1 ✅ FIX — `sys.executable` в `executor.py`

```
Файл: executor.py, строка ~23
Проблема: "python"/"python3" может указать на системный Python вне venv (Windows)
Решение: заменить на sys.executable
Тест: tests/test_executor.py — проверить _PYTHON_CMD == sys.executable
```

```python
# БЫЛО:
_PYTHON_CMD: str = "python3" if sys.platform in {"linux", "linux2", "darwin"} else "python"

# СТАЛО:
_PYTHON_CMD: str = sys.executable
```

#### 6.2 ✅ FIX — мёртвый код в `normalizers.py`

```
Файл: core/normalizers.py
Проблема: sort_lines() и normalize_whitespace() помечены "not called in production"
Решено: вариант (в) — добавлены в __all__, докстринги помечают их
"experimental" (не подключены ни к одному режиму grader_core.py), тесты
уже существовали в tests/test_normalizers.py.
```

#### 6.3 ✅ NEW — `config.py` — единая конфигурация

```
Создать: config.py
Цель: заменить разбросанные константы единой точкой правды
```

```python
# config.py
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["GraderConfig", "load_config", "CONFIG"]


@dataclass(frozen=True)
class GraderConfig:
    """Единая конфигурация грейдера. frozen=True — потокобезопасно."""

    timeout_seconds: float = 10.0
    executor_timeout: int = 10
    similar_threshold: float = 1.15
    much_slower_threshold: float = 1.50
    measure_child_memory: bool = True
    microbench_max_cases: int = 5
    encoding: str = "utf-8"


def load_config() -> GraderConfig:
    """Загружает конфиг из [tool.stepik-grader] в pyproject.toml.

    Если pyproject.toml отсутствует или секция не найдена —
    возвращает GraderConfig с дефолтными значениями.
    """
    pyproject = Path(__file__).parent / "pyproject.toml"
    if not pyproject.exists():
        return GraderConfig()
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    overrides = data.get("tool", {}).get("stepik-grader", {})
    valid = {k: v for k, v in overrides.items()
             if k in GraderConfig.__dataclass_fields__}
    return GraderConfig(**valid)


CONFIG: GraderConfig = load_config()
```

```toml
# Добавить в pyproject.toml:
[tool.stepik-grader]
timeout_seconds = 10.0
executor_timeout = 10
microbench_max_cases = 5
```

```
Тесты: tests/test_config.py
  - GraderConfig() создаётся с дефолтами
  - frozen=True: FrozenInstanceError при мутации
  - load_config() читает pyproject.toml без ошибок
  - load_config() при отсутствии файла — возвращает дефолты
```

> Реализовано как описано выше. `grader_core.py` читает
> `TIMEOUT_SECONDS`/`ENCODING`/`SIMILAR_THRESHOLD`/`MUCH_SLOWER_THRESHOLD`/
> `MEASURE_CHILD_MEMORY`/`MICROBENCH_MAX_CASES` из `CONFIG` при импорте
> (имена и дефолтные значения не изменились — обратная совместимость
> `grader.__all__` сохранена). `core/executor.py` тоже читает
> `CONFIG.executor_timeout`, но с fallback на литерал `10` в `except
> ImportError` — `python core/executor.py` как subprocess-скрипт запускается
> с `sys.path[0] == core/`, где `config.py` (лежит в корне проекта) не
> импортируется напрямую.

---

### 🟡 Sprint 7 — Рефакторинг `grader.py`

#### 7.1 ✅ ЗАВЕРШЕНО (2026-07) — разбить `grader.py` (1489 строк) на три модуля

```
Цель: grader.py → reporter.py + grader_core.py + cli.py
Принцип: НЕ переписывать — только перемещать
Инвариант: все существующие тесты проходят БЕЗ изменений
```

**Шаг 1: `reporter.py`** (~300 строк)

Перенести из `grader.py`:
- `_console` singleton + graceful fallback (rich stubs)
- `format_correctness_row()`
- `print_correctness_header()`
- `print_correctness_results()`
- `format_benchmark_row()`
- `print_benchmark_header()`
- `print_benchmark_results()`

**Шаг 2: `grader_core.py`** (~700 строк)

Перенести из `grader.py`:
- `TestCase` dataclass
- `_is_safe_constant()`
- `is_function_only_solution()`
- `is_solution_file()`
- `find_all_solution_files()`
- `collect_grouped_files()`
- `load_test_cases()`
- `load_text_lines()`
- `run_single_test()`
- `run_tests()`
- `run_benchmark()`
- `run_microbench_mode()`

**Шаг 3: `cli.py`** (~200 строк)

- Меню (режимы 0–4)
- argparse для non-interactive:
  ```bash
  python grader.py --mode 1 --file path/to/task.py
  python grader.py --mode 3 --dir StepikTasks/module1/task1 --repeats 15
  python grader.py --version
  ```
- `if __name__ == "__main__": main()`

**Шаг 4: `grader.py` — тонкий фасад**

```python
# grader.py (после рефакторинга)
"""grader.py — фасад для обратной совместимости.

Все публичные символы реэкспортируются из grader_core и reporter.
Прямой запуск: python grader.py [--mode N] [--file PATH]
"""
from __future__ import annotations

from grader_core import *   # noqa: F401, F403
from reporter import *      # noqa: F401, F403
from cli import main

__version__ = "1.0.0"

if __name__ == "__main__":
    main()
```

```
Порядок выполнения:
  1. Создать reporter.py → pytest tests/ -x -q ✅
  2. Создать grader_core.py → pytest tests/ -x -q ✅
  3. Создать cli.py → pytest tests/ -x -q ✅
  4. Упростить grader.py → pytest tests/ -x -q ✅
```

#### 7.2 ✅ NEW — `BenchStats` dataclass

```python
# Добавить в grader_core.py (или bench_stats.py):
from __future__ import annotations

import statistics
from dataclasses import dataclass

__all__ = ["BenchStats"]


@dataclass
class BenchStats:
    """Унифицированная статистика замеров для режимов 3 и 4.

    Устраняет дублирование вычислений между run_benchmark()
    и _micro_stats() в grader.py.
    """

    timings: list[float]

    @property
    def min(self) -> float:
        """Минимальное время замера."""
        return min(self.timings)

    @property
    def median(self) -> float:
        """Медианное время — основной ориентир при сравнении решений."""
        return statistics.median(self.timings)

    @property
    def mean(self) -> float:
        """Среднее время замера."""
        return statistics.mean(self.timings)

    @property
    def stdev(self) -> float:
        """Среднеквадратичное отклонение; 0.0 при единственном замере."""
        return statistics.stdev(self.timings) if len(self.timings) > 1 else 0.0

    @property
    def max(self) -> float:
        """Максимальное время замера."""
        return max(self.timings)

    def relative_to(self, baseline: float) -> float:
        """Возвращает median / baseline * 100 (процент от эталона)."""
        return (self.median / baseline * 100) if baseline > 0 else 0.0
```

> Реализовано в `grader_core.py` (не в отдельном `bench_stats.py`) — оба
> потребителя (`run_benchmark()`, `_micro_stats()`) уже живут там. Обе
> функции по-прежнему возвращают `dict`, а не `BenchStats`-инстанс: внешний
> контракт (ключи словаря, потребляемые `reporter.py` и тестами) не менялся,
> `BenchStats` используется только для самого вычисления min/median/mean/
> stdev/max в одном месте.

#### 7.3 ✅ NEW — таймаут для microbench (режим 4)

```python
# Добавить в microbench_runner.py:
import concurrent.futures
from collections.abc import Callable

def run_microbench_with_timeout(
    fn: Callable[[], list[float]],
    timeout: float = 60.0,
) -> list[float]:
    """Запускает fn() с защитным таймаутом.

    Parameters
    ----------
    fn:
        Функция, возвращающая список замеров времени.
    timeout:
        Максимальное время ожидания в секундах.

    Returns
    -------
    Список замеров или пустой список при превышении таймаута.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return []
```

> Реализовано как описано, добавлена в `core/microbench_runner.py`. **Не
> подключена** к текущим вызовам `run_microbench()` в `run_microbench_mode()`:
> `run_microbench()` уже оборачивает свой `subprocess.run()` в `timeout=60`,
> который надёжно убивает дочерний процесс и гарантированно разблокирует
> вызывающий поток — `ThreadPoolExecutor`-обёртка поверх уже
> subprocess-защищённого вызова не добавляет реальной защиты, а при
> реальном таймауте **не убивает** зависший поток/подпроцесс (просто
> перестаёт его ждать) — то есть может УХУДШИТЬ ситуацию (утечка
> orphan-процесса) для вызовов, которые сами по себе не subprocess-bounded.
> Оставлена как готовый строительный блок для будущего `fn()` без
> собственного таймаута — см. докстринг функции.

---

### 🟢 Sprint 8 — CLI и PyPI-ready

#### 8.1 ✅ argparse CLI (2026-07-02)

```
python grader.py                                          — интерактивное меню (как раньше)
python grader.py --version                                — версия и выход
python grader.py --mode 1 --file path/to/task.py          — режим 1, non-interactive
python grader.py --mode 2 --dir path/to/folder            — режим 2, non-interactive
python grader.py --mode 3 --dir path/to/folder --repeats 15  — режим 3, non-interactive
python grader.py --mode 4 --dir path/to/folder --number 1000 — режим 4, non-interactive
```

> Реализовано в `cli.py`: `_run_mode_1/2/3/4()` — извлечённые из
> `_interactive_menu()` тела режимов (без изменения логики), переиспользуются
> и меню, и `main()`. `main(argv: list[str] | None = None)` — явный
> `argv`-параметр вместо неявного чтения `sys.argv`, чтобы тесты не зависели
> от аргументов самого pytest. `--repeats`/`--number` имеют дефолты (15/1000,
> без интерактивного запроса профиля). `__version__` перенесена из grader.py
> в cli.py (grader.py реэкспортирует её обратно) — иначе понадобился бы
> обратный импорт cli.py → grader.py, нарушающий DAG.

#### 8.2 OPTIONAL — `src/`-layout

```
Только если планируется публикация на PyPI.
Переместить: все .py в src/stepik_grader/
Обновить: pyproject.toml → [tool.setuptools.packages.find] where = ["src"]
Обновить: conftest.py → sys.path.insert(0, ...)
```

---

## 📐 ФОРМАТЫ ТЕСТ-КЕЙСОВ

```
tests/
  1          ← stdin для теста №1
  1.clue     ← ожидаемый вывод теста №1
  1.type     ← "function" (только для function-style задач)
  2
  2.clue
  ...
```

Три формата (grader автодетектирует):

| Формат | Файлы | Источник |
|--------|-------|---------|
| 1 — Legacy | `N`, `N.clue` | Stepik ZIP / downloader.py |
| 2 — Named | `input_N.txt`, `expected_N.txt` | ручное добавление |
| 3 — python-generation | `input.txt` + `output.txt` с `# TEST_N:` | репозитории python-generation |

---

## 🔑 ФОРМАТ КОММИТОВ

Conventional Commits — обязательно:

```
fix(executor): use sys.executable instead of platform string
feat(config): add GraderConfig dataclass with pyproject.toml support
refactor(grader): extract reporter.py with rich output logic
test(config): add tests for GraderConfig defaults and freeze behaviour
chore(deps): update psutil to 6.x
docs(claude): update sprint 7 task list
```

---

## 🚫 АНТИПАТТЕРНЫ (не воспроизводить)

```python
# ❌ Платформо-зависимая команда Python:
_PYTHON_CMD = "python3" if sys.platform == "linux" else "python"
# ✅ Используй: sys.executable

# ❌ Мутабельный default в dataclass:
@dataclass
class Foo:
    items: list = []
# ✅ Используй: items: list = field(default_factory=list)

# ❌ Старые type hints:
from typing import Optional, List
def f(x: Optional[int]) -> List[str]: ...
# ✅ Используй: def f(x: int | None) -> list[str]: ...

# ❌ os.path вместо pathlib:
os.path.join(os.path.dirname(__file__), "data")
# ✅ Используй: Path(__file__).parent / "data"

# ❌ print() в логике модулей:
print(f"Running test {i}")
# ✅ Используй: _console.print(...) с rich fallback

# ❌ Голый except:
try:
    result = run()
except:
    pass
# ✅ Используй: except Exception as e: log или re-raise

# ❌ Импорт на уровне модуля, создающий цикл:
# В downloader.py: from grader import _parse_testblock_file
# ✅ Используй локальный импорт внутри функции
```

---

## 📎 СВЯЗАННЫЙ ПРОЕКТ

**Glossary-Python** (`https://github.com/ArtVsMark/Glossary-Python`)

- Статический HTML-глоссарий Python-терминов (581 карточка, 43 раздела)
- Текущий статус: **заморожен** до завершения Sprint 6–7 в grader
- Будущая интеграция (Sprint 10+): grader показывает ссылку на глоссарий
  при RE/WA — например, `RecursionError → #recursion`
- НЕ трогать этот проект в текущей сессии

---

## 📊 МЕТРИКИ ПРОЕКТА (на момент v1.0.0)

| Метрика | Значение |
|---------|---------|
| Версия | 1.0.0 (stable) |
| Python | 3.12 / 3.13 / 3.14 |
| Тестов | 461 |
| Покрытие | 88% |
| Строк (grader.py) | 8 (тонкий фасад, Sprint 7 ✅) |
| Зависимостей runtime | 3 (requests, psutil, rich) |
| CI | GitHub Actions (pytest + ruff) |

---

## ✅ ЧЕКЛИСТ ПЕРЕД PR

```
[ ] git checkout ArtVsMark-patch-1
[ ] pytest tests/ -x -q --tb=short   → все зелёные
[ ] ruff check .                      → 0 ошибок
[ ] ruff format --check .             → 0 ошибок
[ ] Новые функции имеют type hints и docstring
[ ] Новые модули имеют __all__
[ ] from __future__ import annotations в начале файла
[ ] Коммит в Conventional Commits формате
[ ] CHECKPOINT.md обновлён (если завершён спринт)
[ ] CHANGELOG.md обновлён (если добавлена/исправлена фича)
```
