"""detector.py — консервативный детектор недостающих концепций (issue #126).

Архитектурный слой: Domain. Зависит только от ``glossary/models.py`` (stdlib).

``MissingConceptDetector`` сканирует решения (через ``ast``, БЕЗ исполнения
кода — см. запрет в CLAUDE.md на запуск untrusted-кода) и тексты ошибок и
находит функции/конструкции/исключения, для которых, вероятно, нет карточки в
локальной базе (журнал J7). Результат — список ``GlossaryMissingEntry`` для
очереди пополнения.

Принципы:
- **Консервативность.** Не помечаем пользовательские имена: учитываются только
  вызовы через импортированные stdlib-модули (``functools.reduce``),
  from-import'ы (``from functools import reduce``), курируемый набор «заметных»
  встроенных функций и явные синтаксические конструкции (``match/case``).
  Обычный ``def foo(): ...; foo()`` пробелом не считается.
- **Детерминизм.** Один и тот же вход → один и тот же результат; порядок
  обнаружения стабилен (сортировка по concept).
- **Подавление известного.** Переданные ``known`` термины (например,
  ``JsonGlossaryProvider.known_terms()``) исключают уже покрытые карточками
  концепции — без дублей в очереди.
"""

from __future__ import annotations

import ast
from datetime import date

from .models import GlossaryMissingEntry

__all__ = [
    "DEFAULT_NOTABLE_BUILTINS",
    "MissingConceptDetector",
    "scan_code_concepts",
]

# «Заметные» встроенные функции, которые новичок обычно ищет в справочнике.
# Намеренно узкий набор (итерация/функциональщина), чтобы не шуметь на
# повседневных print/len/int/str.
DEFAULT_NOTABLE_BUILTINS: frozenset[str] = frozenset(
    {
        "map",
        "filter",
        "zip",
        "enumerate",
        "sorted",
        "reversed",
        "any",
        "all",
        "iter",
        "next",
        "isinstance",
        "getattr",
        "setattr",
    }
)


# Панель «Функции в коде» (issue #322) показывает и повседневные builtin'ы —
# в отличие от узкого DEFAULT_NOTABLE_BUILTINS (тот заточен под очередь
# «Недостающее» и намеренно молчит про print/len). Пополняет тот же набор
# базовыми функциями, которые новичок ищет в справочнике.
CODE_TERM_BUILTINS: frozenset[str] = DEFAULT_NOTABLE_BUILTINS | frozenset(
    {
        "print",
        "input",
        "len",
        "int",
        "str",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "range",
        "open",
        "abs",
        "sum",
        "min",
        "max",
        "round",
        "type",
        "repr",
        "format",
        "ord",
        "chr",
        "bin",
        "hex",
        "pow",
        "divmod",
        "hasattr",
        "issubclass",
    }
)

# Имена методов встроенных типов (str/list/dict/set) — эвристика #322: вызов
# `x.<method>()` считается использованием метода встроенного типа. Тип `x`
# статически неизвестен → возможны ложные срабатывания на пользовательских
# объектах, поэтому такие концепции помечаются confidence="low".
_BUILTIN_METHODS: frozenset[str] = frozenset(
    {
        # str
        "split",
        "rsplit",
        "splitlines",
        "join",
        "strip",
        "lstrip",
        "rstrip",
        "replace",
        "find",
        "rfind",
        "startswith",
        "endswith",
        "upper",
        "lower",
        "title",
        "capitalize",
        "casefold",
        "swapcase",
        "zfill",
        "center",
        "ljust",
        "rjust",
        "format",
        "format_map",
        "encode",
        "isdigit",
        "isalpha",
        "isalnum",
        "isspace",
        "isupper",
        "islower",
        "partition",
        "rpartition",
        # list
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "sort",
        "reverse",
        "copy",
        # dict
        "keys",
        "values",
        "items",
        "get",
        "setdefault",
        "update",
        "popitem",
        "fromkeys",
        # set
        "add",
        "discard",
        "union",
        "intersection",
        "difference",
        "symmetric_difference",
        "issubset",
        "issuperset",
        "isdisjoint",
        # общие
        "count",
        "index",
        "clear",
    }
)


