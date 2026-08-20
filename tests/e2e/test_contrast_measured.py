"""Контраст меряется в браузере, а не выводится из списка пар (issue #921).

Находка `DES-1-04`: `scripts/check_contrast.py` проверяет **объявленные** пары
«токен-текст / токен-фон». Список `_PAIRS` пишет человек, и он может разойтись
с тем, что реально стоит за текстом на странице: пара останется зелёной, а
пользователь увидит другое сочетание. Статический гейт про это не узнает
никогда — цвет фона под элементом задаёт каскад, а его текстом не разобрать.

Поэтому измерение здесь: настоящий Chromium, настоящий каскад, `getComputedStyle`.
Проверяются два утверждения, и второе важнее первого:

1. **Заявленный контраст выполняется** — измеренная пара даёт не меньше порога,
   записанного для неё в `_PAIRS`.
2. **Незаявленных пар на странице нет** — каждое реальное сочетание
   «текст/фон» находится в `_PAIRS`. Именно этого статический гейт не умеет: он
   знает, что токен где-то упомянут, но не знает, на чём этот текст лежит.

Пороги берутся из `_PAIRS`, а не назначаются здесь заново. Иначе тест начал бы
спорить с осознанными решениями проекта — например, badge-warning держится на
3.0 по WCAG 1.4.11 как мелкая статус-плашка, и «поправить» это до 4.5 значило бы
переголосовать чужое решение молча, тестом.

Два подводных камня, оба поймались при первом же прогоне:

* **Переходы.** Смена темы анимируется, и `getComputedStyle` сразу после
  переключения отдаёт промежуточные цвета — серую кашу между палитрами. Правки
  «под неё» были бы правками несуществующего. Анимации глушатся до измерения.
* **Невидимый текст.** Скип-ссылка `.sr-only` — 1×1 px под `clip`, и её
  «синий по умолчанию на чёрном» ни на что не влияет: в видимом состоянии
  (`:focus`) у неё свои цвета. Элементы, которых не видно, из выборки убираются.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_CHECK_CONTRAST = pathlib.Path(__file__).parent.parent.parent / "scripts" / "check_contrast.py"

# Разделы, по которым проходим: у каждого своя разметка и свои поверхности.
_SECTIONS = ("check", "downloader", "glossary", "rules", "insights", "progress", "sandbox")

# Реальные пары «цвет текста / эффективный фон» со страницы. Фон собирается
# снизу вверх с учётом альфы: полупрозрачная подложка обязана быть наложена на
# то, что под ней, иначе получится цвет, которого на экране нет.
_MEASURE = """
() => {
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x.trim()));
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const effectiveBg = (el) => {
    const stack = [];
    let node = el, opaque = false;
    while (node) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0) {
        stack.push(bg);
        if (bg.a >= 1) { opaque = true; break; }
      }
      node = node.parentElement;
    }
    let acc = opaque ? stack.pop() : {r: 255, g: 255, b: 255, a: 1};
    for (let i = stack.length - 1; i >= 0; i--) acc = over(stack[i], acc);
    return acc;
  };
  const hidden = (el, cs, rect) => {
    if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return true;
    // .sr-only и его родня: 1x1 под clip — текст есть в DOM, на экране его нет.
    if (rect.width < 2 || rect.height < 2) return true;
    return /rect\\((0p?x?,\\s*){3}0p?x?\\)/.test(cs.clip);
  };
  const name = (el) => {
    let out = el.tagName.toLowerCase();
    if (typeof el.className === 'string' && el.className.trim()) {
      out += '.' + el.className.trim().split(/\\s+/).join('.');
    }
    return out;
  };
  const rows = [];
  for (const el of document.querySelectorAll('*')) {
    const own = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (hidden(el, cs, rect)) continue;
    const fg = parse(cs.color);
    if (!fg || fg.a === 0) continue;
    const bg = effectiveBg(el);
    const flat = over(fg, bg);
    rows.push({
      el: name(el),
      text: el.textContent.trim().slice(0, 40),
      fg: [Math.round(flat.r), Math.round(flat.g), Math.round(flat.b)],
      bg: [Math.round(bg.r), Math.round(bg.g), Math.round(bg.b)],
    });
  }
  return rows;
}
"""


@pytest.fixture(scope="module")
def contrast() -> ModuleType:
    """`scripts/check_contrast.py` как модуль — источник пар и порогов."""
    spec = importlib.util.spec_from_file_location("_check_contrast", _CHECK_CONTRAST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _declared(contrast: ModuleType, theme: str) -> dict[tuple[tuple[int, ...], ...], Any]:
    """Объявленные пары темы как ``{(fg_rgb, bg_rgb): Pair}``."""
    tokens = contrast.load_tokens()[theme]

    def _rgb(ref: str) -> tuple[int, ...] | None:
        value = ref if ref.startswith("#") else tokens.get(ref)
        return contrast._hex_to_rgb(value) if value else None

    out = {}
    for pair in contrast._PAIRS:
        if theme not in pair.themes:
            continue
        fg, bg = _rgb(pair.fg), _rgb(pair.bg)
        if fg is not None and bg is not None:
            out[(fg, bg)] = pair
    return out


def _ratio(contrast: ModuleType, fg: list[int], bg: list[int]) -> float:
    to_hex = "#{:02x}{:02x}{:02x}".format
    return contrast.contrast_ratio(to_hex(*fg), to_hex(*bg))


def _measure(page: Any, theme: str) -> list[dict[str, Any]]:
    """Все видимые пары «текст/фон» страницы во всех разделах данной темы."""
    page.evaluate(f"() => document.documentElement.setAttribute('data-theme', '{theme}')")
    page.wait_for_timeout(50)
    rows: list[dict[str, Any]] = []
    for section in _SECTIONS:
        page.click(f'.sidebar-item[data-section="{section}"]')
        page.wait_for_selector(f"#view-{section}:not([hidden])", timeout=10000)
        for row in page.evaluate(_MEASURE):
            rows.append({**row, "section": section})
    return rows


@pytest.fixture
def measured(page: Any, e2e_server: str) -> Any:
    """Страница с отключёнными переходами — иначе меряются цвета анимации."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check", timeout=15000)
    page.add_style_tag(
        content=(
            "*, *::before, *::after { transition: none !important; animation: none !important; }"
        )
    )
    return lambda theme: _measure(page, theme)


