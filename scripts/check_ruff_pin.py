#!/usr/bin/env python3
"""scripts/check_ruff_pin.py — версия ruff задана ровно в одном месте (issue #791).

Раньше её задавали двое: `rev:` хука в `.pre-commit-config.yaml` и спецификатор
`ruff>=...` в `pyproject.toml`. Синхронизация была ручной — и разъехалась ровно
так, как предупреждал комментарий в самом конфиге: в окружении и CI встал ruff
0.16, а хук продолжал править код правилами 0.15. Локальный «pre-commit прошёл»
переставал что-либо говорить о том, будет ли зелёным CI.

Проверяется три вещи:

1. Хуки ruff в `.pre-commit-config.yaml` — `language: system`, то есть берут
   ruff из окружения. Возврат к репозиторию `ruff-pre-commit` заводит второй
   источник версии, и всё повторится.
2. Спецификатор в `[project.optional-dependencies] dev` имеет **верхнюю
   границу**. Без неё CI ставит свежайший ruff в день его выхода, а у
   контрибьютора остаётся вчерашний — то же расхождение, только растянутое во
   времени.
3. Установленный ruff (если он есть в окружении) этому спецификатору
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
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

__all__ = [
    "installed_ruff_version",
    "main",
    "precommit_violations",
    "ruff_requirement",
    "specifier_violations",
]

_ROOT = Path(__file__).resolve().parent.parent
_PRECOMMIT = _ROOT / ".pre-commit-config.yaml"
_PYPROJECT = _ROOT / "pyproject.toml"

# Репозиторий, чей `rev:` и был вторым источником версии.
_RUFF_HOOK_REPO = "ruff-pre-commit"
_VERSION_RE = re.compile(r"^ruff (\d+\.\d+\.\d+)", re.MULTILINE)


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


def ruff_requirement() -> Requirement:
    """Спецификатор ``ruff`` из ``[project.optional-dependencies] dev``."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    for raw in data["project"]["optional-dependencies"]["dev"]:
        req = Requirement(raw)
        if req.name == "ruff":
            return req
    raise SystemExit("pyproject.toml: в extra [dev] нет зависимости ruff")


def installed_ruff_version() -> Version | None:
    """Версия ruff в текущем окружении; ``None`` — не установлен."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return None
    match = _VERSION_RE.search(out)
    return Version(match.group(1)) if match else None


def specifier_violations(req: Requirement, installed: Version | None) -> list[str]:
    """Нарушения спецификатора: нет верхней границы / установлен не тот ruff."""
    problems = []
    if not any(spec.operator in ("<", "<=", "==", "~=") for spec in req.specifier):
        problems.append(
            f"pyproject.toml: спецификатор `{req}` без верхней границы — CI поставит "
            "свежайший ruff в день релиза, а у контрибьютора останется прежний "
            "(issue #791)."
        )
    if installed is not None and installed not in req.specifier:
        problems.append(
            f"установлен ruff {installed}, что не удовлетворяет `{req}` — "
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
    req = ruff_requirement()
    installed = installed_ruff_version()
    problems += specifier_violations(req, installed)

    if problems:
        print("FAIL: пин ruff разъехался:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    where = f"установлен {installed}" if installed else "в окружении не найден"
    print(f"ruff pin: единственный источник — pyproject.toml `{req}` ({where}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
