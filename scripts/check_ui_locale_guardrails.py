#!/usr/bin/env python3
"""scripts/check_ui_locale_guardrails.py — CI-guard локализации web-UI оболочки (issue #547).

`check_locale_guardrails.py` (issue #264) стережёт только каталог сообщений
web-API (`core/locales/*.json`), но НЕ саму UI-оболочку (`web/static/`). После
эпика E4 (`ui.json` статики #545 + динамика JS-рендеров #546) регресс
локализации UI был невидим ни e2e, ни guardrail'ом — эта проверка закрывает
дыру тремя машинными защитами:

1. **Паритет ru/en в `ui.json`.** Оба языка обязаны иметь ровно один и тот же
   набор ключей — иначе `?lang=en`/`t(key)` частично покажет русский текст (или
   маркер) там, где перевод забыли.
2. **Покрытие ключей.** Каждый `data-i18n[-*]`-ключ из `index.html` и каждый
   литеральный `t("...")`/`tp(.., "...")`-ключ из JS-рендеров существует в
   каталоге — иначе UI покажет видимый маркер `⟦key⟧` (issue #546) вместо
   текста. Плюрал-ключи проверяются во всех формах (`.one/.few/.many`).
3. **Нет голых кириллических литералов вне `ui.json`.** Ни в видимых текст-узлах
   и переводимых атрибутах `index.html` без `data-i18n`, ни в строковых
   литералах JS — кроме комментариев, `console.warn`/`console.error`-диагностики
   и строк, помеченных `i18n-exempt` (серверные фильтр-значения `GLOSSARY_CHIPS`).

По образцу `check_locale_guardrails.py`: чистый stdlib (`html.parser`/`json`/
`pathlib`) — детерминированно и кроссплатформенно (Windows/Linux/macOS).

Ограничение (как у #264-guard): ловятся только ЛИТЕРАЛЬНЫЕ `t("...")`-ключи;
динамические (`t(entry[0])`, `t(ch.labelKey)`) статикой не покрываются — их
защищает паритет + сам факт присутствия в каталоге.

    python scripts/check_ui_locale_guardrails.py   # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

__all__ = [
    "check_key_parity",
    "check_keys_present",
    "check_no_bare_cyrillic",
    "collect_index_html",
    "has_cyrillic",
    "load_ui_catalog",
    "main",
    "scan_js",
]

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "src" / "stepik_grader" / "web" / "static"
_UI_JSON = _STATIC / "locales" / "ui.json"
_INDEX_HTML = _STATIC / "index.html"
_JS_FILES = (
    "core.js",
    "app.js",
    "grade.js",
    "content.js",
    "sandbox.js",
    "downloader.js",
    "trace-player.js",
)

# Атрибуты разметки, несущие ключ каталога (issue #545).
_I18N_ATTRS = ("data-i18n", "data-i18n-placeholder", "data-i18n-title", "data-i18n-aria-label")
# Переводимый HTML-атрибут → его data-i18n-компаньон (для проверки, что
# кириллический атрибут покрыт ключом).
_TRANSLATABLE_ATTRS = {
    "placeholder": "data-i18n-placeholder",
    "title": "data-i18n-title",
    "aria-label": "data-i18n-aria-label",
}
# Теги, чьё текстовое содержимое не является видимой UI-надписью.
_NON_VISIBLE_TAGS = frozenset({"script", "style", "template"})
# HTML5 void-элементы — без закрывающего тега, в стек вложенности не кладём.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# Литеральные ключи в вызовах t("...") / tp(.., "..."). Ключ каталога —
# lowercase-namespace с точками (grade.col_file, common.request_error).
_T_CALL = re.compile(r'\bt\(\s*["\']([a-z][\w.]+)["\']')
_TP_CALL = re.compile(r'\btp\(\s*[^,]+?,\s*["\']([a-z][\w.]+)["\']')


def has_cyrillic(text: str) -> bool:
    """Содержит ли строка кириллицу (весь блок U+0400–U+04FF, как в test_i18n_guardrails)."""
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


def load_ui_catalog() -> tuple[dict[str, str], dict[str, str]]:
    """(`ru`, `en`) словари каталога `ui.json` (пустые, если файл битый/не-объект)."""
    try:
        data = json.loads(_UI_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    raw_ru, raw_en = data.get("ru"), data.get("en")
    ru: dict[str, str] = raw_ru if isinstance(raw_ru, dict) else {}
    en: dict[str, str] = raw_en if isinstance(raw_en, dict) else {}
    return ru, en


class _IndexParser(HTMLParser):
    """Собирает `data-i18n[-*]`-ключи и находит голую кириллицу без ключа.

    Ведёт стек открытых непустых элементов с флагом «есть `data-i18n`»; текст-узел
    считается покрытым, если несёт его непосредственный родитель (там же лежит
    RU-fallback из #545). Void-теги в стек не кладём (у них нет закрывашки).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.i18n_keys: set[str] = set()
        self.bare_text: list[tuple[int, str]] = []
        self.bare_attr: list[tuple[int, str, str]] = []
        self._stack: list[tuple[str, bool]] = []
        self._skip_depth = 0

    def _collect_attrs(self, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        for a in _I18N_ATTRS:
            if ad.get(a):
                self.i18n_keys.add(ad[a])
        for attr, i18n_attr in _TRANSLATABLE_ATTRS.items():
            val = ad.get(attr, "")
            if has_cyrillic(val) and i18n_attr not in ad:
                self.bare_attr.append((self.getpos()[0], attr, val))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect_attrs(attrs)
        if tag in _VOID_TAGS:
            return
        has_i18n = any(k == "data-i18n" for k, _ in attrs)
        self._stack.append((tag, has_i18n))
        if tag in _NON_VISIBLE_TAGS:
            self._skip_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <tag/> — самозакрывающийся: ключи собрать, в стек не класть.
        self._collect_attrs(attrs)

    def handle_endtag(self, tag: str) -> None:
        # Снять со стека до парного открытия (терпим необязательные закрывашки).
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                if tag in _NON_VISIBLE_TAGS and self._skip_depth:
                    self._skip_depth -= 1
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not has_cyrillic(data):
            return
        if self._stack and self._stack[-1][1]:
            return  # непосредственный родитель несёт data-i18n → покрыто
        self.bare_text.append((self.getpos()[0], data.strip()[:60]))


def collect_index_html() -> _IndexParser:
    """Распарсить `index.html` и вернуть парсер с собранными ключами/нарушениями."""
    parser = _IndexParser()
    parser.feed(_INDEX_HTML.read_text(encoding="utf-8"))
    return parser


# После этих значимых символов `/` начинает regex-литерал, а не деление
# (стандартная эвристика JS-лексеров: regex может стоять там, где ждут значение).
_REGEX_PRECEDERS = frozenset("([{,;=:!&|?+-*%~^<>")


def _iter_js_string_literals(src: str) -> Iterator[tuple[int, str]]:
    """Порождает (lineno, content) для строковых литералов JS, пропуская
    комментарии `//` и `/* */` И regex-литералы `/.../` (иначе кавычка внутри
    класса regex — `/[&<>"']/g` — «открыла» бы фиктивную строку). Экранированные
    кавычки учитываются; template-literal `${...}` намеренно НЕ разбирается
    (её содержимое — редкий источник кириллицы, а ключи t() — латиница)."""
    i, n, line = 0, len(src), 1
    last_sig = ""  # последний значимый (не пробельный) символ — для regex/division
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
        elif c == "/" and (last_sig == "" or last_sig in _REGEX_PRECEDERS):
            # regex-литерал: до неэкранированного `/` вне символьного класса [...]
            i += 1
            in_class = False
            while i < n and (src[i] != "/" or in_class):
                if src[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "\n":
                    line += 1
                i += 1
            i += 1  # закрывающий `/`
            last_sig = "/"
        elif c in "'\"`":
            quote, start_line, buf = c, line, []
            i += 1
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    buf.append(src[i + 1])
                    if src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                buf.append(src[i])
                i += 1
            i += 1  # закрывающая кавычка
            yield start_line, "".join(buf)
            last_sig = quote
        else:
            last_sig = c
            i += 1


def scan_js(path: Path) -> tuple[set[str], set[str], list[tuple[int, str]]]:
    """(`t()`-ключи, базовые `tp()`-ключи, голая кириллица) одного JS-файла.

    Голой кириллицей НЕ считаются строки с `i18n-exempt` или `console.warn`/
    `console.error` на той же физической строке."""
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    keys = set(_T_CALL.findall(src))
    plural = set(_TP_CALL.findall(src))
    bare: list[tuple[int, str]] = []
    for lineno, content in _iter_js_string_literals(src):
        if not has_cyrillic(content):
            continue
        line_text = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if any(marker in line_text for marker in ("i18n-exempt", "console.warn", "console.error")):
            continue
        bare.append((lineno, content[:60]))
    return keys, plural, bare


def check_key_parity(errors: list[str]) -> None:
    """`ui.json` ru и en содержат ровно один и тот же набор ключей."""
    ru, en = load_ui_catalog()
    only_ru = sorted(set(ru) - set(en))
    only_en = sorted(set(en) - set(ru))
    if only_ru:
        errors.append("ui.json en missing key(s) present in ru: " + ", ".join(only_ru))
    if only_en:
        errors.append("ui.json ru missing key(s) present in en: " + ", ".join(only_en))
    if not only_ru and not only_en:
        print(f"ui.json key parity: ru and en both have {len(ru)} key(s).")


def check_keys_present(errors: list[str]) -> None:
    """Все `data-i18n[-*]`/`t()`/`tp()`-ключи присутствуют в каталоге (ru)."""
    ru, _ = load_ui_catalog()
    parser = collect_index_html()
    html_missing = sorted(k for k in parser.i18n_keys if k not in ru)

    js_keys: set[str] = set()
    js_plural: set[str] = set()
    for name in _JS_FILES:
        keys, plural, _ = scan_js(_STATIC / name)
        js_keys |= keys
        js_plural |= plural
    js_missing = sorted(k for k in js_keys if k not in ru)
    plural_missing = sorted(
        f"{base}.{form}"
        for base in js_plural
        for form in ("one", "few", "many")
        if f"{base}.{form}" not in ru
    )

    if html_missing:
        errors.append(
            "ui.json missing data-i18n key(s) from index.html: " + ", ".join(html_missing)
        )
    if js_missing:
        errors.append("ui.json missing t()-key(s) from JS renders: " + ", ".join(js_missing))
    if plural_missing:
        errors.append("ui.json missing tp()-plural form(s): " + ", ".join(plural_missing))
    if not (html_missing or js_missing or plural_missing):
        print(
            f"Key coverage: {len(parser.i18n_keys)} data-i18n + {len(js_keys)} t() + "
            f"{len(js_plural)} tp() key(s), all present in ui.json."
        )


def check_no_bare_cyrillic(errors: list[str]) -> None:
    """Нет голых кириллических литералов вне `ui.json` (index.html + JS-рендеры)."""
    parser = collect_index_html()
    for line, text in parser.bare_text:
        errors.append(f"index.html:{line} visible Cyrillic text without data-i18n: {text!r}")
    for line, attr, val in parser.bare_attr:
        errors.append(f"index.html:{line} Cyrillic @{attr} without data-i18n-* companion: {val!r}")

    bare_total = 0
    for name in _JS_FILES:
        _, _, bare = scan_js(_STATIC / name)
        for line, content in bare:
            bare_total += 1
            errors.append(
                f"{name}:{line} bare Cyrillic string literal (route via t()): {content!r}"
            )
    if not (parser.bare_text or parser.bare_attr or bare_total):
        print("No bare Cyrillic literals outside ui.json (index.html + JS renders clean).")


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_key_parity(errors)
    check_keys_present(errors)
    check_no_bare_cyrillic(errors)

    if errors:
        print("\nFAIL: UI locale guardrails violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\nOK: ui.json ru/en parity holds; all keys covered; no bare Cyrillic outside ui.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
