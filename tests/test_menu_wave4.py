"""Tests for Wave 4 menu features (issues #445, #430).

#445: зацикленное меню, пункт «Веб-интерфейс», подсказка про файловый диалог,
сообщение о подстановке дефолта при нечисловом вводе.
#430: тумблер записи истории (сохраняется между запусками), nudge после FAIL.

Язык форсируется английским (как в test_cli.py) — ассерты на стабильные
подстроки локали en.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stepik_grader import cli, downloader, web
from stepik_grader.cli import interactive
from stepik_grader.core import user_settings


@pytest.fixture(autouse=True)
def _force_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_LANG", "en")


@pytest.fixture(autouse=True)
def _no_gui_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_pick_path_via_dialog", lambda *, want_dir: None)


def _failing_result() -> dict:
    return {
        "total": 1,
        "passed": 0,
        "failed": 1,
        "errors": 0,
        "total_time": 0.0,
        "avg_time": 0.0,
        "peak_memory_mb": 0.0,
        "first_fail": 1,
        "cases": [{"passed": False, "verdict": "WA", "time": 0.0, "memory": 0.0, "error": ""}],
    }


def _passing_result() -> dict:
    return {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "total_time": 0.0,
        "avg_time": 0.0,
        "peak_memory_mb": 0.0,
        "first_fail": None,
        "cases": [{"passed": True, "verdict": "AC", "time": 0.0, "memory": 0.0, "error": ""}],
    }


def _make_solution(tmp_path: Path) -> Path:
    sol = tmp_path / "task1.py"
    sol.write_text("print(1)\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "input_1.txt").write_text("1", encoding="utf-8")
    (tests_dir / "expected_1.txt").write_text("1", encoding="utf-8")
    return sol


# ---------------------------------------------------------------------------
# #445: цикл меню
# ---------------------------------------------------------------------------


def test_menu_loops_until_zero(tmp_path: Path, monkeypatch) -> None:
    """Меню зациклено: два прогона режима 1 подряд, затем «0» — выход."""
    sol = _make_solution(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _passing_result())
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: calls.append(solution))
    inputs = iter(["1", str(sol), "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert calls == [sol, sol]


def test_menu_mode3_bad_path_continues_not_exits(tmp_path: Path, monkeypatch, capsys) -> None:
    """Ошибка пути в режиме 3 (continue) не выкидывает из меню — «0» ещё работает."""
    inputs = iter(["3", "/no/such/dir", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "Directory not found" in out
    assert "Goodbye" in out  # дошли до «0», а не выпали после ошибки


def test_eof_exits_gracefully(monkeypatch, capsys) -> None:
    """Конец потока ввода (EOF) — корректный выход без трейсбека."""

    def _raise_eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)
    cli._interactive_menu()
    assert "Goodbye" in capsys.readouterr().out


def test_eof_on_nested_prompt_exits_gracefully(monkeypatch, capsys) -> None:
    """issue #555: EOF на вложенном prompt (путь после выбора режима 1) — goodbye, не трейсбек."""

    def _inputs():
        yield "1"  # выбор режима 1 проходит...
        raise EOFError  # ...а на запросе пути поток исчерпан (пайп `printf '1\n'`)

    gen = _inputs()
    monkeypatch.setattr("builtins.input", lambda *a: next(gen))
    cli._interactive_menu()  # не должно упасть трейсбеком на вложенном input()
    assert "Goodbye" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #445: подсказка про файловый диалог + сообщение о подстановке дефолта
# ---------------------------------------------------------------------------


def test_path_prompt_shows_dialog_hint(tmp_path: Path, monkeypatch, capsys) -> None:
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _passing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "picker" in capsys.readouterr().out  # path_dialog_hint (en)


def test_ask_number_invalid_prints_default_message(monkeypatch, capsys) -> None:
    ctx = cli._build_cli_context()
    monkeypatch.setattr("builtins.input", lambda *a: "abc")
    result = interactive._ask_number("? ", default=15, ctx=ctx)
    assert result == 15
    out = capsys.readouterr().out
    assert "abc" in out and "15" in out  # input_default_used


