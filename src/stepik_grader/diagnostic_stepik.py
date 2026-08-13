"""diagnostic_stepik.py — OAuth-диагностика шага Stepik через API.

Архитектурный слой: CLI-инструмент / диагностика.
OAuth делегируется в oauth_flow (фасад поверх stepik_client):
  - load_secrets — импортируется из oauth_flow
  - authorize_via_browser — импортируется из oauth_flow
  - make_session — импортируется из oauth_flow
  - API_HOST — читается как stepik_client.API_HOST (переопределяем окружением)

Запуск:
    python -m stepik_grader.diagnostic_stepik
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
from typing import Any

import requests

# issue #997 (STR-3-06): импортируем МОДУЛЬ, а не значение — иначе
# переопределение хоста через STEPIK_GRADER_API_HOST не видно диагностике,
# и она проверяет боевой stepik.org, пока грейдер ходит на стенд.
from stepik_grader.core import stepik_client
from stepik_grader.core.diag_log import DIAGNOSTICS_DIR, configure_diagnostics, get_logger, redact
from stepik_grader.core.i18n import load_locale_messages
from stepik_grader.core.oauth_flow import create_user_session, load_secrets_dict
from stepik_grader.downloader import parse_stepik_step_url

__all__ = [
    "OAUTH_TIMEOUT_SECONDS",
    "api_get",
    "build_diagnostic_result",
    "collect_string_candidates",
    "create_user_session",
    "extract_zip_url_from_step_data",
    "extract_zip_url_from_text",
    "get_lesson_data",
    "get_step_data",
    "get_step_data_by_position",
    "main",
    "print_result_summary",
    "save_json",
]

# Вывод через rich с graceful fallback на print() (инвариант CLAUDE.md).
# Свой локальный _console (leaf-совместимо, не тянет core/reporter), issue #354.
try:
    from rich.console import Console

    _console: Console | None = Console()
    _RICH = True
except ImportError:  # pragma: no cover
    _console = None
    _RICH = False


def _print(text: str) -> None:
    """Печать статусной строки через rich (markup off — безопасно для путей/URL)."""
    if _RICH and _console is not None:
        _console.print(text, markup=False)
    else:
        print(text)


# Задача 6: таймаут ожидания OAuth-кода от браузера
_log = get_logger("diagnostic")  # issue #831 (DEV-12): стек падения в opt-in лог

OAUTH_TIMEOUT_SECONDS = 120

# issue #997 (JRN-3A-05): приглашения и сообщения — через каталог локалей, как в
# downloader_config. Раньше три вопроса были по-английски вперемешку с русскими
# ответами: «Enter Stepik step URL» → «✅ secrets.json успешно прочитан».
_LANG = "ru"
_FALLBACK_LANG = "ru"


def set_lang(lang: str) -> None:
    """Задать язык вывода диагностики (тот же приём, что в загрузчике)."""
    global _LANG
    _LANG = lang


def _t(key: str, /, **kwargs: object) -> str:
    """Строка каталога на текущем языке; отсутствующий ключ показывается как есть."""
    messages = load_locale_messages(_LANG) or load_locale_messages(_FALLBACK_LANG)
    template = messages.get(key, key)
    return template.format(**kwargs) if kwargs else template


# ---------------------------------------------------------------------------
# OAuth2 — адаптер поверх oauth_flow.authorize_via_browser
# ---------------------------------------------------------------------------


# Своей реализации авторизации у диагностики нет (issue #1017): имя
# ``create_user_session`` — тот же ``oauth_flow.create_user_session``, что
# используют загрузчик, импорт эталонов и стенд корпуса. Прежняя локальная
# версия принимала три отдельных аргумента и звала ``authorize_via_browser``
# сразу, ничего не зная о сохранённых токенах: при полностью рабочем
# ``secrets.json`` диагностика открывала браузер и падала по таймауту ожидания
# кода — то есть отказывала ровно в той ситуации, ради которой её запускают.
# Реэкспорт сохраняет публичное имя модуля (``__all__``), но поведение теперь
# одно на весь пакет: валидный access_token → обмен refresh_token → браузер.


# ---------------------------------------------------------------------------
# Диагностические API-обёртки
# ---------------------------------------------------------------------------


def api_get(session: requests.Session, url: str) -> dict[str, Any]:
    """GET-запрос к Stepik API; проверяет Content-Type и статус."""
    response = session.get(url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        raise ValueError(f"Ожидался JSON от API, но получен Content-Type: {content_type}")
    data: dict[str, Any] = response.json()
    return data


def get_lesson_data(session: requests.Session, lesson_id: int) -> dict[str, Any]:
    """Получить данные урока по lesson_id."""
    data = api_get(session, f"{stepik_client.API_HOST}/api/lessons/{lesson_id}")
    lessons = data.get("lessons", [])
    if not lessons:
        raise ValueError(f"API не вернул lesson для id={lesson_id}")
    lesson: dict[str, Any] = lessons[0]
    return lesson


def get_step_data(session: requests.Session, step_id: int) -> dict[str, Any]:
    """Получить данные шага по step_id."""
    data = api_get(session, f"{stepik_client.API_HOST}/api/steps/{step_id}")
    steps = data.get("steps", [])
    if not steps:
        raise ValueError(f"API не вернул step для step_id={step_id}")
    step: dict[str, Any] = steps[0]
    return step


def get_step_data_by_position(
    session: requests.Session,
    lesson_id: int,
    step_position: int,
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Получить step_id, lesson, step_data по позиции шага в уроке."""
    lesson = get_lesson_data(session, lesson_id)
    steps = lesson.get("steps", [])
    if not steps:
        raise ValueError("В lesson нет списка steps.")
    if step_position < 1 or step_position > len(steps):
        raise ValueError(f"В уроке {len(steps)} шаг(ов), но запрошен step={step_position}")
    step_id = steps[step_position - 1]
    step_data = get_step_data(session, step_id)
    return step_id, lesson, step_data


