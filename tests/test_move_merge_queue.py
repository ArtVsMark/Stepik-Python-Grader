"""Тесты scripts/move_merge_queue.py — очередь идёт мимо конфликтов (issue #1313).

Прежний мувер падал на первом же конфликтном PR и держал очередь целиком:
три падения подряд, 14 часов простоя, четыре здоровых PR рядом. Здесь
проверяется ровно противоположное поведение — конфликтный помечается и
обходится, здоровый обновляется, прогон остаётся зелёным.

Сеть подделывается на уровне модуля ``gh_rest``: тесты описывают решения
мувера, а не HTTP.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS / "move_merge_queue.py"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_move_merge_queue", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mover() -> ModuleType:
    """Свежий модуль мувера на каждый тест."""
    return _load_module()


class _FakeApi:
    """Подделка ``gh_rest`` ровно в тех функциях, которые зовёт мувер."""

    def __init__(
        self,
        ready: list[tuple[int, bool]],
        states: dict[int, Any],
        *,
        failing: set[int] | None = None,
    ) -> None:
        self.ready = ready
        self.states = states
        self.failing = failing or set()
        self.labelled: list[int] = []
        self.unlabelled: list[int] = []
        self.updated: list[int] = []
        self.created_labels: list[str] = []
        self.sleeps: list[float] = []

    # --- то, что мувер спрашивает ---------------------------------------

    def queue(self, gh: ModuleType) -> Any:
        entries = tuple(
            gh.QueueEntry(number=number, title=f"PR {number}", ready=True, fork=fork)
            for number, fork in self.ready
        )
        return gh.QueueReport(ready=entries, waiting=(), main_busy=False, main_red=False)

    def pull(self, _repo: str, number: int, **_kwargs: Any) -> dict[str, Any]:
        state = self.states[number]
        # Список значений — состояние меняется от запроса к запросу (гонка
        # `unknown` → настоящее состояние).
        if isinstance(state, list):
            value = state.pop(0) if len(state) > 1 else state[0]
        else:
            value = state
        return {"mergeable_state": value}

    # --- то, что мувер меняет -------------------------------------------

    def update_branch(self, _repo: str, number: int, **_kwargs: Any) -> dict[str, Any]:
        if number in self.failing:
            raise self.error("GitHub отказал (422) на update-branch")
        self.updated.append(number)
        return {}

    def ensure_label(self, _repo: str, name: str, **_kwargs: Any) -> bool:
        self.created_labels.append(name)
        return True

    def add_labels(self, _repo: str, number: int, _labels: list[str], **_kwargs: Any) -> list[str]:
        self.labelled.append(number)
        return _labels

    def remove_label(self, _repo: str, number: int, _label: str, **_kwargs: Any) -> bool:
        self.unlabelled.append(number)
        return True

    error: Any = None


def _wire(mover: ModuleType, monkeypatch: pytest.MonkeyPatch, api: _FakeApi) -> _FakeApi:
    """Подменить у мувера всё сетевое на подделку."""
    gh = mover.gh_rest
    api.error = gh.GitHubError
    monkeypatch.setattr(gh, "merge_queue", lambda *a, **k: api.queue(gh))
    monkeypatch.setattr(gh, "pull", api.pull)
    monkeypatch.setattr(gh, "update_branch", api.update_branch)
    monkeypatch.setattr(gh, "ensure_label", api.ensure_label)
    monkeypatch.setattr(gh, "add_labels", api.add_labels)
    monkeypatch.setattr(gh, "remove_label", api.remove_label)
    return api


# ---------------------------------------------------------------------------
# Главное: конфликт не роняет очередь
# ---------------------------------------------------------------------------


def test_conflicted_head_is_skipped_and_marked(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Голова очереди в конфликте — помечена, обновлён следующий."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(ready=[(1304, False), (1305, False)], states={1304: "dirty", 1305: "clean"}),
    )

    outcome = mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert outcome.updated == 1305, "здоровый PR обязан подхватиться"
    assert api.updated == [1305]
    assert api.labelled == [1304], "конфликтный PR обязан быть помечен, а не пропущен молча"
    assert mover.CONFLICT_LABEL in api.created_labels


def test_every_pr_conflicted_is_still_a_green_run(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Очередь целиком из конфликтных: никого не обновили, но прогон зелёный."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(ready=[(1, False), (2, False)], states={1: "dirty", 2: "dirty"}),
    )

    outcome = mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert outcome.updated is None
    assert api.updated == []
    assert api.labelled == [1, 2]
    assert mover.main(["--repo", "owner/repo"]) == 0


def test_failed_update_does_not_stop_the_queue(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ `update-branch` по любой причине — пометить и идти дальше."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(
            ready=[(10, False), (11, False)],
            states={10: "clean", 11: "clean"},
            failing={10},
        ),
    )

    outcome = mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert outcome.updated == 11
    assert api.labelled == [10], "упавший PR помечается, иначе очередь обходила бы его молча"
    assert any("не прошло" in line for line in outcome.lines)


def test_fork_is_skipped_without_a_label(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR из форка не наш — ветку обновляет владелец, метка конфликта тут ни при чём."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(ready=[(20, True), (21, False)], states={20: "clean", 21: "clean"}),
    )

    outcome = mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert outcome.updated == 21
    assert api.labelled == []
    assert any("форка" in line for line in outcome.lines)


# ---------------------------------------------------------------------------
# `unknown` — это «GitHub ещё считает», а не «конфликт»
# ---------------------------------------------------------------------------


def test_unknown_state_is_re_read_before_judging(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Здоровый PR не попадает в пропущенные из-за асинхронного расчёта."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(ready=[(30, False)], states={30: ["unknown", "clean"]}),
    )

    outcome = mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert outcome.updated == 30, "перечитать состояние обязаны, иначе метка достанется здоровому"
    assert api.labelled == []
    assert api.sleeps, "между попытками нужна пауза — иначе перечитываем тот же ответ"


def test_state_that_never_settles_is_left_alone(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Состояние так и не досчитано — не трогаем и не метим, вернёмся позже."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(ready=[(40, False)], states={40: "unknown"}),
    )

    outcome = mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert outcome.updated is None
    assert api.labelled == [], "«ещё считается» — не повод объявлять PR конфликтным"
    assert any("не досчитал" in line for line in outcome.lines)


# ---------------------------------------------------------------------------
# Метка не переживает свою причину
# ---------------------------------------------------------------------------


def test_successful_update_clears_the_conflict_mark(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Конфликт разрешён — метка снимается тем же прогоном, что и обновление."""
    api = _wire(mover, monkeypatch, _FakeApi(ready=[(50, False)], states={50: "clean"}))

    mover.move_queue("owner/repo", sleep=api.sleeps.append)

    assert api.unlabelled == [50]


