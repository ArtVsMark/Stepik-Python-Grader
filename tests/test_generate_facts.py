"""Тесты scripts/generate_facts.py — факты проекта машиночитаемо, для соседей.

Витрина берёт наши числа у себя: клонирует репозиторий целиком ради двух
подсчётов по ``tests/``, разбирает наш ``ci.yml`` регулярным выражением и
оценивает число проверок медианой по семи последним PR. Знание о нашем
устройстве живёт при этом в чужом репозитории — перенесём каталог, и у соседа
молча изменится число, а не сломается сборка.

Здесь проверяется обратный приём: считает издатель, читает потребитель. И
отдельно — что «не измеряли» не притворяется нулём.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "generate_facts.py"


@pytest.fixture
def facts() -> ModuleType:
    """Свежий модуль на каждый тест."""
    spec = importlib.util.spec_from_file_location("_generate_facts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(root: pathlib.Path, *, matrix: str = "") -> pathlib.Path:
    """Дерево проекта: несколько тестов и матрица прогона."""
    tests = root / "tests"
    (tests / "e2e").mkdir(parents=True)
    (tests / "test_один.py").write_text(
        "def test_a():\n    pass\n\nasync def test_b():\n    pass\n", encoding="utf-8"
    )
    (tests / "e2e" / "test_два.py").write_text("def test_c():\n    pass\n", encoding="utf-8")
    (tests / "conftest.py").write_text("def test_не_модуль():\n    pass\n", encoding="utf-8")
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        matrix
        or (
            '        os: ["ubuntu-latest"]\n'
            '        python-version: ["3.12", "3.13"]\n'
            "        include:\n"
            '          - {os: "ubuntu-latest", python-version: "3.14", experimental: true}\n'
        ),
        encoding="utf-8",
    )
    return root


def test_test_functions_are_counted_across_the_whole_tree(
    facts: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Считаются и вложенные, и async — но conftest модулем не является."""
    root = _project(tmp_path)

    assert facts.count_test_functions(root) == 4
    assert facts.count_test_modules(root) == 2


def test_matrix_versions_are_split_by_experimental(
    facts: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Экспериментальных нет в правилах ветки — единственный источник матрица."""
    root = _project(tmp_path)

    assert facts.python_versions(root) == {
        "supported": ["3.12", "3.13"],
        "experimental": ["3.14"],
    }


def test_an_unmeasured_key_is_absent_not_zero(
    facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Спросить площадку не удалось — ключа нет вовсе.

    Ноль читался бы как «проверок на PR не создаётся», то есть точной ложью.
    Тот же приём, что у `portable` в контракте каталога правил: ключа нет —
    значит не отвечали.
    """
    root = _project(tmp_path)
    monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)

    built = facts.build_facts(root)

    assert "checks_per_pr" not in built


def test_the_schema_says_what_it_versions(facts: ModuleType, tmp_path: pathlib.Path) -> None:
    """Номер обязан называть, ЧЕГО он версия.

    Номера разного назначения, названные одним словом, разъезжаются по чужим
    полям: сосед по каталогу правил записал версию формата выгрузки в поле
    версии ответа потребителя, и обе стороны остались формально валидными.
    """
    root = _project(tmp_path)

    built = facts.build_facts(root)

    assert built["schema"] == facts.SCHEMA
    assert "ФОРМАТА" in str(built["_"]), "файл не говорит, чего его номер версия"


def test_the_file_is_written_as_utf8_json(
    facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Потребитель читает JSON, а не текст: кириллица не должна экранироваться."""
    root = _project(tmp_path)
    monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)
    out = tmp_path / "out" / "facts.json"

    assert facts.main(["--out", str(out), "--root", str(root)]) == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tests"] == {"functions": 4, "modules": 2}
    assert "\\u" not in out.read_text(encoding="utf-8")
