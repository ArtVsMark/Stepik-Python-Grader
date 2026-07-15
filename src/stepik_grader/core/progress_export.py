"""progress_export.py — экспорт прогресса/агрегатов в Markdown/HTML (issue #432).

Архитектурный слой: Application-service над ``core/history``/``core/insights``.
Дешёвая «вирусная петля» без сервера: студент делится прогрессом с ментором/в
резюме, репетитор собирает анонимные агрегаты класса файлами — добирает часть
ценности server mode (#151) без ops-бремени.

Инварианты (AC #432):
- В экспорт идут ТОЛЬКО агрегаты (TTFG по задачам, тали вердиктов и
  ``failure_kind``) — **никаких исходников решений**.
- Пустая/отсутствующая история → отчёт с ``total_runs == 0`` (вызывающая
  сторона печатает дружелюбное сообщение, не ошибку).
- Формат агрегата (``build_progress_report``) документирован для будущего
  импорта «класса» — стабильные ключи, JSON-совместимые значения.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from stepik_grader.core import history, insights

__all__ = [
    "SCHEMA",
    "build_progress_report",
    "render_markdown",
    "render_html",
]

# Версия формата агрегата — для будущего импорта «класса» (AC #432).
SCHEMA = "stepik-grader/progress/1"


def build_progress_report(db_path: Path, *, limit: int = 10000) -> dict[str, Any]:
    """Собрать агрегатный отчёт прогресса из истории (issue #432).

    Возвращает JSON-совместимый dict со стабильными ключами:

    - ``schema`` — версия формата (``SCHEMA``);
    - ``total_runs`` — число прогонов в истории;
    - ``tasks`` — список TTFG по задачам (``task_key``/``attempts``/``solved``/
      ``total_runs``/``seconds_to_first_ac``);
    - ``solved_tasks``/``total_tasks`` — сводные счётчики;
    - ``verdicts`` — тали вердиктов кейсов (``{"AC": n, "WA": n, ...}``);
    - ``failure_kinds`` — тали ключей падений (``{"timeout": n, ...}``).

    Исходники решений в отчёт НЕ попадают.
    """
    runs = history.read_recent_runs(db_path, limit=limit)
    verdicts: dict[str, int] = {}
    failure_kinds: dict[str, int] = {}
    for run in runs:
        for case in run.get("cases", []):
            verdict = case.get("verdict")
            if verdict:
                verdicts[verdict] = verdicts.get(verdict, 0) + 1
            fkind = case.get("failure_kind")
            if fkind:
                failure_kinds[fkind] = failure_kinds.get(fkind, 0) + 1

    tasks = [
        {
            "task_key": p.task_key,
            "attempts": p.attempts,
            "solved": p.solved,
            "total_runs": p.total_runs,
            "seconds_to_first_ac": p.seconds_to_first_ac,
        }
        for p in insights.time_to_first_green(db_path, limit=limit)
    ]
    return {
        "schema": SCHEMA,
        "total_runs": len(runs),
        "total_tasks": len(tasks),
        "solved_tasks": sum(1 for t in tasks if t["solved"]),
        "tasks": tasks,
        "verdicts": dict(sorted(verdicts.items())),
        "failure_kinds": dict(sorted(failure_kinds.items())),
    }


def _fmt_secs(secs: float | None) -> str:
    if secs is None:
        return "—"
    if secs < 60:
        return f"{secs:.0f} с"
    if secs < 3600:
        return f"{secs / 60:.0f} мин"
    return f"{secs / 3600:.1f} ч"


def _counts_lines(counts: dict[str, int]) -> list[str]:
    return [f"- `{k}`: {v}" for k, v in counts.items()] or ["- (нет данных)"]


def render_markdown(report: dict[str, Any]) -> str:
    """Отрисовать агрегатный отчёт в самодостаточный Markdown (issue #432)."""
    if report["total_runs"] == 0:
        return "# Прогресс Stepik-Grader\n\n_История пуста — прогонов ещё не было._\n"
    lines: list[str] = [
        "# Прогресс Stepik-Grader",
        "",
        f"Прогонов: **{report['total_runs']}** · задач: **{report['total_tasks']}** · "
        f"решено: **{report['solved_tasks']}**",
        "",
        "## Задачи (до первого AC)",
        "",
        "| Задача | Решено | Попыток | Время до AC |",
        "|---|:---:|---:|---:|",
    ]
    for t in report["tasks"]:
        mark = "✅" if t["solved"] else "…"
        lines.append(
            f"| {t['task_key'] or '(без задачи)'} | {mark} | {t['attempts']} | "
            f"{_fmt_secs(t['seconds_to_first_ac'])} |"
        )
    lines += ["", "## Вердикты", ""]
    lines += _counts_lines(report["verdicts"])
    lines += ["", "## Типы падений", ""]
    lines += _counts_lines(report["failure_kinds"])
    lines.append("")
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    """Отрисовать агрегатный отчёт в самодостаточный HTML (issue #432).

    Без внешних ресурсов (инлайн-стиль) — файл можно открыть/переслать как есть.
    Все значения экранируются (``html.escape``) — данные из истории могут
    содержать спецсимволы (``task_key`` — путь).
    """
    esc = html.escape
    if report["total_runs"] == 0:
        body = "<p><em>История пуста — прогонов ещё не было.</em></p>"
    else:
        rows = "".join(
            f"<tr><td>{esc(t['task_key'] or '(без задачи)')}</td>"
            f"<td style='text-align:center'>{'✅' if t['solved'] else '…'}</td>"
            f"<td style='text-align:right'>{t['attempts']}</td>"
            f"<td style='text-align:right'>{esc(_fmt_secs(t['seconds_to_first_ac']))}</td></tr>"
            for t in report["tasks"]
        )
        verdicts = "".join(
            f"<li><code>{esc(k)}</code>: {v}</li>" for k, v in report["verdicts"].items()
        )
        fkinds = "".join(
            f"<li><code>{esc(k)}</code>: {v}</li>" for k, v in report["failure_kinds"].items()
        )
        body = (
            f"<p>Прогонов: <b>{report['total_runs']}</b> · задач: <b>{report['total_tasks']}</b> "
            f"· решено: <b>{report['solved_tasks']}</b></p>"
            "<h2>Задачи (до первого AC)</h2>"
            "<table><thead><tr><th>Задача</th><th>Решено</th><th>Попыток</th>"
            "<th>Время до AC</th></tr></thead><tbody>"
            f"{rows}</tbody></table>"
            f"<h2>Вердикты</h2><ul>{verdicts or '<li>(нет данных)</li>'}</ul>"
            f"<h2>Типы падений</h2><ul>{fkinds or '<li>(нет данных)</li>'}</ul>"
        )
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<title>Прогресс Stepik-Grader</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;"
        "padding:0 1rem}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:.4rem .6rem}"
        "code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}</style></head>"
        f"<body><h1>Прогресс Stepik-Grader</h1>{body}</body></html>"
    )
