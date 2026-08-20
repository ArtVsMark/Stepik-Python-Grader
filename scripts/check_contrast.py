#!/usr/bin/env python3
"""check_contrast.py — WCAG-контраст пар дизайн-токенов веб-интерфейса (issue #424).

Парсит `--color-*` токены из `web/static/app.css` (светлая и тёмная темы) и
проверяет набор реальных UI-пар «текст/фон» на порог WCAG 2.1 1.4.3:
мелкий текст ≥ 4.5:1, крупный/UI-элемент ≥ 3:1. Плейсхолдеры (1.4.3 их не
покрывает) держим на мягком пороге ~3:1 ради читаемости.

Запуск: ``python scripts/check_contrast.py`` — печатает таблицу и выходит с
кодом 1 при любом провале. Импортируется тестом ``tests/test_contrast.py``
(функция :func:`check`) для кросс-OS-энфорса без отдельного CI-джоба.
"""

from __future__ import annotations

import contextlib
import re
import sys
from pathlib import Path

__all__ = [
    "Pair",
    "check",
    "check_auto_theme_matches_dark",
    "check_pairs_cover_text_tokens",
    "contrast_ratio",
    "load_tokens",
    "main",
]

_CSS_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "stepik_grader" / "web" / "static" / "app.css"
)

# Заголовки блоков токенов в app.css: селектор → ключ темы. Светлые значения
# живут в `:root, [data-theme="light"]`, тёмные — в `[data-theme="dark"]`
# (media-дубль `:root:not([data-theme])` держит те же значения — проверяем один
# канонический источник, чтобы не считать дважды).
_THEME_SELECTORS: dict[str, str] = {
    ':root, [data-theme="light"]': "light",
    '[data-theme="dark"]': "dark",
}

# issue #921 (находка AUD-2-02): палитра темы «авто» — та, что пользователь
# видит ПО УМОЛЧАНИЮ, пока не нажал переключатель. Она жила в media-блоке и в
# проверку не входила вовсе: комментарий рядом утверждал «те же значения», но
# это утверждение ничем не подтверждалось. Разойдись они — и контраст поедет
# именно у большинства, а гейт продолжит печатать «все пары проходят».
#
# Сверяем на равенство, а не гоняем пары второй раз: совпали — контраст
# доказан тёмной темой; разошлись — гейт краснеет и называет токен.
_AUTO_SELECTOR = ":root:not([data-theme])"

_TOKEN_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;")


