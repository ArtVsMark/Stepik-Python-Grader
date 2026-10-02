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
import threading
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_SELECT", "LintUnavailable", "Violation", "ruff_available", "run_lint"]

# Широкие наборы ruff: pycodestyle errors/warnings (E/W) + pyflakes (F).
#
# issue #728: это НЕ то же, что карточки rules/data/pep8_ru.json, хотя прежний
# комментарий так утверждал. Расхождение было двусторонним: ruff в этих наборах
# знает ~110 правил (74 из них без карточки — объяснить нечем), а 17 карточек,
# наоборот, недостижимы, потому что их правила у ruff в preview и без
# ``--preview`` не срабатывают — молчали ровно «школьные» E2xx/E3xx про пробелы
# и пустые строки. Прикладной слой зовёт ``run_lint`` с точным набором из самой
# базы (``rules.lint_select()``, preview=True); этот константный набор остаётся
# для прямых вызовов и обратной совместимости.
DEFAULT_SELECT = "E,W,F"

_RUFF_TIMEOUT_S = 30.0
# issue #877: запас поверх таймаута самого процесса. `timeout=` у
# `subprocess.run` применяется к УЖЕ запущенному процессу (`communicate`), а
# зависнуть можно и раньше — в `Popen.__init__`, на чтении errpipe после fork.
# Именно это поймал pytest-timeout на macOS + Python 3.14, и именно поэтому
# 30-секундный таймаут не спас: проверено прогоном с подменённым `Popen` —
# `run_lint` висел дольше 35 с.
_LAUNCH_GRACE_S = 10.0
_MISSING_RUFF_MARKER = "No module named ruff"


@dataclass(frozen=True)
class Violation:
    """Одно нарушение стиля от ruff (не влияет на вердикт проверки)."""

    rule_code: str  # код правила, напр. "E501" / "F401"
    line_no: int  # 1-based номер строки (0, если ruff не сообщил)
    message: str
    column: int = 0  # 1-based колонка (0, если не сообщена)


def _run_guarded(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str] | None:
    """``subprocess.run`` со страховкой от зависания на ЗАПУСКЕ (issue #877).

    ``timeout=`` покрывает только ожидание уже стартовавшего процесса. Если
    подвиснет сам ``Popen.__init__`` (fork/exec, чтение errpipe), вызывающий
    поток остаётся заблокированным навсегда: в CLI это повисший грейдер, под
    ``--serve`` — занятый воркер, который никто не отменит.

    Поэтому запуск уходит в daemon-поток с собственным дедлайном. Поток может
    остаться висеть, но daemon не мешает процессу завершиться (в отличие от
    воркеров ``ThreadPoolExecutor`` — тот случай разбирался в issue #806).
    ``None`` — не уложились: вызывающий трактует это как «ruff недоступен»,
    то же, что при любом другом сбое запуска.
    """
    outcome: list[subprocess.CompletedProcess[str] | BaseException] = []

    def _worker() -> None:
        try:
            outcome.append(subprocess.run(cmd, **kwargs))  # type: ignore[arg-type,call-overload]
        except BaseException as exc:
            # Ловим всё: исключение переносится в вызывающий поток и там
            # обрабатывается как прежде. Проглотить его здесь означало бы
            # потерять диагностику в чужом потоке.
            outcome.append(exc)

    thread = threading.Thread(target=_worker, daemon=True, name="ruff-launch")
    thread.start()
    timeout = float(kwargs.get("timeout") or _RUFF_TIMEOUT_S)  # type: ignore[arg-type]
    thread.join(timeout + _LAUNCH_GRACE_S)
    if thread.is_alive() or not outcome:
        return None
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result  # тот же класс, что и раньше — вызывающий ловит как прежде
    return result


class LintUnavailable(Exception):
    """ruff не установлен (нет extra ``[lint]``) — раздел «Стиль» недоступен."""


def ruff_available() -> bool:
    """True, если ruff установлен и запускается (для показа блока «Стиль»).

    Дешёвая проба ``python -m ruff --version``; любой сбой запуска трактуется
    как «недоступен» (UI покажет подсказку про extra, не блок).
    """
    try:
        proc = _run_guarded(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True,
            timeout=_RUFF_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # issue #877: `None` — запуск завис; трактуем как «недоступен», а не ждём.
    # `check=True` заменён явной проверкой кода: обёртка возвращает результат,
    # а не бросает CalledProcessError из чужого потока.
    return proc is not None and proc.returncode == 0


def run_lint(
    file_path: Path, *, select: str = DEFAULT_SELECT, preview: bool = False
) -> list[Violation]:
    """Прогнать ruff по файлу решения → список нарушений (issue #346).

    Args:
        select: набор правил для ``ruff --select``. Пустая строка → ruff не
            запускается вовсе (пустой список): нечего проверять.
        preview: включить ``--preview`` (issue #728). Большинство
            pycodestyle-правил про пробелы, отступы и пустые строки (E1xx/E2xx/
            E3xx, W391) у ruff в preview — без флага они не срабатывают, и
            ученик не видит ровно тех замечаний, которые ему нужнее всего.

    Raises:
        LintUnavailable: ruff не установлен (opt-in extra ``[lint]``).

    Прочие сбои — ruff упал, вернул невалидный JSON, аварийный код возврата,
    нечитаемый файл — дают **пустой список** (best-effort): линт информационный
    и не должен ронять/менять проверку.
    """
    if not select.strip():
        return []
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--output-format",
        "json",
        "--select",
        select,
        *(["--preview"] if preview else []),
        str(file_path),
    ]
    try:
        # issue #792 (PY-08): ruff пишет UTF-8, а `text=True` без явной
        # кодировки декодирует системной кодовой страницей. На Windows-RU
        # (cp1251) кириллица в сообщении превращалась в мусор вида «С‰», а
        # редкие байты давали UnicodeDecodeError мимо перехвата ниже — тот
        # ловит только OSError/SubprocessError. errors="replace" оставляет
        # раздел «Стиль» рабочим даже на неожиданном байте.
        proc = _run_guarded(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_RUFF_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc is None:  # issue #877: запуск завис — раздел «Стиль» просто пуст
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
