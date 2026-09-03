"""Тесты второй половины правила 173 (issue #1419).

Первая половина — связь с задачей — проверяется ДО слияния и держится
``check_pr_ready.py``. Врёт же связь ПОСЛЕ: площадка закрыла не ту задачу, не
закрыла ничего, либо частичная работа уехала, а остаток назвать забыли.

Проверка гоняется на том, что она обязана **отвергнуть** (правило 140), и
отдельно — на том, что отвергать не должна: гейт, краснеющий на верном ответе,
снимут первой же правкой.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_issue_state_after_merge.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_issue_state_after_merge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

_REMAINDER = "Осталось:\n\n- [ ] поверхность точки сбоя в основном сценарии\n"


def _pull(number: int, body: str) -> dict[str, Any]:
    return {"number": number, "body": body}


# --- разбор трёх ответов ---------------------------------------------------------


def test_closing_forms_are_all_recognised() -> None:
    """Площадка принимает несколько глаголов — их принимает и проверка."""
    body = "Closes #1\nFixes #2\nresolved #3\n"

    assert _MODULE.closing_numbers(body) == [1, 2, 3]


def test_partial_answer_is_recognised() -> None:
    """Второй ответ правила 173 — «Часть #N — что именно сделано»."""
    body = "Часть #982 — движок проверок в границах модуля диагностики\n"

    assert _MODULE.partial_numbers(body) == [982]


def test_partial_without_the_what_is_not_an_answer() -> None:
    """«Часть #982» без названного сделанного ответом не является.

    Иначе второй ответ выродился бы в отписку: связь объявлена, остаток нет.
    """
    assert _MODULE.partial_numbers("Часть #982\n") == []


def test_remainder_must_be_checkboxes_not_prose() -> None:
    """Остаток называется галочками (правило 028), а не прозой.

    По прозе состояние приходится вычислять чтением — то есть решать за автора.
    """
    assert _MODULE.remainder_is_named("- [ ] осталось сделать это")
    assert not _MODULE.remainder_is_named("Осталось сделать это, потом то.")


def test_a_fully_checked_list_is_not_a_remainder() -> None:
    """Все галочки закрыты — остатка нет, и задача обязана быть закрытой."""
    assert not _MODULE.remainder_is_named("- [x] сделано\n- [X] и это\n")


# --- что проверка обязана отвергнуть ---------------------------------------------


def test_closed_declaration_with_an_open_issue_is_a_finding() -> None:
    """Слито с «Closes», а задача открыта: площадка закрытие не выполнила.

    Незакрытая сделанная задача дешева на вид и дорога по цене — держит
    очередь, попадает в отчёты, и следующее окно берёт её заново.
    """
    found = _MODULE.mismatches([_pull(10, "Closes #5")], {5: ("open", "")})

    assert len(found) == 1
    assert found[0].issue == 5 and found[0].pull == 10


def test_partial_work_on_a_closed_issue_is_a_finding() -> None:
    """Объявлено частью, а задача закрыта: остаток исчез вместе с ней.

    Это хуже незакрытой: найти остаток можно только по памяти того, кто сливал.
    """
    found = _MODULE.mismatches([_pull(11, "Часть #6 — половина")], {6: ("closed", _REMAINDER)})

    assert len(found) == 1
    assert "исчез вместе с ней" in found[0].what


def test_a_closed_issue_with_everything_ticked_is_silent() -> None:
    """Закрытие после доделанного остатка — нормальный конец, а не находка.

    Частичное изменение к моменту закрытия уже перестало быть частичным.
    Гейт, краснеющий на верном ответе, снимут первой же правкой.
    """
    done = "- [x] сделано\n- [x] и это\n"

    assert _MODULE.mismatches([_pull(17, "Часть #20 — половина")], {20: ("closed", done)}) == []


def test_partial_work_without_a_named_remainder_is_a_finding() -> None:
    """Объявлено частью, задача открыта, но остатка в ней нет.

    Живой случай, найденный этой проверкой на первом же прогоне: PR #1415
    объявил себя частью #982, а «Порядок работ» там был прозой.
    """
    found = _MODULE.mismatches([_pull(12, "Часть #7 — половина")], {7: ("open", "просто текст")})

    assert len(found) == 1
    assert "галочками" in found[0].what


# --- что проверка отвергать не должна --------------------------------------------


def test_closed_declaration_with_a_closed_issue_is_silent() -> None:
    """Обычный исход: объявили закрытой, площадка закрыла."""
    assert _MODULE.mismatches([_pull(13, "Closes #8")], {8: ("closed", "")}) == []


def test_partial_work_with_a_named_remainder_is_silent() -> None:
    """Второй ответ, выполненный целиком: задача открыта и несёт остаток."""
    found = _MODULE.mismatches([_pull(14, "Часть #9 — часть работы")], {9: ("open", _REMAINDER)})

    assert found == []


def test_an_unknown_issue_is_not_a_finding() -> None:
    """Задачу спросить не удалось — молчим.

    «Не знать» и «знать плохое» — разные исходы (правило 039): выдуманное
    расхождение хуже отсутствующего.
    """
    assert _MODULE.mismatches([_pull(15, "Closes #99")], {}) == []


def test_a_change_without_any_link_is_not_this_gate_s_subject() -> None:
    """Освобождение «Без issue» проверяется до слияния, здесь предмета нет."""
    assert _MODULE.mismatches([_pull(16, "Без issue: починка своего инструмента")], {}) == []


# --- поведение скрипта -----------------------------------------------------------


def test_unreachable_api_is_the_third_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка не отработала — код 2, а не 1 и не 0.

    Находка возвращает 1, «не отработала» — 2: ночной обход и так не краснеет
    на находке, поэтому кодом 0 «предупреждение» не выражается.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        raise _MODULE.gh_rest.GitHubError("GitHub отказал (403)")

    monkeypatch.setattr(_MODULE.gh_rest, "merged_pulls", _boom)

    assert _MODULE.main([]) == 2


def test_coverage_is_named_by_a_number(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Охват называется числом: молчание значит и «чисто», и «не смотрели»."""
    monkeypatch.setattr(_MODULE.gh_rest, "merged_pulls", lambda *a, **k: [])

    assert _MODULE.main([]) == 0
    assert "изменений просмотрено — 0" in capsys.readouterr().out


def test_the_guard_never_closes_anything() -> None:
    """Сторож не закрывает задачи — он только называет расхождение.

    Автозакрытие превратило бы находку в потерю остатка: закрывает задачу
    человек или его изменение.
    """
    source = _SCRIPT.read_text(encoding="utf-8")

    for writing in ("close_issue", "update_issue", "comment_issue", "add_labels"):
        assert writing not in source, f"сторож пишет в трекер: {writing}"
