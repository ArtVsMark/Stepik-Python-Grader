#!/usr/bin/env python3
"""scripts/window_capabilities.py — что это окно умеет НА САМОМ ДЕЛЕ (issue #1445).

`docs/agent/environments.md` перечисляет, какая работа где выполнима, и список
этот — снимок, а не константа: контейнер пересобирают, предустановленный набор
меняется, а строка в документе живёт годами и продолжает маршрутизировать
работу. Рядом там же стоял список команд перепроверки, и все они отвечали на
вопрос **«лежит ли»** (`which bwrap`, `ls /opt/pw-browsers`), а решает другой —
**«поедет ли»**. Разница не теоретическая, и обе стороны измерены в один заход:

* браузер объявлен и физически есть, но `pip install -e ".[e2e]"` ставит
  playwright, который ждёт другой билд Chromium, — прогон падает «Executable
  doesn't exist» с советом скачать браузер, а скачивание запрещено политикой
  сети. Снаружи это выглядит как «браузера здесь нет», и браузерная работа
  уезжает в другое окно по ложному основанию;
* песочница объявлена (`bwrap` в `/usr/bin`) и там её нет — но и вывод «здесь
  её нет» неверен: пакет ставится одной командой, ядро изоляцию разрешает, и
  после установки backend строится. Состояний, значит, три — **есть · нет ·
  достижимо одной командой**, — и дороже всего принять третье за второе.

Поэтому проверка не читает документ и ничего не сверяет с ним: она **пробует**
и печатает измеренное. Сверять было бы нечем — оба утверждения формально
проверялись бы «наличием файла», а именно наличие ни о чём не говорит.

Отчёт, а не гейт: у окон разные возможности по замыслу, и отсутствующая
песочница в облаке — свойство, а не поломка. Код возврата всегда ``0``; смысл
команды в том, чтобы ответ был измерен, а не вспомнен.

Запуск::

    python scripts/window_capabilities.py
    python scripts/window_capabilities.py --json
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import pathlib
import shutil
import sys
from collections.abc import Callable

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["PROBES", "Capability", "main", "measure"]

_ROOT = pathlib.Path(__file__).parent.parent

#: Билды, которые запускает НАШ e2e-набор. Реестр playwright перечисляет и
#: экспериментальные (``chromium-tip-of-tree``); требовать их значило бы
#: объявлять окно негодным из-за того, чем набор не пользуется, — гейт,
#: краснеющий на верном ответе.
_CHROMIUM_BUILDS = frozenset({"chromium", "chromium-headless-shell"})


class Capability:
    """Одна измеренная возможность окна."""

    __slots__ = ("detail", "name", "works")

    def __init__(self, name: str, works: bool, detail: str) -> None:
        self.name = name
        self.works = works
        self.detail = detail

    def __str__(self) -> str:
        mark = "да " if self.works else "нет"
        return f"[{mark}] {self.name}: {self.detail}"


def _probe_sandbox() -> Capability:
    """Изоляция уровня ОС: не «есть ли bwrap», а «строится ли runner»."""
    spec = importlib.util.find_spec("stepik_grader.core.sandbox")
    if spec is None:
        return Capability("песочница (--sandbox)", False, "пакет недоступен")
    from stepik_grader.core.sandbox import SandboxRunner, SandboxUnavailableError

    try:
        SandboxRunner()
    except SandboxUnavailableError as error:
        return Capability("песочница (--sandbox)", False, str(error).split(" — ")[0])
    except Exception as error:  # pragma: no cover — неожиданный отказ backend'а
        return Capability("песочница (--sandbox)", False, f"{type(error).__name__}: {error}")
    return Capability("песочница (--sandbox)", True, "backend построен")


def _probe_browser() -> Capability:
    """Браузер: не «лежит ли каталог», а «есть ли тот билд, которого ждёт playwright».

    Реестр билдов читается из самого пакета (``driver/package/browsers.json``), а
    драйвер не запускается: короткий старт-останов оставляет в stderr шум
    разборки цикла событий, который перехватом не убрать — он печатается уже на
    выходе интерпретатора. Отчёту он мешает: «нет» становится неотличимо от
    «сломалось».
    """
    if importlib.util.find_spec("playwright") is None:
        return Capability(
            "браузер (e2e)", False, 'playwright не установлен — pip install -e ".[e2e]"'
        )
    import os

    import playwright

    registry = pathlib.Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    try:
        wanted = {
            str(entry.get("name")): str(entry.get("revision"))
            for entry in json.loads(registry.read_text(encoding="utf-8")).get("browsers", [])
            if str(entry.get("name", "")) in _CHROMIUM_BUILDS
        }
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        return Capability("браузер (e2e)", False, f"реестр билдов нечитаем: {error}")
    if not wanted:
        return Capability("браузер (e2e)", False, "в реестре playwright нет chromium")

    # Пустая строка превращается в ``Path(".")``, а текущий каталог существует
    # всегда — отчёт врал бы «билдов нет в .» вместо «переменная не задана».
    raw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or ""
    if not raw.strip():
        return Capability("браузер (e2e)", False, "PLAYWRIGHT_BROWSERS_PATH не задан")
    where = pathlib.Path(raw)
    if not where.is_dir():
        return Capability(
            "браузер (e2e)", False, f"PLAYWRIGHT_BROWSERS_PATH указывает не в каталог: {where}"
        )
    missing = sorted(
        f"{name}-{revision}"
        for name, revision in wanted.items()
        if not (where / f"{name.replace('-', '_')}-{revision}").is_dir()
        and not (where / f"{name}-{revision}").is_dir()
    )
    if missing:
        return Capability(
            "браузер (e2e)",
            False,
            f"playwright ждёт {', '.join(missing)} — их нет в {where}, "
            "а скачивание запрещено политикой сети",
        )
    return Capability("браузер (e2e)", True, f"нужные билды на месте в {where}")


def _probe_display() -> Capability:
    """Графическая оболочка — нужна GUI-лаунчеру, но не headless-браузеру."""
    if importlib.util.find_spec("tkinter") is None:
        return Capability("графическая оболочка (лаунчер)", False, "tkinter отсутствует")
    import os

    if not os.environ.get("DISPLAY"):
        return Capability("графическая оболочка (лаунчер)", False, "DISPLAY не задан")
    return Capability("графическая оболочка (лаунчер)", True, "tkinter и DISPLAY на месте")


def _probe_secrets() -> Capability:
    """Настоящие учётные данные пользователя."""
    present = [name for name in ("secrets.json", "stepik_config.json") if (_ROOT / name).is_file()]
    if not present:
        return Capability("секреты рабочей папки", False, "ни одного файла нет")
    return Capability("секреты рабочей папки", True, ", ".join(present))


def _probe_git_tags() -> Capability:
    """Теги: без них версия неполна, и это норма для свежего клона."""
    git = shutil.which("git")
    if git is None:
        return Capability("теги git (версия)", False, "git недоступен")
    import subprocess

    try:
        out = subprocess.run(
            [git, "tag", "--list", "v*"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover
        return Capability("теги git (версия)", False, f"{type(error).__name__}: {error}")
    tags = [line for line in out.split("\n") if line.strip()]
    if not tags:
        return Capability("теги git (версия)", False, "клон без тегов — версия будет 0.0.N")
    return Capability("теги git (версия)", True, f"тегов: {len(tags)}")


#: Пробы в порядке печати. Каждая ЗАПУСКАЕТ то, о чём отвечает.
PROBES: tuple[Callable[[], Capability], ...] = (
    _probe_sandbox,
    _probe_browser,
    _probe_display,
    _probe_secrets,
    _probe_git_tags,
)


def measure() -> list[Capability]:
    """Измерить все возможности окна.

    Отказ одной пробы не должен уносить отчёт: «не знаю про остальные» хуже,
    чем «эта не работает».
    """
    found: list[Capability] = []
    for probe in PROBES:
        try:
            found.append(probe())
        except Exception as error:  # pragma: no cover — проба сама сломалась
            found.append(Capability(probe.__name__, False, f"проба упала: {error}"))
    return found


def main(argv: list[str] | None = None) -> int:
    """Всегда 0: это отчёт, а не гейт — у окон разные возможности по замыслу."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    found = measure()
    if args.json:
        print(
            json.dumps(
                [{"name": c.name, "works": c.works, "detail": c.detail} for c in found],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print("Возможности окна — измерено, а не прочитано:")
    for capability in found:
        print(f"  {capability}")
    print(f"\nРаботает: {sum(1 for c in found if c.works)} из {len(found)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
