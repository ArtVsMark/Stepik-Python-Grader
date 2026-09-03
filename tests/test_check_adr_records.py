"""Гейт записей о решениях: полнота записи и запрет правки задним числом.

Двусторонний набор (правило 140): и отказ, и приёмка. Отдельно проверяется
граница, ради которой гейт вообще выделяет разделы, — **смена статуса старой
записи разрешена**. Гейт, который ловил бы и её, запрещал бы ровно тот способ
менять решение, который сам же и советует.
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
    path = _ROOT / "scripts" / "check_adr_records.py"
    spec = importlib.util.spec_from_file_location("check_adr_records", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RECORD = """# ADR-0001 — Пример

- **Статус:** Accepted
- **Дата:** 2026-01-01

## Контекст

Что-то происходило.

## Решение

Выбрали первое.

## Альтернативы

- **A. Второе** — дороже.
- **B. Третье** — медленнее.

## Последствия

Живём с этим.
"""


@pytest.fixture
def catalogue(tmp_path: pathlib.Path) -> pathlib.Path:
    """Каталог с одной валидной записью."""
    adr = tmp_path / "docs" / "dev" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-example.md").write_text(_RECORD, encoding="utf-8")
    return tmp_path


class TestRecordCompleteness:
    def test_valid_record_passes(self, catalogue: pathlib.Path) -> None:
        assert _load().incomplete_records(catalogue) == []

    def test_live_repository_passes(self) -> None:
        """Живые записи проекта проходят — иначе гейт нечего включать в CI."""
        assert _load().incomplete_records() == []

    def test_single_alternative_is_flagged(self, catalogue: pathlib.Path) -> None:
        """Один вариант — это тот же выбор, только переписанный."""
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("- **B. Третье** — медленнее.\n", ""), encoding="utf-8")

        found = _load().incomplete_records(catalogue)

        assert found and "альтернатив перечислено 1" in found[0]

    def test_missing_alternatives_section_is_flagged(self, catalogue: pathlib.Path) -> None:
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("## Альтернативы", "## Прочее"), encoding="utf-8")

        assert any(
            "альтернатив перечислено 0" in line for line in _load().incomplete_records(catalogue)
        )

    def test_missing_status_is_flagged(self, catalogue: pathlib.Path) -> None:
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("- **Статус:** Accepted\n", ""), encoding="utf-8")

        assert any("нет строки" in line for line in _load().incomplete_records(catalogue))

    def test_status_outside_the_declared_set_is_flagged(self, catalogue: pathlib.Path) -> None:
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("Accepted", "Обдумывается"), encoding="utf-8")

        assert any("вне набора" in line for line in _load().incomplete_records(catalogue))

    def test_status_with_explanation_passes(self, catalogue: pathlib.Path) -> None:
        """«Accepted (реализовано: #551)» — та же запись, а не другой статус."""
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(
            _RECORD.replace("Accepted", "Accepted (реализовано: #551)"), encoding="utf-8"
        )

        assert _load().incomplete_records(catalogue) == []

    def test_superseded_by_a_missing_record_is_flagged(self, catalogue: pathlib.Path) -> None:
        """Ссылка на замену, которой нет, оставляет решение без наследника."""
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("Accepted", "Superseded by ADR-0099"), encoding="utf-8")

        assert any("которой нет" in line for line in _load().incomplete_records(catalogue))


def _repository(tmp_path: pathlib.Path) -> pathlib.Path:
    """Крошечный репозиторий с одной записью в base-ветке."""
    root = tmp_path / "repo"
    (root / "docs" / "dev" / "adr").mkdir(parents=True)
    (root / "docs" / "dev" / "adr" / "0001-example.md").write_text(_RECORD, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "base")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Тест")
    git("add", "-A")
    git("commit", "-q", "-m", "запись")
    git("checkout", "-q", "-b", "work")
    return root


class TestRewriteAfterTheFact:
    def test_untouched_branch_is_clean(self, tmp_path: pathlib.Path) -> None:
        root = _repository(tmp_path)

        assert _load().rewritten_decisions("base", root) == []

    def test_editing_the_decision_is_flagged(self, tmp_path: pathlib.Path) -> None:
        root = _repository(tmp_path)
        path = root / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("Выбрали первое.", "Выбрали второе."), encoding="utf-8")
        subprocess.run(
            ["git", "commit", "-qam", "правка"], cwd=root, check=True, capture_output=True
        )

        found = _load().rewritten_decisions("base", root)

        assert found == ["docs/dev/adr/0001-example.md: раздел «Решение»"]

    def test_changing_only_the_status_passes(self, tmp_path: pathlib.Path) -> None:
        """Пометить запись заменённой — ровно тот способ, который гейт и советует."""
        root = _repository(tmp_path)
        path = root / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("Accepted", "Superseded by ADR-0002"), encoding="utf-8")
        subprocess.run(
            ["git", "commit", "-qam", "статус"], cwd=root, check=True, capture_output=True
        )

        assert _load().rewritten_decisions("base", root) == []

    def test_a_record_added_by_this_branch_is_not_after_the_fact(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Новая запись — черновик решения; править её этой же веткой законно."""
        root = _repository(tmp_path)
        fresh = root / "docs" / "dev" / "adr" / "0002-second.md"
        fresh.write_text(_RECORD.replace("ADR-0001", "ADR-0002"), encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "новая"], cwd=root, check=True, capture_output=True)

        assert _load().rewritten_decisions("base", root) == []

    def test_missing_base_is_the_third_outcome(self, tmp_path: pathlib.Path) -> None:
        """Базы нет — ответить нечем, и это не «правок нет»."""
        root = _repository(tmp_path)

        with pytest.raises(RuntimeError):
            _load().rewritten_decisions("origin/несуществующая", root)


class TestEntryPoint:
    def test_live_repository_is_green(self) -> None:
        done = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "check_adr_records.py")],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            encoding="utf-8",
        )

        assert done.returncode == 0, done.stderr

    def test_runs_under_a_windows_console_encoding(self) -> None:
        """issue #1095: cp1252 в CI-джобе Windows роняла скрипт на первой же букве."""
        done = subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "check_adr_records.py")],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
            encoding="utf-8",
        )

        assert done.returncode == 0, done.stderr

    def test_incomplete_record_is_the_finding(
        self, catalogue: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Код 1 — «нашла»: прогоняется сам исход, а не только его ветка."""
        guard = _load()
        path = catalogue / "docs" / "dev" / "adr" / "0001-example.md"
        path.write_text(_RECORD.replace("- **B. Третье** — медленнее.\n", ""), encoding="utf-8")
        monkeypatch.setattr(guard, "_ROOT", catalogue)
        monkeypatch.setattr(guard, "_ADR", catalogue / "docs" / "dev" / "adr")

        assert guard.main([]) == 1

    def test_missing_catalogue_is_the_third_outcome(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Каталога нет — проверять нечего, и это не «всё в порядке»."""
        guard = _load()
        monkeypatch.setattr(guard, "_ROOT", tmp_path)
        monkeypatch.setattr(guard, "_ADR", tmp_path / "docs" / "dev" / "adr")

        assert guard.main([]) == 2

    def test_unreachable_base_is_the_third_outcome(self) -> None:
        """Код 2 — «не отработала», отдельно от кода 1 «нашла»."""
        done = subprocess.run(
            [
                sys.executable,
                str(_ROOT / "scripts" / "check_adr_records.py"),
                "--base",
                "origin/такой-ветки-нет",
            ],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            encoding="utf-8",
        )

        assert done.returncode == 2, done.stdout
