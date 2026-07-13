"""tests_writer.py — запись скачанных тест-кейсов на диск (issue #302).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Выделено из ``downloader.py`` (issue #302, SRP): два формата записи в
``tests/``, которыми пользуется downloader после извлечения кейсов из HTML/
ZIP/GitHub. Сеть и извлечение остаются в ``downloader.py``; сюда приходят уже
готовые данные (``str``), поэтому запись тестируется без моков сети.

Форматы (см. docs/configuration.md § Формат тест-кейсов):
  * Format 1 (Legacy) — ``save_tests``: по файлу на кейс — ``N``/``N.clue``
    (+ ``N.type`` для function-mode).
  * Format 3 (python-generation) — ``write_testblock_tests``: единые
    ``input.txt``/``output.txt`` с маркерами ``# TEST_N:``.
"""

from __future__ import annotations

import pathlib

__all__ = ["save_tests", "write_testblock_tests"]


def save_tests(task_dir: pathlib.Path, tests: list[tuple[str, str, str]]) -> int:
    """Записывает тест-кейсы в tests/N, tests/N.clue, tests/N.type (Format 1).

    ``tests`` — список троек (input_data, expected_output, test_type), где
    ``test_type`` — "stdin" | "function". Возвращает количество сохранённых
    тестов.
    """
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for i, (input_data, expected, test_type) in enumerate(tests, start=1):
        (tests_dir / str(i)).write_text(input_data, encoding="utf-8")
        (tests_dir / f"{i}.clue").write_text(expected, encoding="utf-8")
        if test_type == "function":
            (tests_dir / f"{i}.type").write_text("function", encoding="utf-8")
    return len(tests)


def write_testblock_tests(tests_dir: pathlib.Path, pairs: dict[int, tuple[str, str]]) -> int:
    """Записывает tests/input.txt + tests/output.txt с маркерами # TEST_N (Format 3).

    ``pairs`` — словарь ``{номер_теста: (input_text, output_text)}`` с уже
    декодированными и очищенными от хвостовых ``\\n`` текстами (декодирование
    из ZIP-байтов / скачивание с GitHub — забота вызывающей стороны в
    downloader.py, issue #302). Блоки пишутся в порядке возрастания номера.
    Возвращает количество кейсов (``len(pairs)``).

    Дедуплицирует общий код построения Format 3, который раньше был скопирован
    в ``_download_zip_tests`` и ``_download_github_tests`` — байт-в-байт тот же
    формат, что и до issue #302 (заголовок ``# INPUT DATA:``/``# OUTPUT DATA:``,
    пустая строка + ``# TEST_N:`` + текст + перевод строки на кейс).
    """
    tests_dir.mkdir(parents=True, exist_ok=True)
    input_lines = ["# INPUT DATA:\n"]
    output_lines = ["# OUTPUT DATA:\n"]
    for idx in sorted(pairs.keys()):
        inp_text, out_text = pairs[idx]
        input_lines.append(f"\n# TEST_{idx}:\n{inp_text}\n")
        output_lines.append(f"\n# TEST_{idx}:\n{out_text}\n")
    (tests_dir / "input.txt").write_text("".join(input_lines), encoding="utf-8")
    (tests_dir / "output.txt").write_text("".join(output_lines), encoding="utf-8")
    return len(pairs)
