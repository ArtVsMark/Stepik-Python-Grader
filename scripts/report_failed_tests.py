"""report_failed_tests.py — упавшие тесты названы там, где их видно (issue #1382).

Артефакты и логи Actions читаются только со скоупом ``actions``, которого у
облачной сессии нет: прокси отвечает 403. Поэтому junit-отчёт (#1378) отвечает
на вопрос «какой тест упал» только владельцу, у которого есть веб-интерфейс, а
тот, кто чинит из облака, видит ровно одну строку — ``Process completed with
exit code 1``. Цена этой слепоты измерена: причину падения трёх macOS-джобов
пришлось искать тремя полными прогонами набора локально, подбирая условия.

Открытый канал ровно один — **комментарий PR** (REST issues). Скрипт разбирает
junit-отчёты, собранные джобами матрицы, и кладёт в PR короткую сводку: какой
джоб, какой тест, первая строка ошибки.

Три решения, которые здесь важнее кода:

1. **Комментарий один и обновляется**, а не добавляется каждым прогоном. Ищется
   он по скрытому маркеру :data:`MARKER` — тот же приём, что у
   ``scripts/rules_inbox.py``: номер пришлось бы где-то хранить, а хранимое
   состояние разъезжается.
2. **Объём ограничен** (:data:`_DEFAULT_LIMIT`). Сводка нужна, чтобы понять,
   куда смотреть, а не чтобы заменить отчёт: остальные упавшие названы числом.
   Молчаливой обрезки нет — она читалась бы как «это всё».
3. **Пишет только с** ``--apply``. Без него печатает то, что отправил бы.

Скрипт не решает, красный прогон или зелёный: его зовут из шага, который и так
запускается только при падении.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import gh_rest

__all__ = [
    "MARKER",
    "Failure",
    "collect",
    "main",
    "parse_report",
    "render",
]

#: Скрытый маркер, по которому комментарий находится в следующий раз.
MARKER = "<!-- ci-failures -->"

#: Сколько упавших тестов называется поимённо. Остальные — числом.
_DEFAULT_LIMIT = 25

#: Имя файла отчёта: `test-results-<os>-<python>.xml` (см. `ci.yml`).
_REPORT_GLOB = "test-results-*.xml"
_REPORT_PREFIX = "test-results-"


@dataclass(frozen=True)
class Failure:
    """Один упавший тест: где, что и с чего началось.

    Attributes:
        job: комбинация матрицы, из имени файла отчёта.
        test: путь до теста в форме, пригодной для `pytest ...`.
        kind: `failure` (упал) или `error` (сломался на фикстуре/сборе).
        message: первая содержательная строка сообщения.
    """

    job: str
    test: str
    kind: str
    message: str


def _job_name(path: pathlib.Path) -> str:
    """`test-results-ubuntu-latest-3.12.xml` → `ubuntu-latest-3.12`."""
    name = path.stem
    return name[len(_REPORT_PREFIX) :] if name.startswith(_REPORT_PREFIX) else name


def _first_line(text: str | None) -> str:
    """Первая непустая строка сообщения — заголовок ошибки, а не весь traceback."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "без сообщения"


def _test_id(case: ET.Element) -> str:
    """`classname` + `name` → адрес, по которому тест запускается вручную."""
    classname = (case.get("classname") or "").replace(".", "/")
    name = case.get("name") or "?"
    if not classname:
        return name
    # `tests/test_x/TestClass` → `tests/test_x.py::TestClass`: последний
    # сегмент, начинающийся с заглавной, — класс, остальное путь к модулю.
    parts = classname.split("/")
    if len(parts) > 1 and parts[-1][:1].isupper():
        return f"{'/'.join(parts[:-1])}.py::{parts[-1]}::{name}"
    return f"{classname}.py::{name}"


def parse_report(path: pathlib.Path) -> list[Failure]:
    """Разобрать один junit-отчёт; нечитаемый файл — пустой список, не отказ.

    Args:
        path: файл отчёта, записанный `pytest --junitxml`.

    Returns:
        Упавшие и сломавшиеся тесты этого джоба.
    """
    job = _job_name(path)
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        # Обрезанный отчёт — не повод падать: он и пишется на аварийном пути.
        return []

    failures: list[Failure] = []
    for case in tree.getroot().iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            message = _first_line(node.get("message") or node.text)
            failures.append(Failure(job, _test_id(case), kind, message))
            break
    return failures


def collect(directory: pathlib.Path) -> list[Failure]:
    """Собрать упавшие тесты со всех отчётов каталога, по джобам и именам."""
    found: list[Failure] = []
    for report in sorted(directory.rglob(_REPORT_GLOB)):
        found.extend(parse_report(report))
    return sorted(found, key=lambda item: (item.job, item.test))


def render(
    failures: Iterable[Failure], *, run_url: str | None = None, limit: int = _DEFAULT_LIMIT
) -> str:
    """Собрать текст комментария со скрытым маркером в первой строке."""
    items = list(failures)
    lines = [MARKER, ""]
    if not items:
        lines += [
            "**Прогон красный, но ни один тест не назвал себя упавшим.**",
            "",
            "Значит джоб умер до тела тестов — установка, сборка, сам раннер — "
            "либо отчёт не успел записаться. Смотреть логи прогона.",
        ]
    else:
        jobs = sorted({item.job for item in items})
        lines += [
            f"**Упало тестов: {len(items)}** — в джобах: {', '.join(f'`{job}`' for job in jobs)}.",
            "",
        ]
        for item in items[:limit]:
            lines.append(f"- `{item.job}` — `{item.test}`  \n  {item.kind}: {item.message}")
        if len(items) > limit:
            lines += ["", f"…и ещё {len(items) - limit}. Полный список — в артефакте прогона."]
    if run_url:
        lines += ["", f"Прогон: {run_url}"]
    lines += [
        "",
        "---",
        "_Сводка обновляется на каждом красном прогоне этого PR "
        "(`scripts/report_failed_tests.py`, issue #1382)._",
    ]
    return "\n".join(lines) + "\n"


def _existing_comment(repo: str, number: int, **kwargs: object) -> int | None:
    """Номер прежней сводки в этом PR — по маркеру, а не по автору."""
    for comment in gh_rest.issue_comments(repo, number, **kwargs):
        if MARKER in (comment.get("body") or ""):
            identifier = comment.get("id")
            if isinstance(identifier, int):
                return identifier
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа: разобрать отчёты и (с ``--apply``) обновить сводку в PR."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=pathlib.Path, default=pathlib.Path("."))
    parser.add_argument("--repo", default=gh_rest.DEFAULT_REPO)
    parser.add_argument("--pr", type=int, help="номер pull request")
    parser.add_argument("--run-url", help="ссылка на прогон")
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    parser.add_argument("--apply", action="store_true", help="писать в PR, а не печатать")
    args = parser.parse_args(argv)

    body = render(collect(args.dir), run_url=args.run_url, limit=args.limit)
    if not args.apply or args.pr is None:
        print(body)
        return gh_rest.EXIT_OK

    existing = _existing_comment(args.repo, args.pr)
    if existing is None:
        gh_rest.comment_issue(args.repo, args.pr, body)
        print(f"сводка добавлена в PR #{args.pr}")
    else:
        gh_rest.update_comment(args.repo, existing, body)
        print(f"сводка обновлена в PR #{args.pr} (комментарий {existing})")
    return gh_rest.EXIT_OK


if __name__ == "__main__":  # pragma: no cover — точка входа
    raise SystemExit(main())
