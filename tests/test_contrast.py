"""Tests for scripts/check_contrast.py — WCAG-контраст токен-пар (issue #424).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_locale_guardrails.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_contrast.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_contrast", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_css_passes() -> None:
    """На актуальном app.css все пары проходят порог — check() пуст, main()==0."""
    module = _load_module()
    assert module.check() == []
    assert module.main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_contrast.py` завершается кодом 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_contrast_ratio_known_values() -> None:
    """Белый/чёрный = 21:1, одинаковые цвета = 1:1 (референс WCAG)."""
    module = _load_module()
    assert round(module.contrast_ratio("#ffffff", "#000000"), 1) == 21.0
    assert round(module.contrast_ratio("#777777", "#777777"), 2) == 1.0
    # 3-значный hex раскрывается как 6-значный.
    assert module.contrast_ratio("#fff", "#000") == module.contrast_ratio("#ffffff", "#000000")


def test_catches_low_contrast_regression(tmp_path: Path) -> None:
    """Заведомо провальная пара (muted почти сливается с фоном) ловится check()."""
    module = _load_module()
    bad = tmp_path / "app.css"
    # Минимальный CSS с обоими блоками токенов; тёмный muted = почти фон.
    light = "\n".join(
        f"  {name}: {value};" for name, value in _min_tokens(module, dark=False).items()
    )
    dark_tokens = _min_tokens(module, dark=True)
    dark_tokens["--color-text-muted"] = "#1b1b1a"  # ~фон → провал
    dark = "\n".join(f"  {name}: {value};" for name, value in dark_tokens.items())
    bad.write_text(
        f':root, [data-theme="light"] {{\n{light}\n}}\n[data-theme="dark"] {{\n{dark}\n}}\n',
        encoding="utf-8",
    )
    failures = module.check(bad)
    assert any("text-muted" in f and "dark" in f for f in failures), failures


def test_load_tokens_missing_block(tmp_path: Path) -> None:
    """Отсутствие блока токенов — явная ошибка, а не тихий пропуск."""
    module = _load_module()
    bad = tmp_path / "app.css"
    bad.write_text("body { color: red; }\n", encoding="utf-8")
    with pytest.raises(ValueError):
        module.load_tokens(bad)


def _min_tokens(module: ModuleType, *, dark: bool) -> dict[str, str]:
    """Собрать все токены, упоминаемые в парах чекера, с валидными значениями."""
    names: set[str] = set()
    for pair in module._PAIRS:
        for ref in (pair.fg, pair.bg):
            if not ref.startswith("#"):
                names.add(ref)
    # Контрастные дефолты, чтобы «здоровый» блок проходил без ручной подгонки.
    base = "#e8e8e8" if dark else "#202020"
    bg = "#161614" if dark else "#f7f6f2"
    out = {name: base for name in names}
    bg_markers = ("highlight", "bg", "surface", "btn")
    for name in names:
        if any(m in name for m in bg_markers) or name.endswith(("-hover", "-active")):
            out[name] = bg
    return out
