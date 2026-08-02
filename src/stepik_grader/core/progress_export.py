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
from stepik_grader.core.i18n import load_locale_messages

__all__ = [
    "SCHEMA",
    "build_progress_report",
    "render_html",
    "render_markdown",
]

# Версия формата агрегата — для будущего импорта «класса» (AC #432).
SCHEMA = "stepik-grader/progress/1"

# issue #821: отчёт — единственный артефакт, который пользователь показывает
# наружу (ментору, в резюме), и он игнорировал `--lang`: заголовки были русские
# всегда, а `<html lang>` врал. Подписи берутся из того же каталога, что и
# сообщения CLI (`core/locales/<lang>.json`), язык приходит от вызывающей
# стороны — она его уже знает.
_FALLBACK_LANG = "ru"


def _messages(lang: str) -> dict[str, str]:
    """Каталог сообщений языка с откатом на русский (пустой каталог — не ошибка)."""
    messages = load_locale_messages(lang)
    return messages or load_locale_messages(_FALLBACK_LANG)


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


def _fmt_secs(secs: float | None, msgs: dict[str, str]) -> str:
    if secs is None:
        return "—"
    if secs < 60:
        return f"{secs:.0f} {msgs['progress_export_unit_sec']}"
    if secs < 3600:
        return f"{secs / 60:.0f} {msgs['progress_export_unit_min']}"
    return f"{secs / 3600:.1f} {msgs['progress_export_unit_hour']}"


def _counts_lines(counts: dict[str, int], msgs: dict[str, str]) -> list[str]:
    return [f"- `{k}`: {v}" for k, v in counts.items()] or [f"- {msgs['progress_export_no_data']}"]


def _task_label(task_key: str | None, msgs: dict[str, str]) -> str:
    """Подпись задачи в отчёте: «.» и пустой ключ — не имя задачи (issue #817).

    Записи, сделанные до нормализации ключа, хранят «.» (грейдер запускали из
    папки задачи) — показывать точку как название задачи бессмысленно, поэтому
    такие строки помечаются тем же «(без задачи)», что и записи без ключа.
    """
    return task_key if task_key and task_key != "." else msgs["progress_export_no_task"]


def render_markdown(report: dict[str, Any], *, lang: str = _FALLBACK_LANG) -> str:
    """Отрисовать агрегатный отчёт в самодостаточный Markdown (issue #432).

    ``lang`` — язык подписей (issue #821); значения (``task_key``, вердикты,
    ``failure_kind``) не переводятся: это данные и коды, одинаковые в CLI и web.
    """
    msgs = _messages(lang)
    if report["total_runs"] == 0:
        return f"# {msgs['progress_export_title']}\n\n_{msgs['progress_export_empty']}_\n"
    lines: list[str] = [
        f"# {msgs['progress_export_title']}",
        "",
        f"{msgs['progress_export_runs']}: **{report['total_runs']}** · "
        f"{msgs['progress_export_tasks']}: **{report['total_tasks']}** · "
        f"{msgs['progress_export_solved']}: **{report['solved_tasks']}**",
        "",
        f"## {msgs['progress_export_tasks_heading']}",
        "",
        f"| {msgs['progress_export_col_task']} | {msgs['progress_export_col_solved']} "
        f"| {msgs['progress_export_col_attempts']} | {msgs['progress_export_col_time']} |",
        "|---|:---:|---:|---:|",
    ]
    for t in report["tasks"]:
        mark = "✅" if t["solved"] else "…"
        lines.append(
            f"| {_task_label(t['task_key'], msgs)} | {mark} | {t['attempts']} | "
            f"{_fmt_secs(t['seconds_to_first_ac'], msgs)} |"
        )
    lines += ["", f"## {msgs['progress_export_verdicts_heading']}", ""]
    lines += _counts_lines(report["verdicts"], msgs)
    lines += ["", f"## {msgs['progress_export_failures_heading']}", ""]
    lines += _counts_lines(report["failure_kinds"], msgs)
    lines.append("")
    return "\n".join(lines)


def render_html(report: dict[str, Any], *, lang: str = _FALLBACK_LANG) -> str:
    """Отрисовать агрегатный отчёт в самодостаточный HTML (issue #432).

    Без внешних ресурсов (инлайн-стиль) — файл можно открыть/переслать как есть.
    Все значения экранируются (``html.escape``) — данные из истории могут
    содержать спецсимволы (``task_key`` — путь).

    ``lang`` (issue #821) задаёт и подписи, и атрибут ``<html lang>``: раньше
    там стояло жёсткое ``ru`` независимо от языка отчёта, из-за чего браузер и
    скринридер получали неверный язык документа.
    """
    esc = html.escape
    msgs = _messages(lang)
    if report["total_runs"] == 0:
        body = f"<p><em>{esc(msgs['progress_export_empty'])}</em></p>"
    else:
        rows = "".join(
            f"<tr><td>{esc(_task_label(t['task_key'], msgs))}</td>"
            f"<td style='text-align:center'>{'✅' if t['solved'] else '…'}</td>"
            f"<td style='text-align:right'>{t['attempts']}</td>"
            "<td style='text-align:right'>"
            f"{esc(_fmt_secs(t['seconds_to_first_ac'], msgs))}</td></tr>"
            for t in report["tasks"]
        )
        verdicts = "".join(
            f"<li><code>{esc(k)}</code>: {v}</li>" for k, v in report["verdicts"].items()
        )
        fkinds = "".join(
            f"<li><code>{esc(k)}</code>: {v}</li>" for k, v in report["failure_kinds"].items()
        )
        no_data = f"<li>{esc(msgs['progress_export_no_data'])}</li>"
        body = (
            f"<p>{esc(msgs['progress_export_runs'])}: <b>{report['total_runs']}</b> · "
            f"{esc(msgs['progress_export_tasks'])}: <b>{report['total_tasks']}</b> · "
            f"{esc(msgs['progress_export_solved'])}: <b>{report['solved_tasks']}</b></p>"
            f"<h2>{esc(msgs['progress_export_tasks_heading'])}</h2>"
            f"<table><thead><tr><th>{esc(msgs['progress_export_col_task'])}</th>"
            f"<th>{esc(msgs['progress_export_col_solved'])}</th>"
            f"<th>{esc(msgs['progress_export_col_attempts'])}</th>"
            f"<th>{esc(msgs['progress_export_col_time'])}</th></tr></thead><tbody>"
            f"{rows}</tbody></table>"
            f"<h2>{esc(msgs['progress_export_verdicts_heading'])}</h2>"
            f"<ul>{verdicts or no_data}</ul>"
            f"<h2>{esc(msgs['progress_export_failures_heading'])}</h2>"
            f"<ul>{fkinds or no_data}</ul>"
        )
    title = esc(msgs["progress_export_title"])
    return (
        f"<!doctype html><html lang='{esc(lang)}'><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;"
        "padding:0 1rem}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ccc;padding:.4rem .6rem}"
        "code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}</style></head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )
