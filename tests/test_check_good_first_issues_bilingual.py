"""Tests for scripts/check_good_first_issues_bilingual.py — двуязычие задач новичка.

Guard-the-guard: на подделанных телах issue проверка обязана отличать три
состояния — перевода нет, якорь без перевода, «перевод», оставшийся русским.
Сеть не трогается: ``opener`` подменяется, как в тестах бейджа пула задач.

Скрипт лежит в ``scripts/`` (не на sys.path) — грузим по пути, тем же приёмом,
что ``test_check_docs_guardrails.py``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_good_first_issues_bilingual.py"

_RUSSIAN = """## Что не так

Кнопка «Проверить» в веб-интерфейсе не показывает вердикт, если решение упало с
исключением до первого вывода: пользователь видит пустую панель и не понимает,
запустился ли прогон вообще.
"""

_ENGLISH = """## In English

### What is wrong

The "Check" button in the web interface shows no verdict when the solution
crashes before producing any output: the panel stays empty and the reader
cannot tell whether the run started at all.

### What to do

Render the failure card in that branch and cover it with a test that fails
before the fix.
"""


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_gfi_bilingual", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_opener(items: list[dict[str, object]]):
    """Подделка ``urlopen``: отдаёт ответ поиска issue, не касаясь сети."""

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc: object) -> None:
            self.close()

    def opener(_request: object) -> _Response:
        return _Response(json.dumps({"items": items}).encode("utf-8"))

    return opener


class TestCheckIssue:
    def test_bilingual_body_passes(self) -> None:
        module = _load_module()

        assert module.check_issue(42, f"{_RUSSIAN}\n---\n\n{_ENGLISH}") is None

    def test_missing_english_half_is_reported(self) -> None:
        """Задача только по-русски — ровно то состояние, ради которого правило заведено."""
        module = _load_module()

        problem = module.check_issue(42, _RUSSIAN)

        assert problem is not None
        assert "#42" in problem
        assert "In English" in problem

    def test_anchor_without_translation_is_reported(self) -> None:
        """Заголовок без текста прошёл бы проверку «якорь есть», не дав читателю ничего."""
        module = _load_module()

        problem = module.check_issue(7, f"{_RUSSIAN}\n---\n\n## In English\n\nTBD\n")

        assert problem is not None
        assert "перевода нет" in problem

    def test_russian_text_under_the_anchor_is_reported(self) -> None:
        """Якорь поставили, абзац скопировали как есть — не перевод.

        Без этой проверки правило выполнялось бы формально: заголовок на месте,
        а под ним тот же русский текст.
        """
        module = _load_module()

        problem = module.check_issue(9, f"{_RUSSIAN}\n---\n\n## In English\n\n{_RUSSIAN}")

        assert problem is not None
        assert "осталась русской" in problem

    def test_quoted_russian_names_are_tolerated(self) -> None:
        """Перевод законно цитирует русские названия разделов и ключей локали.

        Порог не ноль именно поэтому: «Подучить» и текст ошибки в кавычках —
        часть постановки, а не непереведённый абзац.
        """
        module = _load_module()
        english = (
            "## In English\n\n"
            "The «Подучить» section shows no cards when the history database is "
            "empty. Instead of an empty list, render the onboarding hint so the "
            "reader knows the section works and simply has nothing to show yet. "
            "The locale key is `insights_empty`.\n"
        )

        assert module.check_issue(11, f"{_RUSSIAN}\n---\n\n{english}") is None

    def test_russian_inside_code_is_not_counted(self) -> None:
        """Вывод программы в блоке кода не переводится — он и есть предмет задачи."""
        module = _load_module()
        english = (
            "## In English\n\n"
            "The CLI prints the wrong hint when the tests folder is missing. "
            "Reproduce it by running the grader in an empty directory and compare "
            "the message with the one below:\n\n"
            "```\nТесты не найдены для: task1_1.py — проверьте папку tests/\n```\n"
        )

        assert module.check_issue(13, f"{_RUSSIAN}\n---\n\n{english}") is None

    def test_plain_english_heading_is_also_an_anchor(self) -> None:
        module = _load_module()
        english = _ENGLISH.replace("## In English", "## English")

        assert module.check_issue(15, f"{_RUSSIAN}\n---\n\n{english}") is None

    def test_empty_body_is_reported(self) -> None:
        module = _load_module()

        assert module.check_issue(17, "") is not None


class TestFetchAndMain:
    def test_fetch_returns_number_and_body_pairs(self) -> None:
        module = _load_module()
        opener = _fake_opener([{"number": 5, "body": "текст"}, {"number": 6, "body": None}])

        assert module.fetch_open_issues(opener=opener) == [(5, "текст"), (6, "")]

    def test_entries_without_a_number_are_skipped(self) -> None:
        """Форма ответа GitHub — не наша ответственность, но падать на ней нельзя."""
        module = _load_module()
        opener = _fake_opener([{"body": "нет номера"}, {"number": 8, "body": "есть"}])

        assert module.fetch_open_issues(opener=opener) == [(8, "есть")]

    def test_untranslated_issue_warns_but_does_not_fail(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Состояние трекера — не дефект кода: посторонний PR из-за него не падает.

        Тот же принцип, что у бейджа пула задач: падение блокировало бы чужие
        изменения из-за не дописанного владельцем перевода.
        """
        module = _load_module()
        monkeypatch.setattr(module, "fetch_open_issues", lambda *_a, **_k: [(21, _RUSSIAN)])

        code = module.main([])

        assert code == 0
        assert "::warning::" in capsys.readouterr().out

    def test_network_failure_is_the_only_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("сеть недоступна")

        monkeypatch.setattr(module, "fetch_open_issues", _boom)

        assert module.main([]) == 1

    def test_help_prints_on_a_single_byte_console(self) -> None:
        """Справка печатается под cp1252, а не падает вместо неё.

        Ровно это и уронило Windows-job: описания флагов русские, консоль
        Windows по умолчанию однобайтовая, и `--help` возвращал 1 с
        ``UnicodeEncodeError`` — отказ, подменяющий собственной причиной ту, о
        которой спрашивали. Соседние гейты лечатся тем же ``_force_utf8_stdout``;
        тест держит его на месте и на Linux, где cp1252 задаётся переменной.

        Проверяется КОД ВОЗВРАТА и отсутствие ошибки кодировки, а не текст:
        содержимое справки сверяет соседний тест — прямым вызовом, без
        подпроцесса. Перехват stdout дочернего процесса на Windows-раннерах
        оказался ненадёжен (три job'а подряд отдали ``None`` вместо текста при
        нулевом коде возврата), и сверять текст через него значит держать в
        наборе проверку, которая падает не по своей теме.
        """
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        )

        assert "UnicodeEncodeError" not in (result.stderr or ""), result.stderr
        assert result.returncode == 0, result.stderr

    def test_help_names_the_label_and_the_repo_flag(self, capsys) -> None:
        """Справка называет метку и флаги — без подпроцесса, значит стабильно везде."""
        module = _load_module()

        with pytest.raises(SystemExit) as exit_info:
            module.main(["--help"])

        assert exit_info.value.code == 0
        printed = capsys.readouterr().out
        assert "good first issue" in printed
        assert "--repo" in printed