class Pair:
    """UI-пара «передний план / фон» с порогом контраста.

    ``fg``/``bg`` — имя токена (``--color-…``) или литеральный ``#hex``.
    ``themes`` ограничивает проверку темами (по умолчанию обе).
    """

    def __init__(
        self,
        label: str,
        fg: str,
        bg: str,
        min_ratio: float,
        themes: tuple[str, ...] = ("light", "dark"),
    ) -> None:
        self.label = label
        self.fg = fg
        self.bg = bg
        self.min_ratio = min_ratio
        self.themes = themes


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Разобрать ``#rgb``/``#rrggbb`` (альфа-каналы отбрасываются) в кортеж 0-255."""
    h = value.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h[:3])
    elif len(h) in (6, 8):
        h = h[:6]
    else:
        raise ValueError(f"неподдерживаемый hex-цвет: {value!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _linearize(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (_linearize(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """Отношение контраста WCAG 2.1 двух ``#hex``-цветов (1.0-21.0)."""
    l1 = _luminance(_hex_to_rgb(fg))
    l2 = _luminance(_hex_to_rgb(bg))
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def load_tokens(css_path: Path = _CSS_PATH) -> dict[str, dict[str, str]]:
    """Извлечь ``{theme: {token: hex}}`` из блоков токенов app.css."""
    text = css_path.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for selector, theme in _THEME_SELECTORS.items():
        start = text.find(selector + " {")
        if start == -1:
            start = text.find(selector + "{")
        if start == -1:
            raise ValueError(f"блок токенов не найден: {selector}")
        brace = text.find("{", start)
        end = text.find("}", brace)
        block = text[brace + 1 : end]
        out[theme] = {m.group(1): m.group(2) for m in _TOKEN_RE.finditer(block)}
    return out


# Пары «токен-текст / токен-фон» — реальные использования из app.css. Порог 4.5
# для мелкого текста, 3.0 для крупного/UI. Литеральные ``#hex`` (напр. цвет
# текста кнопки) допустимы вместо имени токена.
_PAIRS: list[Pair] = [
    # Базовый текст на поверхностях.
    Pair("text / bg", "--color-text", "--color-bg", 4.5),
    Pair("text / surface", "--color-text", "--color-surface", 4.5),
    Pair("text / surface-offset", "--color-text", "--color-surface-offset", 4.5),
    # Приглушённый текст (panel-title, form-label, card-body, kpi-label, th…).
    Pair("text-muted / surface", "--color-text-muted", "--color-surface", 4.5),
    Pair("text-muted / surface-offset", "--color-text-muted", "--color-surface-offset", 4.5),
    Pair("text-muted / bg", "--color-text-muted", "--color-bg", 4.5),
    # issue #805: hover-фон списков и кнопок — самый тёмный (в light) и самый
    # светлый (в dark) из поверхностей, то есть худший случай для muted; именно
    # на нём висит пустое состояние `.side-list li.empty` при наведении.
    Pair("text-muted / surface-dynamic", "--color-text-muted", "--color-surface-dynamic", 4.5),
    # issue #921 (DES-1-04): найдено измерением в браузере — на этом фоне
    # сидит обычный текст `.btn-secondary` и активной вкладки панели, а не
    # только приглушённый. Пара была настоящей и незаявленной.
    Pair("text / surface-dynamic", "--color-text", "--color-surface-dynamic", 4.5),
    Pair("text-muted / surface-2", "--color-text-muted", "--color-surface-2", 4.5),
    Pair("text-muted / surface-offset-2", "--color-text-muted", "--color-surface-offset-2", 4.5),
    # Плейсхолдеры (1.4.3 не покрывает — мягкий порог ради читаемости).
    Pair("placeholder / surface", "--color-text-placeholder", "--color-surface", 3.0),
    # Первичная кнопка: текст on-primary на фоне primary-btn во всех состояниях
    # (btn-primary base/hover/active, palette.active).
    Pair("on-primary / primary-btn", "--color-on-primary", "--color-primary-btn", 4.5),
    Pair("on-primary / primary-hover", "--color-on-primary", "--color-primary-hover", 4.5),
    Pair("on-primary / primary-active", "--color-on-primary", "--color-primary-active", 4.5),
    # Первичный акцент-текст на highlight-фоне (sidebar.active, badge-primary,
    # mode-btn.active, chip.active, side-list.selected).
    Pair("primary / primary-highlight", "--color-primary", "--color-primary-highlight", 4.5),
    # Обычный текст на highlight-фоне (trace-line.is-active, mem/trace is-changed).
    Pair("text / primary-highlight", "--color-text", "--color-primary-highlight", 4.5),
    # Первичный акцент как текст на нейтральных поверхностях (term-card-title,
    # rule-code, ссылки errcard/trace, mem-var-name).
    Pair("primary / surface", "--color-primary", "--color-surface", 4.5),
    Pair("primary / bg", "--color-primary", "--color-bg", 4.5),
    # Статус-бейджи «цвет на *-highlight».
    Pair("error / error-highlight", "--color-error", "--color-error-highlight", 4.5),
    Pair("success / success-highlight", "--color-success", "--color-success-highlight", 4.5),
    # badge-warning на highlight — мелкая статус-плашка, порог 3.0 (WCAG 1.4.11,
    # осознанно, ср. комментарий #298 в app.css).
    Pair("warning / warning-highlight", "--color-warning", "--color-warning-highlight", 3.0),
    # Статус-цвета как текст на поверхностях (verdict-*, .msg, editor-dirty).
    Pair("error / surface", "--color-error", "--color-surface", 4.5),
    Pair("success / surface", "--color-success", "--color-surface", 4.5),
    Pair("warning / surface", "--color-warning", "--color-surface", 4.5),
    # issue #1213: строка «что не так с secrets.json» стоит в панели
    # доступа прямо на фоне раздела. Пару нашёл браузерный тест — ровно
    # тем, ради чего он и заведён: новая разметка добавила настоящее
    # сочетание, которого не было в списке.
    Pair("warning / bg", "--color-warning", "--color-bg", 4.5),
    # issue #425: цвета подсветки синтаксиса CodeMirror на фоне редактора
    # (--color-surface) — все ≥4.5:1 в обеих темах.
    Pair("cm-keyword / surface", "--cm-keyword", "--color-surface", 4.5),
    Pair("cm-literal / surface", "--cm-literal", "--color-surface", 4.5),
    Pair("cm-string / surface", "--cm-string", "--color-surface", 4.5),
    Pair("cm-function / surface", "--cm-function", "--color-surface", 4.5),
    Pair("cm-type / surface", "--cm-type", "--color-surface", 4.5),
    Pair("cm-operator / surface", "--cm-operator", "--color-surface", 4.5),
    Pair("cm-comment / surface", "--cm-comment", "--color-surface", 4.5),
    Pair("cm-invalid / surface", "--cm-invalid", "--color-surface", 4.5),
]


# issue #805 (DESW-01): токены, которые CSS ставит в `color:`, но текстом они
# не являются — для них порог 1.4.3 не применяется. Список явный и с причиной:
# молча «не покрыт парой» больше не бывает, см. check_pairs_cover_text_tokens.
_NON_TEXT_FG: dict[str, str] = {
    "--color-text-inverse": "текст на сплошной заливке акцентом — пара считается от неё",
}

# `color: var(--token)` в CSS и `color: "var(--token)"` в CSS-в-JS (тема
# CodeMirror собирается объектом в core.js). Отрицательный просмотр назад
# отсекает `border-left-color:`/`background-color:` и `caretColor:`.
_COLOR_DECL_RE = re.compile(r"(?<![-\w])color\s*:\s*\"?var\((--[a-z0-9-]+)\)")


def check_pairs_cover_text_tokens(css_path: Path = _CSS_PATH) -> list[str]:
    """Каждый токен, стоящий как цвет текста, должен быть в ``_PAIRS``.

    Дыра, из-за которой завели issue #805: ``--color-text-faint`` красил
    вкладки, подсказки и номера строк при 1.7:1, а гейт печатал «все пары
    проходят» — просто потому, что пары с этим токеном в списке не было.
    Проверка ловит уже не значение, а сам пропуск.

    Смотрим и на соседние ``*.js``: тема редактора задаёт цвета объектом
    (``.cm-gutters`` в ``core.js``), и токен, живущий только там, мимо
    проверки по одному ``app.css`` прошёл бы ровно так же.
    """
    sources = [css_path, *sorted(css_path.parent.glob("*.js"))]
    used: set[str] = set()
    for path in sources:
        used |= {m.group(1) for m in _COLOR_DECL_RE.finditer(path.read_text(encoding="utf-8"))}
    covered = {pair.fg for pair in _PAIRS}
    missing = sorted(used - covered - set(_NON_TEXT_FG))
    return [
        f"{token} используется как цвет текста, но не покрыт ни одной парой в _PAIRS "
        f"(добавьте пару или объявите токен нетекстовым в _NON_TEXT_FG)"
        for token in missing
    ]


def check_auto_theme_matches_dark(css_path: Path = _CSS_PATH) -> list[str]:
    """Палитра темы «авто» обязана совпадать с ``[data-theme="dark"]`` (#921).

    Тема «авто» — состояние по умолчанию: её видит каждый, кто не трогал
    переключатель. Её значения лежат отдельным media-блоком, и до этой проверки
    гейт их не читал вовсе — верил комментарию «те же значения».

    Отсутствующий блок — тоже провал, а не «нечего проверять»: без него у
    большинства пользователей тёмной темы просто нет.
    """
    text = css_path.read_text(encoding="utf-8")
    start = text.find(_AUTO_SELECTOR + " {")
    if start == -1:
        start = text.find(_AUTO_SELECTOR + "{")
    if start == -1:
        return [
            f"блок токенов темы «авто» ({_AUTO_SELECTOR}) не найден — "
            "по умолчанию тёмной темы не будет ни у кого"
        ]
    brace = text.find("{", start)
    auto = {
        m.group(1): m.group(2) for m in _TOKEN_RE.finditer(text[brace + 1 : text.find("}", brace)])
    }
    dark = load_tokens(css_path)["dark"]

    # Сверяем не всю палитру, а ровно те токены, чей контраст утверждён парами
    # выше. Именно про них мы говорим «проверено», и именно их расхождение
    # означает, что проверено одно, а показывается другое. Токен, не входящий
    # ни в одну пару, ничего не красит — его отсутствие в «авто» безвредно, а
    # требование объявить его превратило бы гейт в счетовода мёртвых значений.
    # Начнут красить — `check_pairs_cover_text_tokens` затащит его в _PAIRS, и
    # сверка подхватит его сама.
    claimed = {ref for pair in _PAIRS for ref in (pair.fg, pair.bg) if not ref.startswith("#")}

    failures = [
        f'тема «авто»: {token} = {auto[token]}, а в [data-theme="dark"] = {dark[token]} '
        "— контраст проверен для второго, а видят первое"
        for token in sorted(claimed & auto.keys() & dark.keys())
        if auto[token].lower() != dark[token].lower()
    ]
    failures += [
        f"тема «авто»: нет токена {token} — контраст для него проверен только "
        'в [data-theme="dark"], а по умолчанию видят «авто»'
        for token in sorted(claimed & dark.keys() - auto.keys())
    ]
    return failures


def _resolve(ref: str, tokens: dict[str, str]) -> str:
    """Имя токена → его hex; литеральный ``#hex`` возвращается как есть."""
    if ref.startswith("#"):
        return ref
    if ref not in tokens:
        raise KeyError(ref)
    return tokens[ref]


def check(css_path: Path = _CSS_PATH) -> list[str]:
    """Проверить все пары в обеих темах. Вернуть список строк-провалов (пусто = ок)."""
    themes = load_tokens(css_path)
    failures: list[str] = check_pairs_cover_text_tokens(css_path)
    failures += check_auto_theme_matches_dark(css_path)
    for pair in _PAIRS:
        for theme in pair.themes:
            tokens = themes[theme]
            try:
                fg = _resolve(pair.fg, tokens)
                bg = _resolve(pair.bg, tokens)
            except KeyError as exc:
                failures.append(f"[{theme}] {pair.label}: токен {exc} не найден")
                continue
            ratio = contrast_ratio(fg, bg)
            if ratio + 1e-9 < pair.min_ratio:
                failures.append(
                    f"[{theme}] {pair.label}: {ratio:.2f}:1 ({fg} на {bg}) < {pair.min_ratio}:1"
                )
    return failures


def main() -> int:
    """CLI-точка: печать таблицы контраста + ненулевой код при провале."""
    # Windows-консоль по умолчанию cp1252 не кодирует кириллицу/✓/— в выводе —
    # переключаем stdout на utf-8, иначе `python scripts/check_contrast.py`
    # падает UnicodeEncodeError (CI windows-latest, тест test_cli_exits_zero).
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):  # зависит от платформы stdout
            reconfigure(encoding="utf-8")
    themes = load_tokens()
    print(f"WCAG-контраст токен-пар — {_CSS_PATH.name}\n")
    worst = 21.0
    for theme in ("light", "dark"):
        print(f"── {theme} ──")
        tokens = themes[theme]
        for pair in _PAIRS:
            if theme not in pair.themes:
                continue
            try:
                fg = _resolve(pair.fg, tokens)
                bg = _resolve(pair.bg, tokens)
            except KeyError as exc:
                print(f"  ? {pair.label:<32}  n/a   (токен {exc} не найден)")
                continue
            ratio = contrast_ratio(fg, bg)
            worst = min(worst, ratio - pair.min_ratio)
            mark = "✓" if ratio + 1e-9 >= pair.min_ratio else "✗"
            print(f"  {mark} {pair.label:<32} {ratio:5.2f}:1  (need {pair.min_ratio})")
        print()
    failures = check()
    if failures:
        print(f"ПРОВАЛ: {len(failures)} пар ниже порога:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("Все пары проходят WCAG-порог.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
