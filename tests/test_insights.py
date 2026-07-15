"""Тесты core/insights.py — таксономия падений + затухание (issue #347, эпик #342).

`classify_status` — табличные тесты граничных N/T/K (без часов, § 11);
`learning_cards` — сценарные, поверх реальной SQLite-истории (#344).
"""

from __future__ import annotations

from pathlib import Path

from stepik_grader.core import history
from stepik_grader.core.history import CaseRecord, LintRecord
from stepik_grader.core.insights import (
    InsightCard,
    classify_status,
    failure_kind,
    learning_cards,
    time_to_first_green,
    violated_rule_codes,
)

# --------------------------------------------------------------------------- #
# classify_status — статус карточки от истории (True = встречалась)
# --------------------------------------------------------------------------- #


def test_first_appearance_below_threshold_is_watch() -> None:
    assert classify_status([True]) == "watch"  # hits=1 < T=2


def test_active_when_over_threshold_and_last_dirty() -> None:
    assert classify_status([True, True]) == "active"  # hits=2 ≥ T, streak=0
    assert classify_status([True, False, True]) == "active"


def test_fading_when_active_then_clean_streak_below_k() -> None:
    assert classify_status([True, True, False]) == "fading"  # streak=1 < K=3
    assert classify_status([True, True, False, False]) == "fading"  # streak=2 < K


def test_archived_after_k_clean_in_a_row() -> None:
    assert classify_status([True, True, False, False, False]) == "archived"  # streak=3 ≥ K


def test_blinking_below_threshold_is_watch() -> None:
    assert classify_status([False, True, False]) == "watch"  # hits=1 < T, streak=1 < K


def test_window_limits_to_last_n() -> None:
    # старое единичное падение выпадает за окно N=10 → 10 чистых → архив
    assert classify_status([True, *([False] * 10)], n=10) == "archived"


def test_custom_thresholds() -> None:
    assert classify_status([True], t=1) == "active"  # T=1: hits=1 ≥ 1, streak=0
    assert classify_status([True, False, False], k=2) == "archived"  # K=2: streak=2


def test_empty_history_is_watch() -> None:
    assert classify_status([]) == "watch"


# --------------------------------------------------------------------------- #
# failure_kind — классификация исхода кейса
# --------------------------------------------------------------------------- #


def test_failure_kind_timeout() -> None:
    assert failure_kind("TLE") == "timeout"


def test_failure_kind_runtime_error_with_class() -> None:
    err = "Traceback (most recent call last):\n  File \"x\", line 1\nKeyError: 'k'"
    assert failure_kind("RE", error=err) == "runtime-error:KeyError"


def test_failure_kind_runtime_error_without_class() -> None:
    assert failure_kind("RE", error="") == "runtime-error"


def test_failure_kind_output_format_trailing_ws_and_case() -> None:
    assert failure_kind("WA", output=["42 "], expected=["42"]) == "output-format"
    assert failure_kind("WA", output=["Hello"], expected=["hello"]) == "output-format"


def test_failure_kind_wrong_answer() -> None:
    assert failure_kind("WA", output=["41"], expected=["42"]) == "wrong-answer"
    assert failure_kind("WA") == "wrong-answer"  # нет output/expected → не формат


def test_failure_kind_slow_bench() -> None:
    assert failure_kind("SLOWER") == "slow"
    assert failure_kind("MUCH_SLOWER") == "slow"


def test_failure_kind_not_a_failure() -> None:
    assert failure_kind("AC") is None
    assert failure_kind("OK") is None
    assert failure_kind("SIMILAR") is None


# --------------------------------------------------------------------------- #
# learning_cards — агрегация из реальной истории
# --------------------------------------------------------------------------- #


def _db(tmp_path: Path) -> Path:
    return tmp_path / history.HISTORY_DB_NAME


def test_learning_cards_empty_history(tmp_path) -> None:
    assert learning_cards(tmp_path / "absent.db") == []


