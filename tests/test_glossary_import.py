"""Tests for scripts/import_glossary_python.py — импортёр Glossary-Python (issue #326).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем
же приёмом, что tests/test_check_docs_guardrails.py. Тесты используют крошечную
inline-фикстуру HTML (без зависимости от внешнего репозитория Glossary-Python).
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from stepik_grader.glossary.json_provider import JsonGlossaryProvider

_SCRIPT = Path(__file__).parent.parent / "scripts" / "import_glossary_python.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_import_glossary_python", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_script()

# Внешняя схема Glossary-Python: {id, name, group, subcat, cg, description,
# syntax, examples, version, docs}. Покрываем exception (PascalCase id),
# builtin-вызов, construct и term.
_FIXTURE = [
    {
        "id": "RecursionError",
        "name": "RecursionError",
        "group": "Исключения",
        "subcat": "встроенные",
        "cg": "exc",
        "description": "Слишком глубокая рекурсия.",
        "syntax": "",
        "examples": "",
        "version": None,
        "docs": "https://docs.python.org/3/library/exceptions.html#RecursionError",
    },
    {
        "id": "input",
        "name": "input()",
        "group": "Ввод и вывод",
        "subcat": "ввод",
        "cg": "builtin",
        "description": "Читает строку со stdin.",
        "syntax": "input([prompt]) -> str",
        "examples": "# name = input()\n\n# age = int(input())",
        "version": None,
        "docs": "https://docs.python.org/3/library/functions.html#input",
    },
    {
        "id": "match-case",
        "name": "match / case",
        "group": "Условный оператор",
        "subcat": "pattern matching",
        "cg": "op",
        "description": "Структурное сопоставление.",
        "syntax": "match x:\n    case _: ...",
        "examples": "",
        "version": "3.10+",
        "docs": "https://docs.python.org/3/",
    },
]


def _fixture_html(cards: list[dict]) -> str:
    payload = json.dumps(cards, ensure_ascii=False)
    return f'<html><body><script id="glossary-data">{payload}</script></body></html>'


def test_extract_external_cards_reads_embedded_json() -> None:
    cards = mod.extract_external_cards(_fixture_html(_FIXTURE))
    assert [c["id"] for c in cards] == ["RecursionError", "input", "match-case"]


def test_extract_raises_without_data_block() -> None:
    with pytest.raises(ValueError, match="glossary-data"):
        mod.extract_external_cards("<html><body>no data</body></html>")


def test_exception_id_lowercased_title_preserved() -> None:
    # id исключений → lowercase (конвенция анкоров core/glossary), title как есть.
    card = mod.external_to_card(_FIXTURE[0])
    assert card.id == "recursionerror"
    assert card.title == "RecursionError"
    assert card.kind == "exception"
    assert card.version == ""  # null → ""
    assert card.docs_url.endswith("#RecursionError")
    # issue #684: обратной ссылки на витрину у карточки нет — поток
    # односторонний, адрес карточки это её id как якорь своего глоссария.
    assert not hasattr(card, "url")


def test_builtin_call_is_function_with_split_examples() -> None:
    card = mod.external_to_card(_FIXTURE[1])
    assert card.kind == "function"
    assert card.id == "input"  # не исключение — регистр не меняется
    assert card.syntax == "input([prompt]) -> str"
    # examples: строка через \n → список строк с сохранёнными отступами.
    # Пустая строка ВНУТРИ примера остаётся (issue #1450): раньше отбрасывалась
    # любая, а вместе с ней уезжал и отступ тела блока — пример переставал
    # компилироваться. Режутся теперь только пустые строки по краям.
    assert card.examples == ["# name = input()", "", "# age = int(input())"]


def test_conditional_group_is_construct_with_version() -> None:
    card = mod.external_to_card(_FIXTURE[2])
    assert card.kind == "construct"
    assert card.version == "3.10+"
    assert card.section == "Условный оператор"
    assert card.subcat == "pattern matching"


def test_run_import_writes_files_and_is_idempotent(tmp_path: Path) -> None:
    html = tmp_path / "glossary.html"
    html.write_text(_fixture_html(_FIXTURE), encoding="utf-8")
    out = tmp_path / "data"

    counts = mod.run_import(html, out)
    assert counts == {"exc": 1, "builtin": 1, "op": 1}

    # база загружается провайдером; все 3 карточки на месте
    provider = JsonGlossaryProvider.from_directory(out)
    assert len(provider) == 3
    assert provider.get("recursionerror") is not None

    # идемпотентность: повторный запуск даёт побайтово те же файлы
    snapshot = {p.name: p.read_bytes() for p in sorted(out.glob("*.json"))}
    mod.run_import(html, out)
    again = {p.name: p.read_bytes() for p in sorted(out.glob("*.json"))}
    assert again == snapshot


def test_run_import_dedups_by_id(tmp_path: Path) -> None:
    dup = [_FIXTURE[1], {**_FIXTURE[1], "description": "дубль"}]
    html = tmp_path / "g.html"
    html.write_text(_fixture_html(dup), encoding="utf-8")
    out = tmp_path / "data"
    counts = mod.run_import(html, out)
    assert counts == {"builtin": 1}  # второй `input` отброшен


def test_bundled_base_matches_importer_output() -> None:
    # Комплектная база в репозитории — это вывод импортёра: все карточки
    # валидны и грузятся (регресс-проверка целостности data/*.json).
    from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR

    provider = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR)
    assert len(provider) >= 500
    assert provider.get("recursionerror") is not None
    assert provider.get("input") is not None


# --- отступ примера — синтаксис, а не оформление (issue #1450) --------------------
#
# Прежняя редакция `_split_examples` срезала отступ у КАЖДОЙ строки
# (`ln.strip()`), и многострочный пример переставал быть кодом: 90 карточек
# комплектной базы не компилируются именно так. Столько же независимо насчитала
# витрина правилом `example-compiles`.


def test_block_body_keeps_its_indent() -> None:
    """Тело блока остаётся с отступом — иначе пример не компилируется."""
    lines = mod._split_examples("for x in range(3):\n    print(x)")

    assert lines == ["for x in range(3):", "    print(x)"]
    ast.parse("\n".join(lines))


def test_a_flattened_block_would_not_compile() -> None:
    """Красная сторона того же: без отступа это IndentationError.

    Тест держит не форму вывода, а причину, по которой она такая: пример без
    отступа пользователь копирует в «Песочницу» и решает, что ошибся он.
    """
    with pytest.raises(IndentationError):
        ast.parse("for x in range(3):\nprint(x)")


def test_common_indent_of_the_whole_block_is_removed() -> None:
    """Лишний уровень из вёрстки снимается, относительные отступы остаются."""
    assert mod._split_examples("    class A:\n        x = 1") == ["class A:", "    x = 1"]


def test_blank_lines_inside_the_example_survive() -> None:
    """Внутри примера пустая строка разделяет части — склейка делает его нечитаемым."""
    assert mod._split_examples("a = 1\n\nb = 2") == ["a = 1", "", "b = 2"]


def test_blank_lines_are_trimmed_only_at_the_edges() -> None:
    """А по краям — режутся: ведущая пустая строка ничего не разделяет."""
    assert mod._split_examples("\n\nx = 1\n\n") == ["x = 1"]


def test_a_line_of_spaces_does_not_zero_the_common_indent() -> None:
    """Строка из одних пробелов для dedent значима и обнулила бы отступ всего блока."""
    assert mod._split_examples("    if x:\n   \n        y = 1") == ["if x:", "", "    y = 1"]


def test_tabs_are_kept_as_they_came() -> None:
    """Табуляцию не переводим в пробелы: это чужие данные, а не наш стиль."""
    assert mod._split_examples("def f():\n\treturn 1") == ["def f():", "\treturn 1"]


def test_single_line_examples_are_unchanged() -> None:
    """Однострочники ведут себя как раньше — правка не должна их трогать."""
    assert mod._split_examples("# name = input()\n# age = int(input())") == [
        "# name = input()",
        "# age = int(input())",
    ]


def test_an_empty_value_stays_an_empty_list() -> None:
    """Пустое значение — по-прежнему пустой список, а не список из пустой строки."""
    assert mod._split_examples("") == []
    assert mod._split_examples(None) == []
    assert mod._split_examples("\n\n") == []
