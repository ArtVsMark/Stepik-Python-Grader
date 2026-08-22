#!/usr/bin/env python3
"""scripts/check_ruff_pin.py — версии инструментов вердикта пришпилены (#791, #1349).

**Инструмент вердикта** — тот, чей выход о НЕИЗМЕННОМ коде меняется вместе с его
версией: ``ruff`` (новое правило) и ``mypy`` (новая диагностика). Такой
инструмент без верхней границы означает ложное красное у того, кто пересоздал
окружение в день релиза, — и разбирают его руками, не понимая причины.
Прогонные инструменты (``pytest`` и соседи) сюда не входят: почему — записано
рядом со списком в ``pyproject.toml``.

**Имя файла оставлено прежним намеренно.** Указатель правил
``docs/agent/rules/README.md`` генерируется из следов каталога
claude-code-playbook, и след правила 073 указывает на этот путь. Переименование
уронило бы генератор, а починить след можно только в каталоге — то есть в чужом
репозитории (``CLAUDE.md`` § Связанный проект). Неточное имя дешевле сломанного
механизма.

Раньше её задавали двое: `rev:` хука в `.pre-commit-config.yaml` и спецификатор
`ruff>=...` в `pyproject.toml`. Синхронизация была ручной — и разъехалась ровно
так, как предупреждал комментарий в самом конфиге: в окружении и CI встал ruff
0.16, а хук продолжал править код правилами 0.15. Локальный «pre-commit прошёл»
переставал что-либо говорить о том, будет ли зелёным CI.

Проверяется три вещи:

1. Хуки ruff в `.pre-commit-config.yaml` — `language: system`, то есть берут
   ruff из окружения. Возврат к репозиторию `ruff-pre-commit` заводит второй
   источник версии, и всё повторится.
2. Спецификатор **каждого** инструмента вердикта в
   `[project.optional-dependencies] dev` имеет **верхнюю границу**. Без неё CI
   ставит свежайший в день его выхода, а у контрибьютора остаётся вчерашний —
   то же расхождение, только растянутое во времени.
3. Установленная версия (если инструмент есть в окружении) этому спецификатору
   удовлетворяет. Это ловит устаревшую dev-установку до того, как она даст
   ложное «всё чисто».

Чистый stdlib + `packaging` (уже в зависимостях setuptools/pytest-цепочки), без
YAML-парсера: конфиг pre-commit читается построчно — структура файла плоская и
стабильная, а лишняя зависимость ради трёх строк не окупается.

Запуск::

    python scripts/check_ruff_pin.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

__all__ = [
    "VERDICT_TOOLS",
    "VerdictTool",
    "installed_ruff_version",
    "installed_version",
    "main",
    "precommit_violations",
    "ruff_requirement",
    "specifier_violations",
    "tool_requirement",
]


@dataclass(frozen=True)
class VerdictTool:
    """Инструмент, чей вердикт о неизменном коде зависит от его версии."""

    name: str
    """Имя зависимости в ``[project.optional-dependencies] dev``."""

    module: str
    """Модуль для ``python -m <module> --version``."""

    version_pattern: str
    """Регулярное выражение с одной группой — версией в выводе ``--version``."""

    @property
    def version_re(self) -> re.Pattern[str]:
        """Скомпилированный шаблон версии."""
        return re.compile(self.version_pattern, re.MULTILINE)


#: Список пришпиливаемого. Расширяется, когда появляется ещё один инструмент,
#: меняющий вердикт вместе с версией; прогонным инструментам здесь не место.
VERDICT_TOOLS: tuple[VerdictTool, ...] = (
    VerdictTool("ruff", "ruff", r"^ruff (\d+\.\d+\.\d+)"),
    # mypy печатает «mypy 2.3.1 (compiled: yes)»; патч-часть бывает опущена.
    VerdictTool("mypy", "mypy", r"^mypy (\d+\.\d+(?:\.\d+)?)"),
)

_ROOT = Path(__file__).resolve().parent.parent
_PRECOMMIT = _ROOT / ".pre-commit-config.yaml"
_PYPROJECT = _ROOT / "pyproject.toml"

# Репозиторий, чей `rev:` и был вторым источником версии.
_RUFF_HOOK_REPO = "ruff-pre-commit"


def precommit_violations(text: str) -> list[str]:
    """Нарушения в тексте ``.pre-commit-config.yaml`` (пустой список — чисто)."""
    problems = []
    if _RUFF_HOOK_REPO in text:
        problems.append(
            f".pre-commit-config.yaml ссылается на {_RUFF_HOOK_REPO}: это второй источник "
            "версии ruff вдобавок к pyproject.toml. Используйте local-хук с "
            "`language: system` (issue #791)."
        )
    if "id: ruff-check" not in text or "id: ruff-format" not in text:
        problems.append(
            ".pre-commit-config.yaml потерял хук ruff-check или ruff-format — "
            "локальный гейт перестал зеркалить CI."
        )
    return problems


def tool_requirement(tool: VerdictTool) -> Requirement:
    """Спецификатор инструмента из ``[project.optional-dependencies] dev``."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    for raw in data["project"]["optional-dependencies"]["dev"]:
        req = Requirement(raw)
        if req.name == tool.name:
            return req
    raise SystemExit(f"pyproject.toml: в extra [dev] нет зависимости {tool.name}")