def test_empty_queue_is_not_an_error(mover: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустая очередь — «двигать нечего», а не отказ."""
    _wire(mover, monkeypatch, _FakeApi(ready=[], states={}))

    outcome = mover.move_queue("owner/repo")

    assert outcome.updated is None
    assert any("двигать нечего" in line for line in outcome.lines)
    assert mover.main(["--repo", "owner/repo"]) == 0


def test_dry_run_changes_nothing(mover: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--dry-run` не ставит меток и не обновляет ветки."""
    api = _wire(
        mover,
        monkeypatch,
        _FakeApi(ready=[(60, False), (61, False)], states={60: "dirty", 61: "clean"}),
    )

    outcome = mover.move_queue("owner/repo", dry_run=True, sleep=api.sleeps.append)

    assert outcome.updated == 61
    assert api.updated == []
    assert api.labelled == []


# ---------------------------------------------------------------------------
# Коды возврата: красный означает «мувер сломан»
# ---------------------------------------------------------------------------


def test_exhausted_quota_means_wait_not_failure(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Исчерпанная квота — код «ждать»: повторять сейчас бессмысленно."""
    gh = mover.gh_rest

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise gh.RateLimited("лимит исчерпан, сброс в 12:00")

    monkeypatch.setattr(gh, "merge_queue", _raise)

    assert mover.main(["--repo", "owner/repo"]) == gh.EXIT_WAIT


def test_unreadable_queue_is_a_real_failure(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Очередь не прочиталась — вот это и есть «механизм сломан»."""
    gh = mover.gh_rest

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise gh.GitHubError("GitHub отказал (500)")

    monkeypatch.setattr(gh, "merge_queue", _raise)

    assert mover.main(["--repo", "owner/repo"]) == gh.EXIT_FAIL


def test_red_main_says_the_queue_is_frozen(
    mover: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """issue #1326: при красной базе «двигать нечего» звучало бы как «всё спокойно»."""

    class _RedMain(_FakeApi):
        def queue(self, gh: ModuleType) -> Any:
            return gh.QueueReport(ready=(), waiting=(), main_busy=False, main_red=True)

    _wire(mover, monkeypatch, _RedMain(ready=[], states={}))

    outcome = mover.move_queue("owner/repo")

    assert outcome.updated is None
    assert any("заморожена" in line for line in outcome.lines)
