"""Tests for scripts/check_rule_bindings.py — ответ каталогу правил (#1351).

Каталог отдаёт правила машиночитаемо, проект отвечает, что с каждым сделал.
Ответ — это **декларация**, и проверять её надо на расхождение с фактом: путь,
названный в `where`, обязан существовать, а отрицательное решение — нести
причину, иначе через полгода оно неотличимо от «не дошли руки».

Отдельно проверяется метрика: `unreviewed` плюс `active` с `mechanism: none` —
это правила, принятые на словах. Она и есть предмет задачи, поэтому обязана
считаться честно, а не «в приятную сторону».
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_rule_bindings.py"
_BINDINGS = pathlib.Path(__file__).parent.parent / ".rules" / "bindings.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_rule_bindings", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _data(rules: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "1.1", "project": "x/y", "catalogue": "https://example", "rules": rules}


# --- состояние репозитория ----------------------------------------------------


def test_repository_answer_is_valid() -> None:
    """Приёмка #1351: проект отвечает каталогу, и ответ сходится с фактом."""
    assert _MODULE.main([]) == 0


def test_answer_file_exists_and_parses() -> None:
    data = json.loads(_BINDINGS.read_text(encoding="utf-8"))
    assert data["schema"] == "1.1"
    assert data["project"] == "ArtVsMark/Stepik-Python-Grader"
    assert data["rules"], "пустой ответ — то же самое, что отсутствие ответа"


def test_metric_counts_rules_held_by_nothing() -> None:
    """Метрика честна: она видит правило на словах, а не считает в приятную сторону.

    Раньше здесь стояло `unheld > 0` — «правила на словах у нас есть». Теперь их
    нет ни одного, и такое утверждение проверяло бы состояние мира, а не работу
    счётчика: метрика, дошедшая до нуля, роняла бы собственный тест. Поэтому
    спрашивается вердикт на заданном входе.
    """
    data = json.loads(_BINDINGS.read_text(encoding="utf-8"))
    _unheld, total = _MODULE.unheld_count(data)

    assert total > 100, "ответ нужен по каждому правилу каталога"

    on_words = _data(
        {
            "001": {"status": "active", "mechanism": "none", "where": "только память окна"},
            "002": {"status": "unreviewed"},
            "003": {"status": "active", "mechanism": "gate", "where": "scripts/x.py"},
        }
    )

    assert _MODULE.unheld_count(on_words) == (2, 3)


# --- проверка контракта -------------------------------------------------------


def test_active_without_mechanism_is_a_violation() -> None:
    """«Принято» без ответа «чем держится» и есть фикция."""
    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "where": "CLAUDE.md"}})
    )
    assert any("механизм" in problem for problem in problems), problems


def test_active_without_where_is_a_violation() -> None:
    problems = _MODULE.binding_violations(_data({"001": {"status": "active", "mechanism": "gate"}}))
    assert any("where" in problem for problem in problems), problems


def test_where_pointing_at_a_missing_file_is_a_violation(tmp_path: pathlib.Path) -> None:
    """Декларация обязана сходиться с фактом: предмет мог исчезнуть."""
    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "gate", "where": "scripts/нет-такого.py"}}),
        root=tmp_path,
    )
    assert any("нет-такого.py" in problem for problem in problems), problems


def test_a_root_document_by_name_is_an_address() -> None:
    """Корневой документ по имени — адрес, а прозы рядом контракт не запрещает."""
    problems = _MODULE.binding_violations(
        _data(
            {
                "001": {
                    "status": "active",
                    "mechanism": "document",
                    "where": "CONTRIBUTING.md § ревью документации: читает мержащий",
                }
            }
        )
    )
    assert problems == [], problems


def test_prose_instead_of_an_address_is_a_violation() -> None:
    """Проза ВМЕСТО адреса — нет: гейт, чей адрес не назвать, обычно и не гейт.

    Контракт 1.1 требует от `where` разрешимый адрес. Асимметрия стоила ровно
    того, чего от неё ждали: пока проверялась только непустота, разложить
    ответы по уровням можно было лишь разбором прозы регулярным выражением.
    """
    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "document", "where": "ревью документации"}})
    )

    assert any("разрешимого адреса" in problem for problem in problems), problems


