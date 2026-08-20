"""Настройки харнесса — механизм, а не памятка (issue #1280).

`.claude/settings.json` версионируется ради двух вещей: транспорт к GitHub
нельзя обойти по забывчивости, а участие автора проставляется само. Оба
свойства держатся содержимым файла, поэтому оно и проверяется тестом: правка,
снимающая запрет или теряющая человека, должна ронять прогон, а не выясняться
через месяц по счётчику квоты.
"""

from __future__ import annotations

import json
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SETTINGS = _ROOT / ".claude" / "settings.json"

_OWNER = "Artem Markitanov"


@pytest.fixture
def settings() -> dict[str, object]:
    """Разобранный `.claude/settings.json`."""
    return json.loads(_SETTINGS.read_text(encoding="utf-8"))


class TestFileIsTracked:
    """Файл обязан существовать и версионироваться."""

    def test_settings_file_exists(self) -> None:
        """Без файла механизма нет вовсе — остаётся просьба в документации."""
        assert _SETTINGS.is_file()

    def test_gitignore_lets_this_one_file_through(self) -> None:
        """`.claude/` игнорируется, но настройки — исключение.

        Проверяется форма правил: исключать надо СОДЕРЖИМОЕ (`.claude/*`), иначе
        git не заходит внутрь исключённого каталога и «!» на файле не работает.
        """
        rules = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        assert ".claude/*" in rules
        assert "!.claude/settings.json" in rules
        assert ".claude/" not in rules


class TestTransportIsEnforced:
    """Запрет GraphQL-инструментов и страховка на весь сервер."""

    def test_routine_github_tools_are_denied(self, settings: dict[str, object]) -> None:
        """Операции, покрытые `gh_rest.py`, запрещены поимённо."""
        deny = settings["permissions"]["deny"]  # type: ignore[index]

        assert "mcp__github__list_pull_requests" in deny
        assert "mcp__github__get_issue" in deny
        assert "mcp__github__merge_pull_request" in deny

    def test_whole_server_is_at_least_asked(self, settings: dict[str, object]) -> None:
        """Инструмент с неучтённым именем не проходит молча, а спрашивает."""
        assert "mcp__github" in settings["permissions"]["ask"]  # type: ignore[index]

    def test_creating_a_pull_request_is_not_denied(self, settings: dict[str, object]) -> None:
        """Создание PR остаётся доступным — им держится авторство в `main`.

        Squash-мерж атрибутирует коммит автору pull request, поэтому PR обязан
        открывать инструмент, работающий от имени человека. Запретить его —
        значит либо остановить конвейер, либо получить в истории бота.
        """
        deny = settings["permissions"]["deny"]  # type: ignore[index]

        assert "mcp__github__create_pull_request" not in deny

    def test_session_server_is_untouched(self, settings: dict[str, object]) -> None:
        """Сессионный MCP-сервер не GitHub API: запрет отрезал бы репозиторий."""
        rules = list(settings["permissions"]["deny"]) + list(  # type: ignore[index]
            settings["permissions"]["ask"]  # type: ignore[index]
        )

        assert all(rule.startswith("mcp__github") for rule in rules)


class TestOwnerIsNeverLost:
    """Участие человека проставляется само, а не вспоминается."""

    def test_both_participants_are_in_the_commit_trailer(self, settings: dict[str, object]) -> None:
        """Трейлер называет обоих — иначе в `main` уедет один инструмент."""
        trailer = settings["attribution"]["commit"]  # type: ignore[index]

        assert "Claude" in trailer
        assert _OWNER in trailer

    def test_pr_signature_names_the_human(self, settings: dict[str, object]) -> None:
        """Подпись PR — про совместную работу, а не про генерацию инструментом."""
        signature = str(settings["attribution"]["pr"])  # type: ignore[index]

        assert "ArtVsMark" in signature or _OWNER in signature
