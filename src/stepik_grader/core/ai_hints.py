"""ai_hints.py — opt-in AI-объяснение падений WA/RE (issue #435, ADR-0003).

Архитектурный слой: Application/Integration. Реализует стратегию
[ADR-0003](../../../docs/dev/adr/0003-ai-integration.md): BYOK, OpenAI-compatible
``{base_url}/chat/completions`` на голом ``requests`` (без провайдерских SDK и
без новых зависимостей). Один код покрывает и локальные раннеры (ollama), и
облачных OpenAI-совместимых провайдеров — разница лишь в ``ai_base_url``.

Инварианты (ADR-0003 §4, §6):
- **Дефолт — выключено.** Нет ``ai_base_url`` → :func:`is_configured` ложно,
  :func:`explain_failure` возвращает ``None`` (graceful skip).
- **Грейдинг НИКОГДА не падает из-за AI.** Любая ошибка канала (нет сети,
  таймаут, невалидный ключ, битый ответ) → ``None``, не исключение.
- **Приватность.** Ключ читается из env-переменной (имя — ``ai_api_key_env``) в
  момент вызова, НИКОГДА не из файлов проекта; регистрируется в
  ``diag_log.register_secret`` (редактируется в логах). По умолчанию в сеть
  ничего не уходит.
- **Заземление против галлюцинаций (§5).** В промпт кладём вердикт,
  ``failure_kind``, diff и текст релевантной карточки (не голый код); ответ
  помечен как AI-generated и ограничен по длине.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from stepik_grader.core.diag_log import get_logger, register_secret

__all__ = [
    "AI_MARKER_EN",
    "AI_MARKER_RU",
    "AiHintOutcome",
    "FailureContext",
    "base_url_is_allowed",
    "env_name_is_allowed",
    "explain_failure",
    "explain_failure_detailed",
    "is_configured",
]

_log = get_logger("ai_hints")

# issue #812 (SECD-01): откуда разрешено брать ключ. Имя env-переменной
# приходит из pyproject.toml, а тот ищется от cwd вверх — то есть приезжает
# вместе с чужой папкой задач; без ограничения любой файл мог назначить
# «ключом» посторонний секрет окружения.
_DEFAULT_KEY_ENV = "STEPIK_GRADER_AI_KEY"
_ALLOWED_KEY_ENV_PREFIX = "STEPIK_GRADER_"

# issue #812 (SECD-02): по http данные и ключ идут открытым текстом. Локальный
# провайдер (ollama) по http — штатный сценарий и остаётся разрешённым, а вот
# http на удалённый хост уже нет: там та же отправка, но через сеть.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# issue #812 (TREND-01): o-серия OpenAI (o1, o3-mini, o4-preview…). Граница
# после номера обязательна, иначе под шаблон попал бы, например, "opus".
_O_SERIES_RE = re.compile(r"^o\d+(?:[-_.]|$)")

# issue #975 (TRE-1-01): семейства, пришедшие после #812 и живущие по тому же
# контракту — `max_completion_tokens` вместо `max_tokens`, температура не
# принимается. `gpt-5` и его варианты (`gpt-5.1`, `gpt-5-mini`) под o-серию не
# подходили ни одним символом, а `deepseek-reasoner` не совпадал с маркером
# «reasoning» ровно на одну букву. Обоим уходил обычный payload, провайдер
# отвергал его целиком, и подсказки молча не работали.
_GPT5_RE = re.compile(r"^gpt-5(?:[-_.]|$)")

# Пометка ответа как сгенерированного ИИ (ADR-0003 §5 — не выдаётся за истину
# грейдера). Локализована; сам текст подсказки — на языке модели (см. промпт).
AI_MARKER_RU = "🤖 AI-подсказка (сгенерировано ИИ, может ошибаться)"
AI_MARKER_EN = "🤖 AI hint (AI-generated, may be wrong)"

# Максимум символов в ответе (сверх max_tokens сервера) — жёсткий предохранитель
# от простыни, если провайдер проигнорировал max_tokens.
_MAX_HINT_CHARS = 1200

_SYSTEM_RU = (
    "Ты — дружелюбный помощник-репетитор по Python для новичка на курсе "
    "«Поколение Python». Объясни КОРОТКО (2–4 предложения), ПОЧЕМУ решение не "
    "прошло тест, опираясь ТОЛЬКО на приведённый контекст (вердикт, вид ошибки, "
    "diff ожидаемого/полученного, трейсбек, карточку). НЕ выдумывай фактов, "
    "которых нет в контексте. НЕ выдавай готовый код целиком — подскажи "
    "направление исправления. Отвечай по-русски."
)
_SYSTEM_EN = (
    "You are a friendly Python tutor for a beginner. Explain BRIEFLY (2-4 "
    "sentences) WHY the solution failed the test, grounded ONLY in the provided "
    "context (verdict, failure kind, expected/actual diff, traceback, card). Do "
    "NOT invent facts absent from the context. Do NOT output a full solution — "
    "point at the fix. Answer in English."
)


#: Причины, по которым подсказки нет. Значения — ключи локали
#: (``ai_reason_<value>``), поэтому вызывающий волен показать их как хочет:
#: CLI печатает строку, web отдаёт ``message_id``.
_HTTP_REASONS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    429: "rate_limited",
}


def _http_error_reason(status: int) -> str:
    """Код ответа провайдера в причину отказа (issue #975)."""
    if status in _HTTP_REASONS:
        return _HTTP_REASONS[status]
    return "server_error" if status >= 500 else "http_error"


@dataclass(frozen=True)
class AiHintOutcome:
    """Результат обращения к AI-каналу: текст либо причина его отсутствия.

    ``reason`` — короткий ключ (``unauthorized``, ``rate_limited``,
    ``bad_request``, ``network``, ``empty``…), а не готовая фраза: локаль и
    способ показа выбирает вызывающий слой. ``None`` в обоих полях означает
    «канал не настроен» — это не ошибка, а выключенная функция.
    """

    text: str | None
    reason: str | None


@dataclass(frozen=True)
class FailureContext:
    """Заземляющий контекст одного упавшего кейса для AI-объяснения.

    Заполняется вызывающей стороной (CLI-слой) из ``TestResult`` + якорей
    (``insights.failure_kind``, ``error_glossary``/``lint`` карточки). Модуль
    остаётся развязанным от конкретных API якорей — принимает готовые строки.
    """

    verdict: str  # WA / RE / TLE …
    lang: str = "ru"
    case_input: str = ""
    expected: str = ""
    actual: str = ""
    diff: str = ""
    error: str = ""  # трейсбек/stderr (для RE)
    failure_kind: str = ""  # таксономия insights.failure_kind
    card_text: str = ""  # текст карточки error_glossary/lint
    code: str = ""  # исходный код решения (усечённый)
    grounding: str = ""  # top-k карточки глоссария по концептам кода (issue #544)


def is_configured(config: object) -> bool:
    """AI-канал настроен? (есть ``ai_base_url`` и ``ai_model``). Ключ не требуется
    — локальный ollama работает без него (ADR-0003 §3)."""
    return bool(getattr(config, "ai_base_url", None) and getattr(config, "ai_model", None))


def env_name_is_allowed(env_name: str) -> bool:
    """Разрешено ли читать ключ из переменной с таким именем (issue #812).

    ``SECD-01``: имя переменной берётся из ``pyproject.toml``, а тот ищется от
    cwd вверх — то есть приезжает вместе со скачанной или склонированной папкой
    задач. Проверено прогоном: чужой файл с ``ai_api_key_env = "GITHUB_TOKEN"``
    и ``ai_base_url = "http://evil.example/v1"`` заставлял грейдер прочитать
    посторонний токен и отправить его как ``Bearer`` вместе с кодом решения.

    Поэтому имя обязано быть либо дефолтным, либо из собственного пространства
    ``STEPIK_GRADER_*``: свой ключ пользователь так назвать может, а вот
    ``GITHUB_TOKEN``/``AWS_SECRET_ACCESS_KEY`` — уже нет.
    """
    name = (env_name or "").strip()
    return name == _DEFAULT_KEY_ENV or name.startswith(_ALLOWED_KEY_ENV_PREFIX)


def _system_prompt(config: object, lang: str) -> str:
    """Системный промпт: свой из конфига либо встроенный (issue #812, ``VIS-02``).

    Встроенный текст обращается к «новичку на курсе „Поколение Python“» —
    верно для основной аудитории, но грейдер применим к любому курсу и любому
    уровню, а переопределить формулировку было нечем. ``ai_system_prompt``
    задаёт её целиком: пустое значение (дефолт) оставляет встроенный вариант,
    поэтому поведение по умолчанию не меняется.

    Свой промпт — один на оба языка: если пользователь его задал, он и решает,
    на каком языке отвечать модели.
    """
    custom = str(getattr(config, "ai_system_prompt", "") or "").strip()
    if custom:
        return custom
    return _SYSTEM_EN if lang == "en" else _SYSTEM_RU


def _is_reasoning_model(model: str) -> bool:
    """Похоже ли имя модели на reasoning-семейство (issue #812, ``TREND-01``).

    У o-серии OpenAI и «thinking»-моделей другой контракт: лимит называется
    ``max_completion_tokens``, а ``temperature`` не принимается вовсе — обычный
    payload отвергается целиком с 400, и подсказки молча не работают.

    Матч по имени, а не по запросу к провайдеру: канал обязан оставаться
    офлайн-дешёвым, а список семейств меняется медленнее, чем релизы моделей.
    Незнакомая reasoning-модель просто получит прежний payload — деградация
    та же, что была до фикса, не хуже.
    """
    name = (model or "").strip().lower()
    name = name.rsplit("/", 1)[-1]  # провайдерский префикс: "openai/o3-mini"
    # issue #975: «reasoner» — отдельный маркер, а не форма слова «reasoning»:
    # подстрочный поиск их не связывает, и `deepseek-reasoner` пролетал мимо.
    if any(marker in name for marker in ("reasoning", "thinking", "reasoner")):
        return True
    return bool(_O_SERIES_RE.match(name) or _GPT5_RE.match(name))


def base_url_is_allowed(base_url: str) -> bool:
    """Можно ли слать код и ключ на этот адрес (issue #812, ``SECD-02``).

    Схема не проверялась вовсе — прогон показал, что до запроса доходил даже
    ``ftp://``. Правило: ``https`` куда угодно; ``http`` — только на петлю, где
    трафик не покидает машину (локальный ollama — штатный сценарий ADR-0003).
    ``http`` на удалённый хост означал бы код решения и Bearer-ключ открытым
    текстом по сети.
    """
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    return (parsed.hostname or "").lower() in _LOOPBACK_HOSTS


def _resolve_key(config: object) -> str | None:
    """Значение ключа из env-переменной (имя — ``ai_api_key_env``); регистрирует
    его в ``diag_log`` для редакции. ``None``, если переменная не задана (локальный
    провайдер без ключа — это норма) или имя переменной не из своего
    пространства имён (issue #812)."""
    env_name = str(getattr(config, "ai_api_key_env", "") or "").strip()
    if not env_name:
        return None
    if not env_name_is_allowed(env_name):
        _log.warning(
            "ai_api_key_env=%r отклонено: допустимы только %s и имена с префиксом %s "
            "(issue #812) — ключ не читается",
            env_name,
            _DEFAULT_KEY_ENV,
            _ALLOWED_KEY_ENV_PREFIX,
        )
        return None
    key = (os.environ.get(env_name) or "").strip()
    if not key:
        return None
    register_secret(key)  # чтобы случайное попадание в лог было замаскировано
    return key


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _build_user_prompt(ctx: FailureContext) -> str:
    """Собрать заземляющий user-промпт из контекста (только непустые секции)."""
    parts: list[str] = [f"Вердикт: {ctx.verdict}"]
    if ctx.failure_kind:
        parts.append(f"Тип падения: {ctx.failure_kind}")
    if ctx.case_input:
        parts.append(f"Вход теста:\n{_clip(ctx.case_input, 500)}")
    if ctx.expected or ctx.actual:
        parts.append(f"Ожидалось:\n{_clip(ctx.expected, 500)}")
        parts.append(f"Получено:\n{_clip(ctx.actual, 500)}")
    if ctx.diff:
        parts.append(f"Diff:\n{_clip(ctx.diff, 800)}")
    if ctx.error:
        parts.append(f"Ошибка/трейсбек:\n{_clip(ctx.error, 1000)}")
    if ctx.card_text:
        parts.append(f"Справка (карточка):\n{_clip(ctx.card_text, 800)}")
    if ctx.grounding:
        parts.append(f"Релевантные карточки глоссария:\n{_clip(ctx.grounding, 1200)}")
    if ctx.code:
        parts.append(f"Код решения:\n{_clip(ctx.code, 1500)}")
    return "\n\n".join(parts)


def _post_chat(
    config: object, messages: list[dict[str, str]], key: str | None
) -> tuple[str | None, str | None]:
    """Один POST к ``{ai_base_url}/chat/completions``: пара «текст, причина отказа».

    Любая ошибка (сеть/таймаут/HTTP/битый JSON/неожиданная форма) даёт текст
    ``None``: грейдинг не должен падать из-за AI-канала (ADR-0003 §4). Но
    причина возвращается рядом (issue #975), а не растворяется в логе: отказ
    провайдера обязан быть отличим от «канал не настроен».
    """
    # requests — уже runtime-зависимость (OAuth/downloader); импорт внутри, чтобы
    # модуль грузился даже там, где requests отсутствует (тогда просто skip).
    try:
        import requests
    except ImportError:  # pragma: no cover — requests в runtime-зависимостях
        return None, None

    base_url = str(getattr(config, "ai_base_url", "") or "").rstrip("/")
    if not base_url_is_allowed(base_url):
        _log.warning(
            "ai_base_url=%r отклонён: https — куда угодно, http — только на петлю "
            "(issue #812); запрос не отправлен",
            base_url,
        )
        return None, None
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    model = str(getattr(config, "ai_model", "") or "")
    payload: dict[str, object] = {
        "model": getattr(config, "ai_model", None),
        "messages": messages,
        "stream": False,
    }
    # issue #812 (TREND-01): reasoning-модели (o1/o3/o4, «thinking») отвергают
    # запрос с `max_tokens` и `temperature` — 400 на весь payload, то есть
    # подсказки молча не работают у тех, кто включил именно такую модель. Для
    # них лимит называется `max_completion_tokens`, а температура не задаётся.
    max_tokens = int(getattr(config, "ai_max_tokens", 400))
    if _is_reasoning_model(model):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["max_tokens"] = max_tokens
        payload["temperature"] = 0.2
    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            timeout=float(getattr(config, "ai_timeout_seconds", 20.0)),
        )
        # issue #975 (TRE-1-03): отказ провайдера обязан быть слышен. Прежде
        # `raise_for_status` уходил в общий `except`, и 401 («ключ не принят»),
        # 429 («лимит исчерпан») и 400 («модель отвергла payload») давали ровно
        # то же, что «канал не настроен», — пустоту. Пользователь, включивший
        # подсказки, не мог отличить «не работает» от «выключено».
        if resp.status_code >= 400:
            _log.debug("ai_hints: провайдер отверг запрос: HTTP %s", resp.status_code)
            return None, _http_error_reason(resp.status_code)
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        _log.debug("ai_hints: пропуск (ошибка канала): %s", exc)
        return None, "network"
    text = str(content or "").strip()
    return (text or None), None


