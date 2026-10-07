"""Тесты scripts/import_glossary.py — карточки приезжают выгрузкой Glossary-Python (#1573).

Главные свойства — те, ради которых импорт и заведён:

- выгрузка, которую загрузчик прочитал бы с потерями, **отвергается**, а не
  раскладывается: на форме 6.0 прежний загрузчик молча превращал ``{"ru": …}``
  в строку, и такая поломка видна только сравнением;
- незнакомая форма и незнакомая схема — отказ словами, а не пропуск;
- та же выгрузка даёт побайтно те же файлы — иначе ``--check`` краснел бы на
  ровном месте.

Живая половина (``TestTheBundledCatalogue``) читает комплектный каталог данных:
он и есть результат импорта закреплённого выпуска.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

from stepik_grader.glossary.json_provider import (
    BUNDLED_GLOSSARY_DIR,
    GlossaryError,
    JsonGlossaryProvider,
)
from stepik_grader.glossary.models import GlossaryCard

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "import_glossary.py"


@pytest.fixture(scope="module")
def importer() -> ModuleType:
    """Скрипт импорта, загруженный по пути (``scripts/`` — не пакет)."""
    spec = importlib.util.spec_from_file_location("_import_glossary", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _card(card_id: str, **overrides: Any) -> dict[str, Any]:
    """Карточка формы 6.0 — ключи и порядок как в выгрузке."""
    card: dict[str, Any] = {
        "id": card_id,
        "title": {"ru": card_id, "en": card_id},
        "kind": "function",
        "summary": {"ru": "Описание.", "en": "Description."},
        "body": {"ru": "", "en": ""},
        "syntax": "",
        "status": "ready",
        "docs_url": "https://docs.python.org/3/",
        "added": "3.8",
        "deprecated": "",
        "removed": "",
        "platforms": ["AllOS"],
        "section": "Встроенные функции",
        "subcat": {"ru": "Разное", "en": "Misc"},
        "aliases": [],
        "keywords": [],
        "tags": [],
        "examples": [["x = 1", "print(x)  # → 1"], ["print(2)"]],
        "related": [],
        "related_errors": [],
    }
    card.update(overrides)
    return card


def _delivery(**overrides: Any) -> dict[str, Any]:
    """Минимальная выгрузка формы 6.0 из двух групп."""
    delivery: dict[str, Any] = {
        "schema": "1.0",
        "schema_of": "карточки глоссария по группам",
        "producer": "ArtVsMark/Glossary-Python",
        "source": "ArtVsMark/Glossary-Python",
        "generated_at": "2026-10-07T19:05:33+00:00",
        "form": "6.0",
        "snapshot": {"cards": 2, "schema_version": 6, "digest": "abc123"},
        "groups": {
            "builtin": [_card("len")],
            "module": [_card("functools.reduce", related=["len"])],
        },
        "moved": {"reduce": "functools.reduce"},
    }
    delivery.update(overrides)
    return delivery


#: Схема в духе приложенной к выпуску — то же подмножество ключевых слов.
_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "delivery",
    "type": "object",
    "required": ["form", "snapshot", "groups"],
    "properties": {
        "form": {"type": "string", "pattern": r"^\d+\.\d+$"},
        "snapshot": {
            "type": "object",
            "properties": {"cards": {"type": "integer", "minimum": 0}},
        },
        "groups": {
            "type": "object",
            "propertyNames": {"pattern": "^[a-z]+$"},
            "additionalProperties": {"type": "array", "items": {"$ref": "#/$defs/entry"}},
        },
    },
    "$defs": {
        "entry": {
            "type": "object",
            "required": ["id", "kind", "platforms"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "kind": {"enum": ["term", "function", "exception", "construct"]},
                "platforms": {"type": "array", "minItems": 1, "uniqueItems": True},
                "status": {"const": "ready"},
            },
        }
    },
}


class TestReadingWithoutLoss:
    """Карточка формы 6.0 проходит модель туда и обратно без единого изменения."""

    def test_a_form_six_card_round_trips(self) -> None:
        card = _card("len", title={"ru": "len()", "en": "len()"}, removed="3.13")

        assert GlossaryCard.from_dict(card).to_dict() == card

    def test_examples_stay_separate_blocks(self) -> None:
        """Блок — один кусок кода: в API он один ``<pre>``, а не строка за строкой."""
        model = GlossaryCard.from_dict(_card("len"))

        assert model.examples == ["x = 1\nprint(x)  # → 1", "print(2)"]
        assert model.to_api_dict()["examples"] == ["x = 1\nprint(x)  # → 1", "print(2)"]

    def test_title_and_subcat_follow_the_language(self) -> None:
        model = GlossaryCard.from_dict(_card("len", title={"ru": "длина", "en": "length"}))

        assert model.to_api_dict("ru")["title"] == "длина"
        assert model.to_api_dict("en")["title"] == "length"
        assert model.to_api_dict("en")["subcat"] == "Misc"

    def test_old_version_field_reads_as_added(self) -> None:
        """До формы 5.0 было одно поле ``version`` — минимальная версия."""
        assert GlossaryCard.from_dict({"id": "x", "title": "X", "version": "3.10"}).added == "3.10"

    @pytest.mark.parametrize(("removed", "pending"), [("3.0", False), ("99.0", True), ("", False)])
    def test_a_removal_in_a_future_python_is_pending(self, removed: str, pending: bool) -> None:
        """«Удалено в 99.0» у пользователя ещё работает — это запланированное удаление."""
        model = GlossaryCard.from_dict(_card("len", removed=removed))

        assert model.to_api_dict()["removed_pending"] is pending


class TestRejection:
    """Выгрузку, которую нельзя импортировать, импорт называет — и ничего не пишет."""

    def test_an_unknown_form_names_both_versions(self, importer: ModuleType) -> None:
        with pytest.raises(importer.DeliveryRejected) as caught:
            importer.verify_delivery(_delivery(form="7.0"), _SCHEMA)

        assert "контракт глоссария сменился: v6 → v7.0" in caught.value.reasons[0]

    def test_a_newer_minor_is_accepted(self, importer: ModuleType) -> None:
        """Минор — новое необязательное поле: импорт вправе о нём не знать."""
        importer.verify_delivery(_delivery(form="6.1"), _SCHEMA)

    def test_an_unknown_schema_keyword_is_a_refusal(self, importer: ModuleType) -> None:
        """Схема, проверенная наполовину, выглядит проверенной — это хуже, чем никакой."""
        schema = copy.deepcopy(_SCHEMA)
        schema["dependentRequired"] = {}

        with pytest.raises(importer.DeliveryRejected, match="незнакомые ключевые слова"):
            importer.verify_delivery(_delivery(), schema)

    def test_a_schema_violation_is_named_by_path(self, importer: ModuleType) -> None:
        delivery = _delivery()
        delivery["groups"]["builtin"][0]["kind"] = "banana"

        with pytest.raises(importer.DeliveryRejected) as caught:
            importer.verify_delivery(delivery, _SCHEMA)

        assert any("/groups/builtin/0/kind" in reason for reason in caught.value.reasons)

    @pytest.mark.parametrize(
        ("moved", "fragment"),
        [
            ({"reduce": "nowhere"}, "карточки 'nowhere' в выгрузке нет"),
            ({"len": "functools.reduce"}, "осталась"),
        ],
    )
    def test_a_broken_redirect_is_a_refusal(
        self, importer: ModuleType, moved: dict[str, str], fragment: str
    ) -> None:
        with pytest.raises(importer.DeliveryRejected) as caught:
            importer.verify_delivery(_delivery(moved=moved), _SCHEMA)

        assert any(fragment in reason for reason in caught.value.reasons)

    def test_a_lossy_card_is_a_refusal(self, importer: ModuleType) -> None:
        """Поле, которого модель не знает, потерялось бы при записи — отказ, а не раскладка."""
        delivery = _delivery()
        delivery["groups"]["builtin"][0]["future_field"] = "x"

        with pytest.raises(
            importer.DeliveryRejected, match="с потерями в полях \\['future_field'\\]"
        ):
            importer.verify_delivery(delivery, None)

    def test_a_refused_import_writes_nothing(
        self, importer: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        source = tmp_path / "delivery.json"
        source.write_text(json.dumps(_delivery(form="7.0")), encoding="utf-8")
        data = tmp_path / "data"

        code = importer.main(["--file", str(source), "--release", "v9", "--data-dir", str(data)])

        assert code == importer.EXIT_REJECTED
        assert not data.exists()


class TestWriting:
    """Раскладка воспроизводима, сверка находит расхождение."""

    def _import(self, importer: ModuleType, tmp_path: pathlib.Path, *extra: str) -> int:
        source = tmp_path / "delivery.json"
        schema = tmp_path / "schema.json"
        source.write_text(json.dumps(_delivery(), ensure_ascii=False), encoding="utf-8")
        schema.write_text(json.dumps(_SCHEMA), encoding="utf-8")
        argv = ["--file", str(source), "--schema", str(schema), "--release", "v1.2.0"]
        return int(importer.main([*argv, "--data-dir", str(tmp_path / "data"), *extra]))

    def test_the_same_delivery_gives_the_same_bytes(
        self, importer: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        assert self._import(importer, tmp_path) == 0
        first = {p.name: p.read_bytes() for p in (tmp_path / "data").iterdir()}

        assert self._import(importer, tmp_path) == 0
        assert {p.name: p.read_bytes() for p in (tmp_path / "data").iterdir()} == first
        assert self._import(importer, tmp_path, "--check") == 0

    def test_the_catalogue_is_what_the_provider_reads(
        self, importer: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Служебные файлы не читаются карточками, а перенаправление ведёт на новую."""
        self._import(importer, tmp_path)
        provider = JsonGlossaryProvider.from_directory(tmp_path / "data")

        assert sorted(card.id for card in provider.all()) == ["functools.reduce", "len"]
        found = provider.get("reduce")
        assert found is not None and found.id == "functools.reduce"
        assert importer.pinned_release(tmp_path / "data") == "v1.2.0"

    def test_a_hand_edit_is_caught_by_check(
        self, importer: ModuleType, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._import(importer, tmp_path)
        edited = tmp_path / "data" / "builtin.json"
        edited.write_text(
            edited.read_text(encoding="utf-8").replace("Описание.", "Правка."), encoding="utf-8"
        )

        assert self._import(importer, tmp_path, "--check") == importer.EXIT_CHECK_FAILED
        assert "builtin.json" in capsys.readouterr().out


class TestRedirectsInTheProvider:
    def test_a_redirect_to_a_missing_card_is_an_error(self, tmp_path: pathlib.Path) -> None:
        """Обещание издателя «новый id существует» проверяется и здесь."""
        (tmp_path / "builtin.json").write_text(json.dumps([_card("len")]), encoding="utf-8")
        (tmp_path / "_moved.json").write_text(json.dumps({"old": "nowhere"}), encoding="utf-8")

        with pytest.raises(GlossaryError, match="карточки 'nowhere' нет"):
            JsonGlossaryProvider.from_directory(tmp_path)


class TestTheBundledCatalogue:
    """Живая половина: комплектный каталог — результат импорта закреплённого выпуска."""

    def test_every_bundled_card_reads_without_loss(self) -> None:
        for path in sorted(BUNDLED_GLOSSARY_DIR.glob("[!_]*.json")):
            for card in json.loads(path.read_text(encoding="utf-8")):
                assert GlossaryCard.from_dict(card).to_dict() == card, f"{path.name}: {card['id']}"

    def test_the_pin_names_a_release_and_the_form_import_accepts(
        self, importer: ModuleType
    ) -> None:
        source = json.loads(
            (BUNDLED_GLOSSARY_DIR / importer.SOURCE_FILE_NAME).read_text(encoding="utf-8")
        )

        assert source["release"].startswith("v")
        assert int(str(source["form"]).split(".")[0]) == importer.ACCEPTED_FORM
        assert source["cards"] == len(
            JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()
        )

    def test_every_bundled_redirect_resolves(self) -> None:
        provider = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR)

        for old, new in provider.moved.items():
            card = provider.get(old)
            assert card is not None and card.id == new
