"""Тесты одного вердикта по проверкам головы изменения (issue #1420).

Предмет здесь не «складываются ли исходы», а три вещи, каждая из которых уже
ломала конвейер у нас или у соседей:

* **пропущенное — не пройденное.** Агрегатор на ``needs:`` при падении соседа
  не краснеет, а пропускается, и защита ветки засчитывает пропуск как успех;
* **пустой список — не «зелено».** Сразу после пуша check-run'ов ещё нет, и
  «нет красных, нет ожидающих» выполняется на пустоте;
* **эталон выведен из дерева, а не хранится копией** (правило 171): копия
  позволяет переименовать ячейку матрицы и остаться согласной с настройкой,
  разойдясь с работой.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from types import ModuleType
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "ci_aggregate.py"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci-complete.yml"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_ci_aggregate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _run(name: str, status: str = "completed", conclusion: str = "success") -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion}


# --- эталон выведен из дерева ----------------------------------------------------


def test_expected_set_comes_from_the_real_ci_file() -> None:
    """Состав читается из ``ci.yml``, а не из константы рядом.

    Копия разъезжается с деревом молча: переименовали ячейку — настройка и
    константа по-прежнему согласны друг с другом, а работа идёт под другими
    именами.
    """
    expected = _MODULE.expected_checks(_CI.read_text(encoding="utf-8"))

    assert "docs-guardrails" in expected
    assert "static" in expected
    assert any(name.startswith("test (") for name in expected)


def test_experimental_combinations_never_hold_the_merge() -> None:
    """Ячейки 3.14 идут под ``continue-on-error`` и слияние держать не должны."""
    expected = _MODULE.expected_checks(_CI.read_text(encoding="utf-8"))

    assert not [name for name in expected if name.endswith(", true)")]


def test_the_aggregate_never_waits_for_itself() -> None:
    """Своё имя в эталон не попадает — иначе вердикт ждал бы сам себя вечно."""
    expected = _MODULE.expected_checks(_CI.read_text(encoding="utf-8"))

    assert _MODULE.AGGREGATE_NAME not in expected


def test_an_unparsed_ci_file_yields_no_expectations() -> None:
    """Разобрать нечего — состав пуст, и это отдельный исход, а не «всё зелено»."""
    assert _MODULE.expected_checks("name: CI\njobs:\n") <= set(
        _MODULE.check_branch_protection.PLAIN_JOBS
    )


# --- вердикт ---------------------------------------------------------------------


def test_all_green_is_ok() -> None:
    assert _MODULE.verdict([_run("static"), _run("e2e")], {"static", "e2e"})[0] == "ok"


def test_a_failure_holds_the_merge() -> None:
    state, lines = _MODULE.verdict([_run("static", conclusion="failure")], {"static"})

    assert state == "fail"
    assert any("static" in line for line in lines)


def test_a_skipped_check_is_not_a_failure() -> None:
    """Джоб может быть законно пропущен своим ``if:`` — краснеть на этом нельзя."""
    assert _MODULE.verdict([_run("e2e", conclusion="skipped")], {"e2e"})[0] == "ok"


def test_an_unknown_conclusion_is_a_refusal() -> None:
    """Перечислено хорошее, а не плохое: «не знаю» и «всё хорошо» — разные вещи.

    Площадка добавляет исходы, не спрашивая нас; список плохого устаревал бы
    молча и пропускал новый вид отказа как успех.
    """
    assert _MODULE.verdict([_run("static", conclusion="что-то новое")], {"static"})[0] == "fail"


def test_a_missing_check_is_waiting_not_green() -> None:
    """Пустой список — «прогон не стартовал», а не «нет красных»."""
    state, lines = _MODULE.verdict([], {"static", "e2e"})

    assert state == "wait"
    assert any("ещё не появились" in line for line in lines)
    assert any("static" in line and "e2e" in line for line in lines)


def test_a_running_check_is_named_while_waiting() -> None:
    state, lines = _MODULE.verdict(
        [_run("static", status="in_progress", conclusion="")], {"static"}
    )

    assert state == "wait"
    assert any("ещё идут" in line and "static" in line for line in lines)


def test_a_failure_wins_over_waiting() -> None:
    """Ждать остальных, когда одна уже упала, — держать очередь ради известного."""
    runs = [_run("static", conclusion="failure"), _run("e2e", status="in_progress", conclusion="")]

    assert _MODULE.verdict(runs, {"static", "e2e"})[0] == "fail"


def test_a_rerun_is_read_by_its_latest_record() -> None:
    """Перезапуск даёт вторую запись с тем же именем — считается последняя."""
    runs = [_run("static", conclusion="failure"), _run("static", conclusion="success")]

    assert _MODULE.verdict(runs, {"static"})[0] == "ok"


def test_foreign_checks_do_not_hold_the_merge() -> None:
    """Проверка вне эталона (`claude-review`) вердикт не решает.

    Её красный цвет означает осечку исполнения, а не дефект кода, — и она
    намеренно не входит в обязательные.
    """
    runs = [_run("static"), _run("claude-review", conclusion="failure")]

    assert _MODULE.verdict(runs, {"static"})[0] == "ok"


# --- коды возврата ---------------------------------------------------------------


def test_an_unknown_head_is_the_third_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Головы нет — «не отработала» (код 2), а не «зелено»."""
    assert _MODULE.main(["--sha", "", "--repo", "x/y"]) == 2


