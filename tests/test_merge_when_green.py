"""Тесты scripts/merge_when_green.py — метка как согласие на мерж (issue #1303).

Метка `merge-when-green` означает «как позеленеет, мержи без меня». Проверяется
ровно то, что делает её согласием, а не автоматом: включаем только помеченным,
черновик и форк пропускаем, повторный проход ничего не ломает, а снятая метка
согласие **отзывает**.

Сеть подделывается на уровне ``gh_rest``: тесты описывают решения механизма, а
не HTTP.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS / "merge_when_green.py"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_merge_when_green", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def consent() -> ModuleType:
    """Свежий модуль механизма на каждый тест."""
    return _load_module()


class _FakeApi:
    """Подделка ``gh_rest`` в тех функциях, которые зовёт механизм."""

    def __init__(self, labelled: list[dict[str, Any]], pulls: dict[int, dict[str, Any]]) -> None:
        self.labelled = labelled
        self.pulls = pulls
        self.enabled: list[int] = []
        self.disabled: list[int] = []
        self.failing: set[int] = set()
        self.error: Any = None

    def issues_with_label(self, _repo: str, _label: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return self.labelled

    def pull(self, _repo: str, number: int, **_kwargs: Any) -> dict[str, Any]:
        return self.pulls[number]

    def enable_auto_merge(self, _repo: str, number: int, **_kwargs: Any) -> dict[str, Any]:
        if number in self.failing:
            raise self.error("GitHub отказал (422) на enablePullRequestAutoMerge")
        self.enabled.append(number)
        return {}

    def disable_auto_merge(self, _repo: str, number: int, **_kwargs: Any) -> dict[str, Any]:
        self.disabled.append(number)
        return {}


def _pr(number: int, **fields: Any) -> dict[str, Any]:
    """Объект PR глазами REST: по умолчанию обычный, без авто-мержа."""
    data: dict[str, Any] = {
        "number": number,
        "draft": False,
        "auto_merge": None,
        "head": {"repo": {"fork": False}},
    }
    data.update(fields)
    return data


def _wire(consent: ModuleType, monkeypatch: pytest.MonkeyPatch, api: _FakeApi) -> _FakeApi:
    """Подменить у механизма всё сетевое на подделку."""
    gh = consent.gh_rest
    api.error = gh.GitHubError
    monkeypatch.setattr(gh, "issues_with_label", api.issues_with_label)
    monkeypatch.setattr(gh, "pull", api.pull)
    monkeypatch.setattr(gh, "enable_auto_merge", api.enable_auto_merge)
    monkeypatch.setattr(gh, "disable_auto_merge", api.disable_auto_merge)
    return api


# ---------------------------------------------------------------------------
# Отбор: что вообще считается помеченным PR
# ---------------------------------------------------------------------------


def test_issues_are_not_pull_requests(consent: ModuleType) -> None:
    """Issue с той же меткой мержить нечего — в выборку не попадает."""
    items = [
        {"number": 10, "pull_request": {"url": "..."}},
        {"number": 11},  # обычный issue
    ]

    assert consent.pulls_awaiting_auto_merge(items) == [10]


def test_manual_pr_gets_auto_merge(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Помеченный PR получает авто-мерж — ровно то, ради чего метка заведена."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi([{"number": 1297, "pull_request": {}}], {1297: _pr(1297)}),
    )

    outcome = consent.enable_for_labelled("owner/repo")

    assert api.enabled == [1297]
    assert outcome.touched == [1297]


def test_draft_is_skipped(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Черновик пропускается: согласие ещё не выражено."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi([{"number": 5, "pull_request": {}}], {5: _pr(5, draft=True)}),
    )

    outcome = consent.enable_for_labelled("owner/repo")

    assert api.enabled == []
    assert any("черновик" in line for line in outcome.lines)


def test_fork_is_skipped(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR из форка ведёт внешний автор — включать за него авто-мерж не наше дело."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi(
            [{"number": 6, "pull_request": {}}],
            {6: _pr(6, head={"repo": {"fork": True}})},
        ),
    )

    outcome = consent.enable_for_labelled("owner/repo")

    assert api.enabled == []
    assert any("форка" in line for line in outcome.lines)


def test_already_enabled_is_left_alone(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторный проход ничего не делает — скрипт зовётся и по событию, и по расписанию."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi(
            [{"number": 7, "pull_request": {}}],
            {7: _pr(7, auto_merge={"merge_method": "squash"})},
        ),
    )

    outcome = consent.enable_for_labelled("owner/repo")

    assert api.enabled == []
    assert outcome.touched == []
    assert any("уже включён" in line for line in outcome.lines)


def test_failure_on_one_pr_does_not_stop_the_rest(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ на одном PR не лишает остальных — они помечены тем же согласием."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi(
            [{"number": 8, "pull_request": {}}, {"number": 9, "pull_request": {}}],
            {8: _pr(8), 9: _pr(9)},
        ),
    )
    api.failing = {8}

    outcome = consent.enable_for_labelled("owner/repo")

    assert api.enabled == [9]
    assert any("не включился" in line for line in outcome.lines)


def test_nothing_labelled_is_not_an_error(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Помеченных PR нет — «включать нечего», а не отказ."""
    _wire(consent, monkeypatch, _FakeApi([], {}))

    outcome = consent.enable_for_labelled("owner/repo")

    assert outcome.touched == []
    assert any("включать нечего" in line for line in outcome.lines)
    # `--no-default-consent`: здесь проверяется только включение авто-мержа,
    # расстановка меток по умолчанию (issue #1325) — предмет отдельных тестов.
    assert consent.main(["--repo", "owner/repo", "--no-default-consent"]) == 0


