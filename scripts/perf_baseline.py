#!/usr/bin/env python3
"""scripts/perf_baseline.py — базовые замеры стоимости самого грейдера (issue #839).

Продукт умеет бенчмаркать чужой код, а собственная стоимость не измерялась ни
разу: заметить регресс холодного старта или загрузки глоссария было нечем.
Скрипт закрывает этот пробел — не «оптимизирует», а даёт цифры, с которыми
можно сравнить следующую версию.

Что меряется и почему именно это:

* **холодный старт CLI** (``--version`` в свежем процессе) — то, что видит
  пользователь при каждом запуске; сюда входит импорт пакета и чтение конфига;
* **импорт пакета** отдельно — отделяет стоимость импортов от работы CLI;
* **загрузка карточек глоссария и stdlib-инвентаря** — самые крупные данные в
  комплекте, читаются при подсказках и панели «Функции в коде»;
* **прогон на сотнях кейсов** — линейная часть: показывает цену одного кейса и
  тем самым цену запуска subprocess;
* **параллельные прогоны** — та же работа через job-пул web-слоя: видно, даёт
  ли параллелизм выигрыш или упирается в накладные расходы.

Каждый замер повторяется несколько раз, в отчёт идёт **медиана**: одиночное
измерение на рабочей машине шумит на десятки процентов (антивирус, планировщик,
турбо-буст). Абсолютные числа зависят от машины — сравнивать имеет смысл
прогоны на одном и том же железе.

Запуск::

    python scripts/perf_baseline.py                 # полный отчёт
    python scripts/perf_baseline.py --quick         # без долгих сценариев
    python scripts/perf_baseline.py --output json   # машиночитаемо
    python scripts/perf_baseline.py --cases 500     # своё число кейсов
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

__all__ = ["Measurement", "main", "measure", "run_all"]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DEFAULT_CASES = 200
_DEFAULT_REPEATS = 5
# Быстрый режим: тех же сценариев меньше — чтобы прогон в CI/на слабой машине
# укладывался в разумное время, но набор метрик не менялся.
_QUICK_CASES = 30
_QUICK_REPEATS = 3


@dataclass
class Measurement:
    """Один замер: имя, медиана и разброс в секундах."""

    name: str
    median_s: float
    min_s: float
    max_s: float
    repeats: int
    note: str = ""
    extra: dict[str, float] = field(default_factory=dict)


def measure(name: str, fn: Callable[[], object], *, repeats: int, note: str = "") -> Measurement:
    """Прогнать ``fn`` ``repeats`` раз, вернуть медиану и границы.

    Медиана, а не среднее: один выброс (антивирус, своп) сдвигает среднее так,
    что сравнивать версии становится нельзя.
    """
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return Measurement(
        name=name,
        median_s=statistics.median(samples),
        min_s=min(samples),
        max_s=max(samples),
        repeats=repeats,
        note=note,
    )


def _run_subprocess(args: list[str]) -> None:
    """Запустить процесс и дождаться его; вывод не нужен, важна длительность."""
    subprocess.run(
        [sys.executable, *args],
        cwd=_ROOT,
        capture_output=True,
        check=False,
    )


def _make_task(folder: pathlib.Path, cases: int) -> pathlib.Path:
    """Задача с ``cases`` тест-кейсами формата 3 (input.txt + output.txt)."""
    folder.mkdir(parents=True, exist_ok=True)
    solution = folder / "task.py"
    solution.write_text("print(int(input()) + 1)\n", encoding="utf-8")
    tests = folder / "tests"
    tests.mkdir(exist_ok=True)
    inputs = "\n".join(f"# TEST_{i}:\n{i}" for i in range(1, cases + 1))
    outputs = "\n".join(f"# TEST_{i}:\n{i + 1}" for i in range(1, cases + 1))
    (tests / "input.txt").write_text(inputs + "\n", encoding="utf-8")
    (tests / "output.txt").write_text(outputs + "\n", encoding="utf-8")
    return solution


def _measure_startup(repeats: int) -> list[Measurement]:
    """Холодный старт CLI и голый импорт пакета — в отдельных процессах."""
    return [
        measure(
            "холодный старт CLI (--version)",
            lambda: _run_subprocess(["-m", "stepik_grader", "--version"]),
            repeats=repeats,
            note="то, что пользователь ждёт при каждом запуске",
        ),
        measure(
            "импорт пакета (import stepik_grader)",
            lambda: _run_subprocess(["-c", "import stepik_grader"]),
            repeats=repeats,
            note="сколько из старта приходится на импорты",
        ),
        measure(
            "импорт CLI-слоя (import stepik_grader.cli)",
            lambda: _run_subprocess(["-c", "import stepik_grader.cli"]),
            repeats=repeats,
            note="фасад CLI тянет core-модули и локали",
        ),
    ]


def _measure_data_loading(repeats: int) -> list[Measurement]:
    """Загрузка комплектных данных — в отдельных процессах (кеши холодные)."""
    glossary_code = (
        "from stepik_grader.glossary.json_provider import "
        "BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider; "
        "JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()"
    )
    inventory_code = (
        "from stepik_grader.glossary.stdlib_inventory import build_stdlib_inventory; "
        "build_stdlib_inventory()"
    )
    rules_code = "from stepik_grader import rules; rules.bundled_rules()"
    return [
        measure(
            "загрузка карточек глоссария",
            lambda: _run_subprocess(["-c", glossary_code]),
            repeats=repeats,
            note="самые крупные данные комплекта; включает старт процесса",
        ),
        measure(
            "загрузка stdlib-инвентаря",
            lambda: _run_subprocess(["-c", inventory_code]),
            repeats=repeats,
            note="используется панелью «Функции в коде»",
        ),
        measure(
            "загрузка карточек правил (PEP)",
            lambda: _run_subprocess(["-c", rules_code]),
            repeats=repeats,
            note="раздел «Правила» и блок «Стиль»",
        ),
    ]


def _measure_grading(cases: int, repeats: int) -> list[Measurement]:
    """Прогон на сотнях кейсов — линейная часть и цена одного кейса."""
    from stepik_grader.core.grader_core import run_tests

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="perf-grade-"))
    solution = _make_task(workdir, cases)
    tests_dir = workdir / "tests"

    result = measure(
        f"проверка решения на {cases} кейсах",
        lambda: run_tests(solution, tests_dir),
        repeats=repeats,
        note="полный путь: subprocess на кейс + сравнение вывода",
    )
    result.extra["на один кейс, мс"] = round(result.median_s / cases * 1000, 3)
    return [result]


def _measure_parallel(cases: int, repeats: int) -> list[Measurement]:
    """Те же прогоны через job-пул web-слоя — есть ли выигрыш от параллелизма."""
    from stepik_grader.web import runs

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="perf-jobs-"))
    _make_task(workdir, cases)

    def _four_jobs() -> None:
        jobs = [runs.submit_job("tests", workdir, {"lang": "ru"}) for _ in range(4)]
        deadline = time.monotonic() + 120
        for job in jobs:
            while job.status not in ("done", "error", "cancelled"):
                if time.monotonic() > deadline:
                    return
                time.sleep(0.02)

    result = measure(
        f"4 параллельных прогона по {cases} кейсов (job-пул)",
        _four_jobs,
        repeats=repeats,
        note=f"пул на {runs.CONFIG.job_workers} воркера — упирается в них, а не в CPU",
    )
    runs.shutdown_jobs()
    return [result]


def run_all(*, cases: int, repeats: int, quick: bool) -> list[Measurement]:
    """Собрать все замеры в порядке «дешёвые → дорогие»."""
    results: list[Measurement] = []
    results += _measure_startup(repeats)
    results += _measure_data_loading(repeats)
    results += _measure_grading(cases, repeats)
    if not quick:
        results += _measure_parallel(cases, max(1, repeats // 2))
    return results


def _print_table(results: list[Measurement], meta: dict[str, str]) -> None:
    """Человекочитаемый отчёт: медиана, разброс, примечание."""
    print("Базовые замеры грейдера (issue #839)\n")
    for key, value in meta.items():
        print(f"  {key}: {value}")
    print()
    width = max(len(r.name) for r in results) + 2
    print(f"{'Сценарий':<{width}} {'медиана':>10} {'мин':>9} {'макс':>9}  примечание")
    print("-" * (width + 34) + "-" * 30)
    for r in results:
        print(f"{r.name:<{width}} {r.median_s:>9.3f}с {r.min_s:>8.3f}с {r.max_s:>8.3f}с  {r.note}")
        for label, number in r.extra.items():
            print(f"{'':<{width}} {label}: {number}")


def main(argv: list[str] | None = None) -> int:
    """CLI: снять базовые замеры и напечатать отчёт."""
    parser = argparse.ArgumentParser(
        description="Базовые замеры стоимости самого грейдера (issue #839).",
    )
    parser.add_argument("--cases", type=int, default=0, help="Число тест-кейсов в прогоне.")
    parser.add_argument("--repeats", type=int, default=0, help="Повторов на сценарий (медиана).")
    parser.add_argument("--quick", action="store_true", help="Без долгих сценариев.")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    cases = args.cases or (_QUICK_CASES if args.quick else _DEFAULT_CASES)
    repeats = args.repeats or (_QUICK_REPEATS if args.quick else _DEFAULT_REPEATS)

    meta: dict[str, str] = {
        "python": platform.python_version(),
        "платформа": f"{platform.system()} {platform.release()}",
        "процессор": platform.processor() or "неизвестен",
        "кейсов в прогоне": str(cases),
        "повторов": str(repeats),
    }
    results = run_all(cases=cases, repeats=repeats, quick=args.quick)

    if args.output == "json":
        payload = {"meta": meta, "results": [asdict(r) for r in results]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_table(results, meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
