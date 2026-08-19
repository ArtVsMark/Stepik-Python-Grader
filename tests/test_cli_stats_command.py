"""Подкоманда `stepik-grader stats` — единая точка статистики (issue #1192).

Раньше сводка доставалась четырьмя независимыми флагами (`--stats-summary`,
`--export-progress`, `--insights`, `--history`), и чтобы понять, какой из них
что показывает, приходилось читать справку целиком.

Тесты проверяют три вещи: команда работает во всех форматах, прежние флаги
остались рабочими (CLI-поверхность обратно совместима), а `--output html` вне
команды отвергается явно, а не игнорируется молча.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from stepik_grader import cli
from stepik_grader.core import stats as stats_mod


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Журнал, история и кэш — во временном каталоге, а не в домашнем."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(stats_mod, "_default_path", lambda: tmp_path / ".grader_stats.jsonl")


def _write_runs(tmp_path: pathlib.Path, *entries: dict[str, object]) -> None:
    path = tmp_path / ".grader_stats.jsonl"
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


class TestStatsCommand:
    def test_empty_history_reports_it_instead_of_failing(self, capsys) -> None:
        """Пустая история — не ошибка: команду зовут именно чтобы это узнать."""
        code = cli.main(["stats"])

        assert code == 0
        assert capsys.readouterr().out.strip()

    def test_json_carries_the_new_metrics(self, tmp_path: pathlib.Path, capsys) -> None:
        """Среднее время и очередь кэша — то, чего в накопителе не было."""
        _write_runs(
            tmp_path,
            {"mode": 1, "os": "Linux", "total_time": 2.0, "verdicts": {"AC": 1}},
            {"mode": 1, "os": "Linux", "total_time": 4.0, "verdicts": {"WA": 1}},
        )

        code = cli.main(["stats", "--output", "json"])

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total_runs"] == 2
        assert payload["avg_time"] == 3.0
        assert payload["cache_queue"] == {"tasks": 0, "stale": 0}
        assert payload["schema"] >= 1

    def test_average_is_zero_on_empty_journal(self, capsys) -> None:
        """Деление на ноль ждало бы каждого, кто считал среднее сам."""
        cli.main(["stats", "--output", "json"])

        assert json.loads(capsys.readouterr().out)["avg_time"] == 0.0

    def test_summary_table_shows_average(self, tmp_path: pathlib.Path, capsys) -> None:
        """Среднее — строкой таблицы и тем же форматтером, что и сумма."""
        _write_runs(tmp_path, {"mode": 1, "os": "Linux", "total_time": 1.0})

        cli.main(["stats"])

        assert "Average time" in capsys.readouterr().out


class TestBackwardCompatibility:
    """Прежние флаги — часть публичной поверхности, они обязаны работать."""

    @pytest.mark.parametrize("flag", ["--stats-summary", "--insights"])
    def test_old_flags_still_run(self, flag: str) -> None:
        assert cli.main([flag]) == 0

    def test_old_summary_flag_also_gained_the_average(self, tmp_path: pathlib.Path, capsys) -> None:
        """Метрика заведена в общей сводке, а не только в новой команде."""
        _write_runs(tmp_path, {"mode": 1, "os": "Linux", "total_time": 1.0})

        cli.main(["--stats-summary"])

        assert "Average time" in capsys.readouterr().out


class TestHtmlIsRefusedOutsideTheCommand:
    """`--output html` рендерится из истории, а не из вердиктов одного прогона."""

    def test_refused_with_a_pointer_to_the_right_command(self, capsys) -> None:
        """Сверяется ТЕКСТ отказа, а не факт SystemExit.

        Первая редакция теста проверяла только «упало с ненулевым кодом» — и
        оставалась зелёной, когда проверку снимали совсем: `--mode 1` без файла
        падает и без неё, по своей причине. Полу-откат это и показал.
        """
        with pytest.raises(SystemExit) as exc:
            cli.main(["--mode", "1", "--output", "html"])

        assert exc.value.code != 0
        stderr = capsys.readouterr().err
        assert "--output html" in stderr, stderr
        assert "stepik-grader stats --output html" in stderr, stderr

    def test_accepted_for_stats(self, tmp_path: pathlib.Path) -> None:
        assert cli.main(["stats", "--output", "html"]) == 0

    def test_other_formats_untouched(self) -> None:
        """Регресс-щит: расширение choices не должно ломать прежние значения."""
        assert cli.main(["stats", "--output", "json"]) == 0
