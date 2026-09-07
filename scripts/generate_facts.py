"""scripts/generate_facts.py — факты проекта машиночитаемо, для соседей.

Витрине нужны наши числа: сколько тестов, сколько тест-модулей, сколько
проверок создаётся на pull request, какие версии Python в матрице. Сегодня она
берёт их **у себя**: клонирует репозиторий целиком ради двух ``rglob`` по
``tests/``, разбирает наш ``ci.yml`` регулярным выражением и оценивает число
проверок медианой по семи последним PR.

Цена такого способа — не расход, а связанность. Знание о том, где лежат наши
тесты и как устроена наша матрица, живёт в ЧУЖОМ репозитории: переносим
каталог — у соседа молча меняется число, а не ломается сборка. Медиана же
существует ровно потому, что снаружи точного ответа не видно, — тогда как
внутри он есть: тот же набор проверок уже считает ``check_pr_ready.py`` для
собственного мерж-гейта.

Отсюда приём, который в этой экосистеме уже работает у каталога правил:
**издатель считает, потребитель читает**. Файл кладётся в ветку ``badges``
рядом с бейджами — тем же прогоном и тем же способом, каким витрина их уже
получает (contents-API, без клона и без знания нашего дерева).

ПОЧЕМУ НЕ В ``main``. Числа пересобираются на каждом пуше, то есть чаще, чем
идут изменения. Производное с такой частотой в общей ветке не хранят: оно
превращает каждое слияние в конфликт и красит ветку сдвигом числа, а не
поломкой (правило 160). В ``main`` файла нет — он в ``.gitignore``.

ЧЕГО КЛЮЧА НЕТ — ТОГО НЕ ИЗМЕРЯЛИ. ``checks_per_pr`` требует обращения к
площадке, и при отказе ключ ОТСУТСТВУЕТ, а не выставляется нулём: ноль читался
бы как «проверок нет». Тот же приём, что у ``portable`` в контракте каталога.

Запуск::

    python scripts/generate_facts.py --out .github/badges/facts.json
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _datetime
import json
import pathlib
import re
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = [
    "REPO",
    "SCHEMA",
    "build_facts",
    "count_test_functions",
    "count_test_modules",
    "main",
    "python_versions",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Версия ФОРМАТА ЭТОГО ФАЙЛА — не версия проекта и не версия его выпуска.
#: Номера разного назначения, названные одним словом, разъезжаются по чужим
#: полям: сосед по каталогу правил уже записал версию формата выгрузки в поле
#: версии ответа потребителя, и обе стороны остались формально валидными.
SCHEMA = "1.0"

REPO = "ArtVsMark/Stepik-Python-Grader"

_TEST_FUNCTION_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)
_MATRIX_VERSION_RE = re.compile(r"^\s*python-version:\s*\[([^\]]+)\]", re.MULTILINE)
_MATRIX_OS_RE = re.compile(r"^\s*os:\s*\[([^\]]+)\]", re.MULTILINE)
_EXPERIMENTAL_RE = re.compile(r'python-version:\s*"([^"]+)",\s*experimental:\s*true')


def count_test_functions(root: pathlib.Path) -> int:
    """Число тест-функций во всём дереве ``tests/``."""
    total = 0
    for path in sorted((root / "tests").rglob("*.py")):
        total += len(_TEST_FUNCTION_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    return total


def count_test_modules(root: pathlib.Path) -> int:
    """Число тест-модулей.

    Считаются файлы ``test_*.py``: ``conftest`` и хелперы тестами не являются.
    """
    return len(list((root / "tests").rglob("test_*.py")))


def _matrix_list(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Список из матрицы: ``["a", "b"]`` → ``["a", "b"]``."""
    matched = pattern.search(text)
    if not matched:
        return []
    return [item.strip().strip("\"'") for item in matched.group(1).split(",") if item.strip()]