# ---------------------------------------------------------------------------
# Утилиты: JSON, ZIP-поиск, диагностический вывод
# ---------------------------------------------------------------------------


def save_json(
    output_dir: pathlib.Path,
    filename: str,
    payload: dict[str, Any],
) -> pathlib.Path:
    """Сохранить payload как JSON-файл в output_dir, отредактировав секреты.

    issue #950 (находка OPS-1-02): ответы API писались на диск как есть, поэтому
    файлы ``stepik_diagnostics/*.json`` могли содержать токены и ключи —
    ``docs/dev/logging.md`` п. 4 запрещает это ровно потому, что эти файлы и
    создаются для того, чтобы приложить их к issue. Редакция идёт по
    сериализованному тексту (``diag_log.redact``): маскируются и известные
    зарегистрированные секреты, и всё, что подходит под паттерны токенов —
    включая поля, о которых мы заранее не знаем.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / filename
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    file_path.write_text(redact(serialized), encoding="utf-8")
    return file_path


def extract_zip_url_from_text(text: str) -> str | None:
    """Найти первый ZIP-URL в произвольном тексте (HTML, Markdown, JSON)."""
    decoded = html.unescape(text)
    patterns = [
        r"https://stepik\.org/media/attachments/[^\s\"'<>()]+\.zip",
        r"https://stepic\.org/media/attachments/[^\s\"'<>()]+\.zip",
        r"https://ucarecdn\.com/[^\s\"'<>()]+\.zip",
        r"https://[^\s\"'<>()]+\.zip",
    ]
    for pattern in patterns:
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            return match.group(0)
    markdown_patterns = [
        r"\[[^\]]+\]\((https://stepik\.org/media/attachments/[^)]+\.zip)\)",
        r"\[[^\]]+\]\((https://stepic\.org/media/attachments/[^)]+\.zip)\)",
        r"\[[^\]]+\]\((https://ucarecdn\.com/[^)]+\.zip)\)",
        r"\[[^\]]+\]\((https://[^)]+\.zip)\)",
    ]
    for pattern in markdown_patterns:
        match = re.search(pattern, decoded, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def collect_string_candidates(payload: object) -> list[str]:
    """Рекурсивно собрать все строки из произвольной JSON-структуры."""
    candidates: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(payload)
    candidates.append(json.dumps(payload, ensure_ascii=False))
    return candidates


def extract_zip_url_from_step_data(step_data: dict[str, Any]) -> str | None:
    """Найти ZIP-URL во всех строковых полях step_data."""
    for candidate in collect_string_candidates(step_data):
        zip_url = extract_zip_url_from_text(candidate)
        if zip_url:
            return zip_url
    return None


def build_diagnostic_result(
    lesson: dict[str, Any],
    step_id: int,
    step_data: dict[str, Any],
    zip_url: str | None,
    lesson_path: pathlib.Path,
    step_path: pathlib.Path,
) -> dict[str, Any]:
    """Собрать итоговый словарь диагностики."""
    block = step_data.get("block", {}) if isinstance(step_data, dict) else {}
    return {
        "lesson_id": lesson.get("id"),
        "lesson_title": lesson.get("title"),
        "step_id": step_id,
        "step_position": step_data.get("position"),
        "block_name": block.get("name") if isinstance(block, dict) else None,
        "block_keys": list(block.keys()) if isinstance(block, dict) else [],
        "block_text_exists": isinstance(block, dict) and "text" in block,
        "zip_found": bool(zip_url),
        "zip_url": zip_url,
        "lesson_debug_path": str(lesson_path.resolve()),
        "step_debug_path": str(step_path.resolve()),
    }


def print_result_summary(
    result: dict[str, Any],
    output_dir: pathlib.Path,
) -> None:
    """Вывести читаемый итог диагностики в консоль."""
    _print("\n=== Результат диагностики ===")
    _print(f"Lesson ID:        {result['lesson_id']}")
    _print(f"Lesson title:     {result['lesson_title']}")
    _print(f"Step ID:          {result['step_id']}")
    _print(f"Step position:    {result['step_position']}")
    _print(f"Block name:       {result['block_name']}")
    _print(f"Block keys:       {result['block_keys']}")
    _print(f"Block text exists:{result['block_text_exists']}")
    _print(f"ZIP найден:       {'да' if result['zip_found'] else 'нет'}")
    if result["zip_url"]:
        _print(f"ZIP URL:          {result['zip_url']}")
    _print(f"Результаты сохранены в: {output_dir.resolve()}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Парсер аргументов диагностики (issue #997: RUN-4-03, RUN-5-03).

    Модуль начинался с трёх голых ``input()``, поэтому ``--help`` не печатал
    справку, а уходил в первый вопрос и падал ``EOFError`` на закрытом stdin:
    в скрипте и в CI инструмент был неработоспособен, а флаг, которым его
    пытались вызвать, молча съедался приглашением.
    """
    parser = argparse.ArgumentParser(
        prog="python -m stepik_grader.diagnostic_stepik",
        description=_t("diag_description"),
    )
    parser.add_argument("--url", help=_t("diag_arg_url"))
    parser.add_argument("--secrets", default="secrets.json", help=_t("diag_arg_secrets"))
    parser.add_argument("--out", default=str(DIAGNOSTICS_DIR), help=_t("diag_arg_out"))
    parser.add_argument("--lang", choices=["ru", "en"], default="ru", help=_t("diag_arg_lang"))
    return parser


