"""config.py — единая конфигурация грейдера.

Архитектурный слой: Application / Configuration.
Заменяет разбросанные по grader_core.py хардкод-константы
единой точкой правды. Значения переопределяются через секцию
[tool.stepik-grader] в pyproject.toml; при отсутствии файла или секции
используются дефолты из GraderConfig.

Ленивая загрузка (issue #141/#142): импорт этого модуля сам по себе не читает
pyproject.toml — чтение (и кэширование) происходит при первом обращении к
``CONFIG`` (module-level ``__getattr__``, PEP 562) или явном вызове
``get_config()``. Существующий код (``from stepik_grader.config import
CONFIG``) продолжает работать без изменений: `from module import name`
проходит через тот же ``__getattr__``, разница лишь в том, что голый
``import stepik_grader.config`` (без обращения к ``.CONFIG``) больше не
трогает диск.

Откуда берётся конфиг (issue #258, уточнено в #993 — детерминизм окружения):

1. явный путь — CLI-флаг ``--config`` или ``set_config_path()``;
2. переменная окружения ``STEPIK_GRADER_CONFIG``;
3. поиск ``pyproject.toml`` от корня настроек (``workspace_root()``) вверх —
   **до границы проекта** (``.git``/``.hg``/``.grader_settings.json`` или
   домашний каталог) и **только файлы с секцией** ``[tool.stepik-grader]``;
4. legacy-путь относительно расположения пакета (src-layout).

Оба ограничителя третьего пункта — про воспроизводимость вердикта: без них
``pyproject.toml`` чужого проекта этажом выше молча менял параметры прогона, а
синтаксическая ошибка в нём роняла трейсбеком любую команду, включая
``--help``. Применённый файл доступен как ``config_source()`` — часть паспорта
условий прогона (issue #984).
"""

from __future__ import annotations

import codecs
import dataclasses
import os
import pathlib
import sys
import tomllib
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [  # noqa: F822 (CONFIG — module __getattr__, PEP 562)
    "CONFIG",
    "CONFIG_FLAG",
    "GraderConfig",
    "config_source",
    "get_config",
    "load_config",
    "reset_config_cache",
    "set_config_path",
    "set_workspace_root",
    "validate_values",
    "workspace_root",
]

_ENV_CONFIG_PATH = "STEPIK_GRADER_CONFIG"
_CONFIG_SECTION = "stepik-grader"
#: CLI-флаг явного пути к конфигу (issue #993). Объявлен здесь, а не в
#: ``cli/options.py``: его разбирает и сам ``config`` (см. ``_path_from_argv``).
CONFIG_FLAG = "--config"
#: Маркеры границы проекта (issue #993): каталог с любым из них — потолок
#: поиска конфига и кандидат в корень настроек. Выше границы лежат чужие
#: проекты и домашний каталог, чей ``pyproject.toml`` к прогону отношения не
#: имеет — прежде подъём шёл до корня ФС и подхватывал первый попавшийся файл.
_BOUNDARY_MARKERS = (".git", ".hg", ".grader_settings.json")


