#!/usr/bin/env python3
"""scripts/preflight.py — один вход вместо чек-листа «перед PR» (issue #997).

Разбор сессии 2026-08-13 дал одиннадцать инцидентов, и почти каждый — не
незнание правила, а его пропуск: правило «прогонять ВЕСЬ набор» уже было в
``CLAUDE.md``, когда чужой тест сломали профильной выборкой. Текст, который
надо вспомнить, соблюдается ровно до первой спешки, поэтому здесь он заменён
командой, которая проверяет то же самое сама.

Что закрывает (в скобках — инцидент, из которого правило выросло):

* **свежесть ветки** — гейты гоняются на состоянии «моя ветка + свежий
  ``origin/main``», а не на ветке, созданной до чужого мержа: «зелено у меня»
  ≠ «зелено после мержа» (красный ``main`` из-за незадокументированного ребра
  DAG);
* **имя ветки не занято** — ветка с таким именем уже живёт на ``origin`` и
  ведётся другим окном: пуш отлетит «tip is behind», а правка уедет в чужой PR
  (дубль работы двух окон);
* **один прогон за раз** — параллельные ``pytest`` исчерпывают дескрипторы, и
  тесты с subprocess падают пачкой; полдня уходит на разбор 72 «регрессий»,
  которых нет (файл блокировки ``preflight.lock``);
* **весь набор, а не выборка** — чужие тесты патчат наши имена, и переименование
  ломает их молча;
* **вывод прогона целиком в файле** — ``pytest | tail -8`` в фоне отбрасывает
  всё остальное, и шестиминутный прогон приходится повторять;
* **запись о изменении** — фрагмент ``changelog.d/<slug>.<секция>.md`` требуется
  в каждом PR, но его наличие CI не проверяет, значит проверка держалась на
  памяти (строка в ``## Буфер`` тоже принимается — ветки, начатые до перехода
  на фрагменты, не должны краснеть на ровном месте).

Запуск::

    python scripts/preflight.py                # всё: гигиена ветки + линтеры + весь pytest
    python scripts/preflight.py --branch-only  # только гигиена ветки, до начала работы
    python scripts/preflight.py --no-tests     # без pytest (правки только в докáх)

Успешный полный прогон оставляет штамп ``preflight-stamp.json`` с отпечатком
проверенного СОДЕРЖИМОГО рабочего дерева — по нему pre-push хук отличает
«проверено» от «забыл». Привязка к содержимому, а не к ``HEAD``, намеренная:
прогон всегда идёт до коммита, и штамп на SHA обесценивался бы ближайшим
``git commit`` — то есть хук отклонял бы пуш ровно того состояния, которое сам
же и проверил.

Штамп, блокировка и логи живут в служебном каталоге git, и путь к нему спрашивают
у самого git (``stamp_path``/``lock_path``/``logs_dir``), а не собирают как
``<корень>/.git`` (PR #PRNUM). В рабочем дереве ``git worktree`` ``.git`` — это
ФАЙЛ со строкой ``gitdir: ...``, поэтому собранный путь не просто ведёт не туда:
``mkdir(parents=True, exist_ok=True)`` падает ``FileExistsError`` — ``exist_ok``
прощает существующий каталог, а не файл, — и гейт умирал до первой проверки.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence

__all__ = [
    "Check",
    "added_changelog_fragments",
    "added_changelog_lines",
    "buffer_section",
    "changed_public_names",
    "check_branch_fresh",
    "check_branch_not_main",
    "check_branch_not_taken",
    "check_changelog_buffer",
    "lock_is_active",
    "lock_path",
    "logs_dir",
    "main",
    "read_stamp",
    "stamp_is_current",
    "stamp_path",
    "worktree_fingerprint",
    "write_stamp",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_BASE = "origin/main"
_STAMP_NAME = "preflight-stamp.json"
_LOCK_NAME = "preflight.lock"
_LOGS_NAME = "preflight-logs"

# Аварийный выход: пуш срочного фикса, когда прогон физически негде сделать.
# Осознанное решение человека, а не значение по умолчанию.
_SKIP_ENV = "PREFLIGHT_SKIP"

_HOOK_BODY = """#!/bin/sh
# issue #997: пуш только с проверенного коммита.
# Аварийный выход: PREFLIGHT_SKIP=1 git push ...
python scripts/preflight.py --gate-push || exit 1
"""

# Блокировка считается протухшей через два часа: полный набор на медленной
# машине идёт минуты, а не часы, поэтому более старый файл почти наверняка
# остался от процесса, который убили, а не от живого прогона.
_LOCK_TTL_SECONDS = 2 * 60 * 60

GitRunner = Callable[..., str]


@dataclasses.dataclass(frozen=True)
class Check:
    """Результат одной проверки: имя, вердикт, подробность и совет."""

    name: str
    ok: bool
    detail: str = ""
    hint: str = ""
    blocking: bool = True


# issue #1149: сколько ждём САМ запуск `git`. Здоровый спавн укладывается в
# миллисекунды; порог отличает «подвисло навсегда» от «система под нагрузкой».
_GIT_LAUNCH_TIMEOUT_S = 20.0

# issue #1232: та же переменная, что у `core/spawn` — один порог на весь класс
# «медленный раннер», а не два независимых. Значение читается здесь заново по
# той же причине, по которой продублирован сам приём: гейт обязан работать,
# когда пакет не установлен или сломан.
_ENV_LAUNCH_TIMEOUT = "STEPIK_GRADER_LAUNCH_TIMEOUT_S"


def _git_launch_timeout_s() -> float:
    """Действующий дедлайн запуска `git`: переменная окружения или дефолт."""
    try:
        value = float(os.environ.get(_ENV_LAUNCH_TIMEOUT, "").strip())
    except ValueError:
        return _GIT_LAUNCH_TIMEOUT_S
    return value if value > 0 else _GIT_LAUNCH_TIMEOUT_S


def _run_guarded[T](call: Callable[[], T]) -> T | None:
    """Выполнить ``call`` с дедлайном, покрывающим и ЗАПУСК процесса (issue #1149).

    ``timeout=`` у ``subprocess`` покрывает ожидание уже стартовавшего процесса,
    а подвиснуть можно раньше — в ``Popen.__init__``, на чтении errpipe после
    fork/exec. Ровно так гейт и висел на macOS + Python 3.14, пока pytest не
    снимал прогон по своему таймауту.

    Приём тот же, что в ``core/spawn.py`` (там канон и подробное объяснение), но
    продублирован намеренно: ``preflight.py`` обязан работать и тогда, когда сам
    пакет не установлен или сломан — импорт из него превратил бы гейт в
    заложника проверяемого кода.

    Returns:
        Результат вызова или ``None``, если он не уложился в дедлайн.
    """
    outcome: list[T | BaseException] = []

    def _worker() -> None:
        try:
            outcome.append(call())
        except BaseException as exc:  # переносим в вызывающий поток как есть
            outcome.append(exc)

    thread = threading.Thread(target=_worker, daemon=True, name="preflight-git")
    thread.start()
    thread.join(_git_launch_timeout_s())
    if thread.is_alive() or not outcome:
        return None
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


def _git(*args: str) -> str:
    """``git`` в корне репозитория; пустая строка при любой ошибке.

    Кодировка задана явно: ``text=True`` декодирует вывод локальной кодировкой
    (на Windows это cp1251/cp866), и русские строки диффа приезжают искажёнными
    — сравнение с текстом файла, прочитанным как UTF-8, тихо перестаёт
    совпадать. Гейт при этом не падает, а **врёт**, что записи в CHANGELOG нет.

    Зависший запуск (issue #1149) даёт пустую строку — тот же исход, что у
    любого другого сбоя ``git``, вместо бесконечного ожидания.
    """

    def _call() -> str:
        return subprocess.check_output(
            ["git", *args],
            cwd=_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip()

    try:
        return _run_guarded(_call) or ""
    except (OSError, subprocess.CalledProcessError):
        return ""


def _git_ok(*args: str) -> bool:
    """Истина, когда команда завершилась успешно (проверки вида ``--is-ancestor``)."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def current_branch(git: GitRunner = _git) -> str:
    """Имя текущей ветки (``HEAD`` в detached-состоянии)."""
    return git("rev-parse", "--abbrev-ref", "HEAD")


def check_branch_not_main(git: GitRunner = _git) -> Check:
    """Прямые коммиты в ``main`` запрещены — работа идёт через PR."""
    branch = current_branch(git)
    ok = branch not in {"main", "HEAD"}
    return Check(
        name="ветка не main",
        ok=ok,
        detail=f"текущая ветка: {branch or 'неизвестна'}",
        hint="git checkout -b <type>/<short-slug>",
    )


def check_branch_fresh(git: GitRunner = _git, *, ancestor: Callable[..., bool] = _git_ok) -> Check:
    """``origin/main`` должен быть предком ``HEAD``.

    Гейт, прогнанный на ветке от вчерашнего ``main``, проверяет состояние,
    которого после мержа не будет: так на ``main`` уехал импорт без записи в
    графе зависимостей — локально 18 passed, в CI красные все ubuntu-джобы.
    """
    ok = ancestor("merge-base", "--is-ancestor", _BASE, "HEAD")
    behind = git("rev-list", "--count", f"HEAD..{_BASE}") or "?"
    return Check(
        name="ветка от свежего main",
        ok=ok,
        detail="в основе свежий main" if ok else f"main ушёл вперёд на {behind} коммит(ов)",
        hint=f"git fetch origin main && git merge --ff-only {_BASE}",
    )


def check_branch_not_taken(
    git: GitRunner = _git, *, ancestor: Callable[..., bool] = _git_ok
) -> Check:
    """Одноимённая ветка на ``origin`` не должна быть чужой работой.

    Совпадение имени — это либо своя же ветка (тогда её вершина достижима из
    ``HEAD``), либо соседнее окно, которое ведёт свой PR: пуш туда отлетает
    «tip is behind», а при ``--force`` затирает чужое.
    """
    branch = current_branch(git)
    remote = f"origin/{branch}"
    exists = bool(git("rev-parse", "--verify", "--quiet", remote))
    if not exists:
        return Check(name="имя ветки свободно", ok=True, detail=f"{remote} на origin нет")
    ok = ancestor("merge-base", "--is-ancestor", remote, "HEAD")
    return Check(
        name="имя ветки свободно",
        ok=ok,
        detail=f"{remote} — продолжение моей работы" if ok else f"{remote} разошлась с этой веткой",
        hint=(
            "проверьте, не ведёт ли её соседнее окно: в чужую ветку не пушить. "
            "После squash-мержа своего же PR расхождение нормально — squash не "
            "сохраняет прежнюю вершину предком"
        ),
        blocking=False,
    )


def buffer_section(changelog: str) -> list[str]:
    """Строки секции ``## Буфер`` (до следующего заголовка ``##``)."""
    lines = changelog.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith("## Буфер")
            continue
        if inside:
            collected.append(line)
    return collected


def added_changelog_lines(diff: str) -> list[str]:
    """Добавленные диффом строки-записи ``- Fixed: ...`` без ведущего ``+``."""
    added = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if body.lstrip().startswith("- "):
                added.append(body)
    return added


def added_changelog_fragments(git: GitRunner = _git) -> list[str]:
    """Фрагменты ``changelog.d/``, добавленные этой веткой."""
    return sorted(
        path
        for path in _changed_files(git)
        if path.startswith("changelog.d/") and not path.endswith("README.md")
    )


def check_changelog_buffer(git: GitRunner = _git, *, changelog: str | None = None) -> Check:
    """Запись о изменении обязательна в каждом PR, и CI её не проверяет.

    Принимается любая из двух форм: файл-фрагмент в ``changelog.d/`` (основная,
    не конфликтует между PR) или строка в ``## Буфер`` — так ветки, начатые до
    перехода на фрагменты, не становятся красными на ровном месте.

    Требование мягкое ровно в одном случае: когда ветка не трогает ничего,
    кроме самого ``CHANGELOG.md`` — тогда проверять нечего.
    """
    changed = _changed_files(git)
    meaningful = {path for path in changed if path != "CHANGELOG.md"}
    if not meaningful:
        return Check(name="запись в CHANGELOG", ok=True, detail="менять нечего")

    fragments = added_changelog_fragments(git)
    if fragments:
        return Check(
            name="запись в CHANGELOG",
            ok=True,
            detail=f"фрагмент(ы): {', '.join(path.split('/')[-1] for path in fragments)}",
        )

    diff = git("diff", f"{_BASE}...HEAD", "--", "CHANGELOG.md")
    diff += "\n" + git("diff", "--", "CHANGELOG.md")
    diff += "\n" + git("diff", "--cached", "--", "CHANGELOG.md")
    added = added_changelog_lines(diff)
    text = changelog if changelog is not None else _read(_ROOT / "CHANGELOG.md")
    buffer = {line.strip() for line in buffer_section(text) if line.strip()}
    landed = [line for line in added if line.strip() in buffer]
    return Check(
        name="запись в CHANGELOG",
        ok=bool(landed),
        detail=f"{len(landed)} запись(ей) в буфере"
        if landed
        else "ни фрагмента, ни строки в буфере",
        hint="файл changelog.d/<slug>.<секция>.md — одна строка текста записи (#PR)",
    )


def changed_public_names(diff: str) -> set[str]:
    """Имена функций и классов, затронутые диффом (обе стороны ``+``/``-``)."""
    names: set[str] = set()
    pattern = re.compile(r"^[+-]\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        match = pattern.match(line)
        if match:
            names.add(match.group(1))
    return names


def check_tests_mentioning_changed_names(git: GitRunner = _git) -> Check:
    """Показать тесты, которые называют изменённые имена, — они и ломаются молча.

    Не блокирует: это карта, а не вердикт. Смысл — не искать тестовый файл по
    угаданному имени (``test_test_loader.py``, которого нет), а увидеть, кто
    вообще ссылается на правку.
    """
    title = "кто ссылается на правку"
    diff = git("diff", f"{_BASE}...HEAD") + "\n" + git("diff")
    names = changed_public_names(diff)
    if not names:
        return Check(name=title, ok=True, detail="публичных имён не тронуто", blocking=False)
    hits: dict[str, list[str]] = {}
    for path in sorted((_ROOT / "tests").glob("test_*.py")):
        text = _read(path)
        for name in sorted(names):
            if re.search(rf"\b{re.escape(name)}\b", text):
                hits.setdefault(path.name, []).append(name)
    detail = ", ".join(f"{file} ({', '.join(found)})" for file, found in sorted(hits.items()))
    return Check(name=title, ok=True, detail=detail or "прямых упоминаний нет", blocking=False)


def _git_dir(root: pathlib.Path, *, shared: bool = False) -> pathlib.Path:
    """Служебный каталог git рабочего дерева ``root`` (PR #PRNUM).

    Путь спрашивается у самого git, а не собирается как ``root / ".git"``: в
    рабочем дереве ``git worktree`` по этому имени лежит ФАЙЛ со строкой
    ``gitdir: ...``, а настоящий каталог — в основном репозитории.

    Args:
        root: Корень рабочего дерева.
        shared: Общий каталог репозитория (``--git-common-dir``) вместо
            собственного каталога этого дерева (``--git-dir``). У обычного
            клона это одно и то же место; расходятся они только в worktree.

    Returns:
        Каталог служебных файлов; ``root / ".git"`` — если ``git`` недоступен
        или ``root`` не репозиторий (прежнее поведение, а не отказ: гейт не
        должен становиться заложником ``git``).
    """
    flag = "--git-common-dir" if shared else "--git-dir"
    resolved = _git("-C", str(root), "rev-parse", flag)
    if not resolved:
        return root / ".git"
    found = pathlib.Path(resolved)
    return found if found.is_absolute() else root / found


def stamp_path(root: pathlib.Path) -> pathlib.Path:
    """Файл штампа прогона — у КАЖДОГО рабочего дерева свой.

    Штамп описывает содержимое конкретного дерева, поэтому общий файл означал
    бы, что прогон в одном окне обесценивает штамп другого и pre-push отклоняет
    пуш уже проверенного состояния.
    """
    return _git_dir(root) / _STAMP_NAME


def lock_path(root: pathlib.Path) -> pathlib.Path:
    """Файл блокировки прогона — ОДИН на репозиторий, включая все worktree.

    Блокировка защищает не дерево, а дескрипторы машины: два параллельных
    ``pytest`` роняют тесты с subprocess пачкой независимо от того, из основного
    каталога запущен второй прогон или из соседнего рабочего дерева.
    """
    return _git_dir(root, shared=True) / _LOCK_NAME


def logs_dir(root: pathlib.Path) -> pathlib.Path:
    """Каталог полных логов прогона — свой у каждого рабочего дерева."""
    return _git_dir(root) / _LOGS_NAME


def lock_is_active(path: pathlib.Path, *, now: float | None = None) -> bool:
    """Активна ли чужая блокировка прогона (протухшая — не помеха)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        started = float(json.loads(raw).get("at", 0))
    except (ValueError, AttributeError, TypeError):
        return False
    moment = time.time() if now is None else now
    return moment - started < _LOCK_TTL_SECONDS


def _acquire_lock(path: pathlib.Path, *, force: bool = False) -> bool:
    """Занять файл блокировки; ``False`` — прогон уже идёт."""
    if lock_is_active(path) and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "at": time.time()}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return True


def worktree_fingerprint(root: pathlib.Path, git: GitRunner = _git) -> str:
    """Отпечаток СОДЕРЖИМОГО рабочего дерева: отслеживаемое плюс новые файлы.

    Штамп привязан к содержимому, а не к ``HEAD``, потому что прогон всегда
    идёт ДО коммита: привяжи его к SHA — и коммит тут же обесценит только что
    сделанную проверку, а хук отклонит пуш ровно того состояния, которое
    проверял. Гейт, который мешает при правильном порядке действий, обходят.

    Новые, ещё не добавленные в индекс файлы учитываются наравне с
    отслеживаемыми: чаще всего правка приезжает именно так — новым тестом рядом
    с новым модулем.
    """
    listed = git("ls-files").splitlines()
    listed += git("ls-files", "--others", "--exclude-standard").splitlines()
    digest = hashlib.sha256()
    for name in sorted(set(filter(None, listed))):
        digest.update(name.encode("utf-8"))
        try:
            digest.update(hashlib.sha256((root / name).read_bytes()).digest())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


def write_stamp(
    root: pathlib.Path, sha: str, *, tests: bool, fingerprint: str = ""
) -> pathlib.Path:
    """Записать штамп удачного прогона (``sha`` — коммит на момент прогона)."""
    path = stamp_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sha": sha,
        "at": time.time(),
        "tests": tests,
        "fingerprint": fingerprint or worktree_fingerprint(root),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def read_stamp(root: pathlib.Path) -> dict[str, object]:
    """Прочитать штамп; пустой словарь, если его нет или он битый."""
    try:
        loaded = json.loads(stamp_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def stamp_is_current(root: pathlib.Path, git: GitRunner = _git) -> bool:
    """Проверено ли ровно ЭТО содержимое рабочего дерева.

    Коммит между прогоном и пушем штамп не обесценивает — содержимое то же.
    Любая правка файла после прогона обесценивает: проверяли не это.
    """
    stamp = read_stamp(root)
    recorded = stamp.get("fingerprint")
    return bool(recorded) and recorded == worktree_fingerprint(root, git)


def _read(path: pathlib.Path) -> str:
    """Текст файла; пустая строка, если прочитать не удалось."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _changed_files(git: GitRunner = _git) -> set[str]:
    """Файлы ветки против базы плюс рабочее дерево, индекс и НОВЫЕ файлы.

    Неотслеживаемые считаются наравне: правка часто состоит ровно из новых
    файлов (модуль с тестом, фрагмент changelog), и до ``git add`` они в
    ``git diff`` не видны — проверка «запись о изменении есть» на этом молча
    ошибалась.
    """
    files: set[str] = set()
    for args in (
        ("diff", "--name-only", f"{_BASE}...HEAD"),
        ("diff", "--name-only"),
        ("diff", "--name-only", "--cached"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        files |= {line for line in git(*args).splitlines() if line}
    return files


def _run_stage(title: str, command: Sequence[str], log_dir: pathlib.Path) -> Check:
    """Прогнать команду, положив вывод ЦЕЛИКОМ в файл, на экран — хвост."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{title.replace(' ', '-')}.log"
    print(f"  … {title}: {' '.join(command)}")
    try:
        completed = subprocess.run(
            list(command),
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return Check(name=title, ok=False, detail=f"не удалось запустить: {exc}")
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    tail = [line for line in (completed.stdout + completed.stderr).splitlines() if line.strip()]
    summary = tail[-1] if tail else "пустой вывод"
    return Check(
        name=title,
        ok=completed.returncode == 0,
        detail=f"{summary}  → полный лог: {log}",
        hint=f"разбирать по файлу целиком: {log}",
    )


def _print_report(checks: Sequence[Check]) -> bool:
    """Напечатать отчёт; вернуть ``True``, если блокирующих провалов нет."""
    print("\nПредпушевые проверки\n" + "─" * 60)
    for check in checks:
        mark = "OK " if check.ok else ("ПРОВАЛ" if check.blocking else "ЗАМЕТКА")
        print(f"  [{mark:^6}] {check.name}: {check.detail}")
        if not check.ok and check.hint:
            print(f"            → {check.hint}")
    failed = [check for check in checks if not check.ok and check.blocking]
    print("─" * 60)
    if failed:
        print(f"Не пройдено: {len(failed)}. Пуш будет отклонён pre-push хуком.\n")
        return False
    print("Всё чисто — можно коммитить и пушить.\n")
    return True


def _install_hook() -> int:
    """Поставить ``pre-push`` хук, отклоняющий пуш непроверенного коммита."""
    hooks_dir = pathlib.Path(_git("rev-parse", "--git-path", "hooks") or ".git/hooks")
    if not hooks_dir.is_absolute():
        hooks_dir = _ROOT / hooks_dir
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-push"
        hook.write_text(_HOOK_BODY, encoding="utf-8")
        hook.chmod(0o755)
    except OSError as exc:
        print(f"Не удалось поставить хук: {exc}", file=sys.stderr)
        return 1
    print(f"pre-push хук поставлен: {hook}")
    print("Пуш непроверенного коммита будет отклонён; аварийный выход — PREFLIGHT_SKIP=1.")
    return 0


def _gate_push() -> int:
    """Быстрая проверка перед пушем: штамп относится к текущему ``HEAD``.

    Ничего не гоняет — прогон уже был. Проверяется ровно то, что забывается:
    что проверенное состояние и есть то, которое уезжает на origin.
    """
    if os.environ.get(_SKIP_ENV):
        print(f"{_SKIP_ENV}=1 — проверка пропущена осознанно.", file=sys.stderr)
        return 0
    if stamp_is_current(_ROOT):
        return 0
    stamp = read_stamp(_ROOT)
    known = "штампа нет" if not stamp else "содержимое изменилось после прогона"
    print(
        f"Пуш отклонён: {known}.\n"
        "Запустите: python scripts/preflight.py\n"
        f"Аварийный выход, если прогон негде сделать: {_SKIP_ENV}=1 git push ...",
        file=sys.stderr,
    )
    return 1


def _force_utf8_stdio() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли (issue #1108).

    Отчёт гейта — русский, в рамке из ``─`` и со стрелками ``→``; в консоли
    cp1251 таких символов нет, и ``print`` падал ``UnicodeEncodeError`` вместе
    со всей проверкой. Гейт при этом сообщал СВОЮ ошибку вместо результата —
    ровно то, ради чего его запускают, узнать было нельзя, включая ``--help``.

    Собственная копия приёма, а не общий ``stepik_grader.stdio_encoding``:
    скрипт принципиально не импортирует пакет — он работает и там, где пакет не
    установлен. No-op на потоках без ``reconfigure`` (перехваченных pytest).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Прогнать гигиену ветки и (по умолчанию) весь набор проверок CI."""
    # Раньше любой печати, включая справку argparse: описание флагов русское.
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--branch-only", action="store_true", help="только гигиена ветки, без прогонов"
    )
    parser.add_argument(
        "--no-tests", action="store_true", help="без pytest (правки только в докáх)"
    )
    parser.add_argument("--force-lock", action="store_true", help="перехватить блокировку прогона")
    parser.add_argument("--install-hook", action="store_true", help="поставить pre-push хук")
    parser.add_argument("--gate-push", action="store_true", help="режим хука: проверить штамп")
    args = parser.parse_args(argv)

    if args.install_hook:
        return _install_hook()
    if args.gate_push:
        return _gate_push()

    subprocess.run(["git", "fetch", "origin", "main"], cwd=_ROOT, capture_output=True, check=False)

    checks: list[Check] = [
        check_branch_not_main(),
        check_branch_fresh(),
        check_branch_not_taken(),
        check_changelog_buffer(),
        check_tests_mentioning_changed_names(),
    ]

    if args.branch_only:
        return 0 if _print_report(checks) else 1

    lock = lock_path(_ROOT)
    if not _acquire_lock(lock, force=args.force_lock):
        print(
            f"Прогон уже идёт (блокировка {lock}). Два параллельных pytest исчерпывают\n"
            "дескрипторы, и тесты с subprocess падают пачкой — это среда, а не регрессия.\n"
            "Дождитесь первого прогона или перехватите: --force-lock",
            file=sys.stderr,
        )
        return 1

    log_dir = logs_dir(_ROOT)
    try:
        checks.append(
            _run_stage("ruff check", [sys.executable, "-m", "ruff", "check", "."], log_dir)
        )
        checks.append(
            _run_stage(
                "ruff format", [sys.executable, "-m", "ruff", "format", "--check", "."], log_dir
            )
        )
        checks.append(
            _run_stage(
                "mypy", [sys.executable, "-m", "mypy", "src/stepik_grader", "scripts"], log_dir
            )
        )
        if not args.no_tests:
            checks.append(
                _run_stage(
                    "pytest (весь набор)",
                    [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
                    log_dir,
                )
            )
    finally:
        lock.unlink(missing_ok=True)

    ok = _print_report(checks)
    if ok:
        head = _git("rev-parse", "HEAD")
        stamp = write_stamp(_ROOT, head, tests=not args.no_tests)
        print(f"Штамп проверенного коммита: {stamp} ({head[:8]})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
