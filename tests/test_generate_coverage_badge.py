"""Tests for scripts/generate_coverage_badge.py — live coverage badge (issue).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем
же приёмом, что и test_check_version_consistency.py / test_version_script.py.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "generate_coverage_badge.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_coverage_badge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()

_COBERTURA_TEMPLATE = """<?xml version="1.0" ?>
<coverage line-rate="{line_rate}" branch-rate="0.0" version="7.0">
  <packages></packages>
</coverage>
"""


def _write_coverage_xml(tmp_path: pathlib.Path, line_rate: str) -> pathlib.Path:
    path = tmp_path / "coverage.xml"
    path.write_text(_COBERTURA_TEMPLATE.format(line_rate=line_rate), encoding="utf-8")
    return path


def test_compute_coverage_percent_reads_line_rate(tmp_path: pathlib.Path) -> None:
    path = _write_coverage_xml(tmp_path, "0.953")
    assert _MODULE.compute_coverage_percent(path) == 95.3


def test_compute_coverage_percent_rounds_to_one_decimal(tmp_path: pathlib.Path) -> None:
    path = _write_coverage_xml(tmp_path, "0.85678")
    assert _MODULE.compute_coverage_percent(path) == 85.7


def test_compute_coverage_percent_missing_file_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        _MODULE.compute_coverage_percent(tmp_path / "nope.xml")


def test_compute_coverage_percent_missing_line_rate_raises(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "coverage.xml"
    path.write_text('<?xml version="1.0" ?><coverage></coverage>', encoding="utf-8")
    with pytest.raises(ValueError, match="line-rate"):
        _MODULE.compute_coverage_percent(path)


@pytest.mark.parametrize(
    ("percent", "expected_color"),
    [
        (100.0, "brightgreen"),
        (90.0, "brightgreen"),
        (89.9, "green"),
        (80.0, "green"),
        (79.9, "yellowgreen"),
        (70.0, "yellowgreen"),
        (69.9, "yellow"),
        (60.0, "yellow"),
        (59.9, "orange"),
        (50.0, "orange"),
        (49.9, "red"),
        (0.0, "red"),
    ],
)
def test_badge_color_thresholds(percent: float, expected_color: str) -> None:
    assert _MODULE.badge_color(percent) == expected_color


def test_build_badge_payload_shape() -> None:
    payload = _MODULE.build_badge_payload(95.3)
    assert payload == {
        "schemaVersion": 1,
        "label": "coverage",
        "message": "95.3%",
        "color": "brightgreen",
    }


def test_build_badge_payload_formats_whole_percent_without_trailing_zero() -> None:
    payload = _MODULE.build_badge_payload(95.0)
    assert payload["message"] == "95%"


def test_build_badge_payload_accepts_custom_label() -> None:
    """issue #289: two badges (single-OS/combined) must render different
    labels — shields.io draws `label` on the image itself, not just in
    markdown alt-text, so a shared default would make them indistinguishable."""
    payload = _MODULE.build_badge_payload(95.3, label="coverage (all OS)")
    assert payload["label"] == "coverage (all OS)"


def test_main_writes_badge_json(tmp_path: pathlib.Path) -> None:
    coverage_xml = _write_coverage_xml(tmp_path, "0.953")
    out_path = tmp_path / "badges" / "coverage.json"

    _MODULE.main(["--coverage-xml", str(coverage_xml), "--out", str(out_path)])

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["message"] == "95.3%"
    assert payload["color"] == "brightgreen"
    assert payload["schemaVersion"] == 1


def test_main_creates_parent_directories(tmp_path: pathlib.Path) -> None:
    coverage_xml = _write_coverage_xml(tmp_path, "0.5")
    out_path = tmp_path / "deep" / "nested" / "coverage.json"

    _MODULE.main(["--coverage-xml", str(coverage_xml), "--out", str(out_path)])

    assert out_path.exists()


def test_main_passes_through_custom_label(tmp_path: pathlib.Path) -> None:
    coverage_xml = _write_coverage_xml(tmp_path, "0.953")
    out_path = tmp_path / "coverage-combined.json"

    _MODULE.main(
        [
            "--coverage-xml",
            str(coverage_xml),
            "--out",
            str(out_path),
            "--label",
            "coverage (all OS)",
        ]
    )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["label"] == "coverage (all OS)"
