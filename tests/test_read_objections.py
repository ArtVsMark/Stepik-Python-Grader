"""Тесты читателя замечаний витрины (issue #1450).

Половина петли — публикация — сделана витриной; здесь проверяется вторая,
потребление. Предмет узкий и весь состоит из стыков с чужой стороной, поэтому
проверяется он **снятым** файлом (`tests/fixtures/glossary/objections.json`), а
не сочинённым: зелёное на подделке доказывает согласованность кода с
представлением автора о витрине, а не с витриной (правило 170).

Отдельно проверяется то, что теряется у любого потребителя, строящего работу по
списку карточек: находка **без** ``cards`` относится к глоссарию целиком и по
списку выпадает молча.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import pathlib
import sys
import urllib.error
from types import ModuleType
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "read_objections.py"
_SNAPSHOT = _ROOT / "tests" / "fixtures" / "glossary" / "objections.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_read_objections", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _payload() -> dict[str, Any]:
    """Снятый файл витрины — источник для всех проверок ниже."""
    return json.loads(_SNAPSHOT.read_text(encoding="utf-8"))


def _finding(rule: str, count: int, cards: list[str] | None = None) -> Any:
    return _MODULE.Finding(rule, "warning", "…", count, cards or [])


class _FakeResponse:
    """Ответ ``urlopen``: контекст-менеджер с телом и заголовками."""

    def __init__(self, payload: Any) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = 200
        self.headers = {}

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _opener(payload: Any) -> Any:
    def _open(request: Any) -> Any:
        return _FakeResponse(payload)

    return _open


def _contents(body: dict[str, Any] | str) -> dict[str, Any]:
    """Ответ contents-API: содержимое в base64, как у настоящего."""
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii"), "encoding": "base64"}


# --- снятый файл разбирается целиком ---------------------------------------------


def test_the_captured_snapshot_parses_into_findings() -> None:
    """Разбор идёт на настоящем файле витрины, а не на придуманном."""
    findings = _MODULE.parse_findings(_payload())

    assert [f.rule for f in findings] == [
        "translated",
        "example-compiles",
        "summary-length",
        "section-size",
    ]
    assert all(f.count > 0 for f in findings)


def test_every_card_the_showcase_names_exists_here() -> None:
    """``cards[]`` витрины — наши ``GlossaryCard.id``, и это сверено на живом снимке.

    Не сойдись они — работа строилась бы по идентификаторам, которых нет: у
    находки не оказалось бы адресата, а выглядела бы она полной.
    """
    findings = _MODULE.parse_findings(_payload())

    assert _MODULE.unknown_cards(findings, _MODULE.card_ids()) == {}


def test_the_snapshot_counts_the_same_cards_we_have() -> None:
    """Снимок витрины и наша база сходятся числом карточек.

    Расхождение означало бы, что выгрузка отстала, и все счётчики ниже считаны
    с другого содержимого.
    """
    assert _payload()["snapshot"]["cards"] == len(_MODULE.card_ids())


def test_baseline_matches_the_captured_snapshot() -> None:
    """Объявленный долг сверен со снятым файлом, а не выдуман.

    Храповик, чьи пороги никогда не сходились с источником, ничего не держит:
    он либо всегда красный, либо всегда зелёный.
    """
    findings = _MODULE.parse_findings(_payload())

    assert {f.rule: f.count for f in findings} == _MODULE.BASELINE


# --- находка уровня глоссария не теряется ----------------------------------------


def test_a_finding_without_cards_is_kept_as_glossary_level() -> None:
    """``section-size`` относится к разделу, а не к карточке, и ``cards`` у неё пуст."""
    whole = _MODULE.glossary_level(_MODULE.parse_findings(_payload()))

    assert [f.rule for f in whole] == ["section-size"]


def test_a_finding_with_cards_is_not_glossary_level() -> None:
    """Обратная сторона: иначе «уровень глоссария» означал бы просто «находка»."""
    assert _MODULE.glossary_level([_finding("translated", 3, ["all", "any"])]) == []


def test_the_report_names_the_glossary_level_finding_separately(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Она печатается своим разделом — по списку карточек её не увидеть."""
    assert _MODULE.main(["--file", str(_SNAPSHOT)]) == 0

    out = capsys.readouterr().out
    # Именно отдельный раздел, а не строка в общем списке: там она стоит рядом
    # с остальными и по `cards` всё равно выпадает.
    assert "Находки уровня глоссария" in out
    assert out.split("Находки уровня глоссария")[1].count("section-size") == 1


# --- незнакомая карточка называется, а не отбрасывается ---------------------------


def test_an_unknown_card_id_is_named() -> None:
    """Идентификатор, которого у нас нет, — расхождение контрактов, а не мусор."""
    findings = [_finding("translated", 2, ["all", "нет-такой-карточки"])]

    assert _MODULE.unknown_cards(findings, {"all"}) == {"translated": ["нет-такой-карточки"]}


