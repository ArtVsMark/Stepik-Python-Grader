"""settings_adapter.py — тонкий web-адаптер над пользовательскими настройками (issue #830).

Архитектурный слой: Application/UI (web-адаптер), как ``reference_adapter.py``.
Своей логики не добавляет — знает только, что настройки лежат в
``workspace / SETTINGS_FILE_NAME``, и умеет читать/переключать конкретный флаг,
не роняя запрос на недоступном диске (``OSError`` гасится: настройка — не
критичный для ответа путь).

Существует, чтобы слой маршрутов (``api_routes.py``) не ходил в
``core.user_settings`` напрямую: роутер — тонкая обёртка над адаптерами и
viewmodels, бизнес-логики не добавляет (docs/dev/architecture.md, ADR-0010).
"""

from __future__ import annotations

import contextlib
import pathlib

from stepik_grader.config import get_config
from stepik_grader.core.ai_hints import consent_endpoint
from stepik_grader.core.user_settings import (
    UserSettings,
    default_settings_path,
    load_settings,
    save_fields,
)

__all__ = [
    "ai_consent_recipient",
    "grant_ai_consent",
    "has_ai_consent",
    "read_settings",
    "revoke_ai_consent",
    "set_flag",
]


def _path_for(workspace: pathlib.Path) -> pathlib.Path:
    """Файл настроек рабочей директории — тот же, что видит CLI (issue #948).

    Раньше путь собирался здесь руками (``workspace / SETTINGS_FILE_NAME``), и
    после переезда настроек в общий ``~/.stepik-grader/settings.json`` веб
    продолжал бы читать и писать папочный файл — то есть тумблер в браузере и
    тумблер в меню разъехались бы окончательно.
    """
    return default_settings_path(workspace)


def read_settings(workspace: pathlib.Path) -> UserSettings:
    """Прочитать настройки рабочей директории (дефолты, если файла нет)."""
    return load_settings(_path_for(workspace))


def set_flag(workspace: pathlib.Path, name: str, value: bool) -> UserSettings:
    """Выставить булев флаг настроек и сохранить, если значение изменилось.

    Возвращает актуальные настройки (уже с новым значением). Запись, упавшая по
    ``OSError``, не мешает ответу: флаг применён в памяти запроса, а следующий
    запуск просто увидит прежнее значение на диске.
    """
    settings = read_settings(workspace)
    if getattr(settings, name) is not value:
        setattr(settings, name, value)
        with contextlib.suppress(OSError):
            # issue #997: пишем один флаг, а не снапшот — иначе веб затирал бы
            # то, что параллельно переключили в интерактивном меню.
            save_fields(_path_for(workspace), **{name: value})
    return settings


def ai_consent_recipient() -> str:
    """Кому сейчас уйдут данные — ``scheme://host[:port]`` или пусто (issue #931).

    Адрес берётся из активного конфига ЗДЕСЬ, а не в роутере: слой маршрутов
    ходит только в адаптеры и viewmodels (ADR-0010), и импорт ``core``/``config``
    в него нарушил бы это правило — гейт `test_router_reaches_core_only_through_adapters`
    поймал ровно такую попытку.
    """
    return consent_endpoint(get_config().ai_base_url)


def has_ai_consent(workspace: pathlib.Path, endpoint: str | None = None) -> bool:
    """Дано ли согласие на отправку кода ИМЕННО ЭТОМУ получателю (issue #931).

    Веб проверял только факт согласия, игнорируя ``ai_hint_consent_endpoint``,
    который CLI сверяет строго. Значит согласие, данное локальному ollama,
    молча распространялось на любой адрес, попавший в конфиг позже, — а конфиг
    приезжает вместе с чужой папкой задач. Документация при этом обещает
    «сменился получатель — спросят заново», и в CLI это работало.

    Пустой ``endpoint`` — адрес провайдера неизвестен (не задан в конфиге):
    привязывать не к чему, поэтому решает один флаг, как до issue #812.
    Ужесточать здесь нельзя — иначе согласие стало бы невозможно ни дать, ни
    проверить там, где адрес не объявлен, а данные всё равно могут уйти через
    настроенный иным способом канал.
    """
    recipient = ai_consent_recipient() if endpoint is None else endpoint
    settings = read_settings(workspace)
    if settings.ai_hint_consent is not True:
        return False
    if not recipient:
        return True
    return settings.ai_hint_consent_endpoint == recipient


def grant_ai_consent(workspace: pathlib.Path, endpoint: str | None = None) -> bool:
    """Зафиксировать согласие для конкретного получателя; ``False`` — некому.

    Пишутся ОБА поля, когда получатель известен: флаг без адреса — это прежнее
    глобальное согласие, которое #812 и убирал. Если адрес не объявлен,
    записывается только флаг: сохранять пустую строку как «получателя» значило
    бы позже сверять согласие с несуществующим адресом.
    """
    recipient = ai_consent_recipient() if endpoint is None else endpoint
    fields: dict[str, object] = {"ai_hint_consent": True}
    if recipient:
        fields["ai_hint_consent_endpoint"] = recipient
    with contextlib.suppress(OSError):
        save_fields(_path_for(workspace), **fields)
    return True


def revoke_ai_consent(workspace: pathlib.Path) -> bool:
    """Отозвать согласие; ``True`` — оно было (issue #933).

    Стираются оба ключа: оставшийся ``ai_hint_consent_endpoint`` без флага
    выглядел бы как «согласие на этот адрес», и следующая проверка совпадения
    получателя читала бы след отозванного решения.
    """
    had = read_settings(workspace).ai_hint_consent is True
    with contextlib.suppress(OSError):
        save_fields(_path_for(workspace), ai_hint_consent=None, ai_hint_consent_endpoint=None)
    return had
