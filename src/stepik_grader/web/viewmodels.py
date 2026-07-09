"""viewmodels.py — построение JSON-ответов веб-интерфейса из ядра грейдера.

Архитектурный слой: Application/UI (web-адаптер). Переиспользует
``core/grader_core`` (``run_tests``/``run_benchmark``), ``core/test_loader``
(``find_all_solution_files``/``resolve_test_dir``),
``core/microbench_runner.apply_relative_ranking`` и ``core/reporter.fmt_time``
— логика грейдинга и форматирования не дублируется. ``web → core` ациклично.
"""

from __future__ import annotations

import pathlib
from typing import Any

from stepik_grader.core.glossary import lookup_from_error
from stepik_grader.core.grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    run_benchmark,
    run_tests,
)
from stepik_grader.core.microbench_runner import apply_relative_ranking
from stepik_grader.core.reporter import fmt_time
from stepik_grader.core.test_loader import find_all_solution_files, resolve_test_dir

__all__ = ["grade_benchmark", "grade_path"]


def _rel(path: str, base: str) -> str:
    """Путь относительно base (для компактного отображения), с fallback."""
    try:
        return str(pathlib.Path(path).relative_to(base))
    except ValueError:
        return pathlib.Path(path).name


def _resolve_solutions(path: str) -> tuple[str, str, list[str]] | dict[str, Any]:
    """Вернуть (kind, base, solutions) для файла/папки или error-dict.

    kind — "file" | "dir". Общий вход для обоих режимов грейдинга.
    """
    p = pathlib.Path(path).expanduser()
    if p.is_file():
        return "file", str(p.parent), [str(p)]
    if p.is_dir():
        solutions = find_all_solution_files(str(p))
        if not solutions:
            return {"kind": "error", "message": f"Решения не найдены в: {path}", "rows": []}
        return "dir", str(p), solutions
    return {"kind": "error", "message": f"Путь не найден: {path}", "rows": []}


def _case_view(index: int, case: dict[str, Any]) -> dict[str, Any]:
    """Компактное представление одного тест-кейса для UI."""
    error = case.get("error", "")
    view: dict[str, Any] = {
        "n": index,
        "verdict": case.get("verdict") or ("RE" if error else "?"),
        "time": round(case.get("time", 0.0), 4),
        "error": error,
        # diff показываем только для непрошедших — иначе пусто.
        "diff": "" if case.get("passed") else case.get("diff", ""),
    }
    # issue #72: карточка ошибки — тип исключения, пояснение, ссылка на глоссарий.
    entry = lookup_from_error(error) if error else None
    if entry is not None:
        view["glossary"] = {
            "exception": entry.exception,
            "hint": entry.hint,
            "url": entry.url,
        }
    return view


def grade_path(path: str) -> dict[str, Any]:
    """Прогрейдить файл/папку на корректность (режим 1/2).

    Возвращает JSON-совместимый dict: kind ("file"|"dir"|"error"), mode="tests",
    base, rows (по одному решению) либо message при ошибке.
    """
    resolved = _resolve_solutions(path)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

    rows: list[dict[str, Any]] = []
    for sol in solutions:
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not pathlib.Path(test_dir).is_dir():
            rows.append({"file": _rel(sol, base), "status": "NO TESTS", "passed": 0, "total": 0})
            continue
        res = run_tests(sol, test_dir)
        ok = res["total"] > 0 and res["passed"] == res["total"]
        rows.append(
            {
                "file": _rel(sol, base),
                "status": "OK" if ok else "FAIL",
                "passed": res["passed"],
                "total": res["total"],
                "total_time": round(res["total_time"], 4),
                "avg_time": round(res["avg_time"], 4),
                "memory_mb": round(res["peak_memory_mb"], 2),
                "cases": [_case_view(i, c) for i, c in enumerate(res["cases"], 1)],
            }
        )
    return {"kind": kind, "mode": "tests", "base": base, "rows": rows}


def grade_benchmark(path: str, *, repeats: int = 15) -> dict[str, Any]:
    """Бенчмаркнуть файл/папку (режим 3) и ранжировать по медиане.

    Строки отсортированы от быстрого к медленному; вердикт SIMILAR/SLOWER/
    MUCH_SLOWER — относительно самого быстрого (как в CLI mode 3). Ошибочные
    решения идут в конец.
    """
    resolved = _resolve_solutions(path)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

    results: dict[str, dict[str, Any]] = {}
    for sol in solutions:
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not pathlib.Path(test_dir).is_dir():
            results[sol] = {"error": "тесты не найдены", "runs": 0}
        else:
            results[sol] = run_benchmark(sol, test_dir, repeats=max(1, repeats))
    apply_relative_ranking(
        results,
        similar_threshold=SIMILAR_THRESHOLD,
        much_slower_threshold=MUCH_SLOWER_THRESHOLD,
    )

    ok = {s: d for s, d in results.items() if not d.get("error")}
    rows: list[dict[str, Any]] = []
    for sol in sorted(ok, key=lambda s: ok[s]["median"]):
        d = ok[sol]
        rows.append(
            {
                "file": _rel(sol, base),
                "runs": d["runs"],
                "min": fmt_time(d["min"]),
                "median": fmt_time(d["median"]),
                "relative": round(d.get("relative", 1.0) * 100, 1),
                "verdict": d.get("verdict", "SIMILAR"),
                "memory_mb": round(d["peak_memory_mb"], 2),
            }
        )
    for sol, d in results.items():
        if d.get("error"):
            rows.append({"file": _rel(sol, base), "verdict": "ERR", "error": d["error"]})
    return {"kind": kind, "mode": "bench", "base": base, "rows": rows}