def explain_failure_detailed(ctx: FailureContext, config: object) -> AiHintOutcome:
    """AI-объяснение упавшего кейса вместе с причиной отказа (issue #975).

    Возвращает :class:`AiHintOutcome`: либо готовый текст, либо ``reason`` —
    почему подсказки нет. Прежде обе ситуации сводились к ``None``, и
    «провайдер отверг ключ» выглядело для пользователя ровно как «канал
    выключен»: он включал подсказки, ничего не происходило, и узнать причину
    было негде — она уходила в debug-лог, который по умолчанию не пишется.

    Никогда не бросает: грейдинг не падает из-за AI-канала (ADR-0003 §4).
    """
    if not is_configured(config):
        return AiHintOutcome(text=None, reason=None)
    system = _system_prompt(config, ctx.lang)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _build_user_prompt(ctx)},
    ]
    text, reason = _post_chat(config, messages, _resolve_key(config))
    if not text:
        return AiHintOutcome(text=None, reason=reason or "empty")
    marker = AI_MARKER_EN if ctx.lang == "en" else AI_MARKER_RU
    return AiHintOutcome(text=f"{marker}:\n{_clip(text, _MAX_HINT_CHARS)}", reason=None)


def explain_failure(ctx: FailureContext, config: object) -> str | None:
    """AI-объяснение упавшего кейса или ``None`` (skip).

    Тонкая обёртка над :func:`explain_failure_detailed` — имя остаётся в
    ``__all__`` и в ADR-0003. Причина отказа здесь теряется по построению;
    вызывающим, которые её показывают, нужен detailed-вариант.
    """
    return explain_failure_detailed(ctx, config).text
