"""Разборщики ответов площадки гоняются на СНЯТЫХ ответах (issue #1422).

Правило 170 каталога: зелёное на подделке — тоже гипотеза. Подделка это
**модель** чужой стороны, и тест на ней проверяет согласованность кода с
моделью; ошибка в самой модели изнутри набора невидима по построению, потому что
и код, и тест исходят из одного неверного представления.

Здесь тот же код прогоняется на ответах, снятых у настоящей площадки
(``scripts/capture_github_fixtures.py``). Это не заменяет ни подделки — они
быстрее и покрывают неудобные ветки, — ни живой прогон механизма (правило 139):
три разные проверки, и ни одна не отменяет двух других.

Инцидент, из которого выросло правило: у соседа подделка отдавала при отсутствии
задачи **пустую строку** — значение, которого площадка не отдаёт никогда, — и
четыре теста две недели были зелёными на неработающем механизме.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures" / "github"


def _load(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_GH = _load("_gh_rest_for_fixtures", "scripts/gh_rest.py")
_CAPTURE = _load("_capture_github_fixtures", "scripts/capture_github_fixtures.py")


def _response(name: str) -> Any:
    """Сам ответ площадки из образца — без блока происхождения."""
    payload = json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return payload["response"]


def _opener_for(name: str) -> Any:
    """Открыватель, отдающий снятый ответ вместо похода в сеть."""
    import email.message

    class _Reply:
        def __init__(self, payload: Any) -> None:
            self._raw = json.dumps(payload).encode("utf-8")
            self.status = 200
            self.headers = email.message.Message()
            self.headers["X-RateLimit-Remaining"] = "5000"
            self.headers["X-RateLimit-Limit"] = "5000"
            self.headers["X-RateLimit-Reset"] = "9999999999"

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _Reply:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    payload = _response(name)

    def _open(_request: Any) -> Any:
        return _Reply(payload)

    return _open


# --- сам источник ----------------------------------------------------------------


def test_every_declared_fixture_exists_and_is_stamped() -> None:
    """У каждой объявленной подделки есть снятый источник с происхождением.

    Образец без блока «откуда и когда» неотличим от сочинённого — то есть от
    того, против чего правило и заведено.
    """
    assert _CAPTURE.stale() == []


def test_the_stamp_names_the_endpoint_it_came_from() -> None:
    """Происхождение называет предмет: адрес запроса и дату съёмки."""
    for name in sorted(_CAPTURE.FIXTURES):
        payload = json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        origin = payload[_CAPTURE.CAPTURED_KEY]

        assert origin["endpoint"], name
        assert origin["on"], name


def test_fixtures_carry_no_credentials() -> None:
    """В образцах нет ничего похожего на токен.

    Это чтения публичных данных, и токен в них не отражается, — но новая точка
    дампа обязана проверяться, а не подразумеваться (находка ``OPS-1-02``).
    """
    import re

    suspicious = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9]{20,}")
    for path in sorted(_FIXTURES.glob("*.json")):
        assert not suspicious.search(path.read_text(encoding="utf-8")), path.name


# --- наш разбор на настоящем ответе ---------------------------------------------


def test_list_pulls_parses_a_real_list() -> None:
    """``list_pulls`` разбирает настоящий список закрытых изменений.

    Проверяются именно те поля, ради которых сокращённая форма и заведена:
    номер, ветка, автор, голова, метки, тело. Пустой список означал бы, что
    разбор молча ничего не нашёл.
    """
    pulls = _GH.list_pulls(
        _GH.DEFAULT_REPO, state="closed", opener=_opener_for("pulls_closed"), use_cache=False
    )

    assert pulls, "разбор настоящего ответа не дал ни одного изменения"
    first = pulls[0]
    assert first.number > 0
    assert first.branch and first.base
    assert first.author
    assert len(first.sha) == 40, first.sha


def test_pull_keeps_the_fields_the_pipeline_reads() -> None:
    """Одно изменение несёт поля, по которым конвейер принимает решения."""
    data = _GH.pull(_GH.DEFAULT_REPO, 1415, opener=_opener_for("pull"), use_cache=False)

    for field in ("number", "state", "draft", "merged", "merged_at", "labels", "body"):
        assert field in data, field
    assert isinstance(data["labels"], list)


def test_the_closed_list_really_carries_the_merge_time() -> None:
    """Время слияния есть только в ПОЛНОМ ответе списка, не в ``PullSummary``.

    Утверждение проверяется на снятом ответе, а не на догадке: именно из-за
    отсутствия ``merged_at`` в сокращённой форме судьба задач после слияния
    спрашивается отдельной функцией, а не тем же списком.
    """
    items = _response("pulls_closed")

    assert isinstance(items, list) and items
    assert any(item.get("merged_at") for item in items), (
        "среди снятых закрытых изменений нет ни одного слитого — образец не о том"
    )
    assert "merged_at" not in _GH.PullSummary.__dataclass_fields__


def test_issue_state_and_body_are_where_the_gate_looks() -> None:
    """Задача отдаёт состояние и тело — по ним сверяется судьба после слияния."""
    data = _GH.issue(_GH.DEFAULT_REPO, 982, opener=_opener_for("issue"), use_cache=False)

    assert data["state"] in {"open", "closed"}
    assert isinstance(data.get("body"), str)


def test_rate_limit_shape_matches_what_the_stop_valve_reads() -> None:
    """Остаток квоты лежит там, где его ищет стоп-кран."""
    data = _GH.request("GET", "rate_limit", opener=_opener_for("rate_limit"), use_cache=False).data

    resources = data["resources"]
    for bucket in ("core", "graphql", "search"):
        assert {"limit", "remaining", "reset"} <= set(resources[bucket]), bucket


# --- граница правила -------------------------------------------------------------


def test_stepik_fakes_are_declared_unverified() -> None:
    """Чего снять нечем — помечено несверенным, а не выдано за сверенное.

    Живой ответ Stepik API из облачного окна снять невозможно: нет ни
    ``secrets.json``, ни сети до Stepik. Честный ответ — назвать подделку
    несверенной; притворяться, что источник есть, хуже отсутствия источника.
    """
    text = (_ROOT / "scripts" / "capture_github_fixtures.py").read_text(encoding="utf-8")

    assert "Stepik" in text
    assert "несверен" in text


@pytest.mark.parametrize("name", sorted(_CAPTURE.FIXTURES))
def test_a_fixture_is_json_and_not_empty(name: str) -> None:
    """Guard-the-guard: образец разбирается и что-то содержит."""
    payload = _response(name)

    assert payload not in (None, {}, [], "")


# --- три исхода прогоняются, а не только объявляются -----------------------------


def test_a_missing_source_is_a_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Образца нет — код 1: подделка не сверена ни с чем.

    Прогон одного пути подтверждает, что механизм запускается, и ничего больше:
    ветка, которую никто не видел работающей, обычно и оказывается сломанной.
    """
    monkeypatch.setattr(_CAPTURE, "FIXTURE_DIR", tmp_path)

    assert _CAPTURE.main(["--check"]) == 1
    assert "не сверена ни с чем" in capsys.readouterr().out


