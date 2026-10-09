"""stepik_compat.py — что в коде решения не поддерживает версия Python шага Stepik (issue #1621).

Грейдер живёт на своей планке и исполняет код студента тем же интерпретатором
(решение владельца в эпике #1575): интерпретатор не выбирается. Зато Stepik
проверяет решение своей версией — ``python3.10``, ``python3.12`` и т. п., у
каждого шага свой набор. Решение, прошедшее у нас на 3.14, на платформе может
упасть из-за ``except*`` или ``str.removeprefix``, и узнаёт об этом студент
только после отправки, по невнятной ошибке.

Здесь — статический разбор кода, без исполнения:

* **синтаксис** — обход ``ast``: признак → версия, с которой он есть
  (:data:`SYNTAX_FEATURES`); таблица одна, у каждого признака свой тест;
* **имена** — импорты, обращения к атрибутам импортированных модулей,
  встроенные функции и методы литералов сверяются с полями ``added`` и
  ``removed`` карточек глоссария. Своей таблицы версий имён нет: источник
  истины у глоссария (выгрузка Glossary-Python, инвариант 6 CLAUDE.md).

ГРАНИЦА НАЗВАНА. Разбор не видит динамики (``getattr``, ``importlib``) и типов
объектов: метод ``x.removeprefix`` у неизвестного ``x`` не сверяется, только у
литерала. Ошибка здесь сдвинута в безопасную сторону: пропустить несовместимое
можно, назвать несовместимым совместимое — по возможности нет.

Это **предупреждение**, а не вердикт: проверку оно не останавливает.
"""

import ast
import builtins
import functools
import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_TARGET",
    "SYNTAX_FEATURES",
    "CompatIssue",
    "CompatReport",
    "NameIndex",
    "build_name_index",
    "bundled_name_index",
    "check_code",
    "compat_report",
    "format_version",
    "parse_version",
    "read_step_languages",
    "step_python_versions",
]

Version = tuple[int, int]

#: Версия «есть с древних времён»: в данных глоссария это ``<3.0``.
_ANCIENT: Version = (0, 0)
_PYTHON_LANG = re.compile(r"python\s*(\d+)\.(\d+)", re.IGNORECASE)


def parse_version(raw: str) -> Version | None:
    """``"3.10"`` → ``(3, 10)``; ``"<3.0"`` → ``(0, 0)``; непонятное → ``None``."""
    text = raw.strip()
    if text.startswith("<"):
        return _ANCIENT
    match = re.fullmatch(r"(\d+)\.(\d+)", text)
    return (int(match[1]), int(match[2])) if match else None


def step_python_versions(languages: Iterable[str]) -> list[Version]:
    """Версии Python из языков шага Stepik (``python3.10`` → ``(3, 10)``), по возрастанию."""
    found = {(int(m[1]), int(m[2])) for lang in languages if (m := _PYTHON_LANG.search(lang))}
    return sorted(found)


@dataclass(frozen=True)
class CompatIssue:
    """Одна несовместимость: что, где и с какой версии.

    ``since`` — версия, С КОТОРОЙ возможность есть (для удалённого —
    ``None``, а ``removed`` — версия удаления).
    """

    line: int
    what: str  # для синтаксиса — ключ признака, для имени — само имя
    kind: str  # "syntax" | "name"
    since: Version | None = None
    removed: Version | None = None

    def to_dict(self) -> dict[str, object]:
        """Форма для JSON: версии строками ``"3.9"``, отсутствующая — ``None``."""
        return {
            "line": self.line,
            "what": self.what,
            "kind": self.kind,
            "since": format_version(self.since) if self.since else None,
            "removed": format_version(self.removed) if self.removed else None,
        }


def format_version(version: Version) -> str:
    """``(3, 9)`` → ``"3.9"``."""
    return f"{version[0]}.{version[1]}"


# --- Синтаксис ---------------------------------------------------------------

#: Признак синтаксиса: (версия появления, ключ, детектор узла). Детектор
#: получает узел ``ast`` и отвечает, есть ли в нём признак. Подпись признака
#: живёт в каталогах сообщений под ключом ``compat_syntax_<ключ>`` (сервер) и
#: ``compat.syntax_<ключ>`` (веб): разбор не знает языка интерфейса. Таблица
#: закрытая: новый признак — новая строка, две пары подписей и новый тест.
SyntaxFeature = tuple[Version, str, Callable[[ast.AST], bool]]


def _is_async_gen(node: ast.AST) -> bool:
    return isinstance(node, ast.AsyncFunctionDef) and any(
        isinstance(sub, (ast.Yield, ast.YieldFrom)) for sub in ast.walk(node)
    )


