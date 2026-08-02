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
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import tomllib
from dataclasses import dataclass

__all__ = ["CONFIG", "GraderConfig", "get_config", "load_config"]  # noqa: F822 (CONFIG — module __getattr__, PEP 562)

_ENV_CONFIG_PATH = "STEPIK_GRADER_CONFIG"


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


def _find_pyproject(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """Ищет pyproject.toml от ``start`` (по умолчанию cwd) вверх до корня ФС.

    Паттерн поиска конфига, общий для pip/ruff/mypy — первый найденный файл
    выигрывает. Не проверяет наличие секции ``[tool.stepik-grader]`` внутри —
    это делает ``load_config()``.
    """
    current = (start or pathlib.Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        candidate_path = candidate / "pyproject.toml"
        if candidate_path.is_file():
            return candidate_path
    return None


def _resolve_pyproject_path() -> pathlib.Path | None:
    """Определяет путь к pyproject.toml (issue #258).

    Порядок разрешения: ``STEPIK_GRADER_CONFIG`` (если указывает на
    существующий файл) → поиск от ``cwd`` вверх → legacy-путь относительно
    расположения пакета (src/-layout, Issue #35, сохраняет поведение при
    запуске тестов из корня репозитория). Невалидное значение переменной
    окружения не поднимает исключение — резолюция просто продолжается со
    следующего источника.
    """
    env_value = os.environ.get(_ENV_CONFIG_PATH)
    if env_value:
        env_path = pathlib.Path(env_value)
        if env_path.is_file():
            return env_path

    found = _find_pyproject()
    if found is not None:
        return found

    legacy = pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
    if legacy.is_file():
        return legacy

    return None


def load_config() -> GraderConfig:
    """Загружает конфиг из [tool.stepik-grader] в pyproject.toml.

    Путь к pyproject.toml резолвится через ``_resolve_pyproject_path()``
    (env → поиск от cwd вверх → legacy fallback, issue #258). Если файл не
    найден или секция отсутствует — возвращает GraderConfig с дефолтными
    значениями. Всегда перечитывает файл заново (без кэша) — кэширование для
    типичного пути потребления делает ``get_config()``/``CONFIG``, эта
    функция остаётся простым loader'ом.
    """
    pyproject = _resolve_pyproject_path()
    if pyproject is None:
        return GraderConfig()
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    overrides = data.get("tool", {}).get("stepik-grader", {})
    # issue #143: dataclasses.fields() — публичный API вместо приватного
    # dunder-атрибута dataclass (то же множество имён полей, поведение не меняется).
    valid_names = {f.name for f in dataclasses.fields(GraderConfig)}
    valid = {k: v for k, v in overrides.items() if k in valid_names}
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
