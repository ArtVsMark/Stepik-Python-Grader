"""feedback.py — канал обратной связи: контекст обращения и prefilled-URL (issue #753).

Архитектурный слой: Core. Единственное проектное ребро — на leaf
``core/diag_log.py`` (редакция секретов); ``cli``/``web`` этот модуль НЕ
импортирует (версия читается напрямую через ``importlib.metadata``, иначе
появилось бы ребро ``core → cli``).

Зачем модуль. У релиза должен быть канал, по которому пользователь сообщает о
баге или предлагает идею, — и этот канал обязан приносить окружение (версия,
ОС, Python, режим), потому что вручную его не заполняет никто, а без него
баг-репорт невоспроизводим.

Как это работает (эпик #751). Грейдер НЕ создаёт issue сам: токен в
клиентском дистрибутиве утёк бы в первый же день, а прокси-бэкенд требует
сервера, которого у проекта нет. Вместо этого собирается **prefilled-URL** к
GitHub Issue Forms (``.github/ISSUE_TEMPLATE/*.yml``)::

    .../issues/new?template=bug_report.yml&<field-id>=<значение>

Браузер открывает форму уже заполненной, а Submit жмёт сам пользователь —
ноль секретов, ноль инфраструктуры, полная прозрачность.

Приватность (границы, которые здесь захардкожены):

- **Код решения не отправляется никогда.** README обещает «свой код не покидает
  машину»; канал обратной связи не становится исключением — код прикладывает
  только сам пользователь, руками, уже в форме.
- Значения полей проходят ``diag_log.redact`` (токены/``client_secret``/
  ``Authorization``) — своей реализации редакции здесь нет.
- Домашний каталог сворачивается в ``~`` (``scrub_paths``): в путях
  регулярно оказывается ФИО пользователя.
- ``platform.node()`` (имя машины) в окружение НЕ попадает — по той же причине.
- Ничего не открывается и не уходит без явного подтверждения пользователя;
  вызывающая сторона обязана сначала показать ``PreparedIssue.fields``.

``id`` полей форм — публичный контракт: ``_FIELD_IDS`` обязан совпадать с
``id:`` в YAML-шаблонах (парность стережёт ``tests/test_feedback.py``).
"""

from __future__ import annotations

import importlib.metadata
import platform
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

from stepik_grader.core.diag_log import redact

__all__ = [
    "DISCUSSIONS_URL",
    "FIELD_BUDGET_CHARS",
    "MAX_URL_LENGTH",
    "REPO_URL",
    "FeedbackKind",
    "PreparedIssue",
    "collect_environment",
    "kind_from_str",
    "prepare_issue",
]

REPO_URL = "https://github.com/ArtVsMark/Stepik-Python-Grader"
DISCUSSIONS_URL = f"{REPO_URL}/discussions"

# Практический потолок длины URL: браузеры и GitHub принимают ~8 КБ, дальше —
# 414 Request-URI Too Long. Держим запас: кириллица в percent-encoding раздувается
# в 6 символов на символ, поэтому «visually short» текст легко даёт длинный URL.
MAX_URL_LENGTH = 6000
# Бюджет одного поля до кодирования. Логи/трейсбеки длиннее усекаются с маркером —
# усечение всегда объявляется через PreparedIssue.truncated, а не молча.
FIELD_BUDGET_CHARS = 1500
# Ниже этого порога поле не сжимается, а выбрасывается целиком: обрывок в 20
# символов бесполезен, а место в URL всё равно занимает.
_MIN_KEPT_CHARS = 80
_TRUNCATION_MARKER = "\n…"


class FeedbackKind(StrEnum):
    """Тип обращения — определяет YAML-форму и набор предзаполняемых полей."""

    BUG = "bug"
    IDEA = "idea"
    TASK_PROBLEM = "task-problem"


# Файл формы в .github/ISSUE_TEMPLATE/ — параметр ?template= в URL.
_TEMPLATES: dict[FeedbackKind, str] = {
    FeedbackKind.BUG: "bug_report.yml",
    FeedbackKind.IDEA: "idea.yml",
    FeedbackKind.TASK_PROBLEM: "task_problem.yml",
}

