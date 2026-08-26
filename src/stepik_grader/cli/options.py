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

from stepik_grader import config
from stepik_grader.config import COMPARE_MODES, CONFIG, CONFIG_FLAG
from stepik_grader.core import user_settings
from stepik_grader.core.i18n import load_locale_messages
from stepik_grader.stdio_encoding import force_utf8_stdio

# issue #997 (INS-5-03): язык нужен и парсеру, и предпроходу, который
# узнаёт --lang до сборки парсера, — держим одним местом.
DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "en")

__all__ = [
    "DEFAULT_LANG",
    "SUPPORTED_LANGS",
    "_build_arg_parser",
    "_force_utf8_stdio",
    "_program_name",
    "_resolve_record_history",
    "_resolve_record_stats",
    "_resolve_use_cache",
    "_resolve_verbosity",
    "apply_launch_profile",
    "flag_present",
    "peek_lang",
    "validate_output_format",
    "validate_serve_arguments",
]

# Эпилог справки: обе рабочие формы запуска. При src-layout файла grader.py в
# корне нет, поэтому usage обязан называть то, что пользователь может набрать.
#
# issue #997 (INS-5-06, JRN-1-04): справка отвечала «как запустить», но молчала
# о двух вещах, без которых запускать нечего, — откуда взять задачу и как
# устроен каталог с тестами. Первый вопрос новичка («у меня нет task.py») не
# имел ответа в самом инструменте, а раскладку `tests/` он узнавал, только
# провалившись: подсказка при отсутствии тестов зовёт в формат `tests/1`,
# который в документации значится легаси.


def _program_name() -> str:
    """Имя команды для usage-строки: реальная точка входа, а не файл модуля.

    ``argparse`` по умолчанию берёт ``basename(sys.argv[0])`` — при запуске
    ``python -m stepik_grader`` это ``__main__.py``, а при жёстко заданном
    ``prog`` годами печаталось ``grader.py``, которого при src-layout не
    существует. Через console script имя argv[0] и есть команда пользователя
    (``stepik-grader``); в остальных случаях единственная набираемая форма —
    ``python -m stepik_grader``.
    """
    stem = pathlib.Path(sys.argv[0] or "").stem
    return stem if stem.startswith("stepik-grader") else "python -m stepik_grader"


def _help_texts(lang: str) -> dict[str, str]:
    """Тексты справки на ``lang`` с откатом на русские (issue #997, INS-5-03).

    Справка собиралась из строк, зашитых в код, поэтому ``--lang en --help``
    печатал целиком русский текст: единственная поверхность, которую англоязычный
    пользователь видит ПЕРВОЙ, игнорировала его выбор языка. Откат на ``ru``
    оставлен намеренно — пропущенный ключ должен показать текст, а не KeyError
    посреди `--help`.
    """
    russian = load_locale_messages("ru")
    if lang == "ru":
        return russian
    return {**russian, **load_locale_messages(lang)}