@pytest.mark.parametrize("theme", ["light", "dark"])
class TestRealPairsMatchTheDeclaration:
    def test_every_pair_on_screen_is_declared(
        self, measured: Any, contrast: ModuleType, theme: str
    ) -> None:
        """Незаявленная пара — это контраст, который никто не проверял.

        Ровно та дыра, которую статический гейт закрыть не может: он знает, что
        токен где-то стоит цветом текста, но не знает, что под этим текстом.
        """
        declared = _declared(contrast, theme)

        unknown = [
            f"{row['section']}/{row['el']} «{row['text']}»: "
            f"rgb{tuple(row['fg'])} на rgb{tuple(row['bg'])} "
            f"(контраст {_ratio(contrast, row['fg'], row['bg']):.2f})"
            for row in measured(theme)
            if (tuple(row["fg"]), tuple(row["bg"])) not in declared
        ]

        assert not unknown, "пары со страницы нет в _PAIRS:\n  " + "\n  ".join(sorted(set(unknown)))

    def test_declared_threshold_holds_on_screen(
        self, measured: Any, contrast: ModuleType, theme: str
    ) -> None:
        """Порог берётся из `_PAIRS` — тест не переголосовывает решения проекта."""
        declared = _declared(contrast, theme)

        failures = []
        for row in measured(theme):
            pair = declared.get((tuple(row["fg"]), tuple(row["bg"])))
            if pair is None:
                continue
            ratio = _ratio(contrast, row["fg"], row["bg"])
            if ratio < pair.min_ratio:
                failures.append(
                    f"{row['section']}/{row['el']} «{row['text']}»: пара «{pair.label}» "
                    f"даёт {ratio:.2f} при требуемых {pair.min_ratio}"
                )

        assert not failures, "\n  ".join(
            ["измеренный контраст ниже объявленного:", *sorted(set(failures))]
        )

    def test_measurement_is_not_empty(self, measured: Any, theme: str) -> None:
        """Пустая выборка прошла бы обе проверки — этого достаточно для гейта.

        Сломайся селекторы разделов или отбор видимых элементов, и тест стал бы
        зелёным именно потому, что перестал что-либо мерить.
        """
        rows = measured(theme)

        assert len(rows) > 100, f"измерено всего {len(rows)} пар — выборка подозрительно мала"
        assert len({row["section"] for row in rows}) == len(_SECTIONS), "разделы не открылись"
