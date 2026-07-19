"""test_journeys.py -- Playwright smoke tests for the 4 core web UI journeys
(issue #263, see docs/web-current.md J0-J7): mode 2 (folder grading), mode 1
(single-file picker + editable window), glossary search, command palette.

Not part of the default ``pytest``/``pytest tests/`` sweep -- see
``tests/e2e/conftest.py`` and ``norecursedirs`` in ``pyproject.toml``. Run
explicitly: ``pytest tests/e2e/`` (after ``pip install -e ".[e2e]"`` +
``playwright install chromium``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.sync_api import expect

from tests.e2e._helpers import write_task

_TIMEOUT_MS = 10_000


def test_mode2_folder_grading_shows_table_and_detail_tab(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """J: режим 2 -- грейдинг папки, таблица результатов, вкладка «Детали»."""
    write_task(tmp_path, "print(int(input()) + 1)\n")  # 4 -> 5, matches expected

    page.goto(e2e_server + "/")
    # Mode 2 ("Папка") is the default, but click it explicitly for robustness
    # against a stale localStorage default from a previous test run.
    page.click('.mode-btn[data-mode="tests"]')
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    row_badge = page.locator("#out table.data-table tbody tr").first.locator(".badge")
    row_badge.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert row_badge.text_content().strip() == "OK"

    # Expand the row, then open the case's detail tab.
    page.click('td.file-cell[data-toggle="0"]')
    page.click('tr.case-row[data-row="0"][data-case="0"]')

    page.wait_for_selector("#restab-detail:not([hidden])", timeout=_TIMEOUT_MS)
    detail = page.locator("#detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    detail_text = detail.text_content()
    assert "AC" in detail_text
    assert "5" in detail_text  # the printed/actual output


def test_mode2_result_announced_and_focused_for_a11y(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """J (issue #298): результат озвучивается через aria-live, вердикт несёт
    ТЕКСТ (не только цвет), фокус уходит на панель результатов."""
    write_task(tmp_path, "print(int(input()) + 1)\n")

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="tests"]')
    page.fill("#path", str(tmp_path))
    page.click("#run")

    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)

    # aria-live summary region carries a one-line verdict summary.
    announce = page.locator("#result-announce")
    assert announce.get_attribute("aria-live") == "polite"
    # issue #563: CSP (default-src 'self', без 'unsafe-eval') запрещает in-page
    # eval, а Playwright'овский wait_for_function(<строка>) поллит именно через
    # eval → падает. web-first expect() ждёт по протоколу, без eval.
    expect(announce).to_contain_text("OK", timeout=_TIMEOUT_MS)
    assert "OK" in announce.text_content()

    # Verdict badge conveys meaning as TEXT, not by colour alone (WCAG 1.4.1).
    row_badge = page.locator("#out table.data-table tbody tr").first.locator(".badge")
    assert row_badge.text_content().strip() == "OK"

    # Focus moved to the results panel so keyboard/SR users land on the summary.
    focused_id = page.evaluate("document.activeElement && document.activeElement.id")
    assert focused_id == "restab-table"


def test_bench_progressbar_exposes_aria_roles(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J (issue #298): прогресс-бар долгого прогона имеет role="progressbar"
    и корректные aria-value* (min/max), видимые assistive tech."""
    write_task(tmp_path, "print(int(input()) + 1)\n")

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="bench"]')
    page.fill("#path", str(tmp_path))
    page.click("#run")

    # Progress bar appears while the async bench job runs.
    pb = page.locator('#bar [role="progressbar"]')
    pb.wait_for(state="attached", timeout=_TIMEOUT_MS)
    assert pb.get_attribute("aria-label")
    assert pb.get_attribute("aria-valuemin") == "0"
    # aria-valuemax reflects total planned run_single_test calls (>= 1).
    assert int(pb.get_attribute("aria-valuemax")) >= 1

    # And the run still completes and announces a summary.
    expect(page.locator("#result-announce")).to_contain_text("авершён", timeout=_TIMEOUT_MS)