@dataclass(frozen=True)
class GraderConfig:
    """Единая конфигурация грейдера. frozen=True — потокобезопасно."""

    timeout_seconds: float = 10.0
    similar_threshold: float = 1.15
    much_slower_threshold: float = 1.50
    measure_child_memory: bool = True
    microbench_max_cases: int = 5
    encoding: str = "utf-8"
    max_memory_mb: int | None = 1024
    use_cache: bool = False  # issue #56 — opt-in кэш результатов (--cache)
    # issue #125 — локальная база карточек глоссария (None → компактный
    # core/glossary.py как fallback) и очередь пополнения (MissingConceptDetector).
    # issue #552: очередь — SQLite/WAL (``.db``); legacy ``.json``-сосед разово
    # импортируется при первой записи (обратная совместимость).
    glossary_store: str | None = None
    glossary_missing_queue: str = ".grader_glossary_missing.db"
    # issue #262 — размер пула воркеров async job-модели (POST /api/v1/runs,
    # web/runs.py). Не CLI-флаг: одноразовая настройка сервера через
    # pyproject.toml, а не параметр запроса. Дефолт 2 — достаточно, чтобы
    # два параллельных job'а не сериализовались в один поток, не вводя
    # неограниченного параллелизма subprocess-запусков на локальной машине.
    job_workers: int = 2
    # issue #429 — back-pressure для POST /api/v1/runs (web/runs.py): максимум
    # одновременных нетерминальных job'ов (queued/running) в реестре. Превышение
    # → 429 too_many_runs, а не безотказный рост _JOBS/очереди executor'а
    # (TTL чистит только терминальные job'ы). Дефолт 20 — с запасом для
    # локального UI, но отсекает забагованный фронтенд-цикл POST'ов; лимит
    # обязателен и как фундамент server mode (#151). Настройка сервера через
    # pyproject.toml, не CLI-флаг и не параметр запроса (как job_workers).
    max_active_runs: int = 20
    # issue #268 — opt-in локальная статистика запусков (.grader_stats.jsonl,
    # core/stats.py): режимы/вердикты/ОС, без сети. По умолчанию выключена —
    # включается --stats/--no-stats (приоритет) или этим полем.
    record_stats: bool = False
    # issue #344 — opt-in SQLite-история прогонов (.grader_history.db,
    # core/history.py): фундамент разделов «Правила»/«Подучить» (эпик #342).
    # По умолчанию выключена (#134) — включается --history/--no-history или полем.
    record_history: bool = False
    # issue #347 — пороги затухания карточек «Подучить» (core/insights.py):
    # окно последних N прогонов, порог активности T, чистая серия K до архива.
    # По номерам прогонов, не по календарю (тестируемость без freezegun).
    insights_window_n: int = 10
    insights_active_threshold_t: int = 2
    insights_clean_streak_k: int = 3
    # issue #266 — квоты SandboxRunner (--sandbox, core/sandbox/).
    # sandbox_max_cpu_seconds — жёсткий лимит CPU-времени (backstop ПОД
    # общим wall-clock timeout_seconds, не вместо него).
    # sandbox_max_processes — на Linux под bwrap (свежий user namespace,
    # счётчик начинается с 0) используется как абсолютное значение; на
    # голом POSIX/macOS (нет такого namespace) — как бюджет,
    # прибавляемый к текущему числу процессов пользователя на момент
    # запуска (иначе чужие процессы того же пользователя могли бы случайно
    # выбить лимит).
    sandbox_max_cpu_seconds: float = 10.0
    sandbox_max_processes: int = 32
    sandbox_max_output_bytes: int = 10 * 1024 * 1024
    # issue #629 — потолок вывода для ДЕФОЛТНОГО пути (LocalRunner, без
    # --sandbox). Раньше лимит был только у SandboxRunner, а обычный прогон
    # копил stdout/stderr в память без границы: решение с бесконечным print
    # набивало RAM хоста за секунды таймаута. Ограничивается НАКОПЛЕНИЕ (вывод
    # сверх лимита отбрасывается, чтение продолжается), а не время жизни
    # процесса — он доживает до собственного timeout_seconds.
    max_output_bytes: int = 10 * 1024 * 1024
    # issue #435 / ADR-0003 — opt-in AI-подсказки при WA/RE (--ai-hints). BYOK,
    # OpenAI-compatible `{ai_base_url}/chat/completions` на requests (без SDK и
    # новых зависимостей). Дефолт выключено: ai_base_url=None → graceful skip.
    # ai_api_key_env — ИМЯ env-переменной с ключом; значение ключа НИКОГДА не в
    # конфиге/файлах, читается из окружения в момент вызова и регистрируется в
    # diag_log.register_secret. Локальный провайдер (ollama,
    # http://localhost:11434/v1) ключа не требует.
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_api_key_env: str = "STEPIK_GRADER_AI_KEY"
    ai_max_tokens: int = 400
    ai_timeout_seconds: float = 20.0
    # issue #812 (TREND-02): потолок AI-вызовов за один прогон. Без него папка с
    # 40 упавшими кейсами давала 40 последовательных POST по 20 с таймаута —
    # 13 минут ожидания и столько же оплаченных запросов, причём пользователь
    # об этом не предупреждён. Первые N подсказок несут почти всю пользу:
    # ошибки в одном решении обычно однотипны.
    ai_max_hints: int = 5
    # issue #812 (VIS-03): сколько карточек глоссария кладётся в промпт как
    # заземление. Зависит от модели и лимита токенов, поэтому настройка, а не
    # константа модуля.
    ai_grounding_k: int = 3
    # issue #812 (VIS-02): свой системный промпт вместо встроенного. Встроенный
    # обращается к «новичку на курсе „Поколение Python“» — верно для основной
    # аудитории, но грейдер применим к любому курсу и уровню, а переопределить
    # формулировку было нечем. Пусто → встроенный (поведение по умолчанию то же).
    ai_system_prompt: str = ""
    # issue #818: где лежит база истории обучения. Пусто — авторезолв: сначала
    # существующая `.grader_history.db` рядом или выше по дереву (чтобы уже
    # накопленная история не осиротела), иначе единая `~/.stepik-grader/
    # history.db`. Прежнее поведение «база в текущей папке» возвращается
    # значением ".grader_history.db" — относительный путь резолвится от cwd.
    history_db_path: str = ""

    def __post_init__(self) -> None:
        """Проверить типы и диапазоны полей (issue #795).

        ``dataclass`` аннотации в рантайме не проверяет, поэтому
        ``GraderConfig(timeout_seconds="abc")`` спокойно конструировался и падал
        много позже — ``TypeError`` внутри ``proc.communicate(timeout="abc")``,
        то есть в чужом модуле и без единого упоминания конфигурации.
        Пользовательский путь (``pyproject.toml``) до исключения не доходит:
        ``load_config()`` отбраковывает значение раньше и откатывается на
        дефолт с предупреждением.
        """
        problems = validate_values(dataclasses.asdict(self))
        if problems:
            raise ValueError("GraderConfig: " + "; ".join(problems))


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_number(value: object, *, minimum: float, inclusive: bool = False) -> bool:
    """Число (int/float, но не bool) не меньше/строго больше ``minimum``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return value >= minimum if inclusive else value > minimum


def _is_int_at_least(value: object, minimum: int) -> bool:
    """Целое не меньше ``minimum``. ``bool`` отвергается намеренно: ``True`` —
    подкласс ``int``, и ``job_workers = true`` иначе прошло бы как ``1``."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_optional_str(value: object) -> bool:
    return value is None or _is_nonempty_str(value)


