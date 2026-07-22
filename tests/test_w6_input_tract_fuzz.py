"""test_w6_input_tract_fuzz.py — fuzz входного тракта Stepik (issue #691, аудит
2026-07-20 раунд 2 W6).

Три вектора из аудита §10, все три — по факту ЗАЩИЩЕНЫ; тесты фиксируют это как
регрессионный контракт:

- **task_page_parser аномальным HTML** — разбор (``html.parser.HTMLParser`` +
  ``is_function_style`` на ``ast.parse``) НИКОГДА не бросает на битом/произвольном
  входе и держит форму результата. `is_function_style` ловит ``(SyntaxError,
  ValueError)`` — последний документирован у ``ast.parse`` на null-байтах (#691).
- **ReDoS** — регулярки ссылок (``_ZIP_URL_RE`` / ``_GITHUB_URL_RE``) линейны:
  негативные классы ``[^"']*`` с литеральным якорем, без вложенных квантификаторов
  → нет катастрофического backtracking; патологический вход завершается мгновенно.
- **path-injection через имена шагов/файлов** — ``slugify`` вырезает сепараторы и
  точки (``..`` не выживает), ``build_task_directory`` всегда остаётся ПОД
  ``root_dir``. Имена файлов из ZIP/GitHub используются лишь как цифровой фильтр
  (``clean.isdigit()``), запись идёт writer'ом по int-индексу, не по имени из
  архива — Zip Slip структурно невозможен (характеризовано чтением кода в
  ``core/test_source_fetcher.py``).

Живой сети/ФС нет: тестируется чистый разбор строк и построение путей.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from stepik_grader.core import task_page_parser
from stepik_grader.core.task_page_parser import (
    extract_external_test_links,
    extract_tests_from_html,
    is_function_style,
)
from stepik_grader.downloader import build_task_directory
from stepik_grader.downloader_config import slugify

# --- task_page_parser: аномальный HTML не роняет разбор ------------------------

_MALFORMED_HTML = [
    "",  # пусто
    "<table>",  # незакрытый тег
    "<td>ячейка без строки</td>",  # td без tr
    "<table><tr><td>1</td></tr></table>",  # < 3 колонок
    "<table><tr><td>1</td><td>a\x00b</td><td>o</td></tr></table>",  # null в ячейке
    "<TABLE><TR><TD>верхний регистр</TD></TR>",  # регистр + незакрыто
    "<table>" + "<tr><td>a</td>" * 2000,  # много незакрытых ячеек
    "<table><tr><td>&lt;b&gt;</td><td>&amp;</td><td>out</td></tr></table>",  # entity
    "не html вовсе — просто текст с control-байтами \x01\x02\x1f\x7f",
    "<table><tr><td></td><td></td><td></td></tr></table>",  # пустые ячейки
    "<table><tr><td>x</td><td>y</td><td>z</td><td>w</td></tr></table>",  # > 3 колонок
]


@pytest.mark.parametrize("html", _MALFORMED_HTML)
def test_extract_tests_never_raises_on_malformed_html(html: str) -> None:
    """Парсер таблицы кейсов не бросает на битом HTML и держит форму результата."""
    result = extract_tests_from_html(html)
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, tuple) and len(item) == 3
        assert all(isinstance(x, str) for x in item)
        assert item[2] in ("stdin", "function")


@pytest.mark.parametrize("html", _MALFORMED_HTML)
def test_extract_external_links_never_raises_on_malformed_html(html: str) -> None:
    """Извлечение ZIP/GitHub-ссылок не бросает и всегда возвращает две строки-списка."""
    zips, githubs = extract_external_test_links(html)
    assert isinstance(zips, list) and isinstance(githubs, list)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "x = \x00",  # null-байт
        "\x00\x01\x02",  # только control-байты
        "(" * 5000,  # экстремальная вложенность
        "переменная = 5",  # unicode-идентификатор (function-mode)
        "9" * 100_000,  # гигантский числовой литерал
        "def f(:",  # синтаксически битое
        "print(input())",  # заведомо stdin
        "мусор ! @ # $ %",
    ],
)
def test_is_function_style_never_raises(text: str) -> None:
    """is_function_style возвращает bool на любом входе и никогда не бросает."""
    assert isinstance(is_function_style(text), bool)


def test_is_function_style_swallows_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """issue #691: ``ast.parse`` документированно бросает ``ValueError`` на исходнике
    с null-байтами (версинно-зависимо; часть CPython отдаёт ``SyntaxError``).
    is_function_style обязан заглушить его в ``False``, а не уронить разбор.
    Форсируем ValueError мокингом — тест дискриминирующий (падал бы до расширения
    ``except`` до ``(SyntaxError, ValueError)``)."""

    def _raise_value_error(_src: str) -> object:
        raise ValueError("source code string cannot contain null bytes")

    monkeypatch.setattr(task_page_parser.ast, "parse", _raise_value_error)
    assert is_function_style("x = 5") is False


# --- ReDoS: регулярки ссылок линейны -----------------------------------------


def test_zip_url_regex_linear_on_pathological_input() -> None:
    """``_ZIP_URL_RE`` линеен: гигантский ``href`` без закрытия завершается мгновенно
    (негативный класс ``[^"']*`` — нет катастрофического backtracking)."""
    evil = 'href="' + "a" * 300_000  # нет закрывающей кавычки и '.zip'
    start = time.perf_counter()
    zips, _ = extract_external_test_links(evil)
    assert time.perf_counter() - start < 2.0  # линейно → мс; ReDoS → секунды/зависание
    assert zips == []


def test_github_url_regex_linear_on_pathological_input() -> None:
    """``_GITHUB_URL_RE`` (два ``[^"']*`` вокруг литерала ``github.com``) линеен даже
    на патологическом входе с совпавшим якорем, но без закрывающей кавычки."""
    evil = 'href="' + "a" * 200_000 + "github.com" + "b" * 200_000  # нет закрытия
    start = time.perf_counter()
    _, githubs = extract_external_test_links(evil)
    assert time.perf_counter() - start < 2.0
    assert githubs == []


# --- path-injection: имена Stepik → безопасный путь ---------------------------

_PATH_INJECTION = [
    "../../../../etc/passwd",
    "..\\..\\..\\windows\\system32",
    "/etc/shadow",
    "C:\\Windows\\System32",
    "~/.ssh/id_rsa",
    "foo/../../bar",
    "a\x00b",
    "....//....//",
    "...",  # только точки → пусто → fallback
    "",
]


@pytest.mark.parametrize("evil", _PATH_INJECTION)
def test_slugify_strips_separators_and_traversal(evil: str) -> None:
    """slugify не оставляет сепараторов, ``..`` или null — результат пригоден как
    ОДИН безопасный компонент пути (fallback 'task' на пустом)."""
    slug = slugify(evil)
    assert "/" not in slug
    assert "\\" not in slug
    assert ".." not in slug
    assert "\x00" not in slug
    assert slug  # непусто


def test_build_task_directory_confined_to_root(tmp_path: pathlib.Path) -> None:
    """build_task_directory с враждебными именами курса/секции/урока/шага ВСЕГДА
    остаётся под ``root_dir`` — path-injection через имена Stepik невозможен."""
    root = tmp_path / "StepikTasks"
    evil = "../../../../../../etc"
    result = build_task_directory(root, evil, evil, evil, 1, evil)
    assert result.resolve().is_relative_to(root.resolve())


# --- property-based fuzz (hypothesis) -----------------------------------------
# issue #646 (T3): без hypothesis в venv жёсткий top-level import обрушил бы всю
# коллекцию pytest — importorskip пропускает только этот блок.
pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


@settings(deadline=None, max_examples=300)
@given(st.text())
def test_fuzz_extract_tests_never_raises(html: str) -> None:
    """На тысячах произвольных строк extract_tests_from_html не бросает и держит форму."""
    result = extract_tests_from_html(html)
    assert isinstance(result, list)
    for inp, exp, kind in result:
        assert isinstance(inp, str) and isinstance(exp, str)
        assert kind in ("stdin", "function")


@settings(deadline=None, max_examples=300)
@given(st.text())
def test_fuzz_is_function_style_never_raises(text: str) -> None:
    """is_function_style возвращает bool на любом сгенерированном тексте."""
    assert isinstance(is_function_style(text), bool)


@settings(deadline=None, max_examples=200)
@given(st.text())
def test_fuzz_extract_links_never_raises(html: str) -> None:
    """extract_external_test_links не бросает на произвольном тексте."""
    zips, githubs = extract_external_test_links(html)
    assert isinstance(zips, list) and isinstance(githubs, list)


@settings(deadline=None, max_examples=300)
@given(st.text())
def test_fuzz_slugify_never_yields_path_separator(text: str) -> None:
    """slugify на любом входе не порождает сепаратор/``..``/null и непуст."""
    slug = slugify(text)
    assert "/" not in slug and "\\" not in slug
    assert ".." not in slug and "\x00" not in slug
    assert slug
