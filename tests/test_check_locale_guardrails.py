"""Tests for scripts/check_locale_guardrails.py — locale catalog guardrails
(issue #264).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_docs_guardrails.py.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_locale_guardrails.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_locale_guardrails", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main нарушений быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_locale_guardrails.py` завершается 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_collect_referenced_message_ids_finds_render_message_and_message_fields(
    tmp_path: Path,
) -> None:
    module = _load_module()
    src = tmp_path / "sample.py"
    src.write_text(
        "from stepik_grader.web.i18n import message_fields, render_message\n"
        "\n"
        "def f(lang):\n"
        "    a = message_fields('some_key', lang, path='x')\n"
        "    b = render_message('other_key', lang)\n"
        "    return a, b\n",
        encoding="utf-8",
    )
    assert module.collect_referenced_message_ids(src) == {"some_key", "other_key"}


def test_collect_referenced_message_ids_ignores_dynamic_first_arg(tmp_path: Path) -> None:
    """Не строковый литерал первым аргументом — не считается (см. docstring функции)."""
    module = _load_module()
    src = tmp_path / "sample.py"
    src.write_text(
        "def f(key, lang):\n    return render_message(key, lang)\n",
        encoding="utf-8",
    )
    assert module.collect_referenced_message_ids(src) == set()


def test_ru_missing_referenced_key_is_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "mod.py").write_text("render_message('missing_in_ru', 'ru')\n", encoding="utf-8")
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps({}), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(module, "_WEB_DIR", web_dir)
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_ru_covers_referenced_ids(errors)
    assert any("missing_in_ru" in e for e in errors), errors


def test_en_ru_key_mismatch_is_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps({"a": "1", "b": "2"}), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps({"a": "1"}), encoding="utf-8")
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_en_ru_key_parity(errors)
    assert any("b" in e for e in errors), errors


def test_matching_keys_pass(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps({"a": "1"}), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps({"a": "hi"}), encoding="utf-8")
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_en_ru_key_parity(errors)
    assert errors == []


def test_load_locale_keys_missing_file_returns_empty_set(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_LOCALES_DIR", tmp_path)
    assert module.load_locale_keys("xx") == set()
