"""Гейт «автор участвует в коммитах» (issue #1280).

Работа делается вдвоём, и история обязана это показывать: коммит, где владельца
нет ни в поле автора, ни в трейлерах, приписывает всю работу инструменту — а
после squash-мержа этот след остаётся в ``main`` навсегда.

Разбор текста ``git log`` проверяется чистой функцией, а сама проверка — на
временном репозитории, где коммиты делаются от разных авторов.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"

_OWNER = "Artem Markitanov"
_MAIL = "86671904+ArtVsMark@users.noreply.github.com"
_PYPROJECT = '[project]\nauthors = [\n    { name = "Artem Markitanov", email = "a@b.c" },\n]\n'


def _load_module() -> ModuleType:
    """Загрузить гейт как модуль: это скрипт, а не пакет."""
    spec = importlib.util.spec_from_file_location("_preflight_authorship", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: pathlib.Path, *args: str) -> None:
    """``git`` во временном репозитории; падение — сразу ошибка теста."""
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _record(short: str, author: str, message: str) -> str:
    """Одна запись ``git log`` в формате ``%h%x1f%an%x1f%B%x1e``."""
    return f"{short}\x1f{author}\x1f{message}\x1e"


@pytest.fixture
def module() -> ModuleType:
    """Свежий модуль гейта."""
    return _load_module()


@pytest.fixture
def gate(repo: pathlib.Path) -> ModuleType:
    """Модуль гейта, привязанный к временному репозиторию.

    Отдельная фикстура, а не ``module``: тот загружает модуль заново и уводит
    проверку в настоящий репозиторий проекта — где владелец, разумеется, есть.
    """
    return sys.modules["_preflight_authorship"]


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Клон с ``origin/main`` и веткой от него — как у остальных тестов гейта."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "bot@example.com")
    _git(work, "config", "user.name", "Claude")
    (work / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "feat/mine")

    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", work)
    monkeypatch.setattr(sys.modules["_preflight_authorship"], "_ROOT", work)
    return work


class TestOwnerName:
    """Имя владельца берётся из проекта, а не из git-идентичности окна."""

    def test_name_is_read_from_pyproject(self, module: ModuleType) -> None:
        """Источник один на все окна — ``[project].authors``."""
        assert module.owner_name(pyproject=_PYPROJECT) == _OWNER

    def test_missing_authors_is_not_a_failure(self, module: ModuleType) -> None:
        """Владельца не назвали — проверять нечего, а не «всё плохо»."""
        assert module.owner_name(pyproject="[project]\nname = 'x'\n") == ""


class TestCommitsWithoutOwner:
    """Разбор лога: где владелец есть, а где потерялся."""

    def test_owner_as_author_is_enough(self, module: ModuleType) -> None:
        """Автор — человек: соавторства не требуется."""
        log = _record("aaa1111", _OWNER, "feat: что-то\n")

        assert module.commits_without_owner(log, _OWNER) == []

    def test_owner_as_co_author_is_enough(self, module: ModuleType) -> None:
        """Коммит ушёл от бота, но человек назван соавтором — участие видно."""
        log = _record("bbb2222", "Claude", f"feat: что-то\n\nCo-Authored-By: {_OWNER} <a@b.c>\n")

        assert module.commits_without_owner(log, _OWNER) == []

    def test_commit_without_the_owner_is_caught(self, module: ModuleType) -> None:
        """Ни автором, ни соавтором — это и есть потерянный автор."""
        log = _record("ccc3333", "Claude", "feat: без человека\n")

        assert module.commits_without_owner(log, _OWNER) == ["ccc3333"]

    def test_every_lost_commit_is_named(self, module: ModuleType) -> None:
        """Названы все, а не первый: чинить придётся каждый."""
        log = _record("ddd4444", "Claude", "раз\n") + _record("eee5555", "claude[bot]", "два\n")

        assert module.commits_without_owner(log, _OWNER) == ["ddd4444", "eee5555"]

    def test_contributor_commit_is_not_our_business(self, module: ModuleType) -> None:
        """Коммит стороннего человека проверка не трогает вовсе.

        Вопрос стоит «не приписана ли совместная работа инструменту», а не
        «указан ли владелец везде»: требовать его имя в коммите контрибьютора
        было бы и неверно, и отталкивающе.
        """
        log = _record("999cccc", "Внешний Контрибьютор", "fix: опечатка\n")

        assert module.commits_without_owner(log, _OWNER) == []

    def test_bot_suffix_counts_as_a_tool(self, module: ModuleType) -> None:
        """Суффикс ``[bot]`` — это приложение GitHub, а не человек."""
        assert module.authored_by_tool("some-app[bot]")

    def test_human_nickname_with_bot_inside_is_a_human(self, module: ModuleType) -> None:
        """«bot» внутри ника — не повод обвинять человека."""
        assert not module.authored_by_tool("Botvinnik")

    def test_other_trailers_do_not_count(self, module: ModuleType) -> None:
        """Упоминание в теле — не соавторство; считается только трейлер."""
        log = _record("fff6666", "Claude", f"по просьбе {_OWNER}\n\nSigned-off-by: bot <b@c.d>\n")

        assert module.commits_without_owner(log, _OWNER) == ["fff6666"]

    def test_case_is_ignored(self, module: ModuleType) -> None:
        """Регистр имени в трейлере не должен решать судьбу пуша."""
        log = _record("777aaaa", "Claude", "feat: x\n\nCo-authored-by: ARTEM MARKITANOV <a@b.c>\n")

        assert module.commits_without_owner(log, _OWNER) == []

    def test_unknown_owner_disables_the_check(self, module: ModuleType) -> None:
        """Без имени владельца проверка молчит, а не обвиняет всех подряд."""
        log = _record("888bbbb", "Claude", "feat: x\n")

        assert module.commits_without_owner(log, "") == []


class TestCheckOnRealRepo:
    """Проверка целиком — на настоящих коммитах временного репозитория."""

    def test_bot_only_commit_is_red(self, gate: ModuleType, repo: pathlib.Path) -> None:
        """Коммит от бота без трейлера пуш не проходит."""
        (repo / "module.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "feat: только инструмент")

        check = gate.check_commit_authorship()

        assert not check.ok
        assert "Co-Authored-By" in check.hint

    def test_trailer_makes_it_green(self, gate: ModuleType, repo: pathlib.Path) -> None:
        """Тот же коммит с трейлером соавторства — зелёный."""
        (repo / "module.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(
            repo,
            "commit",
            "-m",
            f"feat: вместе\n\nCo-Authored-By: {_OWNER} <{_MAIL}>",
        )

        assert gate.check_commit_authorship().ok

    def test_branch_without_commits_is_green(self, gate: ModuleType, repo: pathlib.Path) -> None:
        """Коммитов ещё нет — терять нечего, гейт не мешает работать."""
        assert gate.check_commit_authorship().ok


class TestMypyMirrorsCi:
    """Гейт обещает зеркалить CI — значит и платформу проверки типов тоже."""

    def test_linux_runs_without_the_flag(self, module: ModuleType) -> None:
        """На Linux добавлять нечего: CI гоняет ровно этот вызов."""
        command = module._mypy_command(platform="linux")

        assert "--platform" not in command
        assert command[-2:] == ["src/stepik_grader", "scripts"]

    def test_windows_asks_for_the_ci_platform(self, module: ModuleType) -> None:
        """На Windows без флага семь ошибок, которых в CI не существует."""
        command = module._mypy_command(platform="win32")

        assert command[-4:] == ["--platform", "linux", "src/stepik_grader", "scripts"]

    def test_macos_too(self, module: ModuleType) -> None:
        """Правило про платформу CI, а не про Windows: macOS ведёт себя так же."""
        assert "--platform" in module._mypy_command(platform="darwin")
