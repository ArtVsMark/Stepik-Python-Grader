"""Tests for scripts/glossary_draft_pipeline.py — B1-конвейер черновиков (#438).

Скрипт в scripts/ (не на sys.path) — грузим по пути, как test_glossary_draft_gen.
Валидация примеров прогоном использует крошечные детерминированные сниппеты.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from stepik_grader.glossary.json_provider import JsonGlossaryProvider
from stepik_grader.glossary.stdlib_inventory import StdlibItem

_SCRIPT = Path(__file__).parent.parent / "scripts" / "glossary_draft_pipeline.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_glossary_draft_pipeline", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Регистрируем в sys.modules до exec: @dataclass с `from __future__ import
    # annotations` через _is_type читает sys.modules[cls.__module__].__dict__.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_script()

_RJUST = StdlibItem("str.rjust", "builtins", "method", "3.13")


# -- split_code_and_expected / extract_expected --------------------------------


def test_split_inline_arrow() -> None:
    code, want = mod.split_code_and_expected("print(1)  # → 1")
    assert code == "print(1)  # → 1"  # код возвращается как есть (комментарий инертен)
    assert want == "1"


def test_split_ascii_arrow() -> None:
    _, want = mod.split_code_and_expected("print(2)  # -> 2")
    assert want == "2"


def test_split_no_marker() -> None:
    _, want = mod.split_code_and_expected("lst = []")
    assert want is None


def test_extract_expected_order() -> None:
    examples = ["print(1)  # → 1", "x = 2", "print(x)", "# → 2"]
    assert mod.extract_expected(examples) == ["1", "2"]


# -- compare_expected_actual ---------------------------------------------------


def test_compare_exact_and_whitespace() -> None:
    assert mod.compare_expected_actual("42", "42")
    assert mod.compare_expected_actual("  42 ", "42")


def test_compare_approximation() -> None:
    assert mod.compare_expected_actual("3.14159...", "3.141592653589793")
    assert not mod.compare_expected_actual("3.15...", "3.141592653589793")


def test_compare_trailing_note() -> None:
    assert mod.compare_expected_actual("None (всегда None)", "None")
    assert mod.compare_expected_actual("[1, 2] (список как элемент)", "[1, 2]")


def test_compare_negative() -> None:
    assert not mod.compare_expected_actual("5", "4")


# -- compare: запись литерала и имя исключения (#745) ---------------------------


def test_compare_literal_spacing() -> None:
    # Ожидание пишут руками, repr печатает с пробелами — значение то же.
    assert mod.compare_expected_actual("(1,2,3)", "(1, 2, 3)")
    assert mod.compare_expected_actual("['a','b']", "['a', 'b']")
    assert mod.compare_expected_actual("{'a':1}", "{'a': 1}")


def test_compare_literal_repr_of_string() -> None:
    assert mod.compare_expected_actual("'P'", "P")
    assert mod.compare_expected_actual('"cde"', "cde")


def test_compare_literal_respects_type() -> None:
    # 1 == True в Python, но как вывод это разные строки — совпадением не считаем.
    assert not mod.compare_expected_actual("1", "True")
    assert not mod.compare_expected_actual("1", "1.0")


def test_exception_name_detects() -> None:
    assert mod.exception_name("TypeError") == "TypeError"
    assert mod.exception_name("statistics.StatisticsError") == "StatisticsError"
    assert mod.exception_name("KeyboardInterrupt") == "KeyboardInterrupt"


def test_exception_name_rejects_plain_values() -> None:
    assert mod.exception_name("Hello") is None
    assert mod.exception_name("42") is None
    assert mod.exception_name("['a', 'b']") is None


def test_validate_intentional_exception_is_ok() -> None:
    # Демонстрация падения — не поломка примера.
    report = mod.validate_examples(["print(int('nope'))  # → ValueError"])
    assert report.status == "ok"


def test_validate_continues_after_intentional_exception() -> None:
    # Строки после демонстрации тоже проверяются, а не теряются на обрыве.
    report = mod.validate_examples(["print(int('nope'))  # → ValueError", "print(2 + 2)  # → 4"])
    assert report.status == "ok"
    assert report.pairs == [("ValueError", "ValueError"), ("4", "4")]


def test_validate_wrong_exception_is_mismatch() -> None:
    report = mod.validate_examples(["print(int('nope'))  # → KeyError"])
    assert report.status == "mismatch"


def test_validate_non_ascii_output() -> None:
    # На Windows кодировка консоли (cp1251) не должна ронять прогон примера.
    assert mod.validate_examples(["print('♠ туз')  # → ♠ туз"]).status == "ok"


def test_compare_note_with_nested_parens() -> None:
    # Пометка сама может содержать скобки: "True (мгновенно, O(1))".
    assert mod.compare_expected_actual("True (мгновенно, O(1))", "True")


def test_compare_note_after_dash_or_colon() -> None:
    assert mod.compare_expected_actual("False — F_OK это проверка существования", "False")
    assert mod.compare_expected_actual("True: getsize читает st_size", "True")
    assert mod.compare_expected_actual("32, по два hex-символа на байт", "32")


def test_compare_note_requires_separator() -> None:
    # Иначе «1000, а не 100» ложно совпало бы со «100».
    assert not mod.compare_expected_actual("1000, а не 100", "100")
    assert not mod.compare_expected_actual("420", "42")


def test_compare_repr_string_keeps_significant_spaces() -> None:
    # Ширина поля значима: сравнивать надо с необрезанным выводом.
    assert mod.compare_expected_actual("'        42'", "        42")


# -- validate_examples ---------------------------------------------------------


def test_validate_ok() -> None:
    report = mod.validate_examples(["print(2 + 2)  # → 4", "print('ok')  # → ok"])
    assert report.status == "ok"
    assert report.pairs == [("4", "4"), ("ok", "ok")]


def test_validate_mismatch() -> None:
    report = mod.validate_examples(["print(2 + 2)  # → 5"])
    assert report.status == "mismatch"
    assert "получили '4'" in report.detail


def test_validate_runtime_error() -> None:
    report = mod.validate_examples(["print(int('nope'))  # → 0"])
    assert report.status == "error"
    assert "ValueError" in report.detail


def test_validate_non_compilable_is_unverifiable() -> None:
    # Многострочный блок, хранимый построчно без отступов (legacy-паттерн):
    report = mod.validate_examples(["for i in range(2):", "print(i)", "# → 0"])
    assert report.status == "unverifiable"
    assert "не компилируется" in report.detail


def test_validate_no_markers_unverifiable() -> None:
    assert mod.validate_examples(["x = 1", "y = 2"]).status == "unverifiable"
    assert mod.validate_examples([]).status == "unverifiable"


def test_validate_count_mismatch_unverifiable() -> None:
    # Два print'а, одно ожидание → нельзя однозначно выровнять.
    report = mod.validate_examples(["print(1)", "print(2)", "# → 1"])
    assert report.status == "unverifiable"
    assert "≠" in report.detail


# -- build_b1_draft / OfflineDraftProvider -------------------------------------


def test_offline_provider_summary_en_from_docstring() -> None:
    content = mod.OfflineDraftProvider().propose(_RJUST)
    assert content.summary == ""  # RU — под ревью
    assert content.summary_en  # EN из docstring
    assert content.examples == []  # без модели примеры не выдумываются


def test_offline_provider_overrides_passthrough() -> None:
    override = mod.ProposedContent(summary="ру", summary_en="en", examples=["print(1)  # → 1"])
    content = mod.OfflineDraftProvider(override).propose(_RJUST)
    assert content is override


def test_build_b1_draft_fields() -> None:
    content = mod.ProposedContent(summary="выравнивание справа", summary_en="right", examples=[])
    card = mod.build_b1_draft(_RJUST, content)
    assert card.id == "str.rjust"
    assert card.status == "draft"
    assert card.summary == "выравнивание справа"
    assert card.summary_en == "right"
    assert "b1-pipeline" in card.tags
    assert "autodraft" in card.tags  # каркас из generate_draft_cards сохранён


# -- review_diff ---------------------------------------------------------------


def test_review_diff_new_card(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    (base / "seed.json").write_text("[]", encoding="utf-8")
    content = mod.ProposedContent(summary="s", summary_en="e", examples=["print(1)  # → 1"])
    card = mod.build_b1_draft(_RJUST, content)
    diff = mod.review_diff(card, base)
    assert "proposed/str.rjust" in diff
    assert "<новая>" in diff  # карточки в базе нет — diff от пустого


# -- run_propose (запись только валидных, в отдельный draft-файл) ---------------


def _base_with(tmp_path: Path) -> Path:
    base = tmp_path / "data"
    base.mkdir()
    (base / "seed.json").write_text("[]", encoding="utf-8")
    return base


def test_run_propose_writes_valid(tmp_path: Path) -> None:
    base = _base_with(tmp_path)
    content_file = tmp_path / "good.json"
    content_file.write_text(
        json.dumps({"summary": "ру", "summary_en": "en", "examples": ["print(7)  # → 7"]}),
        encoding="utf-8",
    )
    out = tmp_path / "review-drafts.json"
    rc = mod.run_propose("str.rjust", base_dir=base, content_file=content_file, out_file=out)
    assert rc == 0
    assert out.exists()
    written = JsonGlossaryProvider.from_file(out).all()
    assert [c.id for c in written] == ["str.rjust"]
    assert written[0].status == "draft"  # никогда не ready-автомержем


def test_run_propose_blocks_write_on_bad_example(tmp_path: Path) -> None:
    base = _base_with(tmp_path)
    content_file = tmp_path / "bad.json"
    content_file.write_text(
        json.dumps({"summary": "x", "examples": ["print(2 + 2)  # → 5"]}),
        encoding="utf-8",
    )
    out = tmp_path / "should-not-write.json"
    rc = mod.run_propose("str.rjust", base_dir=base, content_file=content_file, out_file=out)
    assert rc == 1
    assert not out.exists()  # битый пример → ничего не записано


def test_run_propose_unknown_qualname(tmp_path: Path) -> None:
    base = _base_with(tmp_path)
    assert mod.run_propose("nope.nope.nope", base_dir=base) == 2


# -- check: параллельный обход эквивалентен последовательному -------------------


# По одному набору примеров на каждый исход validate_examples; все детерминированы
# (без времени/порядка множеств), поэтому вывод сравним побайтово.
_CHECK_CASES = {
    "case-error": ["print(int('nope'))  # → 0"],
    "case-mismatch": ["print(2 + 2)  # → 5"],
    "case-ok": ["print(2 + 2)  # → 4"],
    "case-unverifiable": ["x = 1"],
}


def test_run_check_parallel_matches_sequential(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "data"
    base.mkdir()
    # Каждый исход дублируем — чтобы воркерам было что распараллеливать.
    cards = [
        {"id": f"{card_id}-{n}", "title": card_id, "status": "ready", "examples": examples}
        for card_id, examples in _CHECK_CASES.items()
        for n in (1, 2)
    ]
    (base / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")

    seq_flagged = mod.run_check(base, jobs=1)
    seq_out = capsys.readouterr().out
    par_flagged = mod.run_check(base, jobs=4)
    par_out = capsys.readouterr().out

    assert seq_flagged == par_flagged == 4  # mismatch×2 + error×2
    # Параллельный обход не смещает счётчики и не меняет порядок строк отчёта.
    assert par_out == seq_out


def test_run_check_skips_posix_only_outside_posix(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "data"
    base.mkdir()
    cards = [
        {
            "id": "posix-only",
            "title": "posix-only",
            "status": "ready",
            "tags": ["platform:posix"],
            "examples": ["print(2 + 2)  # → 5"],  # заведомо расходится
        },
        {"id": "portable", "title": "portable", "status": "ready", "examples": ["print(1)  # → 1"]},
    ]
    (base / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(mod.os, "name", "nt")
    assert mod.run_check(base) == 0  # POSIX-карточка не исполнялась, расхождения нет
    out = capsys.readouterr().out
    assert "Проверено карточек с примерами: 1 из 2" in out
    assert "skipped=1" in out

    monkeypatch.setattr(mod.os, "name", "posix")
    assert mod.run_check(base) == 1  # на POSIX она проверяется и расходится


# -- check over реальная база (smoke: движок отрабатывает без падений) ----------


def test_run_check_bundled_smoke(capsys) -> None:  # type: ignore[no-untyped-def]
    from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR

    # Единственное место, где движок валидации встречается со всем разнообразием
    # реальной базы (1300+ карточек, ~1100 из них реально уходят в subprocess).
    # Стоимость растёт вместе с базой, поэтому run_check обходит карточки
    # параллельно — последовательный обход упирался в общий 120-секундный
    # дедлайн pytest-timeout на Windows, где процессы дороже (issue #444).
    # Возвращает число «требующих внимания» (mismatch+error) — не падает, печатает сводку.
    flagged = mod.run_check(BUNDLED_GLOSSARY_DIR)
    out = capsys.readouterr().out
    assert "Проверено карточек с примерами:" in out
    assert isinstance(flagged, int) and flagged >= 0
