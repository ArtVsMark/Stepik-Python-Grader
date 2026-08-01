"""Тесты scripts/corpus_run.py — раннер прогонного корпуса.

Скрипт грузится по пути (scripts/ не на sys.path), но перед загрузкой каталог
скриптов временно добавляется в путь поиска: `corpus_run` импортирует
`corpus_mutations` обычным import'ом, как при запуске из командной строки.

Тесты гоняют настоящие прогоны — в этом смысл стенда, — поэтому корпус-фикстура
намеренно крошечный (одна задача, один-два кейса), а мутации выбираются точечно.
Мутация `timeout` в тестах не используется: она честно ждёт таймаут.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS_DIR / "corpus_run.py"

_SOLUTION = "n = int(input())\nprint(n * 2)\n"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_corpus_run", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # См. test_corpus_mutations: dataclass резолвит аннотации через sys.modules.
    sys.modules[spec.name] = module
    added = str(_SCRIPTS_DIR) not in sys.path
    if added:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(str(_SCRIPTS_DIR))
    return module


_MODULE = _load_module()


def _make_task(
    corpus_dir: pathlib.Path,
    slug: str = "double",
    *,
    solution: str = _SOLUTION,
    cases: tuple[tuple[str, str], ...] = (("4\n", "8\n"),),
    meta: dict[str, object] | None = None,
) -> pathlib.Path:
    """Разложить в корпусе задачу-фикстуру и вернуть её директорию."""
    task_dir = corpus_dir / slug
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True)
    (task_dir / "task_1.py").write_text(solution, encoding="utf-8")
    for number, (stdin_text, expected_text) in enumerate(cases, start=1):
        (tests_dir / f"input_{number}.txt").write_text(stdin_text, encoding="utf-8")
        (tests_dir / f"expected_{number}.txt").write_text(expected_text, encoding="utf-8")
    if meta is not None:
        (task_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return task_dir


def test_discover_tasks_reads_meta(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path, meta={"title": "Удвоение"})

    tasks = _MODULE.discover_tasks(tmp_path)

    assert len(tasks) == 1
    assert tasks[0].slug == "double"
    assert tasks[0].title == "Удвоение"
    assert tasks[0].solution_path.name == "task_1.py"


def test_discover_tasks_falls_back_to_slug_without_meta(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path)

    assert _MODULE.discover_tasks(tmp_path)[0].title == "double"


def test_discover_tasks_skips_incomplete_directories(tmp_path: pathlib.Path) -> None:
    """Служебная папка рядом с задачами не должна выглядеть задачей."""
    _make_task(tmp_path)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "run.json").write_text("{}", encoding="utf-8")
    no_tests = tmp_path / "broken"
    no_tests.mkdir()
    (no_tests / "task_1.py").write_text(_SOLUTION, encoding="utf-8")

    assert [task.slug for task in _MODULE.discover_tasks(tmp_path)] == ["double"]


def test_discover_tasks_on_missing_dir_is_empty(tmp_path: pathlib.Path) -> None:
    assert _MODULE.discover_tasks(tmp_path / "нет-такой-папки") == []


def test_expected_lines_of_collects_all_cases(tmp_path: pathlib.Path) -> None:
    task_dir = _make_task(tmp_path, cases=(("4\n", "8\n"), ("5\n", "10\n")))

    assert _MODULE.expected_lines_of(task_dir / "tests") == ["8", "10"]


def test_run_verdict_accepts_correct_solution(tmp_path: pathlib.Path) -> None:
    task_dir = _make_task(tmp_path)

    verdict, detail = _MODULE.run_verdict(task_dir / "task_1.py", task_dir / "tests")

    assert verdict == "AC"
    assert "1" in detail


def test_run_verdict_reports_first_failing_case(tmp_path: pathlib.Path) -> None:
    """Вердикт прогона — вердикт первого непрошедшего кейса, а не последнего."""
    task_dir = _make_task(
        tmp_path,
        solution="n = int(input())\nprint(n * 2)\n",
        cases=(("4\n", "8\n"), ("5\n", "999\n"), ("6\n", "888\n")),
    )

    verdict, detail = _MODULE.run_verdict(task_dir / "task_1.py", task_dir / "tests")

    assert verdict == "WA"
    assert "кейс 2" in detail


def test_check_task_runs_baseline_and_selected_mutations(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path)
    task = _MODULE.discover_tasks(tmp_path)[0]
    mutations = [_MODULE.mutation_by_key("extra_line"), _MODULE.mutation_by_key("no_output")]

    outcomes = _MODULE.check_task(task, mutations=[m for m in mutations if m is not None])

    assert [o.check for o in outcomes] == [_MODULE.BASELINE, "extra_line", "no_output"]
    assert all(o.matched for o in outcomes)


def test_check_task_leaves_corpus_untouched(tmp_path: pathlib.Path) -> None:
    """Мутант живёт во временной папке — эталон на диске не портится."""
    task_dir = _make_task(tmp_path)
    mutation = _MODULE.mutation_by_key("extra_line")
    assert mutation is not None

    _MODULE.check_task(_MODULE.discover_tasks(tmp_path)[0], mutations=[mutation])

    assert (task_dir / "task_1.py").read_text(encoding="utf-8") == _SOLUTION


def test_check_task_skips_inapplicable_mutations(tmp_path: pathlib.Path) -> None:
    """Числовому ответу не назначается порча регистра — иначе ожидание ложно."""
    _make_task(tmp_path)
    mutation = _MODULE.mutation_by_key("upper_case")
    assert mutation is not None

    outcomes = _MODULE.check_task(_MODULE.discover_tasks(tmp_path)[0], mutations=[mutation])

    assert [o.check for o in outcomes] == [_MODULE.BASELINE]


def test_check_task_flags_mismatch_on_broken_baseline(tmp_path: pathlib.Path) -> None:
    """Неверный эталон — расхождение на строке baseline, а не «падение стенда»."""
    _make_task(tmp_path, solution="print(0)\n")

    outcomes = _MODULE.check_task(_MODULE.discover_tasks(tmp_path)[0], mutations=[])

    assert len(outcomes) == 1
    assert not outcomes[0].matched
    assert outcomes[0].expected == "AC"
    assert outcomes[0].actual == "WA"


def test_outcome_as_dict_is_json_ready() -> None:
    outcome = _MODULE.CheckOutcome(
        task="double", check="extra_line", expected="WA", actual="AC", detail="…"
    )

    payload = outcome.as_dict()

    assert payload["matched"] is False
    assert json.loads(json.dumps(payload, ensure_ascii=False))["task"] == "double"


def test_select_mutations_rejects_unknown_key() -> None:
    with pytest.raises(SystemExit, match="Неизвестная мутация"):
        _MODULE._select_mutations(["нет-такой"])


def test_select_mutations_defaults_to_full_catalog() -> None:
    assert len(_MODULE._select_mutations([])) == len(_MODULE.MUTATIONS)


def test_select_tasks_rejects_unknown_slug(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path)
    tasks = _MODULE.discover_tasks(tmp_path)

    with pytest.raises(SystemExit, match="Неизвестная задача"):
        _MODULE._select_tasks(tasks, ["нет-такой"])


def test_main_reports_json_and_exits_zero_on_match(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_task(tmp_path)

    code = _MODULE.main(["--corpus", str(tmp_path), "--mutation", "extra_line", "--output", "json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["mismatched"] == 0
    assert {row["check"] for row in payload["checks"]} == {_MODULE.BASELINE, "extra_line"}


def test_main_exits_nonzero_on_mismatch(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_task(tmp_path, solution="print(0)\n")

    code = _MODULE.main(["--corpus", str(tmp_path), "--mutation", "extra_line"])

    assert code == 1
    assert "расхождений: 1" in capsys.readouterr().out


def test_main_report_flag_keeps_exit_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--report печатает те же расхождения, но не валит прогон."""
    _make_task(tmp_path, solution="print(0)\n")

    code = _MODULE.main(["--corpus", str(tmp_path), "--mutation", "extra_line", "--report"])

    assert code == 0
    assert "расхождений: 1" in capsys.readouterr().out


def test_main_on_empty_corpus_fails_loudly(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _MODULE.main(["--corpus", str(tmp_path)])

    assert code == 1
    assert "Корпус пуст" in capsys.readouterr().err
