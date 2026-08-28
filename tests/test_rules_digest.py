"""Второй рубеж: правила читаются окном на старте (issue #1384).

Первый рубеж — механизм: гейт краснеет, и правило действует независимо от
памяти окна. Второй — дайджест: утверждения всех правил одной строкой, которые
стартовый хук кладёт в контекст сессии.

Проверяется здесь не «файл собрался», а два его свойства: **порядок** (сначала
то, что не поймает машина) и **честность** (обрезка обозначена, пустая группа
названа словами). Плюс — что хук вообще объявлен: файл без хука существует, но
окном не читается, а ради чтения он и заведён.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load(name: str) -> Any:
    path = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


digest = _load("generate_rules_digest")
guard = _load("check_rules_digest")


def _rule(rule_id: str, status: str = "active", mechanism: str = "none") -> Any:
    return digest.Rule(
        rule_id=rule_id,
        title=f"Заголовок {rule_id}",
        claim="Утверждение правила.",
        status=status,
        mechanism=mechanism,
        where="",
        path=f"rules/ru/{rule_id}-slug.md",
    )


class TestOrderAndHonesty:
    def test_unheld_rules_come_first(self) -> None:
        """Порядок — не косметика: правило с гейтом окну помнить не обязательно."""
        text = digest.render([_rule("001", mechanism="gate"), _rule("002")])

        assert text.index("Не держится ничем") < text.index("Держится гейтом")

    def test_gated_rules_come_without_claims(self) -> None:
        """Стартовый контекст не бесконечен: у гейтов остаётся заголовок."""
        text = digest.render([_rule("001", mechanism="gate")])

        assert "**001** Заголовок 001." in text
        assert "Утверждение правила." not in text

    def test_empty_group_says_so(self) -> None:
        """Пустой раздел читался бы как «не собрали» (правило 027)."""
        assert "_Сейчас пусто._" in digest.render([_rule("001")])

    def test_clipped_claim_is_marked(self) -> None:
        """Молча урезанное утверждение выглядит полным (правило 016)."""
        clipped = digest.clip("слово " * 100, limit=40)

        assert clipped.endswith("…")
        assert len(clipped) <= 42

    def test_short_claim_is_untouched(self) -> None:
        assert digest.clip("Коротко и ясно.") == "Коротко и ясно."

    def test_answered_negative_rules_are_not_shown(self) -> None:
        """`not-applicable` окну помнить нечего: предмета здесь нет."""
        text = digest.render([_rule("003", status="not-applicable", mechanism="")])

        assert "**003**" not in text


class TestGuard:
    _SETTINGS = json.dumps(
        {
            "hooks": {
                "SessionStart": [{"hooks": [{"command": "python .claude/hooks/session_start.py"}]}],
                "PreToolUse": [{"hooks": [{"command": "python .claude/hooks/pre_tool_use.py"}]}],
            }
        }
    )

    def test_group_mismatch_is_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Правило, названное гейтом, а держащееся ничем, — ложь второму рубежу."""
        monkeypatch.setattr(guard, "expected_groups", lambda: {"001": "none"})
        monkeypatch.setattr(guard, "digest_groups", lambda: {"001": "gate"})
        errors: list[str] = []

        guard.check_digest(errors)

        assert len(errors) == 1
        assert "001" in errors[0]

    def test_missing_rule_is_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(guard, "expected_groups", lambda: {"007": "none"})
        monkeypatch.setattr(guard, "digest_groups", lambda: {})
        errors: list[str] = []

        guard.check_digest(errors)

        assert len(errors) == 1
        assert "не названо в дайджесте" in errors[0]

    def test_unregistered_hooks_are_red(self) -> None:
        """Дайджест без хука — документ, который никто не открывает.

        Проверяются оба требуемых события: снятый `PreToolUse` осиротит правила
        012 и 013 так же молча, как снятый `SessionStart` — весь второй рубеж.
        """
        errors: list[str] = []

        guard.check_hook_is_registered(errors, json.dumps({"hooks": {}}))

        assert len(errors) == len(guard._REQUIRED_HOOKS)
        assert any("SessionStart" in error for error in errors)
        assert any("PreToolUse" in error for error in errors)

    def test_declared_hook_must_exist_in_the_repository(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Объявить хук и не положить файл — то же, что не объявить.

        Ровно это и случилось: `.gitignore` игнорировал `.claude/*` целиком,
        хук жил только в рабочей копии, и в чистом клоне механизма не было —
        настройки при этом честно его объявляли.
        """
        monkeypatch.setattr(guard, "_ROOT", tmp_path)
        errors: list[str] = []

        guard.check_hook_is_registered(errors, self._SETTINGS)

        assert len(errors) == len(guard._REQUIRED_HOOKS)
        assert all("нет в репозитории" in error for error in errors)

    def test_hook_file_is_tracked_by_git(self) -> None:
        """Живой предмет: файл хука виден git, а не только файловой системе."""
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", ".claude/hooks"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=_ROOT,
        ).stdout

        for script in guard._REQUIRED_HOOKS.values():
            assert script in tracked, (
                f"хук {script} не отслеживается git: в чистом клоне его не будет, "
                "а настройки продолжат его объявлять"
            )

    def test_registered_hook_passes(self) -> None:
        errors: list[str] = []

        guard.check_hook_is_registered(errors, self._SETTINGS)

        assert errors == []

    def test_digest_groups_are_parsed_from_headings(self) -> None:
        text = (
            "## Не держится ничем — только вниманием окна — 2\n\n"
            "- **011** Одно.\n- **012** Другое.\n\n"
            "## Держится гейтом — 1\n\n- **001** Третье.\n"
        )

        assert guard.digest_groups(text) == {"011": "none", "012": "none", "001": "gate"}

    def test_live_repository_is_consistent(self) -> None:
        """Живой предмет: дайджест репозитория и ответ проекта не разошлись."""
        errors: list[str] = []

        guard.check_digest(errors)
        guard.check_hook_is_registered(errors)

        assert errors == []


class TestHook:
    def test_hook_prints_the_digest(self) -> None:
        """Хук — это способ прочитать; проверяем прогоном, а не чтением кода."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(_ROOT / ".claude" / "hooks" / "session_start.py")],
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )

        assert "Правила проекта" in result.stdout
        assert "Не держится ничем" in result.stdout

    def test_hook_survives_a_narrow_console(self) -> None:
        """Дайджест русский, а консоль бывает cp1251 — падение съело бы старт.

        Кодировка здесь `cp1252`, а не `cp1251`: в русской консоли кириллица
        как раз кодируется, а падало на Windows-раннере, где кодовая страница
        западноевропейская и кириллицы в ней нет вовсе. Поймано прогоном — на
        всех трёх Windows-комбинациях матрицы хук возвращал код 1, то есть
        ронял бы старт сессии из-за кодировки вывода.
        """
        import os
        import subprocess

        result = subprocess.run(
            [sys.executable, str(_ROOT / ".claude" / "hooks" / "session_start.py")],
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        )

        assert result.returncode == 0, result.stderr
        assert "Правила проекта" in result.stdout

    def test_missing_digest_is_not_a_failure(self, tmp_path: pathlib.Path) -> None:
        """Старт сессии не роняется из-за документа."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(_ROOT / ".claude" / "hooks" / "session_start.py")],
            input="{}",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={"CLAUDE_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        )

        assert result.returncode == 0
        assert "не прочитан" in result.stdout
