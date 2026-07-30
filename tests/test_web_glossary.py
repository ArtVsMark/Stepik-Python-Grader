"""Tests for web/glossary_adapter.py — Глоссарий web-эндпоинты (issue #125).

Direct function tests (glossary_search/get/missing) plus HTTP-level tests via
a real ThreadingHTTPServer on an ephemeral port, mirroring tests/test_web.py's
established pattern.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stepik_grader import web
from stepik_grader.glossary.models import GlossaryCard, GlossaryMissingEntry
from stepik_grader.web import glossary_adapter
from stepik_grader.web.commands import COMMANDS, filter_commands

# ---------------------------------------------------------------------------
# glossary_search / glossary_get — direct function tests
# ---------------------------------------------------------------------------


class TestGlossarySearchNoStoreConfigured:
    """store_path=None (and no CONFIG.glossary_store) → комплектная база (#326);
    при её отсутствии — компактный ``core/glossary.py`` fallback."""

    def test_search_default_shows_only_ready(self) -> None:
        # issue #436: дефолтная выдача — только ready (черновики скрыты);
        # ?status=all — надмножество. После завершения эпика #363 комплектных
        # черновиков может не остаться, поэтому здесь проверяем инвариант
        # default⊆all и «в default только ready»; сам статус-фильтр на
        # контролируемом черновике — в TestGlossarySearchWithConfiguredStore.
        default_cards = glossary_adapter.glossary_search("")
        assert default_cards
        assert all(c["status"] == "ready" for c in default_cards)
        all_cards = glossary_adapter.glossary_search("", status="all")
        assert {c["id"] for c in default_cards} <= {c["id"] for c in all_cards}
        assert len(all_cards) >= len(default_cards)

    def test_search_matches_known_exception(self) -> None:
        cards = glossary_adapter.glossary_search("RecursionError")
        assert any(c["id"] == "recursionerror" for c in cards)

    def test_search_no_match_returns_empty(self) -> None:
        assert glossary_adapter.glossary_search("TotallyMadeUpTerm") == []

    def test_get_known_id_returns_card(self) -> None:
        card = glossary_adapter.glossary_get("keyerror")
        assert card is not None
        assert card["title"] == "KeyError"

    def test_get_unknown_id_returns_none(self) -> None:
        assert glossary_adapter.glossary_get("not-a-real-id") is None

    def test_default_serves_bundled_base_not_only_exceptions(self) -> None:
        # issue #326: комплектная база даёт не только исключения — напр.
        # встроенную функцию input(), которой нет в компактном fallback.
        card = glossary_adapter.glossary_get("input")
        assert card is not None
        assert card["kind"] == "function"

    def test_true_fallback_when_bundled_absent(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # issue #326: если комплектной базы нет — деградируем на ~28 исключений
        # core/glossary.py (KeyError есть, builtin input() — нет).
        monkeypatch.setattr(glossary_adapter, "BUNDLED_GLOSSARY_DIR", tmp_path / "nope")
        cards = glossary_adapter.glossary_search("")
        ids = {c["id"] for c in cards}
        assert "keyerror" in ids
        assert "input" not in ids


class TestGlossarySearchWithConfiguredStore:
    """store_path pointing at a real JSON card file."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(id="functools-reduce", title="functools.reduce", kind="function"),
            GlossaryCard(id="match-case", title="match/case", kind="construct"),
        ]
        path = tmp_path / "glossary.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_search_returns_configured_cards_not_fallback(self, store_path: pathlib.Path) -> None:
        # status="all": store-карточки без явного ready не должны отфильтроваться
        # дефолтом (issue #436) — тест про источник, не про статус-фильтр.
        cards = glossary_adapter.glossary_search("", status="all", store_path=str(store_path))
        ids = {c["id"] for c in cards}
        assert ids == {"functools-reduce", "match-case"}

    def test_get_returns_configured_card(self, store_path: pathlib.Path) -> None:
        card = glossary_adapter.glossary_get("match-case", store_path=str(store_path))
        assert card is not None
        assert card["kind"] == "construct"

    def test_missing_store_file_falls_back_gracefully(self, tmp_path: pathlib.Path) -> None:
        cards = glossary_adapter.glossary_search("", store_path=str(tmp_path / "nope.json"))
        assert len(cards) > 0  # fell back to core/glossary.py, didn't raise

    def test_status_filter_hides_drafts_by_default(self, tmp_path: pathlib.Path) -> None:
        # issue #436: дефолт скрывает draft-карточки, ?status=all показывает их.
        # Проверяется на контролируемом store (комплектных черновиков после #363
        # может не быть — см. TestGlossarySearchNoStoreConfigured выше).
        cards = [
            GlossaryCard(id="ready-one", title="Ready", kind="function", status="ready"),
            GlossaryCard(id="draft-one", title="Draft", kind="function", status="draft"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        sp = str(path)
        default_ids = {c["id"] for c in glossary_adapter.glossary_search("", store_path=sp)}
        all_ids = {
            c["id"] for c in glossary_adapter.glossary_search("", status="all", store_path=sp)
        }
        assert default_ids == {"ready-one"}
        assert all_ids == {"ready-one", "draft-one"}


class TestGlossaryI18n:
    """issue #363: summary/body отдаются строкой выбранной локали (``?lang=``)."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        glossary_adapter._CARDS_CACHE.clear()
        glossary_adapter._INDEX_CACHE.clear()  # issue #404: тот же жизненный цикл
        yield
        glossary_adapter._CARDS_CACHE.clear()
        glossary_adapter._INDEX_CACHE.clear()

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        card = GlossaryCard(
            id="str.capitalize",
            title="str.capitalize",
            kind="function",
            summary="Первая буква — заглавная",
            summary_en="Capitalize the first letter",
            status="ready",
        )
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def test_search_serves_ru_by_default(self, store_path: pathlib.Path) -> None:
        cards = glossary_adapter.glossary_search("", store_path=str(store_path))
        assert cards[0]["summary"] == "Первая буква — заглавная"

    def test_search_serves_en_when_requested(self, store_path: pathlib.Path) -> None:
        cards = glossary_adapter.glossary_search("", lang="en", store_path=str(store_path))
        assert cards[0]["summary"] == "Capitalize the first letter"

    def test_get_localizes_by_lang(self, store_path: pathlib.Path) -> None:
        card = glossary_adapter.glossary_get(
            "str.capitalize", lang="en", store_path=str(store_path)
        )
        assert card is not None
        assert card["summary"] == "Capitalize the first letter"

    def test_code_terms_localizes_by_lang(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "'x'.capitalize()", lang="en", store_path=str(store_path)
        )
        matched = [t for t in terms if t["id"] == "str.capitalize"]
        assert matched and matched[0]["summary"] == "Capitalize the first letter"


class TestCardCache:
    """issue #339: memoization карточек по mtime — без репарсинга на каждый вызов."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        glossary_adapter._CARDS_CACHE.clear()
        glossary_adapter._INDEX_CACHE.clear()  # issue #404: тот же жизненный цикл
        yield
        glossary_adapter._CARDS_CACHE.clear()
        glossary_adapter._INDEX_CACHE.clear()

    @staticmethod
    def _write(path: pathlib.Path, cards: list[GlossaryCard]) -> None:
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_repeated_load_hits_cache(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "g.json"
        self._write(p, [GlossaryCard(id="a", title="A")])
        first = glossary_adapter._all_cards(p)
        second = glossary_adapter._all_cards(p)
        assert second is first  # тот же объект → файл не перечитывался

    def test_mtime_change_invalidates_cache(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "g.json"
        self._write(p, [GlossaryCard(id="a", title="A")])
        first = glossary_adapter._all_cards(p)
        assert {c.id for c in first} == {"a"}
        # переписать с новой карточкой и сдвинуть mtime вперёд (детерминированно)
        self._write(p, [GlossaryCard(id="a", title="A"), GlossaryCard(id="b", title="B")])
        st = p.stat()
        os.utime(p, (st.st_atime + 10, st.st_mtime + 10))
        second = glossary_adapter._all_cards(p)
        assert second is not first
        assert {c.id for c in second} == {"a", "b"}  # правка подхвачена

    def test_bundled_base_cached_across_calls(self) -> None:
        a = glossary_adapter._all_cards(None)
        b = glossary_adapter._all_cards(None)
        assert a is b and len(a) > 500  # комплектная база кешируется


class TestGlossaryFilterAndSort:
    """issue #329: фильтры section/kind/status (без объединения типов) + sort."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(
                id="list-append",
                title="list.append()",
                kind="function",
                section="Списки (list)",
                status="ready",
            ),
            GlossaryCard(
                id="tuple-count",
                title="tuple.count()",
                kind="function",
                section="Кортежи (tuple)",
                status="ready",
            ),
            GlossaryCard(
                id="match-case",
                title="match/case",
                kind="construct",
                section="Условный оператор",
                status="draft",
                version="3.10",
            ),
            GlossaryCard(
                id="zzz-term",
                title="zzz",
                kind="term",
                section="Списки (list)",
                status="ready",
            ),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_section_filter_does_not_merge_list_and_tuple(self, store_path: pathlib.Path) -> None:
        # Ключевое требование владельца: «Кортежи» не показывают списки.
        tuples = glossary_adapter.glossary_search(
            "", section="Кортежи (tuple)", store_path=str(store_path)
        )
        assert {c["id"] for c in tuples} == {"tuple-count"}
        lists = glossary_adapter.glossary_search(
            "", section="Списки (list)", store_path=str(store_path)
        )
        assert {c["id"] for c in lists} == {"list-append", "zzz-term"}

    def test_kind_filter(self, store_path: pathlib.Path) -> None:
        # status="all": kind-фильтр проверяем по всей базе (match-case — draft).
        res = glossary_adapter.glossary_search(
            "", kind="construct", status="all", store_path=str(store_path)
        )
        assert {c["id"] for c in res} == {"match-case"}

    def test_status_filter(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("", status="draft", store_path=str(store_path))
        assert {c["id"] for c in res} == {"match-case"}

    def test_query_and_section_combine(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search(
            "append", section="Списки (list)", store_path=str(store_path)
        )
        assert {c["id"] for c in res} == {"list-append"}

    def test_sort_az(self, store_path: pathlib.Path) -> None:
        titles = [
            c["title"]
            for c in glossary_adapter.glossary_search("", sort="az", store_path=str(store_path))
        ]
        assert titles == sorted(titles, key=str.lower)

    def test_sort_section(self, store_path: pathlib.Path) -> None:
        sections = [
            c["section"]
            for c in glossary_adapter.glossary_search(
                "", sort="section", store_path=str(store_path)
            )
        ]
        assert sections == sorted(sections, key=str.lower)

    def test_sort_version_orders_versioned_first(self, store_path: pathlib.Path) -> None:
        # status="all": версионированная карточка match-case — draft (issue #436).
        res = glossary_adapter.glossary_search(
            "", sort="version", status="all", store_path=str(store_path)
        )
        assert res[0]["version"] == "3.10"  # версионированные — вперёд
        assert all(c["version"] == "" for c in res[1:])  # без версии — в конец


class TestGlossaryGroupsAndRelevance:
    """issue #685: грань ?group= (модули/типы данных) и сортировка по релевантности."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(
                id="math.sqrt",
                title="math.sqrt()",
                kind="function",
                section="Модуль math",
                status="ready",
            ),
            GlossaryCard(
                id="itertools.chain",
                title="itertools.chain()",
                kind="function",
                section="Модуль itertools",
                status="ready",
            ),
            GlossaryCard(
                id="str.split",
                title="str.split()",
                kind="function",
                section="Строки (str)",
                status="ready",
                syntax="str.split(sep=None, maxsplit=-1)",
                examples=['"a,b".split(",")'],
            ),
            GlossaryCard(
                id="dict.get",
                title="dict.get()",
                kind="function",
                section="Словари (dict)",
                status="ready",
            ),
            GlossaryCard(
                id="keyerror",
                title="KeyError",
                kind="exception",
                section="Исключения",
                status="ready",
                summary="Возникает при обращении к отсутствующему ключу (dict.get спасает)",
            ),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_group_modules_selects_module_sections(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("", group="modules", store_path=str(store_path))
        assert {c["id"] for c in res} == {"math.sqrt", "itertools.chain"}

    def test_group_types_selects_builtin_type_sections(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("", group="types", store_path=str(store_path))
        assert {c["id"] for c in res} == {"str.split", "dict.get"}

    def test_unknown_group_is_ignored_not_empty(self, store_path: pathlib.Path) -> None:
        # Терпимость к мусору в query — как у неизвестного sort (порядок источника).
        res = glossary_adapter.glossary_search("", group="bogus", store_path=str(store_path))
        assert len(res) == 5

    def test_group_combines_with_query(self, store_path: pathlib.Path) -> None:
        res = glossary_adapter.glossary_search("chain", group="modules", store_path=str(store_path))
        assert {c["id"] for c in res} == {"itertools.chain"}

    def test_cards_carry_group_field(self, store_path: pathlib.Path) -> None:
        # UI строит по нему кнопки семейств и списки их разделов, не повторяя правило.
        groups = {
            c["id"]: c["group"]
            for c in glossary_adapter.glossary_search("", store_path=str(store_path))
        }
        assert groups["math.sqrt"] == "modules"
        assert groups["str.split"] == "types"
        assert groups["keyerror"] == "builtins"

    def test_section_label_localizes_by_lang(self, store_path: pathlib.Path) -> None:
        # issue #685: `section` — серверное ЗНАЧЕНИЕ фильтра (остаётся русским),
        # `section_label` — подпись для UI на языке запроса.
        ru = {c["id"]: c for c in glossary_adapter.glossary_search("", store_path=str(store_path))}
        en = {
            c["id"]: c
            for c in glossary_adapter.glossary_search("", lang="en", store_path=str(store_path))
        }
        assert ru["str.split"]["section_label"] == "Строки (str)"
        assert en["str.split"]["section_label"] == "Strings (str)"
        assert en["keyerror"]["section_label"] == "Exceptions"
        # Имя модуля — идентификатор, переводится только слово «Модуль».
        assert en["math.sqrt"]["section_label"] == "Module math"
        assert en["math.sqrt"]["section"] == "Модуль math"

    def test_section_label_falls_back_to_source_name(self, tmp_path: pathlib.Path) -> None:
        # Незнакомый раздел показывается как есть, а не маркером пропущенного
        # перевода: переименование раздела при аудите (#684) не ломает UI.
        card = GlossaryCard(id="x", title="X", section="Совершенно новый раздел", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        res = glossary_adapter.glossary_search("", lang="en", store_path=str(path))
        assert res[0]["section_label"] == "Совершенно новый раздел"

    def test_get_card_carries_localized_section_label(self, store_path: pathlib.Path) -> None:
        # Deep-link на карточку показывает раздел на том же языке, что и список.
        card = glossary_adapter.glossary_get("math.sqrt", lang="en", store_path=str(store_path))
        assert card is not None
        assert card["section_label"] == "Module math"
        assert card["group"] == "modules"

    def test_every_bundled_section_has_english_label(self) -> None:
        # Guard дрейфа: у каждого раздела комплектной базы есть EN-подпись —
        # иначе в англоязычном UI раздел показался бы русским именем.
        en_cards = glossary_adapter.glossary_search("", lang="en", status="all")
        untranslated = sorted(
            {c["section"] for c in en_cards if c["section"] and c["section_label"] == c["section"]}
        )
        assert untranslated == [], f"разделы без EN-подписи: {untranslated}"

    def test_unclassified_section_falls_into_other(self, tmp_path: pathlib.Path) -> None:
        # Семейства заменили селект «Раздел», поэтому раздел без явного семейства
        # обязан оставаться достижимым — через «Прочее», а не выпадать из навигации.
        card = GlossaryCard(id="x", title="X", section="Совершенно новый раздел", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        res = glossary_adapter.glossary_search("", group="other", store_path=str(path))
        assert [c["id"] for c in res] == ["x"]

    def test_every_bundled_section_has_explicit_group(self) -> None:
        # Guard дрейфа (#684 активно правит разделы): «Прочее» в комплектной базе
        # должно быть пустым — новый раздел классифицируется в _SECTION_GROUPS.
        unclassified = glossary_adapter.glossary_search("", group="other", status="all")
        assert unclassified == [], "разделы без семейства: " + ", ".join(
            sorted({c["section"] for c in unclassified})
        )

    def test_groups_partition_the_whole_base(self) -> None:
        # Ни одна карточка не теряется и не дублируется между семействами.
        total = len(glossary_adapter.glossary_search("", status="all"))
        by_group = sum(
            len(glossary_adapter.glossary_search("", group=g, status="all"))
            for g in sorted(glossary_adapter._GROUPS)
        )
        assert by_group == total

    def test_search_finds_by_syntax_and_examples(self, store_path: pathlib.Path) -> None:
        by_syntax = glossary_adapter.glossary_search("maxsplit", store_path=str(store_path))
        assert {c["id"] for c in by_syntax} == {"str.split"}
        by_example = glossary_adapter.glossary_search('"a,b"', store_path=str(store_path))
        assert {c["id"] for c in by_example} == {"str.split"}

    def test_sort_relevance_puts_title_match_before_text_mention(
        self, store_path: pathlib.Path
    ) -> None:
        # «dict.get» упомянут в summary KeyError — но карточка самого метода выше.
        res = glossary_adapter.glossary_search(
            "dict.get", sort="relevance", store_path=str(store_path)
        )
        assert [c["id"] for c in res] == ["dict.get", "keyerror"]

    def test_sort_relevance_without_query_falls_back_to_az(self, store_path: pathlib.Path) -> None:
        # Приоритет типа-владельца тут НЕ применяется: «просто открыл раздел» —
        # алфавитный список, иначе методы str. всплыли бы наверх.
        titles = [
            c["title"]
            for c in glossary_adapter.glossary_search(
                "", sort="relevance", store_path=str(store_path)
            )
        ]
        assert titles == sorted(titles, key=str.lower)

    def test_sort_relevance_prefers_main_type_over_alphabet(self, tmp_path: pathlib.Path) -> None:
        # Запрос «split»: одноимённый метод основного типа — выше и однофамильцев
        # (bytearray.split), и просто похожих имён (bytearray.rsplit).
        cards = [
            GlossaryCard(id="bytearray.rsplit", title="bytearray.rsplit()", status="ready"),
            GlossaryCard(id="bytearray.split", title="bytearray.split()", status="ready"),
            GlossaryCard(id="str.split", title="str.split()", status="ready"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        res = glossary_adapter.glossary_search("split", sort="relevance", store_path=str(path))
        assert [c["id"] for c in res] == ["str.split", "bytearray.split", "bytearray.rsplit"]


class TestGlossaryReadyDefaultAndPrivate:
    """issue #436: дефолт ready + скрытие приватных автодрафтов из выдачи."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(id="str.split", title="str.split()", kind="function", status="ready"),
            GlossaryCard(id="os._exit", title="os._exit()", kind="function", status="draft"),
            GlossaryCard(
                id="_pickle.pickleerror",
                title="_pickle.PickleError",
                kind="exception",
                status="draft",
            ),
            # дандер-метод — легитимная публичная карточка, НЕ приватная
            GlossaryCard(id="str.__len__", title="str.__len__()", kind="function", status="ready"),
            # регрессия #436: рукописная ready-карточка со slug-именем «выглядит»
            # приватной (сегмент __add__…-operators не заканчивается на __), но
            # приватный фильтр не должен её прятать — она ready.
            GlossaryCard(
                id="__add__-__mul__-operators",
                title="Операторные методы (__add__/__mul__)",
                kind="term",
                status="ready",
            ),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_default_shows_only_ready_non_private(self, store_path: pathlib.Path) -> None:
        ids = {c["id"] for c in glossary_adapter.glossary_search("", store_path=str(store_path))}
        # все ready видны (в т.ч. slug-имя __add__…-operators); draft-приватные — нет
        assert ids == {"str.split", "str.__len__", "__add__-__mul__-operators"}

    def test_ready_slug_card_not_hidden_as_private(self, store_path: pathlib.Path) -> None:
        # регрессия #436: ready-карточка со slug-именем не должна пропадать из
        # выдачи (иначе OOP-карточки операторных методов исчезали бы из поиска).
        for st in ("", "all"):
            ids = {
                c["id"]
                for c in glossary_adapter.glossary_search(
                    "", status=st or None, store_path=str(store_path)
                )
            }
            assert "__add__-__mul__-operators" in ids, st

    def test_private_hidden_even_under_all(self, store_path: pathlib.Path) -> None:
        ids = {
            c["id"]
            for c in glossary_adapter.glossary_search("", status="all", store_path=str(store_path))
        }
        assert "os._exit" not in ids
        assert "_pickle.pickleerror" not in ids
        assert "str.__len__" in ids  # дандер не скрыт

    def test_private_hidden_under_explicit_draft(self, store_path: pathlib.Path) -> None:
        ids = {
            c["id"]
            for c in glossary_adapter.glossary_search(
                "", status="draft", store_path=str(store_path)
            )
        }
        assert ids == set()  # обе draft-карточки приватны → скрыты (AC2)

    def test_detector_base_keeps_full_set(self, store_path: pathlib.Path) -> None:
        # _all_cards (питает code_terms/queue_code_gaps) НЕ фильтруется — детектор
        # видит полную базу, включая приватные/draft (issue #436 AC3).
        all_ids = {c.id for c in glossary_adapter._all_cards(store_path)}
        assert {"os._exit", "_pickle.pickleerror", "str.split", "str.__len__"} <= all_ids


def test_is_private_name_unit() -> None:
    from stepik_grader.web.glossary_adapter import _is_private_name

    assert _is_private_name("os._exit")
    assert _is_private_name("_pickle.pickleerror")
    assert _is_private_name("warnings._optionerror")
    assert not _is_private_name("str.split")
    assert not _is_private_name("__init__")  # дандер публичен
    assert not _is_private_name("str.__len__")
    assert not _is_private_name("input")


# ---------------------------------------------------------------------------
# code_terms — мини-карточки функций из кода песочницы (issue #321)
# ---------------------------------------------------------------------------


class TestCodeTerms:
    """issue #321/#322: концепции кода → карточки (+has_card, методы, очередь)."""

    @pytest.fixture
    def store_path(self, tmp_path: pathlib.Path) -> pathlib.Path:
        cards = [
            GlossaryCard(
                id="sorted",
                title="sorted()",
                kind="function",
                status="ready",
                summary="Новый отсортированный список из итерируемого.",
            ),
            # id без префикса модуля — проверяет матч по «хвосту» (math.sqrt → sqrt)
            GlossaryCard(id="sqrt", title="math.sqrt()", kind="function", status="ready"),
            # метод встроенного типа — матчится с голого имени split (issue #322)
            GlossaryCard(id="str.split", title="str.split()", kind="function", status="ready"),
            GlossaryCard(id="match/case", title="match/case", kind="construct", status="draft"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_notable_builtin_maps_to_card(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms("xs = sorted([3, 1, 2])", store_path=str(store_path))
        assert [t["id"] for t in terms] == ["sorted"]
        assert terms[0]["has_card"] is True
        assert terms[0]["summary"]  # покрытый термин несёт summary для мини-карточки
        assert terms[0]["confidence"] == "high"

    def test_dotted_concept_matches_by_tail(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "import math\nx = math.sqrt(4)\n", store_path=str(store_path)
        )
        assert any(t["id"] == "sqrt" and t["has_card"] for t in terms)  # math.sqrt → карта sqrt

    def test_method_detected_with_low_confidence(self, store_path: pathlib.Path) -> None:
        # issue #322: s.split() → карта str.split; confidence=low (тип получателя неизвестен)
        terms = glossary_adapter.code_terms(
            "s = 'a,b'\nparts = s.split(',')\n", store_path=str(store_path)
        )
        split = next(t for t in terms if t["id"] == "str.split")
        assert split["has_card"] is True
        assert split["confidence"] == "low"

    def test_match_case_construct_detected(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "match x:\n    case 1:\n        pass\n", store_path=str(store_path)
        )
        assert any(t["id"] == "match/case" for t in terms)

    def test_recognized_builtin_without_card_is_dimmed(self, store_path: pathlib.Path) -> None:
        # issue #322: повседневные builtin'ы теперь распознаются; без карточки в
        # базе возвращаются has_card=False (панель рисует их приглушённо)
        terms = {
            t["id"]: t
            for t in glossary_adapter.code_terms("print(len([1]))", store_path=str(store_path))
        }
        assert terms["print"]["has_card"] is False
        assert terms["len"]["has_card"] is False

    def test_covered_terms_sorted_before_uncovered(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms(
            "xs = sorted([1])\nprint(xs)\n", store_path=str(store_path)
        )
        assert terms[0]["id"] == "sorted" and terms[0]["has_card"]  # покрытые вперёд
        assert any(not t["has_card"] for t in terms)  # print — без карточки, ниже

    def test_user_defined_names_excluded(self, store_path: pathlib.Path) -> None:
        # Пользовательское имя (helper) не концепция — ни как def, ни как вызов.
        # Сами конструкции def/return (issue #686) при этом распознаются, но это
        # не пользовательские имена, поэтому проверяем именно отсутствие helper.
        code = "def helper():\n    return 1\nhelper()\n"
        ids = {t["id"] for t in glossary_adapter.code_terms(code, store_path=str(store_path))}
        assert "helper" not in ids

    def test_syntax_error_returns_empty(self, store_path: pathlib.Path) -> None:
        assert glossary_adapter.code_terms("def (:", store_path=str(store_path)) == []

    def test_no_duplicate_card_for_repeated_concept(self, store_path: pathlib.Path) -> None:
        terms = glossary_adapter.code_terms("sorted([]); sorted([1])", store_path=str(store_path))
        assert [t["id"] for t in terms] == ["sorted"]

    def test_exception_from_raise_and_except_matches_card(self, tmp_path: pathlib.Path) -> None:
        # issue #686: раньше исключения в коде не распознавались вовсе — панель
        # молчала, хотя карточек исключений в базе больше сотни.
        cards = [
            GlossaryCard(id="valueerror", title="ValueError", kind="exception", status="ready"),
            GlossaryCard(id="keyerror", title="KeyError", kind="exception", status="ready"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        code = "try:\n    int('x')\nexcept ValueError:\n    raise KeyError('bad')\n"
        terms = {t["id"]: t for t in glossary_adapter.code_terms(code, store_path=str(path))}
        assert terms["valueerror"]["has_card"] is True
        assert terms["keyerror"]["has_card"] is True
        assert terms["valueerror"]["snippet"] == "except ValueError:"
        assert terms["keyerror"]["snippet"] == "raise KeyError(...)"

    def test_module_attribute_without_call_detected(self, tmp_path: pathlib.Path) -> None:
        # issue #686: `math.pi` — не вызов, сканер его не видел; карточка есть.
        card = GlossaryCard(id="math.pi", title="math.pi", kind="term", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        terms = glossary_adapter.code_terms("import math\nprint(math.pi)\n", store_path=str(path))
        assert any(t["id"] == "math.pi" and t["has_card"] for t in terms)

    def test_nested_attribute_link_not_reported_separately(self, tmp_path: pathlib.Path) -> None:
        # Из `os.path.join(...)` концепция одна — вся цепочка; промежуточный
        # `os.path` отдельным термином панели быть не должен.
        card = GlossaryCard(id="os.path.join", title="os.path.join()", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        ids = [
            t["id"]
            for t in glossary_adapter.code_terms(
                "import os\np = os.path.join('a', 'b')\n", store_path=str(path)
            )
        ]
        assert "os.path.join" in ids
        assert "os.path" not in ids and "path" not in ids

    def test_stdlib_class_method_matches_card_from_base(self, tmp_path: pathlib.Path) -> None:
        # issue #686: stdlib-инвентарь знает методы только встроенных типов,
        # поэтому имена методов классов берутся из самой базы (path.exists).
        cards = [
            GlossaryCard(id="path.exists", title="Path.exists()", status="ready"),
            GlossaryCard(id="math.sqrt", title="math.sqrt()", status="ready"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        code = "from pathlib import Path\nf = Path('x')\nif f.exists():\n    pass\n"
        terms = {t["id"]: t for t in glossary_adapter.code_terms(code, store_path=str(path))}
        assert terms["path.exists"]["has_card"] is True
        assert terms["path.exists"]["confidence"] == "low"  # тип получателя неизвестен

    def test_module_function_tail_is_not_treated_as_method(self, tmp_path: pathlib.Path) -> None:
        # Обратная сторона предыдущего теста: `math.sqrt` — функция МОДУЛЯ,
        # поэтому «sqrt» не должен становиться именем метода и матчить `obj.sqrt()`.
        card = GlossaryCard(id="math.sqrt", title="math.sqrt()", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        terms = glossary_adapter.code_terms("obj = something()\nobj.sqrt()\n", store_path=str(path))
        assert [t["id"] for t in terms if t["id"] == "math.sqrt"] == []

    def test_bare_name_reference_matches_card(self, tmp_path: pathlib.Path) -> None:
        # issue #686: имя с карточкой в позиции ссылки, а не вызова —
        # `isinstance(x, int)`, аннотация `c: Counter`. Раньше не находилось.
        cards = [
            GlossaryCard(id="int", title="int", kind="term", status="ready"),
            GlossaryCard(id="collections.counter", title="Counter", kind="term", status="ready"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        code = (
            "from collections import Counter\nc: Counter = Counter()\nassert isinstance(1, int)\n"
        )
        ids = {t["id"] for t in glossary_adapter.code_terms(code, store_path=str(path))}
        assert "int" in ids  # голая ссылка во втором аргументе isinstance
        assert "collections.counter" in ids  # аннотация c: Counter

    def test_bare_name_call_not_duplicated_by_reference(self, tmp_path: pathlib.Path) -> None:
        # Имя-функция вызова не должно ещё раз всплыть голой ссылкой — одна
        # карточка на концепт (дедуп по id() call-func).
        card = GlossaryCard(id="sorted", title="sorted()", kind="function", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        terms = glossary_adapter.code_terms("xs = sorted([3, 1])", store_path=str(path))
        assert [t["id"] for t in terms].count("sorted") == 1

    def test_user_variable_shadowing_builtin_is_not_reported(self, tmp_path: pathlib.Path) -> None:
        # `int = 5` делает `int` пользовательской переменной — голая ссылка на
        # неё концептом не считается («любое кроме значений переменных»).
        card = GlossaryCard(id="int", title="int", kind="term", status="ready")
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [card.to_dict()]}, ensure_ascii=False), encoding="utf-8"
        )
        terms = glossary_adapter.code_terms("int = 5\ny = int\n", store_path=str(path))
        assert [t["id"] for t in terms if t["id"] == "int"] == []

    def test_language_keyword_construct_matches_card(self, tmp_path: pathlib.Path) -> None:
        # issue #686: ключевые конструкции (for/while/if/…) — тоже совпадения с
        # глоссарием, если карточка есть.
        cards = [
            GlossaryCard(id="for", title="for", kind="construct", status="ready"),
            GlossaryCard(id="while", title="while", kind="construct", status="ready"),
            GlossaryCard(id="break", title="break", kind="construct", status="ready"),
        ]
        path = tmp_path / "g.json"
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )
        code = "for i in range(3):\n    while True:\n        break\n"
        ids = {t["id"] for t in glossary_adapter.code_terms(code, store_path=str(path))}
        assert {"for", "while", "break"} <= ids

    def test_queue_code_gaps_appends_uncovered_concept(
        self, store_path: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        # issue #322 practice-канал: заметная функция без карточки → очередь
        queue = tmp_path / "missing.json"
        code = "import functools\nfunctools.reduce(lambda a, b: a + b, [1])\n"
        glossary_adapter.queue_code_gaps(
            code, source="sol.py", queue_path=queue, store_path=str(store_path)
        )
        concepts = {e["concept"] for e in glossary_adapter.glossary_missing(queue_path=queue)}
        assert "functools.reduce" in concepts

    def test_queue_code_gaps_bad_path_is_silent(self, store_path: pathlib.Path) -> None:
        # defensive: незаписываемый путь очереди не роняет (best-effort)
        glossary_adapter.queue_code_gaps(
            "xs = sorted([1])", queue_path=pathlib.Path("/nonexistent/dir/q.json")
        )


# ---------------------------------------------------------------------------
# glossary_missing — очередь пополнения (J7)
# ---------------------------------------------------------------------------


class TestGlossaryMissing:
    def test_missing_queue_absent_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        assert glossary_adapter.glossary_missing(queue_path=tmp_path / "nope.json") == []

    def test_missing_queue_returns_entries(self, tmp_path: pathlib.Path) -> None:
        from stepik_grader.glossary.json_provider import save_missing_queue

        queue_path = tmp_path / "missing.json"
        save_missing_queue(
            queue_path,
            [GlossaryMissingEntry(concept="functools.reduce", kind="function")],
        )

        entries = glossary_adapter.glossary_missing(queue_path=queue_path)

        assert len(entries) == 1
        assert entries[0]["concept"] == "functools.reduce"


# ---------------------------------------------------------------------------
# HTTP-level — real server on an ephemeral port (same pattern as test_web.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path: pathlib.Path):
    # issue #261: this file only hits /api/glossary*/api/commands (no `path`
    # param) — workspace just has to exist for the handler's incidental uses.
    httpd = web._GraderServer(("127.0.0.1", 0), web._Handler, workspace=tmp_path, confine=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read()


def _post_json(url: str, body: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read()


class TestGlossaryHttpEndpoints:
    def test_api_glossary_search_returns_json_list(self, server: str) -> None:
        status, body = _get(server + "/api/glossary?" + urllib.parse.urlencode({"q": "KeyError"}))
        assert status == 200
        data = json.loads(body)
        assert isinstance(data, list)
        assert any(c["id"] == "keyerror" for c in data)

    def test_api_glossary_search_without_q_lists_everything(self, server: str) -> None:
        status, body = _get(server + "/api/glossary")
        assert status == 200
        assert len(json.loads(body)) > 0

    def test_api_glossary_get_known_id(self, server: str) -> None:
        status, body = _get(server + "/api/glossary/keyerror")
        assert status == 200
        assert json.loads(body)["title"] == "KeyError"

    def test_api_glossary_get_unknown_id_is_404(self, server: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(server + "/api/glossary/not-a-real-id")
        assert exc.value.code == 404

    def test_api_code_terms_returns_matched_cards(self, server: str) -> None:
        # issue #321: POST кода → мини-карточки функций (bundled-база несёт sorted).
        status, body = _post_json(server + "/api/code-terms", {"code": "xs = sorted([1])"})
        assert status == 200
        terms = json.loads(body)["terms"]
        assert any(t["id"] == "sorted" for t in terms)

    def test_api_code_terms_empty_code_returns_empty(self, server: str) -> None:
        status, body = _post_json(server + "/api/code-terms", {"code": "   "})
        assert status == 200
        assert json.loads(body)["terms"] == []

    def test_api_code_terms_by_path_reads_confined_file(
        self, server: str, tmp_path: pathlib.Path
    ) -> None:
        # issue #322: тело {path} — сервер читает файл из workspace (confine) и
        # возвращает термины его кода (режим 2, по выбранному решению).
        (tmp_path / "sol.py").write_text("xs = sorted([3, 1, 2])\n", encoding="utf-8")
        status, body = _post_json(server + "/api/code-terms", {"path": "sol.py"})
        assert status == 200
        assert any(t["id"] == "sorted" for t in json.loads(body)["terms"])

    def test_api_glossary_kind_param_filters(self, server: str) -> None:
        # issue #329: сервер прокидывает kind в glossary_search.
        status, body = _get(
            server + "/api/glossary?" + urllib.parse.urlencode({"kind": "exception"})
        )
        assert status == 200
        cards = json.loads(body)
        assert cards and all(c["kind"] == "exception" for c in cards)

    def test_api_glossary_sort_az_orders_titles(self, server: str) -> None:
        status, body = _get(server + "/api/glossary?" + urllib.parse.urlencode({"sort": "az"}))
        assert status == 200
        titles = [c["title"] for c in json.loads(body)]
        assert titles == sorted(titles, key=str.lower)

    def test_api_glossary_group_param_filters_to_modules(self, server: str) -> None:
        # issue #685: сервер прокидывает group в glossary_search (бандл-база —
        # разделы «Модуль X»; проверяем инвариант грани, не конкретные карточки).
        status, body = _get(
            server + "/api/glossary?" + urllib.parse.urlencode({"group": "modules"})
        )
        assert status == 200
        cards = json.loads(body)
        assert cards and all(c["group"] == "modules" for c in cards)
        assert all(c["section"].startswith("Модуль ") for c in cards)

    def test_api_glossary_sort_relevance_ranks_exact_title_first(self, server: str) -> None:
        status, body = _get(
            server + "/api/glossary?" + urllib.parse.urlencode({"q": "sorted", "sort": "relevance"})
        )
        assert status == 200
        cards = json.loads(body)
        assert cards and cards[0]["id"] == "sorted"

    def test_api_glossary_missing_empty_by_default(self, server: str) -> None:
        # CONFIG.glossary_missing_queue defaults to a relative path that
        # doesn't exist in the test's cwd -- graceful empty list, not a 500.
        status, body = _get(server + "/api/glossary/missing")
        assert status == 200
        assert json.loads(body) == []


# ---------------------------------------------------------------------------
# commands.py — единый реестр команд (issue #125)
# ---------------------------------------------------------------------------


class TestCommandRegistry:
    def test_registry_has_exactly_the_mvp_ids(self) -> None:
        ids = {c["id"] for c in COMMANDS}
        assert ids == {
            "run_again",
            "copy_input",
            "copy_output",
            "explain_error",
            "open_glossary",
            "toggle_theme",
            "switch_section",
        }

    def test_registry_never_includes_out_of_scope_actions(self) -> None:
        """Regression guard: create_test/compare_solutions are design-only for this
        issue (docs/dev/design/web-design.md § Action cards, отложенные) — never
        register them."""
        ids = {c["id"] for c in COMMANDS}
        assert "create_test" not in ids
        assert "compare_solutions" not in ids

    def test_every_command_has_bilingual_title(self) -> None:
        for cmd in COMMANDS:
            assert set(cmd["title"]) == {"ru", "en"}
            assert cmd["title"]["ru"] and cmd["title"]["en"]

    def test_filter_commands_none_returns_everything(self) -> None:
        assert filter_commands(None) == COMMANDS

    def test_filter_commands_always_tag_is_never_excluded(self) -> None:
        result = filter_commands(set())
        ids = {c["id"] for c in result}
        assert {"run_again", "toggle_theme", "switch_section"} <= ids

    def test_filter_commands_context_tag_includes_matching_command(self) -> None:
        result = filter_commands({"has_glossary"})
        ids = {c["id"] for c in result}
        assert "open_glossary" in ids
        assert "copy_input" not in ids  # has_stdin not in context


class TestCommandsHttpEndpoint:
    def test_api_commands_without_context_returns_full_registry(self, server: str) -> None:
        status, body = _get(server + "/api/commands")
        assert status == 200
        assert len(json.loads(body)) == len(COMMANDS)

    def test_api_commands_with_context_filters(self, server: str) -> None:
        status, body = _get(server + "/api/commands?context=has_stdin,is_failure")
        assert status == 200
        ids = {c["id"] for c in json.loads(body)}
        assert "copy_input" in ids
        assert "explain_error" in ids
        assert "open_glossary" not in ids


class TestGlossaryIndexCache:
    """issue #404: производные индексы источника (by_id для glossary_get, by_concept
    для code_terms) кешируются по mtime — не пересобираются на каждый запрос."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        glossary_adapter._CARDS_CACHE.clear()
        glossary_adapter._INDEX_CACHE.clear()
        yield
        glossary_adapter._CARDS_CACHE.clear()
        glossary_adapter._INDEX_CACHE.clear()

    @staticmethod
    def _write(path: pathlib.Path, cards: list[GlossaryCard]) -> None:
        path.write_text(
            json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_index_built_once_across_consumers(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """glossary_get и code_terms делят один кешированный индекс: сбор — единожды."""
        p = tmp_path / "g.json"
        self._write(
            p, [GlossaryCard(id="str.split", title="str.split", kind="function", status="ready")]
        )
        calls = 0
        real_build = glossary_adapter._build_index

        def counting(cards: list[GlossaryCard]) -> glossary_adapter._GlossaryIndex:
            nonlocal calls
            calls += 1
            return real_build(cards)

        monkeypatch.setattr(glossary_adapter, "_build_index", counting)
        assert glossary_adapter.glossary_get("str.split", store_path=p) is not None
        glossary_adapter.code_terms("'x'.split()", store_path=p)
        glossary_adapter.glossary_get("str.split", store_path=p)
        assert calls == 1  # индекс собран один раз, дальше — из кеша (оба потребителя)

    def test_index_rebuilds_on_mtime_change(self, tmp_path: pathlib.Path) -> None:
        """Правка store со сдвигом mtime → индекс пересобирается, новинка видна."""
        p = tmp_path / "g.json"
        self._write(p, [GlossaryCard(id="a", title="A", status="ready")])
        assert glossary_adapter.glossary_get("a", store_path=p) is not None
        assert glossary_adapter.glossary_get("b", store_path=p) is None  # b ещё нет

        self._write(
            p,
            [
                GlossaryCard(id="a", title="A", status="ready"),
                GlossaryCard(id="b", title="B", status="ready"),
            ],
        )
        st = p.stat()
        os.utime(p, (st.st_atime + 10, st.st_mtime + 10))
        assert glossary_adapter.glossary_get("b", store_path=p) is not None  # индекс обновился

    def test_glossary_get_returns_none_for_unknown_id(self, tmp_path: pathlib.Path) -> None:
        """by_id.get на несуществующем id → None (адаптер отдаст 404)."""
        p = tmp_path / "g.json"
        self._write(p, [GlossaryCard(id="a", title="A", status="ready")])
        assert glossary_adapter.glossary_get("missing", store_path=p) is None
