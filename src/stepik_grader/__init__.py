"""stepik_grader — локальный грейдер для курсов «Поколение Python» на Stepik.

src/-layout (Issue #35 / CLAUDE.md Sprint 8.2). Публичная точка входа:
``python -m stepik_grader.grader`` или консольная команда ``stepik-grader``
после ``pip install -e .`` (см. README.md).

``__version__`` — та же строка, что печатает ``--version``: версия
**динамическая, из git-тегов** (setuptools-scm), поэтому читается из метаданных
установленного дистрибутива, а не хранится здесь литералом (issue #996,
``PKG-1-06``).

Читается **лениво**, через ``__getattr__`` модуля (PEP 562), и это не
украшательство. Корневой ``__init__`` исполняется при ЛЮБОМ импорте пакета —
раньше ``python -m stepik_grader``, раньше чистки ``sys.path`` в ``__main__``.
Обычный ``import importlib.metadata`` на верхнем уровне тянет за собой
``json``, а файл ``json.py`` в каталоге задачи его перекрывает — и фикс
``INS-3-01`` оказывался бы отменён этим же файлом (поймано тестами
``test_entrypoint.py``). Ленивое чтение вычисляет версию только когда её
действительно спросили, то есть уже после чистки пути.
"""

from __future__ import annotations

from typing import Any

__all__ = ["__version__"]


def __getattr__(name: str) -> Any:
    """Ленивые атрибуты пакета — сейчас только ``__version__`` (PEP 562)."""
    if name != "__version__":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib.metadata

    # Значение совпадает с тем, что отдают ``cli._resolve_version`` и
    # ``launcher``: источник данных один — метаданные дистрибутива, — поэтому
    # три строчки чтения не создают трёх правд. Импортировать их отсюда нельзя:
    # это притащило бы в корневой ``__init__`` весь CLI ради номера версии.
    try:
        return importlib.metadata.version("stepik-python-grader")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover — нужен удалённый пакет
        # Спрашивать версию у неустановленного пакета — законный вопрос с
        # честным ответом, а не повод падать.
        return "0.0.0+unknown"
