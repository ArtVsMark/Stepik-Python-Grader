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


class _CodeScanner(ast.NodeVisitor):
    """Обходит AST решения, собирая импорты, определения и «интересные» вызовы."""

    def __init__(self, notable_builtins: frozenset[str]) -> None:
        self._notable = notable_builtins
        self.import_aliases: dict[str, str] = {}  # alias -> module (import x [as y])
        self.from_imports: dict[str, str] = {}  # local -> "module.name"
        self.defined_names: set[str] = set()  # def/class/assign/args — пользовательские
        # concept -> (kind, snippet); dict сохраняет первое вхождение, дедуп по concept.
        self.found: dict[str, tuple[str, str]] = {}

    # -- сбор контекста ---------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.import_aliases[alias.asname or alias.name] = alias.name
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
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.defined_names.add(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined_names.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.defined_names.add(target.id)
        self.generic_visit(node)

    # -- обнаружение концепций -------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            root = func.value.id
            if root in self.import_aliases:
                module = self.import_aliases[root]
                self._record(f"{module}.{func.attr}", "function", f"{root}.{func.attr}(...)")
        elif isinstance(func, ast.Name):
            name = func.id
            if name in self.from_imports:
                self._record(self.from_imports[name], "function", f"{name}(...)")
            elif name in self._notable and name not in self.defined_names:
                self._record(name, "function", f"{name}(...)")
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._record("match/case", "construct", "match ...: case ...")
        self.generic_visit(node)

    def _record(self, concept: str, kind: str, snippet: str) -> None:
        self.found.setdefault(concept, (kind, snippet))


def _last_exception_name(error_text: str) -> str | None:
    """Имя класса исключения из последней строки трейсбека (или None)."""
    lines = [ln for ln in error_text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    candidate = lines[-1].strip().split(":", 1)[0].strip()
    candidate = candidate.split(".")[-1]  # foo.BarError → BarError
    if candidate.isidentifier() and candidate[:1].isupper():
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
