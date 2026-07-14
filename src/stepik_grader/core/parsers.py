"""parsers.py — утилиты разбора файлов тест-блоков.

Архитектурный слой: Infrastructure / Utility.
Не зависит от бизнес-логики grader.py или downloader.py.

Используется:
  - grader.py    — для разбора input.txt/output.txt при загрузке тест-кейсов
  - downloader.py — для подсчёта числа тест-кейсов в скачанных input.txt
"""

from __future__ import annotations

import re

__all__ = [
    "parse_testblock_file",
]


def parse_testblock_file(text: str) -> list[str]:
    """Разобрать input.txt/output.txt с маркерами блоков `# TEST_N:`.

    Возвращает список содержимого блоков (каждый .strip()).
    Строки `# INPUT DATA:` игнорируются.

    Пустые блоки СОХРАНЯЮТСЯ как `''` (например, `# TEST_5:` без данных),
    чтобы индексы input- и output-блоков оставались синхронными.
    """
    blocks: list[str] = []
    current_lines: list[str] = []
    in_block = False

    for line in text.splitlines():
        if re.match(r"#\s*TEST_\d+:", line.strip()):
            if in_block:
                blocks.append("\n".join(current_lines).strip())
                current_lines = []
            in_block = True
        elif line.strip().startswith("# INPUT DATA:"):
            continue
        elif in_block:
            current_lines.append(line)

    if in_block:
        blocks.append("\n".join(current_lines).strip())

    return blocks
