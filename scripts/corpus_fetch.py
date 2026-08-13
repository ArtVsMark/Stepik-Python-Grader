#!/usr/bin/env python3
"""scripts/corpus_fetch.py — собрать локальную базу задач курса для сквозного прогона.

Шаг подготовки перед `corpus_sweep.py`: обходит курс Stepik (курс → секции →
юниты → уроки → шаги), отбирает шаги-задачи и скачивает каждый штатным
загрузчиком (`downloader.process_step_url`) — с условием, тестами и, если он
есть, кодом решения, который пользователь когда-то отправил на платформу.

**Только чтение.** Скрипт не отправляет решений и ничего не меняет на Stepik:
единственные операции — GET к API и запись файлов на диск. Батч-отправка
решений под живым аккаунтом — накрутка прогресса, стендом она не делается ни в
каком режиме.

**Куда качать.** По умолчанию `corpus/local/` — папка в `.gitignore`: условия и
тесты чужого курса не должны попасть в публичный репозиторий. Менять цель на
что-то внутри git-дерева — осознанное решение вызывающего.

Запуск::

    python scripts/corpus_fetch.py --course 63085 --dry-run   # только список шагов
    python scripts/corpus_fetch.py --course 63085             # скачать в corpus/local
    python scripts/corpus_fetch.py --course 63085 --limit 20
    python scripts/corpus_fetch.py --steps steps.txt          # по готовому списку URL

Требует авторизации: `secrets.json` рядом (см. `docs/use/installation.md`).
Полная инструкция локального прогона — [`docs/agent/local-sweep.md`](../docs/agent/local-sweep.md).
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import requests

from stepik_grader.core.oauth_flow import create_user_session, load_secrets_dict
from stepik_grader.core.stepik_client import (
    fetch_course_data,
    fetch_lesson_data,
    fetch_section_data,
    fetch_section_units,
    fetch_step_data,
)

__all__ = [
    "DEFAULT_TARGET",
    "CourseStep",
    "fetch_steps",
    "iter_course_steps",
    "main",
    "read_step_urls",
]

# Цель по умолчанию — вне git: чужие условия и тесты в репозиторий не едут.
DEFAULT_TARGET: pathlib.Path = pathlib.Path("corpus") / "local"

# Тип блока Stepik у шага-задачи с кодом. Прочие шаги (текст, видео, тест с
# выбором ответа) грейдеру не подходят: у них нет ни тестов, ни решения.
_CODE_BLOCK_NAME = "code"


@dataclass(frozen=True)
class CourseStep:
    """Шаг курса, пригодный для локальной проверки."""

    lesson_id: int
    position: int
    step_id: int
    title: str
    section_id: int
    section_title: str
    section_position: int

    @property
    def url(self) -> str:
        """Канонический URL шага, который понимает загрузчик."""
        return f"https://stepik.org/lesson/{self.lesson_id}/step/{self.position}"


def iter_course_steps(
    session: requests.Session, course_id: int, *, code_only: bool = True
) -> Iterator[CourseStep]:
    """Обойти курс и выдать его шаги по порядку.

    Порядок обхода — курс → секции → юниты → уроки → шаги; он же порядок
    прохождения курса, поэтому база на диске повторяет структуру программы.

    Args:
        session: авторизованная сессия Stepik.
        course_id: идентификатор курса.
        code_only: отдавать только шаги-задачи с кодом (иначе — все шаги).

    Yields:
        Шаги курса в порядке программы.
    """
    course = fetch_course_data(session, course_id)
    for section_id in course.get("sections") or []:
        section = fetch_section_data(session, int(section_id))
        section_title = str(section.get("title") or f"section {section_id}")
        section_position = int(section.get("position") or 0)
        for unit in fetch_section_units(session, int(section["id"])):
            lesson_id = unit.get("lesson")
            if lesson_id is None:
                continue
            lesson = fetch_lesson_data(session, int(lesson_id))
            title = str(lesson.get("title") or f"lesson {lesson_id}")
            for position, step_id in enumerate(lesson.get("steps") or [], start=1):
                if code_only and not _is_code_step(
                    session, int(lesson_id), position
                ):
                    continue
                yield CourseStep(
                    lesson_id=int(lesson_id),
                    position=position,
                    step_id=int(step_id),
                    title=title,
                    section_id=int(section["id"]),
                    section_title=section_title,
                    section_position=section_position,
                )


def _is_code_step(session: requests.Session, lesson_id: int, position: int) -> bool:
    """Проверить, что шаг — задача с кодом; сетевой сбой трактуется как «нет».

    Шаг адресуется парой «урок + позиция», а не собственным ``step_id``: именно
    так его ищет ``fetch_step_data``, и той же парой он адресуется в URL.
    """
    try:
        step = fetch_step_data(session, lesson_id, position)
    except (requests.RequestException, ValueError, KeyError):
        return False
    block: dict[str, Any] = step.get("block") or {}
    return str(block.get("name") or "") == _CODE_BLOCK_NAME


def read_step_urls(list_path: pathlib.Path) -> list[str]:
    """Прочитать список URL шагов из файла (по одному в строке).

    Пустые строки и строки, начинающиеся с ``#``, игнорируются — файл можно
    комментировать и держать в нём выключенные шаги.
    """
    lines = list_path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def fetch_steps(
    session: requests.Session,
    step_urls: Sequence[str],
    target: pathlib.Path,
) -> tuple[int, list[str]]:
    """Скачать шаги в целевую директорию, не прерываясь на сбоях.

    Один недоступный шаг не должен ронять сбор базы из сотен задач: ошибка
    запоминается и попадает в итог.

    Args:
        session: авторизованная сессия Stepik.
        step_urls: URL шагов.
        target: корень локальной базы.

    Returns:
        Пара «сколько шагов скачано, список сообщений об ошибках».
    """
    from stepik_grader import downloader

    target.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    errors: list[str] = []

    for number, url in enumerate(step_urls, start=1):
        print(f"[{number}/{len(step_urls)}] {url}", file=sys.stderr)
        try:
            downloader.process_step_url(url, session, target)
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        downloaded += 1

    return downloaded, errors


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: собрать локальную базу задач курса."""
    parser = argparse.ArgumentParser(
        description="Скачать задачи курса Stepik в локальную базу для сквозного прогона."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--course", type=int, help="идентификатор курса Stepik")
    source.add_argument(
        "--steps", type=pathlib.Path, help="файл со списком URL шагов (по одному в строке)"
    )
    parser.add_argument(
        "--target",
        type=pathlib.Path,
        default=DEFAULT_TARGET,
        help=f"куда складывать базу (по умолчанию {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--secrets",
        type=pathlib.Path,
        default=pathlib.Path("secrets.json"),
        help="путь к secrets.json с токеном Stepik",
    )
    parser.add_argument("--limit", type=int, default=0, help="взять не больше N шагов (0 — все)")
    parser.add_argument(
        "--all-steps",
        action="store_true",
        help="не фильтровать по типу шага (по умолчанию только задачи с кодом)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="только показать список шагов, ничего не скачивать"
    )
    args = parser.parse_args(argv)

    try:
        secrets = load_secrets_dict(args.secrets)
        session = create_user_session(secrets, args.secrets)
    except (OSError, ValueError) as exc:
        print(f"Не удалось авторизоваться: {exc}", file=sys.stderr)
        return 1

    if args.steps is not None:
        step_urls = read_step_urls(args.steps)
    else:
        try:
            steps = list(iter_course_steps(session, args.course, code_only=not args.all_steps))
        except (requests.RequestException, ValueError, KeyError) as exc:
            print(f"Обход курса не удался: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        step_urls = [step.url for step in steps]
        for step in steps:
            print(f"{step.url}  # {step.title}")

    if args.limit > 0:
        step_urls = step_urls[: args.limit]

    if not step_urls:
        print("Нечего скачивать: список шагов пуст.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"\nНайдено шагов: {len(step_urls)} (--dry-run, ничего не скачано)", file=sys.stderr)
        return 0

    downloaded, errors = fetch_steps(session, step_urls, args.target)
    print(f"\nСкачано шагов: {downloaded} из {len(step_urls)} → {args.target}", file=sys.stderr)
    if errors:
        print(f"Ошибок: {len(errors)}", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
