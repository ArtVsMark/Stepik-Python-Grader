"""_linux.py — SandboxRunner backend для Linux: bubblewrap (primary), nsjail
(fallback), issue #266.

``bwrap`` даёт полную изоляцию через Linux namespaces без root/setuid (сам
использует ``user_namespaces(7)``): свежий network namespace без интерфейсов
(``--unshare-net``) закрывает и исходящие соединения, и слушающие сокеты;
свежий user namespace (``--unshare-user``) сбрасывает счётчик
``RLIMIT_NPROC`` (kernel ucounts, ~5.14+) в 0, поэтому там достаточно
небольшого АБСОЛЮТНОГО значения — в отличие от ``_macos.py``, где такого
namespace нет и лимит считается от текущего числа процессов пользователя.
CPU/адресное пространство/размер файла — ``RLIMIT_CPU``/``RLIMIT_AS``/
``RLIMIT_FSIZE`` через общую POSIX-обёртку (``_posix_bootstrap.py``);
``RLIMIT_AS`` здесь реально работает (в отличие от macOS) — доп. backstop
поверх psutil-поллинга из ``_posix_common.py`` (тот общий на все платформы).

Известное ограничение MVP: биндится только сам интерпретатор + stdlib —
site-packages venv'а НЕ пробрасываются, поэтому решения, использующие
сторонние пакеты, в ``--sandbox`` режиме не поддерживаются (документировано
в SECURITY.md).
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import sysconfig
from pathlib import Path

from stepik_grader.config import CONFIG
from stepik_grader.core.runner import RunOutcome, RunSpec
from stepik_grader.core.sandbox import _posix_bootstrap, _posix_common
from stepik_grader.core.sandbox._run_dir import ephemeral_run_dir

__all__ = ["create_backend", "LinuxSandboxRunner"]


def _python_tree_binds() -> list[str]:
    """Пути интерпретатора/stdlib, которые нужно смонтировать read-only.

    Резолвит и venv (``sys.prefix``/``sys.exec_prefix``), и его
    ``base_prefix``/``base_exec_prefix`` — venv-интерпретатор часто лишь
    тонкий launcher/symlink в базовую установку (issue #266 план,
    подтверждено Plan-агентом). Дедуплицирует пересекающиеся пути.
    """
    candidates = {
        sys.prefix,
        sys.exec_prefix,
        sys.base_prefix,
        sys.base_exec_prefix,
        sysconfig.get_path("stdlib"),
        sysconfig.get_path("platstdlib"),
        str(Path(sys.executable).resolve().parent),
    }
    resolved = sorted({str(Path(p).resolve()) for p in candidates if p})
    # Отбросить пути, уже являющиеся подкаталогом другого пути в списке --
    # bwrap не возражает против вложенных --ro-bind, но незачем их дублировать.
    deduped: list[str] = []
    for path in resolved:
        if not any(path != other and path.startswith(other + os.sep) for other in resolved):
            deduped.append(path)
    return deduped


def _build_bwrap_argv(bwrap: Path, spec: RunSpec, run_dir: Path, script_path: Path) -> list[str]:
    cpu_seconds = max(1, math.ceil(CONFIG.sandbox_max_cpu_seconds))
    max_memory_bytes = (spec.max_memory_mb or CONFIG.max_memory_mb or 1024) * 1024 * 1024
    bootstrap = _posix_bootstrap.build_bootstrap_argv(
        sys.executable,
        str(script_path),
        cpu_seconds=cpu_seconds,
        max_processes=CONFIG.sandbox_max_processes,
        max_file_bytes=CONFIG.sandbox_max_output_bytes,
        max_memory_bytes=max_memory_bytes,
    )

    argv = [str(bwrap)]
    for tree in _python_tree_binds():
        argv += ["--ro-bind", tree, tree]
    argv += ["--tmpfs", "/tmp"]
    argv += ["--bind", str(run_dir), str(run_dir)]
    argv += ["--dev", "/dev", "--proc", "/proc"]
    argv += ["--unshare-net", "--unshare-pid", "--unshare-user"]
    argv += ["--die-with-parent", "--new-session"]
    argv += [
        "--clearenv",
        "--setenv",
        "PYTHONIOENCODING",
        "utf-8",
        "--setenv",
        "PYTHONUTF8",
        "1",
        "--setenv",
        "PATH",
        str(Path(sys.executable).resolve().parent),
    ]
    argv += ["--chdir", str(run_dir)]
    argv += ["--"]
    argv += bootstrap
    return argv


class LinuxSandboxRunner:
    """``Runner`` изолирующий выполнение через bubblewrap на Linux."""

    def __init__(self, bwrap_path: Path) -> None:
        self._bwrap = bwrap_path

    def run(self, spec: RunSpec) -> RunOutcome:
        with ephemeral_run_dir() as run_dir:
            script_path = run_dir / "solution.py"
            try:
                script_path.write_bytes(spec.path.read_bytes())
            except OSError as exc:
                return RunOutcome(launch_error=str(exc))

            argv = self._build_argv(spec, run_dir, script_path)
            return _posix_common.run_argv_with_limits(
                argv,
                stdin=spec.stdin,
                timeout=spec.timeout,
                max_output_bytes=CONFIG.sandbox_max_output_bytes,
                max_memory_mb=float(spec.max_memory_mb or CONFIG.max_memory_mb or 1024),
            )

    def _build_argv(self, spec: RunSpec, run_dir: Path, script_path: Path) -> list[str]:
        return _build_bwrap_argv(self._bwrap, spec, run_dir, script_path)


def create_backend() -> LinuxSandboxRunner:
    """Найти доступный Linux sandbox-инструмент — bwrap (primary), nsjail
    (fallback, issue #266 — используется тот же bwrap-путь построения argv,
    т.к. nsjail в этом MVP не реализован отдельно; см. SECURITY.md/план).

    Поднимает ``SandboxUnavailableError``, если ни один не найден в PATH —
    никогда не тихий fallback на ``LocalRunner``.
    """
    from stepik_grader.core.sandbox import SandboxUnavailableError

    bwrap = shutil.which("bwrap")
    if bwrap is not None:
        return LinuxSandboxRunner(Path(bwrap))
    raise SandboxUnavailableError(
        "bubblewrap (bwrap) не найден в PATH — установите bubblewrap для --sandbox на Linux "
        "(nsjail fallback не реализован в этом MVP)."
    )