def test_ask_number_empty_uses_default_silently(monkeypatch, capsys) -> None:
    ctx = cli._build_cli_context()
    monkeypatch.setattr("builtins.input", lambda *a: "")
    result = interactive._ask_number("? ", default=42, ctx=ctx)
    assert result == 42
    assert "42" not in capsys.readouterr().out  # пустой ввод — без сообщения


# ---------------------------------------------------------------------------
# #445: пункт «Веб-интерфейс»
# ---------------------------------------------------------------------------


def test_web_menu_item_default_on_for_fresh_user(tmp_path: Path, monkeypatch) -> None:
    """Свежий пользователь (нет .grader_settings.json) → web-история ВКЛючена по
    умолчанию, как --serve (#395): пункт меню не должен расходиться с --serve."""
    monkeypatch.chdir(tmp_path)
    called: list[dict] = []
    monkeypatch.setattr(web, "run_server", lambda **k: called.append(k))
    inputs = iter(["6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert called == [{"record_history": True}]


def test_web_menu_item_respects_explicit_off(tmp_path: Path, monkeypatch) -> None:
    """Явно выключенный тумблер (settings False) → web-история тоже выключена."""
    monkeypatch.chdir(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(record_history=False),
        user_settings.default_settings_path(tmp_path),
    )
    called: list[dict] = []
    monkeypatch.setattr(web, "run_server", lambda **k: called.append(k))
    inputs = iter(["6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert called == [{"record_history": False}]


def test_web_menu_item_survives_keyboard_interrupt(monkeypatch, capsys) -> None:
    """Ctrl+C в сервере возвращает в меню (не роняет процесс) — «0» ещё работает."""

    def _raise_kbd(**_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(web, "run_server", _raise_kbd)
    inputs = iter(["6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "Goodbye" in capsys.readouterr().out


def test_web_menu_item_survives_oserror(monkeypatch, capsys) -> None:
    """issue #555: занятый порт (OSError) в run_server не роняет меню — «0» ещё работает."""

    def _raise_oserror(**_k):
        raise OSError("port already in use")

    monkeypatch.setattr(web, "run_server", _raise_oserror)
    inputs = iter(["6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "web server" in out  # server_start_failed (en)
    assert "Goodbye" in out  # дошли до «0» после ошибки, меню выжило


# ---------------------------------------------------------------------------
# #430: тумблер истории (персистентность) + nudge
# ---------------------------------------------------------------------------


def test_history_toggle_on_persists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = iter(["7", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "enabled" in out
    settings = user_settings.load_settings(user_settings.default_settings_path(tmp_path))
    assert settings.record_history is True


def test_history_toggle_off_persists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    path = user_settings.default_settings_path(tmp_path)
    user_settings.save_settings(user_settings.UserSettings(record_history=True), path)
    inputs = iter(["7", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "disabled" in capsys.readouterr().out
    assert user_settings.load_settings(path).record_history is False


def test_history_toggle_does_not_resurrect_revoked_consent(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """issue #997 (CNC-5-01/CNC-5-04): открытое меню воскрешало отозванное согласие.

    Меню снимает снапшот настроек при запуске. Пока оно открыто, согласие на
    отправку кода AI-провайдеру отзывают другим каналом (веб или
    ``--revoke-ai-consent``) — и переключение тумблера истории возвращало на
    диск весь снапшот, включая ``ai_hint_consent: true``.
    """
    monkeypatch.chdir(tmp_path)
    path = user_settings.default_settings_path(tmp_path)
    path.write_text(
        json.dumps({"ai_hint_consent": True, "ai_hint_consent_endpoint": "http://ai"}),
        encoding="utf-8",
    )

    inputs = iter(["7", "0"])

    def _input_with_revocation(*_a):
        value = next(inputs)
        if value == "7":
            # Момент между снапшотом меню и записью тумблера.
            user_settings.save_fields(path, ai_hint_consent=None, ai_hint_consent_endpoint=None)
        return value

    monkeypatch.setattr("builtins.input", _input_with_revocation)
    cli._interactive_menu()

    settings = user_settings.load_settings(path)
    assert settings.ai_hint_consent is None, "меню вернуло отозванное согласие на диск"
    assert settings.record_history is True, "тумблер истории не сохранился"


def test_history_toggle_save_failure_is_graceful(tmp_path: Path, monkeypatch, capsys) -> None:
    """Сбой сохранения (read-only cwd / полный диск) не роняет меню — best-effort,
    симметрично load_settings (review-находка PR-A)."""
    monkeypatch.chdir(tmp_path)

    def _boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(user_settings, "save_fields", _boom)
    inputs = iter(["7", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()  # не должно упасть трейсбеком
    assert "session" in capsys.readouterr().out  # history_save_failed (en)


def test_toggle_on_then_mode_records_history(tmp_path: Path, monkeypatch) -> None:
    """После включения тумблера режим 1 вызывается с record_history=True."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    seen: list[bool] = []
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: seen.append(k["record_history"]))
    inputs = iter(["7", "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert seen == [True]


def test_toggle_on_then_web_passes_true(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called: list[dict] = []
    monkeypatch.setattr(web, "run_server", lambda **k: called.append(k))
    inputs = iter(["7", "6", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert called == [{"record_history": True}]


def test_nudge_after_fail_when_history_off(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _failing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" in capsys.readouterr().out  # nudge_enable_history


def test_no_nudge_when_all_pass(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _passing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" not in capsys.readouterr().out


def test_no_nudge_when_history_on(tmp_path: Path, monkeypatch, capsys) -> None:
    """История включена → nudge не печатается даже при падении."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(record_history=True),
        user_settings.default_settings_path(tmp_path),
    )
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _failing_result())
    inputs = iter(["1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" not in capsys.readouterr().out


def test_nudge_only_once_per_session(tmp_path: Path, monkeypatch, capsys) -> None:
    """Два падающих прогона за одну сессию меню → nudge печатается РОВНО один раз
    (#430(2): не чаще раза за запуск; «запуск» = сессия зацикленного меню)."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _failing_result())
    inputs = iter(["1", str(sol), "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert capsys.readouterr().out.count("💡") == 1


def test_nudge_after_fail_mode2(tmp_path: Path, monkeypatch, capsys) -> None:
    """Падение в режиме 2 (папка) тоже вызывает nudge при выключенной истории."""
    monkeypatch.chdir(tmp_path)
    _make_solution(tmp_path)
    monkeypatch.setattr(cli, "run_tests", lambda *a, **k: _failing_result())
    inputs = iter(["2", str(tmp_path), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "💡" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #643: скачивание задачи из меню (пункт 8, downloader in-process)
# ---------------------------------------------------------------------------


def test_menu_shows_download_hint_and_item(monkeypatch, capsys) -> None:
    """Меню печатает подсказку про скачивание и пункт 8."""
    inputs = iter(["0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "Item 8 downloads" in out  # menu_download_hint (en)
    assert "Download a task from Stepik" in out  # menu_8 (en)


def test_download_menu_item_calls_downloader(monkeypatch) -> None:
    """Пункт 8 вызывает downloader.main() in-process, затем возврат в меню."""
    called: list[str] = []
    # Язык фиксируется явно: `cli._LANG` — глобальное состояние, и соседние
    # тесты этого файла оставляют его английским (issue #821 сделал язык
    # наблюдаемым здесь, раньше расхождение было незаметно).
    monkeypatch.setattr(cli, "_LANG", "ru")
    monkeypatch.setattr(downloader, "main", lambda lang: called.append(lang))
    inputs = iter(["8", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert called == ["ru"]


def test_download_menu_item_passes_menu_language(monkeypatch) -> None:
    """issue #821: язык меню доезжает до мастера — иначе он оставался русским."""
    called: list[str] = []
    monkeypatch.setattr(cli, "_LANG", "en")
    monkeypatch.setattr(downloader, "main", lambda lang: called.append(lang))
    inputs = iter(["8", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert called == ["en"]


def test_download_menu_item_survives_keyboard_interrupt(monkeypatch, capsys) -> None:
    """Ctrl+C в downloader.main() возвращает в меню, не роняет процесс — «0» ещё работает."""

    def _raise_kbd(lang: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(downloader, "main", _raise_kbd)
    inputs = iter(["8", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "Goodbye" in capsys.readouterr().out  # дошли до «0» после Ctrl+C


# ---------------------------------------------------------------------------
# #645(1): обогащённое пустое состояние (no_solutions_found)
# ---------------------------------------------------------------------------


def test_empty_state_mode3_shows_guidance(tmp_path: Path, monkeypatch, capsys) -> None:
    """Режим 3 в папке без решений печатает паттерн имён, путь и next-step (скачать)."""
    empty = tmp_path / "empty3"
    empty.mkdir()
    inputs = iter(["3", str(empty), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "task*.py" in out  # ожидаемый паттерн имён
    assert "Download it from Stepik" in out  # next-step (en)
    assert str(empty) in out  # путь в сообщении


def test_empty_state_mode4_shows_guidance(tmp_path: Path, monkeypatch, capsys) -> None:
    """Режим 4 в папке без решений — тот же обогащённый empty state с путём."""
    empty = tmp_path / "empty4"
    empty.mkdir()
    inputs = iter(["4", str(empty), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    out = capsys.readouterr().out
    assert "task*.py" in out
    assert str(empty) in out


# ---------------------------------------------------------------------------
# #645(2): positive-nudge после серии успехов (петля удержания)
# ---------------------------------------------------------------------------


def test_success_streak_nudge_when_history_off(tmp_path: Path, monkeypatch, capsys) -> None:
    """Серия из K успешных прогонов при выключенной истории → 🎉-nudge ровно раз за сессию."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: False)  # без падений
    inputs = iter(["1", str(sol), "1", str(sol), "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert capsys.readouterr().out.count("🎉") == 1  # nudge_success_streak


def test_no_success_nudge_below_threshold(tmp_path: Path, monkeypatch, capsys) -> None:
    """Меньше K успехов подряд → positive-nudge не печатается."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: False)
    inputs = iter(["1", str(sol), "1", str(sol), "0"])  # только 2 успеха (< 3)
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "🎉" not in capsys.readouterr().out


def test_success_streak_resets_on_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    """Провал сбрасывает стрик — после него не набирается K успехов подряд, 🎉 нет."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    results = iter([False, True, False, False])  # успех, ПРОВАЛ, успех, успех
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: next(results))
    inputs = iter(["1", str(sol), "1", str(sol), "1", str(sol), "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "🎉" not in capsys.readouterr().out  # макс. 2 успеха подряд после сброса


def test_no_success_nudge_when_history_on(tmp_path: Path, monkeypatch, capsys) -> None:
    """История включена → petля уже работает, positive-nudge не нужен."""
    monkeypatch.chdir(tmp_path)
    sol = _make_solution(tmp_path)
    user_settings.save_settings(
        user_settings.UserSettings(record_history=True),
        user_settings.default_settings_path(tmp_path),
    )
    monkeypatch.setattr(cli, "_run_mode_1", lambda solution, **k: False)
    inputs = iter(["1", str(sol), "1", str(sol), "1", str(sol), "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    cli._interactive_menu()
    assert "🎉" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# issue #836 (QA-09) — пункт меню 5 «Подучить». Соседние пункты покрыты, этот
# не выполнялся ни одним тестом: --insights идёт через другой код-путь
# (cli/commands.py), а тело _show_insights и его вызов из диспетчера — нет.
# ---------------------------------------------------------------------------


def test_menu_insights_without_history_says_no_data(tmp_path: Path, monkeypatch, capsys) -> None:
    """Нет `.grader_history.db` в cwd → локализованное «данных нет», не трейсбек."""
    monkeypatch.chdir(tmp_path)
    inputs = iter(["5", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert "No insights yet" in capsys.readouterr().out


def test_menu_insights_prints_cards(tmp_path: Path, monkeypatch, capsys) -> None:
    """Карточки есть → печатается сводка «Подучить» с ключом карточки."""
    from stepik_grader.core import insights
    from stepik_grader.core.insights import InsightCard

    monkeypatch.chdir(tmp_path)
    card = InsightCard(
        key="runtime-error:KeyError",
        category="failure",
        status="active",
        hits=3,
        runs_considered=10,
    )
    monkeypatch.setattr(insights, "learning_cards", lambda *a, **k: [card])
    inputs = iter(["5", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert "runtime-error:KeyError" in capsys.readouterr().out


def test_menu_insights_is_reached_from_dispatcher(tmp_path: Path, monkeypatch) -> None:
    """Ввод «5» приводит именно к пункту «Подучить», а не к соседнему."""
    called: list[object] = []
    monkeypatch.setattr(interactive, "_show_insights", lambda ctx: called.append(ctx))
    inputs = iter(["5", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert len(called) == 1


# ---------------------------------------------------------------------------
# issue #822 — воронка download→grade замкнута, пустые состояния ведут дальше.
# ---------------------------------------------------------------------------


def _stub_download(monkeypatch, dirs: list[Path]) -> None:
    """Пункт 8 «скачал» перечисленные каталоги."""
    monkeypatch.setattr(downloader, "main", lambda lang: list(dirs))


def test_download_offers_to_grade_downloaded_task(tmp_path: Path, monkeypatch) -> None:
    """PROD-09: после скачивания предлагается проверка, путь подставляется сам."""
    monkeypatch.setattr(cli, "_LANG", "ru")
    task = tmp_path / "04-slug"
    task.mkdir()
    _stub_download(monkeypatch, [task])
    graded: list[Path] = []
    monkeypatch.setattr(cli, "_run_mode_2", lambda d, **kw: graded.append(d) or False)
    inputs = iter(["8", "y", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert graded == [task]


def test_download_grade_offer_defaults_to_yes(tmp_path: Path, monkeypatch) -> None:
    """Пустой Enter — согласие: это предлагаемое следующее действие, а не опрос."""
    monkeypatch.setattr(cli, "_LANG", "ru")
    task = tmp_path / "t"
    task.mkdir()
    _stub_download(monkeypatch, [task])
    graded: list[Path] = []
    monkeypatch.setattr(cli, "_run_mode_2", lambda d, **kw: graded.append(d) or False)
    inputs = iter(["8", "", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert graded == [task]


def test_download_grade_offer_can_be_declined(tmp_path: Path, monkeypatch) -> None:
    """«n» — просто возврат в меню, как и было до issue #822."""
    monkeypatch.setattr(cli, "_LANG", "ru")
    task = tmp_path / "t"
    task.mkdir()
    _stub_download(monkeypatch, [task])
    graded: list[Path] = []
    monkeypatch.setattr(cli, "_run_mode_2", lambda d, **kw: graded.append(d) or False)
    inputs = iter(["8", "n", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert graded == []


def test_download_without_result_asks_nothing(monkeypatch) -> None:
    """Ничего не скачали (отказ авторизации, пустой ввод) — лишнего вопроса нет."""
    monkeypatch.setattr(cli, "_LANG", "ru")
    _stub_download(monkeypatch, [])
    inputs = iter(["8", "0"])  # «0» сразу: лишний вопрос съел бы его и уронил iter
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()  # StopIteration здесь означал бы лишний prompt


def test_download_several_tasks_offers_the_last(tmp_path: Path, monkeypatch, capsys) -> None:
    """Скачано несколько — предлагаем последнюю и говорим, что остальные никуда не делись."""
    monkeypatch.setattr(cli, "_LANG", "ru")
    first, second = tmp_path / "a", tmp_path / "b"
    for d in (first, second):
        d.mkdir()
    _stub_download(monkeypatch, [first, second])
    graded: list[Path] = []
    monkeypatch.setattr(cli, "_run_mode_2", lambda d, **kw: graded.append(d) or False)
    inputs = iter(["8", "y", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    assert graded == [second]
    assert "Скачано задач: 2" in capsys.readouterr().out


def test_insights_empty_state_points_to_menu_item_not_flag(monkeypatch, capsys) -> None:
    """PROD-11: из меню историю включают пунктом 7 — совет про `--history` был тупиком."""
    monkeypatch.setattr(cli, "_LANG", "ru")
    inputs = iter(["5", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    cli._interactive_menu()

    out = capsys.readouterr().out
    assert "пункт 7" in out
    assert "--history" not in out


def test_insights_flag_path_keeps_flag_wording() -> None:
    """Флаговый путь (`--insights`) по-прежнему советует флаг — там меню нет."""
    from stepik_grader.core.i18n import load_locale_messages

    assert "--history" in load_locale_messages("ru")["insights_no_data"]


# ---------------------------------------------------------------------------
# Меню говорит, что сделало — issue #997 (DES-2-08, JRN-1-05)
# ---------------------------------------------------------------------------


class TestProfileFallbackIsAnnounced:
    """DES-2-08: неверный номер профиля не подменяется молча."""

    def _ctx(self):
        return cli._build_cli_context()

    def test_bench_unknown_profile_says_so(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *_a: "7")

        repeats = interactive._ask_bench_profile(self._ctx())

        out = capsys.readouterr().out
        assert repeats == interactive._BENCH_PROFILES["2"]
        assert "7" in out and "2" in out

    def test_micro_unknown_profile_says_so(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *_a: "9")

        number = interactive._ask_micro_profile(self._ctx())

        out = capsys.readouterr().out
        assert number == interactive._MICRO_PROFILES["2"]
        assert "9" in out

    def test_valid_profile_is_silent(self, monkeypatch, capsys):
        """Регрессия: правильный выбор не обрастает лишней строкой."""
        ctx = self._ctx()
        monkeypatch.setattr("builtins.input", lambda *_a: "3")

        repeats = interactive._ask_bench_profile(ctx)

        warning = ctx.t("profile_unknown_default_used", value="3", default="2")
        assert repeats == interactive._BENCH_PROFILES["3"]
        assert warning not in capsys.readouterr().out

    def test_empty_input_keeps_default_without_warning(self, monkeypatch, capsys):
        """Пустой ввод — это выбор дефолта, а не опечатка."""
        ctx = self._ctx()
        monkeypatch.setattr("builtins.input", lambda *_a: "")

        repeats = interactive._ask_bench_profile(ctx)

        warning = ctx.t("profile_unknown_default_used", value="", default="2")
        assert repeats == interactive._BENCH_PROFILES["2"]
        assert warning not in capsys.readouterr().out


class TestInsightsNamesItsSource:
    """JRN-1-05: раздел «Подучить» называет базу и окно, по которым считал."""

    def test_source_line_printed_with_cards(self, tmp_path, monkeypatch, capsys):
        from stepik_grader.core import history_recording, insights

        db_path = tmp_path / "history.db"
        card = insights.InsightCard(
            key="runtime-error:KeyError",
            category="failure",
            status="active",
            hits=3,
            runs_considered=10,
        )
        monkeypatch.setattr(history_recording, "default_history_db_path", lambda *a, **k: db_path)
        monkeypatch.setattr(insights, "learning_cards", lambda *a, **k: [card])

        interactive._show_insights(cli._build_cli_context())

        out = capsys.readouterr().out
        assert str(db_path) in out, "не сказано, из какой базы взяты карточки"

    def test_no_source_line_when_nothing_to_show(self, tmp_path, monkeypatch, capsys):
        """Пустой раздел не должен обрастать технической шапкой."""
        from stepik_grader.core import history_recording, insights

        db_path = tmp_path / "history.db"
        monkeypatch.setattr(history_recording, "default_history_db_path", lambda *a, **k: db_path)
        monkeypatch.setattr(insights, "learning_cards", lambda *a, **k: [])

        interactive._show_insights(cli._build_cli_context())

        assert str(db_path) not in capsys.readouterr().out
