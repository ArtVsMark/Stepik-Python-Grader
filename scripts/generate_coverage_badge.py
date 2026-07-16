#!/usr/bin/env python3
"""scripts/generate_coverage_badge.py — живой coverage-бейдж без ручной правки README.

Проблема: README.md держал бейдж покрытия как статичную картинку
(``img.shields.io/badge/coverage-95%25-...``) с числом, зашитым в URL текстом —
CI гонял ``pytest --cov`` (``pyproject.toml`` § ``[tool.pytest.ini_options]``),
но результат никуда не попадал обратно в README, поэтому цифра застывала на
дату последней ручной правки и расходилась с реальным покрытием.

Это скрипт-мост: читает суммарный ``line-rate`` из Cobertura ``coverage.xml``
(его уже даёт ``--cov-report=xml``) и пишет ``.github/badges/coverage.json`` в
формате shields.io "endpoint badge" (https://shields.io/badges/endpoint-badge).
README ссылается на этот файл через ``img.shields.io/endpoint?url=...`` (raw
GitHub URL) — shields.io каждый раз запрашивает JSON заново, поэтому бейдж
живой, если CI поддерживает файл в актуальном состоянии (см. `.github/workflows/ci.yml`,
шаг после прогона тестов на push в main).

Запуск::

    python scripts/generate_coverage_badge.py [--coverage-xml coverage.xml] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import xml.etree.ElementTree as ET

__all__ = [
    "badge_color",
    "build_badge_payload",
    "compute_coverage_percent",
    "main",
]

# Пороги цвета — стандартная шкала shields.io/Codecov для coverage-бейджей.
_COLOR_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (90.0, "brightgreen"),
    (80.0, "green"),
    (70.0, "yellowgreen"),
    (60.0, "yellow"),
    (50.0, "orange"),
)


def compute_coverage_percent(coverage_xml_path: pathlib.Path) -> float:
    """Прочитать суммарный процент покрытия из Cobertura ``coverage.xml``.

    Raises:
        FileNotFoundError: если файл отсутствует (прогнать ``pytest`` с
            ``--cov-report=xml`` перед вызовом).
        ValueError: если в корневом ``<coverage>`` нет атрибута ``line-rate``
            (не Cobertura-формат или битый файл).
    """
    if not coverage_xml_path.exists():
        raise FileNotFoundError(
            f"{coverage_xml_path} не найден — прогоните `pytest` "
            "(уже даёт --cov-report=xml, см. pyproject.toml) перед генерацией бейджа"
        )
    root = ET.parse(coverage_xml_path).getroot()
    line_rate = root.get("line-rate")
    if line_rate is None:
        raise ValueError(f"{coverage_xml_path}: нет атрибута 'line-rate' в корневом <coverage>")
    return round(float(line_rate) * 100, 1)


def badge_color(percent: float) -> str:
    """Цвет бейджа по стандартной шкале порогов покрытия shields.io."""
    for threshold, color in _COLOR_THRESHOLDS:
        if percent >= threshold:
            return color
    return "red"


def build_badge_payload(percent: float, *, label: str = "coverage") -> dict[str, str | int]:
    """Собрать JSON-объект схемы shields.io endpoint badge (schemaVersion 1).

    ``label`` — видимый текст слева на самой картинке бейджа (не только в
    markdown alt-тексте) — по умолчанию просто "coverage"; вызывающая
    сторона обязана передать разные значения для разных бейджей (issue
    #289: single-OS `coverage.json` и cross-OS `coverage-combined.json`
    рендерились с одинаковым label'ом "coverage", различались только числом —
    пользователь не мог понять по самой картинке, что каждая измеряет).
    """
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{percent:g}%",
        "color": badge_color(percent),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--coverage-xml",
        type=pathlib.Path,
        default=pathlib.Path("coverage.xml"),
        help="Путь к Cobertura coverage.xml (по умолчанию ./coverage.xml).",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path(".github/badges/coverage.json"),
        help="Куда записать shields.io endpoint JSON.",
    )
    parser.add_argument(
        "--label",
        default="coverage",
        help="Видимый текст слева на бейдже (по умолчанию 'coverage') — "
        "задайте разный label для разных бейджей, иначе они неразличимы "
        "на самой картинке (issue #289).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Точка входа: прочитать coverage.xml, записать badge JSON, напечатать сводку."""
    args = _build_arg_parser().parse_args(argv)
    percent = compute_coverage_percent(args.coverage_xml)
    payload = build_badge_payload(percent, label=args.label)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Coverage badge written to {args.out}: {payload['message']} ({payload['color']})")


if __name__ == "__main__":
    main()
