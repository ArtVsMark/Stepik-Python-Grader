"""issue #647 (DEV-02): общий скелет строк bench/microbench вынесен в хелперы.

``grade_benchmark`` (режим 3) и ``grade_microbench`` (режим 4) повторяли один
скелет сборки строк ответа — «успешные по возрастанию median, ошибочные в
конец» — различаясь лишь форматом колонок (секунды vs µs). Теперь скелет —
``_ranked_bench_rows`` с ``row_of``-колбэком (``_bench_row``/``_microbench_row``),
а запись истории — общий ``_record_bench_history``.
"""

from __future__ import annotations

import pathlib

from stepik_grader.web.viewmodels import (
    _bench_row,
    _microbench_row,
    _ranked_bench_rows,
    _rel,
)

_BASE = pathlib.Path("/w")


def _ok(median: float) -> dict[str, object]:
    return {
        "runs": 5,
        "min": median,
        "median": median,
        "mean": median,
        "max": median,
        "stdev": 0.0,
        "peak_memory_mb": 2.0,
        "relative": 1.0,
        "verdict": "SIMILAR",
    }


def test_ranked_rows_sorts_ok_by_median_then_errors_last() -> None:
    fast, slow, broken = _BASE / "fast.py", _BASE / "slow.py", _BASE / "broken.py"
    results = {slow: _ok(3.0), fast: _ok(1.0), broken: {"error": "boom"}}

    rows = _ranked_bench_rows(results, _BASE, _bench_row)

    assert [r["file"] for r in rows] == [_rel(fast, _BASE), _rel(slow, _BASE), _rel(broken, _BASE)]
    assert rows[-1]["verdict"] == "ERR"
    assert rows[-1]["error"] == "boom"


def test_ranked_rows_uses_row_of_for_columns() -> None:
    """row_of задаёт набор колонок — секунды (bench) vs µs (microbench)."""
    sol = _BASE / "s.py"
    results = {sol: _ok(0.001)}

    bench = _ranked_bench_rows(results, _BASE, _bench_row)[0]
    micro = _ranked_bench_rows(results, _BASE, _microbench_row)[0]

    # bench — секундные строковые колонки; microbench — числовые µs.
    assert "median" in bench and "median_us" not in bench
    assert "median_us" in micro and "median" not in micro
    assert micro["median_us"] == round(0.001 * 1e6, 3)  # 1000.0 µs
    # общие колонки совпадают
    assert bench["file"] == micro["file"] == _rel(sol, _BASE)
    assert bench["memory_mb"] == micro["memory_mb"] == 2.0


def test_ranked_rows_empty_results_yield_no_rows() -> None:
    assert _ranked_bench_rows({}, _BASE, _bench_row) == []
