"""Тесты пакета rules/ — карточки правил PEP 8 (issue #345, эпик #342)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stepik_grader.rules import JsonRulesProvider, RuleCard, RulesError, bundled_rules
from stepik_grader.rules.json_provider import BUNDLED_RULES_DIR


def test_rulecard_roundtrip() -> None:
    card = RuleCard(
        id="E501",
        title="Строка длиннее лимита",
        summary="Строка превышает лимит.",
        body="Почему и как исправить.",
        pep_url="https://peps.python.org/pep-0008/",
        severity="convention",
        tags=["line-length"],
        example_bad="x = 1  # very long",
        example_good="x = 1",
    )
    assert RuleCard.from_dict(card.to_dict()) == card


def test_rulecard_requires_id_and_title() -> None:
    with pytest.raises(ValueError, match="'id'"):
        RuleCard.from_dict({"title": "t"})
    with pytest.raises(ValueError, match="'title'"):
        RuleCard.from_dict({"id": "E501"})


def test_rulecard_validates_severity_and_status() -> None:
    with pytest.raises(ValueError, match="severity"):
        RuleCard.from_dict({"id": "E1", "title": "t", "severity": "critical"})
    with pytest.raises(ValueError, match="status"):
        RuleCard.from_dict({"id": "E1", "title": "t", "status": "exported"})


def test_provider_from_file_and_queries(tmp_path) -> None:
    data = [
        {"id": "E501", "title": "Long line", "tags": ["fmt"]},
        {"id": "F401", "title": "Unused import"},
    ]
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    prov = JsonRulesProvider.from_file(p)
    assert len(prov) == 2
    got = prov.get("E501")
    assert got is not None and got.title == "Long line"
    assert prov.get("nope") is None
    assert [c.id for c in prov.search("unused")] == ["F401"]
    assert [c.id for c in prov.list_by_tag("fmt")] == ["E501"]


def test_provider_rejects_duplicate_ids(tmp_path) -> None:
    p = tmp_path / "rules.json"
    p.write_text(
        json.dumps([{"id": "E1", "title": "a"}, {"id": "E1", "title": "b"}]), encoding="utf-8"
    )
    with pytest.raises(RulesError, match="Дублирующийся"):
        JsonRulesProvider.from_file(p)


def test_provider_invalid_json_raises_rules_error(tmp_path) -> None:
    p = tmp_path / "rules.json"
    p.write_text("{ broken", encoding="utf-8")
    with pytest.raises(RulesError, match="невалидный JSON"):
        JsonRulesProvider.from_file(p)


def test_provider_from_object_with_rules_key(tmp_path) -> None:
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"rules": [{"id": "W291", "title": "Trailing ws"}]}), encoding="utf-8")
    assert len(JsonRulesProvider.from_file(p)) == 1


def test_bundled_rules_has_at_least_30_ready_cards() -> None:
    cards = bundled_rules().all()
    assert len(cards) >= 30
    assert all(c.status == "ready" for c in cards)
    # каждая карточка наполнена: есть человекочитаемые title/summary и объяснение
    assert all(c.title and c.summary and c.body for c in cards)


def test_bundled_rules_ids_unique_and_have_examples() -> None:
    cards = bundled_rules().all()
    ids = [c.id for c in cards]
    assert len(ids) == len(set(ids))  # провайдер уже проверяет, дублируем как контракт базы
    # ключевые частые коды присутствуют
    assert {"E501", "F401", "E711", "E722"} <= set(ids)


def test_bundled_rules_dir_exists() -> None:
    assert BUNDLED_RULES_DIR.is_dir()
    assert any(BUNDLED_RULES_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# issue #405 (T2): каждая ветка валидации JsonRulesProvider → RulesError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("42", "ожидался список карточек"),  # корень не list/dict
        ('{"rules": "nope"}', "должно быть списком"),  # 'rules' не список
        ("[1, 2]", "должен быть объектом"),  # элемент не объект
    ],
)
def test_provider_rejects_malformed_json_structure(
    tmp_path: Path, content: str, match: str
) -> None:
    """Каждая структурная ветка `_iter_card_dicts` → понятная RulesError."""
    f = tmp_path / "rules.json"
    f.write_text(content, encoding="utf-8")
    with pytest.raises(RulesError, match=match):
        JsonRulesProvider.from_file(f)


def test_provider_from_file_missing_raises_rules_error(tmp_path: Path) -> None:
    """Несуществующий файл → RulesError, не голый FileNotFoundError."""
    with pytest.raises(RulesError, match="не найден"):
        JsonRulesProvider.from_file(tmp_path / "nope.json")


def test_provider_from_directory_not_a_dir_raises(tmp_path: Path) -> None:
    """from_directory на файле (не каталоге) → RulesError."""
    f = tmp_path / "afile.json"
    f.write_text("[]", encoding="utf-8")
    with pytest.raises(RulesError, match="не найдена"):
        JsonRulesProvider.from_directory(f)


def test_provider_rejects_invalid_card(tmp_path: Path) -> None:
    """Карточка без обязательных полей → RulesError (обёртка ValueError from_dict)."""
    f = tmp_path / "rules.json"
    f.write_text("[{}]", encoding="utf-8")  # пустой объект — нет обязательных полей
    with pytest.raises(RulesError):
        JsonRulesProvider.from_file(f)


def test_bundled_rules_missing_dir_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствующий каталог bundled → ПУСТОЙ провайдер, не исключение (graceful
    degradation, как у глоссария #126) — витрина покажет пустой раздел."""
    from stepik_grader.rules import json_provider

    json_provider._BUNDLED_CACHE.clear()  # сбросить возможный прогретый провайдер
    monkeypatch.setattr(json_provider, "BUNDLED_RULES_DIR", Path("/no/such/rules/dir"))
    provider = json_provider.bundled_rules()
    assert isinstance(provider, JsonRulesProvider)
    assert provider.all() == []
    json_provider._BUNDLED_CACHE.clear()  # не оставлять монки-состояние соседям
