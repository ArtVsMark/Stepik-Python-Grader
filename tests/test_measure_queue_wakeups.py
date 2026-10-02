"""Тесты замера пробуждений очереди (issue #1427, правило 169).

Расписание площадки — **пожелание, а не частота**. У механизма, объявленного
страховкой, обязан быть замер: пока его нет, «работает» держится верой, а
основной путь строят так, будто страховка есть.

Замер на нашем же репозитории: за 201 час заказано ~201 срабатывание, случилось
42 — впятеро реже, интервалы от 55 минут до 13 часов 22 минут.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "measure_queue_wakeups.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_measure_queue_wakeups", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _run(event: str, created: str) -> dict[str, Any]:
    return {"event": event, "created_at": created}


# --- сам замер -------------------------------------------------------------------


def test_sources_are_counted_separately() -> None:
    """Каждый источник считается отдельно — иначе непонятно, что работает."""
    measurement = _MODULE.measure(
        [
            _run("workflow_run", "2026-09-01T10:00:00Z"),
            _run("schedule", "2026-09-01T11:00:00Z"),
            _run("workflow_run", "2026-09-01T12:00:00Z"),
            _run("workflow_dispatch", "2026-09-01T13:00:00Z"),
        ]
    )

    assert measurement.by_event == {"workflow_run": 2, "schedule": 1, "workflow_dispatch": 1}
    assert measurement.scheduled == 1


def test_intervals_are_measured_between_scheduled_runs() -> None:
    """Интервал — наблюдаемая величина, а не заказанная в cron."""
    measurement = _MODULE.measure(
        [
            _run("schedule", "2026-09-01T10:00:00Z"),
            _run("schedule", "2026-09-01T14:00:00Z"),
            _run("schedule", "2026-09-01T15:00:00Z"),
        ]
    )

    assert measurement.gaps_minutes == [240.0, 60.0]


def test_the_window_is_measured_not_assumed() -> None:
    """Длина окна берётся из крайних прогонов, а не из числа записей."""
    measurement = _MODULE.measure(
        [_run("schedule", "2026-09-01T00:00:00Z"), _run("schedule", "2026-09-02T00:00:00Z")]
    )

    assert measurement.window_hours == pytest.approx(24.0)


def test_an_empty_input_invents_no_numbers() -> None:
    """На пустом входе — пустой замер, а не выдуманные величины."""
    measurement = _MODULE.measure([])

    assert measurement.by_event == {}
    assert measurement.gaps_minutes == []
    assert measurement.window_hours == 0.0


# --- граница правила: замер требуется у ОБЪЯВЛЕННОЙ страховки --------------------


def test_the_mover_declares_its_schedule_as_insurance() -> None:
    """Приёмка: предмет правила у нас есть — график назван страховкой."""
    mover = (_ROOT / ".github" / "workflows" / "merge-queue.yml").read_text(encoding="utf-8")

    assert _MODULE.declares_insurance(mover)


def test_a_schedule_nobody_called_insurance_is_not_the_subject() -> None:
    """Расписание, страховкой не названное, — просто расписание."""
    assert not _MODULE.declares_insurance("on:\n  schedule:\n    - cron: '0 * * * *'\n")


def test_the_header_no_longer_promises_a_frequency() -> None:
    """Комментарий называет наблюдаемое, а не задуманное.

    Прежняя редакция обещала «раз в час он посмотрит на неё сам» — утверждение
    о частоте, которого площадка не даёт. Замер показал впятеро реже.

    Прежняя формулировка в файле осталась, и это намеренно: запись о том, что
    было и почему исправлено, — история, а не действующее утверждение (правило
    114). Поэтому проверяется не отсутствие строки, а то, что она названа
    прежней и рядом стоят измеренные числа.
    """
    mover = (_ROOT / ".github" / "workflows" / "merge-queue.yml").read_text(encoding="utf-8")

    assert "пожелание, а не обещание" in mover.lower()
    assert "Прежняя редакция" in mover
    assert "Замер за" in mover, "частота названа без измерения — то же обещание, только тише"


# --- три исхода ------------------------------------------------------------------


def test_declared_insurance_that_never_fires_is_a_finding(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Страховка объявлена, а по расписанию — ни одного прогона: её нет.

    Порог «сколько процентов достаточно» намеренно не вводится: он был бы
    выдуман, а не измерен. Отказ односторонний и на решаемом случае.
    """
    monkeypatch.setattr(
        _MODULE.gh_rest,
        "request",
        lambda *a, **k: type(
            "R", (), {"data": {"workflow_runs": [_run("workflow_run", "2026-09-01T10:00:00Z")]}}
        )(),
    )

    assert _MODULE.main([]) == 1
    assert "страховки нет" in capsys.readouterr().out


def test_a_firing_schedule_is_silent_but_still_measured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Срабатывает — молчим, но числа печатаем всегда.

    Вопрос правила не «хорошо ли», а «измерено ли»: молчание без чисел и есть
    та вера, против которой оно заведено.
    """
    monkeypatch.setattr(
        _MODULE.gh_rest,
        "request",
        lambda *a, **k: type(
            "R",
            (),
            {
                "data": {
                    "workflow_runs": [
                        _run("schedule", "2026-09-01T10:00:00Z"),
                        _run("workflow_run", "2026-09-01T11:00:00Z"),
                    ]
                }
            },
        )(),
    )

    assert _MODULE.main([]) == 0
    out = capsys.readouterr().out
    assert "schedule: 1" in out
    assert "окно" in out


def test_an_unreachable_platform_is_the_third_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Замер снять не удалось — код 2, а не «страховки нет»."""

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise _MODULE.gh_rest.GitHubError("403")

    monkeypatch.setattr(_MODULE.gh_rest, "request", _refuse)

    assert _MODULE.main([]) == 2
    assert "замер не снят" in capsys.readouterr().out


def test_no_runs_at_all_is_not_a_verdict_about_insurance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Прогонов нет вовсе — окна наблюдения нет, а не «страховка не работает»."""
    monkeypatch.setattr(
        _MODULE.gh_rest,
        "request",
        lambda *a, **k: type("R", (), {"data": {"workflow_runs": []}})(),
    )

    assert _MODULE.main([]) == 2
    assert "окна наблюдения нет" in capsys.readouterr().out
