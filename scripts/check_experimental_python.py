#!/usr/bin/env python3
"""scripts/check_experimental_python.py — «экспериментальная» устаревает сама.

Пометка `experimental: true` у ячейки матрицы — утверждение о **чужом
календаре**: версия предрелизная не потому, что мы так решили, а потому что её
ещё не выпустили. Такое утверждение стареет без единой правки в нашем дереве, и
ни один прогон при этом не краснеет — флаг делает ровно то, что написано.

Замер 09.09.2026. Флаг стоял на Python 3.14 с её предрелизных времён и пережил
выход: манифест раннера отдавал `3.14.7 stable=True` (седьмой патч) и
`3.15.0-rc.2 stable=False`. Целый цикл релизов падения на полноценной
поддерживаемой версии не блокировали мерж — `continue-on-error` снимал их
молча. Цена: PR #1522 уехал в `main` с единственной упавшей ячейкой 3.14, и
причина осталась неизвестной, потому что сводка при `continue-on-error` не
приходит вовсе (#1526).

**Спрашивается у площадки, а не у памяти.** Живой источник — манифест
`actions/python-versions`, тот самый, из которого раннер и берёт версию: его
поле ``stable`` и есть ответ на вопрос «предрелизная ли». Своей таблицы дат
здесь нет намеренно — она разошлась бы с чужим календарём ровно так же, как
разошёлся флаг.

**Предупреждение, а не отказ.** Красный гейт здесь останавливал бы работу из-за
события в чужом проекте — выхода версии, — которое произошло без нас. Находка
уезжает в задачу ночного обхода, откуда её видно тому, кто правит матрицу и
ruleset (правило 142: у находки обязан быть адресат).

Три исхода разведены (правило 039): «совпало», «флаг разошёлся с площадкой» и
«спросить не удалось» — последнее означает недоступный манифест, а не порядок
в матрице.

Запуск::

    python scripts/check_experimental_python.py
    python scripts/check_experimental_python.py --manifest <файл>   # без сети
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

_ROOT = Path(__file__).resolve().parent.parent
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_UNKNOWN",
    "MANIFEST_URL",
    "Mismatch",
    "flagged_versions",
    "main",
    "mismatches",
    "stable_minors",
]

#: Живой источник: тот же манифест, из которого раннер берёт интерпретатор.
MANIFEST_URL = (
    "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"
)

EXIT_OK = 0
EXIT_FAIL = 1
#: Спросить не удалось: сеть, прокси, неразобранный ответ. Не «всё совпало».
EXIT_UNKNOWN = 2

_TIMEOUT_S = 30

#: Ячейка матрицы: ``- {os: "x", python-version: "3.15", experimental: true}``.
_CELL_RE = re.compile(
    r"python-version:\s*\"([\d.]+)\"[^}]*experimental:\s*(true|false)",
)


class Mismatch(NamedTuple):
    """Версия, чей флаг разошёлся с площадкой."""

    version: str
    latest: str

    def line(self) -> str:
        """Строка отчёта — с номером патча, чтобы «давно ли» читалось сразу."""
        return (
            f"  {self.version}: помечена экспериментальной, а раннер отдаёт "
            f"{self.latest} со `stable=True`"
        )


def flagged_versions(text: str) -> set[str]:
    """Версии, помеченные в матрице экспериментальными — чистая функция.

    Args:
        text: Содержимое ``ci.yml``.

    Returns:
        Множество вида ``{"3.15"}``.
    """
    return {version for version, flag in _CELL_RE.findall(text) if flag == "true"}


def stable_minors(manifest: list[dict[str, Any]]) -> dict[str, str]:
    """Минорные версии, у которых есть выпущенный релиз, и свежайший из них.

    Предрелизные записи отбрасываются: вопрос ровно в том, вышла версия или нет.

    Args:
        manifest: Разобранный ``versions-manifest.json``.

    Returns:
        ``{"3.14": "3.14.7", …}`` — минорная версия и свежайший стабильный патч.
    """
    newest: dict[str, str] = {}
    for item in manifest:
        if not isinstance(item, dict) or not item.get("stable"):
            continue
        version = str(item.get("version", ""))
        minor = ".".join(version.split(".")[:2])
        # Манифест отсортирован от новых к старым, поэтому первая запись на
        # минорную версию и есть свежайшая.
        newest.setdefault(minor, version)
    return newest


def mismatches(text: str, manifest: list[dict[str, Any]]) -> list[Mismatch]:
    """Помеченные экспериментальными версии, которые площадка считает вышедшими.

    Обратный случай — вышедшая версия БЕЗ флага — находкой не является: так и
    должно быть, и матрица обязана её содержать.

    Args:
        text: Содержимое ``ci.yml``.
        manifest: Разобранный манифест раннера.

    Returns:
        Список расхождений; пустой — флаги совпали с площадкой.
    """
    stable = stable_minors(manifest)
    return [
        Mismatch(version, stable[version])
        for version in sorted(flagged_versions(text))
        if version in stable
    ]


def _load_manifest(source: Path | None) -> list[dict[str, Any]] | None:
    """Манифест раннера из файла либо из сети; ``None`` — прочитать не удалось."""
    try:
        if source is not None:
            raw = source.read_text(encoding="utf-8")
        else:
            with urllib.request.urlopen(MANIFEST_URL, timeout=_TIMEOUT_S) as response:
                raw = response.read().decode("utf-8")
        data = json.loads(raw)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def main(argv: list[str] | None = None) -> int:
    """0 — флаги совпали (находки печатаются предупреждением); 2 — спросить не удалось."""
    parser = argparse.ArgumentParser(
        prog="python scripts/check_experimental_python.py",
        description="Экспериментальной помечается предрелизная версия, а не вчерашняя.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="локальная копия versions-manifest.json (без сети)",
    )
    args = parser.parse_args(argv)

    try:
        text = _CI.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Спросить не удалось: {_CI.name} не прочитан ({exc})", file=sys.stderr)
        return EXIT_UNKNOWN

    manifest = _load_manifest(args.manifest)
    if manifest is None:
        print(
            "Спросить не удалось: манифест версий раннера не прочитан. Это не "
            "«флаги совпали» — предмет сверки недоступен.",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

    flagged = sorted(flagged_versions(text))
    found = mismatches(text, manifest)
    print(f"Помечены экспериментальными: {', '.join(flagged) or 'нет'}; разошлись: {len(found)}")
    if not found:
        return EXIT_OK

    for item in found:
        print(item.line())
    print(
        "::warning::пометка «экспериментальная» — утверждение о чужом календаре, и она "
        "устарела. Пока флаг стоит, падения на вышедшей версии идут под "
        "`continue-on-error` и мерж не блокируют, а сводка о них не приходит вовсе."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
