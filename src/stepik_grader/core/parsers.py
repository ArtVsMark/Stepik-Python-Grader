"""parsers.py — утилиты разбора файлов тест-блоков.

Архитектурный слой: Infrastructure / Utility.
Не зависит от бизнес-логики grader.py или downloader.py.

Используется:
  - grader.py    — для разбора input.txt/output.txt при загрузке тест-кейсов
  - downloader.py — для подсчёта числа тест-кейсов в скачанных input.txt
"""

from __future__ import annotations

import re

from stepik_grader.core.normalizers import split_output_lines

__all__ = [
    "parse_testblock_file",
]


def _finish_block(lines: list[str]) -> str:
    """Собрать содержимое блока, срезав по краям только строки-отбивку.

    Отбивка — пустая строка или строка из одних пробелов: в формате 3 граница
    блока задаётся маркером `# TEST_N:`, поэтому отделяющая пустая строка
    неотличима от данных и режется. А вот пробелы ВНУТРИ непустой строки —
    значимые данные: ожидаемый вывод задачи с выравниванием (ёлочка, таблица)
    состоит из них целиком. Прежний ``.strip()`` всего блока срезал и их, из-за
    чего верное решение получало WA (issue #783).

    Блок целиком из отбивки даёт ``''`` — как ``# TEST_5:`` без данных.
    """
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def parse_testblock_file(text: str) -> list[str]:
    """Разобрать input.txt/output.txt с маркерами блоков `# TEST_N:`.

    Возвращает список содержимого блоков; у каждого срезаны строки-отбивка по
    краям, значимые пробелы внутри строк сохранены (см. ``_finish_block``).

    Строка-заголовок `# INPUT DATA:` (её пишет `write_testblock_tests` первой,
    ДО первого `# TEST_N:`) пропускается только ВНЕ блока. Внутри открытого
    блока такая же строка — реальные данные кейса и СОХРАНЯЕТСЯ (issue #410, B6).

    Пустые блоки СОХРАНЯЮТСЯ как `''` (например, `# TEST_5:` без данных),
    чтобы индексы input- и output-блоков оставались синхронными.
    """
    blocks: list[str] = []
    current_lines: list[str] = []
    in_block = False

    # issue #843: разбор по настоящим переводам строки — тем же правилом, что и
    # обе стороны сравнения. С `splitlines()` управляющий символ внутри данных
    # (VT, NEL, U+2028) разрывал строку блока, и ожидание формата 3 теряло его,
    # расходясь с фактическим выводом решения.
    for line in split_output_lines(text):
        if re.match(r"#\s*TEST_\d+:", line.strip()):
            if in_block:
                blocks.append(_finish_block(current_lines))
                current_lines = []
            in_block = True
        elif not in_block and line.strip().startswith("# INPUT DATA:"):
            # issue #410 (B6): `# INPUT DATA:` — файловый заголовок (до первого
            # `# TEST_N:`), пропускаем только ВНЕ блока. Внутри открытого блока
            # такая строка — реальные данные кейса, иначе они бы терялись.
            continue
        elif in_block:
            current_lines.append(line)

    if in_block:
        blocks.append(_finish_block(current_lines))

    return blocks
