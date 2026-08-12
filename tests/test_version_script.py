"""Tests for scripts/version.py — версионирование по схеме проекта (issue #68).

Схема (CONTRIBUTING.md §Версионирование) — НЕ SemVer: MAJOR.MINOR из тега
``vX.Y.0``, PATCH = число ПРИНЯТЫХ изменений после тега. Изменение опознаётся по
номеру PR (``(#NNNN)``), а не по положению в графе истории (issue #1042);
коммиты без номера считаются с first-parent линии, без badge-бота
(``chore(ci): update badges``, issue #231) и склеивающих мержей ``git pull``.
До первого тега — fallback на MAJOR.MINOR из метаданных установленного пакета
(setuptools-scm, issue #557).

Ключевые сценарии issue #1042 проверяются на НАСТОЯЩЕМ git-репозитории
(``_build_repo``), а не на моках ``_git``: прежний дефект жил именно в том, как
git обходит граф, и любой мок этот обход подменял — то есть маскировал дефект.
"""

from __future__ import annotations

import importlib.util
import os
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


# ── настоящий git-репозиторий для сценариев issue #1042 ────────────────────


def _git_env() -> dict[str, str]:
    """Окружение с фиксированным автором — иначе git падает без user.email."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )
    return env


def _run(repo: Path, *args: str) -> str:
    """Выполнить git в repo; падение теста несёт stderr, а не голый returncode."""
    result = subprocess.run(
        ["git", *args], cwd=repo, env=_git_env(), capture_output=True, text=True
    )
    assert result.returncode == 0, f"git {' '.join(args)} → {result.stderr}"
    return result.stdout.strip()


def _commit(repo: Path, name: str, subject: str) -> None:
    """Создать файл и закоммитить его с заданной темой."""
    (repo / name).write_text(name, encoding="utf-8")
    _run(repo, "add", name)
    _run(repo, "commit", "-q", "-m", subject)


def _patch_of(repo: Path) -> int:
    """PATCH, посчитанный скриптом в этом репозитории."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], cwd=repo, env=_git_env(), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip().split(".")[-1])


