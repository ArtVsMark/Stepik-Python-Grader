"""lint.py — opt-in PEP-проверка решения через ruff (issue #346, эпик #342).

Архитектурный слой: Infrastructure / Utilities (leaf — только stdlib).

Тонкая обёртка над `ruff check --output-format json --select E,W,F`: возвращает
список нарушений стиля (`Violation`), на которые ссылаются карточки правил
(`rules/`, #345) в разделе «Стиль». Свой AST/tokenize-чекер НЕ пишем — не
переиспользовать pycodestyle было бы дублированием (решение § 11 аудита).

**Опциональность и границы (дизайн § 5/§ 9.4):**

- ruff ставится отдельным extra ``[lint]`` — runtime-зависимости не растут.
  Без него ``run_lint`` бросает ``LintUnavailable``, а UI/CLI скрывают блок
  «Стиль» с подсказкой ``pip install stepik-python-grader[lint]``.
- Линт **не влияет на вердикт** проверки — это информационный канал.
- Прочие сбои (ruff упал, вернул мусор, файл не читается) проглатываются в
  пустой список — принцип best-effort ``cache``/``stats``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_SELECT", "Violation", "LintUnavailable", "ruff_available", "run_lint"]

# Наборы правил ruff по умолчанию: pycodestyle errors/warnings (E/W) +
# pyflakes (F). Совпадает с карточками rules/data/pep8_ru.json (#345).
DEFAULT_SELECT = "E,W,F"

_RUFF_TIMEOUT_S = 30.0
_MISSING_RUFF_MARKER = "No module named ruff"


@dataclass(frozen=True)
class Violation:
    """Одно нарушение стиля от ruff (не влияет на вердикт проверки)."""

    rule_code: str  # код правила, напр. "E501" / "F401"
    line_no: int  # 1-based номер строки (0, если ruff не сообщил)
    message: str
    column: int = 0  # 1-based колонка (0, если не сообщена)


class LintUnavailable(Exception):
    """ruff не установлен (нет extra ``[lint]``) — раздел «Стиль» недоступен."""


def ruff_available() -> bool:
    """True, если ruff установлен и запускается (для показа блока «Стиль»).

    Дешёвая проба ``python -m ruff --version``; любой сбой запуска трактуется
    как «недоступен» (UI покажет подсказку про extra, не блок).
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            check=True,
            timeout=_RUFF_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def run_lint(file_path: Path, *, select: str = DEFAULT_SELECT) -> list[Violation]:
    """Прогнать ruff по файлу решения → список нарушений (issue #346).

    Raises:
        LintUnavailable: ruff не установлен (opt-in extra ``[lint]``).

    Прочие сбои — ruff упал, вернул невалидный JSON, аварийный код возврата,
    нечитаемый файл — дают **пустой список** (best-effort): линт информационный
    и не должен ронять/менять проверку.
    """
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--output-format",
        "json",
        "--select",
        select,
        str(file_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_RUFF_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return []

    if _MISSING_RUFF_MARKER in proc.stderr:
        raise LintUnavailable(
            "ruff не установлен — раздел «Стиль» недоступен. "
            "Установите: pip install stepik-python-grader[lint]"
        )
    # ruff check: 0 — нарушений нет, 1 — есть (оба дают JSON в stdout); прочие
    # коды (2 = ошибка использования/внутренний сбой) — тихо пропускаем.
    if proc.returncode not in (0, 1):
        return []

    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    violations: list[Violation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        # ruff даёт code=null для синтаксических ошибок (E999-подобных) — их
        # пропускаем: у нас нет карточки правила, а RE и так виден в проверке.
        if not isinstance(code, str) or not code:
            continue
        location = item.get("location")
        row = location.get("row") if isinstance(location, dict) else None
        column = location.get("column") if isinstance(location, dict) else None
        violations.append(
            Violation(
                rule_code=code,
                line_no=row if isinstance(row, int) else 0,
                message=str(item.get("message", "")),
                column=column if isinstance(column, int) else 0,
            )
        )
    return violations
