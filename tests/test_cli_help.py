"""Тесты пользовательской справки CLI — usage и чистота текста (issue #820).

`--help` — первая (а для части пользователей и единственная) прочитанная
поверхность инструмента. Проверяем два её свойства: usage называет команду,
которую действительно можно набрать, и текст не выглядит выпиской из трекера.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout

import pytest

from stepik_grader import cli
from stepik_grader.cli.options import _build_arg_parser, _program_name

# Ссылка на задачу: "#123". Внутренние якоря/шебанги в справке не встречаются,
# поэтому достаточно голого номера.
_ISSUE_RE = re.compile(r"#\d+")


@pytest.mark.parametrize(
    ("argv0", "expected"),
    [
        ("/usr/local/bin/stepik-grader", "stepik-grader"),
        # Windows кладёт рядом .exe-обёртку; путь берём голым именем, потому что
        # разделитель "\" на POSIX не разбирается и тест стал бы ОС-зависимым.
        ("stepik-grader.exe", "stepik-grader"),
        ("/usr/local/bin/stepik-grader-gui", "stepik-grader-gui"),
        ("/repo/src/stepik_grader/__main__.py", "python -m stepik_grader"),
        ("/repo/.venv/bin/pytest", "python -m stepik_grader"),
        ("", "python -m stepik_grader"),
    ],
)
def test_program_name_follows_actual_entry_point(monkeypatch, argv0: str, expected: str) -> None:
    """Имя в usage — то, чем пользователь запустил, а не файл модуля."""
    monkeypatch.setattr("sys.argv", [argv0, "--help"])
    assert _program_name() == expected


def test_usage_does_not_name_nonexistent_grader_py(monkeypatch) -> None:
    """При src-layout файла grader.py нет — usage не должен его предлагать."""
    monkeypatch.setattr("sys.argv", ["/repo/src/stepik_grader/__main__.py"])
    usage = _build_arg_parser().format_usage()
    assert "grader.py" not in usage
    assert usage.startswith("usage: python -m stepik_grader")


def test_help_has_no_issue_references(monkeypatch) -> None:
    """В справке нет номеров issue/эпиков — они ничего не говорят пользователю."""
    monkeypatch.setattr("sys.argv", ["stepik-grader"])
    found = _ISSUE_RE.findall(_build_arg_parser().format_help())
    assert found == [], f"справка содержит ссылки на задачи: {sorted(set(found))}"


def test_help_lists_both_working_launch_forms(monkeypatch) -> None:
    """Эпилог показывает обе рабочие формы запуска, а не одну."""
    monkeypatch.setattr("sys.argv", ["stepik-grader"])
    help_text = _build_arg_parser().format_help()
    assert "stepik-grader --mode 1 --file task.py" in help_text
    assert "python -m stepik_grader --mode 1 --file task.py" in help_text


def test_version_names_real_command() -> None:
    """`--version` печатает имя дистрибутива, а не несуществующий grader.py."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        cli.main(["--version"])
    out = buffer.getvalue()
    assert out.startswith("stepik-grader ")
    assert "grader.py" not in out