def _build_repo(tmp_path: Path) -> Path:
    """Репозиторий с тегом ``v1.10.0`` на первом коммите."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _commit(repo, "base", "init")
    _run(repo, "tag", "v1.10.0")
    return repo


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
        if args[0] == "log":
            return "\n".join(f"fix(x): изменение {i} (#{100 + i})" for i in range(17))
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
        if args[0] == "log":
            return "\n".join(f"fix(x): изменение {i} (#{200 + i})" for i in range(42))
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
    """PATCH-счётчик не считает ``chore(ci): update badges`` (issue #231).

    С issue #1042 бот отсеивается двумя независимыми способами: у его коммитов
    нет номера PR, и они явно отброшены в ``_is_countable_unnumbered``. Второе
    существенно: бот коммитит прямо в main, то есть попадает на first-parent
    линию, откуда берутся коммиты без номера.
    """
    module = _load_module()

    def fake_git(*args: str) -> str | None:
        if args[:1] == ("describe",):
            return "v2.0.0"
        if args[0] == "log":
            return (
                "fix(a): раз (#11)\n"
                "chore(ci): update badges [skip ci]\n"
                "fix(b): два (#12)\n"
                "chore(ci): update badges [skip ci]"
            )
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    assert module.project_version() == "2.0.2"
    assert module._is_countable_unnumbered("chore(ci): update badges [skip ci]") is False


def test_sync_merge_not_counted_but_feature_merge_is() -> None:
    """Склейка ``git pull`` — не изменение; мерж ветки-фичи — изменение (#1042)."""
    module = _load_module()
    assert (
        module._is_countable_unnumbered("Merge branch 'main' of https://example.invalid/r") is False
    )
    assert module._is_countable_unnumbered("Merge remote-tracking branch 'origin/main'") is False
    assert module._is_countable_unnumbered("Merge branch 'feat'") is True


def test_pr_numbers_from_both_merge_forms() -> None:
    """Номер PR читается и из squash-темы ``(#NNNN)``, и из ``Merge pull request``."""
    module = _load_module()
    numbers = module._pr_numbers(
        [
            "fix(a): squash-мерж (#101)",
            "Merge pull request #102 from user/branch",
            "fix(b): тот же PR ещё раз (#101)",
        ]
    )
    assert numbers == {"101", "102"}


def test_no_tags_warns_instead_of_silent_zero_zero(monkeypatch, capsys) -> None:
    """Клон без тегов не выдаёт правдоподобное ``0.0.N`` молча (issue #1042).

    Так клонирует облачная сессия и ``actions/checkout`` без ``fetch-depth: 0``:
    ``git describe`` падает, метаданные тегов тоже не видели — версия неполна, и
    это должно быть видно. Предупреждение идёт в stderr, чтобы stdout остался
    чистой версией для бейджа.
    """
    module = _load_module()
    monkeypatch.setattr(module, "_git", lambda *args: None if args[:1] == ("describe",) else "")
    monkeypatch.setattr(module, "_dist_version", lambda _name: "0.0.post64+gabc1234")

    version = module.project_version()
    assert version.startswith("0.0."), version
    assert "git fetch --tags" in capsys.readouterr().err


def test_unnumbered_commits_counted_from_first_parent_only(monkeypatch) -> None:
    """Коммиты БЕЗ номера PR берутся только с first-parent линии (issue #1042).

    Иначе внутренние коммиты слитой ветки (номера у них нет) считались бы
    поштучно, и дробление PR на коммиты снова завышало бы счётчик — ровно то,
    от чего защищал прежний ``--first-parent``.
    """
    module = _load_module()
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str | None:
        calls.append(args)
        if args[:1] == ("describe",):
            return "v2.0.0"
        if args[0] == "log" and "--first-parent" in args:
            return "прямой коммит в main"
        if args[0] == "log":
            return "fix(a): раз (#11)\nвнутренний коммит ветки\nfix(b): два (#12)"
        return None

    monkeypatch.setattr(module, "_git", fake_git)
    # Два номера + один прямой коммит на first-parent. «внутренний коммит ветки»
    # виден в общей выдаче, но не на first-parent линии — и не считается.
    assert module.project_version() == "2.0.3"

    first_parent_calls = [c for c in calls if c[0] == "log" and "--first-parent" in c]
    assert first_parent_calls, calls


# ── регрессии issue #1042 на настоящем git-репозитории ─────────────────────


def test_pull_merge_keeps_changes_from_remote(tmp_path: Path) -> None:
    """Изменения, пришедшие через ``git pull`` merge'ом, попадают в PATCH.

    Раньше ``--first-parent`` шёл по локальной линии, а всё пришедшее с GitHub
    оказывалось ВТОРЫМ родителем merge-коммита и в счёт не попадало: два
    принятых PR + один локальный коммит давали 2 вместо 3. Плюс сама склейка
    ``Merge branch 'main' of ...`` считалась за изменение, хотя своего
    содержимого не несёт.
    """
    repo = _build_repo(tmp_path)

    # «GitHub»: два squash-мержа PR в отдельной линии.
    _run(repo, "checkout", "-q", "-b", "remote-line")
    _commit(repo, "a", "fix(x): первое (#101)")
    _commit(repo, "b", "fix(y): второе (#102)")

    # Локально: свой коммит и pull merge'ом поверх него.
    _run(repo, "checkout", "-q", "main")
    _commit(repo, "c", "fix(z): локальное")
    _run(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "Merge branch 'main' of https://example.invalid/r",
        "remote-line",
    )

    assert _patch_of(repo) == 3


def test_feature_merge_counts_as_single_change(tmp_path: Path) -> None:
    """Мерж ветки из трёх коммитов — одно принятое изменение, а не три.

    Guard против «починки» через ``--no-merges``: она вернула бы потерянные
    коммиты, но начала бы считать дробление PR (проверено — 6 вместо 4).
    """
    repo = _build_repo(tmp_path)
    _run(repo, "checkout", "-q", "-b", "feat")
    for i in (1, 2, 3):
        _commit(repo, f"s{i}", f"wip {i}")
    _run(repo, "checkout", "-q", "main")
    _run(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'feat'", "feat")

    assert _patch_of(repo) == 1


def test_same_pr_number_counted_once(tmp_path: Path) -> None:
    """Одно изменение, попавшее в историю дважды под одним номером, — один раз.

    Так выглядит задваивание при мерже с локальной машины: своя версия коммита
    и пришедшая с GitHub несут один и тот же ``(#NNNN)``.
    """
    repo = _build_repo(tmp_path)
    _commit(repo, "x", "fix(v): своё (#104)")
    _commit(repo, "y", "fix(v): своё же, пришло с гита (#104)")

    assert _patch_of(repo) == 1


def test_badge_bot_and_sync_merges_not_counted(tmp_path: Path) -> None:
    """Badge-коммиты бота и склейки ``git pull`` не считаются изменениями."""
    repo = _build_repo(tmp_path)
    _commit(repo, "real", "fix(q): настоящее (#105)")
    _commit(repo, "badge", "chore(ci): update badges [skip ci]")
    _run(repo, "checkout", "-q", "-b", "other")
    _commit(repo, "o", "fix(r): в другой линии (#106)")
    _run(repo, "checkout", "-q", "main")
    _run(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "Merge remote-tracking branch 'origin/main'",
        "other",
    )

    # Два номера (#105, #106); badge-коммит и склейка — не изменения.
    assert _patch_of(repo) == 2
