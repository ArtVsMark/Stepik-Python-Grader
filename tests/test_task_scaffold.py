"""Тесты `--init-task` — каталог задачи без Stepik (issue #1072, находка STR-3-05).

Обещание команды одно и очень конкретное: человек без аккаунта Stepik, без
OAuth-приложения и без сети получает каталог, который **сразу проверяется**.
Поэтому главный тест здесь не «создались ли три файла», а прогон грейдера по
созданному каталогу настоящей командой: пустой шаблон прошёл бы любую проверку
на наличие файлов и провалил бы первое, ради чего команда написана.

Прогон идёт отдельным процессом, а не вызовом ``cli.main`` в этом же: каталог
проверяется ровно так, как его будет проверять человек, — строкой из подсказки.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from stepik_grader import cli
from stepik_grader.core.task_scaffold import SOLUTION_NAME, create_task_dir


def test_creates_the_documented_layout(tmp_path: pathlib.Path) -> None:
    """Раскладка ровно та, что описана контрактом в `docs/use/configuration.md`."""
    target = tmp_path / "my-task"

    solution = create_task_dir(target)

    assert solution == target / SOLUTION_NAME
    assert solution.is_file()
    assert (target / "tests" / "1").is_file()
    assert (target / "tests" / "1.clue").is_file()


def test_meta_json_is_not_created(tmp_path: pathlib.Path) -> None:
    """`meta.json` опционален, и пустой только сбивает с толку.

    Читатель начинает искать, что там должно быть, — а нужного ему поля
    (`function_name`) в пустом файле нет.
    """
    target = tmp_path / "my-task"

    create_task_dir(target)

    assert not (target / "meta.json").exists()


def test_an_existing_directory_is_never_overwritten(tmp_path: pathlib.Path) -> None:
    """Чужая работа не затирается молча — как и у `--init-vscode`."""
    target = tmp_path / "my-task"
    target.mkdir()
    kept = target / "решение.py"
    kept.write_text("важное\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_task_dir(target)

    assert kept.read_text(encoding="utf-8") == "важное\n"


def test_the_created_task_passes_mode_1_unedited(tmp_path: pathlib.Path) -> None:
    """Созданная задача проходит проверку БЕЗ единой правки.

    Ради этого шаблон решения рабочий, а не пустой: первое, что увидел бы
    человек, — красный прогон на том, что ему только что создали, и разбирать
    он стал бы собственную установку.
    """
    target = tmp_path / "my-task"
    solution = create_task_dir(target)

    result = subprocess.run(
        [sys.executable, "-m", "stepik_grader.grader", "--mode", "1", "--file", str(solution)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "AC" in result.stdout


def test_the_cli_reports_the_path_and_the_next_step(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вывод называет и что создано, и чем это проверить.

    «Создал и молчи» оставляет человека там же, откуда он пришёл: каталог есть,
    следующего шага не видно.
    """
    target = tmp_path / "my-task"

    assert cli.main(["--init-task", str(target)]) == 0

    out = capsys.readouterr().out
    assert str(target) in out
    assert "--mode 1" in out


def test_the_cli_refuses_an_existing_directory_with_a_nonzero_code(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отказ уходит ненулевым кодом: «ничего не сделал» обязано отличаться и для скрипта."""
    target = tmp_path / "my-task"
    target.mkdir()

    assert cli.main(["--init-task", str(target)]) != 0
    assert str(target) in capsys.readouterr().out


def test_the_hint_names_the_function_style_key(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Для function-style задачи хватает одной строки — она и названа.

    Иначе `meta.json`, которого нет, пришлось бы искать в документации, зная,
    что он вообще бывает.
    """
    cli.main(["--init-task", str(tmp_path / "my-task")])

    assert "function_name" in capsys.readouterr().out
