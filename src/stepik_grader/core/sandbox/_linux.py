"""_linux.py — SandboxRunner backend для Linux: bubblewrap, issue #266.

Единственный реализованный backend в этом MVP. Исходный план (issue #266)
упоминал ``nsjail`` как fallback без ``bwrap`` — не реализован (см.
``create_backend()``/``SandboxUnavailableError`` ниже, ``SECURITY.md``).

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

Enforcement памяти под Linux-sandbox (честно, issue #556): активная линия —
psutil-поллинг RSS **всего поддерева** bwrap (``sample_tree_rss``), потому что
решение исполняется внуком в PID-namespace (``--unshare-pid``) и замер одного
pid bwrap его память не видел; при превышении процесс убивается
(``sandbox_violation="memory"``). Ниже — kernel-backstop ``RLIMIT_AS`` внутри
bootstrap (решение, перешагнувшее лимит адресного пространства, падает
``MemoryError`` → обычный RE). Более точным был бы per-run cgroup v2
``memory.peak``, но он требует делегированного/выделенного cgroup на каждый
запуск, которого MVP на bwrap не создаёт (bwrap изолирует namespace'ами, не
cgroup'ами); это возможное будущее улучшение, а не текущая гарантия — поэтому
источник числа памяти именно tree-RSS.

Известное ограничение MVP: биндится только сам интерпретатор + stdlib —
site-packages venv'а НЕ пробрасываются, поэтому решения, использующие
сторонние пакеты, в ``--sandbox`` режиме не поддерживаются (документировано
в SECURITY.md).
"""

from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path

from stepik_grader.config import CONFIG
from stepik_grader.core.run_dir import ephemeral_run_dir
from stepik_grader.core.runner import RunOutcome, RunSpec, materialize_spec
from stepik_grader.core.sandbox import _posix_bootstrap, _posix_common

__all__ = ["LinuxSandboxRunner", "create_backend"]


