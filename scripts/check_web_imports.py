#!/usr/bin/env python3
"""scripts/check_web_imports.py — CI-guard импортов web-модулей (issue #855).

Модули `web/static/*.js` — ES-модули без сборщика: имя, не попавшее в
`import { … } from "./core.js"`, не всплывает ни при загрузке страницы, ни в
линтере — только `ReferenceError` в рантайме, ровно в момент рендера.

Прецедент: `kpiGrid` вызывался в `content.js`, но в импорт включён не был.
Раздел «Прогресс» переставал рисоваться целиком, как только в истории
появлялся хотя бы один прогон, — а выглядело это как пустой экран, потому что
пустое состояние к тому моменту уже снято, а исключение из `async`-функции
уходит в `unhandledrejection`. На свежей установке (пустая история) раздел
работал, поэтому дефект жил незамеченным.

Проверка статическая и грубая намеренно: ищем вызовы `name(` для имён,
экспортируемых `core.js`, и сверяем с фактическим списком импорта. Обращения
без вызова (константы вроде `SECTIONS`, `state`) сюда не попадают — их
пропуск дал бы ту же ошибку, но регулярка на «идентификатор где угодно» шумит
на совпадениях в строках и комментариях, а вызов — надёжный признак.

По образцу `check_locale_guardrails.py`: чистый stdlib, без сборщика и без
Node — детерминированно и кроссплатформенно.

Запуск::

    python scripts/check_web_imports.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

__all__ = [
    "check_core_imports",
    "core_exports",
    "imported_names",
    "main",
    "missing_imports",
    "module_files",
]

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "src" / "stepik_grader" / "web" / "static"
_CORE = _STATIC / "core.js"

# Блок `export { a, b, c };` в конце core.js.
_EXPORT_BLOCK = re.compile(r"export\s*\{([^}]+)\}")
# `import { a, b } from "./core.js"` — модуль может импортировать несколькими.
_CORE_IMPORT = re.compile(r"import\s*\{([^}]+)\}\s*from\s*[\"']\./core\.js[\"']")


def _names(block: str) -> set[str]:
    """Имена из фигурных скобок импорта/экспорта (`a as b` → берём локальное `b`)."""
    names: set[str] = set()
    for raw in block.split(","):
        part = raw.strip()
        if not part:
            continue
        names.add(part.split(" as ")[-1].strip() if " as " in part else part)
    return names


def core_exports() -> set[str]:
    """Имена, экспортируемые `core.js`."""
    match = _EXPORT_BLOCK.search(_CORE.read_text(encoding="utf-8"))
    return _names(match.group(1)) if match else set()


def imported_names(source: str) -> set[str]:
    """Имена, импортированные модулем из `core.js`."""
    names: set[str] = set()
    for match in _CORE_IMPORT.finditer(source):
        names |= _names(match.group(1))
    return names


def module_files() -> list[Path]:
    """JS-модули статики, кроме самого `core.js`."""
    return [p for p in sorted(_STATIC.glob("*.js")) if p.name != _CORE.name]


def missing_imports(source: str, exports: set[str]) -> list[str]:
    """Экспорты `core.js`, которые модуль ВЫЗЫВАЕТ, не импортировав."""
    imported = imported_names(source)
    missing = []
    for name in sorted(exports - imported):
        # `(?<![\w.])` — не свойство объекта (`obj.kpiGrid(...)`) и не часть
        # длинного идентификатора; `\s*\(` — именно вызов.
        if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", source):
            missing.append(name)
    return missing


def check_core_imports(errors: list[str]) -> None:
    """Каждый вызываемый экспорт `core.js` импортирован в своём модуле."""
    exports = core_exports()
    if not exports:
        errors.append(f"не разобран блок export в {_CORE} — проверять нечего")
        return

    modules = module_files()
    if not modules:
        errors.append(f"в {_STATIC} нет ни одного .js кроме core.js — проверять нечего")
        return

    total_missing = 0
    for path in modules:
        for name in missing_imports(path.read_text(encoding="utf-8"), exports):
            total_missing += 1
            errors.append(
                f"{path.name}: вызывает {name}() из core.js, но не импортирует его "
                "(ReferenceError в рантайме, а не при загрузке)"
            )
    if not total_missing:
        print(
            f"web imports: {len(modules)} module(s) checked against "
            f"{len(exports)} core.js export(s), no missing imports."
        )


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_core_imports(errors)

    if errors:
        print("\nFAIL: web module imports violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
