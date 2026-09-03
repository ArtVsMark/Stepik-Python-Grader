"""Tests for scripts/check_version_consistency.py — version-drift guard (issue #165).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_version_script.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_version_consistency.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_version_consistency", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main дрейфа быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_version_consistency.py` завершается 0."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_pyproject_static_version_is_flagged(monkeypatch) -> None:
    """Возврат статической [project].version → ошибка (source-of-truth регресс)."""
    module = _load_module()
    errors: list[str] = []

    real_load = module.tomllib.load

    def fake_load(f):
        data = real_load(f)
        data.setdefault("project", {})["version"] = "9.9.9"
        data["project"]["dynamic"] = []
        return data

    monkeypatch.setattr(module.tomllib, "load", fake_load)
    module._check_pyproject_dynamic(errors)
    assert any("statically" in e for e in errors), errors


class TestReleaseInFlight:
    """Допуск «CHANGELOG на один MINOR впереди тега» — релизный PR до постановки тега.

    Без него гейт валил каждый релизный PR: запись `[X.Y+1.0]` появляется в
    момент подготовки, а тег ложится на merge-коммит уже смерженного PR.
    Допуск обязан быть узким — всё остальное по-прежнему дрейф.
    """

    @staticmethod
    def _with_top_entry(module, monkeypatch, tmp_path: Path, version: str) -> list[str]:
        """Прогнать _check_changelog на подставном CHANGELOG с заданной верхней записью.

        Подменяется сам путь, а не метод `read_text`: `pathlib.Path` объявляет
        `__slots__`, и `setattr` на экземпляре не проходит.
        """
        fake = tmp_path / "CHANGELOG.md"
        fake.write_text(
            f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-01-01\n", encoding="utf-8"
        )
        monkeypatch.setattr(module, "_CHANGELOG", fake)
        errors: list[str] = []
        module._check_changelog((1, 9, 0), errors)
        return errors

    def test_next_minor_is_allowed(self, monkeypatch, tmp_path: Path, capsys) -> None:
        module = _load_module()
        assert self._with_top_entry(module, monkeypatch, tmp_path, "1.10.0") == []
        assert "release in flight" in capsys.readouterr().out

    def test_matching_tag_is_allowed(self, monkeypatch, tmp_path: Path) -> None:
        module = _load_module()
        assert self._with_top_entry(module, monkeypatch, tmp_path, "1.9.0") == []

    @pytest.mark.parametrize(
        ("version", "why"),
        [
            ("1.11.0", "прыжок через MINOR"),
            ("2.0.0", "смена MAJOR"),
            ("1.8.0", "CHANGELOG отстал от тега"),
            ("1.10.1", "PATCH в релизной записи"),
        ],
    )
    def test_other_drift_still_fails(
        self, monkeypatch, tmp_path: Path, version: str, why: str
    ) -> None:
        module = _load_module()
        errors = self._with_top_entry(module, monkeypatch, tmp_path, version)
        assert errors, f"дрейф не пойман: {why}"
        assert "does not match" in errors[0]

    def test_claude_metrics_row_follows_the_same_rule(self, monkeypatch, tmp_path: Path) -> None:
        """Строка версии в CLAUDE.md обновляется тем же PR — предупреждения быть не должно."""
        module = _load_module()
        fake = tmp_path / "CLAUDE.md"
        fake.write_text("| Версия | 1.10.0 (stable) |\n", encoding="utf-8")
        monkeypatch.setattr(module, "_CLAUDE", fake)
        warnings: list[str] = []
        module._check_claude_metrics((1, 9, 0), warnings)
        assert warnings == []


def test_skips_without_git_tags(monkeypatch, capsys) -> None:
    """Нет baseline (нет git/тегов) → SKIP сверки доков, main() всё равно 0
    (pyproject на текущем репо динамический)."""
    module = _load_module()
    monkeypatch.setattr(module, "_latest_tag_baseline", lambda: None)
    monkeypatch.delenv("CI", raising=False)
    assert module.main() == 0
    assert "SKIP" in capsys.readouterr().out


class TestBaselineRequiredInCi:
    """Гейт обязан падать там, где проверять было чем, но не вышло (issue #988)."""

    def test_missing_baseline_in_ci_is_an_error(self, monkeypatch, capsys) -> None:
        """В CI теги доступны (fetch-depth: 0) — их отсутствие означает поломку.

        Прежде эта ветка печатала SKIP, затем «OK: versions consistent» и
        выходила нулём: гейт зеленел ровно тогда, когда не проверил ничего,
        кроме pyproject.
        """
        module = _load_module()
        monkeypatch.setattr(module, "_latest_tag_baseline", lambda: None)
        monkeypatch.setenv("CI", "true")

        code = module.main()

        assert code == 1
        assert "OK: versions consistent" not in capsys.readouterr().out

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_ci_flag_recognised(self, monkeypatch, value: str) -> None:
        module = _load_module()
        monkeypatch.setenv("CI", value)
        assert module._baseline_is_required() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no"])
    def test_local_run_keeps_skip(self, monkeypatch, value: str) -> None:
        """Локально сборка из sdist без истории по-прежнему не падает."""
        module = _load_module()
        monkeypatch.setenv("CI", value)
        assert module._baseline_is_required() is False


class TestHistoryMetricsRow:
    """issue #1181: проверка переехала с `docs/use/versions.md` на `HISTORY.md`.

    Форма изменилась вместе с местом: в прежнем документе релизы шли **колонками**
    и хватало проверки «есть ли такая подстрока», а в `HISTORY.md` они строки
    таблицы — и та же подстрока встречается ещё и в заголовке записи о релизе.
    Проверка «где угодно» проходила бы на пустой таблице.
    """

    def _history(self, tmp_path, body: str, monkeypatch):
        module = _load_module()
        path = tmp_path / "HISTORY.md"
        path.write_text(body, encoding="utf-8")
        monkeypatch.setattr(module, "_HISTORY", path)
        return module

    def test_row_present_is_silent(self, tmp_path, monkeypatch) -> None:
        module = self._history(tmp_path, "| Релиз |\n|---|\n| v1.10.0 | 2295 |\n", monkeypatch)
        warnings: list[str] = []

        module._check_history_md((1, 10, 0), warnings)

        assert warnings == []

    def test_missing_row_warns(self, tmp_path, monkeypatch) -> None:
        module = self._history(tmp_path, "| Релиз |\n|---|\n| v1.9.0 | 1600+ |\n", monkeypatch)
        warnings: list[str] = []

        module._check_history_md((1, 10, 0), warnings)

        assert warnings and "v1.10.0" in warnings[0]

    def test_heading_alone_is_not_a_row(self, tmp_path, monkeypatch) -> None:
        """Заголовок записи о релизе — не строка таблицы.

        Ровно этот случай прежняя проверка «tag in text» приняла бы за
        заполненную таблицу и промолчала.
        """
        module = self._history(
            tmp_path, "## v1.10.0 · 30 июля 2026 · тема\n\nтекст записи\n", monkeypatch
        )
        warnings: list[str] = []

        module._check_history_md((1, 10, 0), warnings)

        assert warnings and "v1.10.0" in warnings[0]
