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
❌ НЕ добавлять новые зависимости в pyproject.toml без явного указания
   (requirements.txt удалён — issue #51 P-01, pyproject.toml — единственный
   источник; requirements.txt больше не существует, не воссоздавать)
❌ НЕ коммитить secrets.json, stepik_config.json, StepikTasks/
❌ НЕ запускать executor.py с untrusted-кодом — нет sandbox на уровне ОС
❌ НЕ трогать .github/workflows/ci.yml без явной задачи
❌ НЕ применять SemVer к версии — схема проекта СВОЯ (тег = MINOR+1,
   PATCH = число коммитов после тега, обнуляется при MINOR; все теги = vX.Y.0).
   См. CONTRIBUTING.md §Версионирование и scripts/version.py (issue #68)
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

> src/-layout (Issue #35 / Sprint 8.2 ✅, 2026-07): весь пакет живёт в
> `src/stepik_grader/`. Запуск — только `python -m stepik_grader.X` или
> консольная команда `stepik-grader` после `pip install -e .`; прямого
> `python grader.py` из корня репозитория больше нет.

```
Stepik-Python-Grader/
│
├── src/
│   └── stepik_grader/
│       ├── __init__.py
│       ├── grader.py             # Тонкий фасад обратной совместимости (Sprint 7 ✅)
│       │                         # реэкспортирует core/grader_core.py / core/reporter.py / cli.py
│       ├── cli.py                 # Application/CLI: интерактивное меню (режимы 0-4), entry point stepik-grader
│       ├── config.py              # Application/Configuration: GraderConfig, CONFIG (Sprint 6.3 ✅)
│       │
│       ├── downloader.py         # Domain: скачивание задач, ZIP/HTML, slugify
│       ├── diagnostic_stepik.py  # Application: диагностика API и токена
│       │
│       └── core/                 # Internal Infrastructure/Utility модули (Issue #23, #26)
│           ├── __init__.py
│           ├── grader_core.py    # Application: run_single_test/run_tests/run_benchmark/
│           │                     # run_microbench_mode — исполнение и агрегация (Issue #45 A-01 ✅)
│           ├── test_loader.py    # Application: обнаружение файлов-решений, загрузка
│           │                     # тест-кейсов, resolve_test_dir (Issue #45 A-01 ✅)
│           ├── mode_detector.py  # Application: детекция stdin/function
│           │                     # (_detect_run_mode, is_function_only_solution) (Issue #45 A-01 ✅)
│           ├── wrapper_builder.py # Application: генерация wrapper-скриптов
│           │                     # (_build_function_wrapper, _build_call_wrapper) (Issue #45 A-01 ✅)
│           ├── reporter.py       # Application/UI: rich-таблицы, _console, verbose-diff
│           ├── executor.py       # Infrastructure: compile+exec в subprocess
│           ├── microbench_runner.py  # Infrastructure: timeit через subprocess
│           ├── normalizers.py    # Utilities: normalize_floats (leaf, нет зависимостей)
│           ├── storage.py        # Utilities: load/save JSON (leaf, нет зависимостей)
│           ├── stepik_client.py  # Infrastructure: OAuth2, requests.Session
│           ├── oauth_flow.py     # Infrastructure: фасад авторизации
│           └── parsers.py        # Infrastructure: парсинг тест-блоков (# TEST_N:)
│
├── conftest.py                # pytest: sys.path.insert(0, "src") для discovery без install
│
├── tests/                    # 523 теста (pytest), покрытие 95%
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
└── pyproject.toml            # ruff, mypy, pytest, зависимости (requests, psutil, rich),
                               # packages.find where=["src"] — единственный источник
                               # зависимостей (requirements.txt удалён, issue #51 P-01)
```

### Граф зависимостей (DAG, без циклов)

> Все модули ниже живут в `src/stepik_grader/` (Issue #35, src-layout);
> пути в графе — относительно этого пакета.

```
grader.py ──→ core/grader_core.py
grader.py ──→ core/reporter.py
grader.py ──→ cli.py

core/grader_core.py ──→ config.py          # CONFIG (TIMEOUT_SECONDS и т.д.)
core/grader_core.py ──→ core/executor.py
core/grader_core.py ──→ core/microbench_runner.py
core/grader_core.py ──→ core/normalizers.py
core/grader_core.py ──→ core/mode_detector.py    # Issue #45 A-01
core/grader_core.py ──→ core/test_loader.py       # Issue #45 A-01
core/grader_core.py ──→ core/wrapper_builder.py   # Issue #45 A-01

core/test_loader.py ──→ config.py
core/test_loader.py ──→ core/mode_detector.py     # _is_python_code_block, _detect_run_mode
core/test_loader.py ──→ core/parsers.py

core/mode_detector.py ──→ config.py
core/mode_detector.py ──→ core/storage.py

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

diagnostic_stepik.py ──→ core/stepik_client.py
diagnostic_stepik.py ──→ core/oauth_flow.py
diagnostic_stepik.py ──→ downloader.py  # только parse_stepik_step_url

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
> `diagnostic_stepik.py` и `config.py`). `grader.py` и `cli.py` импортируют
> `from core.grader_core import ...` / `from core.reporter import ...`.
> Тесты, обращавшиеся к этим модулям напрямую (не через фасад `grader.py`),
> обновлены: `import grader_core`/`import reporter` → `from core import
> grader_core`/`from core import reporter`; `patch("reporter.X")` →
> `patch("core.reporter.X")`.

> Issue #35 / Sprint 8.2 (2026-07): все 16 исходных файлов перенесены в
> `src/stepik_grader/` (src-layout) — `git mv` сохранил историю. Каждый
> внутренний импорт получил префикс `stepik_grader.` (`from
> stepik_grader.core.grader_core import ...` и т.д.). `pyproject.toml`:
> `[tool.setuptools.packages.find] where = ["src"]`, новый entry point
> `[project.scripts] stepik-grader = "stepik_grader.cli:main"`. `conftest.py`
> добавляет `src/` в `sys.path`, чтобы тесты работали без `pip install -e .`.
> `config.py`'s `load_config()` резолвит `pyproject.toml` тремя уровнями выше
> своего `__file__` (было — одним), так как теперь лежит в
> `src/stepik_grader/config.py`. Прямой запуск `python grader.py` из корня
> репозитория удалён — только `python -m stepik_grader.X` или консольная
> команда `stepik-grader`. Все 523 теста прошли без сюрпризов после
> экзаустивного grep-аудита импортов перед миграцией.

> Issue #45 A-02 (2026-07, Sprint B): устранён обратный импорт
> `core/grader_core.py → core/reporter.py` (эта строка DAG выше). `run_tests()`
> получил параметр `verbose_callback: Callable[[TestCase, dict], None] | None`;
> печать verbose-кейса теперь ответственность вызывающей стороны — `cli.py`
> передаёт `reporter.print_case_verbose` явно. `grader_core.py` больше не знает
> о существовании `reporter.py`. A-04 (те же issue #45): `_resolve_test_dir` →
> `resolve_test_dir` (grader_core.py), `_rich_track` → `rich_track`,
> `_print_case_verbose` → `print_case_verbose` (оба — reporter.py); все три
> добавлены в `__all__` своих модулей — `cli.py` больше не импортирует
> приватные (`_`-префиксные) имена из других модулей. `grader.py`
> backward-compat `__all__` не изменился.

> Issue #45 A-01 (2026-07): `grader_core.py` (1200+ строк) разбит на
> `test_loader.py` (обнаружение файлов-решений, `load_test_cases`,
> `resolve_test_dir`), `mode_detector.py` (`_detect_run_mode`,
> `is_function_only_solution`, `_is_python_code_block`) и
> `wrapper_builder.py` (`_build_function_wrapper`, `_build_call_wrapper`).
> `grader_core.py` сохранил `run_single_test`/`run_tests`/`run_benchmark`/
> `run_microbench_mode` и реэкспортирует все 16 перенесённых имён по имени
> (не через `import *`) — `__all__` grader_core.py, явный импорт-список
> `grader.py` и импорты `cli.py` не изменились. Единственное направление
> зависимости между новыми модулями: `test_loader.py → mode_detector.py`
> (`load_test_cases` классифицирует Format-3 блоки через
> `_is_python_code_block`; `_apply_run_mode_override` вызывает
> `_detect_run_mode`) — циклов нет. Перед разбиением отдельный агент
> проаудировал весь тестовый набор на предмет `monkeypatch`/`mock.patch`,
> нацеленных на перемещаемые имена через `grader_core`/`cli` — таких не
> нашлось, так что правки тестов не потребовались.

---

## ⚙️ ОКРУЖЕНИЕ И КОМАНДЫ

### Установка

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"   # requests/psutil/rich + pytest, pytest-cov, ruff, mypy
```

### Обязательные команды перед коммитом

```bash
# 1. Тесты — всегда запускать полный сьют
pytest tests/ -x -q --tb=short

# 2. Линтер
ruff check .

# 3. Форматтер (проверка, не правка)
ruff format --check .

# 4. Типизация (Sprint D / Issue #49 C-02, зеркалит шаг CI)
mypy src/stepik_grader --ignore-missing-imports

# 5. Покрытие (информационно)
pytest tests/ --cov=. --cov-report=term-missing -q
```

### Запуск грейдера

```bash
python -m stepik_grader.grader              # интерактивное меню (режимы 0-4)
python -m stepik_grader.downloader          # скачать задачу по URL Stepik
python -m stepik_grader.diagnostic_stepik   # диагностика API и токена

# или, после pip install -e .:
stepik-grader
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

__version__ = "1.1.0"

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
stepik-grader                                          — интерактивное меню (как раньше)
stepik-grader --version                                — версия и выход
stepik-grader --mode 1 --file path/to/task.py          — режим 1, non-interactive
stepik-grader --mode 2 --dir path/to/folder            — режим 2, non-interactive
stepik-grader --mode 3 --dir path/to/folder --repeats 15  — режим 3, non-interactive
stepik-grader --mode 4 --dir path/to/folder --number 1000 — режим 4, non-interactive
```

> Примеры выше — через консольную команду `stepik-grader` (после
> `pip install -e .`, Issue #35 / Sprint 8.2). Эквивалентно через
> `python -m stepik_grader.grader --mode ...`.

> Реализовано в `cli.py`: `_run_mode_1/2/3/4()` — извлечённые из
> `_interactive_menu()` тела режимов (без изменения логики), переиспользуются
> и меню, и `main()`. `main(argv: list[str] | None = None)` — явный
> `argv`-параметр вместо неявного чтения `sys.argv`, чтобы тесты не зависели
> от аргументов самого pytest. `--repeats`/`--number` имеют дефолты (15/1000,
> без интерактивного запроса профиля). `__version__` перенесена из grader.py
> в cli.py (grader.py реэкспортирует её обратно) — иначе понадобился бы
> обратный импорт cli.py → grader.py, нарушающий DAG.

#### 8.2 ✅ ЗАВЕРШЕНО (2026-07) — `src/`-layout

```
Перемещено: все .py в src/stepik_grader/ (git mv, история сохранена)
Обновлено: pyproject.toml → [tool.setuptools.packages.find] where = ["src"]
Обновлено: conftest.py → sys.path.insert(0, str(Path(__file__).parent / "src"))
Добавлено: [project.scripts] stepik-grader = "stepik_grader.cli:main"
```

> Реализовано как Issue #35 (см. примечание в разделе «Граф зависимостей»
> выше). Каждый внутренний импорт получил префикс `stepik_grader.`; все 523
> теста прошли на первом запуске после миграции.

---

### 🔴 Sprint A — Безопасность (аудит v1.1.0, эпик #60) ✅ ЗАВЕРШЁН (2026-07-03)

#### A.1 ✅ FIX — #44 (S-03): wildcard-импорты в `_build_call_wrapper`

```
Файл: core/grader_core.py, _build_call_wrapper()
Было: from collections/datetime/itertools/functools import *
Стало: явные импорты, покрывающие полное документированное публичное API
       каждого модуля (не производный dir() — это исключило бы служебные
       реэкспорты вроде functools.RLock/GenericAlias, не относящиеся к
       типичным тест-блокам python-generation).
Тесты: tests/test_grader_core.py — нет "import *" в сгенерированном
       исходнике; решение, переопределяющее reduce()/chain(), не
       перекрывается stdlib-версией (порядок копирования имён решения
       в globals() уже гарантировал это и до фикса).
```

#### A.2 ✅ FIX — #43 (S-01): best-effort memory cap для дочернего процесса

```
Добавлено: GraderConfig.max_memory_mb (config.py, дефолт 1024)
Добавлено: _make_memory_limiter() в core/grader_core.py и
           core/microbench_runner.py (дублируется в обоих — grader_core
           импортирует microbench_runner, не наоборот; кросс-импорт
           создал бы цикл в DAG)
Подключено: preexec_fn= в subprocess.Popen (run_single_test, режимы 1-3)
            и subprocess.run (run_microbench, режим 4)
```

> POSIX-only (`resource.setrlimit(RLIMIT_AS, ...)`) — на Windows модуль
> `resource` отсутствует, `_make_memory_limiter()` возвращает `None`,
> `preexec_fn=None` не меняет поведение `Popen`. Тот же паттерн graceful
> degradation, что и у `SIGALRM`-таймаута в `executor.py`. Работает в
> Linux CI, не защищает Windows-запуски (основная личная среда этого
> проекта) — задокументированное ограничение, не полноценный OS-sandbox
> (по-прежнему нет изоляции ФС/сети).

> **#43 (S-02) закрыт как дубликат S-01, не отдельный фикс.** `safe_input`/
> `call_block` встраиваются в generated-код как выражения верхнего уровня,
> а не внутри строкового литерала — вырваться из контекста через кавычки
> невозможно, `shell=True` нигде не используется (subprocess вызывается
> списком аргументов). Реальный риск — тот же, что в S-01: тест-контент по
> формату обязан быть исполняемым Python-кодом, и это исполняется без
> sandbox. Предложенный в issue вариант (env var вместо f-string) не снижает
> риск (код всё равно `exec`-нётся) и добавляет риск обрезания на лимите
> размера env var в Windows (~32KB) для многострочных тест-блоков — решено
> не делать.

---

### 🟠 Sprint B — Архитектура (аудит v1.1.0, эпик #60) 🟡 ЧАСТИЧНО (2026-07-03)

#### B.1 ✅ FIX — #45 A-02, A-04: layering и приватные кросс-модульные импорты

```
A-02: run_tests() → verbose_callback вместо прямого импорта reporter.
A-04: _resolve_test_dir → resolve_test_dir, _rich_track → rich_track,
      _print_case_verbose → print_case_verbose; все добавлены в __all__.
```

> См. примечание Issue #45 A-02/A-04 в разделе «Граф зависимостей» выше —
> там же удалено ребро `core/grader_core.py → core/reporter.py` из DAG.

#### B.2 ✅ РЕШЕНО (не код) — #46 A-03: судьба `executor.py`

```
Issue предлагал: (A) интегрировать как unified runner в grader_core.py,
                  (B) перенести в tests/helpers/ как test-only утилиту.
Решение: НИ ТО, НИ ДРУГОЕ — оставить как есть.
```

> (A) невозможен без регресса: `run_solution()` не измеряет память (psutil),
> не поддерживает function-mode wrapper-файлы, и его `SIGALRM`-таймаут не
> работает на Windows (основная среда разработки этого проекта) — тогда как
> `run_single_test()` уже даёт всё это через `subprocess.communicate(timeout=)`,
> кросс-платформенно. (B) ломает существующие тесты (`tests/test_executor.py`
> запускает `executor.main()` в чистом subprocess через `python -c "from
> stepik_grader.core import executor; ..."` — это требует, чтобы модуль
> оставался частью УСТАНОВЛЕННОГО пакета `stepik_grader.core`, а не лежал в
> `tests/helpers/`). `executor.py` остаётся тестируемым, но не
> production-задействованным модулем — это уже было явно задокументировано в
> его собственном докстринге до этого issue, статус-кво принят осознанно.

#### B.3 ⏸️→✅ #45 A-01: разбить `grader_core.py` (700+ строк, SRP)

```
Issue предлагал: core/test_loader.py, core/mode_detector.py,
                 core/wrapper_builder.py — извлечь из grader_core.py.
```

> Отложено в Sprint B (риск для качества при выполнении наравне с остальными
> пунктами того же прохода) — сделано отдельным заходом после Sprint E и
> roadmap-партии #53/#54/#58. См. примечание Issue #45 A-01 в разделе «Граф
> зависимостей» выше — там детали разбиения и почему обошлось без правок
> тестов.

---

### 🟠 Sprint C — Надёжность (аудит v1.1.0, эпик #60) ✅ ЗАВЕРШЁН (2026-07-03)

#### C.1 ✅ FIX — #47 R-04: `resolve_test_dir()` больше не возвращает "призрачный" путь

```
Было: последняя строка — return str(candidate_tests), даже если is_dir()
      выше уже вернул False (несуществующий путь возвращался молча).
Стало: return None. cli.py (_run_mode_1/2/3) проверяет `is None` ПЕРЕД
       pathlib.Path(...).is_dir() — иначе pathlib.Path(None) кидает TypeError.
```

#### C.2 ✅ FIX (узкий) — #47 R-02: голое имя без вызова/присваивания

```
Добавлено: если top-level тело блока — ровно один ast.Expr(ast.Name(...))
           (например "x" или "print" целиком, без вызова/присваивания) —
           _is_python_code_block() возвращает False.
```

> Намеренно узкая правка — НЕ переписывал общую эвристику "есть ли Name-узел".
> Реальный python-generation корпус (523+ теста, включая
> `test_integration_repos.py` против настоящих репозиториев) уже проходит на
> текущей эвристике; более агрессивное "улучшение" рисковало сломать
> классификацию контента, который нельзя полностью протестировать локально
> (внешние репозитории). Голое имя без вызова/присваивания — единственный
> AST-паттерн, который никогда не встречается в реальных call-block/
> variable-declaration тест-блоках, поэтому фикс безопасен.

#### C.3 🟡 ЧАСТИЧНО — #47 R-01: диагностика таймаута microbench

```
Добавлено: сообщение об ошибке при TimeoutExpired теперь включает
           number=<N> (количество итераций на repeat) — единственная
           содержательная подсказка, которая у нас реально есть.
НЕ сделано: настоящий per-call таймаут внутри timeit.repeat().
```

> `run_microbench_with_timeout()` (Sprint 7.3, всё ещё не подключена) не
> решает R-01: она оборачивает уже `subprocess.run(timeout=60)`-защищённый
> вызов в `ThreadPoolExecutor`, что не даёт защиты (см. её собственный
> докстринг) — и КОСВЕННО ухудшает картину: при реальном таймауте она не
> убивает поток/подпроцесс, а просто перестаёт его ждать. Настоящий
> per-call таймаут потребовал бы отказа от `timeit.repeat()` в пользу
> ручного цикла с проверкой времени, либо `SIGALRM` — недоступного на
> Windows (основная среда разработки этого проекта).

#### C.4 ✅ FIX — #48 R-03, R-05: предупреждения вместо тихих fallback'ов

```
R-03: warnings.warn() в load_test_cases(), если Формат 3 (input.txt/
      output.txt) используется, а рядом лежат "осиротевшие" файлы Формата 1/2
      (N.clue / input_N.txt) — они молча игнорировались.
R-05: warnings.warn() в _measure_peak_memory() при NoSuchProcess/
      AccessDenied/ZombieProcess — peak=0.0 больше не неотличим от
      "действительно использовал ~0 памяти".
```

---

### 🟠 Sprint D — CI/CD и качество (аудит v1.1.0, эпик #60) ✅ ЗАВЕРШЁН (2026-07-03)

#### D.1 ✅ FIX — #49 C-01: Windows/macOS runners в CI

```
Было: runs-on: ubuntu-latest (единственная ОС).
Стало: matrix.os = [ubuntu-latest, windows-latest, macos-latest] для
       Python 3.12/3.13. 3.14-experimental остаётся Ubuntu-only.
```

#### D.2 ✅ FIX — #49 C-02: mypy в CI

```
Добавлено: mypy>=1.10 в [project.optional-dependencies].dev.
Добавлено: шаг "Type check" (mypy src/stepik_grader --ignore-missing-imports)
           в .github/workflows/ci.yml, после ruff, перед pytest.
```

> Первый прогон mypy вскрыл ~12 ошибок — все устранены ПЕРЕД включением шага
> в CI (иначе первый же коммит сломал бы CI):
> - `grader_core.py:_read_meta_function_name` передавал `str(meta_path)` в
>   `load_json_file()`, аннотированную как `pathlib.Path` — убран лишний
>   `str()`, `meta_path` и так `pathlib.Path`.
> - `cli.py` (`_run_mode_2/3/4`): `resolve_test_dir()`/
>   `_resolve_test_dir_from_input()` теперь возвращают `str | None`
>   (issue #47 R-04) — mypy корректно отследил, что `None` может утечь в
>   `run_tests()`/`run_benchmark()`/`run_microbench_mode()`, которые ожидают
>   `str`. Добавлены `assert ... is not None` (режимы 2/3 — после fallback,
>   который на практике никогда не возвращает `None`) и явная `is None`
>   проверка (режим 4 — там нет fallback, только `continue`).
> - `executor.py` (`signal.alarm` × 2) и `grader_core.py`/
>   `microbench_runner.py` (`resource.setrlimit`/`RLIMIT_AS` × 2, issue #43
>   S-01) — точечные `# type: ignore[attr-defined]`: typeshed не включает эти
>   атрибуты в стабы для win32, хотя вызовы уже защищены рантайм-проверками
>   (`hasattr(signal, "SIGALRM")` / try-except `ImportError` на `resource`).
>   На Linux/macOS CI-раннерах эти атрибуты реально существуют — `# type:
>   ignore` там станет "unused", но `warn_unused_ignores` не включён, ошибкой
>   это не станет.
> - `reporter.py`: fallback-заглушка `rich_track()` (используется, когда
>   `rich` не установлен) не совпадает по сигнатуре с настоящим
>   `rich.progress.track` — `# type: ignore[misc]`, тот же паттерн, что уже
>   применялся к заглушкам `Console`/`Table`/`Text` (`# type: ignore[no-redef]`).

#### D.3 ✅ FIX — #49 Q-01: mock-тесты для ошибок GitHub API

```
Добавлено в tests/test_downloader.py (TestDownloadGithubTests):
  - test_api_request_exception_returns_zero — requests.ConnectionError
  - test_api_raise_for_status_error_returns_zero — raise_for_status() → 404/500
  - test_no_recognized_files_returns_zero — файлы есть, но ни Формат 3,
    ни N/N.clue не распознаны (branch `if not pairs`)
```

> Покрытие `downloader.py`: 98% → 99% (закрыты строки 465-467, 502-503 —
> ранее непокрытые ветки обработки ошибок `_download_github_tests`).

---

### 🟡 Sprint E — UX/Документация/Зависимости (аудит v1.1.0, эпик #60) ✅ ЗАВЕРШЁН (2026-07-03)

#### E.1 ✅ FIX — #51 D-01: i18n меню и CLI-сообщений

```
Добавлено: cli.py — _LANG (модульная переменная, дефолт "ru"), _MESSAGES
           (словарь ключ → {"ru": ..., "en": ...}), _t(key, **kwargs).
Добавлено: --lang {ru,en} в argparse (дефолт ru).
```

> Минимальный словарь вместо полноценного gettext — соразмерно масштабу
> этого CLI (~30 сообщений). Тесты `tests/test_cli.py` (написаны ДО i18n)
> проверяют английский текст напрямую — вместо дублирования ассертов на
> двух языках добавлена autouse-фикстура `_force_english`, форсирующая
> `cli._LANG = "en"` для всего файла. Новый файл
> `tests/test_cli_sprint_e.py` проверяет реальный русский дефолт и
> переключение `--lang` без этой фикстуры.

#### E.2 ✅ FIX — #50 D-03: `--verbose`/`--quiet`

```
Добавлено: взаимоисключающая группа --verbose/--quiet в argparse.
_run_mode_1(..., verbose: bool = True)   — дефолт как раньше, --quiet гасит.
_run_mode_2(..., verbose: bool = False)  — дефолт как раньше, --verbose включает.
Режимы 3/4 флаг игнорируют — там нет per-case verbose-вывода.
```

#### E.3 ✅ FIX — #50 D-04: `--output json`

```
Добавлено: --output {text,json} в argparse, применяется во всех 4 режимах.
Схема: напрямую JSON-сериализуются уже существующие dict'ы run_tests()/
       run_benchmark()/run_microbench_mode() — отдельная схема не
       придумывалась (ключи "file"/"results"/"groups" в зависимости от
       режима).
```

#### E.4 ✅ FIX — #50 D-05: содержательная диагностика "тесты не найдены"

```
Было (режим 1): "Test directory not found for: {solution}"
Стало: "⚠️ Тесты не найдены для: {name}\n   Ожидалась папка: {expected}\n
        Запустите: python -m stepik_grader.downloader\n   Или создайте
        вручную: tests/1, tests/1.clue"
```

#### E.5 ✅ РЕШЕНО (уточнение аудита) — #50 D-02: CONTRIBUTING.md

> `CONTRIBUTING.md` **уже существовал** на момент аудита (устаревшее
> утверждение issue) — не было создано заново. Точечно исправлено то, что
> ДЕЙСТВИТЕЛЬНО было устаревшим: "Python 3.10+" (везде в проекте — 3.12+),
> отдельный шаг `pip install rich` как "опциональный" (rich уже обязательная
> runtime-зависимость в `pyproject.toml`). Добавлен шаг `mypy` (появился в
> Sprint D, после написания исходного CONTRIBUTING.md).

#### E.6 ✅ FIX — #51 P-01: удалён `requirements.txt`

```
pyproject.toml — единственный источник зависимостей. README/CONTRIBUTING/
CLAUDE.md обновлены (pip install -e . / -e ".[dev]" вместо -r requirements.txt).
```

#### E.7 ✅ FIX (скорректировано) — #51 P-02: верхние границы версий

```
requests>=2.34.2,<3.0
psutil>=5.9,<8.0     # issue предлагал <7.0 — устарело, см. ниже
rich>=13.0,<16.0     # issue не предлагал границу для rich — добавлена по аналогии
```

> Issue предлагал `psutil<7.0`, но в окружении уже установлен и используется
> `psutil` 7.2.2 (и `rich` 15.0.0) — весь тестовый набор проходит именно с
> ними. Буквальное следование предложению issue сломало бы `pip install
> -e ".[dev]"` немедленно. Границы выставлены с запасом НАД реально
> проверенными версиями, а не по устаревшему примеру из аудита.

#### E.8 ✅ FIX — #51 C-03: `release.yml` (только GitHub Release)

```
.github/workflows/release.yml — триггер: push тега v*.
Собирает sdist+wheel (python -m build), создаёт GitHub Release
(softprops/action-gh-release@v2, generate_release_notes: true).
```

> PyPI-публикация НЕ включена: `pypa/gh-action-pypi-publish` требует
> настроенного trusted publisher на pypi.org для этого проекта — это
> одноразовая настройка, которую должен сделать владелец репозитория со
> своим PyPI-аккаунтом, агент это не может сделать за него. Комментарий в
> самом workflow объясняет, что добавить, когда trusted publisher появится.

---

### 🚀 Roadmap batch — #53, #54, #58 (частично) ✅ (2026-07-03)

```
#53 --output csv, #58 экспорт в Markdown — тот же механизм, что --output
    json (Sprint E.3): _rows_to_csv()/_rows_to_markdown() в cli.py,
    построчно из тех же словарей run_tests()/run_benchmark()/
    run_microbench_mode(). Пишут в stdout (перенаправление шеллом), а не
    сохраняют файл сами — issue #58 предлагал "сохраняет RESULTS.md", но
    единообразие с json/csv (все в stdout) показалось важнее буквального
    соответствия формулировке issue.

#54 --watch — новая опциональная зависимость watchfiles
    (pip install stepik-grader[watch]). Только для --mode 1/2 (--mode 3/4 —
    parser.error). Перезапускает ВЕСЬ режим на любое изменение, не
    вычисляет, какой именно файл изменился (issue предлагал "перезапускать
    только изменённый файл" — для --mode 2 это потребовало бы сопоставлять
    путь изменения с его собственной test_dir и печатать частичный
    результат отдельно от уже напечатанной таблицы — решено не усложнять).
```

> #55 (сравнение с solution.py), #56 (`.grader_cache/`), #57 (pytest-плагин)
> и остальная часть #58/#59 НЕ взяты в этот проход — каждая либо трогает
> несколько модулей сразу (#55, #56), либо по сути отдельный
> пакет/инфраструктура (#57 — pytest-плагин, #58 Web UI/VS Code/PyPI, #59
> Docker-sandbox/другие платформы/AI-подсказки), либо требует внешнего
> API-ключа (#59 AI-подсказки). Остаются в бэклоге, см. CHECKPOINT.md.

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
- Текущий статус: **доступен для работы** — Sprint 6–8.2 в grader завершены
  (2026-07-03); собственная документация проекта (CLAUDE.md/CHANGELOG.md/
  CONTRIBUTING.md/CI в самом Glossary-Python) пока отсутствует — Issue #38
  (работа ведётся в отдельном репозитории, не здесь)
- Будущая интеграция (Sprint 10+): grader показывает ссылку на глоссарий
  при RE/WA — например, `RecursionError → #recursion`
- НЕ трогать этот проект в текущей сессии (изменения — только через
  отдельную задачу/issue в самом Glossary-Python)

---

## 📊 МЕТРИКИ ПРОЕКТА (на момент v1.3.0)

| Метрика | Значение |
|---------|---------|
| Версия | 1.3.0 (stable) |
| Python | 3.12 / 3.13 / 3.14 |
| Тестов | 599 |
| Покрытие | 95% |
| Строк (grader.py) | 93 (тонкий фасад — 7 исполняемых `Stmts` по pytest-cov, Sprint 7 ✅) |
| Layout | src/-layout (`src/stepik_grader/`, Issue #35 / Sprint 8.2 ✅) |
| Зависимостей runtime | 3 (requests, psutil, rich) |
| CI | GitHub Actions (pytest + ruff + mypy) |

---

## ✅ ЧЕКЛИСТ ПЕРЕД PR

```
[ ] git checkout ArtVsMark-patch-1
[ ] pytest tests/ -x -q --tb=short   → все зелёные
[ ] ruff check .                      → 0 ошибок
[ ] ruff format --check .             → 0 ошибок
[ ] mypy src/stepik_grader --ignore-missing-imports  → 0 ошибок (Sprint D)
[ ] Новые функции имеют type hints и docstring
[ ] Новые модули имеют __all__
[ ] from __future__ import annotations в начале файла
[ ] Коммит в Conventional Commits формате
[ ] CHECKPOINT.md обновлён (если завершён спринт)
[ ] CHANGELOG.md обновлён (если добавлена/исправлена фича)
```
