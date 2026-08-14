"""_posix_bootstrap.py — общая self-limiting обёртка для Linux/macOS
backend'ов ``SandboxRunner`` (issue #266).

Вместо post-spawn ``resource.prlimit()`` (``core/runner.py._apply_memory_limit``
— Linux-only, race window между spawn и постановкой лимита) обёртка вызывает
``resource.setrlimit()`` НА СЕБЕ до ``os.execv()`` целевого интерпретатора:
выполняется уже в свежем exec'нутом образе процесса, поэтому не имеет
отношения к небезопасности ``preexec_fn`` в многопоточном родителе (issue
#67) — лимиты гарантированно активны ДО того, как начнёт исполняться код
решения, без окна гонки.

``RLIMIT_AS`` (память) сюда включается ТОЛЬКО опционально
(``max_memory_bytes``) — вызывающая сторона (``_linux.py``) передаёт его как
доп. kernel-level backstop; ``_macos.py`` его не передаёт, потому что
``RLIMIT_AS`` подтверждённо не работает на Darwin — там единственный
работающий механизм измерения/принудительного обрыва по памяти это
psutil-поллинг (см. ``core/runner.py._measure_peak_memory`` и
``_common.py.run_argv_with_limits`` в этом пакете), общий для всех трёх ОС.
"""

from __future__ import annotations

from stepik_grader.core.sandbox import _limits

__all__ = ["build_bootstrap_argv", "cpu_quota_seconds"]

_BOOTSTRAP_SRC = """\
import resource, os, sys
resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds}))
resource.setrlimit(resource.RLIMIT_NPROC, ({max_processes}, {max_processes}))
resource.setrlimit(resource.RLIMIT_FSIZE, ({max_file_bytes}, {max_file_bytes}))
{memory_rlimit_line}
os.execv(sys.argv[1], sys.argv[1:])
"""


def cpu_quota_seconds(timeout: float, configured_max: float) -> int:
    """CPU-квота изоляции — тонкая обёртка над общей формулой (issue #927).

    Сама формула переехала в ``_limits`` вместе с Windows-backend'ом: она
    нужна всем трём, а POSIX-модуль оттуда не импортируется. Имя оставлено —
    на него ссылаются `_linux`/`_macos` и их тесты.
    """
    return _limits.cpu_quota_seconds(timeout, configured_max)


def build_bootstrap_argv(
    interpreter: str,
    script_path: str,
    *,
    cpu_seconds: int,
    max_processes: int,
    max_file_bytes: int,
    max_memory_bytes: int | None = None,
) -> list[str]:
    """Собрать argv self-limiting обёртки.

    Возвращает ``[interpreter, "-c", <bootstrap-код>, interpreter,
    script_path]`` — обёртка сама делает
    ``os.execv(sys.argv[1], sys.argv[1:])``, поэтому внутри ``-c`` она
    видит ``sys.argv == [interpreter, script_path]`` и exec'ает именно
    целевой интерпретатор со скриптом решения в качестве аргумента.

    ``max_memory_bytes`` при задании добавляет ``RLIMIT_AS`` (см. докстринг
    модуля — только для Linux; ``_macos.py`` этот параметр не передаёт).
    ``cpu_seconds`` округляется вызывающей стороной до целого — ``RLIMIT_CPU``
    принимает только целые секунды.
    """
    memory_line = ""
    if max_memory_bytes is not None:
        memory_line = (
            f"resource.setrlimit(resource.RLIMIT_AS, ({max_memory_bytes}, {max_memory_bytes}))"
        )
    src = _BOOTSTRAP_SRC.format(
        cpu_seconds=cpu_seconds,
        max_processes=max_processes,
        max_file_bytes=max_file_bytes,
        memory_rlimit_line=memory_line,
    )
    return [interpreter, "-c", src, interpreter, script_path]
