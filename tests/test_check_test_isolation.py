"""Tests for scripts/check_test_isolation.py — абсолютные пути в argv тестов (issue #997).

Guard-the-guard: на реальном репозитории зелёный, а синтетический тест с
выдуманным путём делает его красным. Скрипт лежит в `scripts/` (не на sys.path)
— грузим по пути, тем же приёмом, что `test_check_web_imports.py`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_test_isolation.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_test_isolation", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_tests_dir(monkeypatch, tmp_path: Path, source: str, *, filler: bool = True) -> ModuleType:
    """Модуль guard'а, нацеленный на синтетический `tests/`.

    ``filler`` дописывает второй файл с заведомо законной командной строкой:
    без него набор из одного файла-без-argv упирается в проверку «guard потерял
    вход», и вердикт получается не о том, что проверяет тест.
    """
    module = _load_module()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(source, encoding="utf-8")
    if filler:
        (tests / "test_filler.py").write_text(
            'cli.main(["--mode", "1", "--file", "sol.py"])\n', encoding="utf-8"
        )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    monkeypatch.setattr(module, "_TESTS", tests)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main нарушений быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_test_isolation.py` завершается 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Сам предмет проверки: выдуманный абсолютный путь в командной строке
# ---------------------------------------------------------------------------


def test_posix_absolute_path_in_argv_is_flagged(monkeypatch, tmp_path: Path) -> None:
    """Регрессия прецедента: `--root /some/dir` создавал каталог на диске."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, 'cli.main(["--serve", "--root", "/some/dir"])\n'
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert len(errors) == 1
    assert "test_sample.py" in errors[0] and "/some/dir" in errors[0]


def test_windows_absolute_path_in_argv_is_flagged(monkeypatch, tmp_path: Path) -> None:
    """Тот же дефект в виндовой записи — `C:\\some\\dir`."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, 'cli.main(["--serve", "--root", "C:\\\\some\\\\dir"])\n'
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert len(errors) == 1


def test_home_relative_path_in_argv_is_flagged(monkeypatch, tmp_path: Path) -> None:
    """`~/x` адресует домашнюю папку разработчика — там уже гибли данные."""
    module = _fake_tests_dir(monkeypatch, tmp_path, 'cli.main(["--config", "~/grader.toml"])\n')
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert len(errors) == 1


def test_path_built_from_tmp_path_is_not_flagged(monkeypatch, tmp_path: Path) -> None:
    """Штатный способ: путь строится от tmp_path — литерала в списке нет."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, 'cli.main(["--mode", "1", "--file", str(tmp_path / "no.py")])\n'
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert errors == []


def test_relative_path_in_argv_is_not_flagged(monkeypatch, tmp_path: Path) -> None:
    """Относительный путь резолвится от cwd теста (обычно tmp) — не нарушение."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, 'cli.main(["--mode", "1", "--file", "sol.py"])\n'
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert errors == []


def test_url_in_argv_is_not_a_path(monkeypatch, tmp_path: Path) -> None:
    """Адрес со схемой — не путь файловой системы, ловить его нечего."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, 'main(["--url", "https://stepik.org/lesson/1/step/1"])\n'
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert errors == []


def test_list_without_flags_is_not_argv(monkeypatch, tmp_path: Path) -> None:
    """Список строк без флага — это данные, а не командная строка.

    Граница проверки: HTTP-пути (`client.get("/api/v1/runs")`) и наборы вроде
    `{"/etc/passwd", "/tmp"}` в тестах confinement выглядят как абсолютные
    пути, но никуда не исполняются.
    """
    module = _fake_tests_dir(monkeypatch, tmp_path, 'check_confined(["/etc/passwd", "/tmp/x"])\n')
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert errors == []


def test_argv_assembled_in_a_variable_is_out_of_scope(monkeypatch, tmp_path: Path) -> None:
    """Список, собранный по кускам, не проверяется — там argv чужих утилит.

    `bwrap --ro-bind /usr /usr` в sandbox-тестах монтирует существующий
    системный каталог только на чтение: абсолютный путь там неизбежен и
    безопасен.
    """
    module = _fake_tests_dir(
        monkeypatch,
        tmp_path,
        'argv = [bwrap, "--ro-bind", "/usr", "/usr"]\nargv += ["--tmpfs", "/tmp"]\n'
        "subprocess.run(argv, capture_output=True)\n",
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert errors == []


def test_keyword_argument_argv_is_checked(monkeypatch, tmp_path: Path) -> None:
    """`subprocess.run(args=[...])` — тот же argv, только именованным аргументом."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, 'subprocess.run(args=["grader", "--root", "/some/dir"])\n'
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Нулевой вход = ошибка (то же правило, что у guard'ов локалей и web-импортов)
# ---------------------------------------------------------------------------


def test_zero_test_files_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Каталог тестов переехал → guard падает, а не рапортует «всё чисто»."""
    module = _load_module()
    empty = tmp_path / "tests"
    empty.mkdir()
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    monkeypatch.setattr(module, "_TESTS", empty)

    errors: list[str] = []
    module.check_argv_paths(errors)
    assert any("guard потерял вход" in e for e in errors), errors


def test_zero_argv_lists_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Файлы есть, но ни одной командной строки — проверять тоже нечего."""
    module = _fake_tests_dir(
        monkeypatch, tmp_path, "def test_nothing() -> None:\n    assert True\n", filler=False
    )
    errors: list[str] = []
    module.check_argv_paths(errors)
    assert any("guard потерял вход" in e for e in errors), errors
