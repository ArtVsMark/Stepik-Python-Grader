"""Гейт покрытия не делает частичный прогон ложно-красным (issue #954).

`addopts` включает `--cov` безусловно, поэтому порог, объявленный в
`[tool.coverage.report]`, применялся к ЛЮБОМУ запуску: прогон одного файла
мерял покрытие всего пакета по паре файлов, получал ~0% и возвращал 1 при
полностью зелёных тестах. Читающий код возврата — контрибьютор, агент или
скрипт — делает вывод «тесты упали» и чинит несуществующую поломку.

Проверяется настоящим запуском pytest в подпроцессе: смысл находки — именно в
коде возврата процесса, и подмена этого слоя спрятала бы ровно то место, где
живёт дефект.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).parent.parent

# Маленький и быстрый файл: тест запускает pytest внутри pytest, и брать сюда
# что-то тяжёлое значило бы платить минутами за проверку кода возврата.
_TARGET = "tests/test_timeout_config.py"


def _run_partial(tmp_path: pathlib.Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Прогнать один тестовый файл так, чтобы не задеть данные основного прогона."""
    env = {
        **os.environ,
        # Без этого подпроцесс перезаписал бы .coverage корня — то есть тест
        # портил бы покрытие того прогона, внутри которого сам работает.
        "COVERAGE_FILE": str(tmp_path / ".coverage"),
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            _TARGET,
            "-q",
            "-p",
            "no:cacheprovider",
            "--cov-report=",
            # issue #1165: свой каталог временных файлов, а не общесистемный.
            # Дочерний pytest брал его из `tempfile.gettempdir()`, и там, где
            # тот закрыт на запись, падала фикстура `_known_filesystem_entries`
            # — то есть прогон не начинался вовсе. Внешний тест видел код 1 и
            # объявлял это провалом гейта покрытия: диагноз, противоположный
            # происходящему. Каталог берётся из `tmp_path`, который сюда и так
            # передан ради `COVERAGE_FILE`.
            "--basetemp",
            str(tmp_path / "pytest-tmp"),
            *extra,
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


@pytest.mark.timeout(360)
class TestPartialRunIsNotFalselyRed:
    """Зелёные тесты обязаны давать код возврата 0."""

    def test_single_file_run_exits_zero(self, tmp_path: pathlib.Path) -> None:
        """Прогон одного файла — успех, а не провал по чужому покрытию."""
        result = _run_partial(tmp_path)

        # issue #1165: у кода 2+ причина не в гейте покрытия. pytest отдаёт 1
        # только когда тесты реально упали; 2 — прерывание, 3 — внутренняя
        # ошибка, 4 — негодные аргументы, 5 — тестов не найдено. Сваливать это
        # в «гейт завернул зелёный прогон» значит уводить разбор в сторону:
        # именно так падение фикстуры дочернего прогона два раза выглядело
        # регрессом гейта.
        assert result.returncode < 2, (
            f"дочерний pytest не стартовал (код {result.returncode}) — "
            "это не гейт покрытия, а сбой самого прогона:\n"
            + result.stdout[-2000:]
            + result.stderr[-2000:]
        )
        assert result.returncode == 0, (
            "частичный прогон вернул ненулевой код при зелёных тестах:\n"
            + result.stdout[-2000:]
            + result.stderr[-2000:]
        )
        assert "Required test coverage" not in result.stdout, (
            "порог покрытия применился к частичному прогону — он снова в конфиге"
        )

    def test_threshold_still_works_when_asked(self, tmp_path: pathlib.Path) -> None:
        """Гейт не отменён: с явным флагом недобор покрытия по-прежнему валит прогон.

        Именно это отличает «перенесли порог» от «убрали порог»: в CI флаг
        задан в шаге `Run tests`, и там прогон полный.
        """
        result = _run_partial(tmp_path, "--cov-fail-under=99")

        assert result.returncode != 0, "с флагом --cov-fail-under недобор обязан валить прогон"
        assert "Required test coverage" in result.stdout


class TestConfigKeepsThresholdOutOfPyproject:
    """Порог не должен вернуться в конфиг незаметно."""

    def test_pyproject_has_no_fail_under(self) -> None:
        """`[tool.coverage.report] fail_under` — источник ложно-красных прогонов."""
        text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        report_section = text.split("[tool.coverage.report]", 1)[1].split("\n[tool.", 1)[0]

        declared = [
            line
            for line in report_section.splitlines()
            if line.strip().startswith("fail_under") and not line.strip().startswith("#")
        ]

        assert not declared, (
            "fail_under вернулся в pyproject.toml: из-за безусловного --cov в addopts "
            "он делает ложно-красным любой частичный прогон (issue #954). "
            "Порог живёт флагом --cov-fail-under в шаге CI"
        )
