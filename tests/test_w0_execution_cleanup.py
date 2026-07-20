"""Волна W0 аудита 2026-07-20: гигиена слоя исполнения.

Покрывает четыре находки одним набором (эпик #614):

* **#623** — ``run_microbench_mode`` не применял ``_apply_run_mode_override``,
  из-за чего function-only решение уходило на stdin-путь.
* **#624** — ``LocalRunner.run`` не гарантировал уборку дочернего процесса при
  неожиданном исключении (внешний try ловил только ``OSError``).
* **#625** — RE с пустым stderr считался ``failed`` вместо ``errors``;
  отменённый кейс попадал в ошибки и выставлял ``first_fail``.
* **#626** — устаревший ``tests/`` не сигнализировался при неудачном
  перекачивании задачи.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader import downloader
from stepik_grader.core import grader_core
from stepik_grader.core import runner as runner_mod
from stepik_grader.core.grader_core import TestCase, _map_outcome_to_result
from stepik_grader.core.runner import LocalRunner, RunOutcome, RunSpec


def _case(test_type: str = "stdin") -> TestCase:
    """Минимальный тест-кейс."""
    return TestCase(index=1, input_lines=["1"], expected_lines=["ok"], test_type=test_type)


# ---------------------------------------------------------------------------
# #625 — классификация исходов
# ---------------------------------------------------------------------------


def test_nonzero_exit_without_stderr_still_carries_error_message() -> None:
    """RE без stderr (сигнал/segfault) должен нести осмысленное сообщение.

    Регрессия #625: ``error=stderr.strip()`` давало пустую строку, а
    ``run_tests`` классифицирует ошибки по truthiness этого поля — RE молча
    попадал в ``failed``, то есть выглядел как обычный неверный ответ.
    """
    result = _map_outcome_to_result(
        RunOutcome(stdout=b"", stderr=b"", returncode=-11, elapsed=0.2),
        _case(),
        timeout=5.0,
    )

    assert result["verdict"] == "RE"
    assert result["exit_code"] == -11
    assert result["error"], "пустая ошибка снова утопит RE в failed"
    assert "-11" in result["error"]


def test_stderr_is_preferred_over_synthetic_message() -> None:
    """Если stderr есть — используем его, а не подставное сообщение."""
    result = _map_outcome_to_result(
        RunOutcome(stdout=b"", stderr=b"Traceback: boom\n", returncode=1, elapsed=0.1),
        _case(),
        timeout=5.0,
    )

    assert result["error"] == "Traceback: boom"


def test_cancelled_case_is_not_counted_as_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отмена — не вердикт о решении: ни в ``errors``, ни в ``first_fail``.

    Регрессия #625: у CANCELLED поле ``error`` непустое ("Cancelled by user"),
    поэтому кейс попадал в ошибки и UI показывал «первый упавший тест» там,
    где пользователь просто нажал «Отмена».
    """
    solution = tmp_path / "task1.py"
    solution.write_text("print('ok')\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "1").write_text("1\n", encoding="utf-8")
    (tests_dir / "1.clue").write_text("ok\n", encoding="utf-8")

    def fake_run_single_test(path: pathlib.Path, case: TestCase, **kwargs: object) -> dict:
        # Реальный маппинг гарантирует корректную форму CaseResult.
        return _map_outcome_to_result(RunOutcome(cancelled=True, elapsed=0.1), case, 5.0)

    monkeypatch.setattr(grader_core, "run_single_test", fake_run_single_test)

    summary = grader_core.run_tests(solution, tests_dir)

    assert summary["errors"] == 0, "отмена не должна считаться ошибкой решения"
    assert summary["failed"] == 0
    assert summary["first_fail"] is None, "отмена не помечает тест как упавший"


# ---------------------------------------------------------------------------
# #624 — гарантированная уборка процесса
# ---------------------------------------------------------------------------


def test_local_runner_kills_child_on_unexpected_exception(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Неожиданное исключение после spawn не должно оставлять живой процесс.

    Регрессия #624: внешний try ловил только ``OSError`` и ``TimeoutExpired``,
    поэтому ошибка из ``Thread.start()`` / ``_apply_memory_limit`` уходила
    наружу, а процесс решения продолжал жить — на сервере это утечка.
    """
    solution = tmp_path / "sleepy.py"
    solution.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    killed: list[object] = []
    real_kill = runner_mod._kill_process_tree

    def spy_kill(proc: object) -> None:
        killed.append(proc)
        real_kill(proc)  # действительно убиваем, иначе тест оставит процесс

    def explode(pid: int, max_memory_mb: int | None) -> None:
        raise RuntimeError("unexpected failure after spawn")

    monkeypatch.setattr(runner_mod, "_kill_process_tree", spy_kill)
    monkeypatch.setattr(runner_mod, "_apply_memory_limit", explode)

    spec = RunSpec(path=solution, stdin=None, timeout=5.0, measure_memory=False)

    with pytest.raises(RuntimeError, match="unexpected failure"):
        LocalRunner().run(spec)

    assert killed, "процесс решения должен быть убит в finally"


def test_local_runner_does_not_kill_on_normal_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """На штатном пути finally не должен трогать уже завершившийся процесс."""
    solution = tmp_path / "quick.py"
    solution.write_text("print('done')\n", encoding="utf-8")

    killed: list[object] = []
    monkeypatch.setattr(runner_mod, "_kill_process_tree", lambda p: killed.append(p))

    outcome = LocalRunner().run(
        RunSpec(path=solution, stdin=None, timeout=10.0, measure_memory=False)
    )

    assert outcome.returncode == 0
    assert outcome.stdout.decode().strip() == "done"
    assert not killed, "штатное завершение не должно вызывать kill"


# ---------------------------------------------------------------------------
# #626 — устаревший tests/ при неудачном перекачивании
# ---------------------------------------------------------------------------


def test_warns_when_stale_tests_remain(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    """Непустой tests/ после неудачного перекачивания — громкое предупреждение.

    Удалять нельзя: документация разрешает заполнять tests/ вручную, поэтому
    автоснос затирал бы работу пользователя (issue #626).
    """
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "tests" / "1").write_text("старый кейс\n", encoding="utf-8")

    downloader._warn_if_stale_tests(task_dir)

    assert "НЕ обновлены" in capsys.readouterr().out


def test_no_warning_when_tests_dir_empty_or_missing(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    """Пустой или отсутствующий tests/ не порождает шум."""
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    downloader._warn_if_stale_tests(task_dir)  # tests/ нет вовсе
    (task_dir / "tests").mkdir()
    downloader._warn_if_stale_tests(task_dir)  # tests/ пустой

    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# #623 — детекция режима в микробенче
# ---------------------------------------------------------------------------


def test_microbench_applies_run_mode_override_per_solution(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Микробенч применяет override режима, и каждому решению — своя копия кейсов.

    Регрессия #623: ``run_microbench_mode`` вообще не вызывал
    ``_apply_run_mode_override`` (в отличие от ``run_tests``/``run_benchmark``),
    поэтому function-only решение уходило на stdin-путь и микробенч мерил лишь
    время определения функций. Копия обязательна: override мутирует список на
    месте и не откатывает "function" обратно, иначе он протёк бы на следующие
    решения группы.
    """
    first = tmp_path / "task1_1.py"
    first.write_text("def solve(a):\n    return a\n", encoding="utf-8")
    second = tmp_path / "task1_2.py"
    second.write_text("def solve(a):\n    return a\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "1").write_text("1\n", encoding="utf-8")
    (tests_dir / "1.clue").write_text("1\n", encoding="utf-8")

    real_override = grader_core._apply_run_mode_override
    passed_lists: list[list[TestCase]] = []

    def spy_override(
        cases: list[TestCase], solution_path: pathlib.Path, test_dir: pathlib.Path
    ) -> list[TestCase]:
        passed_lists.append(cases)
        return real_override(cases, solution_path, test_dir)

    monkeypatch.setattr(grader_core, "_apply_run_mode_override", spy_override)
    # Не гоняем реальный timeit-subprocess: интересует только маршрутизация.
    monkeypatch.setattr(
        grader_core,
        "run_microbench",
        lambda *a, **k: {"error": "", "times": [0.001], "peak_memory_mb": 1.0},
    )

    grader_core.run_microbench_mode([first, second], tests_dir, number=100)

    assert len(passed_lists) == 2, "override должен применяться к каждому решению (#623)"
    assert passed_lists[0] is not passed_lists[1], "каждому решению — своя копия кейсов"
