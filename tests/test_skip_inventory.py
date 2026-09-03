"""Тесты scripts/skip_inventory.py — инвентарь пропусков набора (issue #837).

Скрипт лежит в scripts/ (не на sys.path) — грузим его по пути, тем же приёмом,
что и test_check_docs_guardrails.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "skip_inventory.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_skip_inventory", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Регистрируем ДО exec_module: @dataclass резолвит аннотации через
    # sys.modules[cls.__module__], и без записи падает AttributeError на None.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, source: str) -> Path:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_sample.py").write_text(source, encoding="utf-8")
    return tests_dir


def test_current_suite_has_no_unexplained_skips() -> None:
    """Главный инвариант: каждый пропуск в наборе объясняет себя причиной.

    Пропуск без причины неотличим от забытого теста, а «зелёный прогон» из
    таких пропусков — ложная уверенность.
    """
    assert _load_module().main([]) == 0


def test_cli_exits_zero() -> None:
    """`python scripts/skip_inventory.py` завершается 0 на текущем наборе."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_report_counts_every_kind(tmp_path: Path) -> None:
    """Все четыре вида пропуска попадают в инвентарь."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        '@pytest.mark.skip(reason="почему-то")\n'
        "def test_a(): pass\n"
        '@pytest.mark.skipif(True, reason="условие")\n'
        "def test_b(): pass\n"
        '@pytest.mark.xfail(reason="известный дефект")\n'
        "def test_c(): pass\n"
        "def test_d():\n"
        '    pytest.importorskip("hypothesis")\n',
    )
    kinds = {site.kind for site in module.collect_skip_sites(tests_dir)}
    assert kinds == {"skip", "skipif", "xfail", "importorskip"}


def test_reason_is_read_from_positional_and_keyword(tmp_path: Path) -> None:
    """Причина читается и как `reason=`, и как позиционный аргумент."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        '@pytest.mark.skip("позиционная")\n'
        "def test_a(): pass\n"
        "def test_b():\n"
        '    pytest.skip(reason="именованная")\n',
    )
    reasons = {site.reason for site in module.collect_skip_sites(tests_dir)}
    assert reasons == {"позиционная", "именованная"}


def test_fstring_reason_counts_as_explained(tmp_path: Path) -> None:
    """f-строка — настоящая причина, а не «пропуск без объяснения»."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\nNAME = 'X'\ndef test_a():\n    pytest.skip(f'нужен {NAME}')\n",
    )
    (site,) = module.collect_skip_sites(tests_dir)
    assert site.has_reason
    assert site.reason == "нужен {…}"


def test_skip_without_reason_fails_the_gate(tmp_path: Path, monkeypatch, capsys) -> None:
    """Пропуск без причины делает выход ненулевым — это и есть гейт."""
    module = _load_module()
    tests_dir = _write(tmp_path, "import pytest\n@pytest.mark.skip\ndef test_a(): pass\n")
    monkeypatch.setattr(module, "_TESTS_DIR", tests_dir)
    monkeypatch.setattr(module, "_ROOT", tmp_path)

    assert module.main([]) == 1
    assert "без причины" in capsys.readouterr().out


def test_unrelated_marks_are_not_counted(tmp_path: Path) -> None:
    """Чужие маркеры и одноимённые функции не попадают в инвентарь."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        "@pytest.mark.parametrize('x', [1])\n"
        "def test_a(x): pass\n"
        "def skip(reason): pass\n"
        "def test_b():\n"
        "    skip('это не pytest.skip')\n",
    )
    assert module.collect_skip_sites(tests_dir) == []


def test_summary_lists_kinds(tmp_path: Path) -> None:
    """Сводка перечисляет виды пропусков — по ней видно, чего в наборе больше."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n@pytest.mark.skipif(True, reason='ОС')\ndef test_a(): pass\n",
    )
    report = module.render_report(module.collect_skip_sites(tests_dir), summary_only=True)
    assert "Всего мест пропуска: 1" in report
    assert "skipif 1" in report


def test_module_level_pytestmark_is_seen(tmp_path: Path) -> None:
    """Пропуск ЦЕЛОГО файла виден инвентарю (issue #921, находка `QA-2-01`).

    Это не декоратор и не вызов `pytest.skip`, а обычное присваивание, поэтому
    обход по декораторам его не находил: файл, отключённый целиком, вовсе не
    попадал в отчёт. Гейт «каждый пропуск объясняет себя» молчал именно про
    самые дорогие пропуски.
    """
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        'pytestmark = pytest.mark.skipif(True, reason="только Windows")\n'
        "def test_a(): pass\n",
    )

    sites = module.collect_skip_sites(tests_dir)

    assert [(site.kind, site.reason) for site in sites] == [("skipif", "только Windows")]


def test_pytestmark_list_form_is_expanded(tmp_path: Path) -> None:
    """`pytestmark = [mark_a, mark_b]` — законная форма, считаем оба."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        "pytestmark = [\n"
        '    pytest.mark.skipif(True, reason="первая"),\n'
        '    pytest.mark.xfail(reason="вторая"),\n'
        "]\n"
        "def test_a(): pass\n",
    )

    kinds = {site.kind for site in module.collect_skip_sites(tests_dir)}

    assert kinds == {"skipif", "xfail"}


def test_pytestmark_without_reason_fails_the_gate(tmp_path: Path) -> None:
    """Критерий приёмки #921: guard обязан краснеть на сломанном входе."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\npytestmark = pytest.mark.skipif(True)\ndef test_a(): pass\n",
    )

    sites = module.collect_skip_sites(tests_dir)

    assert sites and not any(site.has_reason for site in sites)


def test_class_level_pytestmark_is_seen(tmp_path: Path) -> None:
    """Внутри класса `pytestmark` отключает все его тесты — тоже пропуск."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        "class TestX:\n"
        '    pytestmark = pytest.mark.skip(reason="весь класс")\n'
        "    def test_a(self): pass\n",
    )

    assert [site.reason for site in module.collect_skip_sites(tests_dir)] == ["весь класс"]


def test_other_assignments_are_not_mistaken_for_skips(tmp_path: Path) -> None:
    """Однофамильцы не считаются: иначе отчёт зарос бы шумом."""
    module = _load_module()
    tests_dir = _write(
        tmp_path,
        "import pytest\n"
        'timeout = pytest.mark.skipif(True, reason="не pytestmark")\n'
        'pytestmark = pytest.mark.parametrize("x", [1])\n'
        "def test_a(x): pass\n",
    )

    assert module.collect_skip_sites(tests_dir) == []
