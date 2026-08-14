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
import logging.handlers
import os
import platform
import re
import sys
import warnings
from pathlib import Path

__all__ = [
    "DIAGNOSTICS_DIR",
    "LOG_BACKUP_COUNT",
    "LOG_MAX_BYTES",
    "MIN_SECRET_LEN",
    "configure_diagnostics",
    "get_logger",
    "redact",
    "register_secret",
]

_ROOT = "stepik_grader"
DIAGNOSTICS_DIR = Path("stepik_diagnostics")  # держится в .gitignore
_LOG_FILENAME = "grader.log"
_ENV_VAR = "STEPIK_GRADER_LOG"

# issue #999 (OPS-1-05): потолок файла и число ротаций. Прежде лог рос без
# границы — под `--serve` с `debug` он пишется на каждый запрос, и «включил
# диагностику и забыл» означало гигабайты на диске пользователя. Пять мегабайт
# на файл — это десятки тысяч строк, то есть заведомо больше, чем нужно для
# разбора одного инцидента; три архива держат историю в пределах 20 МБ.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# issue #999 (SEC-3-06): ниже этой длины значение не регистрируется как секрет.
# Порог нужен: зарегистрировав «ok» или «1», мы превратили бы в
# `***redacted***` половину лога и сделали бы его нечитаемым — то есть отняли
# бы у диагностики смысл. Прежние восемь символов отсекали и настоящие короткие
# ключи; четыре — граница, ниже которой значение практически не бывает
# секретом. Отказ больше не молчаливый: он виден в самом логе (без значения).
MIN_SECRET_LEN = 4

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

# Имена полей, значение которых считается секретом. issue #813 (SECD-05):
# список расширен — `api_key` не ловился ВООБЩЕ (ни в JSON, ни в repr), хотя
# именно им настраивается AI-канал подсказок; `secret`/`password`/`api_token`
# отсутствовали так же.
_SECRET_KEYS = (
    "access_token",
    "refresh_token",
    "client_secret",
    "api_key",
    "apikey",
    "api_token",
    "auth_token",
    "password",
    "secret",
    "token",
    "code",
)
# issue #999: разделитель в имени ключа — любой из `_`/`-`. Список выше пишется
# с подчёркиваниями (так ключи выглядят в JSON и в Python), а в командной строке
# те же секреты приходят через дефис: `--ai-api-key=sk-live-…` не содержал
# подстроки `api_key` и уезжал в лог целиком. Найдено тестом шапки прогона,
# которая как раз печатает `sys.argv`.
_KEYS_RE = "|".join(key.replace("_", "[-_]") for key in _SECRET_KEYS)

# Отдельного паттерна под префикс (`--ai-api-key`, `stepik_access_token`) НЕ
# нужно: поиск идёт по вхождению, и `api-key=` находится внутри `--ai-api-key=`
# сам собой — префикс просто остаётся вне матча, то есть сохраняется в логе.
# Первая редакция правки ставила перед ключом `[\w-]*`, и это давало
# КВАДРАТИЧНЫЙ рост: на строке из 10 000 символов без `=` поиск занимал 4.7 с
# против 0.05 с на тысяче — прогон набора вставал на `test_feedback`. Тот же
# класс, что issue #952: вешает не объём данных, а работа по их подготовке.

