"""HTTP-тесты эндпоинтов /api/rules, /api/rules/{code}, /api/insights (issue #348).

Backend-часть web-разделов «Правила»/«Подучить» (эпик #342): адаптеры
`web/rules_adapter.py`/`web/insights_adapter.py` поверх готового core-слоя
(#345/#347). Паттерн server-fixture — как в `tests/test_web_journeys.py`.
"""

from __future__ import annotations

import json
import pathlib
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from stepik_grader import web
from stepik_grader.core import history
from stepik_grader.core.history import CaseRecord, LintRecord


@pytest.fixture
def server(tmp_path: pathlib.Path):
    httpd = web._GraderServer(("127.0.0.1", 0), web._Handler, workspace=tmp_path, confine=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read()


# --- /api/rules ------------------------------------------------------------


def test_rules_list_returns_bundled_cards(server: str) -> None:
    status, body = _get(server + "/api/rules")
    assert status == 200
    cards = json.loads(body)
    assert len(cards) >= 30
    assert any(c["id"] == "E501" for c in cards)


def test_rules_search_by_query(server: str) -> None:
    # matches ищет по id/title/tags; title F401 — «Импорт не используется»
    status, body = _get(server + "/api/rules?" + urllib.parse.urlencode({"q": "используется"}))
    assert status == 200
    assert any(c["id"] == "F401" for c in json.loads(body))


def test_rules_filter_by_tag(server: str) -> None:
    status, body = _get(server + "/api/rules?" + urllib.parse.urlencode({"tag": "imports"}))
    assert status == 200
    cards = json.loads(body)
    assert cards
    assert all("imports" in {t.lower() for t in c["tags"]} for c in cards)


def test_rule_get_by_code(server: str) -> None:
    status, body = _get(server + "/api/rules/E501")
    assert status == 200
    card = json.loads(body)
    assert card["id"] == "E501"
    assert card["pep_url"]


def test_rule_get_unknown_is_404(server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server + "/api/rules/NOPE999")
    assert exc.value.code == 404
    assert "NOPE999" in exc.value.read().decode("utf-8")


# --- /api/insights ---------------------------------------------------------


def test_insights_empty_history(server: str, tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # нет .grader_history.db → []
    status, body = _get(server + "/api/insights")
    assert status == 200
    assert json.loads(body) == []


def test_insights_active_card_from_history(
    server: str, tmp_path: pathlib.Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    db = tmp_path / history.HISTORY_DB_NAME
    for _ in range(3):  # 3 падения → active
        history.record_run(1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db)
    status, body = _get(server + "/api/insights")
    assert status == 200
    cards = json.loads(body)
    active = [c for c in cards if c["key"] == "wrong-answer"]
    assert active and active[0]["status"] == "active"
    assert active[0]["category"] == "failure"


def test_insights_lint_and_glossary_ref(server: str, tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db = tmp_path / history.HISTORY_DB_NAME
    for _ in range(2):
        history.record_run(
            1,
            [CaseRecord(1, "RE", failure_kind="runtime-error:KeyError")],
            db_path=db,
            lint=[LintRecord("E501", 1)],
        )
    status, body = _get(server + "/api/insights")
    cards = {c["key"]: c for c in json.loads(body)}
    assert cards["runtime-error:KeyError"]["glossary_id"] == "keyerror"
    assert cards["E501"]["category"] == "lint"


def test_insights_journey_fail_to_archive(server: str, tmp_path: pathlib.Path, monkeypatch) -> None:
    """User-journey (issue #348): падения → карточка active → чистые прогоны → архив (скрыт)."""
    monkeypatch.chdir(tmp_path)
    db = tmp_path / history.HISTORY_DB_NAME
    for _ in range(3):  # 3 падения
        history.record_run(1, [CaseRecord(1, "WA", failure_kind="wrong-answer")], db_path=db)
    _, body = _get(server + "/api/insights")
    assert any(c["key"] == "wrong-answer" and c["status"] == "active" for c in json.loads(body))

    for _ in range(3):  # 3 чистых прогона → архив → исчезает из «Подучить»
        history.record_run(1, [CaseRecord(1, "OK")], db_path=db)
    _, body = _get(server + "/api/insights")
    assert not any(c["key"] == "wrong-answer" for c in json.loads(body))


# ---------------------------------------------------------------------------
# issue #822 (COMM-02) — «Подучить» объясняет тип падения, а не печатает ключ.
# Гейт держит JS в синхроне с core: новый `failure_kind` в insights.py без
# объяснения в content.js вернул бы раздел к «судит, но не учит».
# ---------------------------------------------------------------------------

_STATIC = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader" / "web" / "static"


def _failure_kind_map() -> dict[str, dict[str, str]]:
    """Разобрать литерал ``FAILURE_KIND`` из content.js без запуска JS."""
    source = (_STATIC / "content.js").read_text(encoding="utf-8")
    body = source.split("const FAILURE_KIND = {", 1)[1].split("\n};", 1)[0]
    entries: dict[str, dict[str, str]] = {}
    for raw in re.finditer(r'"?([\w-]+)"?:\s*\{([^}]*)\}', body):
        fields = dict(re.findall(r'(\w+):\s*"([^"]*)"', raw.group(2)))
        entries[raw.group(1)] = fields
    return entries


def _core_failure_kinds() -> set[str]:
    """Типы падений, которые core реально кладёт в карточки (кроме RE с классом)."""
    from stepik_grader.core import insights

    kinds = {
        insights.failure_kind("TLE"),
        insights.failure_kind("WA", output=["1 "], expected=["1"]),  # различие только в формате
        insights.failure_kind("WA", output=["2"], expected=["1"]),
        insights.failure_kind("SLOWER"),
        insights.failure_kind("RE", error="не опознаваемое исключение"),
    }
    return {k for k in kinds if k}


def test_every_failure_kind_is_explained_in_the_ui() -> None:
    """Каждый тип падения имеет человеческое название и «что делать»."""
    explained = _failure_kind_map()
    missing = _core_failure_kinds() - set(explained)
    assert not missing, f"типы падений без объяснения в content.js: {sorted(missing)}"


def test_failure_kind_labels_exist_in_both_locales() -> None:
    """Ключи объяснений есть в ru и en — иначе в UI появится сырой ключ каталога."""
    catalog = json.loads((_STATIC / "locales" / "ui.json").read_text(encoding="utf-8"))
    for kind, fields in _failure_kind_map().items():
        for field in ("title", "hint"):
            key = fields[field]
            for lang in ("ru", "en"):
                assert key in catalog[lang], f"{kind}.{field}: нет ключа {key} в {lang}"


def test_failure_kind_glossary_targets_exist() -> None:
    """Ссылка ведёт в существующую карточку: битый deep-link хуже отсутствующего."""
    from stepik_grader.glossary import json_provider

    provider = json_provider.JsonGlossaryProvider.load(json_provider.BUNDLED_GLOSSARY_DIR)
    for kind, fields in _failure_kind_map().items():
        target = fields.get("glossary")
        if target:
            assert provider.get(target) is not None, f"{kind}: нет карточки {target!r}"
