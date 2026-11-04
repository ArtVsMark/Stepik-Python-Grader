"""«Подучить»: темы по повторным переходам из ошибки в глоссарий (issue #1608).

Таблица ``glossary_hits`` пополнялась на каждом переходе из ошибки в карточку,
а читал её только тест. Здесь закреплено, что повтор даёт рекомендацию, а
одиночный переход — нет, и что тема доходит до веба (``/api/insights/revisited``).
"""

from pathlib import Path

from stepik_grader.core import history, insights


def _db(tmp_path: Path) -> Path:
    return tmp_path / history.HISTORY_DB_NAME


def _hit(db: Path, card: str, error_class: str | None = "KeyError") -> None:
    history.record_glossary_hit(
        card, db_path=db, failure_kind="runtime-error", error_class=error_class
    )


def test_repeated_visits_make_a_topic(tmp_path: Path) -> None:
    """Повтор — тема «Подучить» с числом переходов и классом ошибки."""
    db = _db(tmp_path)
    _hit(db, "keyerror")
    _hit(db, "keyerror")

    [topic] = insights.revisited_topics(db)

    assert (topic.card_id, topic.hits, topic.error_class) == ("keyerror", 2, "KeyError")
    assert topic.last_ts


def test_single_visit_is_not_a_recommendation(tmp_path: Path) -> None:
    """Один переход — любопытство или случайный клик, а не непонятая тема."""
    db = _db(tmp_path)
    _hit(db, "keyerror")

    assert insights.revisited_topics(db) == []


def test_most_visited_first(tmp_path: Path) -> None:
    """Чаще открываемая тема — выше."""
    db = _db(tmp_path)
    for _ in range(2):
        _hit(db, "str.strip", error_class=None)
    for _ in range(3):
        _hit(db, "keyerror")

    topics = insights.revisited_topics(db)

    assert [t.card_id for t in topics] == ["keyerror", "str.strip"]
    assert topics[1].error_class is None


def test_most_frequent_error_class_names_the_reason(tmp_path: Path) -> None:
    """Класс ошибки — самый частый среди переходов, а не последний."""
    db = _db(tmp_path)
    _hit(db, "dict.get", "KeyError")
    _hit(db, "dict.get", "KeyError")
    _hit(db, "dict.get", "TypeError")

    [topic] = insights.revisited_topics(db)

    assert topic.error_class == "KeyError"


def test_no_history_is_empty(tmp_path: Path) -> None:
    """Базы нет — пусто, а не исключение."""
    assert insights.revisited_topics(tmp_path / "nope.db") == []


def test_web_adapter_serializes_topics(tmp_path: Path) -> None:
    """Адаптер веба отдаёт JSON-совместимые словари с объявленными полями."""
    from stepik_grader.web import insights_adapter

    db = _db(tmp_path)
    _hit(db, "keyerror")
    _hit(db, "keyerror")

    [row] = insights_adapter.revisited_topics(db_path=db)

    assert set(row) == {"card_id", "hits", "last_ts", "error_class"}
    assert row["hits"] == 2
