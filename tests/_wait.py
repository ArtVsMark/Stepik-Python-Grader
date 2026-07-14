"""wait_until — детерминированное ожидание условия для тестов async web-слоя.

Заменяет фиксированные ``time.sleep(...)`` паузы в тестах job-модели/песочницы
(issue #357): вместо «поспать N мс и надеяться, что воркер успел» — опрашивать
условие до дедлайна. Быстрее (возврат сразу по выполнении) и не флейкует на
медленных CI-агентах. Аналог браузерного ``wait_for_selector`` из e2e, но для
Python-состояния (статус job'ы, наличие процесса и т.п.).

НЕ трогает ``time.sleep`` внутри тел решений-фикстур — там паузы намеренные
(например, TLE-кейсы ``time.sleep(30)``).
"""

from __future__ import annotations

import time
from collections.abc import Callable

__all__ = ["wait_until"]


def wait_until[T](
    predicate: Callable[[], T], *, timeout: float = 10.0, interval: float = 0.01
) -> T:
    """Опрашивать ``predicate()`` до truthy-значения или дедлайна; вернуть последнее.

    Возвращается немедленно, как только ``predicate()`` даёт truthy-значение
    (его и возвращает — удобно вернуть сам объект, а не только флаг). Если за
    ``timeout`` секунд условие не выполнилось — возвращает последнее (falsy)
    значение как есть: вызывающий ассертит его и падает с осмысленным
    сообщением вместо немого таймаута.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            return value
        time.sleep(interval)
