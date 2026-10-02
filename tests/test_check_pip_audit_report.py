"""Аудит зависимостей: «чисто» и «не отработал» — разные исходы (issue #921).

Находка REL-3-05: `pip-audit` выходит с ненулевым кодом и когда нашёл
уязвимость, и когда упал сам. Прежний шаг `ci.yml` печатал на оба случая
«найдена уязвимость», а job объявлен `continue-on-error` — то есть тихий отказ
инструмента был неотличим от чистого замыкания.

Разница ведёт к разным действиям: «проверили — чисто» закрывает вопрос, а
«проверить не удалось» означает, что сегодня о безопасности зависимостей мы не
знаем ничего. Поэтому исход называется тремя кодами возврата, а не двумя.

Проверяется по критерию приёмки #921 — guard обязан падать на заведомо
сломанном входе: нет отчёта, битый JSON, чужая структура.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_pip_audit_report.py"
_CI_YML = Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_pip_audit_report", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def _report(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "pip-audit.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


_CLEAN = {"dependencies": [{"name": "requests", "version": "2.32.3", "vulns": []}]}
_VULNERABLE = {
    "dependencies": [
        {"name": "requests", "version": "2.0.0", "vulns": [{"id": "GHSA-xxxx", "fix_versions": []}]}
    ]
}


class TestThreeOutcomes:
    def test_clean_report_is_success(self, module: ModuleType, tmp_path: Path) -> None:
        assert module.main([str(_report(tmp_path, _CLEAN))]) == module.EXIT_CLEAN

    def test_vulnerability_is_reported(
        self, module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = module.main([str(_report(tmp_path, _VULNERABLE))])

        assert code == module.EXIT_VULNERABLE
        assert "найдено уязвимостей: 1" in capsys.readouterr().out

    def test_missing_report_is_not_silence(
        self, module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Главный смысл скрипта: краха инструмента не должно быть не видно."""
        code = module.main([str(tmp_path / "нет.json")])

        assert code == module.EXIT_NOT_RUN
        assert "НЕ ОТРАБОТАЛ" in capsys.readouterr().out

    def test_broken_json_is_not_silence(
        self, module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Оборванный отчёт — тоже «не отработал», а не «уязвимостей нет»."""
        path = tmp_path / "pip-audit.json"
        path.write_text('{"dependencies": [', encoding="utf-8")

        code = module.main([str(path)])

        assert code == module.EXIT_NOT_RUN
        assert "НЕ ОТРАБОТАЛ" in capsys.readouterr().out

    def test_three_codes_are_distinct(self, module: ModuleType) -> None:
        """Иначе исходы снова схлопнутся — ради этого всё и делалось."""
        codes = {module.EXIT_CLEAN, module.EXIT_VULNERABLE, module.EXIT_NOT_RUN}

        assert len(codes) == 3


class TestCounting:
    def test_counts_across_dependencies(self, module: ModuleType) -> None:
        report = {
            "dependencies": [
                {"name": "a", "vulns": [{"id": "1"}, {"id": "2"}]},
                {"name": "b", "vulns": []},
                {"name": "c", "vulns": [{"id": "3"}]},
            ]
        }

        assert module.count_vulnerabilities(report) == 3

    def test_bare_list_form_is_supported(self, module: ModuleType) -> None:
        """Версия инструмента ставится ad-hoc и не пришпилена — форм две."""
        assert module.count_vulnerabilities([{"name": "a", "vulns": [{"id": "1"}]}]) == 1

    def test_missing_vulns_key_counts_as_zero(self, module: ModuleType) -> None:
        assert module.count_vulnerabilities({"dependencies": [{"name": "a"}]}) == 0

    @pytest.mark.parametrize(
        "junk",
        [
            "просто строка",
            42,
            {"вместо": "отчёта"},
            {"dependencies": "не список"},
            {"dependencies": ["не объект"]},
            {"dependencies": [{"name": "a", "vulns": "не список"}]},
        ],
    )
    def test_alien_structure_is_refused(self, module: ModuleType, junk: Any) -> None:
        """Молча вернуть ноль означало бы объявить чистоту по мусору."""
        with pytest.raises(ValueError):
            module.count_vulnerabilities(junk)


class TestCliAndWiring:
    def test_cli_returns_the_code(self, tmp_path: Path) -> None:
        path = _report(tmp_path, _VULNERABLE)
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert result.returncode == 1

    def test_ci_calls_the_checker(self) -> None:
        """Провод: скрипт, который никто не зовёт, — тот же слепой guard."""
        text = _CI_YML.read_text(encoding="utf-8")

        assert "check_pip_audit_report.py" in text

    def test_ci_asks_pip_audit_for_a_report(self) -> None:
        """Отличаем по наличию отчёта — значит его надо потребовать."""
        text = _CI_YML.read_text(encoding="utf-8")
        step = text[text.index("supply-chain:") :]

        assert re.search(r"--format=json", step)
        assert re.search(r"--output=pip-audit\.json", step)
