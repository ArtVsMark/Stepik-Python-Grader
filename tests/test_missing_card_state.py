"""Ссылка на несуществующую карточку говорит об этом (issue #969).

Находка FE-3-01. ``selectGlossaryCard()`` при 404 не делала **ничего**:
детальная панель не очищалась, пустое состояние не показывалось — на экране
оставалась ранее открытая карточка.

Тихий отказ здесь хуже пустого экрана. Студент попадает сюда из ошибки в своём
коде — жмёт «Открыть карточку» у исключения, для которого карточки нет, — и
читает объяснение ЧУЖОГО термина как ответ на свой вопрос. То есть интерфейс не
просто молчит, а учит неверному.

Тот же дефект был в ``selectRuleCard()``: копия кода разошлась с соседями ровно
так же, как копии focus-trap в #1225.

Тесты статические — браузерного движка в облачной сессии нет. Они сторожат, что
обработчик появился и что состояние переключается в обе стороны; сам экран
проверяется прогоном в браузере (описан в PR).
"""

from __future__ import annotations

import json
import pathlib

import pytest

_STATIC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stepik_grader" / "web" / "static"


@pytest.fixture
def content_js() -> str:
    """Исходник разделов «Глоссарий» и «Правила»."""
    return (_STATIC / "content.js").read_text(encoding="utf-8")


@pytest.fixture
def index_html() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def ui_locale() -> dict[str, dict[str, str]]:
    return json.loads((_STATIC / "locales" / "ui.json").read_text(encoding="utf-8"))


def _block(source: str, header: str) -> str:
    start = source.index(header)
    return source[start : source.index("\n}", start)]


class TestNoSilentFailure:
    """Пустой ``.catch(() => {})`` — это и есть дефект, а не стиль."""

    @pytest.mark.parametrize("header", ["function selectGlossaryCard(", "function selectRuleCard("])
    def test_missing_card_is_reported(self, content_js: str, header: str) -> None:
        body = _block(content_js, header)

        assert "showCardMissing(" in body

    @pytest.mark.parametrize("header", ["function selectGlossaryCard(", "function selectRuleCard("])
    def test_network_failure_is_reported_too(self, content_js: str, header: str) -> None:
        """404 и оборванная сеть неразличимы для студента: обе ветки говорят."""
        body = _block(content_js, header)

        assert ".catch(() => {})" not in body
        assert ".catch(() => showCardMissing(" in body


class TestStateSwitchesBothWays:
    """Состояние обязано и появляться, и уходить."""

    def test_missing_hides_the_previous_card(self, content_js: str) -> None:
        """Ровно то, чего не было: прошлая карточка убирается с экрана."""
        body = _block(content_js, "function showCardMissing(")

        assert "detail.hidden = true" in body
        assert 'detail.innerHTML = ""' in body
        assert "missing.hidden = false" in body

    @pytest.mark.parametrize(
        "header,selector",
        [
            ("function renderGlossaryDetail(", '$("#glossary-not-found").hidden = true'),
            ("function renderRuleDetail(", '$("#rules-not-found").hidden = true'),
        ],
    )
    def test_successful_card_clears_the_state(
        self, content_js: str, header: str, selector: str
    ) -> None:
        """Иначе «не найдено» осталось бы висеть над найденной карточкой."""
        assert selector in _block(content_js, header)


class TestDeepLinkActuallyResolves:
    """«Не найдено» обязано быть правдой, иначе состояние врёт вместо молчания.

    Найдено прогоном в браузере: у карточек есть кириллические id («остаток»,
    «срез»), фронтенд шлёт их через ``encodeURIComponent``, а сервер брал путь
    из запроса как есть — искал строку ``%D0%BE%D1%81…``. Прямая ссылка на
    такую карточку не открывалась НИКОГДА; до фикса #969 это просто молчало, а
    после — уверенно сообщало бы «карточки нет», хотя она есть.
    """

    def test_encoded_path_is_decoded_before_lookup(self) -> None:
        from stepik_grader.web import api_routes

        source = pathlib.Path(api_routes.__file__).read_text(encoding="utf-8")

        assert 'unquote(parsed.path[len("/api/glossary/") :])' in source
        assert 'unquote(parsed.path[len("/api/rules/") :])' in source

    def test_cyrillic_id_resolves_in_the_adapter(self) -> None:
        """Сам поиск умеет находить такую карточку — ломало именно кодирование."""
        from urllib.parse import quote

        from stepik_grader.web.glossary_adapter import glossary_get

        assert glossary_get("остаток", lang="ru") is not None
        assert glossary_get(quote("остаток"), lang="ru") is None


class TestMarkupAndLocale:
    """Состояние — своя разметка с ключами, а не подменённый текст."""

    @pytest.mark.parametrize("anchor", ["glossary-not-found", "rules-not-found"])
    def test_block_exists_and_starts_hidden(self, index_html: str, anchor: str) -> None:
        block = index_html[index_html.index(f'id="{anchor}"') :][:200]

        assert "hidden" in block
        assert "empty-state" in index_html[index_html.index(f'id="{anchor}"') - 60 :][:120]

    @pytest.mark.parametrize(
        "key",
        [
            "glossary.not_found_title",
            "glossary.not_found_text",
            "rules.not_found_title",
            "rules.not_found_text",
        ],
    )
    def test_key_exists_in_both_locales(
        self, ui_locale: dict[str, dict[str, str]], key: str
    ) -> None:
        """Пустое состояние переводится, значит и отказ обязан переводиться."""
        assert ui_locale["ru"].get(key)
        assert ui_locale["en"].get(key)

    @pytest.mark.parametrize("key", ["glossary.not_found_title", "rules.not_found_title"])
    def test_markup_declares_the_key(self, index_html: str, key: str) -> None:
        """`data-i18n` — то, чем состояние перерисуется при смене языка."""
        assert f'data-i18n="{key}"' in index_html

    def test_wording_differs_from_empty_state(self, ui_locale: dict[str, dict[str, str]]) -> None:
        """«Не выбрано» и «не найдено» — разные ответы, и путать их нельзя."""
        ru = ui_locale["ru"]

        assert ru["glossary.not_found_title"] != ru["glossary.empty_title"]
        assert ru["rules.not_found_title"] != ru["rules.empty_title"]
