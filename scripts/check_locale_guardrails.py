#!/usr/bin/env python3
"""scripts/check_locale_guardrails.py — CI-guard локализации веб-API (issue #264).

Две машинные защиты, чтобы каталог сообщений веб-слоя (``message_id`` →
локализованный текст, см. ``web/i18n.py``) не разъезжался с фактическим
использованием и между локалями:

1. **Полнота ``ru.json``.** Каждый ``message_id``, на который в ``web/*.py``
   ссылается вызов ``render_message(...)``/``message_fields(...)``, должен
   существовать как ключ в ``core/locales/ru.json`` — иначе ``render_message()``
   тихо откатится на сам ``message_id`` вместо текста (graceful degradation
   ценой немой опечатки, которую эта проверка ловит).
2. **Синхронность ``ru.json``/``en.json``.** Оба файла должны иметь ровно
   одинаковый набор ключей — иначе ``?lang=en`` частично покажет русский
   текст (fallback в ``render_message()``) там, где перевод забыли добавить.

По образцу ``scripts/check_docs_guardrails.py`` (issue #173): чистый
``ast``/``json``/``pathlib``, без внешних зависимостей — быстро и
детерминированно (Windows/Linux/macOS).

Запуск::

    python scripts/check_locale_guardrails.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

__all__ = [
    "check_en_ru_key_parity",
    "check_ru_covers_referenced_ids",
    "collect_referenced_message_ids",
    "load_locale_keys",
    "main",
    "source_files",
]

_ROOT = Path(__file__).resolve().parent.parent
_PKG_DIR = _ROOT / "src" / "stepik_grader"
_LOCALES_DIR = _PKG_DIR / "core" / "locales"

# Функции каталога сообщений (``web/i18n.py``), чей первый позиционный
# аргумент — строковый литерал ``message_id`` (issue #264).
_CATALOG_CALL_NAMES = frozenset({"render_message", "message_fields"})


def _call_name(node: ast.Call) -> str | None:
    """Имя вызываемой функции для простых форм ``f(...)`` и ``mod.f(...)``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def collect_referenced_message_ids(path: Path) -> set[str]:
    """``message_id``-литералы из вызовов ``render_message``/``message_fields`` в файле.

    Обнаруживает только литеральные (``ast.Constant`` строка) первые
    аргументы — динамически построенные ``message_id`` (сегодня в кодовой
    базе таких нет) проверкой не покрываются.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    ids: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node) not in _CATALOG_CALL_NAMES:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            ids.add(first.value)
    return ids


def load_locale_keys(lang: str) -> set[str]:
    """Ключи ``core/locales/<lang>.json`` (пустой набор, если файл битый/не-объект)."""
    path = _LOCALES_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, dict):
        return set()
    return set(data.keys())


def source_files() -> list[Path]:
    """Все ``.py`` пакета — рекурсивно, а не только верхний уровень ``web/``.

    issue #787 (CIG-02): прежний ``_WEB_DIR.glob("*.py")`` не спускался в
    подпакеты, поэтому переезд ``web/`` в подкаталог обнулил бы проверку молча.
    Каталог сообщений может использоваться из любой части пакета, так что
    правильная область — весь ``src/stepik_grader``.
    """
    return sorted(_PKG_DIR.rglob("*.py"))


def check_ru_covers_referenced_ids(errors: list[str]) -> None:
    """Каждый используемый ``message_id`` есть в ``ru.json``."""
    referenced: set[str] = set()
    files = source_files()
    for f in files:
        referenced |= collect_referenced_message_ids(f)

    # issue #787: ноль входов — ошибка, а не успех. Guard, которому нечего
    # проверять, обязан падать: пустой результат неотличим от «всё чисто».
    if not files:
        errors.append(f"в {_PKG_DIR} не найдено ни одного .py — проверять нечего")
        return
    if not referenced:
        errors.append(
            f"ни одного вызова {'/'.join(sorted(_CATALOG_CALL_NAMES))} не найдено в "
            f"{len(files)} файле(ах) — каталог сообщений либо переехал, либо проверка ослепла"
        )
        return

    ru_keys = load_locale_keys("ru")
    missing = sorted(referenced - ru_keys)
    if missing:
        errors.append(
            "core/locales/ru.json missing message_id(s) referenced in the package: "
            + ", ".join(missing)
        )
    else:
        print(
            f"ru.json coverage: {len(referenced)} referenced message_id(s) across "
            f"{len(files)} package .py file(s), all present in ru.json."
        )


def check_en_ru_key_parity(errors: list[str]) -> None:
    """``ru.json`` и ``en.json`` содержат ровно один и тот же набор ключей."""
    ru_keys = load_locale_keys("ru")
    en_keys = load_locale_keys("en")
    only_in_ru = sorted(ru_keys - en_keys)
    only_in_en = sorted(en_keys - ru_keys)
    if only_in_ru or only_in_en:
        if only_in_ru:
            errors.append("en.json missing key(s) present in ru.json: " + ", ".join(only_in_ru))
        if only_in_en:
            errors.append("ru.json missing key(s) present in en.json: " + ", ".join(only_in_en))
    else:
        print(f"Locale key parity: ru.json and en.json both have {len(ru_keys)} key(s).")


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_ru_covers_referenced_ids(errors)
    check_en_ru_key_parity(errors)

    if errors:
        print("\nFAIL: locale guardrails violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK: ru.json covers all referenced message_id(s); ru.json/en.json keys match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
