"""Тесты core/insights.py — таксономия падений + затухание (issue #347, эпик #342).

`classify_status` — табличные тесты граничных N/T/K (без часов, § 11);
`learning_cards` — сценарные, поверх реальной SQLite-истории (#344).
"""

from __future__ import annotations

from pathlib import Path

from stepik_grader.core import history, insights
from stepik_grader.core.history import CaseRecord, LintRecord
from stepik_grader.core.insights import (
    InsightCard,
    classify_status,
    current_streak,
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


# --------------------------------------------------------------------------- #
# current_streak — текущая серия AC-подряд (issue #540)
# --------------------------------------------------------------------------- #


def test_current_streak_counts_consecutive_ac_from_newest(tmp_path: Path) -> None:
    db = tmp_path / history.HISTORY_DB_NAME
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="a", duration_s=1.0)
    history.record_run(1, [CaseRecord(1, "WA")], db_path=db, task_key="b", duration_s=1.0)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="c", duration_s=1.0)
    history.record_run(1, [CaseRecord(1, "AC")], db_path=db, task_key="d", duration_s=1.0)
    # порядок (новые первыми): d(AC), c(AC), b(WA), a(AC) → серия = 2, обрывается на WA
    assert current_streak(db) == 2


def test_current_streak_empty_history_zero(tmp_path: Path) -> None:
    assert current_streak(tmp_path / "nope.db") == 0


# ---------------------------------------------------------------------------
# issue #819 — бенчмарк не рвёт серию и не искажает «попытки до первого AC»
# ---------------------------------------------------------------------------


def test_bench_run_does_not_break_streak(tmp_path: Path) -> None:
    """Репро находки: три зачёта, затем сравнение вариантов — серия цела.

    Прогон режима 3/4 не содержит вердикта AC в принципе (вердикты
    ERR/SIMILAR/SLOWER), поэтому прежде считался провалом и обнулял KPI
    «Серия» вместе с бейджами streak_3/streak_7.
    """
    db = tmp_path / history.HISTORY_DB_NAME
    for _ in range(3):
        history.record_run(1, [history.CaseRecord(1, "AC")], db_path=db, task_key="t")
    assert insights.current_streak(db) == 3

    history.record_run(3, [history.CaseRecord(1, "SIMILAR")], db_path=db, task_key="t")
    assert insights.current_streak(db) == 3

    history.record_run(4, [history.CaseRecord(1, "SLOWER")], db_path=db, task_key="t")
    assert insights.current_streak(db) == 3


def test_failed_check_still_breaks_streak(tmp_path: Path) -> None:
    """Провал проверки серию по-прежнему рвёт — иначе метрика ничего не значит."""
    db = tmp_path / history.HISTORY_DB_NAME
    history.record_run(1, [history.CaseRecord(1, "AC")], db_path=db, task_key="t")
    history.record_run(2, [history.CaseRecord(1, "WA")], db_path=db, task_key="t")
    history.record_run(1, [history.CaseRecord(1, "AC")], db_path=db, task_key="t")

    assert insights.current_streak(db) == 1


def test_bench_runs_do_not_inflate_attempts(tmp_path: Path) -> None:
    """«Попыток до первого зачёта» считает проверки, а не сравнения вариантов."""
    db = tmp_path / history.HISTORY_DB_NAME
    history.record_run(1, [history.CaseRecord(1, "WA")], db_path=db, task_key="t")
    for _ in range(5):
        history.record_run(3, [history.CaseRecord(1, "SIMILAR")], db_path=db, task_key="t")
    history.record_run(1, [history.CaseRecord(1, "AC")], db_path=db, task_key="t")

    (progress,) = insights.time_to_first_green(db)
    assert progress.attempts == 2
    assert progress.total_runs == 2
    assert progress.solved is True


def test_ttfg_is_not_distorted_by_retention(tmp_path: Path) -> None:
    """Удаление старых прогонов не меняет уже посчитанные attempts (AC issue)."""
    db = tmp_path / history.HISTORY_DB_NAME
    for _ in range(4):
        history.record_run(
            1, [history.CaseRecord(1, "WA")], db_path=db, task_key="t", max_runs_per_task=2
        )
    history.record_run(
        1, [history.CaseRecord(1, "AC")], db_path=db, task_key="t", max_runs_per_task=2
    )

    (progress,) = insights.time_to_first_green(db)
    assert progress.attempts == 5  # а не 2, как показал бы остаток таблицы runs
    assert progress.total_runs == 5


def test_run_without_mode_counts_as_check() -> None:
    """Запись без поля mode (старые/синтетические данные) считается проверкой."""
    assert insights._is_bench_run({"cases": [{"verdict": "AC"}]}) is False
    assert insights._run_is_full_ac({"cases": [{"verdict": "AC"}]}) is True


# ---------------------------------------------------------------------------
# Окно «Подучить» масштабируется числом задач — issue #972 (PROD-2-05)
# ---------------------------------------------------------------------------


def test_other_task_progress_does_not_archive_your_error(tmp_path) -> None:
    """Работа над другой задачей не убирает из «Подучить» неисправленную ошибку.

    Сценарий из issue: задача A падает `KeyError`, затем студент делает три
    чистых прогона задачи B. При окне в десять прогонов на всю базу карточка
    уходила в архив и исчезала с глаз, хотя решение A не менялось и по-прежнему
    падает. Окно осталось общим (ошибка осмысленна поперёк задач), но глубина
    считается на задачу.
    """
    db = _db(tmp_path)
    for _ in range(3):
        history.record_run(
            1,
            [CaseRecord(1, "RE", failure_kind="runtime-error:KeyError", error_class="KeyError")],
            db_path=db,
            task_key="step:100",
            task_title="zadacha-A",
        )

    for _ in range(3):
        history.record_run(
            1, [CaseRecord(1, "OK")], db_path=db, task_key="step:200", task_title="zadacha-B"
        )

    keys = [card.key for card in learning_cards(db)]

    assert "runtime-error:KeyError" in keys


def test_window_scales_with_task_count(tmp_path) -> None:
    """Глубина окна — «n на задачу», а не n на всю базу."""
    from stepik_grader.core.insights import _scaled_window

    db = _db(tmp_path)
    for task in range(4):
        history.record_run(
            1, [CaseRecord(1, "OK")], db_path=db, task_key=f"step:{task}", task_title=f"t{task}"
        )

    assert _scaled_window(db, per_task=10) == 40


def test_window_never_below_single_task(tmp_path) -> None:
    """Пустая или недоступная база не схлопывает окно в ноль."""
    from stepik_grader.core.insights import _scaled_window

    assert _scaled_window(tmp_path / "net-takogo-faila.db", per_task=10) == 10