def _is_known_encoding(value: object) -> bool:
    """Имя кодека, известного stdlib: неизвестное падало бы при первом чтении."""
    if not _is_nonempty_str(value):
        return False
    try:
        codecs.lookup(str(value))
    except LookupError:
        return False
    return True


@dataclass(frozen=True)
class _Rule:
    """Правило проверки одного поля: предикат + описание допустимого значения."""

    check: Callable[[object], bool]
    expected: str


_POSITIVE_NUMBER = _Rule(lambda v: _is_number(v, minimum=0), "число больше 0")
_POSITIVE_INT = _Rule(lambda v: _is_int_at_least(v, 1), "целое число не меньше 1")
_FLAG = _Rule(_is_bool, "true или false")
_OPTIONAL_STR = _Rule(_is_optional_str, "непустая строка или отсутствие ключа")

# Правило на КАЖДОЕ поле GraderConfig: новое поле без правила ловится тестом
# (tests/test_config.py), иначе валидация тихо разошлась бы с dataclass.
_RULES: dict[str, _Rule] = {
    "timeout_seconds": _POSITIVE_NUMBER,
    "similar_threshold": _POSITIVE_NUMBER,
    "much_slower_threshold": _POSITIVE_NUMBER,
    "measure_child_memory": _FLAG,
    "microbench_max_cases": _POSITIVE_INT,
    "encoding": _Rule(_is_known_encoding, "имя кодировки, известной Python (например utf-8)"),
    "max_memory_mb": _Rule(
        lambda v: v is None or _is_int_at_least(v, 1),
        "целое число мегабайт не меньше 1 или отсутствие ключа (без лимита)",
    ),
    "use_cache": _FLAG,
    "glossary_store": _OPTIONAL_STR,
    "glossary_missing_queue": _Rule(_is_nonempty_str, "непустая строка — путь к файлу очереди"),
    "job_workers": _POSITIVE_INT,
    "max_active_runs": _POSITIVE_INT,
    "record_stats": _FLAG,
    "record_history": _FLAG,
    "insights_window_n": _POSITIVE_INT,
    "insights_active_threshold_t": _POSITIVE_INT,
    "insights_clean_streak_k": _POSITIVE_INT,
    "sandbox_max_cpu_seconds": _POSITIVE_NUMBER,
    "sandbox_max_processes": _POSITIVE_INT,
    "sandbox_max_output_bytes": _POSITIVE_INT,
    "max_output_bytes": _POSITIVE_INT,
    "ai_base_url": _OPTIONAL_STR,
    "ai_model": _OPTIONAL_STR,
    "ai_api_key_env": _Rule(_is_nonempty_str, "непустая строка — ИМЯ переменной окружения"),
    "ai_max_tokens": _POSITIVE_INT,
    "ai_timeout_seconds": _POSITIVE_NUMBER,
    "ai_max_hints": _POSITIVE_INT,
    "ai_grounding_k": _POSITIVE_INT,
    # Пустая строка здесь ЗНАЧИМА (= встроенный промпт), поэтому не _OPTIONAL_STR:
    # отбраковывать нужно только не-строку.
    "ai_system_prompt": _Rule(
        lambda v: isinstance(v, str),
        "строка (пусто — встроенный системный промпт)",
    ),
    # Пустая строка значима и здесь (= авторезолв пути базы истории).
    "history_db_path": _Rule(
        lambda v: isinstance(v, str),
        "строка-путь (пусто — авторезолв: база рядом или ~/.stepik-grader/history.db)",
    ),
}


