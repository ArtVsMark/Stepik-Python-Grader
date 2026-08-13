#!/usr/bin/env python3
"""scripts/check_workflow_guardrails.py — инварианты CI и релизного конвейера.

Зачем отдельная проверка. Ошибка в workflow не видна ни линтеру, ни тестам: она
проявляется один раз, в момент релиза, и стоит дорого — версия в PyPI
неперезаписываема, а страницу релиза видят раньше, чем кто-то заметит, что
ассетов нет. Ровно так и случилось (issue #988): в job'е публикации
``actions/checkout`` стоял ПОСЛЕ ``download-artifact`` и очищал рабочую
директорию вместе со скачанным ``dist/``.

Проверяются факты, которые ломаются молча:

* порядок ``checkout`` → ``download-artifact`` в job'е публикации релиза;
* перед публикацией есть шаг, отвергающий пустой ``dist/``;
* собранные артефакты проходят ``twine check``;
* у workflow объявлены ``permissions`` (иначе токен получает права по умолчанию);
* ``ci.yml`` слушает ``ready_for_review`` — без него PR, созданный черновиком,
  не получает проверок ни при создании, ни при снятии черновика.

Разбор текстовый, без PyYAML: тянуть зависимость ради нескольких фактов незачем,
а формат этих файлов свой и стабильный. Проверка идёт по имени job'а, поэтому
переименование job'а не проходит молча — guard падает с явной ошибкой, а не
зеленеет на пустом входе.

Запуск::

    python scripts/check_workflow_guardrails.py
"""

from __future__ import annotations

import pathlib
import re
import sys

