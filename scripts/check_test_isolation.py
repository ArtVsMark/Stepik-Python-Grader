#!/usr/bin/env python3
"""scripts/check_test_isolation.py — CI-guard изоляции тестов от реальной ФС (issue #997).

Тест, которому передали выдуманный абсолютный путь, работает с НАСТОЯЩИМ
диском разработчика. Прецедент: ``cli.main(["--serve", "--root", "/some/dir"])``
проверял, что ``--root`` доходит до сервера. Пока ``--root`` задавал только
корень раздачи, путь никого не смущал; когда он стал ещё и корнем настроек,
резолвер начал читать и писать по нему — на диске появился ``C:\\some\\dir`` с
``.grader_settings.json``. Через несколько недель этот файл сломал сам тест:
``record_history`` резолвился в ``False`` вместо ожидаемого ``True``, и только
на машине, где каталог успел появиться. В CI тест оставался зелёным — под
Linux запись в корень ``/`` не проходит вовсе.

Отсюда разделение труда с рантайм-guard'ом ``_no_writes_outside_tmp``
(``tests/conftest.py``): фикстура ловит ФАКТ появления файла — но только там,
где он появляется, то есть у разработчика, уже после загрязнения. Этот скрипт
ловит ПРИЧИНУ до прогона и в том числе на Linux-CI, где следа не будет.

Проверка: в списке аргументов командной строки, переданном прямо в вызов
(``cli.main([...])``, ``subprocess.run([...])``), нет строкового литерала,
похожего на абсолютный путь. Путь в argv теста строится от ``tmp_path`` — в том
числе заведомо несуществующий (``tmp_path / "no_such_file.py"``): он так же
проверяет ветку «файла нет», но не может ничего создать снаружи.

Область намеренно узкая — список литералов ПРЯМО в аргументах вызова. Список,
собранный по кускам в переменную, не проверяется: так из-под guard'а выходят
argv внешних утилит, где системные пути неизбежны и безопасны (``bwrap
--ro-bind /usr /usr`` в sandbox-тестах монтирует существующий каталог только на
чтение). Признак argv — элемент-флаг (``-x``/``--long``) в списке; список строк
без единого флага под проверку не попадает.

Никаких внешних зависимостей: чистый ``ast`` + ``pathlib``, детерминированно и
кроссплатформенно (Windows/Linux/macOS).

Запуск::

    python scripts/check_test_isolation.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

__all__ = [
    "argv_lists",
    "check_argv_paths",
    "collect_test_files",
    "is_absolute_path_literal",
    "main",
    "stray_paths",
]

_ROOT = Path(__file__).resolve().parent.parent
_TESTS = _ROOT / "tests"

# Абсолютный путь файловой системы: `C:\x`, `C:/x`, `/x`, `~/x`. Ведущее `//`
# не матчится намеренно — это protocol-relative URL, а не путь. HTTP-пути
# (`/api/v1/runs`) под правило не попадают по другой причине: они не лежат в
# argv-списках, а идут отдельным аргументом запроса.
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?!/)|~[\\/])")


def collect_test_files() -> list[Path]:
    """Все ``*.py`` набора тестов, включая ``tests/e2e/``."""
    return sorted(_TESTS.rglob("*.py"))


def is_absolute_path_literal(value: str) -> bool:
    """Строка выглядит как абсолютный путь файловой системы."""
    return bool(_ABSOLUTE_PATH_RE.match(value))


def argv_lists(tree: ast.AST) -> list[ast.List | ast.Tuple]:
    """Списки-литералы, переданные прямо в вызов и похожие на argv.

    Похожесть — по элементу-флагу (``-x``/``--long``): без него список строк
    неотличим от любых других данных, а с ним это командная строка.
    """
    found: list[ast.List | ast.Tuple] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        arguments = [*node.args, *(kw.value for kw in node.keywords)]
        for argument in arguments:
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            strings = [
                element.value
                for element in argument.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if any(s.startswith("-") and len(s) > 1 for s in strings):
                found.append(argument)
    return found


def stray_paths(argv: ast.List | ast.Tuple) -> list[str]:
    """Литералы argv-списка, похожие на абсолютный путь."""
    return [
        element.value
        for element in argv.elts
        if isinstance(element, ast.Constant)
        and isinstance(element.value, str)
        and is_absolute_path_literal(element.value)
    ]


def check_argv_paths(errors: list[str]) -> None:
    """В argv тестов нет абсолютных путей — только производные от ``tmp_path``."""
    files = collect_test_files()
    checked = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(_ROOT).as_posix()
        for argv in argv_lists(tree):
            checked += 1
            stray = stray_paths(argv)
            if stray:
                errors.append(
                    f"{relative}:{argv.lineno}: абсолютный путь в аргументах "
                    f"командной строки ({', '.join(repr(s) for s in stray)}) — тест "
                    "работает с настоящим диском разработчика. Стройте путь от "
                    "tmp_path: несуществующий путь внутри tmp_path проверяет ту же "
                    "ветку, но ничего не создаёт снаружи."
                )
    if not files or not checked:
        # Находка аудита: guard, потерявший вход, молча зелёный. Переезд tests/
        # или смена стиля вызовов не должны выключать проверку без сигнала.
        errors.append(
            f"tests/: проверять нечего ({len(files)} файл(ов), {checked} argv-список(ов)) "
            "— guard потерял вход и перестал бы охранять что-либо молча."
        )
        return
    print(f"test argv: checked {checked} argv list(s) across {len(files)} test file(s).")


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли.

    Тексты нарушений русские, а консоль Windows по умолчанию cp1251: без этого
    ``print`` падает ``UnicodeEncodeError`` и гейт возвращает 1 «на ровном
    месте», подменяя настоящую причину отказа своей собственной (тот же приём,
    что в ``scripts/check_docs_guardrails.py``). No-op на потоках без
    ``reconfigure`` — например, перехваченных pytest.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    _force_utf8_stdout()
    errors: list[str] = []
    check_argv_paths(errors)

    if errors:
        print("\nFAIL: test isolation guardrails violated:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("OK: тесты не передают в командную строку абсолютных путей.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
