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
    """Запрет GitHub-сервера MCP держится формой, а не именами (issue #1346)."""

    def test_whole_github_server_is_denied(self, settings: dict[str, object]) -> None:
        """Запрещён сервер целиком — единственная форма, переживающая чужие имена.

        Прежний вариант перечислял двадцать девять имён инструментов и отказал
        молча: сервер их консолидировал, и ни одно записанное имя не осталось
        существующим. Проверка теперь про форму, а не про содержимое списка.
        """
        assert "mcp__github" in settings["permissions"]["deny"]  # type: ignore[index]

    def test_no_tool_names_anywhere(self, settings: dict[str, object]) -> None:
        """Именных записей нет ни в одном списке — они хрупки по построению.

        В `allow` такая запись вдобавок бесполезна: `deny` сильнее, и точечное
        разрешение поверх запрета сервера не сработает, создавая ложное
        впечатление доступности.
        """
        permissions: dict[str, object] = settings["permissions"]  # type: ignore[assignment]
        for name in ("deny", "allow", "ask"):
            for rule in permissions.get(name) or []:  # type: ignore[union-attr]
                assert not rule.startswith("mcp__github__"), (name, rule)

    def test_authorship_no_longer_depends_on_mcp(self, settings: dict[str, object]) -> None:
        """Авторство PR держит префикс ветки, а не разрешённый инструмент.

        Squash-мерж атрибутирует коммит автору pull request, и раньше ради
        этого приходилось держать `create_pull_request` доступным. Теперь PR
        открывает `agent-pr.yml` от имени владельца по PAT, поэтому запрет
        сервера целиком авторство не ломает — и исключений в списке не нужно.
        """
        deny = settings["permissions"]["deny"]  # type: ignore[index]

        assert deny == ["mcp__github"], "исключений быть не должно: их держит agent-pr.yml"

    def test_session_server_is_untouched(self, settings: dict[str, object]) -> None:
        """Сессионный MCP-сервер не GitHub API: запрет отрезал бы репозиторий."""
        permissions: dict[str, object] = settings["permissions"]  # type: ignore[assignment]
        rules = [
            rule
            for name in ("deny", "allow", "ask")
            for rule in permissions.get(name) or []  # type: ignore[union-attr]
        ]

        assert rules, "пустые списки означают, что запрета нет вовсе"
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
