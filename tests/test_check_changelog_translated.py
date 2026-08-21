"""Tests for scripts/check_changelog_translated.py — записи CHANGELOG по-русски.

Guard-the-guard: на подделанных записях проверка обязана различать три
состояния — русская проза, английская проза и перечень идентификаторов, где
переводить нечего. Плюс граница строгости: на PR предупреждение, на релизе
отказ, и выпущенная история не судится вовсе.

Скрипт лежит в ``scripts/`` (не на sys.path) — грузим по пути, тем же приёмом,
что ``test_check_docs_guardrails.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_changelog_translated.py"

_RU = "Загрузчик называет проблему с `secrets.json` и путь поиска (#1213)"
_EN = "Downloader now names the problem with `secrets.json` and the path it searched (#1213)"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_changelog_translated", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestProblemWith:
    def test_russian_entry_is_fine(self) -> None:
        assert _load_module().problem_with(_RU) is None

    def test_english_entry_is_reported(self) -> None:
        problem = _load_module().problem_with(_EN)

        assert problem is not None
        assert "непереведённ" in problem

    def test_identifiers_only_entry_passes_silently(self) -> None:
        """Запись из имён кириллицы не содержит по природе — это не дефект.

        Исключение явное: после вырезания кода в остатке не остаётся букв
        вовсе, то есть переводить нечего. «По длине» такую запись не отличить.
        """
        module = _load_module()

        assert module.problem_with("`--ai-hints`, `--sandbox`, `--watch` (#777)") is None

    def test_url_and_link_target_are_not_prose(self) -> None:
        module = _load_module()
        entry = "[Гейт](https://example.com/very-long-english-path) описан выше (#12)"

        assert module.problem_with(entry) is None

    def test_english_prose_around_code_is_still_reported(self) -> None:
        """Код вырезается, проза остаётся — иначе гейт слепнет от обратных кавычек."""
        module = _load_module()

        assert module.problem_with("Now `preflight.py` also checks the branch (#4)") is not None


class TestEntriesAndSections:
    def test_only_dash_lines_are_entries(self) -> None:
        module = _load_module()
        text = "## [Unreleased]\n\n### Added\n\n- первая\n- вторая\nне запись\n"

        assert [entry for _number, entry in module.entries(text)] == ["первая", "вторая"]

    def test_fenced_code_is_not_an_entry(self) -> None:
        """В блоке кода дефис — часть примера, а не запись."""
        module = _load_module()
        text = "- настоящая запись\n\n```\n- pip install foo\n```\n"

        assert [entry for _number, entry in module.entries(text)] == ["настоящая запись"]

    def test_released_history_is_below_the_border(self) -> None:
        module = _load_module()
        text = "## [Unreleased]\n\n- будущая запись\n\n## [1.9.0] - 2026-07-20\n\n- English entry\n"

        assert [entry for _number, entry in module.entries(module.unreleased_part(text))] == [
            "будущая запись"
        ]


class TestCheckFilesAndMain:
    def test_english_fragment_is_named_by_path(self, tmp_path: Path) -> None:
        module = _load_module()
        fragments = tmp_path / "changelog.d"
        fragments.mkdir()
        (fragments / "good.fixed.md").write_text(_RU, encoding="utf-8")
        (fragments / "untranslated.fixed.md").write_text(_EN, encoding="utf-8")

        problems = module.check_files(None, fragments, [])

        assert len(problems) == 1
        assert "untranslated.fixed.md" in problems[0]

    def test_released_sections_are_not_judged(self, tmp_path: Path) -> None:
        """История до 1.10.0 писалась по-английски — это не дефект, а прошлое."""
        module = _load_module()
        changelog = tmp_path / "CHANGELOG.md"
        changelog.write_text(
            "## [Unreleased]\n\n- новая запись\n\n## [1.9.0] - 2026-07-20\n\n- English entry\n",
            encoding="utf-8",
        )

        assert module.check_files(changelog, None, []) == []

    def test_release_notes_file_is_judged_whole(self, tmp_path: Path) -> None:
        """Release notes — то, что публикуется: там граница «выпущенного» не работает."""
        module = _load_module()
        notes = tmp_path / "notes.md"
        notes.write_text("### Added\n\n- English entry that ships (#5)\n", encoding="utf-8")

        problems = module.check_files(None, None, [notes])

        assert len(problems) == 1
        assert "notes.md" in problems[0]

    def test_missing_file_is_a_problem_not_a_pass(self, tmp_path: Path) -> None:
        """Пустой вход читается как «чисто» — та же ошибка, что у гейтов до issue #988."""
        module = _load_module()

        problems = module.check_files(None, None, [tmp_path / "нет-такого.md"])

        assert len(problems) == 1

    def test_pr_run_warns_and_returns_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load_module()
        notes = tmp_path / "notes.md"
        notes.write_text("- English entry that ships (#5)\n", encoding="utf-8")

        code = module.main(["--changelog", str(tmp_path / "нет.md"), str(notes)])

        printed = capsys.readouterr().out
        assert code == 0
        assert "::warning::" in printed

    def test_release_run_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Публикация непереведённого необратима — здесь гейт роняет прогон."""
        module = _load_module()
        notes = tmp_path / "notes.md"
        notes.write_text("- English entry that ships (#5)\n", encoding="utf-8")
        empty = tmp_path / "changelog.d"
        empty.mkdir()

        code = module.main(
            [
                "--strict",
                "--changelog",
                str(tmp_path / "нет.md"),
                "--fragments",
                str(empty),
                str(notes),
            ]
        )

        assert code == 1
        assert "::error::" in capsys.readouterr().out

    def test_repository_itself_is_clean(self) -> None:
        """Гейт неверен, если краснеет на собственном репозитории."""
        module = _load_module()

        assert module.check_files(module.DEFAULT_CHANGELOG, module.DEFAULT_FRAGMENTS, []) == []
