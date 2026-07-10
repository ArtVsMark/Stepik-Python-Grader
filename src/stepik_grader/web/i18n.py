"""i18n.py — каталог сообщений веб-API: ``message_id`` → локализованный текст
(issue #264).

Архитектурный слой: Application/UI (web-адаптер), **не** leaf — в отличие от
``core/i18n.py`` (issue #144), которому запрещены project-импорты. Этот модуль
как раз строится НА НЕМ: ``core/i18n.load_locale_messages()`` даёт graceful-
деградирующую загрузку ``core/locales/<lang>.json``, а здесь — рендеринг с
подстановкой ``message_params`` и выбор локали по ``?lang=``. Каталог общий
для CLI (``cli._t()``) и веба, но ключи не пересекаются: веб использует
"плоские" snake_case-имена (``path_not_found``), заведённые этим issue.

Контракт ответа (см. docs/result-contract.md): JSON-ответы с человекочитаемым
``message`` дополняются ``message_id`` (ключ каталога) и ``message_params``
(dict подстановок, использованных при рендере — пустой, если их не было).
``message`` остаётся для обратной совместимости и рендерится ИМЕННО здесь —
единственное место в web-слое, которое знает про JSON-локали.
"""

from __future__ import annotations

from typing import Any

from stepik_grader.core.i18n import load_locale_messages

__all__ = [
    "DEFAULT_LANG",
    "SUPPORTED_LANGS",
    "message_fields",
    "render_message",
    "resolve_lang",
]

# Дефолт — ru (issue #264: `?lang=` не должен менять поведение существующих
# вызывающих сторон, которые его не передают — байт-в-байт тот же текст,
# что был захардкожен в viewmodels.py/server.py до этого рефакторинга).
DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en")


def resolve_lang(raw: str | None) -> str:
    """Нормализовать значение query-параметра ``?lang=`` в поддерживаемую локаль.

    Неизвестное/пустое/отсутствующее значение → ``DEFAULT_LANG`` (ru) — без
    исключений, тот же принцип graceful degradation, что у
    ``core.i18n.load_locale_messages()``.
    """
    lang = (raw or "").strip().lower()
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def render_message(message_id: str, lang: str = DEFAULT_LANG, **params: Any) -> str:
    """Отрендерить ``message_id`` из каталога ``lang`` с подстановкой ``params``.

    Каталог перечитывается на каждый вызов (тонкий JSON-файл, не hot path —
    как и ``core.i18n.load_locale_messages()``, простота важнее кэширования).
    Отсутствующий ключ в запрошенной локали → откат на ``DEFAULT_LANG``;
    отсутствующий и там → сам ``message_id`` (никогда не бросает исключение,
    чтобы опечатка в ключе не роняла ответ API). Несовпадение ``params`` с
    плейсхолдерами шаблона (``KeyError``/``IndexError`` из ``str.format``) —
    так же graceful: возвращается нерендеренный шаблон.
    """
    template = load_locale_messages(lang).get(message_id)
    if template is None and lang != DEFAULT_LANG:
        template = load_locale_messages(DEFAULT_LANG).get(message_id)
    if template is None:
        return message_id
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


def message_fields(message_id: str, lang: str = DEFAULT_LANG, **params: Any) -> dict[str, Any]:
    """Собрать ``{"message", "message_id", "message_params"}`` для ответа API.

    Точка входа для вызывающего кода в ``viewmodels.py``/``server.py`` —
    единообразно добавляет все три поля контракта (issue #264), не заставляя
    каждый call site вручную звать ``render_message()`` и собирать словарь.
    """
    return {
        "message": render_message(message_id, lang, **params),
        "message_id": message_id,
        "message_params": dict(params),
    }