def installed_version(tool: VerdictTool) -> Version | None:
    """Версия инструмента в текущем окружении; ``None`` — не установлен."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", tool.module, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout
    except OSError:
        return None
    match = tool.version_re.search(out)
    return Version(match.group(1)) if match else None


def ruff_requirement() -> Requirement:
    """Спецификатор ``ruff`` — обёртка над :func:`tool_requirement`."""
    return tool_requirement(VERDICT_TOOLS[0])


def installed_ruff_version() -> Version | None:
    """Версия ruff в окружении — обёртка над :func:`installed_version`."""
    return installed_version(VERDICT_TOOLS[0])


def specifier_violations(req: Requirement, installed: Version | None) -> list[str]:
    """Нарушения: нет верхней границы / установлено не то, что объявлено."""
    problems = []
    if not any(spec.operator in ("<", "<=", "==", "~=") for spec in req.specifier):
        problems.append(
            f"pyproject.toml: спецификатор `{req}` без верхней границы — CI поставит "
            f"свежайший {req.name} в день релиза, а у контрибьютора останется прежний "
            "(issue #791, #1349)."
        )
    if installed is not None and installed not in req.specifier:
        problems.append(
            f"установлен {req.name} {installed}, что не удовлетворяет `{req}` — "
            'обновите окружение: pip install -e ".[dev]"'
        )
    return problems


def main() -> int:
    """Вернуть 0, если версия ruff задана в одном месте; иначе 1 и отчёт."""
    # Windows-консоль по умолчанию cp1252 и кириллицу в выводе не кодирует —
    # без этого шаг падает UnicodeEncodeError на windows-latest, то есть гард
    # краснеет не по существу (так и случилось на первом же прогоне CI). Тот же
    # приём, что в check_contrast.py.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):  # зависит от платформы stdout
            reconfigure(encoding="utf-8")

    problems = precommit_violations(_PRECOMMIT.read_text(encoding="utf-8"))
    reports = []
    for tool in VERDICT_TOOLS:
        req = tool_requirement(tool)
        installed = installed_version(tool)
        problems += specifier_violations(req, installed)
        where = f"установлен {installed}" if installed else "в окружении не найден"
        reports.append(f"  {tool.name}: `{req}` ({where})")

    if problems:
        print("FAIL: пин инструментов вердикта разъехался:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("Пины инструментов вердикта — единственный источник pyproject.toml:")
    for line in reports:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
