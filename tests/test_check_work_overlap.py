"""Тесты scripts/check_work_overlap.py — пересечения между окнами (issue #1091).

Гоняются на НАСТОЯЩЕМ временном git-репозитории, а не на моках обхода git: весь
смысл скрипта — правильно спросить git про живые ветки и их диффы, и подмена
этого слоя спрятала бы ровно то место, где живёт логика (тот же приём, что в
`test_version_script.py`).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_work_overlap.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_work_overlap", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Репозиторий с `origin/main` и двумя чужими ветками, трогающими разные файлы."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "test")

    (work / "shared.md").write_text("base\n", encoding="utf-8")
    (work / "solo.py").write_text("x = 1\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "-u", "origin", "main")

    # Чужая ветка №1 — трогает общий файл.
    _git(work, "checkout", "-b", "other/shared")
    (work / "shared.md").write_text("base\nfrom other\n", encoding="utf-8")
    _git(work, "commit", "-am", "other touches shared")
    _git(work, "push", "-u", "origin", "other/shared")

    # Чужая ветка №2 — трогает только свой файл.
    _git(work, "checkout", "main")
    _git(work, "checkout", "-b", "other/solo")
    (work / "solo.py").write_text("x = 2\n", encoding="utf-8")
    _git(work, "commit", "-am", "other touches solo")
    _git(work, "push", "-u", "origin", "other/solo")

    _git(work, "checkout", "main")
    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", work)
    return work


class TestOverlapDetection:
    """Пересечение по файлам — главный сигнал скрипта."""

    def test_shared_file_is_reported(self, repo: pathlib.Path) -> None:
        module = sys.modules["_check_work_overlap"]

        found = module.overlaps({"shared.md"})

        assert "origin/other/shared" in found
        assert found["origin/other/shared"] == {"shared.md"}

    def test_unrelated_file_is_not_reported(self, repo: pathlib.Path) -> None:
        module = sys.modules["_check_work_overlap"]

        assert module.overlaps({"nikto-ne-trogaet.txt"}) == {}

    def test_own_branch_is_excluded(self, repo: pathlib.Path) -> None:
        """Своя ветка не считается чужой работой — иначе предупреждение на каждый пуш."""
        module = sys.modules["_check_work_overlap"]

        found = module.overlaps({"shared.md"}, exclude_branch="other/shared")

        assert "origin/other/shared" not in found

    def test_main_is_never_listed_as_someone_elses_work(self, repo: pathlib.Path) -> None:
        module = sys.modules["_check_work_overlap"]

        assert "origin/main" not in module.living_branches()


class TestBranchFiles:
    """Разбор диффа ветки против базы."""

    def test_lists_only_changed_files(self, repo: pathlib.Path) -> None:
        module = sys.modules["_check_work_overlap"]

        assert module.branch_files("origin/other/solo") == {"solo.py"}

    def test_unknown_branch_is_empty_not_error(self, repo: pathlib.Path) -> None:
        """Отсутствующая ветка — пустой набор, а не падение: git молчит, скрипт тоже."""
        module = sys.modules["_check_work_overlap"]

        assert module.branch_files("origin/net-takoy-vetki") == set()


class TestExitCode:
    """Скрипт предупреждает, а не запрещает."""

    def test_returns_zero_even_with_overlap(self, repo: pathlib.Path) -> None:
        """Пересечение не должно ронять пуш: два PR могут законно править один файл."""
        module = sys.modules["_check_work_overlap"]
        (repo / "shared.md").write_text("base\nmine\n", encoding="utf-8")

        assert module.main([]) == 0

    def test_overview_returns_zero(self, repo: pathlib.Path) -> None:
        module = sys.modules["_check_work_overlap"]
        assert module.main(["--all"]) == 0


def test_runs_on_this_repository() -> None:
    """Скрипт отрабатывает на настоящем репозитории проекта."""
    result = subprocess.run([sys.executable, str(_SCRIPT), "--all"], capture_output=True, text=True)
    assert result.returncode == 0


@pytest.mark.parametrize("encoding", ["cp1251", "ascii"])
def test_survives_narrow_console_encoding(encoding: str) -> None:
    """Консоль без Юникода не роняет скрипт (issue #1095).

    Windows-джоб CI работает в cp1251/cp866, и `print` символа вне этой кодировки
    завершал скрипт `UnicodeEncodeError` — то есть инструмент, показывающий чужую
    работу, сам падал на половине платформ. Проверяется через переменную
    окружения: это тот же механизм, которым узкую кодировку задаёт Windows, и
    ловится он только запуском настоящего процесса.
    """
    env = {**os.environ, "PYTHONIOENCODING": encoding}

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--all"], capture_output=True, text=True, env=env
    )

    assert result.returncode == 0, result.stderr
