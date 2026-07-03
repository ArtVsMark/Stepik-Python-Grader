"""config.py — единая конфигурация грейдера.

Архитектурный слой: Application / Configuration.
Заменяет разбросанные по grader_core.py / core/executor.py хардкод-константы
единой точкой правды. Значения переопределяются через секцию
[tool.stepik-grader] в pyproject.toml; при отсутствии файла или секции
используются дефолты из GraderConfig.
"""

from __future__ import annotations

import pathlib
import tomllib
from dataclasses import dataclass

__all__ = ["GraderConfig", "load_config", "CONFIG"]


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


def load_config() -> GraderConfig:
    """Загружает конфиг из [tool.stepik-grader] в pyproject.toml.

    Если pyproject.toml отсутствует или секция не найдена —
    возвращает GraderConfig с дефолтными значениями.
    """
    # src/-layout (Issue #35): config.py живёт в src/stepik_grader/, а
    # pyproject.toml — в корне репозитория, на два уровня выше.
    pyproject = pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return GraderConfig()
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    overrides = data.get("tool", {}).get("stepik-grader", {})
    valid = {k: v for k, v in overrides.items() if k in GraderConfig.__dataclass_fields__}
    return GraderConfig(**valid)


CONFIG: GraderConfig = load_config()
