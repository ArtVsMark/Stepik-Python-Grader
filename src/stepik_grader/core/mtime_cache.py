"""mtime_cache.py — generic mtime-инвалидируемый кеш загрузки (issue #345).

Архитектурный слой: Infrastructure / Utilities (leaf — только stdlib).

Вынесено из ``web/glossary_adapter.py`` (issue #339), чтобы переиспользовать
единый механизм в провайдерах glossary и rules, не копипастя его (слабое место
№4 аудита глоссария). Идея: распарсить источник один раз и держать в памяти,
перечитывая только при смене mtime его файлов — правки store подхватываются, а
read-only bundled-база после первой загрузки стабильна.

Потребители не должны мутировать возвращённое значение (кеш шарит один объект
между вызовами/потоками ``ThreadingHTTPServer`` — гонка на пересчёт
идемпотентна).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

__all__ = ["MtimeCache", "mtime_signature"]


def mtime_signature(files: list[Path]) -> float:
    """Подпись источника — максимальный mtime его файлов (``0.0`` при недоступности)."""
    try:
        return max((f.stat().st_mtime for f in files), default=0.0)
    except OSError:
        return 0.0


class MtimeCache[T]:
    """Кеш одного загружаемого значения, инвалидируемый по mtime исходных файлов.

    Ключ (``str``) разделяет несколько источников в одном кеше (как раньше
    ``_CARDS_CACHE`` в glossary_adapter). ``get_or_load`` перечитывает значение
    только если mtime ``files`` изменился с прошлой загрузки.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, T]] = {}

    def get_or_load(
        self,
        key: str,
        files: list[Path],
        load: Callable[[], T],
        *,
        on_error: type[Exception] | tuple[type[Exception], ...] = Exception,
    ) -> T | None:
        """Вернуть кешированное значение или загрузить заново при смене mtime.

        ``load`` вызывается только при промахе/устаревании. Если он бросил
        исключение из ``on_error`` (по умолчанию любое), возвращается ``None``
        — вызывающий переходит к следующему уровню fallback, а мусор не
        кешируется. Прочие исключения пробрасываются.
        """
        sig = mtime_signature(files)
        hit = self._store.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        try:
            value = load()
        except on_error:
            return None
        self._store[key] = (sig, value)
        return value

    def clear(self) -> None:
        """Сбросить кеш (для тестов и явной инвалидации)."""
        self._store.clear()
