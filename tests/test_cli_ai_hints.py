"""CLI-тесты --ai-hints (issue #435, ADR-0003): врезка AI-объяснений в режим 1.

Проверяют всю цепочку options→main→commands→_print_ai_hints. Сеть замокана
(requests.post); AI никогда не роняет грейдинг и не печатается без падений.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest
import requests

from stepik_grader import cli
from stepik_grader.cli import commands
from stepik_grader.config import CONFIG
from stepik_grader.core import ai_hints


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
    """Включить AI-канал (is_configured → True) для commands._print_ai_hints.

    Заодно снимается consent-гейт (issue #630): эти тесты проверяют ВЫВОД
    подсказок, а само согласие покрыто отдельно в test_w1_cli_consent.py.
    """
    # issue #812: адрес должен быть допустимым (https куда угодно, http — только
    # на петлю), иначе запрос отсекается до вывода подсказок. Политика адресов
    # покрыта отдельно (tests/test_ai_hints.py::TestBaseUrlAllowlist).
    cfg = dataclasses.replace(CONFIG, ai_base_url="https://test.local/v1", ai_model="m")
    monkeypatch.setattr(commands, "get_config", lambda: cfg)
    monkeypatch.setattr(commands, "_ensure_ai_consent", lambda _base_url=None: True)


class _Resp:
    # issue #975: канал разбирает код ответа, чтобы отличить отказ провайдера
    # от «не настроено», поэтому фейк обязан его нести.
    status_code = 200

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
    # issue #975: канал пропущен, но не молча — иначе «не работает» неотличимо
    # от «выключено», и пользователь ищет причину в своих настройках.
    assert "до провайдера не достучаться" in out


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


# ---------------------------------------------------------------------------
# Потолок AI-вызовов — issue #812 (TREND-02)
# ---------------------------------------------------------------------------


def test_ai_hints_respect_call_ceiling(tmp_path, monkeypatch, capsys) -> None:
    """Потолка не было вовсе: N упавших кейсов = N последовательных POST.

    При дефолтном таймауте 20 с папка на 40 решений — это 13 минут ожидания и
    40 оплаченных запросов, о которых пользователь не предупреждён.
    """
    _configure(monkeypatch)
    cfg = dataclasses.replace(
        CONFIG, ai_base_url="https://test.local/v1", ai_model="m", ai_max_hints=2
    )
    monkeypatch.setattr(commands, "get_config", lambda: cfg)

    calls: list[object] = []
    monkeypatch.setattr(
        commands.ai_hints,
        "explain_failure_detailed",
        lambda fc, config: (
            calls.append(fc) or commands.ai_hints.AiHintOutcome(text="подсказка", reason=None)
        ),
    )

    rows = [
        (
            tmp_path / "task1.py",
            {"cases": [{"passed": False, "verdict": "WA"} for _ in range(5)]},
        )
    ]
    commands._print_ai_hints(rows)

    assert len(calls) == 2, "потолок ai_max_hints не применён"
    assert "потолок 2" in capsys.readouterr().out  # обрыв объяснён, а не молчалив


def test_ai_hints_below_ceiling_are_all_shown(tmp_path, monkeypatch, capsys) -> None:
    """Контроль: пока кейсов меньше потолка, ничего не режется и не сообщается."""
    _configure(monkeypatch)
    cfg = dataclasses.replace(
        CONFIG, ai_base_url="https://test.local/v1", ai_model="m", ai_max_hints=5
    )
    monkeypatch.setattr(commands, "get_config", lambda: cfg)

    calls: list[object] = []
    monkeypatch.setattr(
        commands.ai_hints,
        "explain_failure_detailed",
        lambda fc, config: (
            calls.append(fc) or commands.ai_hints.AiHintOutcome(text="подсказка", reason=None)
        ),
    )

    rows = [(tmp_path / "task1.py", {"cases": [{"passed": False, "verdict": "WA"}] * 3})]
    commands._print_ai_hints(rows)

    assert len(calls) == 3
    assert "потолок" not in capsys.readouterr().out


class TestModelFamilies:
    """issue #975 (TRE-1-01): payload обязан соответствовать семейству модели.

    У o-серии, `gpt-5` и `deepseek-reasoner` контракт другой:
    `max_completion_tokens` вместо `max_tokens`, температура не принимается.
    Обычный payload они отвергают целиком (400) — и подсказки молча не работают
    ровно у тех, кто включил самую свежую модель.
    """

    @pytest.mark.parametrize(
        "model",
        ["o1", "o3-mini", "gpt-5", "gpt-5.1", "gpt-5-mini", "deepseek-reasoner", "openai/o4-mini"],
    )
    def test_reasoning_families_recognised(self, model: str) -> None:
        assert ai_hints._is_reasoning_model(model) is True

    @pytest.mark.parametrize("model", ["gpt-4o", "llama3", "opus", "mistral", "gpt-3.5-turbo"])
    def test_ordinary_models_keep_plain_payload(self, model: str) -> None:
        """Обратная сторона: обычные модели не должны потерять `temperature`."""
        assert ai_hints._is_reasoning_model(model) is False


class TestProviderRefusalIsAudible:
    """issue #975 (TRE-1-03): отказ провайдера отличим от выключенного канала."""

    @staticmethod
    def _outcome(monkeypatch, status: int) -> object:
        class _Refusal:
            status_code = status

            def json(self) -> object:  # pragma: no cover — до разбора не доходит
                return {}

        monkeypatch.setattr(requests, "post", lambda url, **kw: _Refusal())
        cfg = dataclasses.replace(CONFIG, ai_base_url="https://test.local/v1", ai_model="m")
        ctx = ai_hints.FailureContext(verdict="WA", lang="ru")
        return ai_hints.explain_failure_detailed(ctx, cfg)

    @pytest.mark.parametrize(
        "status,reason",
        [
            (401, "unauthorized"),
            (403, "forbidden"),
            (429, "rate_limited"),
            (400, "bad_request"),
            (500, "server_error"),
            (418, "http_error"),
        ],
    )
    def test_http_status_becomes_reason(self, monkeypatch, status: int, reason: str) -> None:
        outcome = self._outcome(monkeypatch, status)

        assert outcome.text is None
        assert outcome.reason == reason

    def test_unconfigured_channel_has_no_reason(self) -> None:
        """Выключенный канал — не отказ: `reason` пуст, и жаловаться не на что."""
        ctx = ai_hints.FailureContext(verdict="WA", lang="ru")

        outcome = ai_hints.explain_failure_detailed(ctx, dataclasses.replace(CONFIG))

        assert outcome.text is None and outcome.reason is None

    def test_legacy_wrapper_still_returns_text(self, monkeypatch) -> None:
        """`explain_failure` остаётся в `__all__` и отдаёт текст как раньше."""
        monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp())
        cfg = dataclasses.replace(CONFIG, ai_base_url="https://test.local/v1", ai_model="m")

        hint = ai_hints.explain_failure(ai_hints.FailureContext(verdict="WA", lang="ru"), cfg)

        assert hint is not None and "прибавляешь 2" in hint
