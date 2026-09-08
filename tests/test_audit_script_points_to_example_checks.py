"""Ревизия карточек называет, где проверяются их примеры (issue #919, ``ADD-3-07``).

Аудит записал находку «`audit_glossary_cards.py` проверяет наличие полей, а не
работоспособность примера» именно потому, что искал проверку примеров там: по
имени файла разумно ожидать, что «ревизия карточек» смотрит и на их код.

Проверка есть, но в двух других местах, и разделение намеренное — одна и та же
вещь, проверяемая трижды, даёт три источника правды и расходится молча. Значит
скрипт обязан СКАЗАТЬ, где искать, а сказанное обязано оставаться правдой.

Этот тест и держит правду: он читает названные пути из дерева, а не из памяти
автора. Комментарий, который никто не сверяет, разъезжается с кодом — ровно тот
класс дефекта, из-за которого находка и появилась.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_AUDIT = _ROOT / "scripts" / "audit_glossary_cards.py"

#: Что скрипт обещает, и чем это обещание подтверждается в дереве.
_PROMISED = {
    "scripts/check_glossary_examples.py": "compile(",
    "tests/test_glossary_draft_pipeline.py": "test_run_check_bundled_ratchet",
    "tests/glossary_audit_known_issues.txt": None,
}


@pytest.fixture
def audit_docstring() -> str:
    """Докстрока скрипта ревизии — первый блок файла."""
    text = _AUDIT.read_text(encoding="utf-8")
    start = text.index('"""')
    end = text.index('"""', start + 3)
    return text[start:end]


class TestThePointerIsThere:
    """Скрипт не молчит о том, чего не делает."""

    def test_it_says_it_does_not_run_examples(self, audit_docstring: str) -> None:
        assert "НЕ ПРОВЕРЯЕТ" in audit_docstring

    @pytest.mark.parametrize("path", sorted(_PROMISED))
    def test_every_named_place_is_mentioned(self, audit_docstring: str, path: str) -> None:
        assert path in audit_docstring


class TestThePointerIsTrue:
    """Названное существует и делает обещанное."""

    @pytest.mark.parametrize("path", sorted(_PROMISED))
    def test_the_named_file_exists(self, path: str) -> None:
        assert (_ROOT / path).is_file(), f"скрипт ссылается на несуществующий {path}"

    @pytest.mark.parametrize(
        ("path", "marker"),
        sorted((p, m) for p, m in _PROMISED.items() if m is not None),
    )
    def test_the_named_file_does_what_is_promised(self, path: str, marker: str) -> None:
        """Файл на месте — мало: он должен делать то, ради чего назван."""
        assert marker in (_ROOT / path).read_text(encoding="utf-8")

    def test_the_compile_check_still_has_a_zero_budget(self) -> None:
        """Обещано «роняет CI сразу» — значит бюджет обязан быть нулевым.

        Вернись бюджет к ненулевому, обещание стало бы неправдой: часть
        карточек снова проходила бы мимо проверки.
        """
        guard = (_ROOT / "scripts" / "check_glossary_examples.py").read_text(encoding="utf-8")

        assert "BUDGET = 0" in guard
