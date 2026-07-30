#!/usr/bin/env python
"""glossary_draft_pipeline.py — полуавтоматический конвейер черновиков (issue #438).

Следующий шаг после ``generate_draft_cards.py`` (голый черновик из интроспекции,
issue #328): собрать карточку по шаблону волны **B1** (RU+EN summary + примеры),
**автоматически проверить примеры прогоном** («# → результат», практика #371) и
выдать diff против базы для ручного ревью — ничего не мержится автоматически.

**Без реальной модели (пока).** Генерация текста/примеров — за сменным
провайдером ``DraftProvider``; дефолт ``OfflineDraftProvider`` работает
**офлайн, без LLM** (EN-summary из docstring, каркас B1). Реальный BYOK-провайдер
(облако/ollama на ``requests``, эпик AI #434/#435) — сменная точка расширения,
**не** подключается здесь и не добавляет runtime-зависимостей: сам конвейер —
dev-инструмент из ``scripts/`` на чистом stdlib. Ценность, не зависящая от
модели, — движок валидации примеров и diff-ревью: они работают уже сейчас.

Режимы:

    # аудит примеров существующих карточек (прогон + сверка «# → …»)
    python scripts/glossary_draft_pipeline.py check \
        --base src/stepik_grader/glossary/data

    # предложить B1-черновик qualname (офлайн-каркас или контент из файла),
    # проверить его примеры, показать review-diff против базы (dry-run):
    python scripts/glossary_draft_pipeline.py propose --qualname str.rjust
    python scripts/glossary_draft_pipeline.py propose --qualname str.rjust \
        --content-file draft.json --write review-drafts.json
"""

from __future__ import annotations

import argparse
import ast
import builtins
import difflib
import functools
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Protocol

