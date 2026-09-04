"""Экранирование в веб-выводе: payload не исполняется ни на одном стоке (issue #263).

`app.js` рисует через `innerHTML` около сорока мест, и часть из них показывает
текст, которым управляет проверяемый — вывод решения, имена файлов и путей.
Безопасность этих мест держалась код-ревью; здесь она держится прогоном в
настоящем браузере: решение печатает payload, интерфейс его рисует, а тест
спрашивает, не стал ли он живым элементом DOM.

Стоков в наборе три, и это не перечисление ради полноты (issue #1004, находка
`SEC-1-06`). Первая редакция закрывала один — вывод решения, — и молчаливо
предполагала, что остальные защищены тем же `esc()`. Предположение проверяемо,
и проверять его надо: `esc()` зовётся в каждом месте руками, а забытый вызов
выглядит ровно как соблюдённый.

Второй сток — **имя каталога**. Имя файла решения ограничено шаблоном
`task*.py`, а имя папки не ограничено ничем: путь попадает в колонку «Файл»
таблицы результатов как есть. Проверено пробой, а не рассуждением.

Третий — **путь, введённый пользователем**: он возвращается в сообщении об
ошибке, то есть проходит через другую ветку рендера, чем таблица.

Файл написан по-русски (issue #1004, находка `SEC-1-07`): инвариант § Язык
артефактов действует и на тесты, а прежняя английская редакция была здесь
единственной. Комментарии в тестах периметра читают в момент разбора инцидента,
и язык там значит больше обычного.

**Граница зелёного названа здесь, а не подразумевается.** Прогон отвечает
«на этих трёх стоках безопасно», и не более того. Утверждения «во всех сорока
местах `innerHTML` вызван `esc()`» не держит ни один механизм: сама функция
покрыта модульными тестами (`tests/test_web.py`, issue #214), а сверки вызовов
по всему слою нет. Это открытая слепая зона, а не то, что закрыто чтением.

Не входит в обычный прогон `pytest tests/` — см. `tests/e2e/conftest.py` и
`norecursedirs` в `pyproject.toml`. Запуск: `pytest tests/e2e/` после
`pip install -e ".[e2e]"` и `playwright install chromium`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 10_000
_PAYLOAD = "<img src=x onerror=window.__xss=1>"


def _assert_not_live(page: Any, area: Any) -> None:
    """Payload виден как текст и не стал элементом DOM.

    Четыре утверждения, и каждое закрывает свой способ ошибиться: обработчик не
    сработал, живого тега нет, в разметке стоят сущности, а видимый текст всё
    же показывает payload — экранирование не спрятало его, а показало безопасно.
    """
    assert page.evaluate("window.__xss") is None, "обработчик onerror сработал"
    assert area.locator("img").count() == 0, "payload стал живым элементом"

    markup = area.inner_html()
    assert "&lt;img" in markup, "разметка не экранирована"
    assert "<img src=x" not in markup, "разметка вставлена как живой тег"
    assert _PAYLOAD in area.text_content(), "payload не показан пользователю вовсе"


def test_payload_in_solution_output_is_escaped(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """Сток первый: вывод решения.

    Вывод решения и есть payload, а ожидание совпадает с ним дословно — значит
    вердикт AC, и поле «Вывод» на вкладке разбора рисует его через
    `codeBlock()`/`esc()` (`_case_view` заполняет `actual` при любом вердикте).
    """
    write_task(tmp_path, f"print({_PAYLOAD!r})\n", stdin="", expected=_PAYLOAD)

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="tests"]')
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    page.click('td.file-cell[data-toggle="0"]')
    page.click('tr.case-row[data-row="0"][data-case="0"]')
    page.wait_for_selector("#restab-detail:not([hidden])", timeout=_TIMEOUT_MS)

    detail = page.locator("#detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    # Ждём именно блок кода с payload, а не саму панель разбора: issue #563 —
    # CSP запрещает eval на странице, поэтому web-first `expect`, а не
    # `wait_for_function`.
    expect(detail).to_contain_text("onerror", timeout=_TIMEOUT_MS)

    _assert_not_live(page, detail)


def test_payload_in_a_directory_name_is_escaped(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """Сток второй: имя каталога в колонке «Файл».

    Имя файла решения ограничено шаблоном `task*.py` и payload'ом быть не может,
    а имя папки не ограничено ничем — путь доезжает до таблицы как есть. Это
    другая ветка рендера, чем вкладка разбора выше, и своего `esc()` там столько
    же поводов забыть.
    """
    folder = tmp_path / _PAYLOAD
    folder.mkdir()
    write_task(folder, "print(4)\n", stdin="4", expected="4")

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="tests"]')
    page.fill("#path", str(tmp_path))
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    table = page.locator("#out table.data-table").first
    expect(table).to_contain_text("onerror", timeout=_TIMEOUT_MS)

    _assert_not_live(page, table)


def test_payload_in_a_user_typed_path_is_escaped(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Сток третий: путь, введённый пользователем, в сообщении об ошибке.

    Несуществующий путь возвращается человеку в тексте ошибки — то есть payload
    проходит ветку рендера сообщений, а не таблицы. Ветка отдельная, и защищена
    она отдельным вызовом `esc()`.
    """
    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="tests"]')
    page.fill("#path", str(tmp_path / _PAYLOAD))
    page.click("#run")

    out = page.locator("#out")
    expect(out).to_contain_text("onerror", timeout=_TIMEOUT_MS)

    _assert_not_live(page, out)
