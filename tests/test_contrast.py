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
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, encoding="utf-8"
    )
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


# ---------------------------------------------------------------------------
# issue #805 (DESW-01) — полнота самого списка пар. Провал WCAG держался
# зелёным не из-за неверного порога, а из-за отсутствующей строки в `_PAIRS`:
# токен красил вкладки и подсказки при 1.7:1, а чекер печатал «все пары
# проходят». Проверка ловит пропуск, а эти тесты — что она его ловит.
# ---------------------------------------------------------------------------


def _css_with_extra(tmp_path: Path, extra: str) -> Path:
    """Копия реального app.css с дописанным правилом (токены остаются валидными)."""
    module = _load_module()
    css = module._CSS_PATH.read_text(encoding="utf-8")
    target = tmp_path / "app.css"
    target.write_text(css + "\n" + extra + "\n", encoding="utf-8")
    return target


def test_coverage_guard_passes_on_the_real_stylesheet() -> None:
    """Живой app.css проверку проходит — иначе гейт красный на пустом месте."""
    module = _load_module()
    assert module.check_pairs_cover_text_tokens() == []


def test_coverage_guard_flags_a_text_token_without_a_pair(tmp_path: Path) -> None:
    """Новый токен в `color:` без пары в `_PAIRS` — провал с именем токена."""
    module = _load_module()
    css = _css_with_extra(tmp_path, ".made-up { color: var(--color-faint); }")
    failures = module.check_pairs_cover_text_tokens(css)
    assert failures and "--color-faint" in failures[0], failures


def test_coverage_guard_ignores_non_color_properties(tmp_path: Path) -> None:
    """`border-left-color`/`background-color` — не цвет текста, порог не их."""
    module = _load_module()
    css = _css_with_extra(
        tmp_path,
        ".made-up { border-left-color: var(--color-faint); background-color: var(--color-faint); }",
    )
    assert module.check_pairs_cover_text_tokens(css) == []


def test_coverage_guard_reads_css_in_js(tmp_path: Path) -> None:
    """Тема редактора задаёт цвета объектом в JS — токен оттуда тоже покрывается.

    Без этого `.cm-gutters` в `core.js` остаётся слепой зоной: токен, живущий
    только там, прошёл бы мимо проверки по одному `app.css`.
    """
    module = _load_module()
    css = _css_with_extra(tmp_path, "")
    (tmp_path / "theme.js").write_text(
        '".cm-gutters": { color: "var(--color-faint)", border: "none" },\n',
        encoding="utf-8",
    )
    failures = module.check_pairs_cover_text_tokens(css)
    assert failures and "--color-faint" in failures[0], failures


def test_check_reports_coverage_gap_together_with_ratio_failures(tmp_path: Path) -> None:
    """`check()` возвращает и пропуски покрытия — иначе CI их не увидит."""
    module = _load_module()
    css = _css_with_extra(tmp_path, ".made-up { color: var(--color-faint); }")
    assert any("не покрыт ни одной парой" in f for f in module.check(css))


# ---------------------------------------------------------------------------
# issue #921 (AUD-2-02): палитра темы «авто» — та, что видят по умолчанию.
# Раньше гейт её не читал вовсе и верил комментарию «те же значения».
# ---------------------------------------------------------------------------


def _themed_css(module: ModuleType, *, auto: dict[str, str] | None = None) -> str:
    """CSS с тремя блоками: light, dark и media-блок темы «авто»."""

    def _block(tokens: dict[str, str]) -> str:
        return "\n".join(f"  {name}: {value};" for name, value in tokens.items())

    light = _min_tokens(module, dark=False)
    dark = _min_tokens(module, dark=True)
    auto_tokens = dict(dark) if auto is None else {**dark, **auto}
    return (
        f':root, [data-theme="light"] {{\n{_block(light)}\n}}\n'
        f'[data-theme="dark"] {{\n{_block(dark)}\n}}\n'
        "@media (prefers-color-scheme: dark) {\n"
        f"  :root:not([data-theme]) {{\n{_block(auto_tokens)}\n  }}\n"
        "}\n"
    )


def test_auto_theme_matching_dark_is_quiet(tmp_path: Path) -> None:
    """Совпали — контраст «авто» доказан тёмной темой, второй раз не считаем."""
    module = _load_module()
    css = tmp_path / "app.css"
    css.write_text(_themed_css(module), encoding="utf-8")

    assert module.check_auto_theme_matches_dark(css) == []


def test_drifted_auto_theme_is_flagged(tmp_path: Path) -> None:
    """Расхождение означает: проверено одно, а показывается другое."""
    module = _load_module()
    css = tmp_path / "app.css"
    css.write_text(_themed_css(module, auto={"--color-text": "#3a3a3a"}), encoding="utf-8")

    failures = module.check_auto_theme_matches_dark(css)

    assert len(failures) == 1
    assert "--color-text" in failures[0] and "#3a3a3a" in failures[0]


def test_missing_auto_block_is_a_failure(tmp_path: Path) -> None:
    """Не «нечего проверять»: без блока тёмной темы нет у большинства."""
    module = _load_module()
    css = tmp_path / "app.css"
    light = "\n".join(f"  {k}: {v};" for k, v in _min_tokens(module, dark=False).items())
    dark = "\n".join(f"  {k}: {v};" for k, v in _min_tokens(module, dark=True).items())
    css.write_text(
        f':root, [data-theme="light"] {{\n{light}\n}}\n[data-theme="dark"] {{\n{dark}\n}}\n',
        encoding="utf-8",
    )

    assert module.check_auto_theme_matches_dark(css)


def test_token_nobody_paints_with_is_out_of_scope(tmp_path: Path) -> None:
    """Мёртвый токен ничего не красит — требовать его в «авто» незачем.

    В самом `app.css` таких четыре (`--color-blue`/`gold`/`orange`/`purple`):
    объявлены в обеих темах, не используются нигде. Гейт, считающий мёртвые
    значения, шумит — а шумящий гейт обходят.
    """
    module = _load_module()
    css = tmp_path / "app.css"
    text = _themed_css(module)
    text = text.replace("  --color-unused: #123456;\n", "")
    # Токен есть в dark, но не в «авто» — и ни в одной паре.
    text = text.replace(
        '[data-theme="dark"] {\n', '[data-theme="dark"] {\n  --color-unused: #123456;\n', 1
    )
    css.write_text(text, encoding="utf-8")

    assert module.check_auto_theme_matches_dark(css) == []


def test_check_runs_the_auto_theme_guard(tmp_path: Path) -> None:
    """Провод: `check()` обязан звать проверку, а не только объявлять её."""
    module = _load_module()
    css = tmp_path / "app.css"
    css.write_text(_themed_css(module, auto={"--color-text": "#3a3a3a"}), encoding="utf-8")

    assert any("авто" in f for f in module.check(css))


def test_real_stylesheet_has_the_auto_block() -> None:
    """Страховка от тихого исчезновения блока темы по умолчанию."""
    module = _load_module()

    assert module.check_auto_theme_matches_dark() == []
