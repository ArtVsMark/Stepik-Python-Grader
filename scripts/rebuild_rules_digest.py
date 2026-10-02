"""scripts/rebuild_rules_digest.py — пересобрать дайджест и решить, можно ли везти.

Дайджест и указатель правил — производное от чужого каталога, и до сих пор их
пересобирало окно: ночной обход находил отставание, человек открывал PR.
Пересборка механична, поэтому её делает прогон (``.github/workflows/rules-digest.yml``).

Решение живёт ЗДЕСЬ, а не шагом workflow, по той же причине, по которой в
скрипте живут проверки ночного обхода: шаги workflow не тестируются, а этот
файл — да.

Исходов три, и средний — не ошибка:

``nothing``
    Производное совпадает с каталогом, везти нечего.
``blocked``
    Пересобрано, но отправлять нельзя: правило появилось в каталоге, попало в
    дайджест, а ответа по нему в ``.rules/bindings.json`` ещё нет — обязательная
    проверка ``check_rules_digest.py`` на этом краснеет. Запушив такое, прогон
    открыл бы красный PR и запер им очередь мержа, то есть сломал бы ровно то,
    ради чего заводился. Ответ каталогу — суждение проекта, а не работа
    генератора; находку и так ведёт ночной обход.
``ready``
    Пересобрано и согласовано — ветку можно пушить.

Запуск::

    python scripts/rebuild_rules_digest.py --catalogue <клон каталога>
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["DERIVED", "VERDICTS", "main", "rebuild_verdict"]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Что пересобирается. Путь один: оба файла лежат рядом и едут вместе.
DERIVED = "docs/agent/rules"

#: Возможные исходы — в порядке возрастания работы.
VERDICTS = ("nothing", "blocked", "ready")

#: Код «проверка не отработала»: генератор упал, каталога нет, git недоступен.
EXIT_BROKEN = 2


def _run(argv: list[str], *, root: pathlib.Path) -> subprocess.CompletedProcess[str]:
    # argv собирается здесь целиком, оболочка не участвует: пользовательского
    # ввода в командной строке нет.
    # encoding задан явно: без него `text=True` декодирует по локали, и на
    # машине с cp1251 русский вывод генератора падает UnicodeDecodeError —
    # подклассом ValueError, который обычно проглатывают вместе с ним.
    return subprocess.run(
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def rebuild_verdict(catalogue: pathlib.Path, *, root: pathlib.Path | None = None) -> str:
    """Пересобрать производное и сказать, что с ним делать.

    Raises:
        RuntimeError: генератор или git не отработали — предмет назван в тексте
            (правило 158: третий исход говорит, ЧТО именно отказало).
    """
    base = root if root is not None else _ROOT
    for generator in ("generate_rules_digest.py", "generate_rules_index.py"):
        done = _run(
            [sys.executable, str(base / "scripts" / generator), "--catalogue", str(catalogue)],
            root=base,
        )
        if done.returncode != 0:
            raise RuntimeError(f"scripts/{generator}: {done.stderr.strip() or done.stdout.strip()}")

    diff = _run(["git", "diff", "--quiet", "--", DERIVED], root=base)
    if diff.returncode == 0:
        return "nothing"
    if diff.returncode != 1:
        raise RuntimeError(f"git diff -- {DERIVED}: {diff.stderr.strip() or 'неожиданный код'}")

    guard = _run([sys.executable, str(base / "scripts" / "check_rules_digest.py")], root=base)
    return "ready" if guard.returncode == 0 else "blocked"


def main(argv: list[str] | None = None) -> int:
    """Печатает вердикт одним словом; 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=pathlib.Path, required=True, help="клон каталога")
    parser.add_argument("--root", type=pathlib.Path, default=_ROOT, help="корень проекта")
    args = parser.parse_args(argv)

    try:
        verdict = rebuild_verdict(args.catalogue, root=args.root)
    except RuntimeError as exc:
        print(f"пересборка не отработала: {exc}", file=sys.stderr)
        return EXIT_BROKEN

    print(verdict)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
