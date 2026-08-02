"""cli/options.py — построение argparse-парсера и разрешение CLI-опций.

Архитектурный слой: Application / CLI (leaf-модуль).

Выделено из cli.py без изменения поведения (issue #119, Stage 1 эпика
#117): safe-extraction кандидаты первого extraction-PR — не импортируют
stepik_grader.cli и не содержат mutable module-level state (_LANG,
_LOCALE_MESSAGES остаются в cli/__init__.py, issue #117).

Имена реэкспортированы фасадом ``stepik_grader.cli`` (``cli/__init__.py``)
как ``cli._build_arg_parser`` и т.д. для обратной совместимости с
существующими monkeypatch-тестами.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from stepik_grader.config import CONFIG

__all__ = [
    "_build_arg_parser",
    "_force_utf8_stdio",
    "_resolve_record_history",
    "_resolve_record_stats",
    "_resolve_use_cache",
    "_resolve_verbosity",
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
    parser.add_argument(
        "--file", type=pathlib.Path, help="Путь к файлу решения (обязателен для --mode 1)."
    )
    parser.add_argument(
        "--dir",
        type=pathlib.Path,
        help="Путь к папке с решениями (обязателен для --mode 2/3/4).",
    )
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
        "--diagnostic",
        action="store_true",
        help="Диагностический лог сети/OAuth/загрузки в stepik_diagnostics/grader.log "
        "(с редакцией секретов). То же — переменной STEPIK_GRADER_LOG=debug. Issue #146.",
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
            "(требует: pip install stepik-python-grader[watch]). Issue #54. Для --mode 2 "
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
        help="Удалить .grader_cache/ и .stepik_cache/ и выйти. Issues #56, #816.",
    )
    parser.add_argument(
        "--revoke-ai-consent",
        action="store_true",
        help=(
            "Отозвать согласие на отправку кода AI-провайдеру и выйти. "
            "При следующем --ai-hints согласие спросят заново. Issue #812."
        ),
    )
    parser.add_argument(
        "--purge-history",
        nargs="?",
        const="",
        metavar="TASK_KEY",
        help=(
            "Удалить локальную историю обучения (.grader_history.db) и журнал "
            "статистики (.grader_stats.jsonl) и выйти. С аргументом — только "
            "прогоны указанной задачи; статистика при этом не трогается. "
            "Issue #813."
        ),
    )
    parser.add_argument(
        "--stats",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Писать локальную статистику запусков (режим/вердикты/ОС) в "
            ".grader_stats.jsonl (--no-stats отключает). По умолчанию из "
            "[tool.stepik-grader] record_stats. Только локально, без сети. "
            "Issue #268."
        ),
    )
    parser.add_argument(
        "--stats-summary",
        action="store_true",
        help="Показать сводку локальной статистики запусков и выйти. Issue #268.",
    )
    parser.add_argument(
        "--history",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Писать историю прогонов (режимы/кейсы/вердикты) в локальную "
            "SQLite-базу .grader_history.db (--no-history отключает). По "
            "умолчанию из [tool.stepik-grader] record_history. Основа будущих "
            "разделов «Правила»/«Подучить», только локально. Issue #344."
        ),
    )
    parser.add_argument(
        "--insights",
        action="store_true",
        help=(
            "Показать сводку карточек «Подучить» (частые ошибки и их затухание) "
            "из накопленной истории прогонов и выйти. Требует включённую "
            "--history. Issue #349."
        ),
    )
    parser.add_argument(
        "--export-progress",
        choices=["md", "html"],
        default=None,
        metavar="{md,html}",
        help=(
            "Экспортировать агрегаты прогресса (попыток/времени до первого AC по "
            "задачам, тали вердиктов и типов падений — без исходников решений) из "
            ".grader_history.db в самодостаточный файл grader-progress.md/.html и "
            "выйти. Issue #432."
        ),
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help=(
            "После проверки режимов 1/2 показать блок «Стиль» — нарушения PEP 8 "
            "от ruff (требует extra: pip install stepik-python-grader[lint]). "
            "Не влияет на вердикт. Issue #349."
        ),
    )
    parser.add_argument(
        "--ai-hints",
        action="store_true",
        help=(
            "После проверки режимов 1–4 показать AI-объяснение упавших кейсов "
            "(WA/RE; в бенчмарк-режимах 3/4 — решений с ошибкой исполнения) через "
            "OpenAI-совместимый endpoint (BYOK, ADR-0003). По умолчанию выключено; "
            "без ai_base_url/ai_model в pyproject.toml — тихий пропуск с подсказкой. "
            "Ничего не уходит в сеть без настройки. Не влияет на вердикт. Issue #435/#542."
        ),
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
        type=pathlib.Path,
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
        "--sandbox",
        action="store_true",
        help=(
            "Исполнять решения --mode 1/2/3/4 в ОС-изолированной песочнице "
            "(SandboxRunner) вместо обычного subprocess — bubblewrap на "
            "Linux, sandbox-exec на macOS, Job Objects на Windows. Гарантии "
            "различаются по ОС (см. SECURITY.md); при недоступности backend'а "
            "на этой машине — явная ошибка, без тихого отката на обычный "
            "запуск. Issue #266."
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
    parser.add_argument(
        "--import-reference",
        type=pathlib.Path,
        metavar="TASK_DIR",
        help=(
            "Импортировать закреплённое решение Stepik (+топовые по лайкам) из "
            "ветки решений в папку задачи как task{N}_{100+}.py для сравнения в "
            "режимах 2–4 и выйти. Читает meta.json из TASK_DIR (нужна скачанная "
            "задача и OAuth). Issue #55."
        ),
    )
    parser.add_argument(
        "--import-top",
        type=int,
        default=5,
        metavar="N",
        help=(
            "Сколько топовых по лайкам решений импортировать сверх закреплённого "
            "(--import-reference). По умолчанию 5; нулёвые по лайкам не берутся. "
            "Issue #55."
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
        return bool(args.cache)
    if incremental:
        return True
    return CONFIG.use_cache


def _resolve_record_stats(args: argparse.Namespace) -> bool:
    """Разрешить --stats/--no-stats в конкретное bool-значение (issue #268).

    Приоритет: явный --stats/--no-stats (args.stats is not None) выигрывает;
    иначе — дефолт из pyproject ([tool.stepik-grader] record_stats). Нет
    аналога ``incremental`` из ``_resolve_use_cache`` — статистике не с чем
    "включаться автоматически" (в отличие от --watch --mode 2 и кэша).
    """
    if args.stats is not None:
        return bool(args.stats)
    return CONFIG.record_stats


def _resolve_record_history(args: argparse.Namespace) -> bool:
    """Разрешить --history/--no-history в конкретное bool-значение (issue #344).

    Приоритет: явный --history/--no-history (``args.history is not None``)
    выигрывает; иначе — дефолт из pyproject (``[tool.stepik-grader]
    record_history``). Симметрично ``_resolve_record_stats``.
    """
    if args.history is not None:
        return bool(args.history)
    return CONFIG.record_history


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