def _describe(field: str, value: object) -> str:
    """Строка «поле: ожидается X, получено Y (тип)» — одна проблема (issue #795)."""
    return (
        f"{field}: ожидается {_RULES[field].expected}, получено {value!r} ({type(value).__name__})"
    )


def validate_values(values: Mapping[str, object]) -> list[str]:
    """Проверить значения полей конфигурации; вернуть список описаний проблем.

    Неизвестные имена не проверяются — их обрабатывает ``load_config()``
    (в конструктор они всё равно не попадают). Пустой список означает, что
    значения пригодны к использованию.
    """
    return [
        _describe(name, value)
        for name, value in values.items()
        if name in _RULES and not _RULES[name].check(value)
    ]


def _home() -> pathlib.Path | None:
    """Домашний каталог пользователя или ``None``, если его нет (CI, контейнер)."""
    try:
        return pathlib.Path.home()
    except (OSError, RuntimeError):
        return None


def _is_boundary(directory: pathlib.Path) -> bool:
    """Каталог — граница проекта: содержит ``.git``/``.hg``/файл настроек."""
    return any((directory / marker).exists() for marker in _BOUNDARY_MARKERS)


def _dirs_up_to_boundary(start: pathlib.Path) -> Iterator[pathlib.Path]:
    """Каталоги от ``start`` вверх, до границы проекта включительно.

    Границей служит маркер из ``_BOUNDARY_MARKERS`` или домашний каталог
    пользователя. Выше не поднимаемся: содержимое чужих папок не должно
    влиять на вердикт (issue #993).
    """
    home = _home()
    for directory in (start, *start.parents):
        yield directory
        if _is_boundary(directory) or directory == home:
            return


