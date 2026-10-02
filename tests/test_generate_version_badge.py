"""Tests for scripts/generate_version_badge.py — live "current version" badge.

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем
же приёмом, что и test_check_version_consistency.py / test_version_script.py /
test_generate_coverage_badge.py.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import ModuleType

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "generate_version_badge.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_version_badge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def test_load_project_version_fn_matches_scripts_version_py() -> None:
    """Бейдж обязан переиспользовать scripts/version.py, а не дублировать git describe."""
    import subprocess
    import sys

    fn = _MODULE.load_project_version_fn()
    expected = subprocess.check_output(
        [sys.executable, str(pathlib.Path(__file__).parent.parent / "scripts" / "version.py")],
        text=True,
        encoding="utf-8",
    ).strip()
    assert fn() == expected


def test_build_badge_payload_shape() -> None:
    payload = _MODULE.build_badge_payload("1.5.32")
    assert payload == {
        "schemaVersion": 1,
        "label": "version",
        "message": "1.5.32",
        "color": "blue",
    }


def test_build_badge_payload_passes_through_arbitrary_version_string() -> None:
    payload = _MODULE.build_badge_payload("2.0.0")
    assert payload["message"] == "2.0.0"


def test_main_writes_badge_json(tmp_path: pathlib.Path) -> None:
    out_path = tmp_path / "badges" / "version.json"

    _MODULE.main(["--out", str(out_path)])

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["label"] == "version"
    assert payload["color"] == "blue"
    # message — реальная текущая версия репозитория, форма X.Y.Z.
    assert payload["message"].count(".") == 2


def test_main_creates_parent_directories(tmp_path: pathlib.Path) -> None:
    out_path = tmp_path / "deep" / "nested" / "version.json"

    _MODULE.main(["--out", str(out_path)])

    assert out_path.exists()


def test_main_default_out_path_argument() -> None:
    parser = _MODULE._build_arg_parser()
    args = parser.parse_args([])
    assert args.out == pathlib.Path(".github/badges/version.json")
