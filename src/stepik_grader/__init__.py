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

import sys
from typing import Any

__all__ = ["__version__"]

# ---------------------------------------------------------------------------
# Отказ словами под старым Python (issue #1570, эпик #1575).
#
# Этот файл исполняется ПЕРВЫМ при любом входе в пакет: консольные команды,
# ``python -m stepik_grader.*``, лаунчер, pytest-плагин. После подъёма планки
# код пакета перестаёт разбираться прежними интерпретаторами, и уже стоящий
# ``.venv`` на 3.12/3.13 (editable-установка, ``git pull``) падал бы
# ``SyntaxError`` с номером строки — поломкой грейдера, а не «обновите Python».
#
# ПОЭТОМУ ФАЙЛ ОБЯЗАН РАЗБИРАТЬСЯ СТАРЫМИ ИНТЕРПРЕТАТОРАМИ: ruff держит его
# per-file целью ``py39``, шаг ``static`` в CI компилирует его 3.11 и 3.12.
#
# Планка — константа, а не чтение метаданных: здесь нельзя импортировать
# ``importlib.metadata`` (он тянет ``json``, а ``json.py`` в каталоге задачи
# его перекрывает — см. докстринг модуля). Расходиться с ``requires-python``
# ей не даёт тест ``test_python_floor.py``.
# ---------------------------------------------------------------------------

#: Планка ``requires-python`` из ``pyproject.toml``.
_PYTHON_FLOOR = (3, 14)
#: Код возврата «запущено не тем интерпретатором» — тот же, что у гейтов
#: (``scripts/_require_python.py``), чтобы обёртки различали его одинаково.
_EXIT_WRONG_PYTHON = 3


def _wrong_python_message(current: tuple, executable: str) -> str:
    """Что сказать человеку, запустившему грейдер не тем Python."""
    need = ".".join(str(part) for part in _PYTHON_FLOOR)
    have = ".".join(str(part) for part in current[:3])
    return (
        f"Грейдеру нужен Python {need} или новее, а запущен Python {have}\n"
        f"({executable}).\n"
        "\n"
        "Окружение собрано на старой версии — пересоздайте его в каталоге проекта:\n"
        f"    python{need} -m venv .venv --clear\n"
        '    .venv/bin/pip install -e ".[dev]"      (Windows: .venv\\Scripts\\pip ...)\n'
        "\n"
        "Остаться на Python 3.12–3.13 можно с выпуском 1.12 — последним, который\n"
        'их поддерживает: pip install "stepik-python-grader<1.13"\n'
    )


def _refuse_old_python() -> None:
    """Сообщить и выйти. Без консоли (``pythonw``) — окном, а не в никуда."""
    message = _wrong_python_message(tuple(sys.version_info), sys.executable)
    if sys.stderr is not None:
        sys.stderr.write(message)
    else:  # pragma: no cover — нужен pythonw: лаунчер на Windows
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("Stepik Python Grader", message)
            root.destroy()
        except Exception:  # окна нет: сказать больше некуда
            pass
    raise SystemExit(_EXIT_WRONG_PYTHON)


if sys.version_info < _PYTHON_FLOOR:
    _refuse_old_python()


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
