"""diag_log.py — opt-in диагностическое логирование сетевого/OAuth-слоя (issue #146).

Архитектурный слой: Utilities (leaf — только stdlib: ``logging``/``os``/``re``/
``pathlib``, без project-импортов и без внешних зависимостей).

Реализует дизайн-контракт [docs/dev/logging.md](../../../docs/dev/logging.md) (issue
#150): по умолчанию **тихо**, включается явным opt-in (флаг CLI или переменная
окружения ``STEPIK_GRADER_LOG``), пишет человекочитаемый лог в
``stepik_diagnostics/grader.log`` и **обязательно редактирует секреты**
(токены, ``client_secret``, заголовок ``Authorization``) до записи — это самая
частая точка утечки токена (SECURITY.md).

Бизнес-вывод остаётся через ``_console`` (rich); этот логгер — отдельный
диагностический канал, включаемый по запросу для разбора проблем сети/OAuth/
парсинга у пользователя.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

__all__ = [
    "DIAGNOSTICS_DIR",
    "configure_diagnostics",
    "get_logger",
    "redact",
    "register_secret",
]

_ROOT = "stepik_grader"
DIAGNOSTICS_DIR = Path("stepik_diagnostics")  # держится в .gitignore
_LOG_FILENAME = "grader.log"
_ENV_VAR = "STEPIK_GRADER_LOG"

# off/None → выключено; иначе — уровень stdlib logging.
_LEVELS: dict[str, int] = {
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}
_MASK = "***redacted***"

# Точные секрет-значения (токены/секреты), зарегистрированные в рантайме — их
# маскируем в любом сообщении, даже если формат не совпал с паттернами ниже.
_SECRETS: set[str] = set()

# Паттерны секретов в тексте: заголовок Bearer, query-параметры и JSON-поля с
# токенами/секретами/кодом авторизации. group(1) — префикс (сохраняем),
# group(2) — секрет (маскируем).
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(Bearer\s+)([^\s\"'&]+)", re.IGNORECASE),
    re.compile(
        r"((?:access_token|refresh_token|client_secret|token|code)=)([^&\s\"']+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\"(?:access_token|refresh_token|client_secret|token|code)\"\s*:\s*\")([^\"]+)",
        re.IGNORECASE,
    ),
)


def register_secret(value: str | None) -> None:
    """Зарегистрировать точное секрет-значение для маскирования во всех логах.

    Вызывается, когда токен/секрет становится известен в рантайме (напр. после
    обмена OAuth) — так его случайное попадание в любое лог-сообщение будет
    заменено на ``***redacted***``. Короткие/пустые значения игнорируются, чтобы
    не маскировать безобидный текст.
    """
    v = (value or "").strip()
    if len(v) >= 8:
        _SECRETS.add(v)


def redact(text: str) -> str:
    """Отредактировать секреты в строке: паттерны токенов/заголовков + известные значения."""
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + _MASK, text)
    # issue #564: снимок множества перед итерацией — под многопоточным web
    # другой поток может параллельно вызвать register_secret (_SECRETS.add),
    # а прямая итерация set во время .replace() дала бы "Set changed size
    # during iteration". tuple(_SECRETS) строится атомарно (GIL, C-уровень).
    for secret in tuple(_SECRETS):
        if secret in text:
            text = text.replace(secret, _MASK)
    return text


# issue #410 (S5): голый Formatter для редакции трейсбэка — formatException
# доступен и без привязки к конкретному хендлеру.
_EXC_FORMATTER = logging.Formatter()


class _RedactingFilter(logging.Filter):
    """Редактирует секреты в сообщении, трейсбэке и stack_info записи до вывода."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        # issue #410 (S5): редактировать и трейсбэк — при exc_info=True он иначе
        # утёк бы мимо редакции (record.msg покрывает только само сообщение).
        # Готовим redacted exc_text заранее: Formatter.format видит непустой
        # exc_text и не переформатирует exc_info повторно.
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(_EXC_FORMATTER.formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def get_logger(name: str) -> logging.Logger:
    """Дочерний диагностический логгер модуля (напр. ``get_logger("downloader")``)."""
    return logging.getLogger(f"{_ROOT}.{name}")


def _resolve_level(level: str | None) -> int | None:
    """Уровень из явного аргумента либо ``STEPIK_GRADER_LOG`` (``None`` = выключено)."""
    key = (level if level is not None else os.getenv(_ENV_VAR, "")).strip().lower()
    return _LEVELS.get(key)  # off/пусто/неизвестное → None (выключено, консервативно)


def configure_diagnostics(level: str | None = None, *, log_dir: Path | None = None) -> bool:
    """Настроить диагностический логгер (opt-in). Вернуть ``True``, если включён.

    ``level`` — ``"warning"``/``"info"``/``"debug"`` либо ``None`` (тогда берётся
    из ``STEPIK_GRADER_LOG``; ``off``/пусто/неизвестное → выключено). При
    включении создаётся ``<log_dir>/grader.log`` с редакцией секретов и меткой
    времени/уровня. Идемпотентно: повторный вызов переустанавливает состояние.
    """
    root = logging.getLogger(_ROOT)
    for handler in list(root.handlers):  # идемпотентность — снять прежние хендлеры
        root.removeHandler(handler)
        handler.close()

    resolved = _resolve_level(level)
    if resolved is None:
        root.addHandler(logging.NullHandler())  # тихо, без файла
        root.setLevel(logging.CRITICAL + 1)
        root.propagate = False
        return False

    directory = log_dir if log_dir is not None else DIAGNOSTICS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(directory / _LOG_FILENAME, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.addFilter(_RedactingFilter())  # редакция до записи в файл
    root.addHandler(file_handler)
    root.setLevel(resolved)
    root.propagate = False
    return True
