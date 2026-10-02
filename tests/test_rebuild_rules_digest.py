"""Тесты scripts/rebuild_rules_digest.py — пересборка производного прогоном.

Дайджест правил — производное от чужого каталога, и пересобирало его окно:
обход находил отставание, человек открывал PR. Механику отдали прогону, а
решение «можно ли везти» — этому скрипту, потому что шаги workflow не
тестируются, а он тестируется.

Средний исход и есть предмет: пересобранный дайджест бывает НЕПРИГОДЕН к
отправке. Правило, появившееся в каталоге, попадает в дайджест, а ответа по нему
ещё нет — и обязательная проверка краснеет. Запушив такое, прогон открыл бы
красный PR и запер им очередь мержа, то есть сломал бы ровно то, ради чего
заводился.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "rebuild_rules_digest.py"


@pytest.fixture
def rebuilder() -> ModuleType:
    """Свежий модуль на каждый тест."""
    spec = importlib.util.spec_from_file_location("_rebuild_rules_digest", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_runs(
    rebuilder: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    diff: int,
    guard: int = 0,
    generator: int = 0,
) -> None:
    """Подменить запуски: генераторы, `git diff` и сторож дайджеста."""

    def run(argv: list[str], *, root: pathlib.Path) -> subprocess.CompletedProcess[str]:
        joined = " ".join(argv)
        if "generate_rules" in joined:
            code = generator
        elif "git" in argv[0]:
            code = diff
        else:
            code = guard
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="упало")

    monkeypatch.setattr(rebuilder, "_run", run)


def test_no_change_is_nothing_to_ship(
    rebuilder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Производное совпадает с каталогом — везти нечего."""
    _fake_runs(rebuilder, monkeypatch, diff=0)

    assert rebuilder.rebuild_verdict(tmp_path / "каталог") == "nothing"


def test_a_consistent_rebuild_is_ready(
    rebuilder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Файлы изменились и сходятся с ответом — ветку можно пушить."""
    _fake_runs(rebuilder, monkeypatch, diff=1, guard=0)

    assert rebuilder.rebuild_verdict(tmp_path / "каталог") == "ready"


def test_missing_answers_block_the_push(
    rebuilder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Нет ответа по новому правилу — не везём, и это не ошибка.

    Обязательная проверка покраснела бы на «правило N есть в дайджесте, но
    ответа по нему нет», и прогон запер бы очередь мержа красным PR.
    """
    _fake_runs(rebuilder, monkeypatch, diff=1, guard=1)

    assert rebuilder.rebuild_verdict(tmp_path / "каталог") == "blocked"


def test_a_failed_generator_names_the_subject(
    rebuilder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Третий исход называет ПРЕДМЕТ отказа, а не только его причину."""
    _fake_runs(rebuilder, monkeypatch, diff=0, generator=1)

    with pytest.raises(RuntimeError, match="generate_rules_digest.py"):
        rebuilder.rebuild_verdict(tmp_path / "каталог")


def test_the_cli_prints_one_word(
    rebuilder: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
) -> None:
    """Вердикт читается шагом workflow как есть, без разбора текста."""
    _fake_runs(rebuilder, monkeypatch, diff=1, guard=1)

    assert rebuilder.main(["--catalogue", str(tmp_path / "каталог")]) == 0
    assert capsys.readouterr().out.strip() in rebuilder.VERDICTS


def test_a_broken_run_returns_the_third_code(
    rebuilder: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """«Не отработала» — отдельный код, а не «везти нечего»."""
    _fake_runs(rebuilder, monkeypatch, diff=0, generator=1)

    assert rebuilder.main(["--catalogue", str(tmp_path / "каталог")]) == rebuilder.EXIT_BROKEN
