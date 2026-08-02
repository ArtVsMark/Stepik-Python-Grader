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
from dataclasses import dataclass
from urllib.parse import urlparse

from stepik_grader.core.diag_log import get_logger, register_secret

__all__ = [
    "AI_MARKER_EN",
    "AI_MARKER_RU",
    "FailureContext",
    "base_url_is_allowed",
    "env_name_is_allowed",
    "explain_failure",
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


def _post_chat(config: object, messages: list[dict[str, str]], key: str | None) -> str | None:
    """Один POST к ``{ai_base_url}/chat/completions``; текст ответа или ``None``.

    Любая ошибка (сеть/таймаут/HTTP/битый JSON/неожиданная форма) → ``None``:
    грейдинг не должен падать из-за AI-канала (ADR-0003 §4)."""
    # requests — уже runtime-зависимость (OAuth/downloader); импорт внутри, чтобы
    # модуль грузился даже там, где requests отсутствует (тогда просто skip).
    try:
        import requests
    except ImportError:  # pragma: no cover — requests в runtime-зависимостях
        return None

    base_url = str(getattr(config, "ai_base_url", "") or "").rstrip("/")
    if not base_url_is_allowed(base_url):
        _log.warning(
            "ai_base_url=%r отклонён: https — куда угодно, http — только на петлю "
            "(issue #812); запрос не отправлен",
            base_url,
        )
        return None
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": getattr(config, "ai_model", None),
        "messages": messages,
        "max_tokens": int(getattr(config, "ai_max_tokens", 400)),
        "temperature": 0.2,
        "stream": False,
    }
    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            timeout=float(getattr(config, "ai_timeout_seconds", 20.0)),
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        _log.debug("ai_hints: пропуск (ошибка канала): %s", exc)
        return None
    text = str(content or "").strip()
    return text or None


def explain_failure(ctx: FailureContext, config: object) -> str | None:
    """AI-объяснение упавшего кейса или ``None`` (skip).

    ``None`` при: не настроен канал (:func:`is_configured` ложно), пустой ответ
    или любая ошибка сети/провайдера. При успехе — помеченный AI-generated текст,
    усечённый до :data:`_MAX_HINT_CHARS` (ADR-0003 §4, §5). Никогда не бросает.
    """
    if not is_configured(config):
        return None
    system = _SYSTEM_EN if ctx.lang == "en" else _SYSTEM_RU
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _build_user_prompt(ctx)},
    ]
    text = _post_chat(config, messages, _resolve_key(config))
    if not text:
        return None
    marker = AI_MARKER_EN if ctx.lang == "en" else AI_MARKER_RU
    return f"{marker}:\n{_clip(text, _MAX_HINT_CHARS)}"