def python_versions(root: pathlib.Path) -> dict[str, list[str]]:
    """Версии Python и операционные системы из матрицы.

    Экспериментальных версий нет в правилах ветки по устройству — они идут под
    ``continue-on-error`` и мерж не блокируют, поэтому единственный источник —
    сама матрица.

    ``os`` добавлен по просьбе витрины (issue #1448) и по той же причине, что и
    остальные ключи: число операционных систем она добывала **регулярным
    выражением по именам наших джобов**, то есть держала знание о нашем формате
    имён в своём коде. Переименуй мы комбинацию — у соседа молча изменилось бы
    число, и не упало бы ничего.

    Из ``checks_per_pr.names`` вывести нельзя: там ВСЕ проверки на изменение, а
    операционные системы показываются по обязательным — разные множества, и
    ответ вышел бы на другой вопрос.
    """
    text = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return {
        "supported": _matrix_list(text, _MATRIX_VERSION_RE),
        "experimental": sorted(set(_EXPERIMENTAL_RE.findall(text))),
        "os": _matrix_list(text, _MATRIX_OS_RE),
    }


def _head_commit(root: pathlib.Path) -> str:
    """SHA состояния, по которому посчитаны числа; пусто — git недоступен."""
    done = subprocess.run(  # argv собран здесь, оболочка не участвует
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _checks_per_pr(root: pathlib.Path) -> dict[str, object] | None:
    """Эталонный набор проверок PR: сколько их и как называются.

    Берётся оттуда же, откуда его берёт собственный мерж-гейт
    (``check_pr_ready.py``), — из живого состояния ``main``, а не из константы.
    Спросить не удалось — ``None``: ключа в файле не будет вовсе.
    """
    sys.path.insert(0, str(root / "scripts"))
    try:
        import check_pr_ready
        import gh_rest
    except ImportError as exc:
        print(f"::warning::эталонный набор не прочитан: {exc}", file=sys.stderr)
        return None

    try:
        names = check_pr_ready._expected_names(
            gh_rest._get,
            REPO,
            root / ".github" / "workflows",
        )
    except gh_rest.RateLimited as exc:
        # Отказ ИСТОЧНИКА отличается от чистого результата: квота кончилась —
        # это не «проверок на PR не создаётся». Ключ пропадает, остальные факты
        # едут как обычно.
        print(f"::warning::квота исчерпана, checks_per_pr не измерен: {exc}", file=sys.stderr)
        return None
    except gh_rest.GitHubError as exc:
        print(f"::warning::площадка отказала, checks_per_pr не измерен: {exc}", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"::warning::сеть недоступна, checks_per_pr не измерен: {exc}", file=sys.stderr)
        return None
    if not names:
        return None
    return {"count": len(names), "names": sorted(names)}


def build_facts(root: pathlib.Path | None = None) -> dict[str, object]:
    """Собрать факты проекта — то, что соседям иначе пришлось бы считать у себя."""
    base = root if root is not None else _ROOT
    facts: dict[str, object] = {
        "schema": SCHEMA,
        "schema_of": (
            "формат ЭТОГО файла — факты проекта для соседних (generate_facts.py). "
            "Не версия проекта, не его выпуск и не чужая схема: в этой экосистеме "
            "их уже четыре (выгрузка правил, ответ потребителя, сводка, факты), "
            "ключ у всех один, предметы разные — правило 164"
        ),
        "_": (
            "Факты этого проекта для соседних. schema — версия ФОРМАТА файла, "
            "не версия проекта и не его выпуск. Ключа нет — значит не измеряли: "
            "нулём отсутствие не обозначается."
        ),
        "repo": REPO,
        "generated_at": _datetime.datetime.now(tz=_datetime.UTC).isoformat(timespec="seconds"),
        "tests": {
            "functions": count_test_functions(base),
            "modules": count_test_modules(base),
        },
        "python": python_versions(base),
    }
    commit = _head_commit(base)
    if commit:
        facts["commit"] = commit
    checks = _checks_per_pr(base)
    if checks is not None:
        facts["checks_per_pr"] = checks
    return facts


def main(argv: list[str] | None = None) -> int:
    """Записать файл фактов; 0 — записан."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=_ROOT / ".github" / "badges" / "facts.json",
        help="куда положить файл",
    )
    parser.add_argument("--root", type=pathlib.Path, default=_ROOT, help="корень проекта")
    args = parser.parse_args(argv)

    facts = build_facts(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"факты собраны: тестов {facts['tests']['functions']}, "  # type: ignore[index]
        f"модулей {facts['tests']['modules']}, "  # type: ignore[index]
        f"файл {args.out}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