def peek_lang(argv: list[str] | None = None) -> str:
    """Узнать ``--lang`` до сборки основного парсера (issue #997, INS-5-03).

    Язык нужен раньше, чем существует парсер, — иначе тексты справки уже
    зафиксированы. Отдельный лёгкий проход: неизвестное значение здесь молча
    даёт дефолт, а ругается на него основной парсер, у которого для этого есть
    ``choices`` и внятное сообщение.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", default=DEFAULT_LANG)
    try:
        known, _ = pre.parse_known_args(argv)
    except SystemExit:  # pragma: no cover - разбор без choices не падает
        return DEFAULT_LANG
    lang = str(known.lang)
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def _build_arg_parser(lang: str = DEFAULT_LANG) -> argparse.ArgumentParser:
    t = _help_texts(lang)
    parser = argparse.ArgumentParser(
        prog=_program_name(),
        description=t["cli_description"],
        epilog=t["cli_epilog"],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # issue #1192: подкоманда как ПОЗИЦИОННЫЙ аргумент, а не argparse-subparsers.
    # Subparser забрал бы себе всё после своего имени, и общие флаги
    # (`--lang`, `--config`, `--output`) пришлось бы объявлять дважды — либо
    # `stepik-grader stats --lang en` перестал бы работать. Позиционный
    # `choices` даёт то же имя команды, ничего не меняя для флагового вызова:
    # позиционных аргументов у грейдера не было и нет.
    parser.add_argument(
        "command",
        nargs="?",
        choices=["stats", "usage"],
        help=t["cli_help_command_stats"],
    )
    parser.add_argument("--version", action="store_true", help=t["cli_help_version"])
    # issue #1365: единственный новый флаг команды `usage`. Без него экспорт
    # идёт в стандартный вывод — перенаправить его умеет любая оболочка, а файл
    # нужен там, где команду зовут из планировщика.
    parser.add_argument(
        "--usage-out",
        type=pathlib.Path,
        metavar="FILE",
        help=t["cli_help_usage_out"],
    )
    # issue #1185: ярлык создаётся ТОЛЬКО явным действием — этим флагом или
    # кнопкой в лаунчере. Непрошеный ярлык на рабочем столе воспринимается как
    # навязчивость, поэтому при установке он не появляется никогда.
    parser.add_argument(
        "--create-shortcut",
        action="store_true",
        help=t["cli_help_create_shortcut"],
    )
    parser.add_argument(
        "--mode",
        type=int,
        choices=[1, 2, 3, 4],
        help=t["cli_help_mode"],
    )
    parser.add_argument("--file", type=pathlib.Path, help=t["cli_help_file"])
    parser.add_argument(
        "--dir",
        type=pathlib.Path,
        help=t["cli_help_dir"],
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=15,
        help=t["cli_help_repeats"],
    )
    parser.add_argument(
        "--number",
        type=int,
        default=1000,
        help=t["cli_help_number"],
    )
    parser.add_argument(
        "--lang",
        choices=list(SUPPORTED_LANGS),
        default=DEFAULT_LANG,
        help=t["cli_help_lang"],
    )
    parser.add_argument(
        CONFIG_FLAG,
        type=pathlib.Path,
        default=None,
        metavar="PATH",
        help=t["cli_help_config"],
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "--verbose",
        action="store_true",
        help=t["cli_help_verbose"],
    )
    verbosity.add_argument(
        "--quiet",
        action="store_true",
        help=t["cli_help_quiet"],
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help=t["cli_help_diagnostic"],
    )
    parser.add_argument(
        "--output",
        # issue #1192: `html` осмыслен только для `stats` — рендерить страницу
        # из вердиктов одного прогона нечем. Значение принимается парсером и
        # отклоняется явной проверкой (`_check_output_html`), а не молча
        # игнорируется: молчание здесь означало бы «отчёт сформирован», когда
        # его нет.
        choices=["text", "json", "csv", "markdown", "html"],
        default="text",
        help=t["cli_help_output"],
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=t["cli_help_watch"],
    )
    parser.add_argument(
        "--cache",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=t["cli_help_cache"],
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=t["cli_help_clear_cache"],
    )
    parser.add_argument(
        "--revoke-ai-consent",
        action="store_true",
        help=t["cli_help_revoke_ai_consent"],
    )
    parser.add_argument(
        "--purge-history",
        nargs="?",
        const="",
        metavar="TASK_KEY",
        help=t["cli_help_purge_history"],
    )
    parser.add_argument(
        "--stats",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=t["cli_help_stats"],
    )
    parser.add_argument(
        "--stats-summary",
        action="store_true",
        help=t["cli_help_stats_summary"],
    )
    parser.add_argument(
        "--history",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=t["cli_help_history"],
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SEC",
        help=t["cli_help_timeout"],
    )
    parser.add_argument(
        "--memory-limit",
        type=int,
        default=None,
        metavar="MB",
        help=t["cli_help_memory_limit"],
    )
    parser.add_argument(
        "--compare",
        choices=COMPARE_MODES,
        default=None,
        help=t["cli_help_compare"],
    )
    parser.add_argument(
        "--insights",
        action="store_true",
        help=t["cli_help_insights"],
    )
    parser.add_argument(
        "--export-progress",
        choices=["md", "html", "json"],
        default=None,
        metavar="{md,html,json}",
        help=t["cli_help_export_progress"],
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help=t["cli_help_lint"],
    )
    parser.add_argument(
        "--ai-hints",
        action="store_true",
        help=t["cli_help_ai_hints"],
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help=t["cli_help_serve"],
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help=t["cli_help_port"],
    )
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=None,
        help=t["cli_help_root"],
    )
    parser.add_argument(
        "--no-root-confinement",
        action="store_true",
        help=t["cli_help_no_root_confinement"],
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help=t["cli_help_sandbox"],
    )
    # issue #1133 (шаг 2): именованный профиль запуска — тот же набор, что
    # выбирается в окне лаунчера. Без флага в CLI профиль остался бы сущностью
    # одного окна, а граница эпика требует обратного.
    parser.add_argument(
        "--profile",
        metavar="NAME",
        default=None,
        help=t["cli_help_profile"],
    )
    parser.add_argument(
        "--init-vscode",
        action="store_true",
        help=t["cli_help_init_vscode"],
    )
    parser.add_argument(
        "--import-reference",
        type=pathlib.Path,
        metavar="TASK_DIR",
        help=t["cli_help_import_reference"],
    )
    parser.add_argument(
        "--import-top",
        type=int,
        default=5,
        metavar="N",
        help=t["cli_help_import_top"],
    )
    return parser


def flag_present(argv: list[str] | None, *names: str) -> bool:
    """Передан ли пользователем один из флагов ЯВНО (issue #1133).

    argparse не различает «не указан» и «указан со значением, равным дефолту»:
    у ``--port`` дефолт 8000, у ``--lang`` — ru. Для профиля разница
    принципиальна — иначе он либо всегда проигрывает дефолту, либо всегда
    выигрывает у явного флага. Смотрим сырой ``argv``: это точный ответ на
    вопрос «пользователь это писал?», в отличие от сравнения с дефолтом.
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    return any(arg == name or arg.startswith(f"{name}=") for arg in raw for name in names)


def _profile_message(args: argparse.Namespace, key: str, /, **params: object) -> str:
    """Строка каталога на языке, выбранном в этом запуске."""
    messages = load_locale_messages(getattr(args, "lang", DEFAULT_LANG)) or load_locale_messages(
        DEFAULT_LANG
    )
    template = messages.get(key, key)
    return template.format(**params) if params else template


def apply_launch_profile(
    args: argparse.Namespace,
    argv: list[str] | None,
    parser: argparse.ArgumentParser,
) -> None:
    """Применить именованный профиль к аргументам ``--serve`` (issue #1133).

    Профиль — база, явные флаги её перекрывают: приоритет остаётся прежним
    (``флаг → профиль → user-state → pyproject → дефолт``), и это единственный
    порядок, при котором «запустить профиль, но на другом порту» выражается без
    правки самого профиля.

    Неизвестное имя — ``parser.error`` со списком доступных, а не молчаливый
    запуск с дефолтами: пользователь просил конкретный набор, и тихая подмена
    дала бы, например, сервер без изоляции там, где её ждали.

    Raises:
        SystemExit: профиль не найден или указан без ``--serve``.
    """
    if args.profile is None:
        return
    if not args.serve:
        parser.error(_profile_message(args, "profile_requires_serve"))
    settings = user_settings.load_settings(
        user_settings.default_settings_path(config.workspace_root())
    )
    profile = settings.launch_profiles.get(args.profile.strip())
    if profile is None:
        known = ", ".join(sorted(settings.launch_profiles)) or "—"
        parser.error(_profile_message(args, "profile_unknown", name=args.profile, known=known))
        return  # недостижимо (parser.error поднимает SystemExit); для mypy
    if profile.sandbox and not flag_present(argv, "--sandbox"):
        args.sandbox = True
    if profile.record_history is not None and not flag_present(argv, "--history", "--no-history"):
        args.history = profile.record_history
    if profile.lang is not None and not flag_present(argv, "--lang"):
        args.lang = profile.lang
    if profile.port is not None and not flag_present(argv, "--port"):
        args.port = profile.port
    if profile.workdir is not None and not flag_present(argv, "--root"):
        args.root = profile.workdir


# issue #930 (LNCH-2-04): флаги, которые ветка `--serve` не выполняет. Она
# возвращается ДО диспетчера режимов, поэтому всё это молча не срабатывало:
# `--serve --mode 1 --file task.py` поднимал сервер и не проверял ничего, а
# `--stats` оставлял `.grader_stats.jsonl` пустым — узнать об этом можно было
# только по пустому файлу.
_SERVE_INCOMPATIBLE: tuple[tuple[str, str], ...] = (
    ("mode", "--mode"),
    ("file", "--file"),
    ("dir", "--dir"),
    ("watch", "--watch"),
    ("stats", "--stats"),
    ("lint", "--lint"),
    ("ai_hints", "--ai-hints"),
)


def validate_serve_arguments(
    args: argparse.Namespace,
    argv: list[str] | None,
    parser: argparse.ArgumentParser,
) -> None:
    """Проверить аргументы ветки ``--serve`` до запуска сервера (issue #930).

    Две проверки, которых у ветки не было вовсе:

    * **порт в диапазоне.** ``type=int`` без границ пропускал ``--port 70000``,
      и пользователь получал ``OverflowError`` из недр ``socket`` — при том что
      GUI-лаунчер тот же ввод отвергает по-человечески;
    * **несовместимые флаги.** Явно переданное и молча не выполненное — хуже
      отказа: команда выглядит сработавшей.

    ``--output`` в список не входит намеренно: у него есть дефолт, и отличить
    «указал text» от «не указывал» можно только по argv — что и делается.

    Raises:
        SystemExit: порт вне диапазона или указан несовместимый флаг.
    """
    if not args.serve:
        return
    port = int(args.port)
    if not 1 <= port <= 65535:
        parser.error(_profile_message(args, "serve_port_out_of_range", port=port))
    conflicting = [
        flag
        for name, flag in _SERVE_INCOMPATIBLE
        if _is_set(args, name) or flag_present(argv, flag)
    ]
    if flag_present(argv, "--output"):
        conflicting.append("--output")
    if conflicting:
        parser.error(
            _profile_message(args, "serve_incompatible_flags", flags=", ".join(conflicting))
        )


def validate_output_format(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    """``--output html`` осмыслен только у команды ``stats`` (issue #1192).

    Отчёт строится из истории прогонов, а не из вердиктов одного запуска, —
    рендерить страницу режимам 1–4 попросту не из чего. Отказ явный, с
    подсказкой правильной команды: молчаливое игнорирование выглядело бы как
    «отчёт сформирован», когда его нет.

    Raises:
        SystemExit: ``--output html`` без команды ``stats``.
    """
    if args.output == "html" and args.command != "stats":
        parser.error(_help_texts(args.lang)["stats_html_needs_command"])


def _is_set(args: argparse.Namespace, name: str) -> bool:
    """Задан ли флаг: ``None``/``False`` считаются «не задан»."""
    value = getattr(args, name, None)
    return value not in (None, False)


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


def _resolve_record_history(args: argparse.Namespace, *, default: bool | None = None) -> bool:
    """Разрешить --history/--no-history в конкретное bool-значение (issue #344).

    Единая лестница приоритета (issue #997: LNCH-3-02, FZZ-5-04, LNCH-2-03,
    LNCH-3-01, LNCH-5-01, SET-2-03):

    1. явный ``--history``/``--no-history`` — разовое решение этого запуска;
    2. персистентный тумблер из ``.grader_settings.json`` — то, что пользователь
       переключил в меню (пункт 7) или в вебе, и ждёт, что это запомнено;
    3. ``[tool.stepik-grader] record_history`` из ``pyproject.toml``;
    4. ``default`` — дефолт поверхности (``--serve`` включает историю, ADR-0002
       оставляет CLI opt-in).

    Прежде лестниц было три, и они не сходились: режимы 1–4 читали только флаг и
    ``CONFIG``, ``--serve`` — только флаг, а тумблер в ``.grader_settings.json``
    не читал никто. Пользователь выключал историю в меню, запускал `--serve` и
    получал запись, которую явно отключил, — четыре независимых среза аудита
    пришли к одному и тому же месту.

    Ограничение, которое честнее назвать: ``record_history = false`` в
    ``pyproject.toml`` неотличимо от «не задано» (``bool`` с дефолтом ``False``),
    поэтому выключить им историю в ``--serve`` нельзя — для этого есть
    ``--no-history`` и тумблер меню.
    """
    if args.history is not None:
        return bool(args.history)
    toggle = _persistent_history_toggle()
    if toggle is not None:
        return toggle
    if CONFIG.record_history:
        return True
    return CONFIG.record_history if default is None else default


def _persistent_history_toggle() -> bool | None:
    """Сохранённый выбор пользователя из ``.grader_settings.json``; ``None`` — не задан.

    Best-effort, как и всё чтение настроек: битый или недоступный файл — это
    «пользователь не переопределял», а не повод отказать в запуске.
    """
    settings = user_settings.load_settings(
        user_settings.default_settings_path(config.workspace_root())
    )
    return settings.record_history


def _force_utf8_stdio() -> None:
    """Принудительно переключить stdout/stderr на UTF-8.

    Git Bash / cmd на Windows по умолчанию используют cp1251 — rich-вывод
    (рамки таблиц, ✓/✗, кириллица) роняет процесс с UnicodeEncodeError
    (issue #64).

    Реализация переехала в leaf-модуль ``stepik_grader.stdio_encoding``
    (issue #1108): приём понадобился всем точкам входа и скриптам стенда, а
    жил здесь — в модуле, который тянет за собой половину CLI. Имя оставлено
    в ``__all__`` как есть: на него ссылаются тесты и соседние скрипты.
    """
    force_utf8_stdio()