def test_an_unknown_card_id_makes_the_run_a_finding(tmp_path: pathlib.Path) -> None:
    """И это код 1: молча отброшенное расхождение вернётся следующей выгрузкой."""
    snapshot = tmp_path / "objections.json"
    snapshot.write_text(
        json.dumps(
            {
                "findings": [
                    {"rule": "translated", "severity": "warning", "count": 1, "cards": ["призрак"]}
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _MODULE.main(["--file", str(snapshot)]) == 1


# --- храповик: находкой считается рост, а не наличие ------------------------------


def test_growth_over_the_declared_debt_is_a_finding() -> None:
    """746 замечаний каждую ночь краснеть не должны — а 747 должны."""
    assert _MODULE.over_budget([_finding("translated", 619)], {"translated": 618}) == [
        ("translated", 619, 618)
    ]
    assert _MODULE.over_budget([_finding("translated", 618)], {"translated": 618}) == []


def test_a_rule_absent_from_the_baseline_is_red_at_once() -> None:
    """Новое правило витрины — событие, и порог у него нулевой по умолчанию.

    Иначе незнакомая строка проезжала бы молча ровно до тех пор, пока её не
    заметит человек, читающий отчёт целиком.
    """
    assert _MODULE.over_budget([_finding("невиданное", 1)], {"translated": 618}) == [
        ("невиданное", 1, 0)
    ]


def test_a_fallen_count_asks_to_lower_the_threshold() -> None:
    """Обратная половина: объявление, пережившее починку, разрешает откат."""
    assert _MODULE.under_budget([_finding("translated", 600)], {"translated": 618}) == [
        ("translated", 600, 618)
    ]


def test_the_report_prints_the_lowered_threshold(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    """И говорит об этом вслух: молча упавшее число храповик не подтянет."""
    snapshot = tmp_path / "objections.json"
    snapshot.write_text(
        json.dumps(
            {"findings": [{"rule": "translated", "severity": "warning", "count": 1, "cards": []}]}
        ),
        encoding="utf-8",
    )

    assert _MODULE.main(["--file", str(snapshot)]) == 0
    assert "пора опустить" in capsys.readouterr().out


# --- чтение из витрины -----------------------------------------------------------


def test_contents_api_content_is_decoded() -> None:
    """Файл приезжает в base64 — читатель его разбирает, а не отдаёт как есть."""
    payload = _MODULE.fetch_objections(
        opener=_opener(_contents({"findings": []})), token="x", use_cache=False
    )

    assert payload == {"findings": []}


def test_an_empty_content_is_not_read_as_no_objections() -> None:
    """Пустое ``content`` — отказ, а не чистота.

    Файл больше мегабайта contents-API отдаёт со ссылкой вместо содержимого;
    вернуть на это «замечаний нет» значило бы объявить чистым непрочитанное.
    """
    with pytest.raises(ValueError, match="нет содержимого"):
        _MODULE.fetch_objections(
            opener=_opener({"content": "", "size": 2_000_000}), token="x", use_cache=False
        )


def test_a_missing_findings_key_is_a_schema_drift() -> None:
    """Пустой список и отсутствующий ключ означают разное."""
    assert _MODULE.parse_findings({"findings": []}) == []
    with pytest.raises(ValueError, match="findings"):
        _MODULE.parse_findings({"totals": {}})


# --- третий исход: «прочитать не удалось» ----------------------------------------


def test_an_unreadable_file_is_the_third_outcome(tmp_path: pathlib.Path) -> None:
    """Код 2, а не 0: «спросить не удалось» и «не прибавилось» — разные состояния."""
    assert _MODULE.main(["--file", str(tmp_path / "нет-такого.json")]) == 2


def test_broken_json_is_the_third_outcome_too(tmp_path: pathlib.Path) -> None:
    """Ответ пришёл, но разобрать нечего — это тоже «не отработала»."""
    snapshot = tmp_path / "objections.json"
    snapshot.write_text("{не json", encoding="utf-8")

    assert _MODULE.main(["--file", str(snapshot)]) == 2


def test_an_exhausted_quota_says_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исчерпанная квота — «подожди», а не «сломалось»: код EXIT_WAIT."""

    def _rate_limited(**kwargs: Any) -> dict[str, Any]:
        raise _MODULE.gh_rest.RateLimited("сброс в 12:00")

    monkeypatch.setattr(_MODULE, "fetch_objections", _rate_limited)

    assert _MODULE.main([]) == _MODULE.gh_rest.EXIT_WAIT


def test_a_refusal_from_the_platform_is_the_third_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ площадки (нет файла, нет прав) — код 2, а не трассировка."""

    def _refused(**kwargs: Any) -> dict[str, Any]:
        raise _MODULE.gh_rest.GitHubError("GitHub отказал (404)")

    monkeypatch.setattr(_MODULE, "fetch_objections", _refused)

    assert _MODULE.main([]) == 2


def test_a_network_failure_does_not_escape_as_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сеть не ответила — читатель говорит об этом, а не падает.

    Критерий приёмки issue #1450 дословно: недоступность файла не роняет
    прогон, а сообщает о себе.
    """
    error = urllib.error.HTTPError(
        "https://api.github.com/x", 404, "Not Found", {}, io.BytesIO(b"{}")
    )

    def _boom(request: Any) -> Any:
        raise error

    monkeypatch.setattr(_MODULE.gh_rest, "resolve_token", lambda **kwargs: "x")
    monkeypatch.setattr(_MODULE.gh_rest, "_default_opener", _boom)

    assert _MODULE.main([]) == 2


# --- машинный вывод --------------------------------------------------------------


def test_machine_output_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` разбирается: разбор годится соседнему инструменту, а не только глазу."""
    assert _MODULE.main(["--file", str(_SNAPSHOT), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out.split("Замечания витрины")[0])

    assert {"findings", "unknown_cards", "glossary_level"} <= set(payload)
    assert payload["glossary_level"] == ["section-size"]
    assert all("budget" in entry for entry in payload["findings"])


def test_the_report_names_its_coverage_with_a_number(capsys: pytest.CaptureFixture[str]) -> None:
    """Правило 165: охват называется числом — молчание значит и «чисто», и «не смотрели»."""
    _MODULE.main(["--file", str(_SNAPSHOT)])

    out = capsys.readouterr().out
    assert "правил — 4" in out
    assert "находок — 739" in out
