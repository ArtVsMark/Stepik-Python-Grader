"""cli/options.py — построение argparse-парсера и разрешение CLI-опций.

Архитектурный слой: Application / CLI (leaf-модуль).

Выделено из cli.py без изменения поведения (issue #119, Stage 1 эпика
#117): safe-extraction кандидаты первого extraction-PR — не импортируют
stepik_grader.cli и не содержат mutable module-level state (_LANG,
_MESSAGES, _LOCALE_MESSAGES остаются в cli/__init__.py, issue #117).

Имена реэкспортированы фасадом ``stepik_grader.cli`` (``cli/__init__.py``)
как ``cli._build_arg_parser`` и т.д. для обратной совместимости с
существующими monkeypatch-тестами.
"""

from __future__ import annotations

import argparse
import sys

from stepik_grader.config import CONFIG

__all__ = [
    "_build_arg_parser",
    "_resolve_verbosity",
    "_resolve_use_cache",
    "_force_utf8_stdio",
]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grader.py",
        description="Stepik Python Grader — проверка и сравнение решений.",
    )
    parser.add_argument("--version", action="store_true", help="Показать версию грейдера и выйти.")
    parser.add_argument(
        "--mode",
        type=int,
        choices=[1, 2, 3, 4],
        help="Режим запуска (без --mode показывается интерактивное меню).",
    )
    parser.add_argument("--file", help="Путь к файлу решения (обязателен для --mode 1).")
    parser.add_argument("--dir", help="Путь к папке с решениями (обязателен для --mode 2/3/4).")
    parser.add_argument(
        "--repeats",
        type=int,
        default=15,
        help="Число повторов на тест-кейс для --mode 3 (по умолчанию 15).",
    )
    parser.add_argument(
        "--number",
        type=int,
        default=1000,
        help="Число вызовов timeit для --mode 4 (по умолчанию 1000).",
    )
    parser.add_argument(
        "--lang",
        choices=["ru", "en"],
        default="ru",
        help="Язык меню и сообщений (по умолчанию ru). Issue #51 D-01.",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help="Подробный вывод с diff для --mode 1/2 (для --mode 1 это уже поведение "
        "по умолчанию). Issue #50 D-03.",
    )
    verbosity.add_argument(
        "--quiet",
        action="store_true",
        help="Только итог, без подробного diff, для --mode 1/2. Issue #50 D-03.",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json", "csv", "markdown"],
        default="text",
        help=(
            "Формат вывода: text (по умолчанию), json/csv для CI-пайплайнов "
            "(issues #50 D-04, #53) или markdown для отчётов (issue #58)."
        ),
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "Перезапускать --mode 1/2 при изменении файла решения "
            "(требует: pip install stepik-grader[watch]). Issue #54. Для --mode 2 "
            "перезапуск инкрементальный — кэш прогоняет только изменённый файл "
            "(--no-cache отключает). Issue #71."
        ),
    )
    parser.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Кэшировать результаты --mode 1/2 в .grader_cache/ и пропускать "
            "неизменённые решения (--no-cache отключает). По умолчанию из "
            "[tool.stepik-grader] use_cache; под --watch --mode 2 включён "
            "автоматически (инкрементальный перезапуск). Issues #56, #71."
        ),
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Удалить .grader_cache/ и выйти. Issue #56.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help=(
            "Запустить локальный веб-интерфейс (только localhost) вместо CLI. "
            "Эпик #80 Tier 1 / issue #58."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Порт для --serve (по умолчанию 8000).",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help=(
            "Рабочая директория --serve: пути из запросов вне неё отклоняются "
            "403-м (по умолчанию — cwd на момент запуска). Issue #261."
        ),
    )
    parser.add_argument(
        "--no-root-confinement",
        action="store_true",
        help=(
            "Отключить проверку путей запросов относительно --root — доступ "
            "к любому пути на диске, как раньше. Явный откат пользователя, "
            "не дефолт. Issue #261."
        ),
    )
    parser.add_argument(
        "--init-vscode",
        action="store_true",
        help=(
            "Сгенерировать .vscode/tasks.json в текущей папке (грейдинг из VS Code "
            "по Ctrl+Shift+B). Эпик #80 Tier 2 / issue #58."
        ),
    )
    return parser


def _resolve_verbosity(args: argparse.Namespace, *, default: bool) -> bool:
    """Разрешить --verbose/--quiet в конкретное bool-значение для режима.

    --verbose/--quiet — общий флаг для режимов 1 и 2, у которых РАЗНЫЕ
    дефолты (1 — подробный вывод, 2 — только итог); default параметризует,
    какой из них используется, если явного флага не передали.
    """
    if args.verbose:
        return True
    if args.quiet:
        return False
    return default


def _resolve_use_cache(args: argparse.Namespace, *, incremental: bool) -> bool:
    """Разрешить --cache/--no-cache в конкретное bool-значение (issues #56, #71).

    Приоритет:
      1. Явный --cache/--no-cache (args.cache is not None) — всегда выигрывает.
      2. incremental=True (--watch --mode 2): кэш включён по умолчанию, чтобы
         перезапускать только изменённый файл (issue #71). Пользователь может
         отказаться через --no-cache.
      3. Иначе — дефолт из pyproject ([tool.stepik-grader] use_cache).
    """
    if args.cache is not None:
        return args.cache
    if incremental:
        return True
    return CONFIG.use_cache


def _force_utf8_stdio() -> None:
    """Принудительно переключить stdout/stderr на UTF-8.

    Git Bash / cmd на Windows по умолчанию используют cp1251 — rich-вывод
    (рамки таблиц, ✓/✗, кириллица) роняет процесс с UnicodeEncodeError.
    ``errors="replace"`` гарантирует отсутствие краша даже на терминалах,
    которые не могут отобразить конкретный символ. Убирает необходимость
    в ручном ``PYTHONIOENCODING=utf-8`` от пользователя (issue #64).

    No-op на потоках без ``reconfigure`` (например, перехваченных pytest
    или уже находящихся в UTF-8).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        enc = (getattr(stream, "encoding", "") or "").lower()
        if enc not in {"utf-8", "utf8"}:
            reconfigure(encoding="utf-8", errors="replace")