def test_learning_cards_scenario_active_then_archived(tmp_path) -> None:
    db = _db(tmp_path)
    for _ in range(3):  # 3 падения подряд
        history.record_run(1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db)
    cards = learning_cards(db)
    assert len(cards) == 1
    assert cards[0].key == "wrong-answer"
    assert cards[0].status == "active"
    assert cards[0].hits == 3

    for _ in range(3):  # 3 чистых прогона подряд → архив
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db)
    assert learning_cards(db) == []  # archived скрыт по умолчанию (хотелка №5)
    archived = learning_cards(db, include_archived=True)
    assert len(archived) == 1
    assert archived[0].status == "archived"


def test_learning_cards_fading_midway(tmp_path) -> None:
    db = _db(tmp_path)
    for _ in range(3):
        history.record_run(1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db)
    history.record_run(1, [CaseRecord(1, "OK")], db_path=db)  # 1 чистый
    [card] = learning_cards(db)
    assert card.status == "fading"


def test_learning_cards_glossary_ref_for_runtime_error(tmp_path) -> None:
    db = _db(tmp_path)
    for _ in range(2):
        history.record_run(
            1, [CaseRecord(1, "RE", failure_kind="runtime-error:KeyError")], db_path=db
        )
    [card] = learning_cards(db)
    assert card == InsightCard(
        key="runtime-error:KeyError",
        category="failure",
        status="active",
        hits=2,
        runs_considered=2,
        glossary_id="keyerror",
    )


def test_learning_cards_includes_lint_keys(tmp_path) -> None:
    db = _db(tmp_path)
    for _ in range(2):
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db, lint=[LintRecord("E501", 1)])
    cards = learning_cards(db)
    lint_cards = [c for c in cards if c.category == "lint"]
    assert [c.key for c in lint_cards] == ["E501"]


# --------------------------------------------------------------------------- #
# time_to_first_green — TTFG-метрика по задачам (issue #431)
# --------------------------------------------------------------------------- #


def test_ttfg_counts_attempts_and_solved(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    # задача "a": WA, затем полный AC (2 попытки, решена)
    history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="a", duration_s=1.0)
    history.record_run(
        1, [CaseRecord(1, "AC"), CaseRecord(2, "AC")], db_path=db, task_key="a", duration_s=1.0
    )
    # задача "b": только WA (не решена)
    history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="b", duration_s=1.0)

    prog = {p.task_key: p for p in time_to_first_green(db)}
    assert prog["a"].solved is True
    assert prog["a"].attempts == 2
    assert prog["a"].total_runs == 2
    assert prog["a"].seconds_to_first_ac is not None
    assert prog["b"].solved is False
    assert prog["b"].attempts == 1


def test_ttfg_empty_history_is_empty_list(tmp_path: Path) -> None:
    assert time_to_first_green(tmp_path / "nope.db") == []


def test_ttfg_partial_ac_run_not_counted_solved(tmp_path: Path) -> None:
    """Прогон с частичным AC (не все кейсы) не считается решением."""
    db = tmp_path / "h.db"
    history.record_run(
        1, [CaseRecord(1, "AC"), CaseRecord(2, "WA")], db_path=db, task_key="t", duration_s=1.0
    )
    (p,) = time_to_first_green(db)
    assert p.solved is False


# --------------------------------------------------------------------------- #
# violated_rule_codes — персональные lint-нарушения (issue #403)
# --------------------------------------------------------------------------- #


def test_violated_rule_codes_from_history(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    history.record_run(
        1,
        [CaseRecord(1, "AC")],
        db_path=db,
        task_key="t",
        duration_s=1.0,
        lint=[LintRecord("F401", 1, "unused"), LintRecord("E501", 2, "long")],
    )
    assert violated_rule_codes(db) == {"F401", "E501"}


def test_violated_rule_codes_empty_history(tmp_path: Path) -> None:
    assert violated_rule_codes(tmp_path / "nope.db") == set()
