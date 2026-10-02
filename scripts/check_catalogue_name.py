"""scripts/check_catalogue_name.py — объявленное имя каталога не должно быть редиректом.

Каталог правил переименовали, и не сломалось НИЧЕГО: `git clone` по старому
имени GitHub переадресует, ссылки в браузере тоже. Именно поэтому заметить
переименование было нечем — все механизмы оставались зелёными, а репозиторий,
на который они ссылаются, уже назывался иначе.

Редирект — не гарантия, а отсрочка. Он живёт ровно до тех пор, пока старое имя
никем не занято: заведи кто-нибудь репозиторий с прежним названием, и клон
поедет к нему молча. Отдельно от этого ссылка вида ``uses: владелец/репозиторий``
в GitHub Actions разрешается не так, как git-клон, и полагаться на одинаковое
поведение обоих нельзя.

ПОЧЕМУ НЕ ПО HTTP-ОТВЕТУ СТРАНИЦЫ. Первая редакция спрашивала
``https://github.com/владелец/репозиторий`` и смотрела на код 3xx. Она отвечала
«имя каноническое» на **любой** ответ, не являющийся переадресацией, — в том
числе на 403, которым облачной сессии отвечает прокси. То есть проверка,
написанная против ложного зелёного, сама зеленела, ничего не спросив. Отсюда
правило её устройства: **ответ засчитывается только определённый**, всё
остальное — третий исход.

Спрашивается канон: REST отдаёт ``full_name`` уже переименованного
репозитория, и сравнение идёт с ним.

Исходы три (правило 039):

``0``
    Канон совпал с объявленным — переименования не было.
``1``
    Канон другой: каталог переименован, находка называет новое имя.
``2``
    Проверка не отработала — прав нет, квота кончилась, сеть недоступна, ответ
    без ``full_name``. Предмет отказа называется (правило 158).

Запуск::

    python scripts/check_catalogue_name.py
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gh_rest

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "EXIT_BROKEN",
    "EXIT_FINDING",
    "EXIT_OK",
    "canonical_name",
    "declared_catalogue",
    "main",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
BINDINGS = _ROOT / ".rules" / "bindings.json"

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_BROKEN = 2


def declared_catalogue(path: pathlib.Path | None = None) -> str:
    """``владелец/репозиторий`` каталога по ответу проекта.

    Raises:
        ValueError: ответа нет либо в нём нет разбираемого адреса.
    """
    target = path if path is not None else BINDINGS
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{target}: ответа каталогу нет") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{target}: не разбирается ({exc})") from exc

    url = str(data.get("catalogue") or "").strip().rstrip("/")
    if not url:
        raise ValueError(f"{target}: поле `catalogue` пусто — сверять нечего")
    parts = url.split("/")
    if len(parts) < 2:
        raise ValueError(f"{target}: `catalogue` не содержит владельца и репозитория: {url!r}")
    return f"{parts[-2]}/{parts[-1]}"


def canonical_name(repo: str) -> str:
    """Как репозиторий называется НА САМОМ ДЕЛЕ — по ответу площадки.

    Raises:
        RuntimeError: спросить не удалось либо ответ без ``full_name``. «Не
            спросили» и «спросили, всё в порядке» — разные исходы, и путать их
            здесь нельзя: проверка ради этого и написана.
    """
    try:
        data = gh_rest._get(f"repos/{repo}")
    except Exception as exc:
        raise RuntimeError(f"repos/{repo}: {exc}") from exc
    full_name = str((data or {}).get("full_name") or "")
    if not full_name:
        raise RuntimeError(f"repos/{repo}: ответ без `full_name` — канон не назван")
    return full_name


def main(argv: list[str] | None = None) -> int:
    """0 — имя каноническое, 1 — переименовано, 2 — проверка не отработала."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", type=pathlib.Path, default=BINDINGS, help="ответ проекта")
    args = parser.parse_args(argv)

    try:
        declared = declared_catalogue(args.bindings)
        canonical = canonical_name(declared)
    except (ValueError, RuntimeError) as exc:
        print(f"проверка не отработала: {exc}", file=sys.stderr)
        return EXIT_BROKEN

    if canonical.lower() == declared.lower():
        print(f"каталог назван канонически: {canonical}")
        return EXIT_OK

    print(
        f"каталог переименован: объявлен {declared}, канон — {canonical}.\n"
        "Редирект — отсрочка, а не гарантия: он держится, пока старое имя никем "
        "не занято, и ссылка `uses:` в GitHub Actions разрешается не так, как "
        "git-клон. Замените имя во всех ссылках и пересоберите производные."
    )
    return EXIT_FINDING


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