# Скрипт в scripts/ (не на sys.path пакета) — добавим src/ и сам scripts/ для
# импортов (переиспользуем интроспекцию generate_draft_cards).
_SCRIPTS = Path(__file__).resolve().parent
_SRC = _SCRIPTS.parent / "src"
for _p in (_SRC, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import generate_draft_cards as _gen  # noqa: E402

from stepik_grader.glossary.json_provider import (  # noqa: E402
    BUNDLED_GLOSSARY_DIR,
    JsonGlossaryProvider,
)
from stepik_grader.glossary.models import GlossaryCard  # noqa: E402
from stepik_grader.glossary.stdlib_inventory import (  # noqa: E402
    StdlibItem,
    build_stdlib_inventory,
)

__all__ = [
    "DraftProvider",
    "ExampleReport",
    "OfflineDraftProvider",
    "ProposedContent",
    "build_b1_draft",
    "compare_expected_actual",
    "exception_name",
    "extract_expected",
    "main",
    "platform_gaps",
    "review_diff",
    "run_check",
    "run_propose",
    "split_code_and_expected",
    "validate_examples",
]

# Маркер ожидаемого результата в примере: "# → …" (стрелка U+2192) либо ASCII
# "# -> …". Практика #371: демонстрационный вывод рядом с кодом.
_EXPECT_RE = re.compile(r"#\s*(?:→|->)\s*(.*)$")
# Хвостовую человеко-пометку "значение (комментарий)" отсекает
# _strip_trailing_note: скобки внутри пометки допустимы ("True (за O(1))"),
# поэтому парная открывающая ищется сканированием с конца, а не регуляркой.
# Ожидание «пример намеренно падает»: "# → ValueError", "# → statistics.StatisticsError"
# (сложившийся в базе способ показать исключение, issue #745).
_EXCEPTION_NAME_RE = re.compile(r"^(?:[A-Za-z_]\w*\.)*(?P<name>[A-Z]\w*)$")
_EXCEPTION_SUFFIXES = ("Error", "Exception", "Warning", "Interrupt", "Exit")
# Карточка, чьи примеры используют API, которого нет вне POSIX (issue #745).
_POSIX_ONLY_TAG = "platform:posix"
# Модули stdlib, чей состав различается по платформам: у примера, зовущего
# отсутствующий здесь атрибут, вердикт говорил бы об ОС прогона, а не о карточке.
# Список закрытый: имена из примеров импортируются для проверки через hasattr,
# и импортировать что попало из данных карточки нельзя.
_PLATFORM_MODULES = frozenset({"errno", "mmap", "os", "select", "signal", "socket", "stat", "time"})
# Модули stdlib, которых вне POSIX нет вовсе (сам `import` даёт ImportError).
_POSIX_ONLY_MODULES = frozenset(
    {
        "crypt",
        "fcntl",
        "grp",
        "nis",
        "ossaudiodev",
        "posix",
        "pty",
        "pwd",
        "resource",
        "spwd",
        "syslog",
        "termios",
        "tty",
    }
)

# Секунд на прогон одного примера (dev-инструмент, снимок против зависаний).
_DEFAULT_TIMEOUT = 10.0


def split_code_and_expected(line: str) -> tuple[str, str | None]:
    """Разбить строку примера на ``(код, ожидаемый_вывод|None)``.

    Ожидаемый вывод — текст после ``# → `` / ``# -> ``; если маркера нет,
    возвращается ``None``. Код возвращается как есть (включая любой inline-хвост
    до маркера) — он остаётся исполнимым, т.к. ``#``-комментарий инертен.
    """
    match = _EXPECT_RE.search(line)
    if match is None:
        return line, None
    return line, match.group(1).strip()


def extract_expected(examples: list[str]) -> list[str]:
    """Список ожидаемых значений (в порядке появления ``# → …`` в примерах)."""
    expected: list[str] = []
    for line in examples:
        _, want = split_code_and_expected(line)
        if want is not None:
            expected.append(want)
    return expected


def _approx_match(want: str, got: str) -> bool:
    """Префиксное совпадение до ``...`` (``3.14159...`` ≈ ``3.141592653589793``)."""
    return "..." in want and got.startswith(want.split("...", 1)[0].rstrip())


def _strip_trailing_note(want: str) -> str | None:
    """Отбросить хвостовую пометку ``значение (комментарий)``, иначе ``None``.

    Внутри пометки скобки допустимы (``True (мгновенно, O(1))``), поэтому
    парная открывающая ищется сканированием с конца, а не регуляркой.
    """
    if not want.endswith(")"):
        return None
    depth = 0
    for pos in range(len(want) - 1, -1, -1):
        if want[pos] == ")":
            depth += 1
        elif want[pos] == "(":
            depth -= 1
            if depth == 0:
                value = want[:pos].rstrip()
                return value if value and value != want else None
    return None


def _prefix_note_match(want: str, got: str) -> bool:
    """Ожидание вида ``значение — пояснение``: сверяем только значение.

    Пояснение к выводу пишут не только в скобках, но и через тире, двоеточие
    или запятую (``False — F_OK это просто проверка существования``,
    ``32, по два hex-символа на байт``). Совпадением считаем лишь случай, когда
    ожидание **начинается** ровно с напечатанного значения, а сразу за ним идёт
    разделитель — иначе ``1000, а не 100`` совпало бы со ``100``.
    """
    if not got or not want.startswith(got):
        return False
    tail = want[len(got) :]
    return tail.startswith((" — ", " - ", ": ", ", ", " ("))


def _literal_match(want: str, got: str, raw: str) -> bool:
    """Сравнить ожидание и вывод как литералы Python, а не как текст.

    Ожидания пишут руками, поэтому запись расходится с ``repr`` там, где
    значение то же самое: ``(1,2,3)`` без пробелов после запятых, ``'P'`` в
    repr-форме строки, которую ``print`` выводит без кавычек. Сравнение идёт
    по значению **и типу** — иначе ``1`` совпало бы с ``True``.

    ``raw`` — вывод до обрезки пробелов: у строки в repr-форме
    (``'        42'``) пробелы значимы и сравнивать надо с ним.
    """
    try:
        want_value = ast.literal_eval(want)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False
    # Ожидание записано как repr строки, а print печатает её содержимое.
    if isinstance(want_value, str) and want_value in (got, raw):
        return True
    try:
        got_value = ast.literal_eval(got)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return False
    return type(want_value) is type(got_value) and bool(want_value == got_value)


def compare_expected_actual(expected: str, actual: str) -> bool:
    """Сверить ожидаемый вывод с фактической строкой stdout (терпимо).

    Терпимость к легитимным формам записи (#371):
    - **аппроксимация** ``…...`` — префиксное совпадение до многоточия
      (``3.14159...`` ≈ ``3.141592653589793``);
    - **хвостовая пометка** ``значение (комментарий)`` — сравнивается только
      значение (``None (всегда None)`` ↔ ``None``);
    - **запись литерала** — ``(1,2,3)`` ↔ ``(1, 2, 3)``, ``'P'`` ↔ ``P`` (#745);
    - **имя исключения** — ``statistics.StatisticsError`` ↔ ``StatisticsError``,
      как его печатает обёртка намеренного падения (#745);
    - обрамляющие пробелы.
    """

    got = actual.strip()
    want = expected.strip()

    def matches(candidate: str) -> bool:
        if (
            candidate == got
            or _approx_match(candidate, got)
            or _literal_match(candidate, got, actual)
            or _prefix_note_match(candidate, got)
        ):
            return True
        return exception_name(candidate) == got

    if matches(want):
        return True
    note = _strip_trailing_note(want)
    return note is not None and matches(note)


@dataclass
class ExampleReport:
    """Итог валидации набора примеров одной карточки.

    ``status``:
    - ``ok`` — все ожидаемые ``# → …`` совпали с фактическим выводом;
    - ``mismatch`` — хотя бы одно ожидание разошлось;
    - ``error`` — скрипт исполнился с ошибкой времени выполнения (raise/timeout);
    - ``unverifiable`` — нельзя сверить прогоном: нет ``# → …``; примеры не
      компилируются как единый скрипт (многострочный блок хранится построчно без
      отступов — типично для legacy-карточек); либо число ожиданий не совпало с
      числом строк вывода.
    """

    status: str
    detail: str = ""
    pairs: list[tuple[str, str]] = field(default_factory=list)  # (expected, actual)


def _run_snippet(script: str, timeout: float) -> tuple[bool, str, str]:
    """Исполнить сниппет в изолированном subprocess.

    Возвращает ``(ok, stdout, err)``: ``ok`` — код завершился 0; ``err`` —
    краткая причина сбоя (stderr/таймаут) для отчёта. Сеть не нужна примерам
    stdlib; ``-I`` — изолированный режим (без user-site/env).

    ``-X utf8`` обязателен: без него дочерний интерпретатор печатает в
    кодировке консоли, и пример с не-ASCII выводом (``'♠'``, кириллица) падал
    бы на Windows с ``UnicodeEncodeError`` — вердикт говорил бы о кодовой
    странице терминала, а не о примере. Флагом, а не ``PYTHONIOENCODING``,
    потому что ``-I`` игнорирует переменные окружения (#745).
    """
    with tempfile.TemporaryDirectory(prefix="glossary-ex-") as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                # Пример может писать сырые байты (bytes-API os/io) — на битой
                # последовательности чтение вывода не должно падать.
                errors="replace",
                timeout=timeout,
                cwd=tmp,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "", f"timeout > {timeout:g}s"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return False, proc.stdout, tail[-1] if tail else f"exit code {proc.returncode}"
    return True, proc.stdout, ""


def exception_name(expected: str) -> str | None:
    """Имя исключения, если ожидание — это оно (``TypeError``), иначе ``None``.

    Карточки показывают намеренное падение, записывая после ``# → `` имя
    исключения — своё (``ValueError``) или с модулем
    (``statistics.StatisticsError``). Отличаем такое ожидание от обычного
    значения по CamelCase-имени с характерным суффиксом либо по совпадению со
    встроенным исключением.
    """
    text = expected.strip()
    match = _EXCEPTION_NAME_RE.match(text)
    if match is None:
        return None
    name = match.group("name")
    if name.endswith(_EXCEPTION_SUFFIXES):
        return name
    builtin = getattr(builtins, name, None)
    if isinstance(builtin, type) and issubclass(builtin, BaseException):
        return name
    # Имя с модулем (io.UnsupportedOperation) — печатается коротким именем.
    return name if "." in text else None


def _expected_mentions_failure(err: str, expected: list[str]) -> bool:
    """Названо ли упавшее исключение в ожиданиях примера.

    ``err`` — последняя строка stderr (``TypeError: Can't instantiate …``).
    Берём из неё имя типа и ищем его в ожиданиях: если карточка его называет,
    падение показано намеренно, пусть и записано свободнее, чем чистым именем.
    """
    head = err.split(":", 1)[0].strip().split(".")[-1]
    if not head or not head[:1].isupper():
        return False
    return any(head in want for want in expected)


def _wrap_statementwise(script: str) -> str:
    """Обернуть сниппет: исполнять по операторам, печатая пойманные исключения.

    Пример с ожиданием ``# → TypeError`` падает намеренно, и падение — часть
    демонстрации. Без обёртки скрипт на нём обрывается: сверить нечего, а
    вердикт (``error``) говорит о поломке, которой нет. Обёртка исполняет
    операторы по одному в общем пространстве имён (ровно та семантика, что
    описана у ``validate_examples``) и печатает имя исключения вместо обрыва,
    поэтому строки после демонстрации тоже проверяются (#745).
    """
    return (
        "import ast as _ast\n"
        f"_src = {script!r}\n"
        "_g = {'__name__': '__main__'}\n"
        "for _node in _ast.parse(_src).body:\n"
        "    try:\n"
        "        exec(compile(_ast.Module([_node], []), '<example>', 'exec'), _g)\n"
        "    except BaseException as _exc:\n"
        "        print(type(_exc).__name__)\n"
    )


def validate_examples(examples: list[str], *, timeout: float = _DEFAULT_TIMEOUT) -> ExampleReport:
    """Проверить примеры прогоном и сверкой ``# → …`` с фактическим stdout.

    Примеры объединяются в один скрипт (общее пространство имён, как
    последовательность операторов) и исполняются. Ожидаемые значения
    сопоставляются со строками вывода **по порядку**; при расхождении числа —
    ``unverifiable`` (честно: нельзя однозначно выровнять), а не тихое смещение.

    Ожидание ``raises <Тип>`` означает, что пример падает намеренно: скрипт
    исполняется в обёртке, печатающей имя пойманного исключения (#745).
    """
    if not examples:
        return ExampleReport("unverifiable", "нет примеров")
    expected = extract_expected(examples)
    if not expected:
        return ExampleReport("unverifiable", "нет маркеров '# → …'")

    script = "\n".join(examples)
    try:
        compile(script, "<example>", "exec")
    except SyntaxError as exc:
        # Не единый исполнимый скрипт (обычно построчно хранимый блок без
        # отступов) — сверить прогоном нельзя, это не runtime-ошибка кода.
        return ExampleReport("unverifiable", f"не компилируется: {type(exc).__name__}")
    shows_exception = any(exception_name(want) for want in expected)
    ok, stdout, err = _run_snippet(
        _wrap_statementwise(script) if shows_exception else script, timeout
    )
    if not ok and not shows_exception and _expected_mentions_failure(err, expected):
        # Падение названо в ожиданиях, но не чистым именем («TypeError (класс
        # абстрактный)», «io.UnsupportedOperation») — значит демонстрация
        # намеренная, просто записана свободнее. Повторяем в обёртке.
        ok, stdout, err = _run_snippet(_wrap_statementwise(script), timeout)
    if not ok:
        return ExampleReport("error", err)

    actual_lines = stdout.splitlines()
    if len(actual_lines) != len(expected):
        return ExampleReport(
            "unverifiable",
            f"строк вывода {len(actual_lines)} ≠ ожиданий {len(expected)}",
        )

    pairs = list(zip(expected, actual_lines, strict=True))
    bad = [(e, a) for e, a in pairs if not compare_expected_actual(e, a)]
    if bad:
        detail = "; ".join(f"ждали {e!r}, получили {a!r}" for e, a in bad[:3])
        return ExampleReport("mismatch", detail, pairs)
    return ExampleReport("ok", f"сверено {len(pairs)}", pairs)


def _example_trees(examples: list[str]) -> list[ast.Module]:
    """Разобрать примеры в AST: блоком целиком, а если не компилируется — построчно.

    Построчный разбор нужен legacy-карточкам: многострочный блок хранится в
    ``examples`` без отступов и как единый скрипт не компилируется (тот же случай,
    что даёт ``unverifiable`` у ``validate_examples``). Нераспознанные строки
    просто пропускаются — задача разбора здесь не исполнить пример, а увидеть,
    к какому API он обращается.
    """
    try:
        return [ast.parse("\n".join(examples))]
    except SyntaxError:
        pass
    trees: list[ast.Module] = []
    for line in examples:
        try:
            trees.append(ast.parse(line))
        except SyntaxError:
            continue
    return trees


def _added_after_runtime(version: str) -> bool:
    """Появилось ли API позже текущего интерпретатора (``version`` вида ``3.14``).

    Отделяет версионный разрыв от платформенного: на 3.12 нет ни ``os.fork``
    (Windows), ни ``os.readinto`` (появился в 3.14), но тег ``platform:posix``
    уместен только первому. Неразобранное или пустое поле — «доступно»: молча
    считать API будущим опаснее, чем проверить его лишний раз.
    """
    parts = version.strip().split(".")[:2]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    return (int(parts[0]), int(parts[1])) > sys.version_info[:2]


@functools.cache
def _platform_module(name: str) -> ModuleType | None:
    """Импортировать системный модуль из ``_PLATFORM_MODULES`` (или ``None``)."""
    if name not in _PLATFORM_MODULES:
        return None
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _guarded_attrs(tree: ast.Module) -> set[str]:
    """Имена, укрытые в ``hasattr(mod, 'x')`` / ``getattr(mod, 'x', …)``.

    Так написан OS-робастный пример батчей В5: ``not hasattr(os, 'fork') or
    callable(os.fork)`` печатает ``True`` на любой ОС. Упоминание имени там —
    не обращение к нему, и тега такой карточке не требуется.
    """
    guarded: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("hasattr", "getattr")
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            guarded.add(f"{node.args[0].id}.{node.args[1].value}")
    return guarded


def platform_gaps(examples: list[str], *, known_api: Mapping[str, str] | None = None) -> set[str]:
    """К какому недоступному на **этой** платформе API обращаются примеры.

    Возвращает имена вида ``os.fork`` (атрибут системного модуля, которого здесь
    нет) и ``import pwd`` (модуль, которого вне POSIX нет вовсе). Пустое
    множество — примеры переносимы, тег ``platform:posix`` карточке не нужен.

    Признак машинный, а не вычитанный из русского текста ожидания: имя ищется
    разбором кода примера (AST) и сверяется с фактическим составом модуля через
    ``hasattr``. Поэтому вне POSIX проверка ловит пропущенный тег (`os.fork` на
    Windows), а на POSIX остаются только заведомо непереносимые импорты
    (`termios`) — там сами примеры и исполняются полностью.

    ``known_api`` — реальное API базы: ``id`` карточки → её поле ``version``
    («доступно с Python X.Y», пусто — всегда). Без него «отсутствует здесь»
    неотличимо ни от «не существует нигде» (намеренная демонстрация
    ``from os import nonexistent  # ImportError`` сошла бы за платформенную), ни
    от «появится в следующей версии» (``os.readinto`` есть с 3.14, и на 3.12 его
    нет на **любой** ОС — тег ``platform:posix`` там не при чём). Модули фильтру
    не подлежат: их список закрытый.

    Учитываются только модули, импортированные в этих же примерах: иначе
    ``from datetime import time`` + ``time.fromisoformat(...)`` принимался бы за
    обращение к модулю ``time``. Имена под ``hasattr``/``getattr`` не считаются
    обращением (см. ``_guarded_attrs``).
    """

    def is_real(name: str) -> bool:
        if known_api is None:
            return True
        version = known_api.get(name)
        return version is not None and not _added_after_runtime(version)

    gaps: set[str] = set()
    for tree in _example_trees(examples):
        guarded = _guarded_attrs(tree)
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    modules.add(alias.asname or root)
                    if root in _POSIX_ONLY_MODULES:
                        gaps.add(f"import {root}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                if root in _POSIX_ONLY_MODULES:
                    gaps.add(f"import {root}")
                    continue
                module = _platform_module(root)
                if module is not None:
                    gaps.update(
                        f"{root}.{alias.name}"
                        for alias in node.names
                        if not hasattr(module, alias.name) and is_real(f"{root}.{alias.name}")
                    )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            name = node.value.id
            key = f"{name}.{node.attr}"
            module = _platform_module(name) if name in modules else None
            if (
                module is not None
                and key not in guarded
                and not hasattr(module, node.attr)
                and is_real(key)
            ):
                gaps.add(key)
    return gaps


@dataclass
class ProposedContent:
    """Предложенный контент карточки (то, что заполнил бы человек/модель)."""

    summary: str = ""
    summary_en: str = ""
    examples: list[str] = field(default_factory=list)


class DraftProvider(Protocol):
    """Источник предложенного контента для B1-черновика (сменная точка).

    Дефолт — ``OfflineDraftProvider`` (без модели). Реальный BYOK-LLM
    (эпик AI #434/#435) реализует тот же протокол, подключается **opt-in** и не
    входит в этот dev-инструмент.
    """

    def propose(self, item: StdlibItem) -> ProposedContent:
        """Вернуть предложенные summary (RU/EN) и примеры для сущности."""
        ...


class OfflineDraftProvider:
    """Провайдер без LLM: EN-summary из docstring, RU-summary/примеры — слоты.

    Детерминирован и офлайн. Примеры **не** выдумываются (для этого нужна
    модель) — их вписывает человек/BYOK-провайдер; конвейер их затем валидирует.
    Опционально принимает ``overrides`` (напр. контент из ``--content-file``),
    имитирующие выход модели.
    """

    def __init__(self, overrides: ProposedContent | None = None) -> None:
        self._overrides = overrides

    def propose(self, item: StdlibItem) -> ProposedContent:
        """EN-summary из первого предложения docstring; RU — пусто (под ревью)."""
        if self._overrides is not None:
            return self._overrides
        obj = _gen.resolve_object(item)
        doc = ""
        if obj is not None:
            import inspect

            doc = (inspect.getdoc(obj) or "").split("\n\n", 1)[0].strip()
        summary_en = doc.split(". ", 1)[0].strip().rstrip(".") if doc else ""
        return ProposedContent(summary="", summary_en=summary_en, examples=[])


def build_b1_draft(item: StdlibItem, content: ProposedContent) -> GlossaryCard:
    """Собрать ``GlossaryCard(status="draft")`` по шаблону волны B1.

    Каркас (id/kind/syntax/body/docs_url/section) — из интроспекции
    ``generate_draft_cards`` (единый источник, без дубля правил); поверх — B1-поля
    ``summary``/``summary_en``/``examples`` из ``content``. Тег ``b1-pipeline``
    метит карточки, прошедшие этот конвейер, отдельно от голых ``autodraft``.
    """
    base = _gen.draft_card(item)
    base.summary = content.summary
    base.summary_en = content.summary_en
    base.examples = list(content.examples)
    base.tags = sorted({*base.tags, "b1-pipeline"})
    return base


def review_diff(new_card: GlossaryCard, base_dir: Path) -> str:
    """Unified-diff предложенной карточки против её версии в базе (или пустой).

    Для ручного ревью: показывает, что изменится, **ничего не записывая**. Если
    в базе карточки с таким ``id`` нет — diff от пустого (полностью новая).
    """
    provider = JsonGlossaryProvider.from_directory(base_dir)
    old = next((c for c in provider.all() if c.id == new_card.id), None)
    old_text = json.dumps(old.to_dict(), ensure_ascii=False, indent=2).splitlines() if old else []
    new_text = json.dumps(new_card.to_dict(), ensure_ascii=False, indent=2).splitlines()
    label = new_card.id
    diff = difflib.unified_diff(
        old_text,
        new_text,
        fromfile=f"base/{label}" if old else "base/<новая>",
        tofile=f"proposed/{label}",
        lineterm="",
    )
    return "\n".join(diff)


def _item_for_qualname(qualname: str) -> StdlibItem | None:
    """Найти ``StdlibItem`` инвентаря по полному qualname (или None)."""
    for item in build_stdlib_inventory():
        if item.qualname == qualname:
            return item
    return None


def _worker_count(jobs: int | None) -> int:
    """Сколько примеров валидировать одновременно (явное ``--jobs`` или по CPU).

    Воркер почти всё время ждёт дочерний интерпретатор (старт процесса — это
    I/O, а не счёт), поэтому потоков берём чуть больше числа ядер; верхняя
    граница — чтобы на многоядерной машине не плодить сотню процессов разом.
    """
    if jobs is not None:
        return max(1, jobs)
    return min(32, (os.cpu_count() or 1) + 4)


def run_check(
    base_dir: Path,
    *,
    only_ready: bool = True,
    timeout: float = _DEFAULT_TIMEOUT,
    jobs: int | None = None,
) -> int:
    """Аудит примеров карточек базы: прогон + сверка ``# → …``. Печать сводки.

    Возвращает число карточек со статусом ``mismatch``/``error`` (для CI/exit
    можно трактовать как «требуют внимания»). ``unverifiable`` и ``ok`` —
    не проблема (первое — нечего/нельзя сверить, второе — сошлось).

    Карточки валидируются параллельно (``jobs`` воркеров): на каждую уходит
    отдельный subprocess, и последовательный обход рос линейно с базой —
    к 1300+ карточкам это минуты на Windows, где процессы дороже. Прогоны
    независимы (свой subprocess, свой cwd), а вывод остаётся детерминированным:
    отчёты собираются в порядке отсортированного списка карточек, не в порядке
    завершения.

    Карточки с тегом ``platform:posix`` вне POSIX пропускаются (``skipped``):
    их примеры зовут API, которого на этой ОС просто нет, и вердикт говорил бы
    об операционной системе прогона, а не о качестве карточки (#745).

    Обратный случай — примеры зовут недоступное здесь API, а тега нет — попадает
    в отчёт строкой ``[нет тега]``: без неё такая карточка выглядела бы обычным
    ``mismatch``, и правкой ожидания «под текущую ОС» её ломали бы на остальных
    (#762).
    """
    provider = JsonGlossaryProvider.from_directory(base_dir)
    cards = sorted(
        (c for c in provider.all() if c.examples and (not only_ready or c.status == "ready")),
        key=lambda c: c.id,
    )
    runnable = [c for c in cards if os.name == "posix" or _POSIX_ONLY_TAG not in c.tags]
    counts = {"ok": 0, "mismatch": 0, "error": 0, "unverifiable": 0}
    skipped = len(cards) - len(runnable)
    flagged: list[tuple[str, ExampleReport]] = []
    with ThreadPoolExecutor(max_workers=_worker_count(jobs)) as pool:
        reports = pool.map(lambda c: validate_examples(c.examples, timeout=timeout), runnable)
        for card, report in zip(runnable, reports, strict=True):
            counts[report.status] += 1
            if report.status in ("mismatch", "error"):
                flagged.append((card.id, report))

    print(f"Проверено карточек с примерами: {len(runnable)} из {len(cards)}")
    print(
        f"  ok={counts['ok']}  mismatch={counts['mismatch']}  "
        f"error={counts['error']}  unverifiable={counts['unverifiable']}  "
        f"skipped={skipped} (только POSIX)"
    )
    for card_id, report in flagged:
        print(f"  [{report.status}] {card_id}: {report.detail}")
    known_api = {c.id: c.version for c in provider.all()}
    for card in runnable:
        # На POSIX в runnable попадают и помеченные карточки — им подсказка не нужна.
        if _POSIX_ONLY_TAG in card.tags:
            continue
        gaps = platform_gaps(card.examples, known_api=known_api)
        if gaps:
            names = ", ".join(sorted(gaps))
            print(f"  [нет тега] {card.id}: зовёт недоступное здесь ({names}) — {_POSIX_ONLY_TAG}?")
    return counts["mismatch"] + counts["error"]


def run_propose(
    qualname: str,
    *,
    base_dir: Path,
    content_file: Path | None = None,
    out_file: Path | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> int:
    """Предложить B1-черновик для ``qualname``: валидация примеров + review-diff.

    ``content_file`` (JSON ``{summary, summary_en, examples}``) имитирует выход
    модели/человека; без него — офлайн-каркас (пустые RU-summary/примеры).
    Запись — только при ``out_file`` и **только** в отдельный draft-файл
    (``status=draft``), никогда в ready-базу и никогда автоматически.
    """
    item = _item_for_qualname(qualname)
    if item is None:
        print(f"В инвентаре stdlib нет '{qualname}'", file=sys.stderr)
        return 2

    overrides: ProposedContent | None = None
    if content_file is not None:
        data = json.loads(content_file.read_text(encoding="utf-8"))
        overrides = ProposedContent(
            summary=str(data.get("summary", "")),
            summary_en=str(data.get("summary_en", "")),
            examples=[str(x) for x in data.get("examples", [])],
        )
    provider: DraftProvider = OfflineDraftProvider(overrides)
    content = provider.propose(item)
    card = build_b1_draft(item, content)

    report = validate_examples(card.examples, timeout=timeout)
    print(f"Валидация примеров: [{report.status}] {report.detail}")
    print("--- review diff (ничего не записано) ---")
    print(review_diff(card, base_dir) or "(изменений нет)")

    if out_file is not None:
        if report.status in ("mismatch", "error"):
            print(
                f"\nНе записано в {out_file}: примеры не прошли валидацию "
                f"([{report.status}]). Почини примеры и повтори.",
                file=sys.stderr,
            )
            return 1
        _append_draft(card, out_file)
        print(f"\nЗаписан черновик (status=draft) в {out_file} — под ручное ревью.")
    return 0


def _append_draft(card: GlossaryCard, out_file: Path) -> None:
    """Добавить/обновить черновик в отдельном draft-файле (не ready-база)."""
    cards: dict[str, GlossaryCard] = {}
    if out_file.exists():
        for existing in JsonGlossaryProvider.from_file(out_file).all():
            cards[existing.id] = existing
    cards[card.id] = card
    ordered = sorted(cards.values(), key=lambda c: c.id)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps([c.to_dict() for c in ordered], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: подкоманды ``check`` (аудит примеров) и ``propose`` (B1-черновик)."""
    parser = argparse.ArgumentParser(
        description="Полуавтоматический конвейер черновиков глоссария (issue #438)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Аудит примеров карточек базы прогоном")
    p_check.add_argument("--base", type=Path, default=BUNDLED_GLOSSARY_DIR, help="Каталог базы")
    p_check.add_argument(
        "--all-statuses",
        action="store_true",
        help="Проверять карточки любого статуса (по умолчанию — только ready)",
    )
    p_check.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT, help="Секунд на пример")
    p_check.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Параллельных прогонов (по умолчанию — по числу ядер; 1 = последовательно)",
    )

    p_prop = sub.add_parser("propose", help="Предложить B1-черновик qualname")
    p_prop.add_argument("--qualname", required=True, help="Полный qualname, напр. str.rjust")
    p_prop.add_argument("--base", type=Path, default=BUNDLED_GLOSSARY_DIR, help="Каталог базы")
    p_prop.add_argument(
        "--content-file", type=Path, default=None, help="JSON с summary/summary_en/examples"
    )
    p_prop.add_argument(
        "--write", type=Path, default=None, dest="out_file", help="Файл черновиков (не ready-база)"
    )
    p_prop.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT, help="Секунд на пример")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        if not args.base.is_dir():
            parser.error(f"База не найдена: {args.base}")
        run_check(
            args.base,
            only_ready=not args.all_statuses,
            timeout=args.timeout,
            jobs=args.jobs,
        )
        return 0
    if not args.base.is_dir():
        parser.error(f"База не найдена: {args.base}")
    if args.content_file is not None and not args.content_file.is_file():
        parser.error(f"Контент-файл не найден: {args.content_file}")
    return run_propose(
        args.qualname,
        base_dir=args.base,
        content_file=args.content_file,
        out_file=args.out_file,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
