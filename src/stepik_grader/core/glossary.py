"""glossary.py — карта встроенных исключений Python → подсказка + ссылка на глоссарий.

Архитектурный слой: Utilities (leaf — только stdlib, не импортирует project-код).

Единый источник истины для issue #72 / эпика #96: когда решение падает с
RuntimeError'ом (вердикт RE), и CLI (`core/reporter.py`), и веб-оболочка
(`web.py`) показывают короткую подсказку по типу исключения и ссылку на
подробную карточку в отдельном проекте-глоссарии Glossary-Python.

Проектное решение (эпик #96, вариант «вендорить малый слой»): здесь живёт
ТОЛЬКО компактный курированный набор ~30 встроенных исключений с однострочными
пояснениями — не копия полного глоссария (581 карточка). Полный контент
развивается отдельным проектом; отсюда лишь ссылка на него для глубины. Так
подсказки работают офлайн (в духе stdlib/offline `web.py`), а глоссарий
остаётся независимым.

Базовый URL и схема якорей (`<base>#<slug>`, slug = имя класса в lower-case)
вынесены в константы — если якоря глоссария поменяются, правка тривиальна
(одна константа + при нужде поле ``slug`` конкретной записи).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GLOSSARY_BASE_URL",
    "GlossaryEntry",
    "lookup",
    "lookup_from_error",
]

# Базовый URL полного глоссария (GitHub Pages отдельного проекта Glossary-Python).
# Якорь карточки — "#<slug>", где slug по умолчанию = имя класса исключения в
# нижнем регистре (напр. RecursionError → #recursionerror). Проверить/поправить
# схему якорей — здесь, в одном месте (эпик #96).
GLOSSARY_BASE_URL = "https://artvsmark.github.io/Glossary-Python/"


@dataclass(frozen=True)
class GlossaryEntry:
    """Запись глоссария для одного встроенного исключения."""

    exception: str  # имя класса, напр. "RecursionError"
    hint: str  # однострочное пояснение (RU)
    slug: str = ""  # якорь карточки; пусто → exception.lower()

    @property
    def anchor(self) -> str:
        """Slug якоря (по умолчанию — имя класса в нижнем регистре)."""
        return self.slug or self.exception.lower()

    @property
    def url(self) -> str:
        """Полный URL карточки в глоссарии."""
        return f"{GLOSSARY_BASE_URL}#{self.anchor}"


def _entry(exception: str, hint: str, slug: str = "") -> GlossaryEntry:
    return GlossaryEntry(exception=exception, hint=hint, slug=slug)


# Курированный набор частых встроенных исключений (не полный глоссарий).
_ENTRIES: dict[str, GlossaryEntry] = {
    e.exception: e
    for e in (
        _entry("SyntaxError", "Синтаксическая ошибка — Python не смог разобрать код."),
        _entry("IndentationError", "Неправильные отступы (смешаны пробелы/табы или сбит уровень)."),
        _entry("TabError", "Смешаны табуляция и пробелы в отступах."),
        _entry("NameError", "Используется имя, которое не определено (опечатка? не объявлено?)."),
        _entry("UnboundLocalError", "Локальная переменная используется до присваивания."),
        _entry("TypeError", "Операция применена к неподходящему типу (напр. int + str)."),
        _entry("ValueError", "Тип верный, но значение недопустимо (напр. int('abc'))."),
        _entry("IndexError", "Индекс за пределами последовательности (список/строка короче)."),
        _entry("KeyError", "Обращение к несуществующему ключу словаря."),
        _entry("AttributeError", "У объекта нет такого атрибута/метода."),
        _entry("ZeroDivisionError", "Деление на ноль."),
        _entry("RecursionError", "Слишком глубокая рекурсия — нет условия выхода?"),
        _entry("ImportError", "Не удалось импортировать имя из модуля."),
        _entry("ModuleNotFoundError", "Модуль не найден (не установлен или опечатка в имени)."),
        _entry("FileNotFoundError", "Файл или директория не найдены."),
        _entry("PermissionError", "Недостаточно прав для операции с файлом/ресурсом."),
        _entry("OverflowError", "Результат вычисления слишком велик для типа."),
        _entry("StopIteration", "Итератор исчерпан (обычно ловится циклом for автоматически)."),
        _entry("AssertionError", "Провалился assert — условие оказалось ложным."),
        _entry("RuntimeError", "Ошибка времени выполнения, не подходящая под другие категории."),
        _entry("NotImplementedError", "Вызван метод-заглушка, который ещё не реализован."),
        _entry("MemoryError", "Закончилась память (слишком большая структура данных?)."),
        _entry("EOFError", "input() не получил данных — ввод закончился раньше ожидаемого."),
        _entry(
            "UnicodeDecodeError", "Не удалось декодировать байты в текст (неверная кодировка?)."
        ),
        _entry(
            "UnicodeEncodeError", "Не удалось закодировать текст в байты (символ вне кодировки?)."
        ),
        _entry("FloatingPointError", "Ошибка при операции с плавающей точкой."),
        _entry("KeyboardInterrupt", "Выполнение прервано (Ctrl+C)."),
        _entry("OSError", "Ошибка операционной системы (файл/сеть/ввод-вывод)."),
    )
}


def lookup(exception_name: str) -> GlossaryEntry | None:
    """Вернуть запись глоссария по имени класса исключения, или None."""
    return _ENTRIES.get(exception_name)


def lookup_from_error(error_text: str) -> GlossaryEntry | None:
    """Найти известное исключение в тексте ошибки (трейсбеке) и вернуть запись.

    Питоновский трейсбек заканчивается строкой вида ``RecursionError: ...``
    (для встроенных исключений — без префикса модуля). Берём последнюю
    непустую строку, отделяем имя до первого двоеточия, отбрасываем возможный
    ``module.``-префикс и ищем в курированном наборе. None, если не совпало.
    """
    if not error_text:
        return None
    lines = [ln for ln in error_text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1].strip()
    candidate = last.split(":", 1)[0].strip()
    candidate = candidate.split(".")[-1]  # foo.BarError → BarError
    return _ENTRIES.get(candidate)