def _python_tree_binds() -> list[str]:
    """Пути интерпретатора/stdlib, которые нужно смонтировать read-only.

    Резолвит и venv (``sys.prefix``/``sys.exec_prefix``), и его
    ``base_prefix``/``base_exec_prefix`` — venv-интерпретатор часто лишь
    тонкий launcher/symlink в базовую установку (issue #266 план,
    подтверждено Plan-агентом). Дедуплицирует пересекающиеся пути.

    issue #420: ``/usr`` монтируется всегда — ELF-загрузчик (``ld-linux``) и
    ``libc`` интерпретатора живут под ``/usr`` (usrmerge), а сам интерпретатор
    может стоять ВНЕ ``/usr`` (``/usr/local`` в Docker-образах python,
    hostedtoolcache на CI, pyenv). Без ``/usr`` bwrap падает
    ``execvp: No such file`` уже на загрузчике интерпретатора. Для
    системного Python (``sys.prefix == /usr``) это ничего не меняет — ``/usr``
    и так был в списке.
    """
    candidates = {
        "/usr",
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


def _site_packages_mask_args() -> list[str]:
    """bwrap-аргументы, скрывающие ``site-packages`` грейдера внутри песочницы.

    issue #986 (SEC-2-02/CANON-2-01). Изоляция монтирует дерево интерпретатора
    целиком, а в venv внутри него лежат зависимости грейдера — решение видело
    ``requests``/``psutil``/``rich`` и любой пакет, который окажется в окружении.
    Заявлено ровно обратное: ``supports_project_imports = False`` в самом
    backend'е и дважды SECURITY.md («биндится только интерпретатор+stdlib, не
    venv site-packages»).

    Вред не только в расхождении с обещанием. Решение, случайно опершееся на
    сторонний пакет, проходит локально и падает `ImportError` на Stepik — то
    есть грейдер выдаёт зелёное там, где проверка на самом деле не пройдена.

    Каталог перекрывается пустой ``tmpfs``: stdlib живёт отдельно
    (``sysconfig.get_path("stdlib")``, обычно под ``/usr``), поэтому
    интерпретатор стартует как ни в чём не бывало. Пути вне примонтированных
    деревьев пропускаем — bwrap отвергает ``--tmpfs`` на несуществующей точке.
    """
    trees = _python_tree_binds()
    masked: list[str] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_paths().get(key)
        if not raw:
            continue
        path = str(Path(raw).resolve())
        inside_tree = any(path == tree or path.startswith(tree + os.sep) for tree in trees)
        if inside_tree and path not in masked:
            masked.append(path)

    args: list[str] = []
    for path in masked:
        args += ["--tmpfs", path]
    return args


def _usrmerge_symlink_args() -> list[str]:
    """bwrap-аргументы, воссоздающие top-level usrmerge-симлинки внутри песочницы.

    На usrmerge-системах (совр. Ubuntu, включая GitHub CI runners) top-level
    ``/lib``/``/lib64``/``/bin``/… — симлинки в ``/usr/lib*``/``/usr/bin``. Мы
    биндим только ``/usr`` (``_python_tree_binds``), поэтому без этих симлинков
    ELF-загрузчик решения (``/lib64/ld-linux-*``) внутри песочницы не находится
    и bwrap падает ``execvp: No such file`` на самом интерпретаторе (issue #420).
    Воссоздаём только реально существующие симлинки, ведущие в примонтированный
    ``/usr`` — не пробрасываем произвольные симлинки хоста.
    """
    args: list[str] = []
    for link in ("/lib", "/lib64", "/lib32", "/libx32", "/bin", "/sbin"):
        p = Path(link)
        if p.is_symlink() and p.resolve().is_relative_to("/usr"):
            args += ["--symlink", str(p.readlink()), link]
    return args


def _build_bwrap_argv(bwrap: Path, spec: RunSpec, run_dir: Path, script_path: Path) -> list[str]:
    # issue #986 (PY-2-01): квота выводится из wall-таймаута прогона, а не
    # берётся константой — иначе разрешённый пользователем долгий прогон
    # режется на середине.
    cpu_seconds = _posix_bootstrap.cpu_quota_seconds(spec.timeout, CONFIG.sandbox_max_cpu_seconds)
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
    # issue #986 (SEC-2-02): скрыть site-packages грейдера — ПОСЛЕ bind'ов дерева
    # интерпретатора, иначе монтирование дерева перекрыло бы саму маску.
    argv += _site_packages_mask_args()
    # issue #420: воссоздать usrmerge-симлинки (/lib64 -> /usr/lib64 и т.п.) ПОСЛЕ
    # bind'а /usr — иначе ELF-загрузчик решения не находится (см. helper).
    argv += _usrmerge_symlink_args()
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
        # issue #726: без ANSI-раскраски traceback'а (Python 3.13+), как в
        # LocalRunner и остальных backend'ах.
        "--setenv",
        "PYTHON_COLORS",
        "0",
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

    supports_project_imports = False  # issue #550: site-packages проекта не в песочнице

    def __init__(self, bwrap_path: Path) -> None:
        self._bwrap = bwrap_path

    def run(self, spec: RunSpec) -> RunOutcome:
        with ephemeral_run_dir() as run_dir:
            # issue #992: имя скрипта и файлы-спутники приходят из spec —
            # в function-режиме обёртку нельзя класть под именем модуля решения.
            try:
                script_path = materialize_spec(spec, run_dir)
            except (OSError, ValueError) as exc:
                return RunOutcome(launch_error=str(exc))

            argv = self._build_argv(spec, run_dir, script_path)
            return _posix_common.run_argv_with_limits(
                argv,
                stdin=spec.stdin,
                timeout=spec.timeout,
                # issue #799 (PY-13): лимит вывода берётся из RunSpec, если он
                # задан, — контракт RunSpec заявлен config-agnostic (``core/runner``),
                # а backend читал только CONFIG, поэтому per-request лимит
                # серверного API под --sandbox молча игнорировался. CONFIG
                # остаётся значением по умолчанию.
                max_output_bytes=spec.max_output_bytes or CONFIG.sandbox_max_output_bytes,
                max_memory_mb=float(spec.max_memory_mb or CONFIG.max_memory_mb or 1024),
                # issue #797: отмена работает и под изоляцией — раньше поле
                # RunSpec просто не доезжало до цикла ожидания.
                cancel_event=spec.cancel_event,
            )

    def _build_argv(self, spec: RunSpec, run_dir: Path, script_path: Path) -> list[str]:
        return _build_bwrap_argv(self._bwrap, spec, run_dir, script_path)


def create_backend() -> LinuxSandboxRunner:
    """Найти ``bwrap`` в PATH — единственный Linux sandbox-backend в этом MVP.

    Исходный план (issue #266) допускал ``nsjail`` как fallback без ``bwrap``
    — не реализован (см. ``SECURITY.md``). Поднимает
    ``SandboxUnavailableError``, если ``bwrap`` не найден — никогда не тихий
    fallback на ``LocalRunner``.
    """
    from stepik_grader.core.sandbox import SandboxUnavailableError

    bwrap = shutil.which("bwrap")
    if bwrap is not None:
        return LinuxSandboxRunner(Path(bwrap))
    raise SandboxUnavailableError(
        "bubblewrap (bwrap) не найден в PATH — установите bubblewrap для --sandbox на Linux "
        "(nsjail fallback не реализован в этом MVP)."
    )
