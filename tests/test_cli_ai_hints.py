"""CLI-тесты --ai-hints (issue #435, ADR-0003): врезка AI-объяснений в режим 1.

Проверяют всю цепочку options→main→commands→_print_ai_hints. Сеть замокана
(requests.post); AI никогда не роняет грейдинг и не печатается без падений.
"""

from __future__ import annotations

import dataclasses
import pathlib

import requests

from stepik_grader import cli
from stepik_grader.cli import commands
from stepik_grader.config import CONFIG


def _make_task(tmp_path: pathlib.Path, body: str, *, name: str = "sol.py") -> pathlib.Path:
    """Решение + tests/ (legacy N/N.clue). ``name`` — режимы 3/4 сканируют папку
    через find_all_solution_files, которому нужно имя-решение (``task1.py``)."""
    sol = tmp_path / name
    sol.write_text(body, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "1").write_text("4", encoding="utf-8")
    (tests / "1.clue").write_text("5", encoding="utf-8")
    return sol


def _configure(monkeypatch) -> None:
    """Включить AI-канал (is_configured → True) для commands._print_ai_hints."""
    cfg = dataclasses.replace(CONFIG, ai_base_url="http://test.local/v1", ai_model="m")
    monkeypatch.setattr(commands, "get_config", lambda: cfg)


class _Resp:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return {"choices": [{"message": {"content": "Ты прибавляешь 2, а надо 1."}}]}


def test_ai_hints_unconfigured_shows_enable_hint(tmp_path, monkeypatch, capsys) -> None:
    """--ai-hints без настройки провайдера → graceful skip с подсказкой, не краш."""
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path, "print(int(input()) + 2)\n")  # 4+2=6 ≠ 5 → WA
    cli.main(["--mode", "1", "--file", str(sol), "--ai-hints"])
    out = capsys.readouterr().out
    assert "провайдер не настроен" in out


def test_ai_hints_configured_prints_marked_hint(tmp_path, monkeypatch, capsys) -> None:
    """Настроенный канал + упавший кейс → печатается помеченная AI-подсказка."""
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path, "print(int(input()) + 2)\n")
    _configure(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp())
    cli.main(["--mode", "1", "--file", str(sol), "--ai-hints"])
    out = capsys.readouterr().out
    assert "AI-подсказка" in out  # маркер AI-generated
    assert "прибавляешь 2" in out
    assert "sol.py" in out and "тест 1" in out


def test_ai_channel_failure_never_breaks_grading(tmp_path, monkeypatch, capsys) -> None:
    """Сбой AI-канала (requests кидает) → грейдинг завершается, вердикт напечатан."""
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path, "print(int(input()) + 2)\n")
    _configure(monkeypatch)

    def _boom(*_a: object, **_k: object) -> object:
        raise requests.exceptions.ConnectionError("no network")

    monkeypatch.setattr(requests, "post", _boom)
    cli.main(["--mode", "1", "--file", str(sol), "--ai-hints"])  # не должно кинуть
    out = capsys.readouterr().out
    assert "FAIL" in out  # вердикт напечатан
    assert "AI-подсказка" not in out  # канал тихо пропущен


def test_no_flag_no_ai_output(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path, "print(int(input()) + 2)\n")
    cli.main(["--mode", "1", "--file", str(sol)])  # без --ai-hints
    out = capsys.readouterr().out
    assert "AI-подсказк" not in out
    assert "провайдер не настроен" not in out


def test_ai_hints_passing_case_silent(tmp_path, monkeypatch, capsys) -> None:
    """Настроенный канал, но кейсы прошли → нечего объяснять, вывода нет."""
    monkeypatch.chdir(tmp_path)
    sol = _make_task(tmp_path, "print(int(input()) + 1)\n")  # 4+1=5 → AC
    _configure(monkeypatch)
    cli.main(["--mode", "1", "--file", str(sol), "--ai-hints"])
    out = capsys.readouterr().out
    assert "AI-подсказк" not in out


def test_ai_hints_mode3_explains_erroring_solution(tmp_path, monkeypatch, capsys) -> None:
    """Режим 3 (бенчмарк) + --ai-hints → решение с ошибкой исполнения объясняется
    тем же core-хелпером (issue #542)."""
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path, "print(int(input()) // 0)\n", name="task1.py")  # ZeroDivisionError
    _configure(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp())
    cli.main(["--mode", "3", "--dir", str(tmp_path), "--repeats", "1", "--ai-hints"])
    out = capsys.readouterr().out
    assert "AI-подсказка" in out  # маркер AI-generated по крашнувшемуся решению


def test_ai_hints_mode4_explains_erroring_solution(tmp_path, monkeypatch, capsys) -> None:
    """Режим 4 (micro-bench) + --ai-hints → крашнувшееся решение объясняется (issue #542)."""
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path, "print(int(input()) // 0)\n", name="task1.py")  # ZeroDivisionError
    _configure(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp())
    cli.main(["--mode", "4", "--dir", str(tmp_path), "--number", "1", "--ai-hints"])
    out = capsys.readouterr().out
    assert "AI-подсказка" in out


def test_ai_hints_mode3_no_flag_silent(tmp_path, monkeypatch, capsys) -> None:
    """Режим 3 без --ai-hints → никакого AI-вывода, даже если решение крашится."""
    monkeypatch.chdir(tmp_path)
    _make_task(tmp_path, "print(int(input()) // 0)\n", name="task1.py")
    _configure(monkeypatch)
    cli.main(["--mode", "3", "--dir", str(tmp_path), "--repeats", "1"])
    out = capsys.readouterr().out
    assert "AI-подсказк" not in out
