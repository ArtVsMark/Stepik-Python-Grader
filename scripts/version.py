#!/usr/bin/env python3
"""scripts/version.py — вычисляет версию проекта по схеме MAJOR.MINOR.PATCH.

Схема проекта (см. CONTRIBUTING.md §Версионирование, issue #68) — НЕ SemVer:

  * MAJOR.MINOR берутся из последнего git-тега вида ``vX.Y.0``;
  * PATCH = число ПРИНЯТЫХ изменений после этого тега. Изменение опознаётся по
    НОМЕРУ PR (``(#NNNN)`` в теме коммита, issue #1042), а не по положению в
    графе истории: номера уникализируются множеством, поэтому счётчик не зависит
    ни от формы истории, ни от того, как автор дробил PR на коммиты. Коммит без
    номера (прямой пуш в main) считается отдельно — но только на first-parent
    линии, чтобы внутренние коммиты слитой ветки не завышали счёт, — и без
    badge-коммитов CI (``chore(ci): update badges [skip ci]``, issue #231) и
    склеивающих мержей ``git pull`` (см. ``_is_sync_merge``).

Почему не топология (issue #1042). Прежняя формула считала коммиты на
first-parent линии — то есть меряла ФОРМУ истории, а форма зависит от окна: в
свежем клоне она линейная (squash-мержи), а на машине с ``git pull`` merge'ом
всё пришедшее с GitHub уходит во ВТОРОЙ родитель и на first-parent линию не
попадает. Проверено моделью: при двух принятых PR и одном локальном коммите
``--first-parent`` печатал 2 вместо 3 (оба фикса с GitHub терялись, а +1 давала
склейка ``Merge branch 'main' of ...``), а ``--no-merges`` на мерже ветки из
трёх коммитов давал 6 вместо 4. Ни одна топологическая формула не давала обе
цифры разом — поэтому считаются сущности, а не рёбра графа.

До первого тега ``git describe`` завершается ошибкой — тогда MAJOR.MINOR
читаются из версии установленного пакета (``importlib.metadata`` поверх
setuptools-scm; статической ``[project].version`` в pyproject нет —
``dynamic = ["version"]``, issue #162/#183), а PATCH = число first-parent
изменений в истории по той же логике исключения (монотонный счётчик).

Запуск::

    python scripts/version.py     # → напр. 1.2.17
"""

from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

__all__ = ["project_version"]

# Имя дистрибутива (pyproject [project].name) — для чтения версии установленного
# пакета из метаданных (setuptools-scm; статической версии нет, issue #162/#183).
_DIST_NAME = "stepik-python-grader"

# issue #231: подстрока commit-сообщения badge-бота (см. модульный докстринг).
_BOT_COMMIT_GREP = "chore(ci): update badges"

# issue #1042: номер PR в теме коммита. Две формы — squash-мерж GitHub дописывает
# `(#NNNN)` в конец темы, merge-мерж даёт `Merge pull request #NNNN from ...`.
# Обе ведут к одному PR, поэтому попадают в одно множество номеров.
# issue #1065: релизный тег — строго `vX.Y.Z`. Служебные теги вроде
# `v-checkpoint-2026-06-24` подходят под `v*` и, оказавшись ближе релизного,
# роняли разбор версии.
_RELEASE_TAG_GLOB = "v[0-9]*.[0-9]*.[0-9]*"
_RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

_PR_NUMBER_RE = re.compile(r"\(#(\d+)\)")
_MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+)\b")

# issue #1042: склеивающий мерж `git pull` — тот, что сводит локальную копию
# ветки с удалённой. Своего изменения не несёт, поэтому в счёт не идёт. Две
# формы темы, которые пишет сам git: `Merge branch 'main' of <url>` (pull по
# URL/remote) и `Merge remote-tracking branch 'origin/main'` (мерж отслеживаемой
# ветки). Мерж ветки-фичи (`Merge branch 'feat'`) под шаблон не подпадает и
# считается за одно изменение: в нём и есть принятая работа.
_SYNC_MERGE_RE = re.compile(
    r"^Merge (?:remote-tracking )?branch '[^']+' of |^Merge remote-tracking branch '"
)


