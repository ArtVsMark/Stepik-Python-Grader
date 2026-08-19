"""Тесты `stepik_grader.stdio_encoding` и guard охвата точек входа.

Дефект #1108 был не в самом приёме — он работал, — а в **охвате**: переключение
на UTF-8 звала одна точка входа из семи, и остальные падали
``UnicodeEncodeError`` в консоли cp1251. Поэтому здесь два разных теста: юнит на
поведение функции и guard, который перечисляет точки входа поимённо. Без guard'а
восьмая точка входа появится без защиты, и дефект вернётся третий раз (после
#64 и #1095).
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys

import pytest

from stepik_grader.stdio_encoding import force_utf8_stdio

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Точки входа, которые печатают данные Stepik или собственные сообщения и
# запускаются процессом. Список ведётся руками намеренно: новая точка входа —
# осознанное решение, и вместе с ним принимается решение про её вывод.
_ENTRY_POINTS = (
    "src/stepik_grader/cli/__init__.py",
    "src/stepik_grader/downloader.py",
    "src/stepik_grader/diagnostic_stepik.py",
    "src/stepik_grader/launcher.py",
    "scripts/corpus_fetch.py",
    "scripts/corpus_sweep.py",
    "scripts/corpus_run.py",
)

# Автономные скрипты: пакет не импортируют принципиально (работают в CI без
# `pip install -e .`), поэтому держат собственную копию приёма. Общий хелпер им
# не подходит — проверяем, что копия на месте.
_STANDALONE_SCRIPTS = (
    "scripts/check_docs_guardrails.py",
    "scripts/check_pr_ready.py",
    "scripts/gh_rest.py",
    "scripts/check_test_isolation.py",
    "scripts/check_work_overlap.py",
    "scripts/extract_release_notes.py",
    "scripts/preflight.py",
    "scripts/skip_inventory.py",
)

# Гейты, которые обязаны печатать в любой консоли: их запускают перед КАЖДЫМ
# коммитом и мержем, и падение вывода подменяет настоящий результат проверки
# своей собственной ошибкой. Проверяются не чтением исходника, а запуском.
_GATES = ("scripts/preflight.py", "scripts/check_pr_ready.py", "scripts/gh_rest.py")

# Защита засчитывается в любой из двух форм: вызов хелпера (общего или
# собственного) либо прямой `reconfigure` на уровне модуля — так сделано в
# `check_work_overlap.py`. Guard следит за наличием защиты, а не за её
# оформлением: требовать одну форму значило бы ломать рабочий код ради стиля.
_CALL_RE = re.compile(r"\b_?force_utf8_std(io|out)\(\)|reconfigure\(\s*encoding=[\"']utf-8[\"']")


class _FakeStream:
    """Поток с ``reconfigure``: запоминает, с чем его звали."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)
        if "encoding" in kwargs:
            self.encoding = kwargs["encoding"]


