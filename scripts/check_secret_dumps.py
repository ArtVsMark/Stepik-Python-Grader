#!/usr/bin/env python3
"""scripts/check_secret_dumps.py — реестр точек дампа (issue #1301, #982).

Предусловие безопасности перед локальным прогоном на настоящем токене звучит
так: **ни один инструмент не пишет секреты на диск**, и проверяется это не
обещанием, а перечислением всех точек, где содержимое сетевого обмена попадает
в файл.

Почему одного фикса мало. Находка ``OPS-1-02`` касалась ровно одного места —
``diagnostic_stepik.save_json()``, писавшего ответы API в
``stepik_diagnostics/*.json`` как есть. Само место починено (редакция через
``diag_log.redact``), но issue #982 констатирует общее: «при текущей архитектуре
любая новая точка дампа повторит ту же ошибку». Автор новой функции, которая
сохраняет ответ API, ничего не нарушает — он просто не знает, что обязан был
подумать о редакции. Эта проверка и есть то место, где он об этом узнаёт.

Как устроено. Обходятся модули пакета, которые работают с сетью или OAuth
(импортируют ``requests``/``stepik_client``/``oauth_flow``), и в них ищутся
записи файлов. Каждая найденная точка обязана быть в реестре
:data:`KNOWN_DUMPS` с причиной, по которой она безопасна; точка, помеченная
:data:`REDACTED`, обязана вдобавок вызывать ``redact`` в своём теле. Реестр
проверяется и в обратную сторону: запись, которой больше не соответствует ни
одна точка, удаляется — иначе реестр протухает и перестаёт что-либо утверждать.

Чего проверка **не** делает: не доказывает, что данные безопасны (это решение
человека, записанное причиной в реестре), и не ходит за пределы сетевых
модулей — файл, писать который некому из сети, точкой дампа не является.

Запуск::

    python scripts/check_secret_dumps.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import ast
import contextlib
import sys
from pathlib import Path

__all__ = [
    "KNOWN_DUMPS",
    "REDACTED",
    "DumpSite",
    "collect_dump_sites",
    "main",
]

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE = _ROOT / "src" / "stepik_grader"

# issue #1095: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

# Модуль считается сетевым, если импортирует что-то из этого списка: только
# такой модуль и может получить в руки токен или ответ API.
_NETWORK_IMPORTS = ("requests", "stepik_client", "oauth_flow")

# Вызовы, которые кладут данные в файл. `.write()` намеренно НЕ здесь: в этом
# же слое так пишут HTTP-ответ OAuth-колбэка (`self.wfile.write`) и stdout, то
# есть проверка утонула бы в совпадениях, не относящихся к диску.
_WRITERS = frozenset(
    {
        "write_text",
        "write_bytes",
        "atomic_write_json",
        "atomic_write_text",
        "save_secrets",
    }
)

# Причина-маркер: точка обязана редактировать секреты сама. Остальные значения
# реестра — обычный текст «почему здесь секрета быть не может».
REDACTED = "редакция через diag_log.redact"

# Реестр точек дампа: «модуль::функция» → почему секрет туда не попадёт.
# Ключ — функция, а не строка: номера строк плывут от любой правки соседа, и
# реестр по ним пришлось бы обновлять постоянно, ничего этим не проверяя.
KNOWN_DUMPS: dict[str, str] = {
    "cli/__init__.py::_run_stats_command": (
        "экспорт локальной статистики прогонов — данные из .grader_stats.jsonl, сети не касаются"
    ),
    "cli/__init__.py::main": "экспорт отчёта прогресса из локальной истории, без сетевых полей",
    "core/attachments.py::download_attachments": (
        "вложения условия задачи — байты файла, скачанного по публичной ссылке"
    ),
    "core/oauth_flow.py::authorize_and_get_token": (
        "save_secrets — это и есть хранилище токенов (атомарно, права 0600)"
    ),
    "core/oauth_flow.py::try_create_session_without_browser": (
        "save_secrets — обновлённый refresh_token возвращается в то же хранилище"
    ),
    "core/stepik_client.py::create_user_session": "save_secrets — хранилище токенов по назначению",
    "core/stepik_client.py::_cached_api_get": REDACTED,
    "core/stepik_reference.py::import_references_from_task_dir": (
        "код эталонов и отобранные поля комментария (id, лайки, признак закрепления)"
    ),
    "core/test_source_fetcher.py::download_github_tests": (
        "архив тестов с GitHub — байты ответа на анонимный запрос"
    ),
    "diagnostic_stepik.py::save_json": REDACTED,
    "downloader.py::save_task_files": (
        "файлы задачи и meta.json из отобранных полей шага/урока/курса, без тела ответа целиком"
    ),
    "downloader.py::_save_attachments": "имена приехавших вложений в meta.json",
    "web/auth_adapter.py::perform_browser_auth": (
        "save_secrets — тот же путь сохранения токенов, что и в CLI"
    ),
    "web/downloader_adapter.py::write_config": (
        "stepik_config.json — только пути к рабочей папке и к secrets.json"
    ),
}


class DumpSite:
    """Точка записи файла: где нашли, в какой функции и чем пишет."""

    __slots__ = ("function", "key", "lineno", "module", "writer")

    def __init__(self, module: str, function: str, writer: str, lineno: int) -> None:
        self.module = module
        self.function = function
        self.writer = writer
        self.lineno = lineno
        self.key = f"{module}::{function}"

    def __repr__(self) -> str:  # pragma: no cover — диагностика в отладке
        return f"DumpSite({self.key!r}, {self.writer!r}, line={self.lineno})"


def _module_imports(tree: ast.Module) -> set[str]:
    """Имена модулей, импортированных файлом (для проверки «сетевой ли он»)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _is_network_module(tree: ast.Module) -> bool:
    """Модуль работает с сетью или OAuth — значит может держать секрет в руках."""
    imports = _module_imports(tree)
    return any(marker in name for marker in _NETWORK_IMPORTS for name in imports)