def test_a_refusal_from_the_platform_is_the_third_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _refused(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise _MODULE.gh_rest.GitHubError("403")

    monkeypatch.setattr(_MODULE.gh_rest, "pull_checks", _refused)

    assert _MODULE.main(["--sha", "abc", "--repo", "x/y", "--once"]) == 2


def test_an_exhausted_quota_says_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    def _limited(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise _MODULE.gh_rest.RateLimited("сброс в 12:00")

    monkeypatch.setattr(_MODULE.gh_rest, "pull_checks", _limited)

    assert _MODULE.main(["--sha", "abc", "--repo", "x/y", "--once"]) == _MODULE.gh_rest.EXIT_WAIT


def test_never_arrived_checks_fail_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """К дедлайну состав не собрался — отказ, а не молчаливый успех."""
    monkeypatch.setattr(_MODULE.gh_rest, "pull_checks", lambda *a, **k: {"check_runs": []})

    assert _MODULE.main(["--sha", "abc", "--repo", "x/y", "--once"]) == 1


def test_all_green_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _MODULE.expected_checks(_CI.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        _MODULE.gh_rest,
        "pull_checks",
        lambda *a, **k: {"check_runs": [_run(name) for name in expected]},
    )

    assert _MODULE.main(["--sha", "abc", "--repo", "x/y", "--once"]) == 0


def test_a_failed_check_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _MODULE.expected_checks(_CI.read_text(encoding="utf-8"))
    runs = [_run(name) for name in expected]
    runs[0] = {**runs[0], "conclusion": "failure"}
    monkeypatch.setattr(_MODULE.gh_rest, "pull_checks", lambda *a, **k: {"check_runs": runs})

    assert _MODULE.main(["--sha", "abc", "--repo", "x/y", "--once"]) == 1


# --- имя и форма самого workflow -------------------------------------------------


def test_the_job_name_matches_the_constant() -> None:
    """Постоянное имя — весь смысл задачи, и совпадение держится тестом.

    Настройка защиты ветки будет ссылаться на эту строку; расхождение даёт не
    красное, а ожидание, неотличимое от «проверки ещё идут».
    """
    text = _WORKFLOW.read_text(encoding="utf-8")

    assert re.search(rf"^  {re.escape(_MODULE.AGGREGATE_NAME)}:$", text, re.MULTILINE)


def test_the_aggregate_is_not_assembled_from_needs() -> None:
    """``needs:`` здесь запрещён: пропущенное защита засчитывает как пройденное.

    Ищется ключ YAML, а не подстрока: сам запрет объяснён в шапке файла
    словами, и текстовый поиск ловил бы объяснение вместо нарушения.
    """
    keys = [
        line
        for line in _WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") and re.match(r"^\s+needs:", line)
    ]

    assert not keys


def test_the_workflow_reads_the_pull_request_head() -> None:
    """У ``pull_request`` ``GITHUB_SHA`` — merge-коммит, проверок на нём нет."""
    text = _WORKFLOW.read_text(encoding="utf-8")

    assert "PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in text
