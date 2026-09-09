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
import re
import shutil
import subprocess
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

#: Сколько ждём ответа `--version`. Запуск браузера с этим флагом печатает
#: строку и выходит; порог отличает «не отвечает» от «система под нагрузкой».
_VERSION_TIMEOUT_S = 20.0


class Capability:
    """Одна измеренная возможность окна.

    ``substituted`` — четвёртое состояние, которого прежде не было (issue
    #1508). Прежние три отвечали «есть · нет · достижимо одной командой»; это —
    **есть, но не то**: работа пойдёт, а прогон получится не тот, что на
    раннере. Зелёный результат такого прогона поломку не подтверждает, поэтому
    состояние обязано быть видно в отчёте, а не растворяться в «да».
    """

    __slots__ = ("detail", "name", "substituted", "works")

    def __init__(self, name: str, works: bool, detail: str, substituted: bool = False) -> None:
        self.name = name
        self.works = works
        self.detail = detail
        self.substituted = substituted

    def __str__(self) -> str:
        if self.substituted:
            mark = "~~ "
        else:
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


#: Где внутри каталога билда лежит исполняемый файл — по одному варианту на
#: платформу и на вид сборки. Полного соответствия не требуется: не нашли —
#: значит версию не прочитали, и это отдельный исход, а не «версия сошлась».
_BROWSER_BINARIES = (
    "chrome-linux/chrome",
    "chrome-linux/headless_shell",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chrome-mac/headless_shell",
    "chrome-win/chrome.exe",
    "chrome-win/headless_shell.exe",
)

#: Из ``Chromium 141.0.7390.37`` нужна только сама версия.
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


def _build_dir(where: pathlib.Path, name: str, revision: str) -> pathlib.Path | None:
    """Каталог билда: playwright пишет имя и с дефисом, и с подчёркиванием."""
    for candidate in (f"{name.replace('-', '_')}-{revision}", f"{name}-{revision}"):
        build = where / candidate
        if build.is_dir():
            return build
    return None


def installed_browser_version(build_dir: pathlib.Path) -> str | None:
    """Версия НАЛИЧНОГО билда — спросить у самого бинаря; не вышло — ``None``.

    Каталог называется так, как его назвали: имя ``chromium-<ревизия>`` ничего
    не доказывает о содержимом, а подстановка каталога — рекомендованный здесь
    приём обхода закрытой сети. Поэтому версию спрашивают у исполняемого файла.
    """
    for relative in _BROWSER_BINARIES:
        binary = build_dir / relative
        if not binary.is_file():
            continue
        try:
            done = subprocess.run(  # argv собран здесь, оболочка не участвует
                [str(binary), "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_VERSION_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        found = _VERSION_RE.search(done.stdout or "")
        return found.group(1) if found else None
    return None


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
            str(entry.get("name")): (
                str(entry.get("revision")),
                str(entry.get("browserVersion") or ""),
            )
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
    missing: list[str] = []
    substituted: list[str] = []
    for name, (revision, expected) in sorted(wanted.items()):
        build = _build_dir(where, name, revision)
        if build is None:
            missing.append(f"{name}-{revision}")
            continue
        # issue #1508: имя каталога — не доказательство содержимого. Подстановка
        # каталога («нужный билд» → предустановленный) прямо рекомендована в
        # docs/agent/environments.md как единственный способ запустить e2e при
        # закрытой сети, и после неё проверка по имени отвечала «на месте», а
        # прогон шёл на браузере другой мажорной версии.
        installed = installed_browser_version(build)
        if installed and expected and installed != expected:
            substituted.append(f"{name}: {installed} вместо {expected}")

    if missing:
        return Capability(
            "браузер (e2e)",
            False,
            f"playwright ждёт {', '.join(missing)} — их нет в {where}, "
            "а скачивание запрещено политикой сети",
        )
    if substituted:
        return Capability(
            "браузер (e2e)",
            True,
            f"билды подменены ({'; '.join(substituted)}) — e2e отсюда пойдёт, но "
            "покажет поломку, а не подтвердит её отсутствие: на раннере другой "
            "браузер, и зелёный прогон здесь про него ничего не говорит",
            substituted=True,
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
                [
                    {
                        "name": c.name,
                        "works": c.works,
                        "detail": c.detail,
                        "substituted": c.substituted,
                    }
                    for c in found
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print("Возможности окна — измерено, а не прочитано:")
    for capability in found:
        print(f"  {capability}")
    print(f"\nРаботает: {sum(1 for c in found if c.works)} из {len(found)}.")
    # issue #1508: подменённое считается работающим — оно и работает. Но счёт
    # «работает N из M» на этот счёт молчит, а разница между «проверено» и
    # «проверено не тем» решает, можно ли ссылаться на здешний зелёный прогон.
    swapped = [c for c in found if c.substituted]
    if swapped:
        print(
            "\nВНИМАНИЕ: "
            + "; ".join(c.name for c in swapped)
            + " — работает подменой. Красный результат здесь настоящий, зелёный "
            "ничего не подтверждает: у раннера другая среда."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
