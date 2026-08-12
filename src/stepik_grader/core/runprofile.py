"""core/runprofile.py — паспорт условий прогона (issue #984).

Архитектурный слой: Application / Configuration.
Зависит от ``config`` (значения настроек) и ``core/runner`` (активный
исполнитель) — оба ниже по DAG, новых циклов не создаёт.

Зачем. Вердикт грейдера определяется не только решением и набором кейсов:
таймаут, лимиты вывода и памяти, кодировка, наличие ОС-изоляции и версия
интерпретатора меняют его так же надёжно. Раньше эти условия нигде не
объявлялись — ни в отчёте, ни в ключе кэша, — поэтому один и тот же вход давал
разный ответ в зависимости от каталога запуска и состояния ``.grader_cache``,
а кэш отдавал обычному прогону вердикт, порождённый песочницей.

Паспорт собирает их в одну структуру, у которой два потребителя:

- **отчёт** — шапка прогона печатает, чем именно проверяли (``describe()``);
- **кэш** — ``fingerprint`` входит в запись наравне с хешами решения и
  тестов, поэтому смена условий инвалидирует вердикт.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import pathlib
import sys
from dataclasses import dataclass

from stepik_grader.config import config_source, get_config
from stepik_grader.core.runner import active_runner

__all__ = ["RunProfile", "current_profile"]


def _package_version() -> str:
    """Версия установленного пакета; ``"unknown"``, если метаданных нет.

    Best-effort по образцу ``cli._resolve_version()``: запуск из клона без
    ``pip install -e .`` — рабочий сценарий, ронять из-за него паспорт нельзя.
    """
    try:
        return importlib.metadata.version("stepik-python-grader")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True)
class RunProfile:
    """Условия, при которых получен вердикт. ``frozen=True`` — снимок, не состояние."""

    runner: str
    sandbox_backend: str | None
    timeout_seconds: float
    max_output_bytes: int
    max_memory_mb: int | None
    sandbox_max_cpu_seconds: float
    sandbox_max_processes: int
    sandbox_max_output_bytes: int
    measure_child_memory: bool
    encoding: str
    python: str
    package_version: str
    config_path: pathlib.Path | None

    @property
    def fingerprint(self) -> str:
        """sha256 условий ИСПОЛНЕНИЯ — ключ инвалидации кэша.

        В отпечаток входят только поля, способные изменить результат прогона.
        Путь конфига намеренно не входит: значим не файл, а значения из него —
        иначе одинаковая конфигурация в двух каталогах давала бы промах на
        ровном месте.
        """
        payload = {
            "runner": self.runner,
            "sandbox_backend": self.sandbox_backend,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "max_memory_mb": self.max_memory_mb,
            "sandbox_max_cpu_seconds": self.sandbox_max_cpu_seconds,
            "sandbox_max_processes": self.sandbox_max_processes,
            "sandbox_max_output_bytes": self.sandbox_max_output_bytes,
            "measure_child_memory": self.measure_child_memory,
            "encoding": self.encoding,
            "python": self.python,
            "package_version": self.package_version,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    @property
    def isolated(self) -> bool:
        """Исполнение идёт в ОС-песочнице (``--sandbox``)."""
        return self.sandbox_backend is not None

    def describe(self) -> str:
        """Исполнитель для шапки отчёта: ``LocalRunner`` или ``SandboxRunner (X)``.

        Локализуемые подписи собираются в CLI-слое — здесь только имена
        классов, одинаковые на всех языках.
        """
        if self.sandbox_backend is None:
            return self.runner
        return f"{self.runner} ({self.sandbox_backend})"


def current_profile() -> RunProfile:
    """Снять паспорт текущего процесса: активный runner + действующий конфиг.

    Читается в момент вызова, а не при импорте: ``--sandbox`` подменяет runner
    уже после загрузки модулей, а ``--config``/``--root`` — источник настроек.
    """
    cfg = get_config()
    runner = active_runner()
    backend = getattr(runner, "backend_name", None)
    return RunProfile(
        runner=type(runner).__name__,
        sandbox_backend=backend,
        timeout_seconds=cfg.timeout_seconds,
        max_output_bytes=cfg.max_output_bytes,
        max_memory_mb=cfg.max_memory_mb,
        sandbox_max_cpu_seconds=cfg.sandbox_max_cpu_seconds,
        sandbox_max_processes=cfg.sandbox_max_processes,
        sandbox_max_output_bytes=cfg.sandbox_max_output_bytes,
        measure_child_memory=cfg.measure_child_memory,
        encoding=cfg.encoding,
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        package_version=_package_version(),
        config_path=config_source(),
    )
