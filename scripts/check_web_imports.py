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
экспортируемых соседним модулем, и сверяем с фактическим списком импорта.
Обращения без вызова (константы вроде `SECTIONS`, `state`) сюда не попадают —
их пропуск дал бы ту же ошибку, но регулярка на «идентификатор где угодно»
шумит на совпадениях в строках и комментариях, а вызов — надёжный признак.

**Стережём ВСЕ рёбра импорта, а не одно** (issue #921, находки AUD-2-03 и
AUD-2-04). Прежняя редакция знала только про `core.js` и обходила лишь верхний
уровень `static/`:

- девять остальных рёбер (`content.js`, `grade.js`, `sandbox.js`,
  `trace-player.js`, …) не проверялись вовсе, хотя ломаются они одинаково;
- `glob("*.js")` вместо `rglob` означал, что переезд модуля в подкаталог
  ослепляет гейт молча — он продолжает печатать «no missing imports».

Сломанный guard по определению не виден: он проходит. Поэтому имя, определённое
в самом модуле, из проверки вычитается — иначе обобщение начало бы краснеть на
локальном `render()`, который у соседа тоже экспортируется, и гейт, кричащий
без причины, обошли бы.

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
    "exported_names",
    "imported_names",
    "local_definitions",
    "main",
    "missing_imports",
    "module_files",
]

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "src" / "stepik_grader" / "web" / "static"
_CORE = _STATIC / "core.js"

# Блок `export { a, b, c };` в конце модуля.
_EXPORT_BLOCK = re.compile(r"export\s*\{([^}]+)\}")
# `import { a, b } from "./core.js"` — модуль может импортировать несколькими.
_CORE_IMPORT = re.compile(r"import\s*\{([^}]+)\}\s*from\s*[\"']\./core\.js[\"']")
# То же для ЛЮБОГО локального модуля: группа 1 — имена, группа 2 — путь.
_LOCAL_IMPORT = re.compile(r"import\s*\{([^}]+)\}\s*from\s*[\"'](\.[^\"']+\.js)[\"']")
# Объявления верхнего уровня: имя, которое модуль определяет САМ.
_DEFINITION = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s*\*?|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


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


def exported_names(path: Path) -> set[str]:
    """Имена, экспортируемые модулем (все блоки `export { … }`)."""
    source = path.read_text(encoding="utf-8")
    names: set[str] = set()
    for match in _EXPORT_BLOCK.finditer(source):
        names |= _names(match.group(1))
    return names


def local_definitions(source: str) -> set[str]:
    """Имена, объявленные в самом модуле на верхнем уровне.

    Вычитаются из проверки: локальный `render()` не обязан импортироваться
    только потому, что сосед экспортирует такое же имя. Без этого обобщение
    гейта на все рёбра начало бы краснеть без причины.
    """
    return set(_DEFINITION.findall(source))


def module_files() -> list[Path]:
    """JS-модули статики, кроме самого `core.js`.

    `rglob`, а не `glob`: переезд модуля в подкаталог иначе ослепляет гейт —
    он продолжает печатать «no missing imports», проверив пустоту (issue #921,
    находка AUD-2-03).
    """
    return [p for p in sorted(_STATIC.rglob("*.js")) if p.name != _CORE.name]


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
    """Каждый вызываемый экспорт `core.js` импортирован в своём модуле.

    Отдельная функция ради ошибки «в статике нет ни одного модуля»: у `core.js`
    ребро особое — его импортируют все, и пустой список здесь означает, что
    гейт проверяет пустоту.
    """
    exports = core_exports()
    if not exports:
        errors.append(f"не разобран блок export в {_CORE} — проверять нечего")
        return

    modules = module_files()
    if not modules:
        errors.append(f"в {_STATIC} нет ни одного .js кроме core.js — проверять нечего")
        return

    for path in modules:
        source = path.read_text(encoding="utf-8")
        for name in missing_imports(source, exports - local_definitions(source)):
            errors.append(
                f"{path.name}: вызывает {name}() из core.js, но не импортирует его "
                "(ReferenceError в рантайме, а не при загрузке)"
            )


def check_all_edges(errors: list[str]) -> None:
    """То же для ОСТАЛЬНЫХ рёбер: модуль ↔ модуль, не только через `core.js`.

    Ломаются они одинаково — `ReferenceError` в момент рендера, — а
    проверялось прежде одно ребро из десяти (issue #921, находка AUD-2-04).
    Ребро на `core.js` здесь пропускается: его стережёт функция выше, и
    дублировать сообщение незачем.
    """
    modules = module_files()
    exports_by_name = {path.name: exported_names(path) for path in modules}
    exports_by_name[_CORE.name] = core_exports()

    checked = 0
    for path in modules:
        source = path.read_text(encoding="utf-8")
        local = local_definitions(source)
        for match in _LOCAL_IMPORT.finditer(source):
            target = Path(match.group(2)).name
            if target == _CORE.name or target not in exports_by_name:
                continue
            checked += 1
            available = exports_by_name[target] - local
            imported = _names(match.group(1))
            for name in sorted(available - imported):
                if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", source):
                    errors.append(
                        f"{path.name}: вызывает {name}() из {target}, но не импортирует его "
                        "(ReferenceError в рантайме, а не при загрузке)"
                    )
    if not errors:
        print(
            f"web imports: {len(modules)} module(s), {checked} module-to-module edge(s) "
            f"plus core.js checked, no missing imports."
        )


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_core_imports(errors)
    check_all_edges(errors)

    if errors:
        print("\nFAIL: web module imports violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
