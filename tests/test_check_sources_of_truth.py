"""Тесты сторожа источников истины (issue #1438).

Сторож проверяется тем, что обязан **отвергнуть** (правило 140), и отдельно —
тем, что отвергать не должен: гейт, краснеющий на верном ответе, снимают первой
же правкой.

Живой случай, ради которого он заведён, воспроизводится дословно: контракт
называл источником состояния и трекер, и реестр в документе аудита, причём оба
абзаца объявляли себя исключительными.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_sources_of_truth.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_sources_of_truth", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


# --- состояние репозитория -------------------------------------------------------


def test_the_contract_is_consistent() -> None:
    """Приёмка: в контракте у каждого предмета один адрес."""
    assert _MODULE.main([]) == 0


def test_coverage_is_named_by_a_number(capsys: pytest.CaptureFixture[str]) -> None:
    """Правило 165: охват называется числом.

    «Сходится» без числа означает и «всё согласовано», и «ничего не смотрели».
    """
    _MODULE.main([])

    out = capsys.readouterr().out
    assert "заявлений об исключительности" in out
    assert any(character.isdigit() for character in out)


# --- разбор ----------------------------------------------------------------------


def test_a_paragraph_is_the_unit_not_a_line() -> None:
    """Утверждение переносится — построчный разбор рвал бы его пополам."""
    text = "Состояние живёт\nтолько в самом issue: реестр заполняется при архивации.\n"

    found = _MODULE.exclusivity_paragraphs(text)

    assert len(found) == 1
    assert "только в самом issue" in found[0]


def test_a_paragraph_without_a_marker_is_not_a_claim() -> None:
    """Обычная проза предметом не является — иначе объявлять пришлось бы всё."""
    assert _MODULE.exclusivity_paragraphs("Обычный абзац про порядок работ.\n") == []


# --- что сторож обязан отвергнуть ------------------------------------------------


def test_the_original_contradiction_is_rejected() -> None:
    """Тот самый дефект: один предмет назван двумя адресами.

    Воспроизводится дословно — контракт говорил, что состояние читают только из
    задачи, и он же говорил, что только из реестра документа.
    """
    subjects = [
        {"subject": "состояние находки", "address": "GitHub issue", "claims": ["только в самом"]},
        {"subject": "состояние находки", "address": "реестр аудита", "claims": ["только оттуда"]},
    ]

    clash = _MODULE.collisions(subjects)

    assert len(clash) == 1
    assert "GitHub issue" in clash[0] and "реестр аудита" in clash[0]


def test_a_new_undeclared_claim_is_a_finding() -> None:
    """Новое заявление об исключительности обязано быть объявлено.

    Без этой половины объявление было бы денилистом: молчит про то, чего в нём
    нет, — а именно новое заявление и вносит противоречие.
    """
    paragraphs = ["Порядок работ — единственный источник правды в этом файле."]
    subjects = [{"subject": "прочее", "address": "где-то", "claims": ["ничего общего"]}]

    assert _MODULE.undeclared(paragraphs, subjects) == paragraphs


def test_a_declaration_that_outlived_the_text_is_a_finding() -> None:
    """Обратная половина: фразу из контракта убрали, объявление осталось.

    Иначе список только растёт, и следующее заявление проезжает под чужим, уже
    недействительным предметом.
    """
    subjects = [{"subject": "предмет", "address": "адрес", "claims": ["фразы больше нет"]}]

    assert _MODULE.unused_claims(["совсем другой текст"], subjects) == ["фразы больше нет"]


# --- что сторож отвергать не должен ----------------------------------------------


def test_one_subject_with_several_claims_is_silent() -> None:
    """Один предмет можно объявлять несколькими фразами — это не расхождение.

    Контракт повторяет важное в нескольких разделах намеренно; расхождением
    является разный АДРЕС, а не повтор.
    """
    subjects = [
        {
            "subject": "состояние находки",
            "address": "GitHub issue",
            "claims": ["только в самом issue", "единственный источник статусов"],
        }
    ]

    assert _MODULE.collisions(subjects) == []


def test_different_subjects_may_have_different_addresses() -> None:
    """Разные предметы живут в разных местах — это норма, а не конфликт."""
    subjects = [
        {"subject": "состояние находки", "address": "GitHub issue", "claims": ["a"]},
        {"subject": "числа покрытия", "address": "бейджи README", "claims": ["b"]},
    ]

    assert _MODULE.collisions(subjects) == []


# --- поведение скрипта -----------------------------------------------------------


def test_a_broken_declaration_is_the_third_outcome(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Объявление нечитаемо — код 2, а не 1.

    «Не знать» и «знать плохое» — разные исходы (правило 039): отсутствующий
    файл не означает, что контракт противоречив.
    """
    broken = tmp_path / "sources.json"
    broken.write_text("{не json", encoding="utf-8")

    assert _MODULE.main(["--declaration", str(broken)]) == 2
    assert "не отработала" in capsys.readouterr().out


def test_an_empty_declaration_is_the_third_outcome(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустое объявление совпало бы с любым контрактом — это не «сходится»."""
    empty = tmp_path / "sources.json"
    empty.write_text(json.dumps({"subjects": []}), encoding="utf-8")

    assert _MODULE.main(["--declaration", str(empty)]) == 2
    assert "ни одного предмета" in capsys.readouterr().out


def test_a_contradiction_in_a_real_run_returns_one(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ветка отказа прогоняется целиком, а не только объявлена.

    Прогон одного пути подтверждает, что механизм запускается, и ничего больше:
    ветка, которую никто не видел работающей, обычно и оказывается сломанной.
    """
    contract = tmp_path / "CONTRACT.md"
    contract.write_text(
        "Состояние живёт только в самом issue.\n\nСостояние читают только оттуда.\n",
        encoding="utf-8",
    )
    declaration = tmp_path / "sources.json"
    declaration.write_text(
        json.dumps(
            {
                "subjects": [
                    {
                        "subject": "состояние",
                        "address": "issue",
                        "claims": ["только в самом issue"],
                    },
                    {"subject": "состояние", "address": "реестр", "claims": ["только оттуда"]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = _MODULE.main(["--contract", str(contract), "--declaration", str(declaration)])

    assert code == 1
    assert "разными адресами" in capsys.readouterr().out
