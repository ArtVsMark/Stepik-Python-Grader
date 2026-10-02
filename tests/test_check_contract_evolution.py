"""Гейт «контракт описывает правила своей эволюции» (правило 113).

Двусторонний набор: и отказ, и приёмка. Отдельно закреплена граница, ради
которой гейт вообще смотрит на содержимое раздела, — **заголовок сам по себе
ответом не считается**: пустой раздел «Правила эволюции» выглядел бы выполнением
правила, ничего не сказав потребителю.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).parent.parent


def _load():
    """Загрузить гейт как модуль (скрипты не пакет)."""
    path = _ROOT / "scripts" / "check_contract_evolution.py"
    spec = importlib.util.spec_from_file_location("check_contract_evolution", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FULL = """# Контракт

## Поля

Что-то есть.

## Правила эволюции

1. Имена полей стабильны: переименование — ломающее изменение.
2. Расширение аддитивно: незнакомое поле потребитель игнорирует.
3. Новое поле добавляют в список полей и в этот документ одним PR.

## Прочее

Хвост.
"""


@pytest.fixture
def tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """Дерево с одним контрактом на месте объявленного списка."""
    (tmp_path / "docs" / "dev").mkdir(parents=True)
    (tmp_path / "docs" / "dev" / "result-contract.md").write_text(_FULL, encoding="utf-8")
    return tmp_path


def _only_one(guard, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сузить закрытый список до единственного файла фикстуры."""
    monkeypatch.setattr(guard, "CONTRACTS", ("docs/dev/result-contract.md",))


class TestSectionLookup:
    def test_section_is_found_by_its_heading(self) -> None:
        body = _load().evolution_section(_FULL)

        assert body is not None and "аддитивно" in body

    def test_section_stops_at_the_next_heading(self) -> None:
        """Соседний раздел не подставляет свои слова в ответ."""
        body = _load().evolution_section(_FULL)

        assert body is not None and "Хвост" not in body

    def test_the_older_heading_counts_too(self) -> None:
        """«Ожидания стабильности» написаны до гейта — правило их не переименовывает."""
        text = _FULL.replace("## Правила эволюции", "## Ожидания стабильности")

        assert _load().evolution_section(text) is not None

    def test_document_without_the_section_is_none(self) -> None:
        assert _load().evolution_section("# Контракт\n\n## Поля\n\nВсё.\n") is None


class TestFindings:
    def test_complete_contract_passes(self, tree: pathlib.Path, monkeypatch) -> None:
        guard = _load()
        _only_one(guard, monkeypatch)

        assert guard.contracts_without_evolution(tree) == []

    def test_live_repository_passes(self) -> None:
        """Живые контракты проекта проходят — иначе гейт нечего включать."""
        assert _load().contracts_without_evolution() == []

    def test_missing_section_is_flagged(self, tree: pathlib.Path, monkeypatch) -> None:
        guard = _load()
        _only_one(guard, monkeypatch)
        (tree / "docs" / "dev" / "result-contract.md").write_text(
            "# Контракт\n\n## Поля\n\nВсё.\n", encoding="utf-8"
        )

        found = guard.contracts_without_evolution(tree)

        assert found and "нет раздела" in found[0]

    def test_empty_section_is_not_an_answer(self, tree: pathlib.Path, monkeypatch) -> None:
        """Заголовок без текста — выполнение правила по форме и невыполнение по сути."""
        guard = _load()
        _only_one(guard, monkeypatch)
        (tree / "docs" / "dev" / "result-contract.md").write_text(
            "# Контракт\n\n## Правила эволюции\n\n## Прочее\n\nХвост.\n", encoding="utf-8"
        )

        assert guard.contracts_without_evolution(tree) != []

    def test_half_answered_section_names_what_is_missing(
        self, tree: pathlib.Path, monkeypatch
    ) -> None:
        """Сказать про стабильность и умолчать о расширении — половина ответа."""
        guard = _load()
        _only_one(guard, monkeypatch)
        (tree / "docs" / "dev" / "result-contract.md").write_text(
            "# Контракт\n\n## Правила эволюции\n\nИмена полей стабильны.\n", encoding="utf-8"
        )

        found = guard.contracts_without_evolution(tree)

        assert found and "что расширяемо" in found[0]

    def test_missing_file_is_reported_not_skipped(
        self, tmp_path: pathlib.Path, monkeypatch
    ) -> None:
        """Список контрактов, разошедшийся с деревом, — тоже находка."""
        guard = _load()
        _only_one(guard, monkeypatch)
        (tmp_path / "docs" / "dev").mkdir(parents=True)

        found = guard.contracts_without_evolution(tmp_path)

        assert found and "файла нет" in found[0]


class TestEntryPoint:
    def test_live_repository_is_green(self) -> None:
        done = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "check_contract_evolution.py")],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            encoding="utf-8",
        )

        assert done.returncode == 0, done.stderr

    def test_runs_under_a_windows_console_encoding(self) -> None:
        """issue #1095: cp1252 в джобе Windows роняла гейт на первой же букве вывода."""
        done = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "check_contract_evolution.py")],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
            encoding="utf-8",
        )

        assert done.returncode == 0, done.stderr

    def test_finding_is_code_one(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Код 1 — «нашла»: сам исход прогоняется, а не только его ветка."""
        guard = _load()
        (tmp_path / "docs" / "dev").mkdir(parents=True)
        monkeypatch.setattr(guard, "_ROOT", tmp_path)
        _only_one(guard, monkeypatch)

        assert guard.main() == 1

    def test_missing_docs_is_the_third_outcome(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Читать нечего — это не «всё в порядке»."""
        guard = _load()
        monkeypatch.setattr(guard, "_ROOT", tmp_path)

        assert guard.main() == 2
