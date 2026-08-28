#!/usr/bin/env python3
"""scripts/check_step_deadlines.py — у сетевого шага свой дедлайн, а не общий.

Правило 100 каталога: дедлайнов два — на работу и на **старт**. Ограничение
времени на выполнение старт не покрывает: зависнуть можно до первой полезной
строки, и там нужен свой, короткий предел.

В нашем конвейере роль «старта» играют шаги, которые ходят в сеть: установка
зависимостей, скачивание артефакта, клон каталога, установка браузера. У job'а
предел есть (`timeout-minutes`, issue #1271), но он общий и грубый: зависшая
установка съедает его целиком, и вместо «упала установка за три минуты»
получается «job превысил пятнадцать». Причина названа неверно, а очередь мержа
простояла впятеро дольше нужного — прецедент #1271 ровно такой: `e2e` встал на
установке Playwright и держал прогон три с половиной часа.

Проверка построчная, без PyYAML: тянуть зависимость в гейт ради нескольких
фактов незачем, а формат этих файлов свой и стабильный. Комментарии из
рассмотрения выброшены — слова «pip install» в объяснении соседнего шага не
делают сетевым шаг, который их упоминает.

Запуск::

    python scripts/check_step_deadlines.py
"""

from __future__ import annotations

import pathlib
import re
import sys

__all__ = ["NETWORK_MARKERS", "main", "steps_without_deadline"]

_ROOT = pathlib.Path(__file__).parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

#: По каким признакам шаг считается сетевым. Список закрытый: гадать по слову
#: «install» нельзя — установка из кэша сетью не является, а гейт, краснеющий на
#: половине шагов, отключают целиком.
NETWORK_MARKERS: tuple[str, ...] = (
    "pip install",
    "pip-audit",
    "playwright",
    "Playwright",
    "download-artifact",
    "git clone",
    "npm install",
)

_STEP_START = re.compile(r"^(?P<indent>\s*)- (?:name|uses):")


def _steps(source: str) -> list[tuple[str, list[str]]]:
    """Шаги файла как (заголовок, строки тела) — включая заголовочную."""
    lines = source.split("\n")
    found: list[tuple[str, list[str]]] = []
    index = 0
    while index < len(lines):
        match = _STEP_START.match(lines[index])
        if match is None:
            index += 1
            continue
        indent = match.group("indent")
        body = [lines[index]]
        index += 1
        while index < len(lines):
            current = lines[index]
            if _STEP_START.match(current) and current.startswith(f"{indent}- "):
                break
            if current.strip() and not current.startswith(indent + " "):
                break
            body.append(current)
            index += 1
        found.append((lines[index - len(body)].strip(), body))
    return found


def steps_without_deadline(sources: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Сетевые шаги без собственного ``timeout-minutes``.

    Args:
        sources: подмена содержимого для тестов: имя файла → текст.

    Returns:
        Пары (файл, заголовок шага).
    """
    if sources is None:
        sources = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(_WORKFLOWS.glob("*.yml"))
        }

    problems: list[tuple[str, str]] = []
    for name, source in sources.items():
        for title, body in _steps(source):
            meaningful = "\n".join(row for row in body if not row.lstrip().startswith("#"))
            if not any(marker in meaningful for marker in NETWORK_MARKERS):
                continue
            if "timeout-minutes:" not in meaningful:
                problems.append((name, title))
    return problems


def main() -> int:
    """0 — у каждого сетевого шага свой предел; 1 — нет."""
    problems = steps_without_deadline()
    if problems:
        print("сетевой шаг без собственного дедлайна:", file=sys.stderr)
        for name, title in problems:
            print(f"  • {name}: {title}", file=sys.stderr)
        print(
            "\nДедлайн job'а старт не покрывает: зависшая установка съест его целиком, "
            "и причина будет названа неверно («job превысил лимит» вместо «упала "
            "установка»). Поставьте шагу свой timeout-minutes.",
            file=sys.stderr,
        )
        return 1

    watched = sum(
        1
        for source in (path.read_text(encoding="utf-8") for path in _WORKFLOWS.glob("*.yml"))
        for _title, body in _steps(source)
        if any(marker in "\n".join(body) for marker in NETWORK_MARKERS)
    )
    print(f"у сетевых шагов есть свой дедлайн: шагов {watched}")
    return 0


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
