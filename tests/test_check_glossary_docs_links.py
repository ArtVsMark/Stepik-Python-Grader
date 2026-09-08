"""Гейт «ссылка ведёт к самой карточке» (issue #919, находка ``ADD-3-06``).

Карточка — конечная станция: студент приходит в неё из отчёта об ошибке и
оттуда уходит в официальную документацию. Адрес, ведущий «куда-то в ту
сторону», превращает этот уход в поиск по странице — в момент, когда человек
уже не понимает, что происходит.

Проверяется поведение гейта на подставных данных, а не только его зелёность на
текущей базе: гейт, который никогда не видел того, что обязан отклонить, — это
обещание, а не проверка (правило каталога 140).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_glossary_docs_links.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_glossary_docs_links", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard() -> ModuleType:
    return _load()


def _write(folder: pathlib.Path, cards: list[dict]) -> pathlib.Path:
    (folder / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    return folder


class TestWhatItCatches:
    """То, ради чего гейт и заведён."""

    def test_a_general_anchor_instead_of_the_name(
        self, guard: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Ровно исходный дефект: itertools.chain вёл на общий термин."""
        _write(
            tmp_path,
            [
                {
                    "id": "itertools.chain",
                    "docs_url": "https://docs.python.org/3/glossary.html#term-generator",
                }
            ],
        )

        found = guard.misdirected_cards(tmp_path)

        assert found != []
        assert found == [
            ("itertools.chain", "https://docs.python.org/3/glossary.html#term-generator")
        ]

    def test_a_link_to_a_different_function(
        self, guard: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """И второй: pathlib.path вёл на страницу open()."""
        _write(
            tmp_path,
            [
                {
                    "id": "pathlib.path",
                    "docs_url": "https://docs.python.org/3/library/functions.html#open",
                }
            ],
        )

        assert [name for name, _ in guard.misdirected_cards(tmp_path)] == ["pathlib.path"]


class TestWhatItLetsThrough:
    """Границы: проверка обязана молчать там, где адрес верен."""

    def test_its_own_anchor_passes(self, guard: ModuleType, tmp_path: pathlib.Path) -> None:
        _write(
            tmp_path,
            [
                {
                    "id": "itertools.chain",
                    "docs_url": "https://docs.python.org/3/library/itertools.html#itertools.chain",
                }
            ],
        )

        assert guard.misdirected_cards(tmp_path) == []

    def test_a_name_without_a_dot_is_not_checked(
        self, guard: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """У «сложения» своего якоря нет, и общая страница для него верна.

        Без этой границы гейт требовал бы невозможного от половины базы.
        """
        _write(
            tmp_path,
            [
                {
                    "id": "сложение",
                    "docs_url": "https://docs.python.org/3/reference/expressions.html"
                    "#binary-arithmetic-operations",
                }
            ],
        )

        assert guard.misdirected_cards(tmp_path) == []

    def test_a_listed_exception_is_silent(self, guard: ModuleType, tmp_path: pathlib.Path) -> None:
        """Исключение с причиной пропускается — на то оно и исключение."""
        _write(
            tmp_path,
            [
                {
                    "id": "re.group",
                    "docs_url": "https://docs.python.org/3/library/re.html#re.Match.group",
                }
            ],
        )

        assert guard.misdirected_cards(tmp_path) == []

    def test_a_card_without_a_link_is_not_a_finding(
        self, guard: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Отсутствие ссылки — вопрос другой проверки, не этой."""
        _write(tmp_path, [{"id": "itertools.chain"}])

        assert guard.misdirected_cards(tmp_path) == []


class TestOnTheRealBase:
    """Текущая база проходит — иначе гейт нельзя включать в CI."""

    def test_passes_on_current_repo(self, guard: ModuleType) -> None:
        assert guard.misdirected_cards() == []

    def test_cli_exits_zero(self, guard: ModuleType) -> None:
        assert guard.main([]) == 0

    def test_cli_exits_one_on_a_misdirected_card(
        self, guard: ModuleType, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Красный путь прогоняется, а не только описывается (правило 039).

        Гейт, чей отказ никто не видел работающим, обычно и оказывается
        сломанным: база в репозитории проходит, и без подставного каталога
        код возврата 1 существовал бы только в исходнике.
        """
        _write(
            tmp_path,
            [
                {
                    "id": "itertools.chain",
                    "docs_url": "https://docs.python.org/3/glossary.html#term-generator",
                }
            ],
        )

        assert guard.main(["--data-dir", str(tmp_path)]) == 1
        assert "itertools.chain" in capsys.readouterr().err

    def test_every_exception_carries_a_reason(self, guard: ModuleType) -> None:
        """Пустая причина превращает список исключений в список отговорок."""
        assert all(reason.strip() for reason in guard.ALLOWED_GENERAL_PAGE.values())

    def test_exceptions_are_all_still_needed(self, guard: ModuleType) -> None:
        """Исключение, которое больше ничего не покрывает, обязано уйти.

        Иначе список растёт и начинает прятать настоящие расхождения.
        """
        import json as _json

        ids = set()
        for path in sorted(guard.DATA_DIR.glob("*.json")):
            cards = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cards, list):
                ids |= {str(c.get("id") or "") for c in cards if isinstance(c, dict)}

        stale = sorted(set(guard.ALLOWED_GENERAL_PAGE) - ids)

        assert not stale, f"исключения без карточек: {stale}"
