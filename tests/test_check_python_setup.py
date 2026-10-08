"""Тесты scripts/check_python_setup.py — python только после setup-python (issue #1570)."""

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_python_setup.py"

_SETUP = (
    '      - uses: actions/setup-python@abc # v7\n        with:\n          python-version: "3.14"\n'
)


def _workflow(*steps: str, container: str = "") -> str:
    return (
        "name: x\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        + container
        + ("    steps:\n" + "".join(steps))
    )


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_python_setup", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_before_setup_is_a_finding(gate: ModuleType) -> None:
    """Ровно случай `github-release`: заметки извлекались до установки Python."""
    text = _workflow(
        "      - name: notes\n        run: |\n          python scripts/x.py --out n.md\n",
        _SETUP,
    )

    found = gate.findings({"release.yml": text})

    assert any("работа build" in line for line in found)


def test_python_after_setup_passes(gate: ModuleType) -> None:
    text = _workflow(_SETUP, "      - run: pip install -e .\n", "      - run: pytest -q\n")

    assert gate.findings({"ci.yml": text}) == []


def test_a_python_image_brings_its_own_interpreter(gate: ModuleType) -> None:
    text = _workflow(
        "      - run: python -m pip install -e .\n",
        container="    container:\n      image: python:3.14-slim\n",
    )

    assert gate.findings({"ci.yml": text}) == []


def test_a_job_without_python_is_not_asked(gate: ModuleType) -> None:
    """`echo python` — не вызов: слово в тексте, а не команда."""
    text = _workflow('      - run: echo "no python here"\n')

    assert gate.findings({"ci.yml": text}) == []


def test_the_gate_rejects_through_main(gate: ModuleType, tmp_path: pathlib.Path) -> None:
    """Подделанный предмет через сам гейт — код находки, а не только список."""
    folder = tmp_path / ".github" / "workflows"
    folder.mkdir(parents=True)
    (folder / "x.yml").write_text(_workflow("      - run: python -V\n"), encoding="utf-8")

    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_FINDINGS


def test_no_workflows_is_a_broken_check(gate: ModuleType, tmp_path: pathlib.Path) -> None:
    """Пустой каталог — «не отработала», а не «чисто» (правило 075)."""
    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_BROKEN


def test_the_live_repository_is_clean(gate: ModuleType) -> None:
    assert gate.main([]) == gate.EXIT_OK
