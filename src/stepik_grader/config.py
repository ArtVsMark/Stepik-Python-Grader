"""config.py — единая конфигурация грейдера.

Архитектурный слой: Application / Configuration.
Заменяет разбросанные по grader_core.py / core/executor.py хардкод-константы
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
import pathlib
import tomllib
from dataclasses import dataclass

__all__ = ["GraderConfig", "load_config", "get_config", "CONFIG"]  # noqa: F822 (CONFIG — module __getattr__, PEP 562)


@dataclass(frozen=True)
class GraderConfig:
    """Единая конфигурация грейдера. frozen=True — потокобезопасно."""

    timeout_seconds: float = 10.0
    executor_timeout: int = 10
    similar_threshold: float = 1.15
    much_slower_threshold: float = 1.50
    measure_child_memory: bool = True
    microbench_max_cases: int = 5
    encoding: str = "utf-8"
    max_memory_mb: int | None = 1024
    use_cache: bool = False  # issue #56 — opt-in кэш результатов (--cache)
    # issue #125 — локальная база карточек глоссария (None → компактный
    # core/glossary.py как fallback) и очередь пополнения (MissingConceptDetector).
    glossary_store: str | None = None
    glossary_missing_queue: str = ".grader_glossary_missing.json"


def load_config() -> GraderConfig:
    """Загружает конфиг из [tool.stepik-grader] в pyproject.toml.

    Если pyproject.toml отсутствует или секция не найдена —
    возвращает GraderConfig с дефолтными значениями. Всегда перечитывает файл
    заново (без кэша) — кэширование для типичного пути потребления делает
    ``get_config()``/``CONFIG``, эта функция остаётся простым loader'ом.
    """
    # src/-layout (Issue #35): config.py живёт в src/stepik_grader/, а
    # pyproject.toml — в корне репозитория, на два уровня выше.
    pyproject = pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
    if not pyproject.exists():
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
