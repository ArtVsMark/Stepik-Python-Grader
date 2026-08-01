"""Тесты scripts/corpus_sweep.py — сквозной прогон подсистем по локальной базе.

Скрипт грузится по пути (scripts/ не на sys.path), каталог скриптов временно
добавляется в путь поиска — `corpus_sweep` импортирует соседние `corpus_run` и
`corpus_mutations` обычным import'ом, как при запуске из командной строки.

База-фикстура намеренно крошечная, а мутации в прогонах выбираются точечно:
полный каталог включает `timeout`, который честно ждёт таймаут на каждом кейсе.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS_DIR / "corpus_sweep.py"

_SOLUTION = "n = int(input())\nprint(n * 2)\n"
_WRONG = "input()\nprint(0)\n"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_corpus_sweep", _SCRIPT)
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
    base: pathlib.Path,
    slug: str = "Module_1/step_1",
    *,
    solutions: dict[str, str] | None = None,
    cases: tuple[tuple[str, str], ...] | None = (("4\n", "8\n"),),
    meta: dict[str, object] | None = None,
) -> pathlib.Path:
    """Разложить задачу-фикстуру в раскладке downloader'а."""
    task_dir = base / slug
    task_dir.mkdir(parents=True)

    files = {"task_1.py": _SOLUTION} if solutions is None else solutions
    for name, code in files.items():
        (task_dir / name).write_text(code, encoding="utf-8")

    if cases is not None:
        tests_dir = task_dir / "tests"
        tests_dir.mkdir()
        for number, (stdin_text, expected_text) in enumerate(cases, start=1):
            (tests_dir / f"input_{number}.txt").write_text(stdin_text, encoding="utf-8")
            (tests_dir / f"expected_{number}.txt").write_text(expected_text, encoding="utf-8")

    if meta is not None:
        (task_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return task_dir


def _mutations(*keys: str) -> list[object]:
    """Выбрать мутации по ключам (в тестах — только дешёвые)."""
    selected = [_MODULE.MUTATIONS[0].__class__ and m for m in _MODULE.MUTATIONS if m.key in keys]
    assert len(selected) == len(keys)
    return selected


def test_discover_base_finds_nested_tasks(tmp_path: pathlib.Path) -> None:
    """База обходится рекурсивно — задачи лежат внутри модулей и уроков."""
    _make_task(tmp_path, "Module_2/Module_2.1/step_5")
    _make_task(tmp_path, "Module_2/Module_2.1/step_6")

    tasks = _MODULE.discover_base(tmp_path)

    assert [t.slug for t in tasks] == [
        str(pathlib.Path("Module_2/Module_2.1/step_5")),
        str(pathlib.Path("Module_2/Module_2.1/step_6")),
    ]
    assert all(t.runnable for t in tasks)


def test_discover_base_does_not_treat_tests_dir_as_task(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path)

    assert [t.task_dir.name for t in _MODULE.discover_base(tmp_path)] == ["step_1"]


def test_discover_base_keeps_incomplete_tasks_visible(tmp_path: pathlib.Path) -> None:
    """Задача без тестов и задача без решения попадают в инвентарь, а не теряются."""
    _make_task(tmp_path, "ok/step_1")
    _make_task(tmp_path, "no_tests/step_2", cases=None)
    _make_task(tmp_path, "no_solution/step_3", solutions={})

    tasks = {t.slug.replace("\\", "/"): t for t in _MODULE.discover_base(tmp_path)}

    assert tasks["no_tests/step_2"].test_dir is None
    assert not tasks["no_tests/step_2"].runnable
    assert tasks["no_solution/step_3"].solutions == ()
    assert not tasks["no_solution/step_3"].runnable
    assert tasks["ok/step_1"].runnable


def test_discover_base_reads_meta_and_survives_broken_json(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path, "good/step_1", meta={"title": "Удвоение"})
    broken = _make_task(tmp_path, "bad/step_2")
    (broken / "meta.json").write_text("{не json", encoding="utf-8")

    tasks = {t.slug.replace("\\", "/"): t for t in _MODULE.discover_base(tmp_path)}

    assert tasks["good/step_1"].meta["title"] == "Удвоение"
    assert tasks["bad/step_2"].meta == {}  # битый meta.json не роняет обход


def test_discover_base_on_missing_dir_is_empty(tmp_path: pathlib.Path) -> None:
    assert _MODULE.discover_base(tmp_path / "нет-базы") == []


def test_solution_py_wins_as_reference_candidate(tmp_path: pathlib.Path) -> None:
    """`solution.py` (принятый Stepik код) идёт первым кандидатом в эталоны."""
    _make_task(
        tmp_path,
        solutions={"task_1.py": _SOLUTION, "solution.py": _SOLUTION, "task_2.py": _SOLUTION},
    )

    task = _MODULE.discover_base(tmp_path)[0]

    assert task.solutions[0].name == "solution.py"


def test_pick_reference_returns_passing_solution(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path, solutions={"task_1.py": _WRONG, "task_2.py": _SOLUTION})
    task = _MODULE.discover_base(tmp_path)[0]

    reference, note = _MODULE.pick_reference(task, timeout=5.0)

    assert reference is not None
    assert reference.name == "task_2.py"
    assert note == "task_2.py"


def test_pick_reference_reports_when_nothing_passes(tmp_path: pathlib.Path) -> None:
    """Ни одно решение не проходит — это находка «нет эталона», а не падение."""
    _make_task(tmp_path, solutions={"task_1.py": _WRONG})
    task = _MODULE.discover_base(tmp_path)[0]

    reference, note = _MODULE.pick_reference(task, timeout=5.0)

    assert reference is None
    assert "task_1.py: WA" in note


def test_pick_reference_without_tests(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path, cases=None)
    task = _MODULE.discover_base(tmp_path)[0]

    assert _MODULE.pick_reference(task)[0] is None


def test_sweep_core_is_quiet_on_healthy_task(tmp_path: pathlib.Path) -> None:
    _make_task(tmp_path)
    task = _MODULE.discover_base(tmp_path)[0]
    reference = task.solutions[0]

    findings = _MODULE.sweep_core(
        task, reference, timeout=5.0, mutations=_mutations("extra_line", "no_output")
    )

    assert findings == []


def test_sweep_core_reports_mismatch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вердикт, разошедшийся с ожиданием мутации, попадает в находки."""
    _make_task(tmp_path)
    task = _MODULE.discover_base(tmp_path)[0]
    reference = task.solutions[0]
    monkeypatch.setattr(_MODULE, "run_verdict", lambda *a, **k: ("AC", "подделка"))

    findings = _MODULE.sweep_core(task, reference, mutations=_mutations("extra_line"))

    assert len(findings) == 1
    assert findings[0].module == "core"
    assert findings[0].kind == "mismatch"
    assert "ожидался WA, получен AC" in findings[0].detail


def test_sweep_core_leaves_base_untouched(tmp_path: pathlib.Path) -> None:
    """Мутанты живут во временных файлах — база задач не портится."""
    task_dir = _make_task(tmp_path)
    task = _MODULE.discover_base(tmp_path)[0]

    _MODULE.sweep_core(task, task.solutions[0], timeout=5.0, mutations=_mutations("extra_line"))

    assert (task_dir / "task_1.py").read_text(encoding="utf-8") == _SOLUTION
    assert sorted(p.name for p in task_dir.iterdir()) == ["task_1.py", "tests"]


def test_sweep_learning_writes_history_and_builds_cards(tmp_path: pathlib.Path) -> None:
    """История пишется в рабочую БД прогона, из неё собираются карточки."""
    base = tmp_path / "base"
    base.mkdir()
    _make_task(base)
    task = _MODULE.discover_base(base)[0]
    db_path = tmp_path / "work" / "history.db"
    db_path.parent.mkdir()

    findings = _MODULE.sweep_learning(task, task.solutions[0], db_path=db_path, timeout=5.0)

    assert findings == []
    assert db_path.exists()


def test_sweep_learning_does_not_touch_user_history(tmp_path: pathlib.Path) -> None:
    """Рядом с задачами не появляется пользовательская база истории."""
    base = tmp_path / "base"
    base.mkdir()
    task_dir = _make_task(base)
    task = _MODULE.discover_base(base)[0]

    _MODULE.sweep_learning(task, task.solutions[0], db_path=tmp_path / "work.db", timeout=5.0)

    assert not (task_dir / ".grader_history.db").exists()
    assert not (base / ".grader_history.db").exists()


def test_finding_as_dict_is_json_ready() -> None:
    finding = _MODULE.Finding("step_1", "core", "mismatch", "подробность")

    payload = finding.as_dict()

    assert json.loads(json.dumps(payload, ensure_ascii=False))["module"] == "core"


def test_report_counts_checks_and_groups_findings() -> None:
    report = _MODULE.SweepReport()

    report.record("core", _MODULE.Finding("a", "core", "error", "x"))
    report.record("core")
    report.record("extras")

    assert report.checked["core"] == 2
    assert report.checked["extras"] == 1
    assert len(report.by_module("core")) == 1
    assert report.by_module("extras") == []


def test_parse_modules_accepts_all_and_subset() -> None:
    assert _MODULE._parse_modules("all") == list(_MODULE.MODULE_KEYS)
    assert _MODULE._parse_modules("core,extras") == ["core", "extras"]


def test_parse_modules_rejects_unknown() -> None:
    with pytest.raises(SystemExit, match="Неизвестные группы"):
        _MODULE._parse_modules("core,нет-такой")


def test_main_inventory_reports_counts(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_task(tmp_path, "ok/step_1")
    _make_task(tmp_path, "no_tests/step_2", cases=None)

    code = _MODULE.main(["--base", str(tmp_path), "--inventory"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Задач в базе: 2" in out
    assert "готовы к прогону (есть тесты и решение): 1" in out
    assert "без тестов: 1" in out


def test_main_on_missing_base_fails(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _MODULE.main(["--base", str(tmp_path / "нет"), "--inventory"])

    assert code == 1
    assert "База пуста или не найдена" in capsys.readouterr().err


def test_main_core_only_run_is_clean(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_task(tmp_path)

    code = _MODULE.main(
        ["--base", str(tmp_path), "--modules", "core", "--timeout", "5", "--output", "json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["tasks_run"] == 1
    assert payload["findings"] == []
    assert payload["checked"]["core"] == 1


def test_main_reports_task_without_reference(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Задача, где ни одно решение не проходит, даёт находку и ненулевой exit."""
    _make_task(tmp_path, solutions={"task_1.py": _WRONG})

    code = _MODULE.main(
        ["--base", str(tmp_path), "--modules", "core", "--timeout", "5", "--output", "json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["findings"][0]["kind"] == "missing"
    assert "нет эталона" in payload["findings"][0]["detail"]


def test_main_report_flag_keeps_exit_zero(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_task(tmp_path, solutions={"task_1.py": _WRONG})

    code = _MODULE.main(
        ["--base", str(tmp_path), "--modules", "core", "--timeout", "5", "--report"]
    )

    assert code == 0
    assert "нет эталона" in capsys.readouterr().out


def test_main_limit_caps_number_of_tasks(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for number in range(3):
        _make_task(tmp_path, f"Module_1/step_{number}")

    code = _MODULE.main(
        [
            "--base",
            str(tmp_path),
            "--modules",
            "core",
            "--limit",
            "1",
            "--timeout",
            "5",
            "--output",
            "json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["tasks_total"] == 3
    assert payload["tasks_run"] == 1
