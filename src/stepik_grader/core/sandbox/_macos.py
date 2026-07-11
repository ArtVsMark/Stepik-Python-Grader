"""_macos.py — SandboxRunner backend для macOS: ``sandbox-exec`` (Seatbelt),
issue #266.

``sandbox-exec`` официально deprecated (нет несигнатурной замены для CLI —
App Sandbox требует entitled/подписанного .app, недоступно для запуска
произвольного скрипта грейдером), но подтверждённо ещё работает на текущих
macOS (Sonoma/Sequoia) — эмитит предупреждение в stderr при запуске
(``sandbox-exec: -f ... deprecated`` и подобные), которое здесь фильтруется
из ``RunOutcome.stderr``, чтобы не путать пользователя с выводом решения.

Политика чтения файлов — ``(allow file-read*)`` без ограничения по пути,
а не перечисление конкретных системных/venv путей. Перечисление (dyld,
``/usr/lib``, ``/System/Library``, venv/stdlib) пробовалось дважды и оба
раза приводило к SIGABRT ("Abort trap: 6") уже на старте интерпретатора,
до первой строчки кода решения — dyld/CPython падают там, где им отказано
в чтении чего-то по своему пути инициализации, а enumeration органически
хрупкая: должна точно совпасть с тем, что реально трогает загрузчик на
конкретном образе ОС, и гарантированно этого не делает. Независимый разбор
той же задачи и актуальная Seatbelt-политика OpenAI Codex (реальный
продакшен-инструмент с идентичной задачей "запустить произвольный процесс
под deny-default профилем") используют именно ``(allow file-read*)``.
Это не потеря гарантии: SECURITY.md для macOS никогда не обещал изоляцию
ЧТЕНИЯ файлов — только запись (ограничена ``run_dir`` ниже), сеть
(``(deny network*)``) и ресурсы (CPU/память/процессы через
``_posix_bootstrap``/``_posix_common``).

Отличия от Linux-backend'а (``_linux.py``), оба задокументированы в
SECURITY.md:
- ``RLIMIT_AS`` **не работает на Darwin** (подтверждено независимо и
  Plan-агентом при повторной валидации — не bpo-34602, та issue про
  ``RLIMIT_STACK``, не про ``RLIMIT_AS``) — сюда НЕ передаётся
  ``max_memory_bytes`` в ``_posix_bootstrap.build_bootstrap_argv()``;
  единственный механизм — psutil-поллинг в
  ``_posix_common.run_argv_with_limits()`` (общий на все платформы, но
  здесь это единственная линия обороны, а не backstop).
- ``RLIMIT_NPROC``: нет user-namespace аналога, счётчик общий на реального
  uid процесса — лимит ставится не абсолютным значением, а как
  сэмплированное текущее число процессов пользователя + бюджет
  (``CONFIG.sandbox_max_processes``), иначе посторонние процессы того же
  пользователя могли бы случайно выбить лимит для решения.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
from pathlib import Path

import psutil

from stepik_grader.config import CONFIG
from stepik_grader.core.runner import RunOutcome, RunSpec
from stepik_grader.core.sandbox import _posix_bootstrap, _posix_common
from stepik_grader.core.sandbox._run_dir import ephemeral_run_dir

__all__ = ["create_backend", "MacSandboxRunner"]

_DEPRECATION_MARKERS = (b"sandbox-exec", b"deprecated")


def _quote_sb_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _build_profile(run_dir: Path) -> str:
    run_dir_q = _quote_sb_path(str(run_dir.resolve()))
    return f"""(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow sysctl-read)
(allow mach-lookup)
(allow file-read*)
(allow file-read* file-write* (subpath "{run_dir_q}"))
(deny network*)
(allow signal (target self))
"""


def _sampled_max_processes() -> int:
    """Текущее число процессов реального uid + бюджет (см. докстринг модуля)."""
    try:
        uid = os.getuid()  # type: ignore[attr-defined]  # POSIX-only; см. _posix_common.py
        current = sum(
            1
            for p in psutil.process_iter(["uids"])
            if p.info["uids"] is not None and p.info["uids"].real == uid
        )
    except (psutil.Error, OSError):
        current = 0
    return current + CONFIG.sandbox_max_processes


def _strip_deprecation_warning(stderr: bytes) -> bytes:
    lines = stderr.split(b"\n")
    kept = [
        line
        for line in lines
        if not (b"sandbox-exec" in line and (b"deprecated" in line or b"WARNING" in line))
    ]
    return b"\n".join(kept)


class MacSandboxRunner:
    """``Runner`` изолирующий выполнение через ``sandbox-exec`` на macOS."""

    def __init__(self, sandbox_exec_path: str) -> None:
        self._sandbox_exec = sandbox_exec_path

    def run(self, spec: RunSpec) -> RunOutcome:
        with ephemeral_run_dir() as run_dir:
            script_path = run_dir / "solution.py"
            profile_path = run_dir / "profile.sb"
            try:
                script_path.write_bytes(Path(spec.path).read_bytes())
                profile_path.write_text(_build_profile(run_dir), encoding="utf-8")
            except OSError as exc:
                return RunOutcome(launch_error=str(exc))

            cpu_seconds = max(1, math.ceil(CONFIG.sandbox_max_cpu_seconds))
            bootstrap = _posix_bootstrap.build_bootstrap_argv(
                sys.executable,
                str(script_path),
                cpu_seconds=cpu_seconds,
                max_processes=_sampled_max_processes(),
                max_file_bytes=CONFIG.sandbox_max_output_bytes,
                max_memory_bytes=None,  # RLIMIT_AS не работает на Darwin -- см. докстринг модуля
            )
            argv = [self._sandbox_exec, "-f", str(profile_path), "--", *bootstrap]

            outcome = _posix_common.run_argv_with_limits(
                argv,
                stdin=spec.stdin,
                timeout=spec.timeout,
                max_output_bytes=CONFIG.sandbox_max_output_bytes,
                max_memory_mb=float(spec.max_memory_mb or CONFIG.max_memory_mb or 1024),
            )
            outcome.stderr = _strip_deprecation_warning(outcome.stderr)
            return outcome


def create_backend() -> MacSandboxRunner:
    """Найти ``sandbox-exec`` — поднимает ``SandboxUnavailableError``, если
    отсутствует (никогда не тихий fallback на ``LocalRunner``)."""
    from stepik_grader.core.sandbox import SandboxUnavailableError

    sandbox_exec = shutil.which("sandbox-exec")
    if sandbox_exec is not None:
        return MacSandboxRunner(sandbox_exec)
    raise SandboxUnavailableError(
        "sandbox-exec не найден — --sandbox недоступен на этой сборке macOS."
    )
