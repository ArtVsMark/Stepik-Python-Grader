"""core/sandbox — ``SandboxRunner``: ОС-уровневая изоляция исполнения решений
(issue #266, реализация требований дизайна ``docs/dev/design/server-mode.md § Sandbox и
сетевая изоляция``, issue #157).

Архитектурный слой: Infrastructure — тот же протокол ``Runner``
(``core/runner.py``), просто другой backend; ``core/grader_core.py`` не
знает и не должен знать, какой конкретно активен — инъекция через
``grader_core.set_runner()``, обычно из CLI (``--sandbox``).

Три backend'а, по одному на ОС, с РАЗНЫМИ гарантиями (полная таблица —
``SECURITY.md``, не дублируется здесь):

- Linux (``_linux.py``) — bubblewrap (единственный backend в этом MVP): сеть/
  ФС/CPU/процессы изолированы ядром (namespaces + RLIMIT_*), плюс RLIMIT_AS
  как доп. backstop для памяти. Исходный план (issue #266) допускал
  ``nsjail`` как fallback без ``bwrap`` — не реализован (см. SECURITY.md).
- macOS (``_macos.py``) — ``sandbox-exec``: сеть/запись-в-ФС/CPU изолированы
  ядром (чтение файлов сознательно НЕ ограничено — см. докстринг
  ``_macos.py``); память — psutil-поллинг (RLIMIT_AS не работает на
  Darwin); anti-fork-bomb слабее (нет namespace-аналога, только
  сэмплированный RLIMIT_NPROC-бюджет).
- Windows (``_windows.py``) — Job Objects: память/CPU/процессы — ядром,
  БЕЗ сетевой изоляции и без строгой ФС-изоляции (только cwd-контейнмент
  относительных путей) — оба пробела задокументированы, не тихий пропуск.

Backend недоступен на текущей платформе/окружении (нет ``bwrap`` на Linux,
``sandbox-exec`` отсутствует на macOS, Job Objects API недоступен на
Windows) → ``SandboxUnavailableError`` с понятной причиной — НИКОГДА не
тихий fallback на ``LocalRunner`` (issue #266, явное требование).
"""

from __future__ import annotations

import platform

from stepik_grader.core.runner import Runner, RunOutcome, RunSpec

__all__ = ["SandboxRunner", "SandboxUnavailableError"]


class SandboxUnavailableError(RuntimeError):
    """Ни один sandbox-backend не доступен на этой платформе/окружении."""


class SandboxRunner:
    """``Runner`` с ОС-уровневой изоляцией — фасад над платформенным backend'ом.

    Backend выбирается один раз при конструировании (не лениво на первом
    ``run()``) — так ``--sandbox`` падает сразу понятной ошибкой при старте
    CLI, если backend недоступен, а не посреди прогона на первом тест-кейсе.
    """

    # issue #550: ОС-изоляция намеренно НЕ пробрасывает site-packages проекта в
    # песочницу (SECURITY.md) — пакет грейдера недоступен дочернему процессу,
    # поэтому пошаговый трейс под --sandbox честно отклоняется (core/tracer).
    supports_project_imports = False

    def __init__(self) -> None:
        system = platform.system()
        if system == "Linux":
            from stepik_grader.core.sandbox import _linux

            self._backend: Runner = _linux.create_backend()
        elif system == "Darwin":
            from stepik_grader.core.sandbox import _macos

            self._backend = _macos.create_backend()
        elif system == "Windows":
            from stepik_grader.core.sandbox import _windows

            self._backend = _windows.create_backend()
        else:
            raise SandboxUnavailableError(f"--sandbox не поддерживается на платформе {system!r}")

    def run(self, spec: RunSpec) -> RunOutcome:
        """Делегировать выбранному при конструировании backend'у."""
        return self._backend.run(spec)

    @property
    def backend_name(self) -> str:
        """Имя платформенного backend'а — для паспорта прогона (issue #984).

        Публичный аксессор вместо ``type(runner._backend).__name__`` у
        вызывающего: гарантии изоляции у трёх backend'ов разные, поэтому в
        отчёте и в ключе кэша должен стоять конкретный, а не общее «песочница».
        """
        return type(self._backend).__name__