def test_check_code_terms_panel_and_mode_visibility(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """J (issue #323/#324): «Функции в коде» в режиме 1 (клик → карточка); скрыта в 3/4."""
    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)

    # режим 1: код в редакторе → мини-карточка sorted → клик открывает карточку
    page.click('.mode-btn[data-mode="file"]')
    page.wait_for_selector("#file-picker-group:not([hidden])", timeout=_TIMEOUT_MS)
    page.click("#solution-editor .cm-content")
    page.keyboard.type("xs = sorted([3, 1, 2])")
    page.wait_for_selector("#check-terms .term-card", timeout=_TIMEOUT_MS)
    titles = page.locator("#check-terms .term-card-title").all_inner_texts()
    assert any("sorted" in t.lower() for t in titles), titles
    page.click("#check-terms .term-card-link")
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)
    page.wait_for_selector("#glossary-detail-content:not([hidden])", timeout=_TIMEOUT_MS)
    assert "sorted" in page.locator("#glossary-detail-content").inner_text().lower()

    # issue #324/#366 (2.з): панель видна только в режиме 1; скрыта в 2/3/4
    page.click('[data-section="check"]')
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    page.click('.mode-btn[data-mode="file"]')
    assert not page.locator("#check-terms-block").is_hidden()
    page.click('.mode-btn[data-mode="tests"]')
    assert page.locator("#check-terms-block").is_hidden()
    page.click('.mode-btn[data-mode="bench"]')
    assert page.locator("#check-terms-block").is_hidden()
    page.click('.mode-btn[data-mode="microbench"]')
    assert page.locator("#check-terms-block").is_hidden()