def test_a_stale_source_asks_to_be_retaken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Просроченный образец переснимают, а не правят по памяти."""
    monkeypatch.setattr(_CAPTURE, "FIXTURE_DIR", tmp_path)
    (tmp_path / "pull.json").write_text(
        json.dumps({_CAPTURE.CAPTURED_KEY: {"endpoint": "x", "on": "2020-01-01"}}),
        encoding="utf-8",
    )

    problems = _CAPTURE.stale(today="2026-09-03")

    assert any("переснять" in problem for problem in problems), problems


def test_a_source_without_a_stamp_is_indistinguishable_from_invented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Образец без происхождения — то же, что сочинённый."""
    monkeypatch.setattr(_CAPTURE, "FIXTURE_DIR", tmp_path)
    (tmp_path / "pull.json").write_text(json.dumps({"response": {}}), encoding="utf-8")

    problems = _CAPTURE.stale(today="2026-09-03")

    assert any("происхождения" in problem for problem in problems), problems


def test_capture_that_cannot_reach_the_platform_is_the_third_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Снять не удалось — код 2, а не 1 и не 0.

    «Не знать» и «знать плохое» — разные исходы (правило 039): пустой образец,
    записанный вместо отказа, был бы подделкой, выданной за снятую.
    """
    monkeypatch.setattr(_CAPTURE, "FIXTURE_DIR", tmp_path)

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise _CAPTURE.gh_rest.GitHubError("GitHub отказал (403)")

    monkeypatch.setattr(_CAPTURE.gh_rest, "request", _refuse)

    assert _CAPTURE.main([]) == 2
    assert "снять не удалось" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.json")), "отказ не должен оставлять пустых образцов"
