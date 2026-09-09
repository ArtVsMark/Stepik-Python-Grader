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
from types import ModuleType, SimpleNamespace
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


class TestFreezeDecision:
    """Красная база замораживает очередь — механизмом, а не памятью (#1510)."""

    def test_a_red_base_freezes_an_ordinary_pull(self) -> None:
        module = _load_module()

        assert module.freeze_decision([], base_red=True) == "set"

    def test_the_fixing_pull_is_the_exception(self) -> None:
        """`blocker` — тот, ради кого очередь и двигается при красной базе."""
        module = _load_module()

        assert module.freeze_decision([module.BLOCKER_LABEL], base_red=True) != "set"

    def test_a_green_base_lifts_the_freeze(self) -> None:
        """Снимает ТОТ ЖЕ механизм, что и поставил.

        Метка, которую ставит автомат, а снимает человек, — заморозка
        навсегда: через сутки никто не вспомнит, чья она.
        """
        module = _load_module()

        assert module.freeze_decision([module.FREEZE_LABEL], base_red=False) == "clear"

    def test_the_fixing_pull_is_thawed_even_on_a_red_base(self) -> None:
        """Поставили `blocker` уже замороженному — заморозка уходит сразу."""
        module = _load_module()

        labels = [module.FREEZE_LABEL, module.BLOCKER_LABEL]

        assert module.freeze_decision(labels, base_red=True) == "clear"

    def test_nothing_to_change_is_not_a_write(self) -> None:
        """Идемпотентность: механизм ходит по расписанию, а не однократно."""
        module = _load_module()

        assert module.freeze_decision([module.FREEZE_LABEL], base_red=True) == "keep"
        assert module.freeze_decision([], base_red=False) == "keep"

    def test_a_human_hold_is_not_the_freeze(self) -> None:
        """Главное разграничение задачи: две метки — две разные причины.

        `hold` — решение человека, заморозка — состояние конвейера. Механизм
        не путает их: `hold` он не ставит и не снимает вовсе, а на решение о
        заморозке она не влияет.
        """
        module = _load_module()

        assert module.FREEZE_LABEL != module.HOLD_LABEL
        assert module.freeze_decision([module.HOLD_LABEL], base_red=True) == "set"
        assert module.freeze_decision([module.HOLD_LABEL], base_red=False) == "keep"


class TestFreezeIsEnforced:
    """Метка не только помечает — она снимает согласие и авто-мерж."""

    @staticmethod
    def _pulls(*labels_per_pull: list[str]) -> list[dict[str, Any]]:
        return [
            {"number": 100 + index, "labels": [{"name": name} for name in labels]}
            for index, labels in enumerate(labels_per_pull)
        ]

    def _wire(
        self,
        module: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        *,
        red: bool,
        pulls: list[dict[str, Any]],
    ) -> dict[str, list[Any]]:
        """Подделать сеть; вернуть журнал сделанного."""
        done: dict[str, list[Any]] = {"added": [], "removed": [], "disabled": []}
        monkeypatch.setattr(
            module.gh_rest,
            "main_run",
            lambda *_a, **_k: {
                "workflow_runs": [
                    {"status": "completed", "conclusion": "failure" if red else "success"}
                ]
            },
        )
        monkeypatch.setattr(
            module.gh_rest, "request", lambda *_a, **_k: SimpleNamespace(data=pulls)
        )
        monkeypatch.setattr(module.gh_rest, "ensure_label", lambda *_a, **_k: None)
        monkeypatch.setattr(
            module.gh_rest,
            "add_labels",
            lambda _repo, number, names, **_k: done["added"].append((number, tuple(names))),
        )
        monkeypatch.setattr(
            module.gh_rest,
            "remove_label",
            lambda _repo, number, name, **_k: done["removed"].append((number, name)),
        )
        monkeypatch.setattr(
            module.gh_rest,
            "disable_auto_merge",
            lambda _repo, number, **_k: done["disabled"].append(number),
        )
        return done

    def test_a_frozen_pull_loses_its_consent_and_auto_merge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Иначе метка — просто надпись.

        Замер, из которого выросла задача: `main` покраснела в 15:24, PR #1480
        смержился в 15:42 — авто-мерж, включённый раньше, увёз его мимо
        заморозки.
        """
        module = _load_module()
        pulls = self._pulls([module.LABEL])
        done = self._wire(module, monkeypatch, red=True, pulls=pulls)

        module.apply_freeze()

        assert done["added"] == [(100, (module.FREEZE_LABEL,))]
        assert done["removed"] == [(100, module.LABEL)]
        assert done["disabled"] == [100]

    def test_a_green_base_thaws_without_a_human(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module()
        pulls = self._pulls([module.FREEZE_LABEL])
        done = self._wire(module, monkeypatch, red=False, pulls=pulls)

        module.apply_freeze()

        assert done["removed"] == [(100, module.FREEZE_LABEL)]
        assert done["added"] == []

    def test_the_fixing_pull_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PR с `blocker` при красной базе не трогается вовсе."""
        module = _load_module()
        pulls = self._pulls([module.BLOCKER_LABEL, module.LABEL])
        done = self._wire(module, monkeypatch, red=True, pulls=pulls)

        module.apply_freeze()

        assert done["added"] == []
        assert done["removed"] == []
        assert done["disabled"] == []

    def test_dry_run_changes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module()
        pulls = self._pulls([module.LABEL])
        done = self._wire(module, monkeypatch, red=True, pulls=pulls)

        outcome = module.apply_freeze(dry_run=True)

        assert done == {"added": [], "removed": [], "disabled": []}
        assert outcome.touched == [100]

    def test_consent_does_not_come_back_to_a_frozen_pull(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Два механизма на одном проходе не спорят друг с другом.

        Умолчание #1325 ставит согласие каждому непомеченному PR. Без этой
        ветки оно возвращало бы его тем, у кого заморозка только что сняла.
        """
        module = _load_module()
        pulls = self._pulls([module.FREEZE_LABEL])
        done = self._wire(module, monkeypatch, red=True, pulls=pulls)

        module.apply_default_consent()

        assert done["added"] == []
