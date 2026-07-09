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

from stepik_grader.config import CONFIG
from stepik_grader.core.glossary import lookup_from_error
from stepik_grader.core.grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    run_benchmark,
    run_tests,
)
from stepik_grader.core.microbench_runner import apply_relative_ranking
from stepik_grader.core.reporter import fmt_time
from stepik_grader.core.test_loader import (
    find_all_solution_files,
    load_test_cases,
    resolve_test_dir,
)
from stepik_grader.glossary.detector import MissingConceptDetector
from stepik_grader.glossary.json_provider import (
    GlossaryError,
    JsonGlossaryProvider,
    append_missing_entries,
)

# Вердикты-"ошибки" (в отличие от AC) — ErrorCard-поля (severity/stderr/
# suggestions/...) заполняются только для них (issue #125, web-mvp.md §
# «Модель error cards»).
_FAILURE_VERDICTS = frozenset({"WA", "RE", "TLE"})

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


def _wa_suggestion(actual: str, expected: str) -> str | None:
    """Курированная (не AI) эвристика: совпадает после rstrip → похоже на пробелы/CRLF."""
    if actual and expected and actual != expected and actual.rstrip() == expected.rstrip():
        return (
            "Вывод совпадает после удаления хвостовых пробелов/переводов строк"
            " — проверьте форматирование."
        )
    return None


def _error_card_actions(
    *, verdict: str, stdin: str, actual: str, glossary_ids: list[str]
) -> list[str]:
    """MVP-набор action cards для кейса (issue #125) — только 5 реализованных id.

    Никогда не возвращает ``create_test``/``compare_solutions`` — они вне
    скоупа #125 (design-only, см. docs/web-mvp.md § Action cards).
    """
    actions = ["run_again"]
    if stdin:
        actions.append("copy_input")
    if actual:
        actions.append("copy_output")
    if verdict in _FAILURE_VERDICTS:
        actions.append("explain_error")
    if glossary_ids:
        actions.append("open_glossary")
    return actions


def _known_glossary_terms() -> set[str]:
    """Search-термины настроенной локальной базы (пусто, если store не задан)."""
    if not CONFIG.glossary_store:
        return set()
    try:
        return JsonGlossaryProvider.load(CONFIG.glossary_store).known_terms()
    except GlossaryError:
        return set()


def _queue_missing_concept(error_text: str, *, source: str, missing_queue_path: str) -> None:
    """Best-effort J7: RE с неизвестным исключением → очередь пополнения глоссария.

    Best-effort и defensive (issue #125, как и опциональный кэш #56): плохой/
    незаписываемый путь к очереди не должен ронять грейдинг.
    """
    try:
        entry = MissingConceptDetector().detect_from_error(
            error_text, known=_known_glossary_terms(), source=source, verdict="RE"
        )
        if entry is not None:
            append_missing_entries(missing_queue_path, [entry])
    except (GlossaryError, OSError):
        pass


def _case_view(
    index: int,
    case: dict[str, Any],
    *,
    stdin: str = "",
    source: str = "",
    missing_queue_path: str | None = None,
) -> dict[str, Any]:
    """Представление одного тест-кейса для UI — ErrorCard для WA/RE/TLE (issue #125)."""
    error = case.get("error", "")
    verdict = case.get("verdict") or ("RE" if error else "?")
    passed = bool(case.get("passed"))
    actual = "\n".join(case.get("output") or [])

    # issue #72: карточка ошибки — тип исключения, пояснение, ссылка на глоссарий.
    entry = lookup_from_error(error) if error else None
    glossary_ids = [entry.anchor] if entry is not None and verdict == "RE" else []

    view: dict[str, Any] = {
        "n": index,
        "case_n": index,
        "verdict": verdict,
        "time": round(case.get("time", 0.0), 4),
        "error": error,
        # diff показываем только для непрошедших — иначе пусто.
        "diff": "" if passed else case.get("diff", ""),
        "stdin": stdin,
        "actual": actual,
        "actions": _error_card_actions(
            verdict=verdict, stdin=stdin, actual=actual, glossary_ids=glossary_ids
        ),
    }
    if entry is not None:
        view["glossary"] = {
            "exception": entry.exception,
            "hint": entry.hint,
            "url": entry.url,
        }

    if verdict in _FAILURE_VERDICTS:
        view["severity"] = "warning" if verdict == "TLE" else "error"
        suggestions: list[str] = []
        if verdict == "RE" and entry is not None:
            suggestions = [entry.hint]
        elif verdict == "TLE":
            suggestions = [
                "Превышён лимит времени — проверьте сложность алгоритма"
                " или наличие бесконечного цикла."
            ]
        elif verdict == "WA":
            expected = "\n".join(case.get("expected") or [])
            hint = _wa_suggestion(actual, expected)
            if hint:
                suggestions = [hint]
        view["suggestions"] = suggestions

    if verdict == "WA":
        view["expected"] = "\n".join(case.get("expected") or [])
    if verdict in ("RE", "TLE"):
        view["stderr"] = error
        view["exit_code"] = case.get("exit_code")
    if verdict == "TLE":
        view["timeout_s"] = CONFIG.timeout_seconds
    if verdict == "RE":
        view["glossary_ids"] = glossary_ids
        if not glossary_ids:
            # Неизвестное исключение — нет карточки в локальной базе (J7).
            _queue_missing_concept(
                error,
                source=source,
                missing_queue_path=missing_queue_path or CONFIG.glossary_missing_queue,
            )

    return view


def grade_path(path: str, *, missing_queue_path: str | None = None) -> dict[str, Any]:
    """Прогрейдить файл/папку на корректность (режим 1/2).

    Возвращает JSON-совместимый dict: kind ("file"|"dir"|"error"), mode="tests",
    base, rows (по одному решению) либо message при ошибке.

    ``missing_queue_path`` — путь к очереди пополнения глоссария (J7); None →
    ``CONFIG.glossary_missing_queue`` (issue #125). Параметр в основном для
    тестов — production-вызовы полагаются на дефолт из конфига.
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
        # Отдельная (дешёвая) загрузка тест-кейсов ради stdin для ErrorCard —
        # run_tests() уже прогнал их в том же порядке (issue #125), поэтому
        # zip по позиции корректен без изменения сигнатуры run_tests().
        test_cases = load_test_cases(test_dir)
        rows.append(
            {
                "file": _rel(sol, base),
                "status": "OK" if ok else "FAIL",
                "passed": res["passed"],
                "total": res["total"],
                "total_time": round(res["total_time"], 4),
                "avg_time": round(res["avg_time"], 4),
                "memory_mb": round(res["peak_memory_mb"], 2),
                "cases": [
                    _case_view(
                        i,
                        c,
                        stdin="\n".join(tc.input_lines),
                        source=_rel(sol, base),
                        missing_queue_path=missing_queue_path,
                    )
                    for i, (c, tc) in enumerate(zip(res["cases"], test_cases, strict=True), 1)
                ],
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