__all__ = [
    "GITHUB_RELEASE_JOB",
    "VERIFY_JOB",
    "check_ci_listens_to_ready_for_review",
    "check_release_gates_match_promises",
    "check_release_pipeline",
    "extract_job",
    "main",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"

GITHUB_RELEASE_JOB = "github-release"
VERIFY_JOB = "verify"

# Job внутри `jobs:` — строка с двумя пробелами отступа и двоеточием на конце.
_JOB_HEADER_RE = re.compile(r"^  (?P<name>[a-zA-Z0-9_-]+):\s*$")


def extract_job(source: str, job_name: str) -> list[str]:
    """Строки одного job'а из workflow (без его заголовка).

    Пустой список означает «job'а с таким именем нет» — вызывающая сторона
    обязана считать это ошибкой, а не поводом пропустить проверку: guard,
    молчащий на пропавшем входе, зеленеет ровно тогда, когда всё сломано.
    """
    collected: list[str] = []
    inside = False
    for line in source.splitlines():
        header = _JOB_HEADER_RE.match(line)
        if header:
            if inside:
                break
            inside = header.group("name") == job_name
            continue
        if inside:
            collected.append(line)
    return collected


def _first_index(lines: list[str], needle: str) -> int | None:
    """Индекс первой строки, содержащей ``needle``; ``None``, если такой нет."""
    for index, line in enumerate(lines):
        if needle in line:
            return index
    return None


def check_release_pipeline(errors: list[str], source: str | None = None) -> None:
    """Релиз не должен публиковаться пустым (issue #988)."""
    if source is None:
        if not _RELEASE.is_file():
            errors.append("release.yml: файла нет — релизный конвейер не проверен")
            return
        source = _RELEASE.read_text(encoding="utf-8")

    job = extract_job(source, GITHUB_RELEASE_JOB)
    if not job:
        errors.append(
            f"release.yml: job '{GITHUB_RELEASE_JOB}' не найден. Если его переименовали — "
            "обновите GITHUB_RELEASE_JOB в этом скрипте, иначе проверка порядка шагов "
            "молча перестанет что-либо проверять."
        )
        return

    checkout_at = _first_index(job, "actions/checkout")
    download_at = _first_index(job, "actions/download-artifact")

    if checkout_at is None:
        errors.append(f"release.yml / {GITHUB_RELEASE_JOB}: нет шага actions/checkout")
    if download_at is None:
        errors.append(f"release.yml / {GITHUB_RELEASE_JOB}: нет шага actions/download-artifact")
    if checkout_at is not None and download_at is not None and checkout_at > download_at:
        errors.append(
            f"release.yml / {GITHUB_RELEASE_JOB}: actions/checkout стоит ПОСЛЕ "
            "download-artifact и очистит рабочую директорию вместе со скачанным dist/ — "
            "релиз опубликуется без ассетов. Переставьте checkout первым шагом."
        )

    if "ls -A dist" not in "\n".join(job):
        errors.append(
            f"release.yml / {GITHUB_RELEASE_JOB}: нет проверки, что dist/ непуст перед "
            "публикацией. Пустой релиз хуже несостоявшегося: тег уже проставлен."
        )

    if "twine check" not in source:
        errors.append(
            "release.yml: собранные артефакты не проходят twine check. Версия в PyPI "
            "неперезаписываема — то, что отвергнется на загрузке, надо ловить до неё."
        )

    if not re.search(r"^permissions:\s*$", source, re.MULTILINE):
        errors.append(
            "release.yml: нет блока permissions на верхнем уровне — job'ы получают "
            "токен с правами по умолчанию."
        )

    print(f"release pipeline: job '{GITHUB_RELEASE_JOB}' проверен ({len(job)} строк).")


def check_release_gates_match_promises(errors: list[str], source: str | None = None) -> None:
    """Обещанное документацией обязано стоять гейтом ДО публикации (issue #988).

    ``docs/dev/versioning.md`` обещает две вещи: «без ротации CHANGELOG релиз
    падает» и «забытая запись под тег роняет релиз». Обе были неправдой. Guard
    документации в релизе не запускался вовсе, а извлечение release notes жило в
    job'е публикации GitHub Release — и PyPI, независимый от него по построению,
    публиковался при любом состоянии CHANGELOG. Проверка стоит именно в
    ``verify``: от него зависят оба публикующих job'а, поэтому обещание держится
    для обоих путей сразу.
    """
    if source is None:
        if not _RELEASE.is_file():
            errors.append("release.yml: файла нет — релизные гейты не проверены")
            return
        source = _RELEASE.read_text(encoding="utf-8")

    verify = extract_job(source, VERIFY_JOB)
    if not verify:
        errors.append(
            f"release.yml: job '{VERIFY_JOB}' не найден. Если его переименовали — обновите "
            "VERIFY_JOB в этом скрипте, иначе релизные гейты перестанут проверяться молча."
        )
        return

    verify_text = "\n".join(verify)
    if "check_docs_guardrails.py" not in verify_text:
        errors.append(
            f"release.yml / {VERIFY_JOB}: нет запуска check_docs_guardrails.py. "
            "docs/dev/versioning.md обещает, что без ротации CHANGELOG релиз падает — "
            "обещание обязано быть гейтом."
        )
    if "extract_release_notes.py" not in verify_text:
        errors.append(
            f"release.yml / {VERIFY_JOB}: нет проверки release notes для тега. "
            "docs/dev/versioning.md обещает, что забытая запись под тег роняет релиз, а "
            "проверка в job'е публикации GitHub Release не мешает PyPI опубликоваться."
        )

    print(f"release gates: job '{VERIFY_JOB}' держит обещания документации.")


def check_ci_listens_to_ready_for_review(errors: list[str], source: str | None = None) -> None:
    """CI обязан просыпаться при снятии черновика с PR (issue #988)."""
    if source is None:
        if not _CI.is_file():
            errors.append("ci.yml: файла нет — триггеры CI не проверены")
            return
        source = _CI.read_text(encoding="utf-8")

    if "pull_request:" not in source:
        errors.append("ci.yml: нет триггера pull_request — проверки на PR не запускаются вовсе")
        return

    if "ready_for_review" not in source:
        errors.append(
            "ci.yml: в types триггера pull_request нет ready_for_review. Дефолт GitHub — "
            "opened/synchronize/reopened, поэтому PR, созданный черновиком, остаётся "
            "без проверок и после снятия черновика."
        )
        return

    print("ci.yml: pull_request слушает ready_for_review.")


def main() -> int:
    """Вернуть 0, если инварианты workflow держатся; 1 — если нарушены."""
    errors: list[str] = []

    check_release_pipeline(errors)
    check_release_gates_match_promises(errors)
    check_ci_listens_to_ready_for_review(errors)

    if errors:
        print("\nFAIL: workflow guardrails violated:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("OK: релизный конвейер и триггеры CI держат заявленное.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
