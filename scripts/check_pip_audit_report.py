#!/usr/bin/env python3
"""check_pip_audit_report.py — «уязвимостей нет» ≠ «аудит не отработал» (#921).

Находка REL-3-05 аудита 2026-08-10: job ``supply-chain`` не различал два
совершенно разных исхода. ``pip-audit`` выходит с ненулевым кодом и когда нашёл
уязвимость, и когда упал сам — не разрешился индекс, оборвалась сеть, PyPI
Advisory DB не ответила. Шаг в ``ci.yml`` печатал на оба случая одно и то же:
«найдена уязвимость». А поскольку job объявлен ``continue-on-error``, тихий
отказ инструмента выглядел ровно как чистое замыкание.

Разница не косметическая. «Проверили — чисто» и «проверить не удалось» ведут к
разным действиям: первое закрывает вопрос, второе означает, что сегодня о
безопасности зависимостей мы не знаем ничего.

Отличаем по НАЛИЧИЮ ОТЧЁТА, а не по коду возврата: код у краха и у находки
совпадает, а отчёт краш не оставляет. Разбор отчёта живёт скриптом, а не
строкой YAML, чтобы у него были тесты — иначе получился бы ещё один guard,
зелёный независимо от того, работает ли он (тот же #921).

Запуск::

    python scripts/check_pip_audit_report.py pip-audit.json

Коды возврата: ``0`` — аудит отработал, уязвимостей нет; ``1`` — уязвимости
найдены; ``2`` — аудит не отработал (отчёта нет или он нечитаем).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["EXIT_CLEAN", "EXIT_NOT_RUN", "EXIT_VULNERABLE", "count_vulnerabilities", "main"]

EXIT_CLEAN = 0
EXIT_VULNERABLE = 1
EXIT_NOT_RUN = 2


def count_vulnerabilities(report: Any) -> int:
    """Сколько уязвимостей в отчёте ``pip-audit --format=json``.

    Формат: ``{"dependencies": [{"name", "version", "vulns": [...]}, …]}``.
    Старые версии отдают голый список зависимостей — поддержаны обе формы,
    потому что версия инструмента ставится ad-hoc и не пришпилена.

    Raises:
        ValueError: структура не похожа на отчёт — считать по ней нельзя, а
            молча вернуть ноль означало бы объявить чистоту по мусору.
    """
    if isinstance(report, dict):
        dependencies = report.get("dependencies")
    elif isinstance(report, list):
        dependencies = report
    else:
        raise ValueError(f"отчёт не объект и не список: {type(report).__name__}")
    if not isinstance(dependencies, list):
        raise ValueError("в отчёте нет списка dependencies")
    total = 0
    for entry in dependencies:
        if not isinstance(entry, dict):
            raise ValueError("элемент dependencies — не объект")
        vulns = entry.get("vulns", [])
        if not isinstance(vulns, list):
            raise ValueError(f"поле vulns у {entry.get('name', '?')} — не список")
        total += len(vulns)
    return total


def main(argv: list[str] | None = None) -> int:
    """Прочитать отчёт и назвать исход; см. коды возврата в докстринге модуля."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("report", type=Path, help="путь к JSON-отчёту pip-audit")
    args = parser.parse_args(argv)

    try:
        raw = args.report.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"::warning title=pip-audit::аудит НЕ ОТРАБОТАЛ — отчёта нет ({exc}). "
            "«Уязвимостей не найдено» сегодня не утверждается."
        )
        return EXIT_NOT_RUN

    try:
        total = count_vulnerabilities(json.loads(raw))
    except (ValueError, TypeError) as exc:
        print(
            f"::warning title=pip-audit::аудит НЕ ОТРАБОТАЛ — отчёт нечитаем ({exc}). "
            "«Уязвимостей не найдено» сегодня не утверждается."
        )
        return EXIT_NOT_RUN

    if total:
        print(
            f"::warning title=pip-audit::найдено уязвимостей: {total} — "
            "см. лог job'а supply-chain и docs/dev/supply-chain.md"
        )
        return EXIT_VULNERABLE

    print("supply-chain: аудит отработал, уязвимостей в runtime-замыкании не найдено.")
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
