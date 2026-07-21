"""Tests for scripts/version.py — версионирование по схеме проекта (issue #68).

Схема (CONTRIBUTING.md §Версионирование) — НЕ SemVer: MAJOR.MINOR из тега
``vX.Y.0``, PATCH = число first-parent коммитов (≈ смерженных PR) после тега,
БЕЗ badge-бота (``chore(ci): update badges``, issue #231); до первого тега —
fallback на MAJOR.MINOR из метаданных установленного пакета (setuptools-scm,
issue #557) + то же first-parent число без бота.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "version.py"

# X.Y.Z, где каждая часть — неотрицательное целое (совпадает по форме с PEP 440
# release-сегментом, хотя схема проекта трактует Z как счётчик коммитов).
_XYZ = re.compile(r"^\d+\.\d+\.\d+$")


def _load_module() -> ModuleType:
    """Загрузить scripts/version.py как модуль (папка scripts/ не на sys.path)."""
    spec = importlib.util.spec_from_file_location("_version_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_version_matches_scheme() -> None:
    """project_version() возвращает строку вида X.Y.Z."""
    version = _load_module().project_version()
    assert _XYZ.match(version), version


def test_version_script_cli_prints_version() -> None:
    """`python scripts/version.py` печатает валидную версию и завершается 0
    (acceptance-критерий issue #68)."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0
    assert _XYZ.match(result.stdout.strip()), result.stdout


def test_tagged_path_parses_commits_as_patch(monkeypatch) -> None:
    """При наличии тега PATCH = число коммитов после него (git rev-list)."""
    module = _load_module()

    def fake_git(*args: str) -> str | None:
        if args[:1] == ("describe",):
            return "v1.2.0"
        if args[:2] == ("rev-list", "--count"):
            return "17"
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    assert module.project_version() == "1.2.17"


def test_fallback_when_no_tags(monkeypatch) -> None:
    """До первого тега (git describe → None) — MAJOR.MINOR из метаданных пакета
    (setuptools-scm), НЕ деградирует в 0.0; PATCH = число коммитов (issue #557).

    Метадату мокаем детерминированно: без тегов в клоне setuptools-scm и сам дал бы
    ``0.0`` — fix проверяем на реалистичной ``X.Y.0.postN`` из установки, где теги
    были (напр. wheel из PyPI рядом с shallow git-клоном без тегов).
    """
    module = _load_module()

    def fake_git(*args: str) -> str | None:
        if args[:1] == ("describe",):
            return None  # тегов ещё нет
        if args[:2] == ("rev-list", "--count"):
            return "42"
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    monkeypatch.setattr(module, "_dist_version", lambda _name: "1.8.0.post5+gabc123")

    version = module.project_version()
    # Регрессия #557: прежде fallback читал удалённый [project].version и всегда
    # давал 0.0.N (маскировалось ассертом только на суффикс). Теперь MAJOR.MINOR
    # берётся из метаданных: 1.8 из "1.8.0.post5+...", PATCH=42.
    assert version == "1.8.42", version
    assert not version.startswith("0.0."), version
    assert _XYZ.match(version), version


def test_major_minor_from_metadata_parses_scm_version(monkeypatch) -> None:
    """``X.Y.0.postN+g<hash>`` (формат post-release setuptools-scm) → (MAJOR, MINOR)."""
    module = _load_module()
    monkeypatch.setattr(module, "_dist_version", lambda _name: "2.5.0.post3+gdeadbee")
    assert module._major_minor_from_metadata() == ("2", "5")


def test_major_minor_from_metadata_missing_package(monkeypatch) -> None:
    """Пакет не установлен → ('0','0') (последний резерв, issue #557)."""
    module = _load_module()

    def _raise(_name: str) -> str:
        raise module.PackageNotFoundError(_name)

    monkeypatch.setattr(module, "_dist_version", _raise)
    assert module._major_minor_from_metadata() == ("0", "0")


def test_patch_count_excludes_badge_bot_commits(monkeypatch) -> None:
    """PATCH-счётчик исключает chore(ci): update badges коммиты (issue #231):
    rev-list вызывается с --invert-grep/--grep/--fixed-strings на их подстроку,
    а не просто считает всё в диапазоне."""
    module = _load_module()
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str | None:
        calls.append(args)
        if args[:1] == ("describe",):
            return "v2.0.0"
        if args[:2] == ("rev-list", "--count"):
            return "5"
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    assert module.project_version() == "2.0.5"

    rev_list_call = next(c for c in calls if c[:2] == ("rev-list", "--count"))
    assert "v2.0.0..HEAD" in rev_list_call
    assert "--invert-grep" in rev_list_call
    assert "--fixed-strings" in rev_list_call
    grep_index = rev_list_call.index("--grep")
    assert "update badges" in rev_list_call[grep_index + 1]


def test_patch_count_uses_first_parent(monkeypatch) -> None:
    """PATCH считается по first-parent линии — один смерженный PR = один коммит,
    без внутренних коммитов PR и merge-дублей (объективная метрика «число
    принятых изменений»)."""
    module = _load_module()
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str | None:
        calls.append(args)
        if args[:1] == ("describe",):
            return "v2.0.0"
        if args[:2] == ("rev-list", "--count"):
            return "3"
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    assert module.project_version() == "2.0.3"

    rev_list_call = next(c for c in calls if c[:2] == ("rev-list", "--count"))
    assert "--first-parent" in rev_list_call
    assert "v2.0.0..HEAD" in rev_list_call