def _git(*args: str) -> str | None:
    """Вернуть stdout git-команды без хвостового перевода строки.

    None при любой ошибке (git недоступен, не git-репозиторий, нет тегов) —
    вызывающая сторона трактует None как «данных нет» и уходит в fallback.

    Кодировка задана явно (issue #1042). ``text=True`` без неё берёт локальную
    кодовую страницу, а под Windows это cp1252 — темы коммитов проекта
    по-русски (см. CLAUDE.md § Язык артефактов), и чтение падало
    ``UnicodeDecodeError`` прямо на первом же заголовке. Раньше не всплывало,
    потому что читались только цифры ``rev-list --count``; с переходом на темы
    коммитов кодировка стала значимой. ``errors="replace"`` — потому что
    отдельный коммит с иной кодировкой не должен ронять подсчёт версии: битый
    символ в теме максимум мешает распознать номер PR.
    """
    try:
        out = subprocess.check_output(
            ["git", *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.strip()


def _major_minor_from_metadata() -> tuple[str, str]:
    """Вернуть (MAJOR, MINOR) из версии установленного пакета (setuptools-scm).

    Проект НЕ хранит статическую версию (``dynamic = ["version"]``, setuptools-scm,
    issue #162/#183) — читаем её из метаданных установленного дистрибутива
    (``importlib.metadata``), формат ``X.Y.0.postN+g<hash>`` → берём X.Y. Прежняя
    версия читала удалённый ``[project].version`` и всегда деградировала в ``0.0``.

    ("0", "0") — только последний резерв (пакет не установлен / версия не
    парсится); в норме этот fallback срабатывает лишь до первого git-тега, когда
    пакет уже установлен для сборки бейджа.
    """
    try:
        raw = _dist_version(_DIST_NAME)
    except PackageNotFoundError:
        return "0", "0"
    parts = raw.split(".")
    major = parts[0] if parts and parts[0].isdigit() else "0"
    minor = parts[1] if len(parts) > 1 and parts[1].isdigit() else "0"
    return major, minor


def _subjects(rev_range: str, *, first_parent: bool = False) -> list[str]:
    """Темы коммитов диапазона (``%s``), по желанию — только first-parent линия."""
    args = ["log", "--pretty=%s"]
    if first_parent:
        args.append("--first-parent")
    out = _git(*args, rev_range)
    if not out:
        return []
    return [line for line in out.split("\n") if line]


def _pr_numbers(subjects: list[str]) -> set[str]:
    """Номера PR, упомянутые в темах: ``(#NNNN)`` и ``Merge pull request #NNNN``."""
    numbers: set[str] = set()
    for subject in subjects:
        numbers.update(_PR_NUMBER_RE.findall(subject))
        merge_pr = _MERGE_PR_RE.match(subject)
        if merge_pr:
            numbers.add(merge_pr.group(1))
    return numbers


def _is_countable_unnumbered(subject: str) -> bool:
    """Считать ли коммит без номера PR за отдельное принятое изменение (#1042).

    Не считаются badge-коммиты бота (issue #231) и склеивающие мержи ``git pull``:
    первые не изменение, вторые — не своё изменение, а сведение двух копий одной
    ветки. Всё остальное без номера — прямой коммит в main, и он реален.
    """
    return _BOT_COMMIT_GREP not in subject and not _SYNC_MERGE_RE.match(subject)


def _commits_since(rev_range: str) -> str:
    """Число «принятых изменений» в rev_range (issue #1042).

    Считаются СУЩНОСТИ, а не рёбра графа: множество номеров PR по всей истории
    диапазона плюс коммиты без номера с first-parent линии. Почему не топология
    — в модульном докстринге.

    Номера собираются по всей истории (без ``--first-parent``), потому что при
    ``git pull`` merge'ом пришедшее с GitHub лежит во втором родителе; множество
    гасит и двойной учёт, если одно изменение попало в историю дважды — своим
    локальным коммитом и squash-версией с GitHub.

    Коммиты БЕЗ номера берутся только с first-parent линии: иначе внутренние
    коммиты слитой ветки (у них номера нет) считались бы поштучно, и дробление
    PR снова завышало бы счётчик — ровно то, от чего защищал прежний
    ``--first-parent``.
    """
    numbered = _pr_numbers(_subjects(rev_range))
    unnumbered = [
        subject
        for subject in _subjects(rev_range, first_parent=True)
        if not _PR_NUMBER_RE.search(subject)
        and not _MERGE_PR_RE.match(subject)
        and _is_countable_unnumbered(subject)
    ]
    return str(len(numbered) + len(unnumbered))


def _latest_release_tag() -> str | None:
    """Ближайший РЕЛИЗНЫЙ тег (``vX.Y.Z``) или ``None``, если такого нет.

    issue #1065: рядом с релизными живут служебные теги (``v-checkpoint-…``), и
    они подходят под наивную маску ``v*``. ``git describe --tags`` без
    ограничения выбирал такой тег наравне с релизным, а разбор
    ``tag.lstrip("v").split(".")`` падал ``ValueError`` — вместе со счётчиком
    версии ложилось и обновление бейджей в CI. Форма проверяется дважды: маской
    в git (чтобы describe сразу искал нужный тег) и регуляркой здесь (glob не
    отличает ``v1.10.0`` от ``v1.10.0-rc``).
    """
    tag = _git("describe", "--tags", "--abbrev=0", "--match", _RELEASE_TAG_GLOB)
    if tag is None or not _RELEASE_TAG_RE.match(tag):
        return None
    return tag


def project_version() -> str:
    """Вернуть версию вида '1.2.17' по схеме проекта (см. модульный докстринг)."""
    tag = _latest_release_tag()
    if tag is not None:
        major, minor, _patch = tag.lstrip("v").split(".")
        commits = _commits_since(f"{tag}..HEAD")
        return f"{major}.{minor}.{commits}"

    # Fallback до первого тега: MAJOR.MINOR из метаданных пакета, PATCH = все коммиты.
    major, minor = _major_minor_from_metadata()
    if (major, minor) == ("0", "0"):
        # issue #1042: тегов нет И метаданные их тоже не видели — версия
        # недостоверна. Раньше отсюда молча выходило правдоподобное `0.0.51`
        # вместо `1.10.N`: клон без тегов (так клонирует облачная сессия, так же
        # ведёт себя `actions/checkout` без `fetch-depth: 0`) неотличим от
        # репозитория до первого релиза. Печатаем в stderr, чтобы stdout остался
        # чистой версией для бейджа и вызывающих скриптов.
        print(
            "warning: git-тегов не видно (клон без тегов?) — MAJOR.MINOR неизвестны, "
            "версия неполна; подтяните теги: git fetch --tags",
            file=sys.stderr,
        )
    commits = _commits_since("HEAD")
    return f"{major}.{minor}.{commits}"


if __name__ == "__main__":
    print(project_version())
