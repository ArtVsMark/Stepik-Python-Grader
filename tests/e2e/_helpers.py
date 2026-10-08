"""_helpers.py -- shared solution/test-case fixtures for tests/e2e/ (issue #263).

Not a test module itself (no ``test_`` prefix, so pytest never collects it).

Здесь же — чистые помощники guard'а «набор не скипнулся молча» (issue #921,
находка `QA-2-02`). Хуки pytest обязаны жить в ``conftest.py``, а вот решение,
которое они принимают, — нет: вынесенное сюда, оно проверяется обычным тестом
из основного набора, а не только в job'е с браузером.
"""

import json
from collections.abc import Iterable
from pathlib import Path

__all__ = [
    "GUARD_FILE",
    "REQUIRE_E2E_ENV",
    "executed_beyond_guards",
    "ui_text",
    "uncovered_call",
    "write_task",
]

REQUIRE_E2E_ENV = "STEPIK_REQUIRE_E2E_TESTS"
"""Переменная, включающая жёсткий режим: пропуск набора становится отказом."""

GUARD_FILE = "test_not_silently_skipped.py"
"""Файл самих guard'ов — они не считаются полезной работой набора."""


def executed_beyond_guards(nodeids: Iterable[str]) -> set[str]:
    """Выполненные e2e-тесты за вычетом самих guard'ов.

    Guard'ы выполняются всегда — они не трогают ни браузер, ни сервер. Считать
    их значило бы объявлять набор живым ровно тогда, когда живы только сторожа.
    """
    return {nodeid for nodeid in nodeids if GUARD_FILE not in nodeid}


def write_task(
    folder: Path,
    code: str,
    *,
    stdin: str = "4",
    expected: str = "5",
    filename: str = "task.py",
) -> Path:
    """Write a solution file + one legacy-format (``N``/``N.clue``) test case.

    Mirrors ``tests/test_web.py``'s ``_make_task`` helper -- one stdin/expected
    pair is enough to drive a real browser through the grading UI end to end.
    """
    sol = folder / filename
    sol.write_text(code, encoding="utf-8")
    tests_dir = folder / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "1").write_text(stdin, encoding="utf-8")
    (tests_dir / "1.clue").write_text(expected, encoding="utf-8")
    return sol


_UI_LOCALES = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "stepik_grader"
    / "web"
    / "static"
    / "locales"
    / "ui.json"
)


def ui_text(key: str, lang: str = "ru") -> str:
    """Строка интерфейса из ``static/locales/ui.json`` — та же, что видит браузер.

    Язык по умолчанию русский, и это не предпочтение автора, а свойство стенда
    (issue #1004, находка `QA-2-05`). Начальный язык фронтенд берёт как
    ``localStorage.grader_lang || document.body.dataset.startLang || "ru"``;
    ``navigator.language`` не участвует нигде. У свежего контекста Playwright
    ``localStorage`` пуст, а ``data-start-lang`` подставляет сервер из своего
    ``lang``, который фикстура ``e2e_server`` не передаёт вовсе — остаётся
    умолчание ``ru``. Значит ассерт «слово в любой из двух локалей» страховал
    от несуществующего риска и заодно принимал английский текст как верный.

    Ключ берётся из файла, а не переписывается в тест: сверять текст с копией
    его же самого — значит проверять, что копия не разошлась, а не что
    интерфейс показывает нужное сообщение.
    """
    catalogue = json.loads(_UI_LOCALES.read_text(encoding="utf-8"))
    return str(catalogue[lang][key])


#: Вызовы stdlib, которые детектор узнаёт. Какой из них сейчас без карточки,
#: решает база, а не этот список: глоссарий пополняется у издателя, и зашитое
#: имя однажды получает карточку — так `cmath.polar` уронил три сценария при
#: переходе на выгрузку Glossary-Python v1.4.0.
_UNCOVERED_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("turtle", "forward"),
    ("curses", "wrapper"),
    ("colorsys", "rgb_to_hsv"),
    ("mailbox", "mbox"),
    ("pydoc", "render_doc"),
    ("webbrowser", "open_new_tab"),
)


def uncovered_call() -> tuple[str, str, str]:
    """Импорт, вызов и имя концепта, у которого в комплектной базе НЕТ карточки.

    Спрашивает тот же детектор, что отвечает панели «Функции в коде», поэтому
    ответ совпадает с тем, что покажет страница. Кандидатов не осталось —
    отказ словами: значит, база догнала список, и его пора пополнить.
    """
    from stepik_grader.web.glossary_adapter import code_terms

    for module, name in _UNCOVERED_CANDIDATES:
        concept = f"{module}.{name}"
        imp, call = f"import {module}\n", f"{concept}(1)"
        terms = [(t["id"], t["has_card"]) for t in code_terms(imp + call)]
        if terms == [(concept, False)]:
            return imp, call, concept
    raise AssertionError(
        "у всех кандидатов _UNCOVERED_CANDIDATES появились карточки — добавьте "
        "в список вызов, которого глоссарий ещё не описывает"
    )
