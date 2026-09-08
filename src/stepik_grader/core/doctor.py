"""core/doctor.py — прогон всех проверок окружения одной командой (issue #982).

Архитектурный слой: Domain (leaf — только stdlib и ``core.diagnostics``).

Отчёт об окружении раньше существовал ровно в одном месте: его собирала
диагностика Stepik, и **только когда сама падала**. Файл для issue ценен и до
сбоя — «у меня не скачивается задача» без него превращается в переписку с
уточнениями, а «сходите запустите диагностику» отправляет пользователя туда, где
инструмент к тому же требует ввода и лезет в сеть.

Здесь — та же поверхность отчёта, вызванная явно и без побочных эффектов:
движок опрашивает состояние, ничего не чинит и не печатает, а редакция секретов
остаётся одна на весь вывод. Своих проверок модуль НЕ ЗАВОДИТ: реестр один
(``core.diagnostics.CHECKS``), иначе `doctor` и точка сбоя начнут отвечать
по-разному — ровно то расхождение, из-за которого занятый порт объявлялся
неверными учётными данными (находка ``JRN-3A-04``).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from stepik_grader.core import diagnostics
from stepik_grader.core.diag_log import redact

__all__ = [
    "REPORT_NAME",
    "build_report",
    "collect",
    "exit_code",
    "save_report",
]

#: Имя файла отчёта. То же, что у диагностики: адресат один — мейнтейнер, и два
#: имени для одного содержимого заставляли бы спрашивать, какой файл прислали.
REPORT_NAME = "environment_checks.json"


def collect(
    secrets_path: pathlib.Path,
    *,
    network: bool = True,
    timeout: float = 10.0,
) -> list[diagnostics.Finding]:
    """Прогнать весь реестр проверок и вернуть находки.

    Args:
        secrets_path: Путь к ``secrets.json``.
        network: Опрашивать ли сеть. ``False`` даёт сетевым проверкам ``SKIP``
            с причиной — офлайн-прогон обязан отличаться от прогона, где сеть
            проверили и она в порядке.
        timeout: Таймаут сетевой проверки в секундах.
    """
    return diagnostics.run_checks(
        diagnostics.Context(secrets_path=secrets_path, network=network, timeout=timeout)
    )


def build_report(
    findings: list[diagnostics.Finding],
    translate: Any,
) -> dict[str, Any]:
    """Собрать находки в сериализуемый отчёт.

    Ключи локалей разворачивает ``translate`` — движок отдаёт данные и о языке
    отчёта не знает. Секреты здесь НЕ редактируются намеренно: редакция одна и
    живёт в :func:`save_report`, поэтому новая точка дампа не может повторить
    находку ``OPS-1-02``.

    Args:
        findings: Результаты :func:`collect`.
        translate: Функция ``(ключ, **params) -> строка`` из вызывающего слоя.
    """
    return {
        "checks": [
            {
                "id": finding.id,
                "status": str(finding.status),
                "subject": translate(finding.check.subject),
                "detail": translate(finding.outcome.detail, **finding.outcome.params),
                "remedy": translate(finding.check.remedy),
            }
            for finding in findings
        ]
    }


def save_report(output_dir: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
    """Записать отчёт, отредактировав секреты по сериализованному тексту.

    Редакция идёт по готовому JSON, а не по полям: маскируются и известные
    зарегистрированные секреты, и всё, что подходит под паттерны токенов —
    включая поля, о которых мы заранее не знаем. Файл создаётся ради того,
    чтобы его приложили к issue, поэтому цена пропущенного токена здесь выше
    обычной.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / REPORT_NAME
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    file_path.write_text(redact(serialized), encoding="utf-8")
    return file_path


def exit_code(findings: list[diagnostics.Finding]) -> int:
    """``1``, если хоть одна проверка провалилась; иначе ``0``.

    ``SKIP`` провалом НЕ считается: «предмета нет» и «предмет плох» — разные
    ответы, и офлайн-прогон не должен выглядеть сломанным окружением.

    Код возврата здесь не украшение: диагностика годами возвращала ноль при
    любом исходе и потому была непригодна как автоматическая проверка (находка
    ``JRN-3A-03``).
    """
    return 1 if any(f.status == diagnostics.Status.FAIL for f in findings) else 0
