"""Изоляция базы истории держится и МЕЖДУ тестами (#1169).

Проверяется не продуктовый код, а фикстуры самого набора: ``conftest.py``
уводит ``STEPIK_GRADER_HISTORY_DB`` во временный каталог, но per-test
``monkeypatch`` откатывается на границе теста. В получавшемся окне
``default_history_db_path()`` резолвился в настоящую
``~/.stepik-grader/history.db`` — и поток, переживший свой тест (web-слой
гоняет проверки в пуле, ``web/runs.py``), создавал каталог в домашней папке
разработчика.

Ловится это только потоком: сам тест окна не видит — оно открывается ПОСЛЕ
того, как тело теста отработало. Поэтому проверка растянута на два теста:
первый запускает наблюдателя, второй снимает показания. Порядок внутри файла
pytest сохраняет; запуск одного лишь ``test_...report`` даст пустой список и
пройдёт — ложно-зелёный здесь безопаснее ложно-красного.
"""

from __future__ import annotations

import threading
import time

from stepik_grader.core import history_recording

_HITS: list[str] = []
_STOP = threading.Event()
# Три границы теста при опросе без пауз: окно узкое (микросекунды между
# откатом одной фикстуры и установкой следующей), поэтому наблюдатель не спит
# вовсе, а запас по числу границ компенсирует планировщик.
_WATCH_SECONDS = 0.9


def _watch() -> None:
    """Резолвить путь к базе, пока идут соседние тесты; помнить попадания в пользовательскую.

    Сравнение точечное — именно с ``user_history_db_path()``, а не «где-то под
    домашней папкой»: на Windows временный каталог pytest сам лежит внутри
    ``C:\\Users\\<user>\\AppData\\Local\\Temp``, и широкая проверка объявляла бы
    исправную изоляцию нарушением. Guard ``_no_filesystem_pollution`` ловит
    ровно этот путь — на него и смотрим.
    """
    real = history_recording.user_history_db_path().resolve()
    deadline = time.monotonic() + _WATCH_SECONDS
    while not _STOP.is_set() and time.monotonic() < deadline:
        if history_recording.default_history_db_path().resolve() == real:
            _HITS.append(str(real))


def test_1_watcher_starts() -> None:
    """Наблюдатель переживает границу теста — как поток job'ы в web/runs.py."""
    threading.Thread(target=_watch, daemon=True).start()
    time.sleep(0.25)


def test_2_time_passes() -> None:
    time.sleep(0.25)


def test_3_time_passes_again() -> None:
    time.sleep(0.25)


def test_4_home_was_never_resolved() -> None:
    """Ни на одной границе путь не увёл в домашнюю папку.

    Красный до фикса: без сессионной страховки в ``conftest.py`` наблюдатель
    ловил ``~/.stepik-grader/history.db`` — тот самый путь, чьё появление
    роняло guard ``_no_filesystem_pollution`` на macOS и Windows, каждый раз в
    другом тесте ``test_runs.py``.
    """
    _STOP.set()

    assert not _HITS, (
        f"путь к базе истории уводил в пользовательскую базу {_HITS[0]} на границе "
        f"теста ({len(_HITS)} раз). Изоляция из conftest.py действует только внутри "
        "теста — нужен слой, переживающий её откат."
    )
