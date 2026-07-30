"""feedback_adapter.py — web-адаптер над ``core/feedback`` (issue #754, эпик #751).

Слой между эндпоинтом ``POST /api/feedback`` и доменной сборкой prefilled-URL.
Тот же core-модуль, что у пункта 9 CLI-меню (#753) — логика формирования ссылки,
редакция секретов и усечение по длине URL не дублируются в web-слое, а тем более
в JavaScript.

Ничего не отправляет: возвращает браузеру ссылку на заполненную GitHub-форму и
предпросмотр полей. Issue публикует сам пользователь кнопкой Submit — у грейдера
нет ни токена, ни сервера для этого (см. docstring ``core/feedback.py``).
"""

from __future__ import annotations

from typing import Any

from stepik_grader.core import feedback

__all__ = ["FEEDBACK_MAX_TEXT", "feedback_draft"]

# Потолок на длину текста из формы модалки. Ниже него ``core/feedback`` всё
# равно усечёт значение под лимит URL — этот кламп лишь отсекает патологический
# ввод (вставленный мегабайтный лог) до попадания в сборку ссылки.
FEEDBACK_MAX_TEXT = 8000

# Главное текстовое поле каждой формы — то же соответствие, что в CLI-меню
# (`cli/interactive.py:_FEEDBACK_SUMMARY_FIELD`); имена — id полей YAML-форм.
_SUMMARY_FIELD: dict[feedback.FeedbackKind, str] = {
    feedback.FeedbackKind.BUG: "what-happened",
    feedback.FeedbackKind.IDEA: "idea",
    feedback.FeedbackKind.TASK_PROBLEM: "details",
}


def _text(value: Any) -> str:
    """Строковое поле тела POST: не-строка/None → пусто, длинное — обрезается."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:FEEDBACK_MAX_TEXT]


def feedback_draft(
    kind_raw: str,
    *,
    summary: Any = None,
    step_url: Any = None,
    logs: Any = None,
    lang: str,
    sandbox: bool = False,
) -> dict[str, Any] | None:
    """Черновик обращения → dict для ``POST /api/feedback``. ``None`` — тип не распознан.

    ``summary``/``step_url``/``logs`` приходят из тела POST как есть (нестроковый
    мусор игнорируется). Окружение собирается сервером — версия/ОС/Python берутся
    с машины, где запущен ``--serve``, то есть с машины пользователя.

    ``fields`` — СПИСОК пар (не объект): предпросмотр показывает поля в
    осмысленном порядке, а порядок ключей JSON-объекта контрактом не гарантирован.
    """
    kind = feedback.kind_from_str(kind_raw)
    if kind is None:
        return None

    fields = {
        "environment": feedback.collect_environment(
            channel="web (--serve)",
            sandbox="да (--sandbox)" if sandbox else None,
            lang=lang,
        )
    }
    summary_text = _text(summary)
    if summary_text:
        fields[_SUMMARY_FIELD[kind]] = summary_text
    step_url_text = _text(step_url)
    if step_url_text and kind is feedback.FeedbackKind.TASK_PROBLEM:
        fields["step-url"] = step_url_text
    logs_text = _text(logs)
    if logs_text and kind is feedback.FeedbackKind.BUG:
        fields["logs"] = logs_text

    prepared = feedback.prepare_issue(kind, fields)
    return {
        "kind": prepared.kind.value,
        "url": prepared.url,
        "fields": [{"id": name, "value": value} for name, value in prepared.fields.items()],
        "truncated": list(prepared.truncated),
        "dropped": list(prepared.dropped),
        "discussions_url": feedback.DISCUSSIONS_URL,
    }