def _toml_table(path: pathlib.Path, *, warn: bool = True) -> dict[str, Any] | None:
    """Разобрать TOML-файл; битый или нечитаемый — предупреждение и ``None``.

    issue #993 (INS-1-01/INS-4-01): прежде ``tomllib.load`` вызывался голым, и
    синтаксическая ошибка в ЧУЖОМ ``pyproject.toml`` этажом выше роняла
    трейсбеком любую команду, включая ``--help`` и ``--version``. Диагностика
    идёт тем же ``UserWarning``-каналом, что и неизвестные ключи секции.

    ``warn=False`` — для служебных чтений (поиск корня настроек), чтобы один
    битый файл не породил два одинаковых предупреждения за резолв.
    """
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        problem = f"файл не разобран как TOML ({e})"
    except OSError as e:
        problem = f"файл недоступен для чтения ({e})"
    if warn:
        warnings.warn(
            f"{path}: {problem}; конфигурация из него не читается.",
            UserWarning,
            stacklevel=2,
        )
    return None


def _grader_section(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Секция ``[tool.stepik-grader]`` или ``None``, если её нет.

    Отсутствие секции — признак чужого ``pyproject.toml``: такой файл при
    поиске вверх по дереву пропускается, а не выигрывает (issue #993).
    """
    tool = data.get("tool")
    if not isinstance(tool, Mapping):
        return None
    overrides = tool.get(_CONFIG_SECTION)
    return overrides if isinstance(overrides, Mapping) else None


def _path_from_argv(argv: Sequence[str] | None = None) -> pathlib.Path | None:
    """Путь из ``--config PATH`` / ``--config=PATH`` в аргументах командной строки.

    Читается напрямую из ``sys.argv``, а не из результата ``argparse``:
    ``CONFIG`` связывается на импорте в двух десятках модулей, и к моменту
    разбора аргументов конфиг уже прочитан. Флаг всё равно объявлен в парсере —
    ради справки и проверки существования файла.

    Чужой ``--config`` не подхватывается: сканирование включается, только если
    процесс запущен как сам грейдер (``stepik-grader`` или
    ``python -m stepik_grader``), а не как чей-то хост-процесс с нашим пакетом
    в зависимостях.
    """
    if argv is None:
        if not _argv_is_grader():
            return None
        argv = sys.argv[1:]
    args = list(argv)
    for i, arg in enumerate(args):
        if arg == CONFIG_FLAG and i + 1 < len(args):
            return pathlib.Path(args[i + 1]).expanduser()
        if arg.startswith(f"{CONFIG_FLAG}="):
            return pathlib.Path(arg.split("=", 1)[1]).expanduser()
    return None


def _argv_is_grader() -> bool:
    """Процесс запущен как CLI грейдера (console script или ``python -m``)."""
    argv0 = pathlib.Path(sys.argv[0] or "")
    if argv0.stem.startswith("stepik-grader"):
        return True
    return argv0.name == "__main__.py" and argv0.parent.name == "stepik_grader"


_explicit_config_path: pathlib.Path | None = None
_explicit_workspace_root: pathlib.Path | None = None
_config_source: pathlib.Path | None = None


def set_config_path(path: pathlib.Path | None) -> None:
    """Задать явный путь к конфигу (``--config``, тесты) и сбросить кэш.

    Высший приоритет источника — выше ``STEPIK_GRADER_CONFIG`` и поиска по
    дереву. ``None`` снимает переопределение.
    """
    global _explicit_config_path
    _explicit_config_path = path.expanduser() if path is not None else None
    reset_config_cache()


def set_workspace_root(root: pathlib.Path | None) -> None:
    """Задать явный корень настроек (``--serve --root``) и сбросить кэш.

    Один корень для CLI и веба (issue #984): от него резолвятся и
    ``pyproject.toml``, и ``.grader_settings.json``. Прежде конфиг якорился на
    ``cwd``, а веб-настройки — на ``--root``, то есть один запуск читал
    настройки из двух разных мест.
    """
    global _explicit_workspace_root
    _explicit_workspace_root = root.expanduser().resolve() if root is not None else None
    reset_config_cache()


def workspace_root(start: pathlib.Path | None = None) -> pathlib.Path:
    """Корень настроек: каталог, от которого резолвятся конфиг и настройки.

    Порядок: явно заданный (``set_workspace_root``) → ближайший вверх от
    ``start`` каталог с файлом настроек, ``pyproject.toml`` с секцией
    ``[tool.stepik-grader]`` или ``.git`` → сам ``start`` (по умолчанию cwd).
    """
    if _explicit_workspace_root is not None:
        return _explicit_workspace_root
    base = (start or pathlib.Path.cwd()).resolve()
    for directory in _dirs_up_to_boundary(base):
        if (directory / ".grader_settings.json").is_file():
            return directory
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            # Тихо: о битом файле предупредит сам резолв конфига ниже.
            data = _toml_table(pyproject, warn=False)
            if data is not None and _grader_section(data) is not None:
                return directory
        if _is_boundary(directory):
            return directory
    return base


def _find_pyproject(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """Ищет pyproject.toml с секцией грейдера от ``start`` вверх до границы проекта.

    От «первого попавшегося файла» (паттерн pip/ruff/mypy) поиск отличается
    двумя ограничителями (issue #993): подъём останавливается на границе
    проекта (``.git``, файл настроек, домашний каталог), а файл без секции
    ``[tool.stepik-grader]`` пропускается как чужой. Прежде обоих не было —
    ``pyproject.toml`` соседнего проекта этажом выше молча переворачивал
    вердикт (``AC 2/2`` → ``FAIL 0/2``).
    """
    found = _find_config_source(start)
    return None if found is None else found[0]


def _find_config_source(
    start: pathlib.Path | None = None,
) -> tuple[pathlib.Path, Mapping[str, Any]] | None:
    """Пара «путь конфига, секция грейдера» из поиска по дереву; иначе ``None``."""
    base = (start or pathlib.Path.cwd()).resolve()
    for directory in _dirs_up_to_boundary(base):
        candidate = directory / "pyproject.toml"
        if not candidate.is_file():
            continue
        data = _toml_table(candidate)
        if data is None:
            continue
        overrides = _grader_section(data)
        if overrides is not None:
            return candidate, overrides
    return None


def _resolve_config_source() -> tuple[pathlib.Path, Mapping[str, Any]] | None:
    """Определяет применяемый конфиг: путь и секция ``[tool.stepik-grader]``.

    Порядок разрешения (issue #258, уточнён в #993): явный путь
    (``--config`` / ``set_config_path``) → ``STEPIK_GRADER_CONFIG`` → поиск от
    корня настроек вверх до границы проекта → legacy-путь относительно
    расположения пакета (src/-layout, issue #35, сохраняет поведение при
    запуске тестов из корня репозитория).

    Явный источник (флаг или переменная окружения) ищется по дереву не дальше:
    если файл указан и не читается, конфиг остаётся дефолтным — подставлять
    вместо него найденный где-то ещё значило бы прогон не тем конфигом, что
    просил пользователь.
    """
    explicit = _explicit_config_path or _path_from_argv()
    if explicit is None:
        env_value = os.environ.get(_ENV_CONFIG_PATH)
        explicit = pathlib.Path(env_value).expanduser() if env_value else None
    if explicit is not None and explicit.is_file():
        data = _toml_table(explicit)
        if data is None:
            return None
        return explicit, _grader_section(data) or {}

    found = _find_config_source(workspace_root())
    if found is not None:
        return found

    legacy = pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
    if legacy.is_file():
        data = _toml_table(legacy)
        overrides = None if data is None else _grader_section(data)
        if overrides is not None:
            return legacy, overrides

    return None


def config_source() -> pathlib.Path | None:
    """Файл, из которого взята действующая конфигурация; ``None`` — дефолты.

    Часть «паспорта условий прогона» (issue #984): пользователь должен видеть,
    какой именно ``pyproject.toml`` повлиял на вердикт. Гарантирует, что
    конфиг уже разрешён — при необходимости резолвит его.
    """
    get_config()
    return _config_source


def load_config() -> GraderConfig:
    """Загружает конфиг из [tool.stepik-grader] в pyproject.toml.

    Источник резолвится через ``_resolve_config_source()`` (явный ``--config``
    → env → поиск от корня настроек вверх до границы проекта → legacy
    fallback). Если файл не найден или секция отсутствует — возвращает
    GraderConfig с дефолтными значениями. Всегда перечитывает файл заново
    (без кэша) — кэширование для типичного пути потребления делает
    ``get_config()``/``CONFIG``, эта функция остаётся простым loader'ом.

    Ни битый TOML, ни опечатка в секции не роняют грейдер (issue #795, #993):
    нечитаемый файл, неизвестное имя ключа и значение, не прошедшее проверку,
    отбрасываются с ``UserWarning``, который называет файл, ключ, допустимое
    значение и подставленный дефолт. Прежде негодное значение доезжало до
    раннера и падало трейсбеком из чужого модуля, а синтаксическая ошибка в
    чужом ``pyproject.toml`` выше по дереву — до первого теста.
    """
    global _config_source
    resolved = _resolve_config_source()
    if resolved is None:
        _config_source = None
        return GraderConfig()
    pyproject, overrides = resolved
    _config_source = pyproject
    # issue #143: dataclasses.fields() — публичный API вместо приватного
    # dunder-атрибута dataclass (то же множество имён полей, поведение не меняется).
    valid_names = {f.name for f in dataclasses.fields(GraderConfig)}
    unknown = sorted(k for k in overrides if k not in valid_names)
    if unknown:
        warnings.warn(
            f"{pyproject}: [tool.stepik-grader] — неизвестные ключи "
            f"({', '.join(unknown)}) проигнорированы; проверьте написание.",
            UserWarning,
            stacklevel=2,
        )
    valid = {k: v for k, v in overrides.items() if k in valid_names}
    for problem in validate_values(valid):
        field = problem.split(":", 1)[0]
        del valid[field]
        warnings.warn(
            f"{pyproject}: [tool.stepik-grader].{problem}. "
            f"Значение отброшено, используется значение по умолчанию "
            f"({getattr(GraderConfig(), field)!r}).",
            UserWarning,
            stacklevel=2,
        )
    return GraderConfig(**valid)


_cached_config: GraderConfig | None = None


def get_config() -> GraderConfig:
    """Ленивая, кешируемая точка доступа к конфигурации (issue #142).

    Первый вызов читает ``pyproject.toml`` (через ``load_config()``) и
    кэширует результат в памяти процесса; последующие вызовы возвращают тот
    же закэшированный объект без повторного обращения к диску.
    """
    global _cached_config
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


def reset_config_cache() -> None:
    """Сбросить закэшированную конфигурацию — следующий доступ перечитает файл.

    Нужна там, где источник конфига меняется уже в живом процессе:
    ``--config``/``--root`` разбираются после импорта модулей, а те успевают
    связать ``CONFIG`` (issue #984). Значения, уже прочитанные через
    ``from ... import CONFIG``, этим не переприсваиваются — на них влияет
    только порядок вызова, поэтому CLI ставит путь до диспетчеризации в режим.
    """
    global _cached_config
    _cached_config = None


def __getattr__(name: str) -> GraderConfig:
    """Module-level lazy attribute (PEP 562) — ``CONFIG`` вычисляется при
    первом обращении, а не при импорте модуля (issue #142/#145).

    Работает и для ``from stepik_grader.config import CONFIG`` (эта форма
    импорта тоже разрешается через ``getattr`` на модуле), и для
    ``stepik_grader.config.CONFIG`` после голого ``import``.
    """
    if name == "CONFIG":
        return get_config()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
