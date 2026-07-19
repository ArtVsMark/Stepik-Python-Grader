#!/usr/bin/env python3
"""scripts/generate_glossary_badge.py — живой бейдж числа ready-карточек глоссария.

Проблема: README/README.en/докстринги/CHANGELOG хардкодили число карточек
глоссария («601», «~600», «581», «787 hidden») текстом — и оно дрейфовало
относительно реальной базы (``src/stepik_grader/glossary/data/*.json``) с каждой
волной наполнения (эпик #363). ``CLAUDE.md`` #398 прямо требует брать число из
кода, а не хардкодить.

Скрипт-мост (зеркало ``generate_version_badge.py``/``generate_coverage_badge.py``):
грузит комплектную базу (``BUNDLED_GLOSSARY_DIR``) через ``JsonGlossaryProvider``,
считает карточки со ``status="ready"`` и пишет ``.github/badges/glossary.json`` в
формате shields.io "endpoint badge" (https://shields.io/badges/endpoint-badge).
README ссылается на файл через ``img.shields.io/endpoint?url=...`` (raw GitHub
URL) — shields.io запрашивает JSON заново, поэтому бейдж живой, если базу
поддерживают в актуальном состоянии (CI-шаг обновления бейджей, `.github/workflows/ci.yml`).

Запуск::

    python scripts/generate_glossary_badge.py [--cards DIR] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import pathlib

from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider

__all__ = ["build_badge_payload", "count_ready_cards", "main"]


def count_ready_cards(cards_dir: pathlib.Path = BUNDLED_GLOSSARY_DIR) -> int:
    """Число ``ready``-карточек в базе глоссария.

    Единственный источник — сама база ``data/*.json`` через
    ``JsonGlossaryProvider.list_by_status("ready")``; никакого хардкода
    (issue #398/#535). По умолчанию — комплектная ``BUNDLED_GLOSSARY_DIR``.
    """
    provider = JsonGlossaryProvider.load(cards_dir)
    return len(provider.list_by_status("ready"))


def build_badge_payload(ready: int) -> dict[str, str | int]:
    """Собрать JSON-объект схемы shields.io endpoint badge (schemaVersion 1)."""
    return {
        "schemaVersion": 1,
        "label": "glossary",
        "message": f"{ready} ready",
        "color": "blue",
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--cards",
        type=pathlib.Path,
        default=BUNDLED_GLOSSARY_DIR,
        help="База карточек (файл/директория); по умолчанию — комплектный bundled-каталог.",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path(".github/badges/glossary.json"),
        help="Куда записать shields.io endpoint JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Точка входа: посчитать ready-карточки, записать badge JSON, напечатать сводку."""
    args = _build_arg_parser().parse_args(argv)
    ready = count_ready_cards(args.cards)
    payload = build_badge_payload(ready)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Glossary badge written to {args.out}: {payload['message']}")


if __name__ == "__main__":
    main()
