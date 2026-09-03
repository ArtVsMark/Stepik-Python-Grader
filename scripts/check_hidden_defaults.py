#!/usr/bin/env python3
"""scripts/check_hidden_defaults.py — умолчания, которые прогон не проверяет (issue #1417).

Два правила каталога, один способ проверки. Оба дефекта требуют **совпадения
двух условий**, чтобы проявиться, поэтому зелёный прогон про них не говорит
ничего, а разбор исходника говорит всё.

**Правило 176 — кодировка.** ``subprocess`` в текстовом режиме без ``encoding=``
берёт кодировку локали: на ubuntu и macOS это UTF-8, на windows-раннере
cp1252/cp1251. Проект ведётся по-русски, git отдаёт русские темы коммитов, и
падение приходит `UnicodeDecodeError` — но только на трёх ячейках матрицы из
девяти и только если в выводе попалась подходящая буква. Хуже: ошибка
**симметрична**, пока обе стороны берут одну и ту же неверную локаль, они
сходятся, и правка одной стороны выглядит поломкой.

**Правило 165 — список путей из git.** ``core.quotePath=true`` — умолчание git,
поэтому имя с не-ASCII символами отдаётся экранированным. Разбор по строкам
принимает такую строку за путь, файл молча выпадает, и проверка остаётся
зелёной. Замер на этом дереве: отпечаток рабочего дерева не замечал правок в
файле ``утечка.py``, то есть pre-push хук принял бы состояние, которого не
проверял.

**Почему разбор исходника, а не прогон.** Прогон отвечает «сегодня не совпало»;
разбор отвечает «умолчание не задано», и этот ответ не зависит ни от платформы,
ни от данных. Прецедент в соседнем вызове механизмом не является: у соседнего
проекта явная кодировка стояла в двух вызовах, и всё равно два новых написали
без неё, разными заходами.

Запуск::

    python scripts/check_hidden_defaults.py
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import sys
from pathlib import Path

__all__ = [
    "GIT_LIST_COMMANDS",
    "SUBPROCESS_CALLS",
    "TEXT_MODE_KEYWORDS",
    "encoding_findings",
    "git_list_findings",
    "main",
    "nul_safe_wrappers",
    "scanned_files",
]

_ROOT = Path(__file__).resolve().parent.parent

#: Каталоги, которые разбираются. Тесты сюда входят намеренно: подделка,
#: читающая чужой вывод не в той кодировке, врёт так же, как рабочий код.
_ROOTS = ("src", "scripts", "tests")

#: Вызовы, у которых есть текстовый режим и кодировка.
SUBPROCESS_CALLS = frozenset({"run", "check_output", "Popen", "call", "check_call"})

#: Любой из них включает текстовый режим. ``errors`` — тоже: он выглядит
#: предусмотрительностью, а кодировку при этом оставляет локальной.
TEXT_MODE_KEYWORDS = frozenset({"text", "universal_newlines", "errors"})

#: Подкоманды git, отдающие СПИСОК ПУТЕЙ. Именно их читают дальше, и именно им
#: нужен ``-z``; ``git log``/``git show`` отдают текст, и правило их не касается.
GIT_LIST_COMMANDS = frozenset({"ls-files", "--name-only", "--porcelain"})


def scanned_files() -> list[Path]:
    """Исходники, которые разбираются, — в устойчивом порядке."""
    files: list[Path] = []
    for name in _ROOTS:
        files.extend(sorted((_ROOT / name).rglob("*.py")))
    return files


def _literals(node: ast.Call) -> list[str]:
    """Строковые литералы среди позиционных аргументов вызова.

    Разворачиваются и списки-литералы: ``subprocess.run(["git", "ls-files"])``
    и ``git("ls-files")`` — одна и та же форма для этой проверки.
    """
    found: list[str] = []
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            found.append(argument.value)
        elif isinstance(argument, ast.List | ast.Tuple):
            found.extend(
                item.value
                for item in argument.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return found


def _call_name(node: ast.Call) -> str:
    """Имя вызываемого: ``subprocess.run`` → ``run``, ``git(...)`` → ``git``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def encoding_findings(path: Path, tree: ast.AST) -> list[str]:
    """Вызовы ``subprocess`` в текстовом режиме без явной ``encoding`` (правило 176)."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in SUBPROCESS_CALLS:
            continue
        keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
        if not (keywords & TEXT_MODE_KEYWORDS) or "encoding" in keywords:
            continue
        problems.append(
            f"{path.relative_to(_ROOT)}:{node.lineno}: текстовый режим без encoding= — "
            "кодировка берётся из локали, и вывод с кириллицей падает только на части "
            "матрицы и только на подходящих данных (правило 176)"
        )
    return problems


def nul_safe_wrappers(tree: ast.AST) -> set[str]:
    """Функции модуля, которые сами добавляют ``-z``.

    Без этого гейт краснел бы на собственной починке: обёртка вида
    ``git_paths(git, "ls-files")`` не несёт ``-z`` в месте вызова — он стоит
    внутри неё. Признак берётся из дерева, а не из соглашения об именах:
    соглашение разъезжается с кодом молча.
    """
    safe: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any("-z" in _literals(inner) for inner in ast.walk(node) if isinstance(inner, ast.Call)):
            safe.add(node.name)
    return safe


def git_list_findings(path: Path, tree: ast.AST) -> list[str]:
    """Запросы списка путей у git без ``-z`` (правило 165)."""
    problems: list[str] = []
    safe = nul_safe_wrappers(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) in safe:
            continue
        literals = _literals(node)
        if not literals or "-z" in literals:
            continue
        # Предмет — вызов ИМЕННО git. Без этого условия под правило попадал
        # любой список строк: собственный `parametrize` этого гейта с именами
        # подкоманд краснел на себе же.
        if "git" not in literals and "git" not in _call_name(node).lower():
            continue
        named = sorted(GIT_LIST_COMMANDS & set(literals))
        if not named:
            continue
        problems.append(
            f"{path.relative_to(_ROOT)}:{node.lineno}: список путей от git "
            f"({', '.join(named)}) без -z — имя с не-ASCII символами приезжает "
            "экранированным и молча выпадает из обработки (правило 165)"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0, если умолчания заданы явно; иначе 1."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):  # зависит от платформы stdout
            reconfigure(encoding="utf-8")

    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    files = scanned_files()
    problems: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as error:
            # Нечитаемый исходник — находка, а не пропуск: молчание здесь и есть
            # та слепота, ради которой правило 165 требует называть охват.
            problems.append(f"{path.relative_to(_ROOT)}: не разбирается ({error})")
            continue
        problems.extend(encoding_findings(path, tree))
        problems.extend(git_list_findings(path, tree))

    # Правило 165, вторая половина: охват называется числом. «Чисто» без него
    # означает и «ничего не нашли», и «ничего не смотрели».
    print(f"Скрытые умолчания: разобрано исходников — {len(files)}.")
    if problems:
        print("FAIL: умолчание берётся из окружения, а не задано явно:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Умолчания заданы явно: кодировка у текстового subprocess, -z у списков путей.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
