"""core/html_sanitizer.py — очистка условия задачи перед показом в браузере (issue #1177).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

``block.text`` — **чужой HTML из внешнего источника**. Вставленный в DOM как
есть, он даёт XSS на странице, которая ходит в локальный API грейдера, — а там
лежит рабочий токен Stepik. Поэтому чистка обязательна, и делается она **на
сервере**: JS-очистку обходят, а правила whitelist должны быть покрыты тестами
в одном месте. CSP (``web/http_guards.py``) остаётся второй линией обороны, а не
первой.

Чистим **при показе, а не при скачивании**. Правила whitelist будут меняться, и
менять их не должно означать «перекачай сорок задач»: сырой источник лежит в
``task.html`` рядом с задачей (issue #1176), очистка выполняется в момент показа.

**Модель — whitelist, а не blacklist**, и это не стилистика. Чёрный список
защищает ровно от того, что в него вписали: любой не перечисленный вектор
(``onpointerrawupdate``, новый URL-схема, экзотический тег) проходит насквозь.
Здесь наоборот: тег без записи в :data:`_ALLOWED_TAGS` разворачивается в
содержимое, атрибут без записи в :data:`_ALLOWED_ATTRS` не выживает вовсе.
Поэтому ``on*``-обработчики отсекаются не правилом про ``on*``, а тем, что их
никто не разрешал.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

__all__ = ["sanitize_statement_html"]

# Теги, которые выживают. Список ровно из issue #1177 — то, из чего реально
# состоят условия Stepik.
_ALLOWED_TAGS = frozenset(
    {
        "p", "br", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li",
        "pre", "code",
        "strong", "em", "b", "i",
        "blockquote",
        "table", "thead", "tbody", "tr", "th", "td",
        "a", "img",
    }
)  # fmt: skip

# Теги, чьё СОДЕРЖИМОЕ тоже не должно попасть в вывод. Для остальных незнакомых
# тегов правило другое — развернуть в текст: условие с незнакомой вёрсткой
# теряет оформление, но не смысл. Здесь же содержимое — не текст условия:
# тело `<script>` HTMLParser отдаёт обычным handle_data, и без явного пропуска
# код скрипта уехал бы в вывод как проза (тот же класс, что issue #838).
_DROP_WITH_CONTENT = frozenset(
    {"script", "style", "iframe", "object", "embed", "form", "svg", "math", "template", "noscript"}
)

# Атрибуты по тегам. Всё, чего здесь нет, не выживает — включая `style`
# (CSS сам по себе вектор), `class`/`id` (ломают вёрстку страницы-хозяина) и
# любые `on*`.
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

# Теги без закрывающей пары.
_VOID_TAGS = frozenset({"br", "hr", "img"})

# Схемы, по которым разрешено ходить ссылке. Проверяется БЕЛЫМ списком:
# запрет `javascript:` и `data:` по списку пропустил бы `vbscript:`,
# `jar:`, `blob:` и всё, что появится завтра.
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})

_SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.\-]*)\s*:")
_DIGITS_RE = re.compile(r"^\d{1,3}$")


def sanitize_statement_html(html_text: str, local_files: dict[str, str] | None = None) -> str:
    """Очищает тело условия до безопасного подмножества HTML.

    Args:
        html_text: сырое тело шага (``task.html``) как пришло из API Stepik.
        local_files: карта «URL вложения -> имя файла рядом с задачей». Только
            попавшие в неё картинки сохраняют ``src``; внешний источник не
            подставляется — его всё равно заблокирует CSP, а ходить в сеть при
            показе скачанной задачи незачем.

    Returns:
        HTML из :data:`_ALLOWED_TAGS` с закрытыми тегами и экранированным
        текстом. Пустой ввод даёт пустую строку.

    Битый HTML не приводит к исключению и не оставляет незакрытых тегов:
    незакрытое в источнике закрывается на выходе, лишняя закрывающая пара
    игнорируется. Иначе фрагмент утащил бы за собой вёрстку всей страницы.
    """
    if not html_text:
        return ""
    cleaner = _Sanitizer(local_files or {})
    cleaner.feed(html_text)
    cleaner.close()
    return cleaner.result()


def _is_safe_url(url: str) -> bool:
    """Ссылка либо относительная, либо с разрешённой схемой.

    Относительные (``files.txt``, ``/media/x``, ``#anchor``) безопасны: схемы у
    них нет, значит нет и ``javascript:``.
    """
    match = _SCHEME_RE.match(url)
    if match is None:
        return True
    return match.group(1).lower() in _SAFE_SCHEMES


def _is_external(url: str) -> bool:
    """Ссылка уводит на другой хост — значит, ей нужен ``rel``."""
    return _SCHEME_RE.match(url) is not None


class _Sanitizer(HTMLParser):
    """HTMLParser, собирающий очищенный HTML.

    Стек открытых тегов ведётся явно: источник может не закрыть тег, а фрагмент
    уезжает внутрь чужой страницы — незакрытый ``<table>`` утащил бы за собой
    её разметку.
    """

    def __init__(self, local_files: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self._local_files = local_files
        self._out: list[str] = []
        self._open: list[str] = []
        self._drop_depth = 0

    def result(self) -> str:
        """Очищенный HTML с закрытыми хвостами."""
        tail = "".join(f"</{tag}>" for tag in reversed(self._open))
        self._open.clear()
        return "".join(self._out) + tail

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._drop_depth:
            if tag in _DROP_WITH_CONTENT:
                self._drop_depth += 1
            return
        if tag in _DROP_WITH_CONTENT:
            self._drop_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return  # незнакомый тег разворачивается: текст внутри уцелеет

        rendered = self._render_open(tag, {k: (v or "") for k, v in attrs})
        if rendered is None:
            return
        self._out.append(rendered)
        if tag not in _VOID_TAGS:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<br/>` и `<img ... />` — HTMLParser зовёт отдельный хук и НЕ зовёт
        # handle_endtag. Без этого метода самозакрывающийся тег молча терялся бы.
        if self._drop_depth or tag not in _ALLOWED_TAGS:
            return
        rendered = self._render_open(tag, {k: (v or "") for k, v in attrs})
        if rendered is not None:
            self._out.append(rendered)

    def handle_endtag(self, tag: str) -> None:
        if self._drop_depth:
            if tag in _DROP_WITH_CONTENT:
                self._drop_depth -= 1
            return
        if tag in _VOID_TAGS or tag not in self._open:
            # Закрывающая без пары — источник кривой; молча пропускаем, иначе
            # в выводе появился бы `</div>`, ломающий страницу-хозяина.
            return
        while self._open:
            current = self._open.pop()
            self._out.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._drop_depth or not data:
            return
        # Текст экранируется всегда. Именно здесь `&lt;script&gt;` из источника,
        # раскодированный `convert_charrefs`, возвращается обратно в сущности,
        # а не превращается в живой тег.
        self._out.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        """Комментарии выбрасываются целиком: условию они не нужны."""

    # -- сборка открывающего тега -------------------------------------------

    def _render_open(self, tag: str, attr: dict[str, str]) -> str | None:
        """Открывающий тег с отфильтрованными атрибутами; ``None`` — не выводить."""
        if tag == "img":
            return self._render_img(attr)

        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        parts: list[str] = []
        for name in sorted(allowed & attr.keys()):
            value = attr[name]
            if name == "href":
                if not _is_safe_url(value):
                    continue  # `javascript:` — тег остаётся, ссылка исчезает
                parts.append(self._attr("href", value))
                if _is_external(value):
                    parts.append(' rel="noopener noreferrer"')
            elif name in {"colspan", "rowspan"}:
                if _DIGITS_RE.match(value.strip()):
                    parts.append(self._attr(name, value.strip()))
            else:
                parts.append(self._attr(name, value))
        return f"<{tag}{''.join(parts)}>"

    def _render_img(self, attr: dict[str, str]) -> str | None:
        """Картинка выживает, только если указывает на приехавшее вложение.

        Внешний источник не подставляется: CSP его всё равно заблокирует, и
        запрос наружу при показе скачанной задачи не нужен. Вместо битой
        картинки выводится ``alt`` текстом — иначе на месте иллюстрации
        оставалась бы пустота без объяснения.
        """
        src = self._local_files.get(attr.get("src", ""))
        alt = attr.get("alt", "").strip()
        if src is None:
            return html.escape(alt, quote=False) if alt else None
        parts = [self._attr("src", src)]
        if alt:
            parts.append(self._attr("alt", alt))
        if attr.get("title"):
            parts.append(self._attr("title", attr["title"]))
        return f"<img{''.join(parts)}>"

    @staticmethod
    def _attr(name: str, value: str) -> str:
        """Атрибут с экранированными кавычками — иначе значение вырвется из них."""
        return f' {name}="{html.escape(value, quote=True)}"'