def _has_posonly(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and bool(
        node.args.posonlyargs
    )


def _has_type_params(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and bool(
        getattr(node, "type_params", ())
    )


def _starred_in_index(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    index = node.slice
    items = index.elts if isinstance(index, ast.Tuple) else [index]
    return any(isinstance(item, ast.Starred) for item in items)


SYNTAX_FEATURES: tuple[SyntaxFeature, ...] = (
    ((3, 6), "fstring", lambda n: isinstance(n, ast.JoinedStr)),
    ((3, 6), "var_annotation", lambda n: isinstance(n, ast.AnnAssign)),
    ((3, 6), "async_generator", _is_async_gen),
    ((3, 8), "walrus", lambda n: isinstance(n, ast.NamedExpr)),
    ((3, 8), "posonly", _has_posonly),
    ((3, 10), "match", lambda n: isinstance(n, ast.Match)),
    ((3, 11), "except_star", lambda n: isinstance(n, ast.TryStar)),
    ((3, 11), "star_index", _starred_in_index),
    ((3, 12), "type_statement", lambda n: isinstance(n, ast.TypeAlias)),
    ((3, 12), "type_params", _has_type_params),
    ((3, 14), "tstring", lambda n: type(n).__name__ == "TemplateStr"),
)


def _syntax_issues(tree: ast.AST, target: Version) -> Iterator[CompatIssue]:
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        for since, key, detect in SYNTAX_FEATURES:
            if since > target and detect(node):
                line = getattr(node, "lineno", 0)
                if (line, key) not in seen:
                    seen.add((line, key))
                    yield CompatIssue(line=line, what=key, kind="syntax", since=since)


# --- Имена -------------------------------------------------------------------

#: Встроенные имена интерпретатора грейдера — круг, в котором вызов ``name()``
#: сверяется с карточкой глоссария как встроенная функция.
_BUILTINS = frozenset(dir(builtins))

#: Сколько карточек членов нужно, чтобы вывести версию модуля без своей карточки.
_MIN_MEMBERS = 2

#: Литерал → имя типа в id карточек глоссария (``str.removeprefix``).
_LITERAL_TYPES: dict[type, str] = {str: "str", bytes: "bytes", int: "int", float: "float"}


@dataclass(frozen=True)
class NameIndex:
    """Версии имён из глоссария: id → (``added``, ``removed``)."""

    versions: dict[str, tuple[Version, Version | None]]

    def lookup(self, name: str) -> tuple[Version, Version | None] | None:
        """Версии имени; id глоссария сверяются без учёта регистра."""
        return self.versions.get(name) or self.versions.get(name.lower())


def build_name_index(cards: Iterable[object]) -> NameIndex:
    """Индекс имён из карточек глоссария (атрибуты ``id``, ``added``, ``removed``).

    Карточки без понятной версии ``added`` в индекс не попадают: молчание
    безопаснее ложной находки. Модули без своей карточки получают самую раннюю
    версию своих членов.
    """
    versions: dict[str, tuple[Version, Version | None]] = {}
    derived: dict[str, Version] = {}
    members: dict[str, int] = {}
    cards = list(cards)
    explicit = {str(getattr(card, "id", "") or "") for card in cards}
    for card in cards:
        card_id = str(getattr(card, "id", "") or "")
        added = parse_version(str(getattr(card, "added", "") or ""))
        if not card_id or added is None:
            continue
        removed = parse_version(str(getattr(card, "removed", "") or ""))
        versions[card_id] = (added, removed)
        versions.setdefault(card_id.lower(), (added, removed))
        # Карточек самих модулей в глоссарии нет — есть карточки их членов
        # (``tomllib.load``). Версия модуля — самая ранняя версия его членов:
        # это вывод из тех же данных, а не вторая таблица версий.
        parts = card_id.split(".")
        for depth in range(1, len(parts)):
            prefix = ".".join(parts[:depth])
            if prefix not in explicit:
                current = derived.get(prefix)
                derived[prefix] = added if current is None else min(current, added)
                members[prefix] = members.get(prefix, 0) + 1
    for module, added in derived.items():
        # Один член — слишком мало, чтобы судить о модуле: при скудном покрытии
        # глоссария старый модуль с одной новой карточкой (``itertools.batched``)
        # выглядел бы новым. Ложное «несовместимо» дороже пропуска.
        if members[module] >= _MIN_MEMBERS:
            versions.setdefault(module, (added, None))
    return NameIndex(versions)


class _NameCollector(ast.NodeVisitor):
    """Имена, которые код берёт из stdlib: (строка, полное имя)."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []
        self.modules: dict[str, str] = {}  # псевдоним → модуль
        self.defined: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((node.lineno, alias.name))
            self.defined.add(alias.asname or alias.name.split(".")[0])
            self.modules[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and not node.level:
            self.found.append((node.lineno, node.module))
            for alias in node.names:
                if alias.name != "*":
                    self.found.append((node.lineno, f"{node.module}.{alias.name}"))
                    self.defined.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined.add(node.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.defined.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(value, ast.Name) and value.id in self.modules:
            self.found.append((node.lineno, f"{self.modules[value.id]}.{node.attr}"))
        elif isinstance(value, ast.Constant) and type(value.value) in _LITERAL_TYPES:
            self.found.append((node.lineno, f"{_LITERAL_TYPES[type(value.value)]}.{node.attr}"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Только настоящие встроенные имена: без точки в глоссарии лежат и
        # термины (``field``, ``sentinel``), и их тёзка в коде студента —
        # своя функция или импорт, а не встроенная.
        if isinstance(node.func, ast.Name) and node.func.id in _BUILTINS:
            self.found.append((node.lineno, f"builtin:{node.func.id}"))
        self.generic_visit(node)


def _name_issues(tree: ast.AST, target: Version, index: NameIndex) -> Iterator[CompatIssue]:
    collector = _NameCollector()
    collector.visit(tree)
    seen: set[str] = set()
    for line, raw in collector.found:
        name = raw
        if raw.startswith("builtin:"):
            name = raw.removeprefix("builtin:")
            # Своя функция с именем встроенной — не встроенная.
            if name in collector.defined:
                continue
        if name in seen:
            continue
        versions = index.lookup(name)
        if versions is None:
            continue
        added, removed = versions
        if added > target:
            seen.add(name)
            yield CompatIssue(line=line, what=name, kind="name", since=added)
        elif removed is not None and removed <= target:
            seen.add(name)
            yield CompatIssue(line=line, what=name, kind="name", removed=removed)


def check_code(code: str, target: Version, index: NameIndex) -> list[CompatIssue]:
    """Несовместимости кода с Python ``target``, по строкам; не разбирается — пусто.

    Синтаксическую ошибку здесь не называют: её скажет сама проверка, а
    предупреждение о совместимости о сломанном коде ничего не знает.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError, ValueError:
        return []
    issues = [*_syntax_issues(tree, target), *_name_issues(tree, target, index)]
    return sorted(issues, key=lambda issue: (issue.line, issue.what))


# --- Отчёт по задаче ---------------------------------------------------------

#: Цель, когда версии шага не известны (задача скачана до того, как их стали
#: сохранять, или заведена без Stepik). Свежая из тех, что Stepik даёт на
#: шагах курса: сверка с ней ловит только новое из 3.13+, то есть почти не
#: шумит, а честная пометка «версии шага не известны» говорит, что делать.
DEFAULT_TARGET: Version = (3, 12)


def read_step_languages(task_dir: Path) -> list[str]:
    """Языки шага Stepik из ``meta.json`` задачи; нет файла или поля — пусто."""
    try:
        meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return []
    languages = meta.get("languages") if isinstance(meta, dict) else None
    return [str(lang) for lang in languages] if isinstance(languages, list) else []


@functools.cache
def bundled_name_index() -> NameIndex:
    """Индекс версий имён по встроенному глоссарию (выгрузка Glossary-Python).

    Данные встроенного глоссария в пределах процесса не меняются, поэтому
    индекс строится один раз. Глоссарий не читается — индекс пуст: проверка
    синтаксиса продолжает работать.
    """
    from stepik_grader.glossary.json_provider import BUNDLED_GLOSSARY_DIR, JsonGlossaryProvider

    try:
        cards = JsonGlossaryProvider.from_directory(BUNDLED_GLOSSARY_DIR).all()
    except Exception:
        return NameIndex({})
    return build_name_index(cards)


@dataclass(frozen=True)
class CompatReport:
    """Совместимость решения с версией Python, на которой его проверит Stepik.

    ``target`` — версия, с которой сверено: та, на которой отправляет грейдер
    (самая свежая из языков шага). ``known`` — версии шага известны; иначе
    сверено с :data:`DEFAULT_TARGET`.
    """

    target: Version
    known: bool
    issues: list[CompatIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Форма для JSON (веб, ``--output json``)."""
        return {
            "target": format_version(self.target),
            "known": self.known,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def compat_report(code: str, task_dir: Path, *, index: NameIndex | None = None) -> CompatReport:
    """Сверить код решения с версией Python шага, к которому относится ``task_dir``.

    Цель — самая свежая версия Python из языков шага: на ней отправляет
    грейдер (``stepik_client._pick_python_language``). Версии шага не
    известны — :data:`DEFAULT_TARGET` и ``known=False``.
    """
    versions = step_python_versions(read_step_languages(task_dir))
    target = versions[-1] if versions else DEFAULT_TARGET
    issues = check_code(code, target, index if index is not None else bundled_name_index())
    return CompatReport(target=target, known=bool(versions), issues=issues)
