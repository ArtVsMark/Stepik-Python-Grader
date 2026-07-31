"""Tests for scripts/check_ui_locale_guardrails.py — web-UI locale guardrails
(issue #547).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_locale_guardrails.py. Guard-the-guard: на реальном
репо зелёный, а синтетический недостающий ключ / голый литерал / рассинхрон
ru↔en делают его красным.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_ui_locale_guardrails.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_ui_locale_guardrails", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _setup(
    module: ModuleType,
    monkeypatch,
    tmp_path: Path,
    *,
    catalog: dict,
    html: str = "<!doctype html><html><body></body></html>",
    js: dict[str, str] | None = None,
) -> None:
    """Собрать временную static-раскладку и перенаправить пути модуля на неё."""
    static = tmp_path / "static"
    (static / "locales").mkdir(parents=True)
    (static / "locales" / "ui.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )
    (static / "index.html").write_text(html, encoding="utf-8")
    js = js or {}
    for name, content in js.items():
        (static / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(module, "_STATIC", static)
    monkeypatch.setattr(module, "_UI_JSON", static / "locales" / "ui.json")
    monkeypatch.setattr(module, "_INDEX_HTML", static / "index.html")
    # issue #787: список JS больше не константа — он собирается из каталога,
    # поэтому подменять нечего: достаточно того, что файлы легли в `static`.


# -- Guard-the-guard: реальный репозиторий зелёный ---------------------------


def test_passes_on_current_repo() -> None:
    """На актуальном main нарушений быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_ui_locale_guardrails.py` завершается 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# -- Паритет ru/en -----------------------------------------------------------


def test_key_parity_mismatch_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(module, monkeypatch, tmp_path, catalog={"ru": {"a": "1", "b": "2"}, "en": {"a": "1"}})
    errors: list[str] = []
    module.check_key_parity(errors)
    assert any("b" in e for e in errors), errors


def test_key_parity_match_passes(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(module, monkeypatch, tmp_path, catalog={"ru": {"a": "1"}, "en": {"a": "hi"}})
    errors: list[str] = []
    module.check_key_parity(errors)
    assert errors == []


# -- Покрытие ключей ---------------------------------------------------------


def test_missing_t_key_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": 'const x = t("grade.absent");\n'},
    )
    errors: list[str] = []
    module.check_keys_present(errors)
    assert any("grade.absent" in e for e in errors), errors


def test_missing_data_i18n_key_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        html='<html><body><span data-i18n="nav.absent">x</span></body></html>',
    )
    errors: list[str] = []
    module.check_keys_present(errors)
    assert any("nav.absent" in e for e in errors), errors


def test_missing_plural_form_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    # есть .one/.few, нет .many → нарушение.
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {"p.k.one": "1", "p.k.few": "2"}, "en": {"p.k.one": "1", "p.k.few": "2"}},
        js={"m.js": 'const s = tp(n, "p.k");\n'},
    )
    errors: list[str] = []
    module.check_keys_present(errors)
    assert any("p.k.many" in e for e in errors), errors


def test_all_keys_present_passes(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {"nav.a": "А", "grade.b": "Б"}, "en": {"nav.a": "A", "grade.b": "B"}},
        html='<html><body><span data-i18n="nav.a">А</span></body></html>',
        js={"m.js": 'const x = t("grade.b");\n'},
    )
    errors: list[str] = []
    module.check_keys_present(errors)
    assert errors == []


# -- Жёстко зашитая латиница (issue #670) ------------------------------------


def test_hardcoded_latin_text_node_flagged(monkeypatch, tmp_path: Path) -> None:
    """Английский заголовок в разметке ловится (issue #670).

    Ровно та регрессия, ради которой проверка и добавлена: восемь заголовков
    таблиц (`Passed`/`Avg`/`Runs`/`Min`/`Median`/`Mean`/`Max`/`Std dev`) были
    зашиты по-английски и показывались в русском интерфейсе. Проверка на голую
    кириллицу их не видела — она ищет непереведённый РУССКИЙ.
    """
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": "h += '<th scope=\"col\">Passed</th>';\n"},
    )
    errors: list[str] = []
    module.check_no_hardcoded_latin_text(errors)
    assert any("m.js" in e and "Passed" in e for e in errors), errors


def test_localized_header_is_not_flagged(monkeypatch, tmp_path: Path) -> None:
    """Заголовок через t() нарушением не считается.

    Подпись приходит конкатенацией, поэтому литерал обрывается на `>` и под
    шаблон текстового узла не подпадает — ложных срабатываний на правильном
    коде быть не должно.
    """
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {"g.col": "Пройдено"}, "en": {"g.col": "Passed"}},
        js={"m.js": "h += '<th scope=\"col\">' + esc(t(\"g.col\")) + '</th>';\n"},
    )
    errors: list[str] = []
    module.check_no_hardcoded_latin_text(errors)
    assert errors == []


def test_verdict_codes_are_allowed(monkeypatch, tmp_path: Path) -> None:
    """Коды вердиктов — не подписи интерфейса, их переводить нельзя.

    `AC`/`WA`/`TLE` печатаются одинаково в CLI и web и задокументированы в
    docs/dev/result-contract.md, поэтому живут в белом списке.
    """
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": "h += '<span class=\"badge\">AC</span><span>WA</span>';\n"},
    )
    errors: list[str] = []
    module.check_no_hardcoded_latin_text(errors)
    assert errors == []


# -- Голая кириллица ---------------------------------------------------------


def test_bare_cyrillic_js_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": 'const label = "Проверка";\n'},
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert any("m.js" in e and "Проверка" in e for e in errors), errors


def test_bare_cyrillic_html_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        html="<html><body><p>Голый текст</p></body></html>",
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert any("index.html" in e for e in errors), errors


def test_cyrillic_under_data_i18n_is_ok(monkeypatch, tmp_path: Path) -> None:
    """Кириллица внутри узла с data-i18n — легитимный RU-fallback (#545)."""
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {"x.y": "Текст"}, "en": {"x.y": "Text"}},
        html='<html><body><span data-i18n="x.y">Текст</span></body></html>',
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert errors == []


def test_i18n_exempt_and_console_are_ignored(monkeypatch, tmp_path: Path) -> None:
    """Строки с i18n-exempt и console.warn/error — не нарушение (диагностика/фильтры)."""
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={
            "m.js": (
                'const chip = { section: "Строки (str)" }; // i18n-exempt\n'
                'console.warn("Ключ отсутствует");\n'
            ),
        },
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert errors == [], errors


# ---------------------------------------------------------------------------
# issue #787: полнота входа и точность исключения console.*
# ---------------------------------------------------------------------------


def test_new_js_file_is_checked_without_touching_the_script(monkeypatch, tmp_path: Path) -> None:
    """Новый файл статики проверяется сам собой — список берётся из каталога.

    Регрессия CIG-01: `feedback.js` появился в `static/`, но в захардкоженный
    кортеж его дописать забыли, и он не проверялся ни разу.
    """
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"brand-new-file.js": 'el.textContent = "Ошибка отправки";\n'},
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert any("brand-new-file.js" in e for e in errors), errors


def test_no_js_files_at_all_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Каталог статики переехал → guard падает вместо «всё чисто» на пустом множестве."""
    module = _load_module()
    _setup(module, monkeypatch, tmp_path, catalog={"ru": {}, "en": {}}, js={})

    errors: list[str] = []
    module.check_inputs_present(errors)
    assert any("нет ни одного .js" in e for e in errors), errors


def test_missing_catalog_or_index_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Пропавший ui.json/index.html — тоже нулевой вход, а не успех."""
    module = _load_module()
    _setup(module, monkeypatch, tmp_path, catalog={"ru": {}, "en": {}}, js={"m.js": "\n"})
    monkeypatch.setattr(module, "_UI_JSON", tmp_path / "gone" / "ui.json")
    monkeypatch.setattr(module, "_INDEX_HTML", tmp_path / "gone" / "index.html")

    errors: list[str] = []
    module.check_inputs_present(errors)
    assert len(errors) == 2, errors


def test_console_call_does_not_amnesty_neighbouring_literal(monkeypatch, tmp_path: Path) -> None:
    """Диагностический console.warn не снимает защиту с соседней надписи (CIG-04).

    Прежде исключение действовало на всю физическую строку: `console.warn("x")`
    в том же однострочнике амнистировал видимый текст рядом.
    """
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": 'if (!ok) { console.warn("x"); el.textContent = "Ошибка загрузки"; }\n'},
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert any("Ошибка загрузки" in e for e in errors), errors


def test_console_argument_itself_is_still_exempt(monkeypatch, tmp_path: Path) -> None:
    """Сам аргумент console.warn/error по-прежнему не считается нарушением."""
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": 'console.warn("Ключ отсутствует");\nconsole.error("Сбой сети");\n'},
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert errors == [], errors


def test_regex_literal_with_quote_not_mistaken_for_string(monkeypatch, tmp_path: Path) -> None:
    """`/[&<>"']/g` не должен «открыть» строку и нахватать кириллицу из комментария."""
    module = _load_module()
    _setup(
        module,
        monkeypatch,
        tmp_path,
        catalog={"ru": {}, "en": {}},
        js={"m.js": "const esc = s => s.replace(/[&<>\"']/g, c => c); // Комментарий\n"},
    )
    errors: list[str] = []
    module.check_no_bare_cyrillic(errors)
    assert errors == [], errors


def test_scan_js_extracts_t_and_tp_keys(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    src = tmp_path / "m.js"
    src.write_text('t("a.b"); tp(n, "c.d", { x: 1 });\n', encoding="utf-8")
    keys, plural, bare = module.scan_js(src)
    assert keys == {"a.b"}
    assert plural == {"c.d"}
    assert bare == []


def test_full_catalog_repo_is_green_end_to_end() -> None:
    """Позитив в связке (дублирует main==0, но фиксирует «полный каталог → зелёный»)."""
    module = _load_module()
    errors: list[str] = []
    module.check_key_parity(errors)
    module.check_keys_present(errors)
    module.check_no_bare_cyrillic(errors)
    assert errors == [], errors


# ---------------------------------------------------------------------------
# issue #821: подсказка contenteditable-редактора
#
# У редактора нет атрибута `placeholder` — текст рисуется из `data-placeholder`
# через CSS. Guard должен знать и про ключ-компаньон, и про кириллицу в самом
# атрибуте, иначе новый механизм окажется вне проверки (та же дыра, что #787).
# ---------------------------------------------------------------------------


def test_data_placeholder_key_is_collected() -> None:
    """Ключ из `data-i18n-data-placeholder` попадает в набор проверяемых."""
    module = _load_module()
    parser = module.collect_index_html()
    assert "check.editor_placeholder" in parser.i18n_keys
    assert "sandbox.editor_placeholder" in parser.i18n_keys


def test_cyrillic_data_placeholder_without_companion_is_flagged() -> None:
    """Кириллица в `data-placeholder` без ключа-компаньона — нарушение."""
    module = _load_module()
    parser = module._IndexParser()
    parser.feed('<div class="editor" data-placeholder="Напишите код"></div>')
    assert any("data-placeholder" in attr for _line, attr, _val in parser.bare_attr)


def test_cyrillic_data_placeholder_with_companion_is_allowed() -> None:
    """С компаньоном — нарушения нет: строка приедет из каталога."""
    module = _load_module()
    parser = module._IndexParser()
    parser.feed(
        '<div class="editor" data-placeholder="Напишите код" '
        'data-i18n-data-placeholder="sandbox.editor_placeholder"></div>'
    )
    assert parser.bare_attr == []