# Контракт с YAML-формами: id полей, которые GitHub умеет предзаполнять.
# Неизвестный id GitHub игнорирует МОЛЧА — поэтому такие ключи отсекаются здесь
# (ValueError), а не уезжают в URL, где потеря данных незаметна.
_FIELD_IDS: dict[FeedbackKind, frozenset[str]] = {
    FeedbackKind.BUG: frozenset(
        {"what-happened", "steps", "expected", "environment", "logs", "extra"}
    ),
    FeedbackKind.IDEA: frozenset({"idea", "problem", "area", "environment"}),
    FeedbackKind.TASK_PROBLEM: frozenset(
        {"step-url", "symptom", "details", "task-context", "environment"}
    ),
}

# Порядок, в котором поля жертвуются, если URL не влезает в MAX_URL_LENGTH:
# сначала объёмное и вторичное (логи, «дополнительно»), в конце — описание
# проблемы. Поля вне списка (environment, symptom, step-url, area и главные
# описания) не дропаются — без них обращение теряет смысл.
_SACRIFICE_ORDER: tuple[str, ...] = (
    "logs",
    "extra",
    "task-context",
    "problem",
    "expected",
    "steps",
    "details",
)


@dataclass(frozen=True)
class PreparedIssue:
    """Готовое обращение: URL, ИТОГОВЫЕ значения полей и что с ними сделали.

    ``fields`` — ровно то, что уедет в форму (после редакции секретов,
    сворачивания домашнего пути и усечения). Вызывающая сторона обязана
    показать их пользователю до открытия браузера. ``truncated``/``dropped`` —
    id полей, которые пришлось усечь/выбросить из-за лимита длины URL: об этом
    нужно сказать вслух, иначе потеря выглядит как «всё отправилось».
    """

    kind: FeedbackKind
    url: str
    fields: dict[str, str]
    truncated: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()


def kind_from_str(value: str) -> FeedbackKind | None:
    """Разобрать тип обращения из строки (CLI-выбор, web-query). ``None`` — мусор."""
    try:
        return FeedbackKind(value.strip().lower())
    except ValueError:
        return None


def _package_version() -> str:
    """Версия пакета из метаданных. Дублирует ``cli._resolve_version`` намеренно:
    импорт ``cli`` из ``core`` создал бы цикл в DAG (см. docstring модуля)."""
    try:
        return importlib.metadata.version("stepik-python-grader")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown (запуск без pip install -e .)"


def scrub_paths(text: str) -> str:
    """Свернуть домашний каталог пользователя в ``~`` во всех его написаниях.

    В путях вида ``C:\\Users\\ivan.petrov\\...`` домашний каталог несёт имя
    (нередко — ФИО) пользователя, поэтому он сворачивается до попадания в
    обращение. Покрыты нативное написание, POSIX-слеши и экранированные
    обратные слеши (путь, пришедший из JSON/repr).
    """
    home = str(Path.home())
    if not home:
        return text
    variants = (home, home.replace("\\", "/"), home.replace("\\", "\\\\"))
    for variant in variants:
        if variant and variant in text:
            text = text.replace(variant, "~")
    return text


def _sanitize(value: str) -> str:
    """Редакция секретов + сворачивание домашнего пути + нормализация пробелов."""
    return scrub_paths(redact(value)).strip()


def _truncate(value: str, limit: int) -> str:
    """Усечь до ``limit`` символов ВКЛЮЧАЯ видимый маркер обрыва.

    Маркер входит в лимит, а не добавляется сверх него: иначе результат мог
    оказаться длиннее исходного лимита, и цикл сжатия в ``prepare_issue`` не
    сходился бы у границы ``_MIN_KEPT_CHARS`` (длина колебалась бы на месте).
    """
    if len(value) <= limit:
        return value
    keep = max(1, limit - len(_TRUNCATION_MARKER))
    return value[:keep].rstrip() + _TRUNCATION_MARKER


