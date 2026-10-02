"""Правило про квоту GitHub API сформулировано по механизму (issue #1222).

Прежняя редакция называла команду — `gh pr view`/`gh pr checks` — и не
сработала: квоту выжгло не ими, а чтениями через GraphQL, причём в окне, где
`gh` нет вовсе. Правило, написанное про инструмент, читалось как «меня не
касается».

Тесты сторожат не формулировку, а состав: назван ли механизм, есть ли запрет
повторять вызовы после исчерпания, сказано ли про общую на аккаунт квоту и про
окно-наблюдатель. Переписать текст можно — потерять пункт нельзя.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_CLAUDE_MD = _ROOT / "CLAUDE.md"
_PREFLIGHT_MD = _ROOT / "docs" / "agent" / "preflight.md"


def _flat(path: pathlib.Path) -> str:
    """Текст документа со схлопнутыми пробелами.

    Markdown переносится по 80 символов, поэтому любая фраза длиннее строки
    оказывается разорванной. Проверять надо смысл, а не вёрстку: без
    нормализации тест краснел бы на переносе внутри «Квота общая на аккаунт».
    """
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.fixture(scope="module")
def claude_md() -> str:
    return _flat(_CLAUDE_MD)


@pytest.fixture(scope="module")
def preflight_md() -> str:
    return _flat(_PREFLIGHT_MD)


class TestRuleNamesTheMechanism:
    """Главная поправка: дорого не имя команды, а транспорт."""

    def test_claude_md_names_graphql(self, claude_md: str) -> None:
        assert "GraphQL" in claude_md

    def test_claude_md_says_points_not_requests(self, claude_md: str) -> None:
        """«Пять тысяч обращений» — иллюзия: лимит считается в points."""
        assert "points" in claude_md

    def test_claude_md_does_not_blame_a_command_alone(self, claude_md: str) -> None:
        """`gh pr view` упоминать можно, но только как частный случай.

        Проверяется, что рядом названо, к какому классу он относится, — иначе
        правило снова прочтут как «не вызывай эту команду».
        """
        if "gh pr view" not in claude_md:
            return
        at = claude_md.index("gh pr view")
        window = claude_md[max(0, at - 600) : at + 300]
        assert "частный случай" in window, "команда названа без указания на механизм"

    def test_rest_path_of_the_gates_is_confirmed(self, claude_md: str) -> None:
        """Чтобы `check_pr_ready.py` никто не «оптимизировал» на GraphQL."""
        assert "check_pr_ready.py" in claude_md
        assert "REST" in claude_md


class TestExhaustionRule:
    def test_claude_md_forbids_retrying(self, claude_md: str) -> None:
        assert "не повторять" in claude_md or "а не повторять" in claude_md

    def test_measurement_is_kept_as_evidence(self, claude_md: str) -> None:
        """Правила проекта растут из инцидентов, а не из общих соображений."""
        assert "10 724" in claude_md

    def test_quota_is_shared_across_windows(self, claude_md: str) -> None:
        assert "общая на аккаунт" in claude_md


class TestCanonicalDocHasTheDetail:
    """CLAUDE.md — компактный триггер; разбор живёт в preflight.md."""

    def test_measurement_table_is_there(self, preflight_md: str) -> None:
        for wallet in ("core", "graphql", "search"):
            assert f'"{wallet}"' in preflight_md
        assert "10724" in preflight_md

    def test_diagnostics_command_for_both_windows(self, preflight_md: str) -> None:
        """`gh` есть только в локальном окне — облачному нужен свой способ."""
        assert "gh api rate_limit" in preflight_md
        assert "api.github.com/rate_limit" in preflight_md, "облачное окно осталось без команды"

    def test_saving_practices_are_listed(self, preflight_md: str) -> None:
        assert "Практики экономии" in preflight_md

    @pytest.mark.parametrize(
        "requirement",
        [
            "subscribe_pr_activity",  # событие вместо опроса
            "If-None-Match",  # условные запросы
            "304",  # ответ, не расходующий лимит
            "updated_at",  # дельта вместо полного обхода
        ],
    )
    def test_watcher_window_requirements(self, preflight_md: str, requirement: str) -> None:
        assert requirement in preflight_md, f"требование к наблюдателю пропало: {requirement}"

    def test_watcher_has_its_own_section(self, preflight_md: str) -> None:
        assert "Окно-наблюдатель за PR" in preflight_md

    def test_ci_is_excluded_explicitly(self, preflight_md: str) -> None:
        """У CI свой токен и свой лимит — иначе туда полезут «экономить»."""
        assert "GITHUB_TOKEN" in preflight_md


class TestClaudeMdStaysATrigger:
    """Детали не дублируются в CLAUDE.md — иначе две редакции разойдутся."""

    @pytest.mark.parametrize("detail", ["If-None-Match", "updated_at", "rate_limit"])
    def test_detail_lives_only_in_the_canonical_doc(self, claude_md: str, detail: str) -> None:
        assert detail not in claude_md, (
            f"{detail} — деталь для docs/agent/preflight.md, а не для CLAUDE.md"
        )