def test_mode1_file_picker_edit_check_then_save(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: режим 1 (issue #297) -- выбрать файл, отредактировать код, ПРОВЕРИТЬ
    (грейд из временного файла, без записи на диск), затем явно СОХРАНИТЬ."""
    # Deliberately wrong solution on disk -- proves the *edited* code (not the
    # original file) is what actually gets graded, AND that "Проверить" never
    # writes it back (the on-disk content stays wrong until explicit save).
    write_task(tmp_path, "print(int(input()) + 99)\n")

    page.goto(e2e_server + "/")
    page.click('.mode-btn[data-mode="file"]')
    page.fill("#path", str(tmp_path))
    page.click("#find-solutions-btn")

    item = page.locator("#solutions-list li[data-file]", has_text="task.py")
    item.wait_for(state="visible", timeout=_TIMEOUT_MS)
    item.click()

    # issue #265: #solution-editor is a CodeMirror 6 mount (contenteditable
    # .cm-content inside it), not a <textarea> -- no .value/.fill() on the
    # container itself. Content is read via .cm-content's text, and typed
    # via real keyboard events (select-all + type) rather than .fill(),
    # since CodeMirror reconciles its state off real DOM/input events, not
    # a bare textContent assignment. Waiting on "textContent.length > 0" is
    # WRONG here (found live, not just theorized): CodeMirror's placeholder
    # renders as a real .cm-placeholder span *inside* .cm-content when the
    # doc is empty, so length > 0 is already true before the real code
    # loads -- wait for the actual expected substring instead.
    editor_content = page.locator("#solution-editor .cm-content")
    expect(editor_content).to_contain_text("99", timeout=_TIMEOUT_MS)
    assert "99" in editor_content.text_content()  # the original (wrong) code loaded

    # Freshly loaded file -> no unsaved changes yet.
    assert page.locator("#editor-dirty").is_hidden()

    editor_content.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("print(int(input()) + 1)\n")  # correct code, in the editable window

    # issue #297: editing shows the unsaved-changes indicator.
    page.wait_for_selector("#editor-dirty:not([hidden])", timeout=_TIMEOUT_MS)

    # "Проверить" grades the edited code without writing it to disk.
    page.click("#run")
    page.wait_for_selector("#out table.data-table", timeout=_TIMEOUT_MS)
    row_badge = page.locator("#out table.data-table tbody tr").first.locator(".badge")
    row_badge.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert row_badge.text_content().strip() == "OK"

    # AC1: the on-disk file is STILL the original wrong code -- grading ran from
    # a temp file, and the indicator still reports unsaved changes.
    assert (tmp_path / "task.py").read_text(encoding="utf-8") == "print(int(input()) + 99)\n"
    assert page.locator("#editor-dirty").is_visible()

    # Explicit "Сохранить" persists the edit to disk and clears the indicator.
    page.click("#save-solution-btn")
    # state="hidden" (NOT a "[hidden]" selector -- Playwright's default wait is
    # for VISIBLE, which a hidden element never satisfies -> false timeout).
    page.locator("#editor-dirty").wait_for(state="hidden", timeout=_TIMEOUT_MS)
    assert (tmp_path / "task.py").read_text(encoding="utf-8") == "print(int(input()) + 1)\n"


def test_glossary_search_and_open_card(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: глоссарий -- поиск по запросу, открытие карточки."""
    page.goto(e2e_server + "/")
    page.click('[data-section="glossary"]')
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)

    page.fill("#glossary-search", "Key")
    card = page.locator("#glossary-cards li[data-id]", has_text="KeyError")
    card.wait_for(state="visible", timeout=_TIMEOUT_MS)
    card.click()

    detail = page.locator("#glossary-detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert "KeyError" in detail.locator("h2").text_content()
    assert page.locator("#glossary-empty").is_hidden()


def test_glossary_chip_filter_and_deeplink(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: глоссарий (issue #329) -- чип-фильтр раздела + deep-link #/glossary/<id>."""
    page.goto(e2e_server + "/")
    page.click('[data-section="glossary"]')
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)

    # Чипы отрисованы после загрузки карточек; клик по «Исключения» сужает выборку
    # и синхронизирует селект раздела (разделы не объединяются).
    page.wait_for_selector("#glossary-chips .chip", timeout=_TIMEOUT_MS)
    page.click('#glossary-chips .chip[data-section="Исключения"]')
    expect(page.locator("#glossary-section")).to_have_value("Исключения", timeout=_TIMEOUT_MS)
    assert page.locator("#glossary-count").text_content().startswith("Показано")

    # Deep-link: прямой хэш открывает конкретную карточку.
    page.goto(e2e_server + "/#/glossary/keyerror")
    detail = page.locator("#glossary-detail-content")
    detail.wait_for(state="visible", timeout=_TIMEOUT_MS)
    assert "KeyError" in detail.locator("h2").text_content()


def test_sandbox_runs_code_with_stdin(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: песочница (issue #317) -- код + stdin → вывод программы."""
    page.goto(e2e_server + "/")
    page.click('[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)

    page.click("#sandbox-editor .cm-content")
    page.keyboard.type("print(input().upper())")
    page.fill("#sandbox-stdin", "hello")
    page.click("#sandbox-run")

    page.wait_for_selector("#sandbox-output pre.code-block", timeout=_TIMEOUT_MS)
    assert "HELLO" in page.locator("#sandbox-output").inner_text()
    assert page.locator("#sandbox-status").text_content() == "Успешно"


def _type_sandbox_code(page: Any, code: str) -> None:
    """Ввести многострочный код в редактор песочницы, снимая автоотступ CodeMirror.

    После двоеточия CodeMirror сам добавляет отступ на новой строке; чистим
    строку (Home → Shift+End → Delete) и печатаем её с нуля, чтобы трейс получил
    ровно ``code``.
    """
    page.click("#sandbox-editor .cm-content")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    for i, line in enumerate(code.split("\n")):
        if i:
            page.keyboard.press("Enter")
            page.keyboard.press("Home")
            page.keyboard.press("Shift+End")
            page.keyboard.press("Delete")
        page.keyboard.type(line)


def _sandbox_var_value(page: Any, name: str) -> str | None:
    """Значение переменной ``name`` из панели кадров текущего шага (или None)."""
    for row in page.locator(".trace-vars tr").all():
        cells = row.locator("td")
        if cells.count() == 2 and cells.nth(0).inner_text() == name:
            return cells.nth(1).inner_text()
    return None


def test_sandbox_step_player_loop_and_keyboard(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J (issue #319): плеер листает цикл — ``i`` меняется, вывод растёт, ← → работают."""
    page.goto(e2e_server + "/")
    page.click('[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)

    _type_sandbox_code(page, "for i in range(3):\n    print(i)")
    page.click("#sandbox-step")
    page.wait_for_selector("#trace-code", timeout=_TIMEOUT_MS)

    label = page.locator("#trace-step-label").text_content()
    assert "шаг 1 из" in label, label
    total = int(label.split("из")[1])

    seen_i: set[str] = set()
    outputs: list[str] = []
    for _ in range(total):
        val = _sandbox_var_value(page, "i")
        if val is not None:
            seen_i.add(val)
        outputs.append(page.locator("#trace-stdout").inner_text())
        nxt = page.locator('[data-trace="next"]')
        if nxt.is_disabled():
            break
        nxt.click()

    assert {"0", "1", "2"} <= seen_i, seen_i  # i прошёл 0,1,2
    assert outputs[-1].strip() == "0\n1\n2"  # вывод дорос до полного
    assert len(outputs[-1]) >= len(outputs[0])  # монотонный рост
    assert page.locator(".trace-line.is-active, .trace-line.is-error").count() >= 1

    # навигация клавиатурой ← → (фокус уводим на снимок кода, не в редактор)
    page.locator("#trace-code").click()
    page.locator('[data-trace="first"]').click()
    before = page.locator("#trace-step-label").text_content()
    page.keyboard.press("ArrowRight")
    assert page.locator("#trace-step-label").text_content() != before
    mid = page.locator("#trace-step-label").text_content()
    page.keyboard.press("ArrowLeft")
    assert page.locator("#trace-step-label").text_content() != mid


def test_sandbox_step_player_function_frame_appears(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """J (issue #319): вызов функции — в стеке появляется кадр ``f()`` с параметром ``n``."""
    page.goto(e2e_server + "/")
    page.click('[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)

    _type_sandbox_code(page, "def f(n):\n    return n + 1\nx = f(5)")
    page.click("#sandbox-step")
    page.wait_for_selector("#trace-code", timeout=_TIMEOUT_MS)

    total = int(page.locator("#trace-step-label").text_content().split("из")[1])
    saw_frame_with_n = False
    for _ in range(total):
        titles = page.locator(".trace-frame-title").all_inner_texts()
        if any("f()" in t for t in titles) and _sandbox_var_value(page, "n") == "5":
            saw_frame_with_n = True
            break
        nxt = page.locator('[data-trace="next"]')
        if nxt.is_disabled():
            break
        nxt.click()
    assert saw_frame_with_n, "кадр f() с параметром n=5 не появился"


def test_sandbox_diagram_shows_aliasing_and_nesting(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """J (issue #320): диаграмма связей — aliasing (две стрелки в один узел) и вложенность."""
    page.goto(e2e_server + "/")
    page.click('[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)

    # aliasing: a и b ссылаются на один список → один узел, две стрелки в него
    _type_sandbox_code(page, "a = [1, 2]\nb = a")
    page.click("#sandbox-step")
    page.wait_for_selector("#trace-code", timeout=_TIMEOUT_MS)
    page.locator('[data-trace="last"]').click()  # финал: a и b заданы
    page.click('[data-traceview="diagram"]')
    page.wait_for_selector("#mem-cols", timeout=_TIMEOUT_MS)

    assert page.locator(".mem-node").count() == 1  # один список — один узел
    anchors = page.locator(".mem-frames-col .mem-anchor").all()
    targets = [a.get_attribute("data-to") for a in anchors]
    assert len(targets) == 2 and targets[0] == targets[1]  # a и b → один heap-id
    assert page.locator("path.mem-arrow").count() >= 2  # две стрелки в узел

    # вложенность: m = {"x": [1]} → два узла (dict + list), стрелка узел→узел
    _type_sandbox_code(page, 'm = {"x": [1]}')
    page.click("#sandbox-step")
    page.wait_for_selector("#trace-code", timeout=_TIMEOUT_MS)
    page.locator('[data-trace="last"]').click()
    page.click('[data-traceview="diagram"]')
    page.wait_for_selector("#mem-cols", timeout=_TIMEOUT_MS)
    assert page.locator(".mem-node").count() == 2  # dict + вложенный list
    assert page.locator(".mem-heap-col .mem-anchor").count() >= 1  # ссылка из dict на list

    # переключение обратно в таблицу работает
    page.click('[data-traceview="table"]')
    page.wait_for_selector(".trace-vars", timeout=_TIMEOUT_MS)


def test_sandbox_code_terms_and_error_card(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J (issue #321): «Функции в коде» (sorted → карточка) и error card при RE."""
    page.goto(e2e_server + "/")
    page.click('[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)

    # мини-карточка sorted появляется по мере ввода кода; клик открывает глоссарий
    page.click("#sandbox-editor .cm-content")
    page.keyboard.type("xs = sorted([3, 1, 2])")
    page.wait_for_selector("#sandbox-terms .term-card", timeout=_TIMEOUT_MS)
    titles = page.locator("#sandbox-terms .term-card-title").all_inner_texts()
    assert any("sorted" in t.lower() for t in titles), titles
    page.click("#sandbox-terms .term-card-link")
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)
    page.wait_for_selector("#glossary-detail-content:not([hidden])", timeout=_TIMEOUT_MS)
    assert "sorted" in page.locator("#glossary-detail-content").inner_text().lower()

    # RE → error card ZeroDivisionError с рабочим deep-link в глоссарий
    page.click('[data-section="sandbox"]')
    page.wait_for_selector("#view-sandbox:not([hidden])", timeout=_TIMEOUT_MS)
    page.click("#sandbox-editor .cm-content")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.keyboard.type("1 / 0")
    page.click("#sandbox-run")
    page.wait_for_selector(".sandbox-errcard", timeout=_TIMEOUT_MS)
    assert "ZeroDivisionError" in page.locator(".sandbox-errcard").inner_text()
    link = page.locator(".sandbox-errcard a.trace-error-link")
    assert link.get_attribute("href") == "#/glossary/zerodivisionerror"
    link.click()
    page.wait_for_selector("#view-glossary:not([hidden])", timeout=_TIMEOUT_MS)
    page.wait_for_selector("#glossary-detail-content:not([hidden])", timeout=_TIMEOUT_MS)
    assert "zerodivision" in page.locator("#glossary-detail-content").inner_text().lower()


def test_switch_section_cycles_all_sections(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J (issue #428): команда «Переключить раздел» циклит по ВСЕМ 8 разделам
    через единый реестр SECTIONS (раньше — жёсткий 4-элементный список
    check/downloader/glossary/sandbox, терявший rules/insights/settings —
    рецидив #317, находка аудита #331; «Прогресс» добавлен в #538)."""
    sections = [
        "check",
        "downloader",
        "glossary",
        "rules",
        "insights",
        "progress",
        "sandbox",
        "settings",
    ]

    def visible() -> str:
        return next(s for s in sections if not page.locator(f"#view-{s}").is_hidden())

    def cycle() -> None:
        page.keyboard.press("Control+k")
        page.wait_for_selector("#palette-overlay:not([hidden])", timeout=_TIMEOUT_MS)
        page.fill("#palette-input", "Переключить раздел")
        page.locator("#palette-list li", has_text="Переключить раздел").first.wait_for(
            state="visible", timeout=_TIMEOUT_MS
        )
        page.keyboard.press("Enter")
        page.wait_for_selector("#palette-overlay[hidden]", state="attached", timeout=_TIMEOUT_MS)

    page.goto(e2e_server + "/")
    page.wait_for_selector("#view-check:not([hidden])", timeout=_TIMEOUT_MS)
    seq = [visible()]
    for _ in range(len(sections)):
        cycle()
        seq.append(visible())
    assert seq == [*sections, "check"], seq


def test_command_palette_opens_and_executes(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """J: command palette -- Ctrl+K/триггер открывает палитру, команда исполняется."""
    page.goto(e2e_server + "/")

    # Entry point 1: the palette trigger button.
    page.click("#palette-btn")
    page.wait_for_selector("#palette-overlay:not([hidden])", timeout=_TIMEOUT_MS)
    page.keyboard.press("Escape")
    # state="attached" (not the default "visible") -- a [hidden] element is
    # by definition never "visible", so waiting for visibility here would
    # never resolve; we only need the attribute to be present.
    page.wait_for_selector("#palette-overlay[hidden]", state="attached", timeout=_TIMEOUT_MS)

    # Entry point 2: Ctrl+K -- filter to "toggle theme" and execute it.
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette-overlay:not([hidden])", timeout=_TIMEOUT_MS)
    page.fill("#palette-input", "тема")
    page.locator("#palette-list li", has_text="Переключить тему").wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    page.keyboard.press("Enter")

    page.wait_for_selector("#palette-overlay[hidden]", state="attached", timeout=_TIMEOUT_MS)
    theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert theme == "light"  # system -> light (cycleTheme's first step)
    assert page.locator("#theme-toggle").text_content() == "☀️"


def test_progress_section_opens_and_renders(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """issue #538: раздел «Прогресс» открывается и рендерит один из двух исходов —
    KPI-контент (непустая история) ИЛИ дружелюбный empty-state, — устойчиво к
    состоянию истории в cwd сервера. Наполнение KPI из данных покрыто unit-тестом
    ``test_progress_report_adapter_aggregates_history``."""
    page.goto(e2e_server + "/")
    page.click('.sidebar-item[data-section="progress"]')
    page.wait_for_selector("#view-progress:not([hidden])", timeout=_TIMEOUT_MS)
    # /api/progress ответил и renderProgress отработал: виден либо empty-state,
    # либо KPI-контент (если оба скрыты — loadProgress упал, ловим таймаутом).
    page.wait_for_selector(
        "#progress-empty:not([hidden]), #progress-content:not([hidden])",
        timeout=_TIMEOUT_MS,
    )


def test_exec_mode_badge_visible_without_sandbox(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """issue #565: e2e-сервер запущен без --sandbox → бейдж «Без OS-изоляции» виден
    (честная видимость режима исполнения недоверенного кода)."""
    page.goto(e2e_server + "/")
    badge = page.locator("#exec-mode-badge")
    expect(badge).to_be_visible(timeout=_TIMEOUT_MS)
    expect(badge).to_contain_text("Без OS-изоляции")


def test_history_notice_shown_once(page: Any, e2e_server: str, tmp_path: Path) -> None:
    """issue #565: уведомление о локальном сборе истории показывается один раз —
    после «Понятно» флаг ложится в localStorage, повторный визит его не показывает."""
    page.goto(e2e_server + "/")
    notice = page.locator("#history-notice")
    expect(notice).to_be_visible(timeout=_TIMEOUT_MS)
    page.click("#history-notice-dismiss")
    expect(notice).to_be_hidden()
    page.reload()
    expect(page.locator("#history-notice")).to_be_hidden()


def test_settings_english_localizes_static_shell(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """issue #545: выбор English в «Настройках» локализует СТАТИЧЕСКУЮ оболочку
    (data-i18n) без перезагрузки — сайдбар и <html lang> становятся английскими.

    Под строгим CSP (default-src 'self', без 'unsafe-eval', issue #563)
    используем только web-first ``expect(...)`` — оно ждёт по протоколу, без
    in-page eval (``page.wait_for_function`` со строкой падал бы EvalError)."""
    page.goto(e2e_server + "/")
    # Открываем «Настройки» — селект языка внутри скрытого раздела, для
    # select_option нужен видимый элемент.
    page.click('.sidebar-item[data-section="settings"]')
    page.wait_for_selector("#view-settings:not([hidden])", timeout=_TIMEOUT_MS)

    page.select_option("#settings-lang", "en")

    # Chrome оболочки сменил язык: пункт сайдбара и атрибут <html lang>.
    expect(page.locator('.sidebar-item[data-section="check"]')).to_have_text(
        "Check solutions", timeout=_TIMEOUT_MS
    )
    expect(page.locator("html")).to_have_attribute("lang", "en", timeout=_TIMEOUT_MS)
