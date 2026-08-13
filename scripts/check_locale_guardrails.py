#!/usr/bin/env python3
"""scripts/check_locale_guardrails.py — CI-guard локализации веб-API (issue #264).

Пять машинных защит, чтобы каталог сообщений веб-слоя (``message_id`` →
локализованный текст, см. ``web/i18n.py``) не разъезжался с фактическим
использованием, между локалями и с тем, что пользователь реально видит:

1. **Полнота ``ru.json``.** Каждый ``message_id``, на который ссылается вызов
   каталога — веб (``render_message``/``message_fields``), CLI (``ctx.t``) или
   загрузчик (``_t``), — должен существовать как ключ в
   ``core/locales/ru.json``. Иначе фолбэк тихо покажет сам ``message_id``
   вместо текста: graceful degradation ценой немой опечатки, которую эта
   проверка и ловит.
2. **Синхронность ``ru.json``/``en.json``.** Оба файла должны иметь ровно
   одинаковый набор ключей — иначе ``?lang=en`` частично покажет русский
   текст (fallback в ``render_message()``) там, где перевод забыли добавить.
3. **Чистота ``en.json``.** В английской локали не должно быть кириллицы
   (issue #821): совпадение ключей ещё не значит, что перевод сделан —
   строка баннера сервера годами называла раздел «Подучить» по-русски внутри
   английского файла, где четыре соседних ключа звали его «Learn».
4. **Ссылки ведут наружу.** Ни одна пользовательская строка не зовёт читать
   путь внутри репозитория (issue #1005): при установке через ``pip``/``pipx``
   ни ``docs/``, ни ``README`` на диске нет — подсказка ведёт в никуда.
5. **Один стиль кавычек в ``en.json``.** Русские «ёлочки» соседствовали с
   английскими “лапками”, причём один и тот же термин был закавычен обоими
   способами; проверка на кириллицу этого не видит — кавычки не буквы.

По образцу ``scripts/check_docs_guardrails.py`` (issue #173): чистый
``ast``/``json``/``pathlib``, без внешних зависимостей — быстро и
детерминированно (Windows/Linux/macOS).

Запуск::

    python scripts/check_locale_guardrails.py     # exit 0 — ок, 1 — нарушение
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

__all__ = [
    "check_en_locale_has_no_cyrillic",
    "check_en_locale_uses_one_quote_style",
    "check_en_ru_key_parity",
    "check_locales_link_outside_the_repo",
    "check_ru_covers_referenced_ids",
    "collect_referenced_message_ids",
    "load_locale_keys",
    "load_locale_values",
    "main",
    "source_files",
]

_ROOT = Path(__file__).resolve().parent.parent
_PKG_DIR = _ROOT / "src" / "stepik_grader"
_LOCALES_DIR = _PKG_DIR / "core" / "locales"

# Функции каталога сообщений, чей первый позиционный аргумент — строковый
# литерал ``message_id``: веб (``web/i18n.py``, issue #264), CLI (``ctx.t(...)``
# из ``cli/context.py``) и загрузчик с диагностикой (``_t(...)``).
#
# issue #960 (ED-2-03): сначала здесь стояли только два веб-имени, а основные
# потребители каталога зовутся иначе — 83 из 85 call-site'ов проверке были не
# видны. Гейт заведён ровно против того, чтобы ключ разъехался с локалью, и
# при этом оставался зелёным на переименовании ключа в CLI: фолбэк
# ``messages.get(key, key)`` показывает студенту служебный идентификатор
# вместо текста ошибки.
_CATALOG_CALL_NAMES = frozenset({"render_message", "message_fields", "t", "_t"})


def _call_name(node: ast.Call) -> str | None:
    """Имя вызываемой функции для простых форм ``f(...)`` и ``mod.f(...)``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def collect_referenced_message_ids(path: Path) -> set[str]:
    """``message_id``-литералы из вызовов каталога сообщений в файле.

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


def load_locale_values(lang: str) -> dict[str, str]:
    """``ключ → текст`` из ``core/locales/<lang>.json`` (пусто, если файл битый)."""
    path = _LOCALES_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


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


def _has_cyrillic(text: str) -> bool:
    """Содержит ли строка кириллицу (блок U+0400–U+04FF)."""
    return any("Ѐ" <= ch <= "ӿ" for ch in text)


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


def check_en_locale_has_no_cyrillic(errors: list[str]) -> None:
    """В английской локали нет кириллицы (issue #821).

    Прецедент `DESC-03`: строка баннера сервера в `en.json` называла раздел
    «Подучить» по-русски, тогда как четыре соседних ключа той же локали
    называли его «Learn». Расхождение внутри одного файла — ровно то, что
    человек не замечает при вычитке, а машина ловит одной проверкой.
    """
    values = load_locale_values("en")
    bad = sorted(key for key, text in values.items() if _has_cyrillic(text))
    if bad:
        errors.append("core/locales/en.json contains Cyrillic text in key(s): " + ", ".join(bad))
    else:
        print(f"en.json: no Cyrillic text across {len(values)} key(s).")


def check_locales_link_outside_the_repo(errors: list[str]) -> None:
    """Пользовательские строки не ссылаются на пути внутри репозитория (issue #1005).

    Находки INS-5-02, LINK-1-05, READER-1-04: сообщения звали читать
    ``docs/use/installation.md``, ``SECURITY.md``, ``README`` — файлы, которых
    у поставившего через ``pip``/``pipx`` на диске нет вовсе. Подсказка,
    ведущая в никуда, хуже её отсутствия: пользователь ищет несуществующее.

    Правильный адрес — абсолютный URL (база объявлена в ``pyproject.toml``
    полем ``Documentation``). Имена файлов, которые продукт **создаёт** сам
    (``grader-progress.md``, ``.grader_history.db``), под запрет не подпадают:
    они относятся к рабочей папке пользователя, а не к репозиторию.
    """
    marker = re.compile(
        r"(?<!/)\b(?:docs/[\w./-]+|README(?:\.md)?|SECURITY\.md|CONTRIBUTING\.md|CHANGELOG\.md)\b"
    )
    offenders: list[str] = []
    for lang in ("ru", "en"):
        for key, text in sorted(load_locale_values(lang).items()):
            # Внутри абсолютного URL тот же путь легален — он и есть решение.
            without_urls = re.sub(r"https?://\S+", "", text)
            for hit in marker.findall(without_urls):
                offenders.append(f"{lang}.json:{key} → {hit}")
    if offenders:
        errors.append(
            "пользовательские строки ссылаются на пути внутри репозитория "
            "(при pip/pipx этих файлов нет — нужен абсолютный URL): " + ", ".join(offenders)
        )
    else:
        print("Locale links: no repo-relative paths in ru.json/en.json user strings.")


def check_en_locale_uses_one_quote_style(errors: list[str]) -> None:
    """Английская локаль закавычивает термины одним способом (issue #1005, ED-2-06).

    В ``en.json`` соседствовали русские «ёлочки» и английские “лапки”, причём
    один и тот же термин Learn был закавычен обоими способами. Проверка на
    кириллицу такое не ловит: сами кавычки буквами не являются.
    """
    guillemets = ("«", "»", "„", "‟")
    bad = sorted(
        key for key, text in load_locale_values("en").items() if any(q in text for q in guillemets)
    )
    if bad:
        errors.append(
            "core/locales/en.json uses Russian guillemets («») instead of English quotes (“”) "
            "in key(s): " + ", ".join(bad)
        )
    else:
        print("en.json: consistent English quotation marks.")


def main() -> int:
    """Вернуть 0, если нарушений нет; 1 — если найдены."""
    errors: list[str] = []
    check_ru_covers_referenced_ids(errors)
    check_en_ru_key_parity(errors)
    check_en_locale_has_no_cyrillic(errors)
    check_locales_link_outside_the_repo(errors)
    check_en_locale_uses_one_quote_style(errors)

    if errors:
        print("\nFAIL: locale guardrails violated:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("OK: ru.json covers all referenced message_id(s); ru.json/en.json keys match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