class _CodeScanner(ast.NodeVisitor):
    """Обходит AST решения, собирая импорты, определения и «интересные» вызовы."""

    def __init__(
        self,
        notable_builtins: frozenset[str],
        *,
        methods: frozenset[str] | None = None,
        detect_constructs: bool = False,
        detect_exceptions: bool = False,
        detect_attributes: bool = False,
        detect_names: bool = False,
    ) -> None:
        self._notable = notable_builtins
        # issue #322/#367: имена методов встроенных типов (None → метод-эвристику
        # не применяем). Набор передаётся извне (инвентарь), а не хардкодится.
        self._methods = methods
        # issue #367: синтаксические конструкции (comprehensions, lambda, срез,
        # f-строка, распаковка, тернарный, walrus, декоратор, with, try). Gated:
        # панель «Функции в коде» включает, узкий MissingConceptDetector — нет
        # (сохраняем его прежнее поведение: только match/case + вызовы).
        self._detect_constructs = detect_constructs
        # issue #686: исключения, названные в коде (`raise ValueError`,
        # `except KeyError:`), и атрибуты импортированных модулей без вызова
        # (`math.pi`, `sys.argv`). Тоже gated: очередь пополнения ловит
        # исключения из ТЕКСТА ошибки, а не из исходника, и её поведение здесь
        # не меняется.
        self._detect_exceptions = detect_exceptions
        self._detect_attributes = detect_attributes
        # issue #686: голые ссылки на имена с карточкой — `isinstance(x, int)`,
        # `x: Counter`, `class Foo(Enum)`. Набор ``notable`` для этого приходит
        # из самой базы (все bare-имена карточек), поэтому шум ограничен тем, на
        # что реально есть карточка. Пользовательские имена гасит ``defined_names``.
        self._detect_names = detect_names
        # id() Name-узлов, уже учтённых как функция вызова (``visit_Call``):
        # `Counter(...)` не должен ещё раз всплыть голой ссылкой ``Counter``.
        self._call_funcs: set[int] = set()
        self.import_aliases: dict[str, str] = {}  # alias -> module (import x [as y])
        self.from_imports: dict[str, str] = {}  # local -> "module.name"
        self.defined_names: set[str] = set()  # def/class/assign/args — пользовательские
        # id() вложенных Attribute-узлов, уже покрытых внешней цепочкой (issue
        # #686): из `os.path.join(...)` концепция — вся цепочка, а не ещё и
        # промежуточный модуль `os.path`.
        self._nested_attrs: set[int] = set()
        # concept -> (kind, snippet); dict сохраняет первое вхождение, дедуп по concept.
        self.found: dict[str, tuple[str, str]] = {}

    # -- сбор контекста ---------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.import_aliases[alias.asname] = alias.name
            else:
                # `import a` и `import a.b.c` связывают ИМЯ верхнего уровня `a`
                # в неймспейсе (issue #367 — иначе `os.path.join` при `import os`
                # не находил корень-модуль). Полное имя тоже регистрируем.
                self.import_aliases[alias.name] = alias.name
                top = alias.name.split(".", 1)[0]
                self.import_aliases.setdefault(top, top)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.from_imports[local] = f"{module}.{alias.name}" if module else alias.name
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined_names.add(node.name)
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.defined_names.add(arg.arg)
        if node.decorator_list:
            self._construct("decorator", "@decorator")
        self._construct("def", "def ...():")  # issue #686: карточка «Функции»
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.defined_names.add(node.name)
        if node.decorator_list:
            self._construct("decorator", "@decorator")
        self._construct("def", "async def ...():")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined_names.add(node.name)
        if node.decorator_list:
            self._construct("decorator", "@decorator")
        self._construct("class", "class ...:")  # issue #686: карточка «ООП»
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.defined_names.add(target.id)
        self.generic_visit(node)

    # -- обнаружение концепций -------------------------------------------

    @staticmethod
    def _attr_chain(node: ast.Attribute) -> tuple[str, str] | None:
        """Развернуть цепочку Attribute до корня-``Name`` → ``(root, dotted_path)``.

        ``os.path.join`` → ``("os", "path.join")``; ``math.sqrt`` → ``("math",
        "sqrt")``. None, если корень — не ``Name`` (``f().attr``,
        ``x.strip().split``): тогда матч идёт по метод-эвристике (issue #367).
        """
        parts = [node.attr]
        cur: ast.expr = node.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            parts.reverse()
            return parts[0], ".".join(parts[1:])
        return None

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            # Помечаем имя-функцию, чтобы ``visit_Name`` не задвоил его голой
            # ссылкой (обход зайдёт в этот же Name через generic_visit).
            self._call_funcs.add(id(func))
        if isinstance(func, ast.Attribute):
            chain = self._attr_chain(func)
            root = chain[0] if chain is not None else None
            if chain is not None and root in self.import_aliases:
                # issue #367: `os.path.join(...)` разворачивается в целостное
                # `os.path.join`, а не путается с методом `str.join`.
                module = self.import_aliases[root]
                attr_path = chain[1]
                self._record(f"{module}.{attr_path}", "function", f"{root}.{attr_path}(...)")
            elif self._methods is not None and func.attr in self._methods:
                # issue #322: `x.split()` → метод встроенного типа (эвристика,
                # тип получателя статически неизвестен — confidence="low").
                recv = root if root is not None else "…"
                self._record(func.attr, "method", f"{recv}.{func.attr}(...)")
        elif isinstance(func, ast.Name):
            name = func.id
            if name in self.from_imports:
                self._record(self.from_imports[name], "function", f"{name}(...)")
            elif name in self._notable and name not in self.defined_names:
                # issue #686: `ValueError("...")` — исключение, а не функция:
                # kind решает панель, поэтому он должен быть точным.
                kind = (
                    "exception"
                    if self._detect_exceptions and _looks_like_exception_name(name)
                    else "function"
                )
                self._record(name, kind, f"{name}(...)")
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._record("match/case", "construct", "match ...: case ...")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Голая ссылка на имя с карточкой (не вызов): ``isinstance(x, int)``,
        ``x: Counter``, ``Enum`` как база класса (issue #686).

        Реагируем только на чтение (``Load``) и не на имя-функцию вызова (его
        уже записал ``visit_Call``). Импортированное имя разворачивается в
        квалифицированный концепт (``Counter`` → ``collections.Counter``),
        встроенное — остаётся собой; пользовательские имена гасит
        ``defined_names``.
        """
        if (
            self._detect_names
            and isinstance(node.ctx, ast.Load)
            and id(node) not in self._call_funcs
        ):
            name = node.id
            if name not in self.defined_names:
                if name in self.from_imports:
                    self._record(self.from_imports[name], "name", name)
                elif name in self._notable:
                    self._record(name, "name", name)
        self.generic_visit(node)

    # -- исключения и атрибуты модулей (issue #686) ------------------------

    def _record_exception(self, node: ast.expr | None, snippet: str) -> None:
        """Записать имя исключения из узла ``raise``/``except`` (если оно похоже на класс).

        Разбирает и голое имя (``ValueError``), и вызов (``ValueError("...")``),
        и квалифицированное (``socket.timeout`` → ``timeout``), и кортеж в
        ``except (A, B)``. Фильтр ``_looks_like_exception_name`` тот же, что у
        разбора трейсбека, — произвольный идентификатор классом не станет.
        """
        if node is None:
            return
        if isinstance(node, ast.Call):
            self._record_exception(node.func, snippet)
            return
        if isinstance(node, ast.Tuple):
            for element in node.elts:
                self._record_exception(element, snippet)
            return
        name = node.id if isinstance(node, ast.Name) else None
        if name is None and isinstance(node, ast.Attribute):
            name = node.attr
        if name and _looks_like_exception_name(name):
            self._record(name, "exception", snippet.format(name=name))

    def visit_Raise(self, node: ast.Raise) -> None:
        if self._detect_exceptions:
            self._record_exception(node.exc, "raise {name}(...)")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if self._detect_exceptions:
            self._record_exception(node.type, "except {name}:")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Атрибут импортированного модуля без вызова: ``math.pi``, ``sys.argv``.

        Карточки таких констант в базе есть, но панель их не показывала —
        сканер реагировал только на ``Call`` (issue #686). Вызовы сюда тоже
        попадают (``math.sqrt(1)``), но ``_record`` держит первое вхождение, а
        ``visit_Call`` отрабатывает раньше — kind остаётся ``function``.

        Записывается только ВНЕШНИЙ узел цепочки: у ``os.path.join`` концепция
        одна (``os.path.join``), а промежуточный ``os.path`` — не отдельный
        термин панели.
        """
        if self._detect_attributes and id(node) not in self._nested_attrs:
            chain = self._attr_chain(node)
            if chain is not None and chain[0] in self.import_aliases:
                module = self.import_aliases[chain[0]]
                self._record(f"{module}.{chain[1]}", "attribute", f"{chain[0]}.{chain[1]}")
        inner = node.value
        while isinstance(inner, ast.Attribute):
            self._nested_attrs.add(id(inner))
            inner = inner.value
        self.generic_visit(node)

    # -- синтаксические конструкции (issue #367, только при detect_constructs) --

    def _construct(self, concept: str, snippet: str) -> None:
        if self._detect_constructs:
            self._record(concept, "construct", snippet)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._construct("list comprehension", "[x for x in ...]")
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._construct("set comprehension", "{x for x in ...}")
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._construct("dict comprehension", "{k: v for ... in ...}")
        self.generic_visit(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._construct("generator expression", "(x for x in ...)")
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._construct("lambda", "lambda x: ...")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._construct("ternary", "a if cond else b")
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._construct("walrus", "(n := ...)")
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self._construct("f-string", 'f"...{x}..."')
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.slice, ast.Slice):
            self._construct("slice", "seq[a:b]")
        self.generic_visit(node)

    def visit_Starred(self, node: ast.Starred) -> None:
        self._construct("unpacking", "*args / a, *b = ...")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self._construct("with", "with ... as ...:")
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._construct("with", "async with ...:")
        self.generic_visit(node)

    # issue #686: ключевые конструкции языка, на которые есть карточки (циклы,
    # условие, ветвление потока, определения). Concept — само ключевое слово,
    # оно совпадает с id соответствующей карточки.
    def visit_For(self, node: ast.For) -> None:
        self._construct("for", "for ... in ...:")
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._construct("for", "async for ... in ...:")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._construct("while", "while ...:")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self._construct("if", "if ...:")
        self.generic_visit(node)

    def visit_Break(self, node: ast.Break) -> None:
        self._construct("break", "break")
        self.generic_visit(node)

    def visit_Continue(self, node: ast.Continue) -> None:
        self._construct("continue", "continue")
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self._construct("return", "return ...")
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._construct("assert", "assert ...")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._construct("try/except", "try: ... except ...:")
        self.generic_visit(node)

    def _record(self, concept: str, kind: str, snippet: str) -> None:
        self.found.setdefault(concept, (kind, snippet))


def scan_code_concepts(
    code: str,
    *,
    notable_builtins: frozenset[str] | None = None,
    methods: frozenset[str] | None = None,
    detect_names: bool = False,
) -> dict[str, tuple[str, str]]:
    """Концепции, найденные в коде (без фильтра «известных»): ``concept -> (kind, snippet)``.

    Публичная обёртка над ``_CodeScanner`` для витрин «функции в коде» (issue
    #321/#322) — в отличие от ``MissingConceptDetector.detect_from_code`` НЕ
    отсеивает покрытые карточками концепции (это делает потребитель, сопоставляя
    с базой) и распознаёт **шире**: builtin'ы, методы встроенных типов
    (``x.split()``, kind ``method``) и синтаксические конструкции
    (comprehensions, lambda, срез, f-строка, распаковка, тернарный, walrus,
    декоратор, with, try — issue #367).

    issue #686: сюда же попадают **исключения**, названные в самом коде
    (``raise ValueError``, ``except KeyError:``, kind ``exception``),
    **атрибуты импортированных модулей** без вызова (``math.pi``, ``sys.argv``,
    kind ``attribute``), **ключевые конструкции** (``for``/``while``/``if``/
    ``def``/``class``/``break``/``continue``/``return``/``assert``) и — при
    ``detect_names`` — **голые ссылки** на имена из ``notable_builtins``
    (``isinstance(x, int)``, ``x: Counter``, kind ``name``): карточки на всё это
    в базе есть, а панель их не видела, потому что сканер реагировал только на
    вызовы.

    ``notable_builtins``/``methods`` (issue #367) переопределяют наборы имён —
    потребитель (``web/glossary_adapter``) собирает их из ``stdlib_inventory`` и
    самой базы (issue #686). По умолчанию — курируемые ``CODE_TERM_BUILTINS``/
    ``_BUILTIN_METHODS`` (обратная совместимость), ``detect_names`` выкл.
    Синтаксически некорректный код → пустой словарь.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    notable = CODE_TERM_BUILTINS if notable_builtins is None else frozenset(notable_builtins)
    method_set = _BUILTIN_METHODS if methods is None else frozenset(methods)
    scanner = _CodeScanner(
        notable,
        methods=method_set,
        detect_constructs=True,
        detect_exceptions=True,
        detect_attributes=True,
        detect_names=detect_names,
    )
    scanner.visit(tree)
    return dict(scanner.found)


# Немногие встроенные исключения Python, не оканчивающиеся на Error/Exception/
# Warning — единственное осмысленное исключение из правила суффикса ниже.
_NON_SUFFIX_BUILTIN_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "StopIteration",
        "StopAsyncIteration",
        "KeyboardInterrupt",
        "SystemExit",
        "GeneratorExit",
    }
)
_EXCEPTION_NAME_SUFFIXES: tuple[str, ...] = ("Error", "Exception", "Warning")


def _looks_like_exception_name(candidate: str) -> bool:
    """True, если ``candidate`` правдоподобен как имя класса исключения.

    Помимо синтаксической валидности (идентификатор с заглавной буквы),
    требует соответствия конвенции именования исключений — иначе произвольный
    текст вида ``"Note: ..."`` (например, заметки ``exc.add_note()``, PEP 678,
    или любая другая строка после трейсбека) ошибочно принимался бы за имя
    исключения (issue #191).
    """
    if not candidate.isidentifier() or not candidate[:1].isupper():
        return False
    return candidate in _NON_SUFFIX_BUILTIN_EXCEPTIONS or candidate.endswith(
        _EXCEPTION_NAME_SUFFIXES
    )


def _last_exception_name(error_text: str) -> str | None:
    """Имя класса исключения из последней строки трейсбека (или None).

    Консервативно: кандидат из последней непустой строки должен не только
    быть валидным идентификатором с заглавной буквы, но и соответствовать
    конвенции именования исключений (см. ``_looks_like_exception_name``).
    """
    lines = [ln for ln in error_text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    candidate = lines[-1].strip().split(":", 1)[0].strip()
    candidate = candidate.split(".")[-1]  # foo.BarError → BarError
    if _looks_like_exception_name(candidate):
        return candidate
    return None


class MissingConceptDetector:
    """Находит функции/конструкции/исключения без карточки в локальной базе."""

    def __init__(self, notable_builtins: frozenset[str] | None = None) -> None:
        self._notable = (
            DEFAULT_NOTABLE_BUILTINS if notable_builtins is None else frozenset(notable_builtins)
        )

    @staticmethod
    def _normalize_known(known: set[str] | None) -> set[str]:
        return {k.strip().lower() for k in known if k.strip()} if known else set()

    def _is_known(self, concept: str, known: set[str]) -> bool:
        concept_lc = concept.lower()
        if concept_lc in known:
            return True
        tail = concept_lc.rsplit(".", 1)[-1]  # functools.reduce → reduce
        return tail in known

    def detect_from_code(
        self,
        code: str,
        *,
        known: set[str] | None = None,
        source: str = "",
        today: str | None = None,
    ) -> list[GlossaryMissingEntry]:
        """Обнаружить пробелы в исходном коде решения (без исполнения).

        Args:
            code: исходный текст решения.
            known: уже покрытые термины (aliases/keywords/id карточек), любой
                регистр; такие концепции не попадают в результат.
            source: имя файла-решения (для ``seen_in``).
            today: ISO-дата для ``first_seen`` (по умолчанию — сегодня).

        Returns:
            Список ``GlossaryMissingEntry`` со статусом ``new``, отсортированный
            по concept. Синтаксически некорректный код → пустой список.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        scanner = _CodeScanner(self._notable)
        scanner.visit(tree)

        known_norm = self._normalize_known(known)
        first_seen = today or date.today().isoformat()
        seen_in = [source] if source else []
        entries: list[GlossaryMissingEntry] = []
        for concept in sorted(scanner.found):
            if self._is_known(concept, known_norm):
                continue
            kind, snippet = scanner.found[concept]
            entries.append(
                GlossaryMissingEntry(
                    concept=concept,
                    kind=kind,  # type: ignore[arg-type]
                    status="new",
                    reason="Обнаружено в коде решения; нет карточки в глоссарии.",
                    snippet=snippet,
                    seen_in=list(seen_in),
                    first_seen=first_seen,
                    origin="solution",
                )
            )
        return entries

    def detect_from_error(
        self,
        error_text: str,
        *,
        known: set[str] | None = None,
        source: str = "",
        verdict: str | None = None,
        today: str | None = None,
    ) -> GlossaryMissingEntry | None:
        """Обнаружить пробел из текста ошибки (трейсбека) решения.

        Возвращает элемент очереди для исключения из последней строки
        трейсбека, если для него нет карточки; иначе None.
        """
        name = _last_exception_name(error_text)
        if name is None:
            return None
        known_norm = self._normalize_known(known)
        if self._is_known(name, known_norm):
            return None
        return GlossaryMissingEntry(
            concept=name,
            kind="exception",
            status="new",
            reason="Исключение в ошибке решения; нет карточки в глоссарии.",
            snippet=error_text.strip().splitlines()[-1].strip() if error_text.strip() else "",
            seen_in=[source] if source else [],
            verdict=verdict,
            first_seen=today or date.today().isoformat(),
            origin="error",
        )
