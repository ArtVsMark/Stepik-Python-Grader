"""Тесты scripts/preflight.py — предпушевый гейт (issue #997).

Гоняются на НАСТОЯЩЕМ временном git-репозитории, а не на моках обхода git: вся
логика проверок — это правильно заданный вопрос к git («свежая ли база»,
«не ведёт ли это имя кто-то ещё»), и подмена этого слоя спрятала бы ровно то
место, где живёт смысл (тот же приём, что в `test_check_work_overlap.py`).
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "preflight.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_preflight", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Клон с `origin/main`, одним коммитом и веткой `feat/mine` от него."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "test")

    (work / "CHANGELOG.md").write_text(
        "# Changelog\n\n## Буфер (разобрать при релизе)\n\n## [Unreleased]\n", encoding="utf-8"
    )
    (work / "module.py").write_text("x = 1\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "-u", "origin", "main")
    _git(work, "checkout", "-b", "feat/mine")

    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", work)
    monkeypatch.setattr(sys.modules["_preflight"], "_ROOT", work)
    return work


@pytest.fixture
def preflight(repo: pathlib.Path) -> ModuleType:
    """Модуль скрипта, привязанный к временному репозиторию."""
    return sys.modules["_preflight"]


def _move_main_ahead(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """Чужое окно смержило свой PR: `origin/main` ушёл вперёд."""
    other = tmp_path / "other"
    origin = repo.parent / "origin.git"
    subprocess.run(["git", "clone", str(origin), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.email", "other@example.com")
    _git(other, "config", "user.name", "other")
    (other / "their.py").write_text("y = 2\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "their work")
    _git(other, "push", "origin", "main")
    _git(repo, "fetch", "origin", "main")


class TestBranchHygiene:
    """Гигиена ветки: не main, от свежего main, имя не занято."""

    def test_fresh_branch_is_green(self, preflight: ModuleType) -> None:
        """Ветка, только что созданная от main, свежая по определению."""
        assert preflight.check_branch_fresh().ok

    def test_stale_branch_is_caught(
        self, preflight: ModuleType, repo: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """Ушедший вперёд `origin/main` делает ветку несвежей.

        Ровно инцидент, который дал красный `main`: гейты прогнали на ветке,
        созданной до чужого мержа, — «зелено у меня» ≠ «зелено после мержа».
        """
        _move_main_ahead(repo, tmp_path)

        check = preflight.check_branch_fresh()

        assert not check.ok
        assert "main ушёл вперёд" in check.detail

    def test_main_branch_is_rejected(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """Прямая работа в `main` запрещена."""
        _git(repo, "checkout", "main")

        assert not preflight.check_branch_not_main().ok

    def test_free_branch_name_is_green(self, preflight: ModuleType) -> None:
        """Имени ветки нет на origin — занимать нечего."""
        check = preflight.check_branch_not_taken()

        assert check.ok
        assert "на origin нет" in check.detail

    def test_own_pushed_branch_is_green(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """Своя же запушенная ветка — не «чужая работа»."""
        _git(repo, "push", "-u", "origin", "feat/mine")

        assert preflight.check_branch_not_taken().ok

    def test_foreign_branch_with_same_name_is_caught(
        self, preflight: ModuleType, repo: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """Имя занято соседним окном — пуш отлетит «tip is behind»."""
        other = tmp_path / "other-window"
        origin = repo.parent / "origin.git"
        subprocess.run(["git", "clone", str(origin), str(other)], check=True, capture_output=True)
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "config", "user.name", "other")
        _git(other, "checkout", "-b", "feat/mine")
        (other / "theirs.py").write_text("z = 3\n", encoding="utf-8")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "their branch")
        _git(other, "push", "-u", "origin", "feat/mine")
        _git(repo, "fetch", "origin")

        check = preflight.check_branch_not_taken()

        assert not check.ok
        assert "ведёт кто-то ещё" in check.detail


class TestChangelogBuffer:
    """Запись в `## Буфер` обязательна в каждом PR, и CI её не проверяет."""

    def test_buffer_section_stops_at_next_heading(self, preflight: ModuleType) -> None:
        """Секция буфера читается до следующего заголовка, а не до конца файла."""
        text = "# Changelog\n\n## Буфер\n\n- Fixed: моё (#1)\n\n## [Unreleased]\n\n- Added: чужое\n"

        assert preflight.buffer_section(text) == ["", "- Fixed: моё (#1)", ""]

    def test_added_lines_ignore_diff_headers(self, preflight: ModuleType) -> None:
        """`+++ b/CHANGELOG.md` — заголовок диффа, а не запись."""
        diff = "+++ b/CHANGELOG.md\n@@\n+- Fixed: запись (#7)\n-- Fixed: старое\n+не запись\n"

        assert preflight.added_changelog_lines(diff) == ["- Fixed: запись (#7)"]

    def test_missing_entry_is_caught(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """Правка кода без записи в буфере — провал проверки."""
        (repo / "module.py").write_text("x = 2\n", encoding="utf-8")
        _git(repo, "commit", "-am", "change code")

        check = preflight.check_changelog_buffer()

        assert not check.ok

    def test_entry_in_buffer_is_accepted(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """Строка, добавленная именно в буфер, закрывает требование."""
        (repo / "module.py").write_text("x = 2\n", encoding="utf-8")
        (repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Буфер (разобрать при релизе)\n\n"
            "- Fixed: правка (#997)\n\n## [Unreleased]\n",
            encoding="utf-8",
        )
        _git(repo, "commit", "-am", "change code with changelog")

        assert preflight.check_changelog_buffer().ok

    def test_survives_narrow_locale(self, repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
        """Узкая локаль не должна ломать сравнение русских строк.

        `text=True` декодирует вывод git **локальной** кодировкой: на Windows
        это cp1251/cp866, и русские строки диффа приезжают искажёнными — гейт
        не падает, а врёт, что записи в CHANGELOG нет. Воспроизводится на Linux
        через `LC_ALL=C` с отключённым UTF-8 mode: тот же механизм, которым
        узкую кодировку задаёт Windows, и ловится он только запуском настоящего
        процесса.
        """
        (repo / "module.py").write_text("x = 2\n", encoding="utf-8")
        (repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Буфер (разобрать при релизе)\n\n"
            "- Fixed: правка с кириллицей (#997)\n\n## [Unreleased]\n",
            encoding="utf-8",
        )
        _git(repo, "commit", "-am", "change with cyrillic changelog")
        probe = tmp_path / "probe.py"
        probe.write_text(
            "import importlib.util, pathlib, sys\n"
            "spec = importlib.util.spec_from_file_location('_pf', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules['_pf'] = module\n"
            "spec.loader.exec_module(module)\n"
            "module._ROOT = pathlib.Path(sys.argv[2])\n"
            "check = module.check_changelog_buffer()\n"
            "print('ok' if check.ok else 'buffer entry not found')\n"
            "sys.exit(0 if check.ok else 1)\n",
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONUTF8": "0",
            "PYTHONCOERCECLOCALE": "0",
        }

        result = subprocess.run(
            [sys.executable, str(probe), str(_SCRIPT), str(repo)],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, result.stdout + result.stderr

    def test_entry_outside_buffer_does_not_count(
        self, preflight: ModuleType, repo: pathlib.Path
    ) -> None:
        """Запись, уехавшая в `[Unreleased]`, буфер не закрывает."""
        (repo / "module.py").write_text("x = 2\n", encoding="utf-8")
        (repo / "CHANGELOG.md").write_text(
            "# Changelog\n\n## Буфер (разобрать при релизе)\n\n## [Unreleased]\n\n"
            "- Fixed: мимо буфера (#997)\n",
            encoding="utf-8",
        )
        _git(repo, "commit", "-am", "changelog in wrong section")

        assert not preflight.check_changelog_buffer().ok


class TestChangedNames:
    """Карта «кто ссылается на правку» — против молчаливой поломки чужих тестов."""

    def test_definitions_are_collected_from_both_sides(self, preflight: ModuleType) -> None:
        """Считаются и добавленные, и удалённые определения."""
        diff = "+def find_all_solution_files():\n-class OldRunner:\n+    x = 1\n--- a/f.py\n"

        assert preflight.changed_public_names(diff) == {"find_all_solution_files", "OldRunner"}

    def test_test_files_mentioning_the_name_are_listed(
        self, preflight: ModuleType, repo: pathlib.Path
    ) -> None:
        """Тест из другого файла находится по имени, а не по догадке о названии."""
        (repo / "tests").mkdir()
        (repo / "tests" / "test_elsewhere.py").write_text(
            "def test_x():\n    assert find_all_solution_files\n", encoding="utf-8"
        )
        (repo / "module.py").write_text("def find_all_solution_files():\n    return []\n", "utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "rename")

        check = preflight.check_tests_mentioning_changed_names()

        assert "test_elsewhere.py" in check.detail
        assert not check.blocking


class TestRunLockAndStamp:
    """Один прогон за раз и штамп проверенного коммита."""

    def test_fresh_lock_blocks_second_run(
        self, preflight: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Свежая блокировка = прогон идёт: второй pytest роняет тесты с subprocess."""
        lock = tmp_path / "preflight.lock"
        lock.write_text(json.dumps({"pid": 1, "at": time.time()}), encoding="utf-8")

        assert preflight.lock_is_active(lock)

    def test_stale_lock_is_ignored(self, preflight: ModuleType, tmp_path: pathlib.Path) -> None:
        """Забытая блокировка не должна блокировать работу навсегда."""
        lock = tmp_path / "preflight.lock"
        lock.write_text(json.dumps({"pid": 1, "at": time.time() - 10 * 60 * 60}), encoding="utf-8")

        assert not preflight.lock_is_active(lock)

    def test_missing_lock_is_free(self, preflight: ModuleType, tmp_path: pathlib.Path) -> None:
        """Нет файла — нет блокировки."""
        assert not preflight.lock_is_active(tmp_path / "nothing.lock")

    def test_stamp_covers_worktree_content(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """Штамп действителен, пока содержимое рабочего дерева не менялось."""
        preflight.write_stamp(repo, "abc123", tests=True)

        assert preflight.stamp_is_current(repo)

    def test_edit_after_run_invalidates_stamp(
        self, preflight: ModuleType, repo: pathlib.Path
    ) -> None:
        """Правка после прогона обесценивает штамп: проверяли не это."""
        preflight.write_stamp(repo, "abc123", tests=True)

        (repo / "module.py").write_text("x = 999\n", encoding="utf-8")

        assert not preflight.stamp_is_current(repo)

    def test_new_untracked_file_invalidates_stamp(
        self, preflight: ModuleType, repo: pathlib.Path
    ) -> None:
        """Новый файл рядом — тоже непроверенное состояние.

        Правка чаще всего приезжает именно так: новый модуль плюс новый тест,
        ещё не добавленные в индекс.
        """
        preflight.write_stamp(repo, "abc123", tests=True)

        (repo / "brand_new.py").write_text("y = 1\n", encoding="utf-8")

        assert not preflight.stamp_is_current(repo)

    def test_absent_stamp_never_matches(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """Без штампа пуш считается непроверенным."""
        assert not preflight.stamp_is_current(repo)


class TestPushGate:
    """pre-push хук: на origin уезжает только проверенный коммит."""

    def test_push_without_stamp_is_rejected(self, preflight: ModuleType) -> None:
        """Нет штампа — нет пуша: ровно тот случай, когда «забыл прогнать»."""
        assert preflight._gate_push() == 1

    def test_push_of_checked_content_passes(
        self, preflight: ModuleType, repo: pathlib.Path
    ) -> None:
        """Проверенное содержимое пушится."""
        preflight.write_stamp(repo, "abc123", tests=True)

        assert preflight._gate_push() == 0

    def test_commit_after_run_does_not_invalidate(
        self, preflight: ModuleType, repo: pathlib.Path
    ) -> None:
        """Коммит между прогоном и пушем штамп НЕ обесценивает.

        Дефект, найденный первым же живым применением: штамп привязывался к
        `HEAD`, а прогон всегда идёт ДО коммита — то есть хук отклонял пуш ровно
        того состояния, которое сам же и проверил. Гейт, который мешает при
        правильном порядке действий, обходят на второй день.
        """
        (repo / "module.py").write_text("x = 42\n", encoding="utf-8")
        preflight.write_stamp(repo, "irrelevant", tests=True)

        _git(repo, "commit", "-am", "commit checked content")

        assert preflight._gate_push() == 0

    def test_edit_after_run_blocks_push(self, preflight: ModuleType, repo: pathlib.Path) -> None:
        """А вот правка после прогона пуш закрывает."""
        preflight.write_stamp(repo, "abc123", tests=True)

        (repo / "module.py").write_text("x = 777\n", encoding="utf-8")

        assert preflight._gate_push() == 1

    def test_emergency_skip_is_explicit(
        self, preflight: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Аварийный выход существует, но требует явной переменной окружения."""
        monkeypatch.setenv(preflight._SKIP_ENV, "1")

        assert preflight._gate_push() == 0