def _halve(value: str) -> str:
    """Сжать поле примерно вдвое, но не ниже ``_MIN_KEPT_CHARS``."""
    return _truncate(value, max(_MIN_KEPT_CHARS, len(value) // 2))


def collect_environment(
    *,
    channel: str,
    sandbox: str | None = None,
    lang: str | None = None,
) -> str:
    """Собрать блок «Окружение» для формы обращения.

    ``channel`` — откуда пришло обращение (``"CLI-меню"``, ``"web --serve"``),
    ``sandbox`` — активный backend изоляции или ``None`` (дефолт — без
    изоляции), ``lang`` — локаль интерфейса.

    Имя машины (``platform.node()``) сюда НЕ попадает намеренно: оно часто
    содержит имя владельца. Возвращается человекочитаемый многострочный текст —
    поле формы объявлено как ``render: text``.
    """
    lines = [
        f"Версия грейдера: {_package_version()}",
        f"ОС: {platform.platform()}",
        f"Python: {platform.python_version()} ({platform.python_implementation()})",
        f"Запуск: {channel}",
        f"Песочница: {sandbox or 'нет (--sandbox не задан)'}",
    ]
    if lang:
        lines.append(f"Локаль интерфейса: {lang}")
    return "\n".join(lines)


def _build_url(kind: FeedbackKind, fields: dict[str, str]) -> str:
    """Собрать ``issues/new?template=...`` с полями формы (percent-encoding)."""
    query = urlencode({"template": _TEMPLATES[kind], **fields})
    return f"{REPO_URL}/issues/new?{query}"


def prepare_issue(
    kind: FeedbackKind,
    fields: dict[str, str],
    *,
    max_url_length: int = MAX_URL_LENGTH,
    field_budget: int = FIELD_BUDGET_CHARS,
) -> PreparedIssue:
    """Подготовить обращение: очистить поля, уложиться в лимит URL, собрать ссылку.

    ``fields`` — значения по ``id`` полей YAML-формы соответствующего типа
    (см. ``_FIELD_IDS``). Пустые значения выбрасываются; каждое значение
    проходит редакцию секретов и сворачивание домашнего пути, затем усечение до
    ``field_budget``. Если URL всё равно длиннее ``max_url_length``, поля
    жертвуются в порядке ``_SACRIFICE_ORDER`` (сначала сжатие, затем дроп) —
    ключевые поля и ``environment`` сохраняются до конца.

    Ничего не открывает и никуда не отправляет — только считает. Открытие
    браузера остаётся решением пользователя на стороне CLI/web.

    Raises:
        ValueError: если передан ``id``, которого нет в форме этого типа —
            GitHub проигнорировал бы его молча, потеряв данные незаметно.
    """
    unknown = set(fields) - _FIELD_IDS[kind]
    if unknown:
        raise ValueError(
            f"неизвестные поля формы {kind.value}: {sorted(unknown)}; "
            f"допустимы: {sorted(_FIELD_IDS[kind])}"
        )

    prepared: dict[str, str] = {}
    truncated: list[str] = []
    for name, raw in fields.items():
        value = _sanitize(raw)
        if not value:
            continue
        if len(value) > field_budget:
            value = _truncate(value, field_budget)
            truncated.append(name)
        prepared[name] = value

    dropped: list[str] = []
    url = _build_url(kind, prepared)
    # Жертвуем по одному шагу за итерацию, каждый раз пересобирая URL: длина
    # зависит от percent-encoding (символ кириллицы — 6 символов на выходе),
    # поэтому посчитать её заранее «на глаз» нельзя. Сжатие — вдвое, а не сразу
    # до минимума: иначе из-за одного лишнего килобайта терялся бы весь лог.
    while len(url) > max_url_length:
        candidate = next((name for name in _SACRIFICE_ORDER if name in prepared), None)
        if candidate is None:
            break
        squeezed = _halve(prepared[candidate])
        # Сжатие, которое не уменьшило поле, ничего не даст — такое поле
        # выбрасываем целиком, иначе цикл крутился бы на месте.
        if len(squeezed) < len(prepared[candidate]):
            prepared[candidate] = squeezed
            if candidate not in truncated:
                truncated.append(candidate)
        else:
            del prepared[candidate]
            dropped.append(candidate)
            if candidate in truncated:
                truncated.remove(candidate)
        url = _build_url(kind, prepared)

    # Предохранитель: жертвы исчерпаны, а URL всё ещё длинный (патологически
    # длинное ключевое поле). Жмём самое длинное из оставшихся — ключевые поля
    # не дропаем никогда, без них обращение бессмысленно.
    while len(url) > max_url_length and prepared:
        name = max(prepared, key=lambda key: len(prepared[key]))
        squeezed = _halve(prepared[name])
        if len(squeezed) >= len(prepared[name]):
            break  # дальше не сжать (всё уже на минимуме) — отдаём как есть
        prepared[name] = squeezed
        if name not in truncated:
            truncated.append(name)
        url = _build_url(kind, prepared)

    return PreparedIssue(
        kind=kind,
        url=url,
        fields=prepared,
        truncated=tuple(truncated),
        dropped=tuple(dropped),
    )
