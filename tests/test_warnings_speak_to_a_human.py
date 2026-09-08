"""Предупреждения печатаются человеку, а не как трассировка (issue #1466).

Загрузчик набора сообщает о неполном наборе через ``warnings.warn``, и это не
недосмотр: ``warnings`` здесь ещё и **транспорт** — ядро ловит их и кладёт в
машиночитаемый ключ ``warnings`` вывода ``--output json``, чтобы CI отличал
полный прогон от урезанного (#935).

Менять надо показ, а не транспорт, поэтому проверок две и они разные: что
видит человек и что уходит в json. Совпадение этих двух ответов и было
дефектом — пользователю доставался машинный формат.
"""

from __future__ import annotations

import json
import pathlib
import warnings
from typing import Any

import pytest

from stepik_grader.cli import main
from stepik_grader.cli.rendering import human_warnings

#: Кусок текста предупреждения загрузчика — общий для обеих проверок.
_UNPAIRED = "файлы без пары пропущены"


def _a_file_inside_the_package() -> pathlib.Path:
    """Любой файл пакета — по нему `human_warnings` узнаёт своё предупреждение."""
    import stepik_grader

    return pathlib.Path(stepik_grader.__file__)


@pytest.fixture
def task_with_an_unpaired_case(tmp_path: pathlib.Path) -> pathlib.Path:
    """Решение и набор, где у второго кейса нет ``.clue`` — как в задаче."""
    solution = tmp_path / "task1_1.py"
    solution.write_text("a, b = map(int, input().split())\nprint(a + b)\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("2 3\n", encoding="utf-8")
    (tests / "1.clue").write_text("5\n", encoding="utf-8")
    (tests / "2").write_text("4 5\n", encoding="utf-8")  # пары нет — отсюда предупреждение
    return solution


class TestWhatTheHumanSees:
    """Одна строка по делу вместо четырёх строк про наши внутренности."""

    def test_the_text_reaches_the_user(
        self, task_with_an_unpaired_case: pathlib.Path, capsys: Any
    ) -> None:
        main(["--mode", "1", "--file", str(task_with_an_unpaired_case)])

        assert _UNPAIRED in capsys.readouterr().err

    def test_our_internals_are_not_shown(
        self, task_with_an_unpaired_case: pathlib.Path, capsys: Any
    ) -> None:
        """Ни пути в ``src/``, ни номера строки, ни имени категории.

        Ровно это и читалось как «сломался грейдер»: человек приходит сюда,
        уже не понимая, что происходит, и получает путь в чужие исходники.
        """
        main(["--mode", "1", "--file", str(task_with_an_unpaired_case)])

        err = capsys.readouterr().err
        line = next(line for line in err.splitlines() if _UNPAIRED in line)
        assert "UserWarning" not in line
        assert "grader_core.py" not in line
        assert "src/stepik_grader" not in line
        assert line.lstrip().startswith("⚠️")


class TestWhatTheMachineGets:
    """Транспорт не тронут: на ключ `warnings` завязан CI."""

    def test_json_still_carries_the_warning(
        self, task_with_an_unpaired_case: pathlib.Path, capsys: Any
    ) -> None:
        main(
            [
                "--mode",
                "1",
                "--file",
                str(task_with_an_unpaired_case),
                "--output",
                "json",
            ]
        )

        payload = json.loads(capsys.readouterr().out)

        assert any(_UNPAIRED in w for w in payload["warnings"])


class TestTheOverrideIsPolite:
    """Перекрытие глобальное, поэтому границы у него жёсткие."""

    def test_foreign_warnings_go_to_the_previous_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Чужое предупреждение уходит прежнему обработчику, как раньше.

        ``DeprecationWarning`` из сторонней библиотеки без файла и строки
        диагностировать нечем, а выдавать его за наше обращение к
        пользователю — прямая ложь об источнике.

        Проверяется делегирование, а не stderr: под pytest предупреждения
        забирает его собственный обработчик, и проверка «что напечаталось»
        мерила бы pytest, а не нас.
        """
        seen: list[tuple[Any, ...]] = []
        monkeypatch.setattr(warnings, "showwarning", lambda *a: seen.append(a))

        with human_warnings():
            warnings.showwarning(
                DeprecationWarning("устарело"), DeprecationWarning, "/чужая/lib.py", 42
            )

        assert len(seen) == 1
        assert seen[0][2] == "/чужая/lib.py"

    def test_a_user_warning_from_elsewhere_is_not_ours_either(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Признак «наше» — файл внутри пакета, а не только категория."""
        seen: list[tuple[Any, ...]] = []
        monkeypatch.setattr(warnings, "showwarning", lambda *a: seen.append(a))

        with human_warnings():
            warnings.showwarning(UserWarning("чужое"), UserWarning, "/чужая/lib.py", 7)

        assert len(seen) == 1

    def test_our_own_warning_does_not_reach_the_previous_handler(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        """Граница с другой стороны: наше печатаем сами и дальше не отдаём.

        Иначе пользователь увидел бы сообщение дважды — по-человечески и
        трассировкой.
        """
        seen: list[tuple[Any, ...]] = []
        monkeypatch.setattr(warnings, "showwarning", lambda *a: seen.append(a))

        with human_warnings():
            warnings.showwarning(
                UserWarning("наше"), UserWarning, str(_a_file_inside_the_package()), 1
            )

        assert seen == []
        assert "⚠️" in capsys.readouterr().err

    def test_the_previous_handler_is_restored(self) -> None:
        """Выход из контекста возвращает прежний обработчик.

        Утечка изменила бы поведение всего, что запускается следом в том же
        процессе, — включая сам набор тестов.
        """
        before = warnings.showwarning

        with human_warnings():
            assert warnings.showwarning is not before

        assert warnings.showwarning is before

    def test_it_is_restored_after_a_failure_too(self) -> None:
        """В том числе когда прогон упал — иначе утечка ждала бы первой ошибки."""
        before = warnings.showwarning

        with pytest.raises(RuntimeError), human_warnings():
            raise RuntimeError("прогон упал")

        assert warnings.showwarning is before
