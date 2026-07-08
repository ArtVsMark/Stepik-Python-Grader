#!/usr/bin/env python3
"""scripts/generate_version_badge.py — живой бейдж ТЕКУЩЕЙ версии (не только релизной).

Проблема: README-бейдж ``Release`` (``img.shields.io/github/v/release/...``)
показывает последний git-тег/GitHub Release — и корректно НЕ меняется между
релизами. Но по схеме проекта (см. CONTRIBUTING.md § Версионирование) PATCH —
это счётчик коммитов ПОСЛЕ тега, поэтому `main` может уйти на десятки коммитов
вперёд последнего релиза незаметно для стороннего наблюдателя README, пока
кто-то не поставит новый тег.

Этот скрипт пишет отдельный shields.io "endpoint badge" JSON с ТЕКУЩЕЙ
версией по логической схеме проекта — переиспользует
``scripts/version.py::project_version()`` (MAJOR.MINOR из последнего тега,
PATCH = число коммитов после него) как единственный источник, вместо того
чтобы заново дублировать вычисление через git. Значение меняется на каждый
коммит в main, в отличие от Release-бейджа.

Запуск::

    python scripts/generate_version_badge.py [--out PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
from collections.abc import Callable

__all__ = ["load_project_version_fn", "build_badge_payload", "main"]

_VERSION_SCRIPT = pathlib.Path(__file__).resolve().parent / "version.py"


def load_project_version_fn() -> Callable[[], str]:
    """Загрузить ``project_version()`` из ``scripts/version.py`` по пути.

    ``scripts/`` — не пакет (нет ``__init__.py``), поэтому обычный import не
    сработает; тот же приём, которым тесты (``test_check_version_consistency.py``
    и т.п.) грузят соседние скрипты.
    """
    spec = importlib.util.spec_from_file_location("_project_version_module", _VERSION_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.project_version  # type: ignore[no-any-return]


def build_badge_payload(version: str) -> dict[str, str | int]:
    """Собрать JSON-объект схемы shields.io endpoint badge (schemaVersion 1)."""
    return {
        "schemaVersion": 1,
        "label": "version",
        "message": version,
        "color": "blue",
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path(".github/badges/version.json"),
        help="Куда записать shields.io endpoint JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Точка входа: вычислить текущую версию, записать badge JSON, напечатать сводку."""
    args = _build_arg_parser().parse_args(argv)
    project_version = load_project_version_fn()
    payload = build_badge_payload(project_version())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Version badge written to {args.out}: {payload['message']}")


if __name__ == "__main__":
    main()