def test_none_may_answer_in_prose() -> None:
    """У `none` адрес не требуется: механизма нет, называть нечего."""
    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "none", "where": "механизма нет"}})
    )
    assert problems == [], problems


def test_the_deprecated_word_is_no_longer_accepted() -> None:
    """`process-step` называл сразу три уровня — и потому не значил ничего.

    Каталог принимает его для совместимости, но в отчётах не сводит ни к
    одному из новых: подмена была бы догадкой за потребителя. У нас все записи
    переведены, поэтому слово отвергается — иначе склейка вернётся (#1400).
    """
    problems = _MODULE.binding_violations(
        _data(
            {"001": {"status": "active", "mechanism": "process-step", "where": "CLAUDE.md § Гейты"}}
        )
    )

    assert any("process-step" in problem or "механизма из" in problem for problem in problems), (
        problems
    )


def _repo(root: pathlib.Path, *, workflow: str = "", scripts: dict[str, str] | None = None) -> None:
    """Минимальный репозиторий: прогон CI плюс несколько скриптов."""
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
    (root / "scripts").mkdir(exist_ok=True)
    for name, text in (scripts or {}).items():
        (root / "scripts" / name).write_text(text, encoding="utf-8")


def test_every_named_path_is_checked_not_only_the_first(tmp_path: pathlib.Path) -> None:
    """Проверяется каждый путь в `where`, а не первое слово строки.

    Пока смотрели первый, у записи с двумя путями второе утверждение не
    проверял никто: правило 119 называло креплением `tests/test_test_loader.py`,
    которого нет, и гейт молчал, потому что первым стоял существующий модуль
    (issue #1400).
    """
    _repo(tmp_path, workflow="run: python scripts/check_x.py", scripts={"check_x.py": ""})

    problems = _MODULE.binding_violations(
        _data(
            {
                "119": {
                    "status": "active",
                    "mechanism": "gate",
                    "where": "scripts/check_x.py — закреплено tests/test_нет_такого.py",
                }
            }
        ),
        root=tmp_path,
    )

    assert any("test_нет_такого.py" in problem for problem in problems), problems


def test_a_gate_nobody_runs_is_a_violation(tmp_path: pathlib.Path) -> None:
    """«Держится гейтом» подтверждается падением, а не существованием файла."""
    _repo(tmp_path, workflow="run: python scripts/check_other.py", scripts={"check_x.py": ""})

    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "gate", "where": "scripts/check_x.py"}}),
        root=tmp_path,
    )

    assert any("не запускается" in problem for problem in problems), problems


def test_a_mention_is_not_an_invocation(tmp_path: pathlib.Path) -> None:
    """Скрипт, лишь НАЗВАННЫЙ в прозе, подключённым не считается.

    Обе стороны ошибки уже случались: `check_docs_guardrails.py` называет
    `skip_inventory.py` в docstring, а `check_pr_ready.py` встречается в трёх
    прогонах — и везде это `#`-комментарий. По одному `grep` оба выглядели
    работающими гейтами.
    """
    _repo(
        tmp_path,
        workflow="# запускать не будем: python scripts/check_x.py",
        scripts={
            "check_x.py": "",
            "check_runner.py": '"""Похож на scripts/check_x.py, но не зовёт его."""',
        },
    )

    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "gate", "where": "scripts/check_x.py"}}),
        root=tmp_path,
    )

    assert any("не запускается" in problem for problem in problems), problems


def test_a_gate_reached_through_another_script_counts(tmp_path: pathlib.Path) -> None:
    """Цепочка длиннее одного звена — тоже подключение, а не долг."""
    _repo(
        tmp_path,
        workflow="run: python scripts/check_runner.py",
        scripts={
            "check_runner.py": 'run([sys.executable, str(_ROOT / "scripts" / "check_x.py")])',
            "check_x.py": "",
        },
    )

    problems = _MODULE.binding_violations(
        _data({"001": {"status": "active", "mechanism": "gate", "where": "scripts/check_x.py"}}),
        root=tmp_path,
    )

    assert problems == [], problems


