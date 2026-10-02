"""core/html_to_markdown.py — условие задачи из HTML в читаемый Markdown (issue #1176).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Соседний ``core/task_page_parser.py`` достаёт из того же HTML **данные**
(тест-кейсы, ссылки); здесь — **текст для человека**. Общего состояния нет,
поэтому модули не зависят друг от друга и тестируются каждый сам по себе, без
моков сети и ФС.

Почему конвертация, а не «оставить как есть». ``downloader`` клал тело шага в
``task.md`` дословно, то есть под расширением ``.md`` лежал HTML: условие
приходилось читать сквозь ``<p>``, ``<code>`` и сущности вроде ``&gt;``. Сырой
исходник при этом никуда не девается — он уезжает в ``task.html`` рядом, и
перегенерация ``task.md`` с изменёнными правилами не требует сети.

**Полный HTML не поддерживается намеренно** — только теги, реально
встречающиеся в условиях Stepik. Неизвестный тег разворачивается в своё
содержимое: условие с незнакомой вёрсткой теряет оформление, но не текст.

**Экранирование минимальное — только ``|`` внутри ячеек таблицы.** Там это
структурная необходимость: неэкранированная черта рвёт строку таблицы на лишние
колонки. В обычном тексте ``#``, ``>`` и ``*`` остаются как есть: цель файла —
чтобы условие читалось глазами, а сплошные ``\\#`` ради теоретической
round-trip-точности читаемость как раз убивают.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["html_to_markdown"]

# Теги, чьё тело не текст условия. HTMLParser отдаёт содержимое <script>
# обычным handle_data — без явного пропуска код скрипта уехал бы в task.md
# (тот же класс дефекта, что issue #838 в соседнем парсере).
_SKIP_TAGS = frozenset({"script", "style", "head", "title"})

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# Блочные теги, которые сами по себе не несут разметки Markdown: их роль —
# отбить абзац. Разворачиваются в содержимое с пустой строкой вокруг.
_BLOCK_WRAPPERS = frozenset({"p", "div", "section", "article", "header", "footer"})

_BOLD_TAGS = frozenset({"strong", "b"})
_ITALIC_TAGS = frozenset({"em", "i"})

# Внутри ячейки таблицы перевод строки невозможен — GFM держит строку таблицы
# на одной физической строке. Стандартный выход — <br>, его понимают и GitHub,
# и просмотрщик условия в вебе.
_CELL_BREAK = "<br>"

_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"\s+")


def html_to_markdown(html: str, local_files: dict[str, str] | None = None) -> str:
    """Преобразует тело шага Stepik в Markdown.

    Args:
        html: тело шага (``step.block.text``) как пришло из API.
        local_files: карта «URL вложения -> имя файла рядом с задачей». Ссылки
            из карты переписываются на локальные: условие, оставшееся указывать
            в сеть, отправляет читателя качать то, что уже лежит в соседнем
            файле. Не попавшие в карту (не приехавшие) ссылки остаются
            сетевыми — врать про локальный файл, которого нет, хуже.

    Returns:
        Markdown-текст. Пустой ввод даёт пустую строку.

    Битый HTML не приводит к исключению: ``HTMLParser`` разбирает его
    best-effort, незакрытые теги просто не находят пары. Скачивание задачи не
    имеет права падать из-за вёрстки условия.
    """
    if not html:
        return ""
    converter = _MarkdownConverter(local_files or {})
    converter.feed(html)
    converter.close()
    return converter.result()


class _MarkdownConverter(HTMLParser):
    """HTMLParser, собирающий Markdown-текст условия.

    Вывод копится в стеке буферов: инлайновый тег (``a``, ``code``, ``strong``)
    и ячейка таблицы кладут наверх свой буфер, а на закрывающем теге забирают
    накопленное и оборачивают разметкой. Без стека пришлось бы искать начало
    ссылки задним числом в уже склеенной строке.
    """

    def __init__(self, local_files: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self._local_files = local_files
        self._stack: list[list[str]] = [[]]
        self._skip_depth = 0
        self._pre_depth = 0
        self._list_stack: list[list[str | int]] = []
        self._quote_depth = 0
        self._link_href: list[str] = []
        self._table_rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._row_is_header = False
        self._header_rows = 0

    # -- вывод -------------------------------------------------------------

    def _emit(self, text: str) -> None:
        self._stack[-1].append(text)

    def _push(self) -> None:
        self._stack.append([])

    def _pop(self) -> str:
        return "".join(self._stack.pop()) if len(self._stack) > 1 else ""

    def result(self) -> str:
        """Готовый Markdown: без хвостовых пробелов и тройных пустых строк.

        Склеивается **весь** стек, а не только нижний буфер: незакрытый тег
        (``<p>текст`` без пары) оставляет содержимое наверху стека, и чтение
        одного дна молча теряло бы весь хвост условия. Разметки у такого куска
        не будет, текст — будет.
        """
        text = "".join("".join(buffer) for buffer in self._stack)
        text = _TRAILING_SPACE_RE.sub("", text)
        text = _MULTI_BLANK_RE.sub("\n\n", text)
        return text.strip() + "\n" if text.strip() else ""

    # -- теги --------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = 1
            return

        attr = {key: (value or "") for key, value in attrs}

        if tag == "br":
            self._emit(_CELL_BREAK if self._row is not None else "\n")
        elif tag == "hr":
            self._emit("\n\n---\n\n")
        elif tag == "img":
            self._emit(self._image(attr))
        elif tag in _HEADINGS or tag in _BLOCK_WRAPPERS or tag == "blockquote":
            self._start_block(tag)
        elif tag == "pre":
            self._pre_depth += 1
            self._push()
        elif tag == "code":
            self._push()
        elif tag in _BOLD_TAGS or tag in _ITALIC_TAGS:
            self._push()
        elif tag == "a":
            self._link_href.append(attr.get("href", ""))
            self._push()
        elif tag in {"ul", "ol"}:
            self._list_stack.append([tag, 0])
        elif tag == "li":
            self._push()
        elif tag == "table":
            self._table_rows = []
            self._header_rows = 0
        elif tag == "tr" and self._table_rows is not None:
            self._row = []
            self._row_is_header = False
        elif tag in {"td", "th"} and self._row is not None:
            self._row_is_header = self._row_is_header or tag == "th"
            self._push()

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth -= 1
            return

        if tag in _HEADINGS:
            self._emit(f"\n\n{'#' * _HEADINGS[tag]} {self._pop().strip()}\n\n")
        elif tag in _BLOCK_WRAPPERS:
            body = self._pop().strip()
            self._emit(f"\n\n{body}\n\n" if body else "")
        elif tag == "blockquote":
            self._quote_depth -= 1
            body = self._pop().strip()
            quoted = "\n".join(f"> {line}".rstrip() for line in body.split("\n"))
            self._emit(f"\n\n{quoted}\n\n" if body else "")
        elif tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
            self._emit(_fence(self._pop()))
        elif tag == "code":
            self._emit(self._inline_code(self._pop()))
        elif tag in _BOLD_TAGS:
            self._emit(_wrap(self._pop(), "**"))
        elif tag in _ITALIC_TAGS:
            self._emit(_wrap(self._pop(), "*"))
        elif tag == "a":
            self._emit(self._link(self._pop()))
        elif tag in {"ul", "ol"}:
            if self._list_stack:
                self._list_stack.pop()
            if not self._list_stack:
                self._emit("\n")
        elif tag == "li":
            self._emit(self._list_item(self._pop()))
        elif tag in {"td", "th"} and self._row is not None:
            self._row.append(_cell(self._pop()))
        elif tag == "tr" and self._row is not None:
            if self._table_rows is not None and self._row:
                self._table_rows.append(self._row)
                if self._row_is_header and self._header_rows == len(self._table_rows) - 1:
                    self._header_rows += 1
            self._row = None
        elif tag == "table":
            rows, self._table_rows = self._table_rows, None
            self._row = None
            self._emit(_table(rows or [], self._header_rows))

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if self._pre_depth:
            self._emit(data)
            return
        text = _WHITESPACE_RE.sub(" ", data)
        if text.strip():
            self._emit(text)
        elif not self._at_block_edge():
            # Перевод строки между тегами — это пробел внутри абзаца, но сразу
            # после закрытого блока он превращался в отступ в начале следующей
            # строки. Значащий пробел сохраняем, отступ — нет.
            self._emit(text)

    def _at_block_edge(self) -> bool:
        """Буфер пуст или только что закрыт блок — пробел здесь незначащий."""
        buffer = self._stack[-1]
        return not buffer or buffer[-1].endswith("\n")

    # -- сборка отдельных конструкций ---------------------------------------

    def _start_block(self, tag: str) -> None:
        if tag == "blockquote":
            self._quote_depth += 1
        self._push()

    def _image(self, attr: dict[str, str]) -> str:
        src = self._resolve(attr.get("src", ""))
        alt = attr.get("alt", "").strip()
        return f"![{alt}]({src})" if src else ""

    def _link(self, text: str) -> str:
        raw = self._link_href.pop() if self._link_href else ""
        href = self._resolve(raw)
        label = text.strip()
        if not href:
            return label
        if href != raw:
            # Вложение приехало локально. Ссылка остаётся ссылкой даже когда
            # подпись совпала с именем файла: иначе `files.txt` превращается в
            # обычное слово, и открыть соседний файл из условия уже нельзя.
            return f"[{label or href}]({href})"
        # Голая ссылка (текст совпадает с адресом) остаётся голой: `[url](url)`
        # читается вдвое длиннее и ничего не добавляет.
        return href if not label or label == href else f"[{label}]({href})"

    def _resolve(self, url: str) -> str:
        """Ссылку на приехавшее вложение заменить на имя файла рядом (#1112)."""
        return self._local_files.get(url, url)

    def _inline_code(self, text: str) -> str:
        if self._pre_depth:
            # <pre><code>…</code></pre> — обратные кавычки ставит забор снаружи,
            # иначе внутри блока появилась бы вторая пара.
            return text
        return _wrap_code(text)

    def _list_item(self, text: str) -> str:
        body = text.strip()
        if not body:
            return ""
        depth = max(0, len(self._list_stack) - 1)
        indent = "  " * depth
        if self._list_stack and self._list_stack[-1][0] == "ol":
            counter = int(self._list_stack[-1][1]) + 1
            self._list_stack[-1][1] = counter
            marker = f"{counter}. "
        else:
            marker = "- "
        # Вложенный список внутри пункта уже пришёл со своими переводами строк —
        # достаточно выровнять продолжение под текст пункта.
        lines = body.split("\n")
        rest = "".join(f"\n{indent}  {line}".rstrip() for line in lines[1:])
        return f"\n{indent}{marker}{lines[0]}{rest}"


# ---------------------------------------------------------------------------
# Чистые помощники — без состояния парсера
# ---------------------------------------------------------------------------


def _wrap(text: str, marker: str) -> str:
    """Обернуть непустой текст парой маркеров, сохранив внешние пробелы."""
    body = text.strip()
    if not body:
        return text
    lead = " " if text[:1].isspace() else ""
    tail = " " if text[-1:].isspace() else ""
    return f"{lead}{marker}{body}{marker}{tail}"


def _wrap_code(text: str) -> str:
    """Инлайновый код: забор из стольких кавычек, чтобы не совпасть с телом."""
    body = text.strip("\n")
    if not body:
        return ""
    runs = [len(match) for match in re.findall(r"`+", body)]
    ticks = "`" * (max(runs, default=0) + 1)
    pad = " " if body.startswith("`") or body.endswith("`") else ""
    return f"{ticks}{pad}{body}{pad}{ticks}"


def _fence(text: str) -> str:
    """Блок кода забором. Забор длиннее любой строки бэктиков внутри тела."""
    body = text.strip("\n")
    if not body.strip():
        return ""
    runs = [len(match) for match in re.findall(r"^\s*(`{3,})", body, re.MULTILINE)]
    fence = "`" * max([3, *[run + 1 for run in runs]])
    return f"\n\n{fence}\n{body}\n{fence}\n\n"


def _cell(text: str) -> str:
    """Содержимое ячейки в одну строку: перевод — ``<br>``, ``|`` экранирована."""
    body = text.strip().replace("|", "\\|")
    return _MULTI_BLANK_RE.sub("\n", body).replace("\n", _CELL_BREAK).strip()


def _table(rows: list[list[str]], header_rows: int) -> str:
    """Собрать GFM-таблицу; ряды выравниваются по самому широкому.

    Таблица без ``<th>`` всё равно получает шапку — из первой строки: GFM без
    разделителя таблицей не считается и выводится слипшимся текстом. В условиях
    Stepik первая строка почти всегда и есть «Ввод / Вывод», так что догадка
    совпадает с замыслом вёрстки.
    """
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    if header_rows or len(padded) > 1:
        head, body = padded[0], padded[1:]
    else:
        head, body = [""] * width, padded
    lines = [
        "| " + " | ".join(head) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n\n" + "\n".join(lines) + "\n\n"
