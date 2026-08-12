"""tests_writer.py — запись скачанных тест-кейсов на диск (issue #302).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

Выделено из ``downloader.py`` (issue #302, SRP): два формата записи в
``tests/``, которыми пользуется downloader после извлечения кейсов из HTML/
ZIP/GitHub. Сеть и извлечение остаются в ``downloader.py``; сюда приходят уже
готовые данные (``str``), поэтому запись тестируется без моков сети.

Форматы (см. docs/use/configuration.md § Формат тест-кейсов):
  * Format 1 (Legacy) — ``save_tests``: по файлу на кейс — ``N``/``N.clue``
    (+ ``N.type`` для function-mode).
  * Format 3 (python-generation) — ``write_testblock_tests``: единые
    ``input.txt``/``output.txt`` с маркерами ``# TEST_N:``.
"""

from __future__ import annotations

import pathlib
import shutil
import warnings

__all__ = ["reset_tests_dir", "save_tests", "write_testblock_tests"]


def _write_text(path: pathlib.Path, text: str) -> None:
    """Записать текст кейса в UTF-8, пережив невалидные символы из сети (#838).

    Данные приходят из ответа API и из HTML страницы задачи, то есть их
    выбирает не пользователь. Одиночный суррогат (``\\ud800``) — валидная
    Python-строка после ``json.loads``, но невалидный UTF-8: строгая запись
    роняла всё скачивание ``UnicodeEncodeError`` из недр ``write_text``.
    Заменяем такие символы и предупреждаем: кейс, скорее всего, битый, но
    остальные задачи скачаются, а подмена не пройдёт молча.
    """
    try:
        path.write_text(text, encoding="utf-8")
    except UnicodeEncodeError:
        warnings.warn(
            f"{path.name}: в данных теста есть символы, не представимые в UTF-8 — "
            "они заменены на «?»; проверьте кейс перед прогоном.",
            UserWarning,
            stacklevel=2,
        )
        path.write_text(text, encoding="utf-8", errors="replace")


def reset_tests_dir(tests_dir: pathlib.Path) -> None:
    """Очистить содержимое ``tests/`` перед записью нового набора (issue #394).

    Перескачивание в существующую ``task_dir`` иначе оставляет устаревшие
    артефакты прошлого прогона: лишние ``N``/``N.clue`` при меньшем числе
    кейсов, а при смене формата — висящие Format-1 файлы поверх Format-3
    (автодетект отдаёт приоритет Format 1, и старый набор молча побеждает).
    Смешанный набор даёт тихий неверный вердикт.

    Чистим *содержимое*, а не сам узел ``tests/``: ``shutil.rmtree(tests_dir)``
    падал бы ``OSError`` на симлинке (POSIX) и ``PermissionError`` на
    заблокированном/read-only файле (Windows) — старый ``mkdir(exist_ok=True)``
    этого не делал, и обрывать скачивание из-за одного неудаляемого файла
    нельзя. Удаление каждого элемента — best-effort под ``OSError``.

    Публична (issue #942), потому что общий путь записи нужен и тем источникам,
    которые кладут файлы Format 3 байт-в-байт (GitHub-каталог с готовыми
    ``input.txt``/``output.txt``) и потому не проходят через
    :func:`write_testblock_tests`. Вызывать ПОСЛЕ получения всех данных: сброс
    до сети оставил бы каталог пустым при обрыве.
    """
    tests_dir.mkdir(parents=True, exist_ok=True)
    for entry in tests_dir.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            # Не роняем запись из-за одного неудаляемого файла (locked/read-only
            # на Windows, симлинк) — write_text ниже всё равно перезапишет по имени.
            pass


def save_tests(task_dir: pathlib.Path, tests: list[tuple[str, str, str]]) -> int:
    """Записывает тест-кейсы в tests/N, tests/N.clue, tests/N.type (Format 1).

    ``tests`` — список троек (input_data, expected_output, test_type), где
    ``test_type`` — "stdin" | "function". Возвращает количество сохранённых
    тестов. Каталог ``tests/`` очищается перед записью (issue #394).
    """
    tests_dir = task_dir / "tests"
    reset_tests_dir(tests_dir)
    for i, (input_data, expected, test_type) in enumerate(tests, start=1):
        _write_text(tests_dir / str(i), input_data)
        _write_text(tests_dir / f"{i}.clue", expected)
        if test_type == "function":
            _write_text(tests_dir / f"{i}.type", "function")
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

    Каталог ``tests/`` очищается перед записью (issue #394), чтобы висящие
    Format-1 файлы прошлого скачивания не перебивали свежий Format 3.
    """
    reset_tests_dir(tests_dir)
    input_lines = ["# INPUT DATA:\n"]
    output_lines = ["# OUTPUT DATA:\n"]
    for idx in sorted(pairs.keys()):
        inp_text, out_text = pairs[idx]
        input_lines.append(f"\n# TEST_{idx}:\n{inp_text}\n")
        output_lines.append(f"\n# TEST_{idx}:\n{out_text}\n")
    _write_text(tests_dir / "input.txt", "".join(input_lines))
    _write_text(tests_dir / "output.txt", "".join(output_lines))
    return len(pairs)
