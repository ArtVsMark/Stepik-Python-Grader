"""Гейты для правил, которые держались на словах (issue #1384).

Три правила каталога получили механизм, и каждый проверяется тем, что обязан
отвергнуть, — иначе это была бы ровно та односторонняя проверка, против которой
написано правило 140.

* **140** — у гейта есть двусторонний набор (`check_gate_tests.py`);
* **118** — у производного файла назван живой исходник (`check_generated_sources.py`);
* **089** — оригинал не ссылается на свою витрину (`check_showcase_links.py`).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load(name: str) -> Any:
    path = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


gate_tests = _load("check_gate_tests")
generated = _load("check_generated_sources")
showcase = _load("check_showcase_links")


class TestGateTests:
    """140: гейт проверяется тем, что он обязан отвергнуть."""

    def test_one_sided_set_is_flagged(self) -> None:
        """Только «на живом репозитории чисто» — набор, зеленеющий у сломанной проверки."""
        problems = gate_tests.gates_without_rejection(
            ["check_thing.py"],
            {"test_check_thing.py": "def test_ok():\n    assert check_thing.problems() == []\n"},
        )

        assert len(problems) == 1
        assert "односторонний" in problems[0][1]

    def test_two_sided_set_passes(self) -> None:
        problems = gate_tests.gates_without_rejection(
            ["check_thing.py"],
            {
                "test_check_thing.py": (
                    "def test_ok():\n    assert check_thing.problems() == []\n\n"
                    "def test_bad():\n    assert problems, 'обязан краснеть'\n"
                )
            },
        )

        assert problems == []

    def test_gate_without_tests_is_flagged(self) -> None:
        problems = gate_tests.gates_without_rejection(["check_thing.py"], {})

        assert len(problems) == 1
        assert "тестов нет вовсе" in problems[0][1]

    def test_declared_debt_is_skipped(self) -> None:
        """Долг объявляется числом и причиной, а не молчанием."""
        known = next(iter(gate_tests.KNOWN_DEBT))

        assert gate_tests.gates_without_rejection([known], {}) == []

    def test_every_debt_entry_names_a_reason(self) -> None:
        for gate, reason in gate_tests.KNOWN_DEBT.items():
            assert len(reason) > 20, f"{gate}: причина без содержания — это тихое исключение"

    def test_live_repository_passes(self) -> None:
        assert gate_tests.gates_without_rejection() == []


class TestGeneratedSources:
    """118: исходник хранится рядом с производным."""

    def test_named_source_that_exists_passes(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "gen.py").write_text("", encoding="utf-8")
        (tmp_path / "docs" / "out.md").write_text(
            "<!-- СГЕНЕРИРОВАНО scripts/gen.py -->\n# Файл\n", encoding="utf-8"
        )
        (tmp_path / ".github").mkdir()
        (tmp_path / ".rules").mkdir()

        assert generated.problems_with_sources(tmp_path) == []

    def test_generator_that_vanished_is_flagged(self, tmp_path: pathlib.Path) -> None:
        """Исходник исчез, а производное осталось — пересобрать нечем."""
        for name in ("docs", ".github", ".rules"):
            (tmp_path / name).mkdir()
        (tmp_path / "docs" / "out.md").write_text(
            "<!-- СГЕНЕРИРОВАНО scripts/ушёл.py -->\n", encoding="utf-8"
        )

        problems = generated.problems_with_sources(tmp_path)

        assert len(problems) == 1
        assert "которого нет" in problems[0]

    def test_unnamed_generator_is_flagged(self, tmp_path: pathlib.Path) -> None:
        """«Сгенерировано» без имени не отвечает на вопрос «чем пересобрать»."""
        for name in ("docs", ".github", ".rules"):
            (tmp_path / name).mkdir()
        (tmp_path / "docs" / "out.md").write_text(
            "<!-- СГЕНЕРИРОВАНО, не править руками -->\n", encoding="utf-8"
        )

        problems = generated.problems_with_sources(tmp_path)

        assert len(problems) == 1
        assert "генератор не назван" in problems[0]

    def test_ordinary_file_is_not_touched(self, tmp_path: pathlib.Path) -> None:
        for name in ("docs", ".github", ".rules"):
            (tmp_path / name).mkdir()
        (tmp_path / "docs" / "hand.md").write_text("# Написано руками\n", encoding="utf-8")

        assert generated.generated_files(tmp_path) == []

    def test_live_repository_passes(self) -> None:
        assert generated.problems_with_sources() == []


class TestShowcaseLinks:
    """089: из оригинала в его копию не ссылаются."""

    def _tree(self, tmp_path: pathlib.Path) -> pathlib.Path:
        (tmp_path / "src/stepik_grader/glossary/data").mkdir(parents=True)
        (tmp_path / "src/stepik_grader/locales").mkdir(parents=True)
        (tmp_path / "src/stepik_grader/web/static").mkdir(parents=True)
        return tmp_path

    def test_link_in_card_data_is_flagged(self, tmp_path: pathlib.Path) -> None:
        """Ссылка в данных уводит читателя на заведомо более старое."""
        root = self._tree(tmp_path)
        (root / "src/stepik_grader/glossary/data/cards.json").write_text(
            json.dumps(
                [{"id": "x", "docs_url": f"https://{showcase.SHOWCASE_URL}/x.html"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        found = showcase.showcase_links(root)

        assert len(found) == 1
        assert "docs_url" in found[0]

    def test_link_in_markup_is_flagged(self, tmp_path: pathlib.Path) -> None:
        root = self._tree(tmp_path)
        (root / "src/stepik_grader/web/static/page.html").write_text(
            f'<a href="https://{showcase.SHOWCASE_URL}">витрина</a>', encoding="utf-8"
        )

        assert len(showcase.showcase_links(root)) == 1

    def test_explanation_in_a_comment_is_allowed(self, tmp_path: pathlib.Path) -> None:
        """Объяснять устройство комментарием надо — иначе запрет переоткроют."""
        root = self._tree(tmp_path)
        (root / "src/stepik_grader/web/static/app.js").write_text(
            f"// Ссылки на {showcase.SHOWCASE_URL} здесь нет: он копия этой базы.\nconst x = 1;\n",
            encoding="utf-8",
        )

        assert showcase.showcase_links(root) == []

    def test_block_comment_is_allowed(self, tmp_path: pathlib.Path) -> None:
        root = self._tree(tmp_path)
        (root / "src/stepik_grader/web/static/app.js").write_text(
            f"/*\n Витрина {showcase.SHOWCASE_URL} — только цель экспорта.\n*/\nconst x = 1;\n",
            encoding="utf-8",
        )

        assert showcase.showcase_links(root) == []

    def test_service_key_in_data_is_allowed(self, tmp_path: pathlib.Path) -> None:
        """Ключ, начинающийся с подчёркивания, — комментарий внутри данных."""
        root = self._tree(tmp_path)
        (root / "src/stepik_grader/locales/ru.json").write_text(
            json.dumps({"_about": f"экспорт идёт в {showcase.SHOWCASE_URL}"}, ensure_ascii=False),
            encoding="utf-8",
        )

        assert showcase.showcase_links(root) == []

    def test_live_repository_passes(self) -> None:
        assert showcase.showcase_links() == []
