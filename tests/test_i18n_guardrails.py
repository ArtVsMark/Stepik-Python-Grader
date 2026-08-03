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


# ---------------------------------------------------------------------------
# issue #836 (QA-06) — обе graceful-ветки render_message. Контракт «API никогда
# не падает из-за опечатки в message_id/params» держался на честном слове:
# guard локалей сверяет только паритет ключей и рантайм-откат не видит.
# ---------------------------------------------------------------------------


def test_render_message_falls_back_to_default_locale(monkeypatch) -> None:
    """Ключа нет в запрошенной локали → берётся русский текст, а не сам id."""
    from stepik_grader.core import i18n as core_i18n
    from stepik_grader.web import i18n as web_i18n

    real = core_i18n.load_locale_messages

    def only_in_ru(lang: str) -> dict[str, str]:
        messages = dict(real(lang))
        if lang == web_i18n.DEFAULT_LANG:
            messages["_probe_key"] = "только по-русски"
        return messages

    monkeypatch.setattr(web_i18n, "load_locale_messages", only_in_ru)
    assert web_i18n.render_message("_probe_key", "en") == "только по-русски"


def test_render_message_returns_id_when_missing_everywhere(monkeypatch) -> None:
    """Опечатка в message_id не роняет ответ API — возвращается сам ключ."""
    from stepik_grader.web import i18n as web_i18n

    assert web_i18n.render_message("нет-такого-ключа", "en") == "нет-такого-ключа"


def test_render_message_survives_placeholder_mismatch(monkeypatch) -> None:
    """params не совпали с плейсхолдерами → нерендеренный шаблон, не исключение."""
    from stepik_grader.web import i18n as web_i18n

    monkeypatch.setattr(
        web_i18n, "load_locale_messages", lambda lang: {"_probe_key": "нужен {path}"}
    )
    assert web_i18n.render_message("_probe_key", "ru", wrong="значение") == "нужен {path}"


# ---------------------------------------------------------------------------
# issue #824 (DESC-04) — CLI-таблицы отчётов тоже обязаны переводиться. Репро из
# аудита: `stepik-grader --lang en`, пункт меню 5 «Learn» — меню и подсказки на
# английском, а таблица под ними приходит с заголовком «Подучить» и колонками
# «Ключ/Статус/Замечен». Половина отчёта не переводилась вовсе, при этом
# docstring print_stats_summary декларировал обратное.
# ---------------------------------------------------------------------------

_CYRILLIC = tuple("абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")


def _has_cyrillic(text: str) -> bool:
    return any(ch in _CYRILLIC for ch in text)


class _Card:
    def __init__(self, key: str, status: str) -> None:
        self.key, self.status = key, status
        self.hits, self.runs_considered = 3, 10
        self.category, self.glossary_id = "runtime-error", ""


class _Progress:
    def __init__(self) -> None:
        self.task_key, self.solved, self.attempts = "", True, 4
        self.seconds_to_first_ac = 3600.0


class _Violation:
    def __init__(self) -> None:
        self.rule_code, self.line_no = "E501", 7


@pytest.fixture
def _english(monkeypatch: pytest.MonkeyPatch):
    """Переключить CLI на английский так же, как это делает ``--lang en``."""
    from stepik_grader import cli

    monkeypatch.setattr(cli, "_LANG", "en")
    return cli


def test_insights_table_is_translated(_english, capsys, monkeypatch) -> None:
    """Заголовок, колонки И статусы карточек — всё на языке интерфейса."""
    from stepik_grader.core import reporter

    monkeypatch.setattr(reporter, "_RICH", False)
    reporter.print_insights_summary(
        [_Card("wrong-answer", "active"), _Card("timeout", "fading")],
        labels=_english._insights_labels(),
    )
    out = capsys.readouterr().out
    assert not _has_cyrillic(out), f"кириллица в английском выводе: {out!r}"
    assert "active" in out and "fading" in out


def test_progress_table_is_translated(_english, capsys, monkeypatch) -> None:
    """Включая «(без задачи)» и единицу длительности — «4ч» ломало строку не меньше."""
    from stepik_grader.core import reporter

    monkeypatch.setattr(reporter, "_RICH", False)
    reporter.print_progress_summary([_Progress()], labels=_english._progress_labels())
    out = capsys.readouterr().out
    assert not _has_cyrillic(out), f"кириллица в английском выводе: {out!r}"


def test_lint_block_is_translated(_english, capsys, monkeypatch) -> None:
    from stepik_grader.core import reporter

    monkeypatch.setattr(reporter, "_RICH", False)
    reporter.print_lint_block([_Violation()], labels=_english._lint_labels())
    out = capsys.readouterr().out
    assert not _has_cyrillic(out), f"кириллица в английском выводе: {out!r}"


def test_russian_stays_default_without_labels() -> None:
    """Прямой вызов без ``labels`` печатает по-русски — обратная совместимость.

    Обёртки в ``__all__`` вызываются и из тестов, и из стороннего кода; смена
    языка по умолчанию была бы тихой поломкой их вывода.
    """
    from stepik_grader.core import reporter

    assert reporter._INSIGHTS_LABELS["title"] == "Подучить"
    assert reporter._PROGRESS_LABELS["no_task"] == "(без задачи)"


def test_cli_label_builders_cover_every_reporter_default() -> None:
    """Сборщик подписей не должен отставать от reporter'а при новой колонке.

    Иначе таблица тихо съедет обратно на русский дефолт по забытому ключу — тот
    самый сценарий, из-за которого DESC-04 и появился.
    """
    from stepik_grader import cli
    from stepik_grader.core import reporter

    for builder, defaults in (
        (cli._insights_labels, reporter._INSIGHTS_LABELS),
        (cli._progress_labels, reporter._PROGRESS_LABELS),
        (cli._lint_labels, reporter._LINT_LABELS),
    ):
        assert set(builder()) == set(defaults), builder.__name__
