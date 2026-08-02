#!/usr/bin/env python3
"""scripts/skip_inventory.py — инвентарь пропусков в наборе тестов (issue #837).

Набор из нескольких тысяч тестов — источник уверенности только до тех пор, пока
известно, сколько из них на самом деле не выполняется. Пропуски здесь законны
(три ОС-специфичных backend'а песочницы, опциональные extra, отсутствие
дисплея), но каждый обязан объяснять себя: пропуск без причины неотличим от
забытого теста, а «зелёный прогон» из пропусков — ложная уверенность, ровно та,
которую назвали оба критика полноты аудита.

Скрипт статически (``ast``, без запуска тестов) собирает все места пропуска:

* ``@pytest.mark.skip`` / ``@pytest.mark.skipif`` / ``@pytest.mark.xfail``;
* вызовы ``pytest.skip(...)`` / ``pytest.xfail(...)`` в теле теста;
* ``pytest.importorskip(...)`` — пропуск по отсутствию зависимости.

Для каждого печатается файл, строка, вид и причина; в конце — сводка по видам и
по файлам. **Пропуск без причины делает выход ненулевым** — это и есть гейт.

Что скрипт НЕ делает: не знает, сработает ли конкретный ``skipif`` на конкретном
CI-job'е — условие вычисляется в рантайме. Фактическую картину прогона даёт
``pytest -rs`` (список ``SKIPPED [n] file:line: причина``); статический инвентарь
отвечает на другой вопрос — «где вообще заложены пропуски и объяснены ли они».

Запуск::

    python scripts/skip_inventory.py            # отчёт + exit 1 при пропуске без причины
    python scripts/skip_inventory.py --summary  # только сводка
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

__all__ = ["SkipSite", "collect_skip_sites", "main", "render_report"]

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Маркеры pytest, означающие «тест может не выполниться».
_SKIP_MARKERS = frozenset({"skip", "skipif", "xfail"})
# Функции pytest с тем же эффектом при вызове в теле теста/фикстуры.
_SKIP_CALLS = frozenset({"skip", "xfail", "importorskip"})


@dataclass(frozen=True)
class SkipSite:
    """Одно место пропуска: где, какого вида и с какой причиной."""

    path: Path
    line: int
    kind: str  # "skip" | "skipif" | "xfail" | "importorskip"
    reason: str

    @property
    def has_reason(self) -> bool:
        """Причина указана и не пуста — пропуск объясняет себя сам."""
        return bool(self.reason.strip())


def _display_path(path: Path) -> str:
    """Путь для отчёта: относительный к корню репозитория, иначе как есть."""
    try:
        return path.relative_to(_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _attr_name(node: ast.expr) -> str | None:
    """Имя атрибута вызова/декоратора: ``pytest.mark.skipif`` → ``skipif``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _literal(node: ast.expr | None) -> str:
    """Текст причины: строковый литерал или f-строка.

    f-строки читаются по частям (``f"нужен {name}"`` → ``нужен {…}``): причина
    там такая же настоящая, как в обычном литерале, и считать её отсутствующей
    значит требовать переписать рабочий код ради статического анализатора.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{…}"
            for part in node.values
        ]
        return "".join(parts)
    return ""


def _reason_from_keywords(keywords: list[ast.keyword]) -> str:
    for kw in keywords:
        if kw.arg == "reason":
            return _literal(kw.value)
    return ""


def _decorator_site(node: ast.expr, path: Path) -> SkipSite | None:
    """``@pytest.mark.skip(...)``/``skipif``/``xfail`` → место пропуска."""
    call = node if isinstance(node, ast.Call) else None
    target = call.func if call is not None else node
    kind = _attr_name(target)
    if kind not in _SKIP_MARKERS:
        return None
    # Отсеиваем однофамильцев: интересует только ветка pytest.mark.*
    if not (isinstance(target, ast.Attribute) and _attr_name(target.value) == "mark"):
        return None
    reason = ""
    if call is not None:
        reason = _reason_from_keywords(call.keywords)
        if not reason and kind == "skip" and call.args:
            reason = _literal(call.args[0])
        if not reason and kind == "skipif" and len(call.args) > 1:
            reason = _literal(call.args[1])
    return SkipSite(path=path, line=node.lineno, kind=kind, reason=reason)


def _call_site(node: ast.Call, path: Path) -> SkipSite | None:
    """``pytest.skip("...")`` / ``pytest.importorskip("mod")`` → место пропуска."""
    kind = _attr_name(node.func)
    if kind not in _SKIP_CALLS:
        return None
    if not (isinstance(node.func, ast.Attribute) and _attr_name(node.func.value) == "pytest"):
        return None
    if kind == "importorskip":
        # Причина — сам импортируемый модуль: «нет такого-то пакета».
        module = _literal(node.args[0]) if node.args else ""
        reason = f"нет модуля {module}" if module else ""
    else:
        reason = _reason_from_keywords(node.keywords) or (
            _literal(node.args[0]) if node.args else ""
        )
    return SkipSite(path=path, line=node.lineno, kind=kind, reason=reason)


def collect_skip_sites(tests_dir: Path | None = None) -> list[SkipSite]:
    """Все места пропуска в наборе тестов, отсортированные по файлу и строке.

    ``tests_dir=None`` — каталог тестов репозитория. Значение по умолчанию
    вычисляется при вызове, а не при импорте: иначе подмена ``_TESTS_DIR``
    (в тестах самого скрипта) не действовала бы.
    """
    tests_dir = tests_dir if tests_dir is not None else _TESTS_DIR
    sites: list[SkipSite] = []
    for file in sorted(tests_dir.rglob("test_*.py")) + sorted(tests_dir.rglob("conftest.py")):
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                for decorator in node.decorator_list:
                    site = _decorator_site(decorator, file)
                    if site is not None:
                        sites.append(site)
            elif isinstance(node, ast.Call):
                site = _call_site(node, file)
                if site is not None:
                    sites.append(site)
    return sorted(sites, key=lambda s: (str(s.path), s.line))


def render_report(sites: list[SkipSite], *, summary_only: bool = False) -> str:
    """Человекочитаемый отчёт: перечень мест и сводка по видам/файлам."""
    lines: list[str] = []
    if not summary_only:
        for site in sites:
            reason = site.reason.strip() or "БЕЗ ПРИЧИНЫ"
            rel = _display_path(site.path)
            lines.append(f"{rel}:{site.line}  {site.kind:<14} {reason}")
        lines.append("")
    by_kind = Counter(site.kind for site in sites)
    by_file = Counter(_display_path(site.path) for site in sites)
    lines.append(f"Всего мест пропуска: {len(sites)}")
    lines.append("По видам: " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())))
    lines.append("Топ файлов:")
    lines.extend(f"  {count:>3}  {name}" for name, count in by_file.most_common(5))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Вернуть 0, если каждый пропуск объяснён; 1 — если есть безпричинные."""
    parser = argparse.ArgumentParser(
        prog="python scripts/skip_inventory.py",
        description="Инвентарь skip/skipif/xfail/importorskip в наборе тестов.",
    )
    parser.add_argument("--summary", action="store_true", help="только сводка, без перечня")
    args = parser.parse_args(argv)

    sites = collect_skip_sites()
    print(render_report(sites, summary_only=args.summary))

    unexplained = [s for s in sites if not s.has_reason]
    if unexplained:
        print("\nFAIL: пропуски без причины (issue #837):")
        for site in unexplained:
            print(f"  - {_display_path(site.path)}:{site.line} ({site.kind})")
        return 1
    print("\nOK: каждый пропуск объясняет себя причиной.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
