"""tests/test_i18n_guardrails.py — no hardcoded Russian message literals in the
web layer, plus web/i18n.py catalog-renderer behavior (issue #264).

Контекст: до этого issue ``web/viewmodels.py``/``web/server.py`` держали
человекочитаемый текст ответов API прямо в русских f-string/строковых
литералах. Теперь весь такой текст рендерится из ``core/locales/<lang>.json``
через ``web/i18n.py`` (``render_message``/``message_fields``), а
``viewmodels.py``/``server.py`` содержат только ASCII ``message_id``-ключи.

Докстринги и комментарии по-прежнему легитимно русские (того требует
CLAUDE.md) — тест их не трогает, только строковые литералы-выражения (то,
что реально попадает в рантайм-значения, а не в объяснение кода для
читателя).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from stepik_grader.web import i18n as web_i18n
from stepik_grader.web import server as web_server
from stepik_grader.web import viewmodels

_SRC_ROOT = pathlib.Path(__file__).parent.parent / "src" / "stepik_grader"
_GUARDED_FILES = [
    _SRC_ROOT / "web" / "viewmodels.py",
    _SRC_ROOT / "web" / "server.py",
]

# Диапазон кириллических букв (включая Ё/ё, гражданский алфавит) — тот же
# простой критерий, что и в issue: "строка содержит кириллицу".
_CYRILLIC_RANGE = ("Ѐ", "ӿ")


def _has_cyrillic(text: str) -> bool:
    lo, hi = _CYRILLIC_RANGE
    return any(lo <= ch <= hi for ch in text)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """id() строковых Constant-узлов, которые являются module/func/class докстрингами."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if ast.get_docstring(node, clean=False) is None:
                continue
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _cyrillic_literals_outside_docstrings(path: pathlib.Path) -> list[tuple[int, str]]:
    """(lineno, value) для строковых Constant-литералов с кириллицей вне докстрингов.

    Ловит и обычные строки, и сегменты f-строк — CPython парсит f-string как
    ``JoinedStr`` из ``Constant`` (литеральные куски) + ``FormattedValue``
    (``{expr}``), поэтому ``ast.walk`` находит литеральный текст f-строки как
    обычный ``ast.Constant`` без специальной обработки.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring_ids = _docstring_constant_ids(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstring_ids:
            continue
        if _has_cyrillic(node.value):
            found.append((node.lineno, node.value))
    return found


@pytest.mark.parametrize("path", _GUARDED_FILES, ids=lambda p: p.name)
def test_no_cyrillic_message_literals_outside_docstrings(path: pathlib.Path) -> None:
    """Регрессия issue #264: все user-facing сообщения — через message_id-каталог.

    Докстринги/комментарии по-прежнему легитимно русские (CLAUDE.md) — эта
    проверка их не касается (комментарии не попадают в AST вообще, докстринги
    явно исключены через ``ast.get_docstring``).
    """
    offenders = _cyrillic_literals_outside_docstrings(path)
    assert offenders == [], (
        f"{path.name}: found Cyrillic string literal(s) outside docstrings — "
        "route user-facing text through web/i18n.py's message catalog instead:\n"
        + "\n".join(f"  line {n}: {v!r}" for n, v in offenders)
    )


# ---------------------------------------------------------------------------
# web/i18n.py — render_message()/message_fields()/resolve_lang()
# ---------------------------------------------------------------------------


def test_resolve_lang_defaults_to_ru_for_unknown_or_missing() -> None:
    assert web_i18n.resolve_lang(None) == "ru"
    assert web_i18n.resolve_lang("") == "ru"
    assert web_i18n.resolve_lang("fr") == "ru"
    assert web_i18n.resolve_lang("RU") == "ru"
    assert web_i18n.resolve_lang(" en ") == "en"


def test_render_message_interpolates_params() -> None:
    assert web_i18n.render_message("path_not_found", "ru", path="x.py") == "Путь не найден: x.py"
    assert web_i18n.render_message("path_not_found", "en", path="x.py") == "Path not found: x.py"


def test_render_message_unknown_key_returns_key_itself() -> None:
    assert web_i18n.render_message("totally_made_up_key", "ru") == "totally_made_up_key"


def test_message_fields_shape() -> None:
    fields = web_i18n.message_fields("path_not_found", "ru", path="x.py")
    assert fields == {
        "message": "Путь не найден: x.py",
        "message_id": "path_not_found",
        "message_params": {"path": "x.py"},
    }


def test_message_fields_empty_params_dict_when_no_interpolation() -> None:
    fields = web_i18n.message_fields("specify_url", "ru")
    assert fields["message_params"] == {}


# ---------------------------------------------------------------------------
# viewmodels.py — lang="en" gives English text; default (ru) unchanged
# ---------------------------------------------------------------------------


def test_grade_path_default_lang_is_russian_and_unchanged() -> None:
    missing = pathlib.Path("/no/such/path.py")
    data = viewmodels.grade_path(missing)
    assert data["message"] == f"Путь не найден: {missing}"
    assert data["message_id"] == "path_not_found"


def test_grade_path_lang_en_translates_message() -> None:
    missing = pathlib.Path("/no/such/path.py")
    data = viewmodels.grade_path(missing, lang="en")
    assert data["message"] == f"Path not found: {missing}"
    assert data["message_id"] == "path_not_found"


def test_list_solutions_lang_en(tmp_path: pathlib.Path) -> None:
    data = viewmodels.list_solutions(tmp_path / "nope", lang="en")
    assert data["message"] == f"Folder not found: {tmp_path / 'nope'}"


def test_read_source_lang_en(tmp_path: pathlib.Path) -> None:
    data = viewmodels.read_source(tmp_path / "nope.py", lang="en")
    assert data["message_id"] == "file_read_failed"
    assert "Failed to read file" in data["message"]


def test_save_solution_lang_en(tmp_path: pathlib.Path) -> None:
    data = viewmodels.save_solution(tmp_path / "nope", None, "print(1)\n", lang="en")
    assert data["message"] == f"Folder not found: {tmp_path / 'nope'}"


# ---------------------------------------------------------------------------
# server.py — _lang_from_query() query-param parsing
# ---------------------------------------------------------------------------


def test_lang_from_query_defaults_and_reads_param() -> None:
    from urllib.parse import urlparse

    assert web_server._lang_from_query(urlparse("/api/grade")) == "ru"
    assert web_server._lang_from_query(urlparse("/api/grade?lang=en")) == "en"
    assert web_server._lang_from_query(urlparse("/api/grade?lang=fr")) == "ru"


# ---------------------------------------------------------------------------
# issue #821: подписи бейджей достижений живут в каталоге UI, а не на сервере
#
# Ключ строится конкатенацией (`"progress.badge_" + b.id` в content.js),
# поэтому статический guard локалей его не видит: там ловятся только
# литеральные `t("...")`. Связь «id с сервера ↔ ключ в каталоге» держит этот
# тест — новый бейдж без перевода уронит прогон, а не покажет русскую подпись
# в английском интерфейсе.
# ---------------------------------------------------------------------------


def test_every_badge_id_has_a_ui_catalog_key() -> None:
    """Для каждого id бейджа есть ключ `progress.badge_<id>` в обеих локалях."""
    import json

    from stepik_grader.core import insights

    # issue #823: расчёт бейджей переехал из web-адаптера в core — их видит и
    # экспорт прогресса, а не только браузер.
    badges = insights.achievement_badges(ac_cases=99, solved_tasks=99, streak=99)
    badge_ids = [b["id"] for b in badges]
    assert badge_ids, "список бейджей пуст — тест потерял предмет проверки"

    ui_json = (
        pathlib.Path(__file__).parent.parent
        / "src"
        / "stepik_grader"
        / "web"
        / "static"
        / "locales"
        / "ui.json"
    )
    catalog = json.loads(ui_json.read_text(encoding="utf-8"))
    for lang in ("ru", "en"):
        missing = [bid for bid in badge_ids if f"progress.badge_{bid}" not in catalog[lang]]
        assert not missing, f"{lang}.: нет ключей для бейджей {missing}"
