"""scripts/_require_python.py — точка входа отказывается работать не под своим интерпретатором.

issue #1507: системный ``python3`` в облачном контейнере — 3.11, а
``requires-python`` проекта — ``>=3.12``. Часть скриптов это переживает и
работает молча: ``gh_rest.py`` и ``check_pr_ready.py`` под 3.11 разбираются и
исполняются, потому что 3.12-синтаксиса в них не оказалось. «Работает, пока
везёт» — не то же самое, что «работает»: первая же конструкция новее 3.11
превращает молчаливую работу в отказ, а отказ выглядит поломкой скрипта, а не
несоответствием среды.

ФАЙЛ ОБЯЗАН РАЗБИРАТЬСЯ СТАРЫМИ ИНТЕРПРЕТАТОРАМИ. Это не стилистическая
вольность, а условие работоспособности: сказать «интерпретатор не тот» можно
только тем интерпретатором, который сумел прочитать сам файл. Отсюда запрет на
``def f[T]`` (PEP 695) и на ``type X = ...`` здесь и в каждой точке входа,
которая этот модуль зовёт, — иначе ``SyntaxError`` случится при разборе, то
есть РАНЬШЕ проверки, и вместо объяснения человек получит номер строки.

Требование читается из ``pyproject.toml``, а не хранится константой: поднимется
минимум — отказ поднимется вместе с ним, без правки здесь.

НЕТ ТРЕБОВАНИЯ — НЕТ И ОТКАЗА. Не нашли ``requires-python`` — молчим: гейт,
краснеющий на отсутствии данных, обходят, а он должен помогать.
"""

from __future__ import annotations

import pathlib
import re
import sys

__all__ = [
    "EXIT_WRONG_PYTHON",
    "minimum_version",
    "require",
]

#: Код возврата «запущено не тем интерпретатором».
#:
#: Именно свой, а не ``1`` и не ``2``: единица означает «проверка не прошла»
#: (то есть скрипт отработал и вынес вердикт), двойка у ``gh_rest.py`` — «ждать,
#: квота исчерпана». Здесь же вердикта нет вовсе — работа не начиналась.
EXIT_WRONG_PYTHON = 3

_REQUIRES_RE = re.compile(
    r"^\s*requires-python\s*=\s*[\"'](?P<spec>[^\"']+)[\"']",
    re.MULTILINE,
)
_MINIMUM_RE = re.compile(r">=\s*(?P<version>\d+(?:\.\d+)*)")


def minimum_version(root: pathlib.Path) -> tuple[int, ...] | None:
    """Нижняя граница ``requires-python`` из ``pyproject.toml``.

    Разбирается только нижняя граница: верхней у проекта нет, а полный разбор
    спецификаторов PEP 440 потребовал бы зависимости — ради одного числа,
    которое в файле записано открытым текстом.

    Файла нет, ключа нет, граница не названа — ``None``.
    """
    try:
        source = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    requires = _REQUIRES_RE.search(source)
    if requires is None:
        return None
    minimum = _MINIMUM_RE.search(requires.group("spec"))
    if minimum is None:
        return None
    return tuple(int(part) for part in minimum.group("version").split("."))


def require(tool: str, root: pathlib.Path | None = None) -> None:
    """Прекратить работу, если интерпретатор ниже требуемого проектом.

    Args:
        tool: чем представиться в сообщении — имя вызвавшего скрипта.
        root: корень проекта; по умолчанию — родитель каталога ``scripts``.

    Raises:
        SystemExit: с кодом :data:`EXIT_WRONG_PYTHON`, если версия ниже.
    """
    base = root if root is not None else pathlib.Path(__file__).resolve().parent.parent
    minimum = minimum_version(base)
    if minimum is None:
        return
    if sys.version_info[: len(minimum)] >= minimum:
        return

    running = ".".join(str(part) for part in sys.version_info[:3])
    wanted = ".".join(str(part) for part in minimum)
    print(
        f"{tool}: запущено под Python {running}, а проект требует {wanted}+ "
        f"(requires-python в pyproject.toml).\n"
        f"  Это среда, а не поломка скрипта: под неподходящим интерпретатором "
        f"часть проверок молча не выполняется, а часть падает разбором.\n"
        f'  Чем починить:  python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"\n'
        f"  Дальше звать через .venv/bin/python, а не через системный python3.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_WRONG_PYTHON)