def _ask(prompt_key: str, default: str = "") -> str:
    """Спросить значение в интерактивном режиме; иначе вернуть значение по умолчанию.

    Неинтерактивный запуск (CI, пайп, перехваченный stdin) — штатная ситуация, а
    не повод для трейсбека: спрашивать некого, поэтому вопрос не задаётся вовсе.
    Вызывающая сторона проверит обязательные значения и скажет об этом словами.
    ``OSError`` ловится наравне с ``EOFError``: закрытый или подменённый поток
    ввода даёт то одно, то другое в зависимости от окружения.
    """
    if not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        answer = input(_t(prompt_key)).strip()
    except (EOFError, KeyboardInterrupt, OSError):
        print()
        return default
    return answer or default


def main(argv: list[str] | None = None) -> int:
    """Точка входа: диагностика шага Stepik через OAuth API.

    Возвращает код процесса (issue #997, JRN-3A-03): ``0`` — данные шага
    получены, ``1`` — диагностика не состоялась. Прежде код был всегда ``0``, и
    триаж-инструмент не годился как автоматическая проверка: «всё сломано»
    выглядело для скрипта так же, как «всё в порядке».
    """
    args = _build_parser().parse_args(argv)
    set_lang(args.lang)
    step_url = args.url or _ask("diag_prompt_url")
    secrets_file = args.secrets or _ask("diag_prompt_secrets", "secrets.json")
    output_dir = pathlib.Path(args.out)
    if not step_url:
        _print(_t("diag_url_required"))
        return 1
    # issue #146: диагностическая утилита — включаем общий логгер (debug) в тот же
    # каталог; сетевые вызовы через stepik_client логируются с редакцией секретов.
    configure_diagnostics("debug", log_dir=output_dir)
    try:
        secrets_path = pathlib.Path(secrets_file)
        # issue #1017: читаем ПОЛНЫЙ словарь, вместе с сохранёнными токенами —
        # иначе валидный access_token и живой refresh_token остаются невидимы,
        # и авторизация всегда уходит в браузер.
        secrets = load_secrets_dict(secrets_path)
        _print(_t("diag_secrets_ok"))
        lesson_id, step_position = parse_stepik_step_url(step_url)
        _print(_t("diag_url_parsed", lesson_id=lesson_id, step=step_position))
        session = create_user_session(secrets, secrets_path)
        _print(_t("diag_token_ok"))
        step_id, lesson, step_data = get_step_data_by_position(session, lesson_id, step_position)
        _print(_t("diag_step_ok"))
    except Exception as error:
        # issue #831 (DEV-12): в лог — стек, пользователю — короткая строка.
        # Диагностика без места падения бесполезна ровно там, где нужна.
        _log.exception("сбой диагностики Stepik (secrets=%s, url=%s)", secrets_file, step_url)
        _print(_t("diag_failed", error=error))
        # issue #997 (OPS-1-03): стек лежит в логе, но пользователю о нём не
        # говорили — оставалось гадать, есть ли он и где. Путь печатается, только
        # когда файл действительно создан.
        log_path = output_dir / "grader.log"
        if log_path.is_file():
            _print(_t("diag_log_path", path=log_path.resolve()))
        return 1
    lesson_path = save_json(output_dir, "lesson_debug.json", lesson)
    step_path = save_json(output_dir, "step_debug.json", step_data)
    zip_url = extract_zip_url_from_step_data(step_data)
    result = build_diagnostic_result(
        lesson=lesson,
        step_id=step_id,
        step_data=step_data,
        zip_url=zip_url,
        lesson_path=lesson_path,
        step_path=step_path,
    )
    save_json(output_dir, "diagnostic_result.json", result)
    print_result_summary(result, output_dir)
    return 0


if __name__ == "__main__":
    # issue #997 (JRN-3A-03): исход диагностики виден процессу, а не только глазам.
    raise SystemExit(main())
