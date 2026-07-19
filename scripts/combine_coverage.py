#!/usr/bin/env python
"""combine_coverage.py — объединение coverage-данных матрицы CI с явным сигналом
о недостающих ОС/песочница-данных (issue #559).

Раньше combine-логика жила инлайном в ``ci.yml``, а job ``coverage-combine``
зависел от ``sandbox-linux`` жёстко (``needs``) — падение flaky
privileged-контейнера ТИХО скипало весь cross-OS gate 90% и бейдж (немой skip,
а не сигнал). Теперь:

* ``coverage-combine`` запускается всегда, когда прошёл ``test``
  (``if: !cancelled() && needs.test.result == 'success'``), даже если
  ``sandbox-linux`` упал;
* этот скрипт детектит отсутствие ожидаемого ``.coverage.<suffix>`` и ГРОМКО
  сигналит (``::warning::`` + ``degraded=true`` в ``$GITHUB_OUTPUT``), а не молча
  недосчитывает покрытие;
* строгий ``--fail-under`` применяется ТОЛЬКО когда все ожидаемые данные на
  месте; при деградации — информационный отчёт + пометка, а шаг бейджа гейтится
  по ``degraded`` и не публикует устаревший бейдж.

Запуск (в job ``coverage-combine``):

    python scripts/combine_coverage.py \
        --artifacts coverage-artifacts --fail-under 90 \
        --require ubuntu-latest --require windows-latest \
        --require macos-latest --require sandbox-linux
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

__all__ = [
    "discover_coverage_files",
    "main",
    "missing_required",
]

# ``coverage combine`` ищет файлы вида ``.coverage.<suffix>``; артефакты матрицы —
# ``.coverage.<os>`` (ubuntu-latest/windows-latest/macos-latest) и
# ``.coverage.sandbox-linux`` (issue #283/#420).
_COVERAGE_PREFIX = ".coverage."


def discover_coverage_files(artifacts_dir: Path) -> list[str]:
    """Суффиксы присутствующих ``.coverage.<suffix>`` файлов (отсортированные)."""
    if not artifacts_dir.is_dir():
        return []
    return sorted(
        entry.name[len(_COVERAGE_PREFIX) :]
        for entry in artifacts_dir.iterdir()
        if entry.is_file() and entry.name.startswith(_COVERAGE_PREFIX)
    )


def missing_required(present: list[str], required: list[str]) -> list[str]:
    """Ожидаемые суффиксы, которых нет среди ``present`` (порядок ``required``)."""
    have = set(present)
    return [name for name in required if name not in have]


def _emit_github_output(name: str, value: str) -> None:
    """Записать ``name=value`` в ``$GITHUB_OUTPUT`` (no-op вне GitHub Actions)."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with Path(out).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _run_coverage(coverage_args: list[str]) -> int:
    """Вызвать ``coverage`` текущим интерпретатором; вернуть код возврата."""
    completed = subprocess.run(
        [sys.executable, "-m", "coverage", *coverage_args],
        check=False,
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    """CLI: объединить coverage-данные, сигналя о недостающих ОС-срезах.

    Возвращает ненулевой код при провале строгого gate (полные данные) или при
    ошибке самого ``coverage combine``. При деградации (нет ожидаемых данных) —
    громкий ``::warning::`` и код 0: инфра-флейк ``sandbox-linux`` не должен
    ложно ронять cross-OS combine из-за недосчитанного ``_linux.py``.
    """
    parser = argparse.ArgumentParser(
        description="Объединить coverage-данные CI с сигналом о недостающих ОС-срезах."
    )
    parser.add_argument("--artifacts", type=Path, required=True, help="Каталог с .coverage.*")
    parser.add_argument("--fail-under", type=float, default=90.0, help="Порог cross-OS gate")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="SUFFIX",
        help="Ожидаемый суффикс .coverage.<suffix> (можно повторять)",
    )
    args = parser.parse_args(argv)

    present = discover_coverage_files(args.artifacts)
    missing = missing_required(present, args.require)
    degraded = bool(missing)

    if degraded:
        joined = ", ".join(missing)
        print(
            "::warning title=coverage-combine degraded::"
            f"Отсутствуют coverage-данные: {joined}. "
            "Cross-OS gate и combined-бейдж пропущены (устаревший бейдж не публикуется). "
            "Обычно это flaky sandbox-linux — перезапустите job.",
            flush=True,
        )
    _emit_github_output("degraded", "true" if degraded else "false")

    # combine выполняем всегда — объединяем то, что есть, а не глотаем молча.
    combine_rc = _run_coverage(["combine", str(args.artifacts)])
    if combine_rc != 0:
        return combine_rc

    # Информационный отчёт — всегда (виден в логах job'а).
    _run_coverage(["report", "-m"])
    _run_coverage(["xml"])

    if degraded:
        # Строгий gate НЕ применяем: недосчитанный _linux.py ложно уронил бы
        # combine из-за инфра-флейка. Сигнал уже дан warning'ом выше.
        return 0

    # Полные данные — строгий cross-OS gate.
    return _run_coverage(["report", f"--fail-under={args.fail_under:g}"])


if __name__ == "__main__":
    raise SystemExit(main())