# Паттерны секретов в тексте: заголовки Authorization, query-параметры и поля
# структур. group(1) — префикс (сохраняем), group(2) — секрет (маскируем).
#
# issue #813 (SECD-05): поля ловятся и в repr-форме Python (одинарные кавычки).
# Прежний паттерн требовал двойных, поэтому типовая строка из traceback'а или
# `print(secrets)` — `{'access_token': 'ya29...'}` — уезжала нетронутой в
# prefilled-URL публичного GitHub issue через форму обратной связи.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer и Basic: у Basic в base64 могут быть '=' на конце — их сохраняем.
    re.compile(r"((?:Bearer|Basic)\s+)([^\s\"'&]+)", re.IGNORECASE),
    re.compile(rf"((?:{_KEYS_RE})=)([^&\s\"']+)", re.IGNORECASE),
    # issue #964 (ADD-4-03): `key='value'` и `key="value"` — форма `repr()` и
    # traceback'а, то есть ровно то, что пользователь копирует в форму
    # обращения. Паттерн выше требует, чтобы после `=` шла НЕ кавычка, а
    # JSON-паттерн ниже — кавычек вокруг самого ключа, поэтому
    # `StepikClient(access_token='ya29…')` не подходил ни под один и уезжал в
    # prefilled-URL публичного issue целиком. Кавычка запоминается и требуется
    # той же на закрытии — смешанное `token='…"` не съест остаток строки.
    re.compile(rf"((?:{_KEYS_RE})=([\"']))(?:(?!\2).)+", re.IGNORECASE),
    # "key": "value" и 'key': 'value' — кавычка запоминается и требуется той же
    # на закрытии, поэтому смешанные `"key': ...` не матчатся случайно.
    re.compile(rf"([\"'](?:{_KEYS_RE})[\"']\s*:\s*([\"']))(?:(?!\2).)+", re.IGNORECASE),
)


def register_secret(value: str | None) -> None:
    """Зарегистрировать точное секрет-значение для маскирования во всех логах.

    Вызывается, когда токен/секрет становится известен в рантайме (напр. после
    обмена OAuth) — так его случайное попадание в любое лог-сообщение будет
    заменено на ``***redacted***``. Пустые и слишком короткие значения
    (< :data:`MIN_SECRET_LEN`) игнорируются, чтобы не маскировать безобидный
    текст.

    issue #999 (SEC-3-06): отказ перестал быть немым. Прежде значение короче
    восьми символов молча не попадало в набор — вызывающий считал секрет
    защищённым, а он уходил в лог как есть. Теперь порог ниже (четыре символа),
    а отказ виден строкой в самом логе: длина названа, значение — нет.
    """
    v = (value or "").strip()
    if not v:
        return
    if len(v) < MIN_SECRET_LEN:
        get_logger("diag").debug(
            "секрет длиной %d не зарегистрирован: короче %d символов, "
            "маскирование сделало бы лог нечитаемым",
            len(v),
            MIN_SECRET_LEN,
        )
        return
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
    # issue #999 (OPS-1-04): диагностика — вспомогательный канал, и её отказ не
    # повод ронять проверку решений. Недоступный каталог (только чтение,
    # переполненный диск, путь занят файлом) прежде выбрасывал OSError прямо из
    # `--log debug`, то есть попытка РАЗОБРАТЬСЯ в проблеме добавляла вторую.
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # issue #999 (OPS-1-05): с ротацией — иначе под `--serve --log debug`
        # файл растёт без границы.
        file_handler: logging.Handler = logging.handlers.RotatingFileHandler(
            directory / _LOG_FILENAME,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        warnings.warn(
            f"диагностический лог не включён: {directory} недоступен ({exc.strerror or exc}). "
            "Прогон продолжается без лога; укажите другой каталог параметром log_dir.",
            UserWarning,
            stacklevel=2,
        )
        root.addHandler(logging.NullHandler())
        root.setLevel(logging.CRITICAL + 1)
        root.propagate = False
        return False
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.addFilter(_RedactingFilter())  # редакция до записи в файл
    root.addHandler(file_handler)
    root.setLevel(resolved)
    root.propagate = False
    _log_run_header()
    return True


def _log_run_header() -> None:
    """Записать шапку прогона: версия, ОС, Python, команда (issue #999, OPS-1-06).

    Без неё лог начинается с середины: разбирающий не знает ни версии грейдера,
    ни ОС, ни того, чем запускали, — а именно это первым спрашивают у
    пользователя, приславшего лог. Аргументы проходят ту же редакцию, что и
    остальные записи (токен попадает в командную строку, например у
    ``--ai-api-key``), потому что фильтр стоит на хендлере.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            grader_version = version("stepik-python-grader")
        except PackageNotFoundError:  # запуск из исходников без установки
            grader_version = "не установлен (запуск из исходников)"
    except Exception as exc:  # pragma: no cover — importlib есть всегда
        grader_version = f"неизвестна ({exc})"
    logger = get_logger("diag")
    logger.info("грейдер %s · %s · Python %s", grader_version, platform.platform(), sys.version)
    logger.info("команда: %s", " ".join(sys.argv))
