"""test_viewmodels_cache.py — кеш search-терминов store'а (issue #404).

``viewmodels._known_glossary_terms`` раньше перечитывал и переразбирал всю
локальную базу (~1.3 МБ) на каждый RE-кейс (через ``_queue_missing_concept``).
Теперь результат кешируется по mtime store'а; эти тесты фиксируют контракт:
разбор один раз на неизменный файл, инвалидация по mtime, graceful-пустота при
отсутствующем/битом store.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib

import pytest

from stepik_grader.glossary.models import GlossaryCard
from stepik_grader.web import viewmodels


def _write_store(path: pathlib.Path, cards: list[GlossaryCard]) -> None:
    path.write_text(
        json.dumps({"cards": [c.to_dict() for c in cards]}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestKnownGlossaryTermsCache:
    """issue #404: search-термины store'а мемоизируются по mtime."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        viewmodels._TERMS_CACHE.clear()
        yield
        viewmodels._TERMS_CACHE.clear()

    def test_terms_cached_until_mtime_changes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "g.json"
        foo = GlossaryCard(id="foo", title="Foo", status="ready")
        _write_store(p, [foo])
        monkeypatch.setattr(
            viewmodels, "CONFIG", dataclasses.replace(viewmodels.CONFIG, glossary_store=str(p))
        )
        first = viewmodels._known_glossary_terms()
        second = viewmodels._known_glossary_terms()
        assert "foo" in first
        assert second is first  # тот же объект → база не перечитывалась (кеш-хит)

        _write_store(p, [foo, GlossaryCard(id="bar", title="Bar", status="ready")])
        st = p.stat()
        os.utime(p, (st.st_atime + 10, st.st_mtime + 10))  # детерминированный сдвиг mtime
        third = viewmodels._known_glossary_terms()
        assert third is not first  # mtime сменился → пересбор
        assert "bar" in third  # правка store подхвачена

    def test_terms_empty_when_store_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            viewmodels, "CONFIG", dataclasses.replace(viewmodels.CONFIG, glossary_store=None)
        )
        assert viewmodels._known_glossary_terms() == set()

    def test_terms_empty_on_broken_store(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Битый store → пустое множество (GlossaryError не роняет грейдинг) и не
        кешируется как ошибка."""
        p = tmp_path / "broken.json"
        p.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(
            viewmodels, "CONFIG", dataclasses.replace(viewmodels.CONFIG, glossary_store=str(p))
        )
        assert viewmodels._known_glossary_terms() == set()
