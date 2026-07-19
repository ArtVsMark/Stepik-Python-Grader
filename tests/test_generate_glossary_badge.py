"""Tests for scripts/generate_glossary_badge.py — live glossary ready-count badge (#398/#535).

Скрипт лежит в scripts/ (не на sys.path) — грузим по пути, тем же приёмом, что
test_generate_coverage_badge.py / test_generate_version_badge.py.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import ModuleType

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "generate_glossary_badge.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_glossary_badge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _write_cards(tmp_path: pathlib.Path, statuses: list[str]) -> pathlib.Path:
    """Записать директорию-базу из минимальных карточек с заданными статусами."""
    cards = [
        {"id": f"c{i}", "title": f"C{i}", "kind": "term", "status": status}
        for i, status in enumerate(statuses)
    ]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cards.json").write_text(json.dumps({"cards": cards}), encoding="utf-8")
    return data_dir


def test_count_ready_cards_counts_only_ready(tmp_path: pathlib.Path) -> None:
    cards_dir = _write_cards(tmp_path, ["ready", "ready", "draft", "ready"])
    assert _MODULE.count_ready_cards(cards_dir) == 3


def test_count_ready_cards_matches_bundled_source_not_hardcoded() -> None:
    """Число генератора == list_by_status('ready') комплектной базы — живой источник, не хардкод."""
    from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider

    expected = len(JsonGlossaryProvider.load(BUNDLED_GLOSSARY_DIR).list_by_status("ready"))
    assert _MODULE.count_ready_cards() == expected
    assert expected > 0  # база не пуста — бейдж осмыслен


def test_build_badge_payload_shape() -> None:
    assert _MODULE.build_badge_payload(1333) == {
        "schemaVersion": 1,
        "label": "glossary",
        "message": "1333 ready",
        "color": "blue",
    }


def test_main_writes_badge_json_from_cards(tmp_path: pathlib.Path) -> None:
    cards_dir = _write_cards(tmp_path, ["ready", "draft", "ready"])
    out_path = tmp_path / "badges" / "glossary.json"

    _MODULE.main(["--cards", str(cards_dir), "--out", str(out_path)])

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["message"] == "2 ready"
    assert payload["schemaVersion"] == 1
    assert payload["label"] == "glossary"


def test_main_creates_parent_directories(tmp_path: pathlib.Path) -> None:
    cards_dir = _write_cards(tmp_path, ["ready"])
    out_path = tmp_path / "deep" / "nested" / "glossary.json"
    _MODULE.main(["--cards", str(cards_dir), "--out", str(out_path)])
    assert out_path.exists()
