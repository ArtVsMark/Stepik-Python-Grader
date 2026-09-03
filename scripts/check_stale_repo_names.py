#!/usr/bin/env python3
"""scripts/check_stale_repo_names.py — перепись имён, которые площадка чинит за нас (issue #1421).

Правило 172 каталога: когда совместимость держит не наш код, а **площадка**, и
отключить её нельзя, обычный приём «сломать сразу и заметно» неприменим по
построению. Переименованный репозиторий продолжает отвечать по старому имени —
редиректом, — поэтому сигнала о незавершённой миграции не будет вовсе: ни
красного прогона, ни битой ссылки.

Замер после переименования каталога правил: ручной прогон зелёный, полный
предпушевой набор из восьми гейтов зелёный целиком — при двадцати устаревших
адресах в четырнадцати файлах. Ни один механизм не заметил ничего и заметить не
мог. Расхождение нашлось тем, что человек назвал его вслух.

Дороже всего не деградация, а **захват**: редирект держится, пока старое имя
свободно. Займи его кто угодно — и закреплённый ``uses:`` начнёт тянуть чужое
действие, то есть чужой код с правом писать в наш трекер под нашим токеном.
Отложенная уборка меняет класс проблемы из гигиены в безопасность.

**Живой источник — площадка, а не константа.** Список прежних имён нигде не
ведётся и вестись не может: перепись собирает имена **из своего дерева** и
спрашивает у API каноническое ``full_name`` каждого. Сверять копию с копией
здесь бессмысленно — обе стороны наши.

**История и действующий адрес — разное.** Старый след в записи о прошлом
инциденте переписывать нельзя (правило 114), поэтому журналы и архив из переписи
исключены явно, а не забыты.

Исходы три (правило 039): ``0`` — все имена канонические, ``1`` — находка,
``2`` — проверка не отработала (нет прав, кончилась квота, сеть недоступна).

Запуск::

    python scripts/check_stale_repo_names.py
    python scripts/check_stale_repo_names.py --owner ArtVsMark
"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "HISTORY_GLOBS",
    "OWNER",
    "SCANNED_GLOBS",
    "main",
    "mentions",
    "stale_names",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Чьи репозитории переписываются. Чужие сюда не входят: переименование чужого
#: проекта — не наша миграция, и спрашивать о нём API мы права не имеем.
OWNER = "ArtVsMark"

#: Где ищем действующие адреса. Список закрытый: перепись перечисляет места, а
#: не намерения.
SCANNED_GLOBS = (
    "*.md",
    "*.toml",
    ".github/**/*.yml",
    ".rules/*.json",
    "docs/**/*.md",
    "scripts/*.py",
    "src/**/*.py",
    "tests/**/*.py",
)

#: Что переписывать НЕЛЬЗЯ: там имя — история, а не адрес. Прошлое не правят
#: задним числом (правило 114), и молчаливое включение этих файлов в перепись
#: означало бы требование переписать журнал.
HISTORY_GLOBS = (
    "CHANGELOG.md",
    "HISTORY.md",
    "changelog.d/",
    "docs/archive/",
    "docs/audit/",
)

#: ``владелец/репозиторий`` в тексте. Форм две, и обе нужны.
#:
#: Первая — адрес в ссылке: там за именем законно идёт продолжение пути
#: (``/blob/main/...``), и опознаётся оно по домену слева.
#:
#: Вторая — голая пара ``владелец/имя``, и вот ей продолжение пути запрещено.
#: Без этого условия под перепись попадал плейсхолдер из шаблона обращения —
#: строка вида «Merge pull request #741 from <владелец>/<ветка>/<хвост>», где
#: за именем владельца стоит имя ВЕТКИ, а не репозитория. Ложная находка в
#: переписи стоит дороже пропущенной: перепись, краснеющую на выдуманном,
#: отключают целиком.
#:
#: Здесь же причина, по которой примеры в этом файле пишутся без живой пары
#: «владелец/имя»: файл разбирается собственной проверкой, и литерал в
#: комментарии стал бы её находкой.
_MENTION_RES = (
    re.compile(rf"(?:github\.com|githubusercontent\.com)/{OWNER}/([A-Za-z0-9._-]+)"),
    re.compile(rf"\b{OWNER}/([A-Za-z0-9._-]+)(?![/\w-])"),
)

#: Хвосты, которые прилипают к имени в ссылке и именем не являются.
_TRAILING = (".git", ".", ",", ")")


def _is_history(path: pathlib.Path, base: pathlib.Path) -> bool:
    """Лежит ли файл там, где имя — история, а не действующий адрес.

    Путь считается от РАЗБИРАЕМОГО корня, а не от корня репозитория: иначе
    перепись работала бы только на собственном дереве, а на любом другом
    падала бы — что и поймал её же тест.
    """
    relative = path.relative_to(base).as_posix()
    return any(relative == marker or relative.startswith(marker) for marker in HISTORY_GLOBS)


def mentions(root: pathlib.Path | None = None) -> dict[str, list[str]]:
    """Имена репозиториев владельца, названные в действующих файлах.

    Args:
        root: Корень дерева; ``None`` — свой собственный.

    Returns:
        Имя репозитория → пути, где оно записано (относительные, отсортированы).
    """
    base = root if root is not None else _ROOT
    found: dict[str, set[str]] = {}
    for pattern in SCANNED_GLOBS:
        for path in sorted(base.glob(pattern)):
            if not path.is_file() or _is_history(path, base):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for pattern_re in _MENTION_RES:
                for raw in pattern_re.findall(text):
                    name = raw
                    for tail in _TRAILING:
                        name = name.removesuffix(tail)
                    if name:
                        found.setdefault(name, set()).add(path.relative_to(base).as_posix())
    return {name: sorted(paths) for name, paths in sorted(found.items())}


def stale_names(
    named: dict[str, list[str]],
    canonical: dict[str, str],
) -> list[str]:
    """Имена, которые площадка сегодня зовёт иначе.

    Args:
        named: Имя → где записано.
        canonical: Имя → каноническое имя по ответу площадки. Отсутствие
            означает «спросить не удалось» и находкой не считается: незнание не
            доказывает устаревания.

    Returns:
        Готовые к печати находки.
    """
    problems: list[str] = []
    for name, paths in named.items():
        current = canonical.get(name)
        if current is None or current == name:
            continue
        where = ", ".join(paths[:6]) + ("…" if len(paths) > 6 else "")
        problems.append(
            f"{OWNER}/{name} сегодня называется {OWNER}/{current} — "
            f"адрес держится редиректом площадки, а не нами: {where}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    """0 — имена канонические, 1 — находка, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default=OWNER)
    args = parser.parse_args(argv)

    named = mentions()
    canonical: dict[str, str] = {}
    unreachable: list[str] = []
    for name in named:
        try:
            data = gh_rest.request("GET", f"repos/{args.owner}/{name}").data
        except gh_rest.RateLimited as error:
            print(f"проверка не отработала: {error}")
            return 2
        except (gh_rest.GitHubError, gh_rest.MissingToken, OSError) as error:
            unreachable.append(f"{name}: {error}")
            continue
        full = str((data or {}).get("full_name") or "")
        if "/" in full:
            canonical[name] = full.split("/", 1)[1]

    # Правило 165: охват называется числом. «Чисто» без него означает и
    # «устаревших нет», и «ничего не спросили».
    print(
        f"Перепись имён: названо репозиториев — {len(named)}, "
        f"канон получен для {len(canonical)}, спросить не удалось — {len(unreachable)}."
    )
    # Неполнота называется ВСЕГДА, а не только когда она полная. Перепись, о
    # которой не сказано, скольких мест она не коснулась, и есть то состояние,
    # ради которого правило 172 заведено: «все прогоны зелёные, и никто не
    # может назвать число мест».
    for line in unreachable:
        print(f"  · не спрошено — {line}")
    if not canonical and named:
        print("проверка не отработала: канон не получен ни по одному имени")
        return 2

    problems = stale_names(named, canonical)
    if problems:
        print("FAIL: в дереве записаны имена, которые площадка чинит за нас:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "Редирект держится, пока старое имя свободно. Занятое кем-то другим, "
            "оно уводит `uses:` на чужой код с правом писать в наш трекер."
        )
        return 1
    print("Все названные имена канонические; журналы и архив в перепись не входят.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