def _enclosing_functions(tree: ast.Module) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Строка → внешняя функция, в которой она лежит.

    ``ast.walk`` идёт в ширину, поэтому внешняя функция встречается раньше
    вложенной, а ``setdefault`` оставляет именно её: реестр должен называть
    публичное имя, а не замыкание внутри него.
    """
    owners: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    owners.setdefault(child.lineno, node)
    return owners


def _called_names(node: ast.AST) -> set[str]:
    """Имена всех функций/методов, вызванных внутри узла."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def collect_dump_sites(package: Path | None = None) -> list[DumpSite]:
    """Собрать точки записи файлов во всех сетевых модулях пакета.

    ``package`` читается в момент ВЫЗОВА, а не вмораживается в дефолт: иначе
    тесты этой проверки не смогли бы подставить синтетический пакет.
    """
    package = _PACKAGE if package is None else package
    sites: list[DumpSite] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _is_network_module(tree):
            continue
        module = path.relative_to(package).as_posix()
        owners = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            writer = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if writer not in _WRITERS:
                continue
            owner = owners.get(node.lineno)
            name = owner.name if owner is not None else "<module>"
            sites.append(DumpSite(module, name, writer, node.lineno))
    return sites


def _redacting_functions(package: Path | None = None) -> set[str]:
    """Ключи функций, которые вызывают ``redact`` — то есть чинят дамп сами."""
    package = _PACKAGE if package is None else package
    redacting: set[str] = set()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = path.relative_to(package).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if "redact" in _called_names(node):
                redacting.add(f"{module}::{node.name}")
    return redacting


def main() -> int:
    """Проверить реестр точек дампа; ``0`` — порядок, ``1`` — нарушение."""
    sites = collect_dump_sites()
    redacting = _redacting_functions()
    problems: list[str] = []

    # issue #787: нулевой вход — это отказ проверки, а не «всё чисто». Пакет
    # переехал или разбор перестал узнавать записи — в обоих случаях зелёный
    # ответ был бы враньём, причём самым дорогим: молчаливым.
    if not sites:
        print(
            f"Ни одной точки записи не найдено в {_PACKAGE} — проверять нечего.\n"
            "Каталог пакета переехал или разбор перестал узнавать записи файлов."
        )
        return 1

    unknown = sorted({site.key for site in sites} - set(KNOWN_DUMPS))
    for key in unknown:
        where = ", ".join(
            f"{site.writer} (строка {site.lineno})" for site in sites if site.key == key
        )
        problems.append(
            f"новая точка записи вне реестра: {key} — {where}.\n"
            "    Данные сетевого модуля попадают в файл. Убедитесь, что секрет туда не\n"
            "    приедет (docs/dev/logging.md п. 4): либо пропустите содержимое через\n"
            "    diag_log.redact, либо сохраняйте отобранные поля, а не ответ целиком.\n"
            f"    Затем внесите точку в KNOWN_DUMPS ({Path(__file__).name}) с причиной."
        )

    for key, reason in sorted(KNOWN_DUMPS.items()):
        if reason == REDACTED and key not in redacting:
            problems.append(
                f"точка {key} помечена как редактирующая, но redact в ней не вызывается — "
                "либо редакция потерялась при правке, либо причина в реестре устарела."
            )

    dead = sorted(set(KNOWN_DUMPS) - {site.key for site in sites})
    for key in dead:
        problems.append(
            f"мёртвая запись реестра: {key} — такой точки записи больше нет. "
            "Удалите строку из KNOWN_DUMPS, иначе реестр перестаёт что-либо утверждать."
        )

    if problems:
        print("Реестр точек дампа разошёлся с кодом:\n")
        for problem in problems:
            print(f"  ✗ {problem}")
        print(f"\nВсего точек записи в сетевых модулях: {len({s.key for s in sites})}")
        return 1

    print(f"Точки дампа: {len({s.key for s in sites})} — все в реестре, редакция на месте.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
