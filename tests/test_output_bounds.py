"""Вывод решения обрабатывается с границами: полнота и длина (issue #952).

Две находки на одном пути «поток вывода решения → вердикт → печать»
(RUN-4-01, RUN-4-02). В обеих пользователь получал не тот результат, который
посчитал грейдер: в первой — ложный ``WA`` с пустым ``Actual`` на верном
решении, во второй — остановившийся CLI.
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path

from stepik_grader.core import reporter, runner


def _solution(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "task.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


class TestOutputSurvivesALingeringGrandchild:
    """RUN-4-01: вывод не теряется, если решение оставило живого внука.

    Внук наследует stdout и держит трубу открытой. EOF не приходит, а
    блокирующее чтение отдавало накопленное только по EOF или по заполнении
    буфера — то есть никогда. Верное решение получало ``WA`` с пустым
    ``Actual``, и понять причину по отчёту было нельзя.
    """

    def test_printed_answer_reaches_the_verdict(self, tmp_path: Path) -> None:
        solution = _solution(
            tmp_path,
            """
            import subprocess
            import sys

            a = int(input())
            b = int(input())
            print(a + b)
            sys.stdout.flush()
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            """,
        )

        outcome = runner.LocalRunner().run(
            runner.RunSpec(stdin=b"3\n4\n", timeout=10.0, path=solution, max_output_bytes=1_000_000)
        )

        assert outcome.stdout.strip() == b"7", (
            "вывод потерян — верное решение получит WA с пустым Actual"
        )
        assert not outcome.timed_out

    def test_lingering_grandchild_does_not_stall_the_run(self, tmp_path: Path) -> None:
        """Прогон не ждёт внука: он завершается по основному процессу.

        Внук спит тридцать секунд — если бы дренаж ждал EOF, столько же ждал бы
        и пользователь за каждый такой кейс.
        """
        solution = _solution(
            tmp_path,
            """
            import subprocess
            import sys

            print("ok")
            sys.stdout.flush()
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            """,
        )
        started = time.perf_counter()

        outcome = runner.LocalRunner().run(
            runner.RunSpec(stdin=None, timeout=25.0, path=solution, max_output_bytes=1_000_000)
        )

        assert time.perf_counter() - started < 15.0
        assert outcome.stdout.strip() == b"ok"

    def test_plain_solution_is_unaffected(self, tmp_path: Path) -> None:
        """Обычное решение читается как раньше — правка не меняет штатный путь."""
        solution = _solution(
            tmp_path,
            """
            print(int(input()) * 2)
            """,
        )

        outcome = runner.LocalRunner().run(
            runner.RunSpec(stdin=b"21\n", timeout=10.0, path=solution)
        )

        assert outcome.stdout.strip() == b"42"
        assert outcome.returncode == 0

    def test_output_larger_than_one_read_is_collected_whole(self, tmp_path: Path) -> None:
        """Вывод длиннее одного чтения собирается целиком, а не первым куском.

        `read1` возвращает то, что пришло за один системный вызов, — цикл обязан
        продолжаться до EOF, иначе длинный вывод обрежется молча.
        """
        solution = _solution(
            tmp_path,
            """
            import sys

            sys.stdout.write("y" * 500_000)
            """,
        )

        outcome = runner.LocalRunner().run(
            runner.RunSpec(stdin=None, timeout=20.0, path=solution, max_output_bytes=1_000_000)
        )

        assert len(outcome.stdout) == 500_000


class TestDiffLinesAreClippedByLength:
    """RUN-4-02: длинная строка diff не доходит до rich целиком.

    Число строк ограничивалось и раньше, длина — нет. Рендер rich растёт
    быстрее линейного, поэтому одной мегабайтной строки хватало, чтобы CLI
    перестал отвечать.
    """

    def test_long_line_is_clipped(self) -> None:
        lines, dropped = reporter._clip_diff_lines("z" * 100_000)

        assert dropped == 0
        assert len(lines) == 1
        assert len(lines[0]) < 10_000, "строка ушла бы в rich целиком"

    def test_clip_notice_names_the_dropped_amount(self) -> None:
        """Хвост объявляет объём — «обрезано» без числа не даёт понять масштаб."""
        lines, _ = reporter._clip_diff_lines("z" * 100_000)

        assert "ещё" in lines[0]
        assert "симв." in lines[0]

    def test_short_lines_are_untouched(self) -> None:
        lines, dropped = reporter._clip_diff_lines("- ожидалось 7\n+ получено 8")

        assert dropped == 0
        assert lines == ["- ожидалось 7", "+ получено 8"]

    def test_line_count_limit_still_applies(self) -> None:
        """Обрезка по длине не отменила обрезку по количеству."""
        many = "\n".join(f"line {index}" for index in range(200))

        lines, dropped = reporter._clip_diff_lines(many)

        assert dropped == 200 - len(lines)
        assert len(lines) < 200

    def test_clipping_touches_only_what_it_shows(self) -> None:
        """Стоимость ограничена показываемой частью, а не длиной вывода.

        Обрезки результата мало: вешал не объём, а работа по его подготовке —
        экранирование шло посимвольно по всей строке, миллион символов ради
        четырёхсот показанных. Считаем обработанные символы, а не секунды:
        замер времени под coverage меряет инструментацию, а не код.
        """
        seen = 0
        original = reporter.escape_control_chars

        def _counting(value: str) -> str:
            nonlocal seen
            seen += len(value)
            return original(value)

        reporter.escape_control_chars = _counting  # type: ignore[assignment]
        try:
            reporter._clip_diff_lines("q" * 1_000_000)
        finally:
            reporter.escape_control_chars = original  # type: ignore[assignment]

        assert seen <= reporter._VERBOSE_MAX_VALUE_CHARS, (
            f"экранировано {seen} символов ради {reporter._VERBOSE_MAX_VALUE_CHARS} показанных"
        )