class TestForceUtf8Stdio:
    """Поведение самого переключателя."""

    def test_narrow_encoding_is_switched_with_replace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out, err = _FakeStream("cp1251"), _FakeStream("cp866")
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", err)

        force_utf8_stdio()

        assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
        assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]

    @pytest.mark.parametrize("encoding", ["utf-8", "UTF-8", "utf8"])
    def test_utf8_stream_is_left_alone(
        self, encoding: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Поток уже в UTF-8 — трогать нечего, лишний reconfigure только вредит."""
        stream = _FakeStream(encoding)
        monkeypatch.setattr("sys.stdout", stream)
        monkeypatch.setattr("sys.stderr", _FakeStream("utf-8"))

        force_utf8_stdio()

        assert stream.calls == []

    def test_stream_without_reconfigure_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Перехваченный pytest'ом или подменённый поток не должен ронять запуск."""

        class _Bare:
            encoding = "cp1251"

        monkeypatch.setattr("sys.stdout", _Bare())
        monkeypatch.setattr("sys.stderr", _Bare())

        force_utf8_stdio()  # не бросает


class TestEntryPointCoverage:
    """Guard: каждая точка входа переключает вывод до первой печати."""

    @pytest.mark.parametrize("relative", _ENTRY_POINTS)
    def test_entry_point_calls_the_helper(self, relative: str) -> None:
        source = (_ROOT / relative).read_text(encoding="utf-8")

        assert _CALL_RE.search(source), (
            f"{relative}: точка входа не зовёт force_utf8_stdio() — в консоли cp1251 "
            f"она упадёт UnicodeEncodeError на первом же эмодзи (issue #1108)"
        )

    @pytest.mark.parametrize("relative", _STANDALONE_SCRIPTS)
    def test_standalone_script_keeps_its_own_copy(self, relative: str) -> None:
        source = (_ROOT / relative).read_text(encoding="utf-8")

        assert "from stepik_grader" not in source, (
            f"{relative}: скрипт стал зависеть от пакета — тогда ему полагается общий "
            f"хелпер stdio_encoding, а не собственная копия"
        )
        assert _CALL_RE.search(source), f"{relative}: потеряна собственная защита кодировки"


class TestSubprocessDecodesUtf8:
    """Guard: текстовый ``subprocess`` не полагается на кодировку системы.

    Симметричная половина той же беды. Печать чинит ``force_utf8_stdio``, а
    ЧТЕНИЕ чужого вывода — нет: ``text=True`` без ``encoding`` декодирует в
    локаль машины, и русский ответ (`gh api` с темами коммитов, вывод git,
    сообщение PowerShell) падает ``UnicodeDecodeError`` на cp1251.

    Прецедент, ради которого guard написан: `check_pr_ready.py` так ронял свой
    вызов `gh api`, а `UnicodeDecodeError` — подкласс `ValueError` и попадал в
    `except (OSError, CalledProcessError, ValueError)`. Гейт молча уходил на
    анонимный REST, упирался в лимит 60 запросов в час и отвечал «rate limit
    exceeded» вместо «готов / не готов» — то есть переставал работать вовсе,
    ничего не сломав видимо.
    """

    @staticmethod
    def _text_calls_without_encoding(source: str) -> list[int]:
        """Строки вызовов ``subprocess``, читающих текст без явной кодировки."""
        found: list[int] = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            names = {kw.arg for kw in node.keywords if kw.arg}
            textual = {"text", "universal_newlines"} & names
            if not textual or "encoding" in names:
                continue
            if any(
                isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
                if kw.arg in textual
            ):
                found.append(node.lineno)
        return found

    @pytest.mark.parametrize(
        "relative",
        sorted(
            str(path.relative_to(_ROOT).as_posix())
            for path in [*(_ROOT / "scripts").glob("*.py"), *(_ROOT / "src").rglob("*.py")]
        ),
    )
    def test_no_locale_dependent_decoding(self, relative: str) -> None:
        source = (_ROOT / relative).read_text(encoding="utf-8")

        lines = self._text_calls_without_encoding(source)

        assert not lines, (
            f"{relative}: строки {lines} читают вывод процесса с text=True без "
            f"encoding — на машине с cp1251 русский ответ упадёт UnicodeDecodeError, "
            f"а он подкласс ValueError и обычно проглатывается вместе с ним"
        )


class TestGatesSurviveNarrowConsole:
    """Гейты печатают справку в однобайтовой консоли, а не падают на рамке.

    Чтение исходника ловит отсутствие вызова, но не его позицию: защита, вызванная
    после первой печати, guard'у неотличима от рабочей. Поэтому гейты — те самые
    команды, которые запускают перед каждым коммитом и мержем, — проверяются
    запуском. `PYTHONIOENCODING=cp1251` воспроизводит русскую консоль Windows на
    любой ОС: кириллица в неё кодируется, а `─`, `→` и `…` из рамок отчёта — нет.
    """

    @pytest.mark.parametrize("relative", _GATES)
    def test_help_prints_in_cp1251(self, relative: str) -> None:
        result = subprocess.run(
            [sys.executable, str(_ROOT / relative), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "cp1251"},
            cwd=_ROOT,
            timeout=60,
        )

        assert "UnicodeEncodeError" not in (result.stderr or ""), (
            f"{relative} --help упал на кодировке консоли: гейт сообщает свою "
            f"ошибку вместо результата проверки (issue #1108, четвёртый возврат)"
        )
        assert result.returncode == 0, f"{relative} --help вернул {result.returncode}"