def test_one_reachable_script_is_enough(tmp_path: pathlib.Path) -> None:
    """Запись законно называет и генератор, и сторожа при нём — держит второй."""
    _repo(
        tmp_path,
        workflow="run: python scripts/check_guard.py",
        scripts={"check_guard.py": "", "generate_thing.py": ""},
    )

    problems = _MODULE.binding_violations(
        _data(
            {
                "120": {
                    "status": "active",
                    "mechanism": "gate",
                    "where": "scripts/generate_thing.py, а scripts/check_guard.py их сверяет",
                }
            }
        ),
        root=tmp_path,
    )

    assert problems == [], problems


def test_declared_debt_is_named_with_a_reason() -> None:
    """Долг объявляется с причиной: молча внесённое исключение — глушилка."""
    assert _MODULE.GATE_DEBT, "список долга пуст — тогда и исключений быть не должно"
    for script, reason in _MODULE.GATE_DEBT.items():
        assert reason.strip(), f"{script}: долг без причины"
        assert "#" in reason, f"{script}: причина без адреса задачи"


@pytest.mark.parametrize("status", ["rejected", "not-applicable"])
def test_negative_decision_needs_a_reason(status: str) -> None:
    """Отрицательное решение без причины через полгода не отличить от забывчивости."""
    problems = _MODULE.binding_violations(_data({"001": {"status": status}}))
    assert any("причины" in problem for problem in problems), problems


def test_unknown_status_is_reported() -> None:
    problems = _MODULE.binding_violations(_data({"001": {"status": "почти-принято"}}))
    assert any("статус" in problem for problem in problems), problems


def test_empty_rules_is_a_failure() -> None:
    """Гейт без предмета проверки обязан падать, а не зеленеть на пустоте."""
    assert _MODULE.binding_violations(_data({})) != []


def test_wrong_schema_is_reported() -> None:
    """Версия контракта — не украшение: сломать формат значит сломать читателей."""
    data = _data({"001": {"status": "unreviewed"}})
    data["schema"] = "2.0"
    assert any("schema" in problem for problem in _MODULE.binding_violations(data)), data


# --- метрика ------------------------------------------------------------------


def test_unheld_counts_unreviewed_and_none() -> None:
    """Не обеспечено ничем — это и «не смотрели», и «принято без механизма»."""
    unheld, total = _MODULE.unheld_count(
        _data(
            {
                "001": {"status": "active", "mechanism": "gate", "where": "scripts/x.py"},
                "002": {"status": "active", "mechanism": "none", "where": "CLAUDE.md"},
                "003": {"status": "unreviewed"},
                "004": {"status": "rejected", "why": "иначе решили"},
            }
        )
    )
    assert (unheld, total) == (2, 4)


class TestUnheldBudget:
    """Храповик: правило без механизма обязано быть записано документом.

    Бюджет — не «столько допустимо», а «столько осталось». Поэтому две стороны:
    гейт краснеет при превышении и — отдельным тестом — само число сверяется с
    реальностью, иначе бюджет тихо разойдётся с ответом и перестанет что-либо
    держать.
    """

    def test_budget_matches_reality(self) -> None:
        data = json.loads(_BINDINGS.read_text(encoding="utf-8"))

        unheld, _total = _MODULE.unheld_count(data)

        assert unheld <= _MODULE.UNHELD_BUDGET, (
            f"не обеспечено ничем {unheld} при бюджете {_MODULE.UNHELD_BUDGET}. "
            "Бюджет опускают починкой, а не правкой числа."
        )

    def test_live_answer_is_green(self) -> None:
        assert _MODULE.main([]) == 0

    def test_exceeding_the_budget_is_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_MODULE, "UNHELD_BUDGET", -1)

        assert _MODULE.main([]) == 1
