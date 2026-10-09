"""Совместимость решения с версией Python шага Stepik (issue #1621).

Решение владельца (эпик #1575): грейдер живёт на своей планке, а перед
проверкой предупреждает, если в коде есть то, чего нет в версии Stepik.
Здесь закреплены обе стороны: находка есть, где версия ниже нужной, и её нет,
где версия достаточна; неизвестное имя и динамика молчат.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stepik_grader.core import stepik_compat as sc


def _index(*cards: tuple[str, str, str]) -> sc.NameIndex:
    return sc.build_name_index(
        SimpleNamespace(id=card_id, added=added, removed=removed)
        for card_id, added, removed in cards
    )


_EMPTY = _index()


@pytest.mark.parametrize(
    ("key", "code"),
    [
        ("fstring", 'x = 1\ns = f"{x}"\n'),
        ("var_annotation", "x: int = 1\n"),
        ("async_generator", "async def g():\n    yield 1\n"),
        ("walrus", "if (n := 3):\n    pass\n"),
        ("posonly", "def f(a, /):\n    pass\n"),
        ("match", "match 1:\n    case 1:\n        pass\n"),
        ("except_star", "try:\n    pass\nexcept* ValueError:\n    pass\n"),
        ("star_index", "a = {}\nb = ()\na[*b]\n"),
        ("type_statement", "type X = int\n"),
        ("type_params", "class C[T]:\n    pass\n"),
        ("tstring", 'x = 1\ns = t"{x}"\n'),
    ],
)
def test_every_syntax_feature_is_detected_below_its_version(key: str, code: str) -> None:
    """Каждая строка таблицы признаков ловится ниже своей версии и молчит на ней."""
    since = next(version for version, name, _ in sc.SYNTAX_FEATURES if name == key)
    below = (since[0], since[1] - 1)

    assert key in {issue.what for issue in sc.check_code(code, below, _EMPTY)}
    assert key not in {issue.what for issue in sc.check_code(code, since, _EMPTY)}


def test_every_syntax_feature_has_labels_in_both_catalogues() -> None:
    """Подпись признака есть в серверных каталогах и в каталоге веба, на обоих языках."""
    root = Path(sc.__file__).parent
    ui = json.loads((root.parent / "web/static/locales/ui.json").read_text(encoding="utf-8"))
    for lang in ("ru", "en"):
        server = json.loads((root / f"locales/{lang}.json").read_text(encoding="utf-8"))
        for _, key, _ in sc.SYNTAX_FEATURES:
            assert f"compat_syntax_{key}" in server, (lang, key)
            assert f"compat.syntax_{key}" in ui[lang], (lang, key)


def test_names_newer_than_target_are_named() -> None:
    """Импорт, атрибут модуля, встроенная функция и метод литерала — все сверяются."""
    index = _index(
        ("itertools.batched", "3.12", ""),
        ("math.lcm", "3.9", ""),
        ("anext", "3.10", ""),
        ("str.removeprefix", "3.9", ""),
    )
    code = (
        "from itertools import batched\n"
        "import math as m\n"
        "m.lcm(2, 3)\n"
        "anext(it)\n"
        '"ab".removeprefix("a")\n'
    )

    found = {(issue.line, issue.what) for issue in sc.check_code(code, (3, 8), index)}

    assert found == {
        (1, "itertools.batched"),
        (3, "math.lcm"),
        (4, "anext"),
        (5, "str.removeprefix"),
    }


def test_new_module_takes_the_earliest_version_of_its_members() -> None:
    """Карточки модуля нет — версия модуля выводится из его членов, а не из второй таблицы."""
    index = _index(("tomllib.load", "3.11", ""), ("tomllib.loads", "3.11", ""))

    [issue] = sc.check_code("import tomllib\n", (3, 10), index)

    assert (issue.what, issue.since) == ("tomllib", (3, 11))


def test_removed_name_is_named_at_and_after_removal() -> None:
    """Удалённое имя — находка на версии удаления и позже, до неё — молчание."""
    index = _index(("typing.no_type_check_decorator", "3.5", "3.15"))
    code = "from typing import no_type_check_decorator\n"

    [issue] = sc.check_code(code, (3, 15), index)

    assert issue.removed == (3, 15)
    assert sc.check_code(code, (3, 14), index) == []


def test_own_function_shadowing_a_builtin_is_not_a_builtin() -> None:
    """Своя функция ``anext`` — не встроенная: ложной находки нет."""
    index = _index(("anext", "3.10", ""))
    code = "def anext(x):\n    return x\nanext(1)\n"

    assert sc.check_code(code, (3, 8), index) == []


def test_unknown_receiver_is_not_guessed() -> None:
    """Метод у объекта неизвестного типа не сверяется: граница названа в модуле."""
    index = _index(("str.removeprefix", "3.9", ""))

    assert sc.check_code('s = input()\ns.removeprefix("a")\n', (3, 8), index) == []


def test_broken_code_gives_no_warning() -> None:
    """Синтаксическую ошибку скажет сама проверка, а не предупреждение о версии."""
    assert sc.check_code("def (:\n", (3, 8), _EMPTY) == []


def test_step_versions_parse_only_python() -> None:
    """Из языков шага берутся только версии Python, по возрастанию."""
    assert sc.step_python_versions(["python3.12", "java17", "python3.6", "python3.10"]) == [
        (3, 6),
        (3, 10),
        (3, 12),
    ]


def test_report_uses_the_newest_step_version(tmp_path: Path) -> None:
    """Цель — самая свежая версия шага: на ней отправляет грейдер."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"languages": ["python3.6", "python3.10"]}), encoding="utf-8"
    )

    report = sc.compat_report("match 1:\n    case 1:\n        pass\n", tmp_path, index=_EMPTY)

    assert (report.target, report.known) == ((3, 10), True)
    assert report.issues == []


def test_report_without_step_languages_says_so(tmp_path: Path) -> None:
    """Версии шага не известны — сверка с версией по умолчанию и честная пометка."""
    report = sc.compat_report("type X = int\n", tmp_path, index=_EMPTY)

    assert (report.target, report.known) == (sc.DEFAULT_TARGET, False)
    payload = report.to_dict()
    assert payload["target"] == "3.12" and payload["known"] is False


def test_bundled_index_reads_the_real_glossary() -> None:
    """Живая половина: индекс по встроенному глоссарию знает ``str.removeprefix`` с 3.9."""
    assert sc.bundled_name_index().lookup("str.removeprefix") == ((3, 9), None)


def test_imported_or_non_builtin_name_is_not_a_builtin() -> None:
    """Термин глоссария без точки (``sentinel``) у вызова — не встроенная функция."""
    index = _index(("sentinel", "3.15", ""), ("anext", "3.10", ""))
    code = "from mylib import anext\nsentinel()\nanext(1)\n"

    assert sc.check_code(code, (3, 12), index) == []


def test_single_new_member_does_not_make_an_old_module_new() -> None:
    """Одна новая карточка члена не делает старый модуль новым: ложной находки нет."""
    index = _index(("itertools.batched", "3.12", ""))

    assert sc.check_code("import itertools\n", (3, 10), index) == []