# ---------------------------------------------------------------------------
# Решение обратимо: снятая метка отзывает согласие
# ---------------------------------------------------------------------------


def test_removing_the_label_disables_auto_merge(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Метку сняли — авто-мерж выключен, PR остаётся на месте."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi([], {12: _pr(12, auto_merge={"merge_method": "squash"})}),
    )

    outcome = consent.disable_for("owner/repo", 12)

    assert api.disabled == [12]
    assert outcome.touched == [12]


def test_disabling_what_was_never_enabled_is_quiet(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Авто-мержа и не было — выключать нечего, это не ошибка."""
    api = _wire(consent, monkeypatch, _FakeApi([], {13: _pr(13)}))

    outcome = consent.disable_for("owner/repo", 13)

    assert api.disabled == []
    assert any("и не был включён" in line for line in outcome.lines)


def test_dry_run_changes_nothing(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--dry-run` не трогает ни включение, ни выключение."""
    api = _wire(
        consent,
        monkeypatch,
        _FakeApi([{"number": 14, "pull_request": {}}], {14: _pr(14)}),
    )

    outcome = consent.enable_for_labelled("owner/repo", dry_run=True)

    assert api.enabled == []
    assert outcome.touched == [14]


# ---------------------------------------------------------------------------
# Коды возврата
# ---------------------------------------------------------------------------


def test_exhausted_quota_means_wait(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Исчерпанная квота — «ждать»: повторять сейчас бессмысленно."""
    gh = consent.gh_rest

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise gh.RateLimited("лимит исчерпан, сброс в 12:00")

    monkeypatch.setattr(gh, "issues_with_label", _raise)

    assert consent.main(["--repo", "owner/repo", "--no-default-consent"]) == gh.EXIT_WAIT


def test_unreadable_list_is_a_failure(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Список PR не прочитался — вот это и есть «механизм сломан»."""
    gh = consent.gh_rest

    def _raise(*_a: Any, **_k: Any) -> Any:
        raise gh.GitHubError("GitHub отказал (500)")

    monkeypatch.setattr(gh, "issues_with_label", _raise)

    assert consent.main(["--repo", "owner/repo", "--no-default-consent"]) == gh.EXIT_FAIL


# ---------------------------------------------------------------------------
# issue #1325 — согласие по умолчанию и стоп-метка
#
# Умолчание перевёрнуто: молчание означает «мержить по зелёному», а человек
# ВЫРАЖАЕТ несогласие. Ловушка, ради которой заведена стоп-метка: механизм
# идемпотентен и ходит по расписанию, поэтому снятая руками метка вернулась бы
# следующим проходом — отличить «ещё не ставили» от «сняли» по состоянию PR
# нельзя.
# ---------------------------------------------------------------------------


class _FakeRepo:
    """Подделка списка PR и операций с метками."""

    def __init__(self, pulls: list[dict[str, Any]]) -> None:
        self.pulls = pulls
        self.added: list[tuple[int, str]] = []
        self.removed: list[tuple[int, str]] = []
        self.ensured: list[str] = []

    def request(self, _method: str, _path: str, **_kwargs: Any) -> Any:
        class _Response:
            data = self.pulls

        return _Response()

    def ensure_label(self, _repo: str, name: str, **_kwargs: Any) -> bool:
        self.ensured.append(name)
        return True

    def add_labels(self, _repo: str, number: int, labels: list[str], **_kwargs: Any) -> list[str]:
        self.added.extend((number, label) for label in labels)
        return labels

    def remove_label(self, _repo: str, number: int, label: str, **_kwargs: Any) -> bool:
        self.removed.append((number, label))
        return True


def _open_pr(number: int, *, labels: tuple[str, ...] = (), **fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "number": number,
        "draft": False,
        "head": {"repo": {"fork": False}},
        "labels": [{"name": name} for name in labels],
    }
    data.update(fields)
    return data


def _wire_repo(consent: ModuleType, monkeypatch: pytest.MonkeyPatch, repo: _FakeRepo) -> _FakeRepo:
    gh = consent.gh_rest
    monkeypatch.setattr(gh, "request", repo.request)
    monkeypatch.setattr(gh, "ensure_label", repo.ensure_label)
    monkeypatch.setattr(gh, "add_labels", repo.add_labels)
    monkeypatch.setattr(gh, "remove_label", repo.remove_label)
    return repo


def test_every_open_pr_gets_consent_by_default(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Молчание означает «мержить»: метка ставится сама."""
    repo = _wire_repo(consent, monkeypatch, _FakeRepo([_open_pr(1), _open_pr(2)]))

    outcome = consent.apply_default_consent("owner/repo")

    assert repo.added == [(1, consent.LABEL), (2, consent.LABEL)]
    assert outcome.touched == [1, 2]


def test_hold_label_blocks_consent(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Стоп-метка сильнее умолчания — согласие не выдаётся."""
    repo = _wire_repo(consent, monkeypatch, _FakeRepo([_open_pr(3, labels=(consent.HOLD_LABEL,))]))

    consent.apply_default_consent("owner/repo")

    assert repo.added == []


def test_hold_label_revokes_existing_consent(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hold` поверх уже стоящего согласия его снимает — решение обратимо."""
    repo = _wire_repo(
        consent,
        monkeypatch,
        _FakeRepo([_open_pr(4, labels=(consent.HOLD_LABEL, consent.LABEL))]),
    )

    outcome = consent.apply_default_consent("owner/repo")

    assert repo.removed == [(4, consent.LABEL)]
    assert outcome.touched == [4]


def test_draft_fork_and_conflict_are_left_alone(
    consent: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Черновик, форк и конфликтный PR согласия по умолчанию не получают."""
    repo = _wire_repo(
        consent,
        monkeypatch,
        _FakeRepo(
            [
                _open_pr(5, draft=True),
                _open_pr(6, head={"repo": {"fork": True}}),
                _open_pr(7, labels=(consent.CONFLICT_LABEL,)),
            ]
        ),
    )

    consent.apply_default_consent("owner/repo")

    assert repo.added == []


def test_consent_is_not_duplicated(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Метка уже стоит — повторный обход её не переставляет."""
    repo = _wire_repo(consent, monkeypatch, _FakeRepo([_open_pr(8, labels=(consent.LABEL,))]))

    outcome = consent.apply_default_consent("owner/repo")

    assert repo.added == []
    assert outcome.touched == []


def test_dry_run_marks_nothing(consent: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--dry-run` не трогает ни меток, ни их создание."""
    repo = _wire_repo(consent, monkeypatch, _FakeRepo([_open_pr(9)]))

    outcome = consent.apply_default_consent("owner/repo", dry_run=True)

    assert repo.added == [] and repo.ensured == []
    assert outcome.touched == [9]
