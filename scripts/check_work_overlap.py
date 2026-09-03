#!/usr/bin/env python3
"""scripts/check_work_overlap.py — кто ещё трогает эти файлы (issue #1091).

Работа идёт из двух окон — облачной сессии и локальной машины, — и они не видят
друг друга. За одну сессию это стоило дважды начатой заново работы: задача
бралась в разбор, когда часть её уже была сделана в соседнем окне и ждала
мержа. Плюс общие файлы (``CHANGELOG.md``, разделяемые тесты) регулярно
расходились конфликтами, которые видно только в момент мержа.

Скрипт отвечает на два вопроса ДО начала работы:

* какие ветки живут на ``origin`` и какие файлы они меняют;
* пересекается ли это с тем, что меняю я.

**Только git, без GitHub API и токенов.** Живые ветки и их диффы полностью
описывают «кто над чем работает»: PR не может существовать без ветки. Отдельный
реестр в файле не заводится намеренно — живой файл в ``main`` сам стал бы
источником тех же конфликтов и требовал бы правил протухания записей, которые
некому применять.

Запуск::

    python scripts/check_work_overlap.py           # пересечения моей работы с чужой
    python scripts/check_work_overlap.py --all     # обзор: все ветки и их файлы
    python scripts/check_work_overlap.py --install-hook   # поставить pre-push хук

Вывод — предупреждение, а не запрет: пересечение файлов часто законно (два PR
правят соседние строки разных функций). Решение остаётся за человеком, скрипт
лишь не даёт узнать о нём в момент мержа.
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import subprocess
import sys

__all__ = [
    "branch_files",
    "changed_files",
    "living_branches",
    "main",
    "overlaps",
]

# issue #1095: консоль Windows работает в cp1251/cp866, и `print` символов вне этой
# кодировки роняет скрипт `UnicodeEncodeError` — не на экзотике, а на обычном
# запуске в CI-джобе. Вывод переводится в UTF-8 явно; `errors="replace"` — чтобы
# отсутствующий глиф испортил один символ, а не убил команду целиком.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BASE = "origin/main"

# Ветки, которые не являются чужой работой: база и служебные линии агентов.
_IGNORED_PREFIXES = ("origin/HEAD",)

_HOOK_BODY = """#!/bin/sh
# issue #1091: предупреждение о пересечении с чужими ветками перед пушем.
# Никогда не блокирует пуш — только печатает, с кем пересеклись.
python scripts/check_work_overlap.py --quiet-when-clean || true
"""


def _git(*args: str) -> str:
    """``git`` в корне репозитория; пустая строка при любой ошибке."""
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=_ROOT,
            text=True,
            # Темы коммитов и имена веток здесь русские: без явной кодировки
            # вывод декодировался бы локалью машины и на cp1251 падал.
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def living_branches(exclude: str = "") -> list[str]:
    """Ветки на ``origin``, кроме базовой и своей собственной."""
    raw = _git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
    branches = []
    for name in raw.splitlines():
        if name in {_BASE, "origin/main"} or name.startswith(_IGNORED_PREFIXES):
            continue
        if exclude and name == f"origin/{exclude}":
            continue
        branches.append(name)
    return sorted(branches)


def _git_paths(*args: str) -> set[str]:
    """Пути от ``git``, прочитанные по NUL, а не по строкам (правило 165).

    ``core.quotePath=true`` — умолчание git, и имя с не-ASCII символами
    приезжает экранированным. Разбор по строкам принимает такую строку за путь,
    и файл выпадает из сравнения: пересечение с чужой веткой по нему не
    находится вовсе — то есть предупреждение молчит там, где обязано сработать
    (issue #1417). Проект ведётся по-русски, поэтому случай не экзотический.
    """
    return {path for path in _git(*args, "-z").split("\0") if path}


def branch_files(branch: str) -> set[str]:
    """Файлы, которые ветка меняет относительно ``origin/main``."""
    return _git_paths("diff", "--name-only", f"{_BASE}...{branch}")


def changed_files() -> set[str]:
    """Мои изменения: коммиты ветки относительно базы плюс рабочее дерево.

    Рабочее дерево включено намеренно: пересечение полезно знать ДО коммита,
    когда правку ещё дёшево перенести в другой файл или согласовать.
    """
    files = set(branch_files("HEAD"))
    for extra in (("diff", "--name-only"), ("diff", "--name-only", "--cached")):
        files |= _git_paths(*extra)
    return files


def overlaps(mine: set[str], exclude_branch: str = "") -> dict[str, set[str]]:
    """``{ветка: пересекающиеся файлы}`` — только непустые пересечения."""
    found: dict[str, set[str]] = {}
    for branch in living_branches(exclude=exclude_branch):
        shared = mine & branch_files(branch)
        if shared:
            found[branch] = shared
    return found


def _print_overview() -> int:
    """Обзор: все живые ветки и сколько файлов каждая трогает."""
    branches = living_branches()
    if not branches:
        print("На origin нет веток, кроме main — чужой работы не видно.")
        return 0
    print(f"Живые ветки на origin ({len(branches)}):\n")
    for branch in branches:
        files = sorted(branch_files(branch))
        head = ", ".join(files[:4]) + (f" и ещё {len(files) - 4}" if len(files) > 4 else "")
        print(f"  {branch}")
        print(f"      файлов: {len(files)} — {head or 'нет изменений против main'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0 всегда: скрипт предупреждает, а не запрещает."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--all", action="store_true", help="обзор всех веток и их файлов")
    parser.add_argument(
        "--quiet-when-clean", action="store_true", help="молчать, если пересечений нет (для хука)"
    )
    parser.add_argument("--install-hook", action="store_true", help="поставить pre-push хук")
    args = parser.parse_args(argv)

    if args.install_hook:
        return _install_hook()
    if args.all:
        return _print_overview()

    current = _git("rev-parse", "--abbrev-ref", "HEAD")
    mine = changed_files()
    if not mine:
        if not args.quiet_when_clean:
            print("Изменений против origin/main нет — пересекаться нечем.")
        return 0

    found = overlaps(mine, exclude_branch=current)
    if not found:
        if not args.quiet_when_clean:
            print(f"Пересечений нет: {len(mine)} файл(ов) этой ветки никто больше не трогает.")
        return 0

    print("ВНИМАНИЕ: эти файлы уже меняют другие ветки — возможен конфликт при мерже:\n")
    for branch, shared in sorted(found.items()):
        print(f"  {branch}")
        for path in sorted(shared):
            print(f"      {path}")
    print(
        "\nЭто предупреждение, а не запрет: два PR могут законно править разные части "
        "одного файла.\nЕсли пересечение содержательное — согласуйте порядок мержа, "
        "иначе второй PR будет разводить конфликт вручную."
    )
    return 0


def _install_hook() -> int:
    """Поставить ``pre-push`` хук, вызывающий этот скрипт."""
    hooks_dir = pathlib.Path(_git("rev-parse", "--git-path", "hooks") or ".git/hooks")
    if not hooks_dir.is_absolute():
        hooks_dir = _ROOT / hooks_dir
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-push"
        hook.write_text(_HOOK_BODY, encoding="utf-8")
        hook.chmod(0o755)
    except OSError as exc:
        print(f"Не удалось поставить хук: {exc}", file=sys.stderr)
        return 1
    print(f"pre-push хук поставлен: {hook}")
    print("Теперь каждый push печатает пересечения с чужими ветками (не блокируя пуш).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
