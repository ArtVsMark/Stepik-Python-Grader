"""Команда ``--doctor``: отчёт об окружении до сбоя, а не только после (issue #982).

Отчёт существовал ровно в одном месте — внутри диагностики Stepik, и только
когда та **падала**. Между тем файл для issue ценен и до сбоя: «у меня не
скачивается задача» без него превращается в переписку с уточнениями. А совет
«сходите запустите диагностику» отправлял пользователя туда, где инструмент
требует ввода и лезет в сеть.

Проверяется три вещи, и все три — про поведение, а не про наличие кода: что
команда отвечает кодом возврата (находка ``JRN-3A-03``: диагностика годами
возвращала ноль при любом исходе), что отчёт пишется всегда, и что секреты в нём
отредактированы (``OPS-1-02``).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from stepik_grader.cli import main
from stepik_grader.cli.exit_codes import ExitCode
from stepik_grader.core import diagnostics, doctor


def _good_secrets(path: pathlib.Path) -> pathlib.Path:
    """Правдоподобный ``secrets.json`` с секретом внутри."""
    path.write_text(
        json.dumps(
            {
                "client_id": "id-12345",
                "client_secret": "sEcReT-token-value-0123456789abcdef",
                "redirect_uri": "http://127.0.0.1:8080",
                "access_token": "aAbBcC0123456789aAbBcC0123456789",
            }
        ),
        encoding="utf-8",
    )
    return path


class TestExitCode:
    """Команда пригодна как автоматическая проверка, а не только как чтение."""

    def test_a_failed_check_makes_the_code_nonzero(self, tmp_path: pathlib.Path) -> None:
        findings = doctor.collect(tmp_path / "нет.json", network=False)

        assert doctor.exit_code(findings) == 1

    def test_a_skip_is_not_a_failure(self) -> None:
        """«Предмета нет» и «предмет плох» — разные ответы.

        Офлайн-прогон не должен выглядеть сломанным окружением: иначе команду
        начнут звать только с сетью, то есть реже всего.
        """
        skipped = [
            diagnostics.Finding(
                check=diagnostics.CHECKS[0],
                outcome=diagnostics.Outcome(diagnostics.Status.SKIP, "почему-то"),
            )
        ]

        assert doctor.exit_code(skipped) == 0


class TestTheReport:
    """Файл для issue: пишется всегда и не выносит секретов."""

    def test_secrets_are_redacted(self, tmp_path: pathlib.Path) -> None:
        """Файл создаётся ради того, чтобы его приложили к issue.

        Поэтому цена пропущенного токена здесь выше обычной, и редакция идёт по
        готовому JSON, а не по известным полям.
        """
        secrets = _good_secrets(tmp_path / "secrets.json")
        findings = doctor.collect(secrets, network=False)

        path = doctor.save_report(tmp_path / "out", doctor.build_report(findings, lambda k, **p: k))
        text = path.read_text(encoding="utf-8")

        assert "sEcReT-token-value-0123456789abcdef" not in text
        assert "aAbBcC0123456789aAbBcC0123456789" not in text

    def test_the_report_is_written_even_when_all_is_well(self, tmp_path: pathlib.Path) -> None:
        """«Всё хорошо, файла нет» заставляло бы ломать окружение ради отчёта."""
        path = doctor.save_report(tmp_path / "out", {"checks": []})

        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8")) == {"checks": []}

    def test_the_report_name_matches_the_diagnostics_one(self) -> None:
        """Имя одно: два файла для одного содержимого заставляют переспрашивать."""
        from stepik_grader.diagnostic_stepik import ENVIRONMENT_REPORT_NAME

        assert doctor.REPORT_NAME == ENVIRONMENT_REPORT_NAME


class TestTheRegistryIsShared:
    """Своих проверок команда не заводит — иначе ответы разъедутся."""

    def test_it_runs_the_common_registry(self, tmp_path: pathlib.Path) -> None:
        findings = doctor.collect(tmp_path / "нет.json", network=False)

        assert [f.id for f in findings] == [c.id for c in diagnostics.CHECKS]


class TestThroughTheCli:
    """То же самое, но так, как это увидит пользователь."""

    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Набор не ходит в сеть: сетевую пробу отключаем на уровне контекста.

        Подменяется не `collect`, а её умолчание по сети — связка «CLI зовёт
        реестр и обрабатывает исход» остаётся под проверкой, а прогон не
        зависит от того, дотянется ли раннер до stepik.org.
        """
        real = doctor.collect
        monkeypatch.setattr(
            doctor,
            "collect",
            lambda path, **kw: real(path, **{**kw, "network": False}),
        )

    def test_it_reports_and_exits_nonzero(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)

        code = main(["--doctor"])

        out = capsys.readouterr().out
        assert code == ExitCode.FAILURES  # secrets.json нет — окружение не готово
        assert "environment_checks.json" in out
        assert (tmp_path / "stepik_diagnostics" / "environment_checks.json").is_file()

    def test_it_does_not_ask_anything(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Команда не должна требовать ввода: её зовут, когда уже сломалось.

        Прежний путь к отчёту вёл через диагностику с приглашениями — и на
        закрытом stdin она падала `EOFError` (находка ``RUN-5-03``).
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("команда запросила ввод"))

        main(["--doctor"])
