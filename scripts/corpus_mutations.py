#!/usr/bin/env python3
"""scripts/corpus_mutations.py — каталог мутаций решения для прогонного корпуса.

Мутация — детерминированная порча заведомо верного решения, у которой **заранее
известен вердикт**. Прогонный корпус (`scripts/corpus_run.py`) применяет каталог
к каждому эталону и сверяет фактический вердикт грейдера с ожидаемым: любое
расхождение — дефект ядра, а не решения.

Зачем детерминированные мутации, а не сгенерированные LLM решения: корпус
должен быть **воспроизводим**. У сгенерированного решения ошибка сегодня одна,
завтра другая, и упавший прогон нечем объяснить. Мутация же — чистая функция от
исходника: тот же вход даёт тот же вердикт на любой машине и в CI.

Каталог держит две равноценные половины, и вторая важнее первой:

- **Ожидаем не-AC** (`timeout`, `runtime_error`, `no_output`, …) — грейдер
  обязан *поймать* порчу. Расхождение здесь = ложный AC, худший из возможных
  дефектов учебного инструмента: студент считает задачу сданной, Stepik её не
  принимает.
- **Ожидаем AC** (`float_noise`, `crlf_newlines`) — грейдер обязан *простить*
  то, что политика сравнения объявила незначимым. Расхождение здесь = ложный
  WA: студент правит верное решение и теряет доверие к инструменту.

Ожидания опираются на реальную политику сравнения
(`core/grader_core.py` — построчное равенство с фолбэком на
`normalizers.normalize_floats` при совпадении числа строк), а не на общие
соображения. Меняется политика — меняется каталог, и это осознанный шаг.

Не все мутации применимы ко всякой задаче: перевод вывода в верхний регистр
ничего не испортит в числовом выводе. Поэтому у мутации есть предикат
применимости по ожидаемым строкам эталона (:attr:`Mutation.requires`) и по
исходнику эталона (:attr:`Mutation.requires_source`), а раннер пропускает
неприменимые — вместо того чтобы записать заведомо ложное ожидание.

Два семейства мутаций (:attr:`Mutation.family`), и они дают разное
(issue #1057):

- ``output`` — механическая порча вывода: лишняя строка, пробел, регистр,
  перевод строки. Студент так не ошибается, зато порча гарантированно видна в
  выводе, поэтому вердикт предсказуем **точно**, и именно это семейство даёт
  гарантию «ложный AC будет пойман».
- ``algorithmic`` — ошибка в самом решении: off-by-one, сдвинутая граница
  условия, не то деление, потерянное между итерациями состояние, обратный
  порядок. Так ошибаются люди, и это материал для «Подучить» и для проверки
  диагностики: на off-by-one грейдер обязан показать понятный diff, а не просто
  ``WA``.

За семейство ``algorithmic`` приходится платить двумя послаблениями, и оба
записаны явно, а не спрятаны:

1. **Вердикт не один.** Сужение диапазона обычно даёт ``WA``, но там, где по
   диапазону идёт индексация, — ``RE``. Поэтому у мутации есть
   :attr:`Mutation.alternatives`: набор равно приемлемых вердиктов. Проверяется
   «ядро не выдало AC на сломанном решении», а конкретный вердикт зависит от
   задачи.
2. **Мутация может не проявиться.** Сдвиг границы ``>=`` → ``>`` ничего не
   изменит, если на тестовых данных граница не достигается. Такой исход — не
   дефект ядра и не успех проверки; раннер (`scripts/corpus_run.py`) отмечает
   его отдельно как нейтральный.

Алгоритмические мутации правятся через AST, а не текстовой заменой: замена по
регулярному выражению рвёт синтаксис на первом же совпадении внутри строкового
литерала, а разобранное дерево либо меняется корректно, либо не меняется вовсе.
Предикат применимости из той же трансформации и получается — «применимо» здесь
буквально значит «в этом исходнике есть что менять».

Чего в каталоге намеренно нет: необработанного крайнего случая (пустой ввод,
один элемент). Это мутация **данных**, а не решения, — в терминах
`Mutation.transform` она невыразима, и её место в наборе тест-кейсов задачи.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from stepik_grader.core.result import Verdict

__all__ = [
    "MUTATIONS",
    "Mutation",
    "MutationFamily",
    "applicable_mutations",
    "mutation_by_key",
]

MutationFamily = Literal["output", "algorithmic"]


def _stdout_filter(expression: str) -> str:
    """Собрать префикс, пропускающий весь stdout решения через ``expression``.

    ``expression`` — выражение Python над именем ``text`` (полный вывод решения),
    например ``text.upper()``. Фильтр буферизует вывод целиком и преобразует его
    один раз на выходе, а не по каждому ``write``: ``print("a", "b")`` дробится
    на четыре вызова ``write``, и трансформация каждого куска порознь дала бы
    непредсказуемый результат.

    Результат пишется в ``sys.stdout.buffer`` готовыми UTF-8 байтами, а не
    текстом: текстовый ``sys.stdout`` на Windows транслирует ``\\n`` в
    ``\\r\\n``, и мутации про переводы строк (``crlf_newlines``,
    ``vertical_tab``) выдавали бы там другой байтовый поток, чем задуман. Ядро
    читает вывод именно как сырые байты и декодирует их UTF-8
    (``grader_core._map_outcome_to_result``), так что байтовая запись — точное
    зеркало того, что увидит сравнение.

    Args:
        expression: выражение над ``text``, возвращающее итоговый вывод.

    Returns:
        Python-код префикса, который дописывается перед исходником решения.
    """
    return (
        "import atexit as _mut_atexit, io as _mut_io, sys as _mut_sys\n"
        "_mut_real = _mut_sys.stdout\n"
        "_mut_buf = _mut_io.StringIO()\n"
        "_mut_sys.stdout = _mut_buf\n"
        "def _mut_flush():\n"
        "    text = _mut_buf.getvalue()\n"
        f"    _mut_real.buffer.write(({expression}).encode('utf-8'))\n"
        "    _mut_real.buffer.flush()\n"
        "_mut_atexit.register(_mut_flush)\n"
    )


def _float_noise_filter() -> str:
    """Собрать префикс, подмешивающий шум в младшие разряды каждого float'а.

    Шум **относительный** (``x * (1 + 1e-13)``), а не абсолютный: он меняет
    представление числа (``3.14`` → ``3.1400000000003``), но исчезает при
    округлении до 9 знаков — именно так ядро нормализует обе стороны сравнения
    (`normalizers.normalize_floats`). Поэтому корректный грейдер обязан оставить
    вердикт AC; WA здесь означает, что фолбэк нормализации не работает.

    Абсолютная прибавка тут была бы неверна, и корпус это показал на задаче со
    средней температурой ``0.0``: ``0.0 + 1e-13`` даёт ``1e-13`` — не «шум в
    младших разрядах», а другое число, к тому же в научной нотации, которую
    ``_FLOAT_RE`` не матчит (нет десятичной точки). Ядро законно отвечало WA,
    ложным было ожидание. Относительный шум оставляет ноль нулём.
    """
    return "import re as _mut_re\n" + _stdout_filter(
        "_mut_re.sub(r'\\d+\\.\\d+', lambda m: repr(float(m.group()) * (1 + 1e-13)), text)"
    )


def _prefix(code: str) -> Callable[[str], str]:
    """Вернуть трансформацию «дописать ``code`` перед исходником решения»."""
    return lambda source: f"{code}\n{source}"


def _suffix(code: str) -> Callable[[str], str]:
    """Вернуть трансформацию «дописать ``code`` после исходника решения»."""
    return lambda source: f"{source}\n{code}\n"


def _always(expected_lines: Sequence[str]) -> bool:
    """Предикат применимости: мутация годится для любой задачи."""
    return True


def _has_output(expected_lines: Sequence[str]) -> bool:
    """Предикат: эталон что-то печатает (иначе портить вывод бессмысленно)."""
    return bool(expected_lines)


def _has_case_sensitive_text(expected_lines: Sequence[str]) -> bool:
    """Предикат: в ожидании есть символы, которые изменит ``str.upper()``.

    Числовой вывод (``42``) от ``upper()`` не меняется — для такой задачи
    мутация регистра дала бы AC, и ожидание WA было бы ложным.
    """
    return any(line != line.upper() for line in expected_lines)


def _has_float(expected_lines: Sequence[str]) -> bool:
    """Предикат: в ожидании есть число с десятичной точкой.

    Только для таких задач осмысленна проверка фолбэка
    ``normalizers.normalize_floats`` (округление до 9 знаков).
    """
    return any(_looks_like_float(token) for line in expected_lines for token in line.split())


def _looks_like_float(token: str) -> bool:
    """Вернуть True, если токен разбирается как число с десятичной точкой."""
    if "." not in token:
        return False
    try:
        float(token)
    except ValueError:
        return False
    return True


def _any_source(source: str) -> bool:
    """Предикат применимости по исходнику: мутация годится для любого решения."""
    return True


# ---------------------------------------------------------------------------
# Алгоритмические мутации: правка самого решения через AST (issue #1057)
#
# Каждая — функция `rewrite(tree) -> bool`: правит ПЕРВОЕ подходящее место и
# сообщает, изменила ли что-нибудь. Из неё же получается предикат применимости
# (`_ast_requires`), поэтому «применимо» не расходится с «применилось»: обе
# стороны считает один и тот же код.
# ---------------------------------------------------------------------------

Rewrite = Callable[[ast.Module], bool]


def _ast_transform(rewrite: Rewrite) -> Callable[[str], str]:
    """Собрать трансформацию исходника из правки дерева.

    Неразбираемый или не подошедший исходник возвращается как есть — портить
    решение наугад нельзя, а неприменимость отсекается предикатом.
    """

    def transform(source: str) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source
        if not rewrite(tree):
            return source
        ast.fix_missing_locations(tree)
        return ast.unparse(tree) + "\n"

    return transform


def _ast_requires(rewrite: Rewrite) -> Callable[[str], bool]:
    """Собрать предикат применимости из той же правки дерева."""

    def requires(source: str) -> bool:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        return rewrite(tree)

    return requires


class _FirstMatch(ast.NodeTransformer):
    """База трансформеров «поправить первое вхождение и остановиться»."""

    def __init__(self) -> None:
        self.changed = False


class _RangeStopShrink(_FirstMatch):
    """``range(n)`` → ``range(n - 1)``: классический off-by-one в диапазоне."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if not self.changed and isinstance(node.func, ast.Name) and node.func.id == "range":
            if node.args:
                # У range(start, stop[, step]) сдвигать надо stop, а не step.
                index = 0 if len(node.args) == 1 else 1
                node.args[index] = ast.BinOp(
                    left=node.args[index], op=ast.Sub(), right=ast.Constant(value=1)
                )
                self.changed = True
                return node
        self.generic_visit(node)
        return node


class _FloorDivToTrueDiv(_FirstMatch):
    """``//`` → ``/``: целочисленное деление подменено обычным.

    Вывод меняется даже когда деление нацело: ``6 // 2`` печатается как ``3``,
    ``6 / 2`` — как ``3.0``.
    """

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        if not self.changed and isinstance(node.op, ast.FloorDiv):
            node.op = ast.Div()
            self.changed = True
        self.generic_visit(node)
        return node


class _BoundaryFlip(_FirstMatch):
    """``<`` ↔ ``<=`` (и ``>`` ↔ ``>=``): сдвинутая граница условия."""

    _FLIP: dict[type[ast.cmpop], type[ast.cmpop]] = {
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
    }

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if not self.changed:
            for index, op in enumerate(node.ops):
                replacement = self._FLIP.get(type(op))
                if replacement is not None:
                    node.ops[index] = replacement()
                    self.changed = True
                    break
        self.generic_visit(node)
        return node


class _SortReversed(_FirstMatch):
    """``sorted(x)`` / ``x.sort()`` → тот же вызов с ``reverse=True``."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if not self.changed and self._is_sort_call(node):
            if not any(keyword.arg == "reverse" for keyword in node.keywords):
                node.keywords.append(ast.keyword(arg="reverse", value=ast.Constant(value=True)))
                self.changed = True
                return node
        self.generic_visit(node)
        return node

    @staticmethod
    def _is_sort_call(node: ast.Call) -> bool:
        if isinstance(node.func, ast.Name):
            return node.func.id == "sorted"
        return isinstance(node.func, ast.Attribute) and node.func.attr == "sort"


def _rewrite_with(transformer: _FirstMatch, tree: ast.Module) -> bool:
    """Прогнать трансформер по дереву и вернуть, изменил ли он что-нибудь."""
    transformer.visit(tree)
    return transformer.changed


def _rewrite_accumulator_reset(tree: ast.Module) -> bool:
    """Перенести инициализацию накопителя внутрь цикла: потеря состояния.

    Ищет пару «``total = <константа>``, следом цикл, использующий ``total``» и
    вставляет копию присваивания первой строкой тела. Накопитель обнуляется на
    каждой итерации — до цикла он остаётся, поэтому решение синтаксически цело
    и падает не импортом, а ответом.
    """
    for node in ast.walk(tree):
        for field_name in ("body", "orelse", "finalbody"):
            statements = getattr(node, field_name, None)
            if not isinstance(statements, list):
                continue
            for index in range(len(statements) - 1):
                init, loop = statements[index], statements[index + 1]
                if not isinstance(loop, ast.For | ast.While):
                    continue
                name = _constant_assignment_target(init)
                if name is None or not _name_is_read_in(loop.body, name):
                    continue
                loop.body.insert(0, copy.deepcopy(init))
                return True
    return False


def _constant_assignment_target(statement: ast.stmt) -> str | None:
    """Имя из ``name = <константа>``; ``None``, если это другой оператор."""
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None
    target = statement.targets[0]
    if not isinstance(target, ast.Name) or not isinstance(statement.value, ast.Constant):
        return None
    return target.id


def _name_is_read_in(statements: Sequence[ast.stmt], name: str) -> bool:
    """Читается ли ``name`` внутри этих операторов (а не только присваивается)."""
    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                return True
    return False


@dataclass(frozen=True)
class Mutation:
    """Одна мутация: как испортить решение и какой вердикт обязан выдать грейдер.

    Attributes:
        key: идентификатор для CLI и отчётов.
        title: краткое человекочитаемое название.
        expected: вердикт, который грейдер обязан выдать на мутанте.
        checks: что именно в грейдере проверяет эта мутация (текст для отчёта).
        transform: чистая трансформация исходника эталона.
        requires: предикат применимости по ожидаемым строкам эталона.
        requires_source: предикат применимости по исходнику эталона — для
            алгоритмических мутаций «есть что менять» решается только по коду.
        family: семейство мутации (см. docstring модуля).
        alternatives: прочие приемлемые вердикты сверх :attr:`expected`; у
            алгоритмических мутаций точный вердикт зависит от задачи.
    """

    key: str
    title: str
    expected: Verdict
    checks: str
    transform: Callable[[str], str]
    requires: Callable[[Sequence[str]], bool] = field(default=_always)
    requires_source: Callable[[str], bool] = field(default=_any_source)
    family: MutationFamily = "output"
    alternatives: tuple[Verdict, ...] = ()

    @property
    def accepted(self) -> tuple[Verdict, ...]:
        """Все вердикты, которые считаются совпадением с ожиданием."""
        return (self.expected, *self.alternatives)

    @property
    def may_be_neutral(self) -> bool:
        """Может ли мутация не проявиться на данных конкретной задачи.

        Порча вывода видна всегда, ошибка в алгоритме — только если тестовые
        данные её задевают. Раннер по этому флагу отличает «мутация не
        проявилась» от «ядро проглядело поломку».
        """
        return self.family == "algorithmic"

    def apply(self, source: str) -> str:
        """Применить мутацию к исходнику эталона."""
        return self.transform(source)

    def applies_to(self, expected_lines: Sequence[str], source: str = "") -> bool:
        """Вернуть True, если мутация осмысленна для этой задачи.

        Args:
            expected_lines: ожидаемый вывод эталона.
            source: исходник эталона; пустая строка означает «исходник
                неизвестен» — тогда мутации, которым нужен код, отсеиваются, а
                не применяются вслепую.
        """
        return self.requires(expected_lines) and self.requires_source(source)


# Порядок каталога — от грубых поломок к тонким: так отчёт читается сверху вниз
# по возрастанию хитрости дефекта, который мутация ловит.
MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        key="timeout",
        title="бесконечный цикл до вывода",
        expected="TLE",
        checks="срабатывание таймаута и снятие зависшего процесса",
        transform=_prefix("while True:\n    pass"),
    ),
    Mutation(
        key="runtime_error",
        title="исключение до вывода",
        expected="RE",
        checks="перехват ненулевого кода возврата и текста трейсбека",
        transform=_prefix("raise RuntimeError('corpus mutation')"),
    ),
    Mutation(
        key="syntax_error",
        title="синтаксическая ошибка",
        expected="RE",
        checks="решение, которое не компилируется, не выдаётся за верное",
        transform=_prefix("def ("),
    ),
    Mutation(
        key="no_output",
        title="пустой вывод",
        expected="WA",
        checks="пустой вывод не совпадает с непустым ожиданием",
        transform=_prefix(_stdout_filter("''")),
        requires=_has_output,
    ),
    Mutation(
        key="extra_line",
        title="лишняя строка перед ответом",
        expected="WA",
        checks="лишняя строка ловится, а не съедается сравнением",
        transform=_prefix("print('corpus mutation')"),
    ),
    Mutation(
        key="dropped_last_line",
        title="потеряна последняя строка",
        expected="WA",
        checks="недобор строк ловится (частый дефект — обрыв цикла вывода)",
        transform=_prefix(_stdout_filter("''.join(text.splitlines(keepends=True)[:-1])")),
        requires=_has_output,
    ),
    Mutation(
        key="blank_line_append",
        title="лишняя пустая строка в конце",
        expected="WA",
        checks="хвостовая пустая строка значима (сверх завершающего перевода)",
        transform=_suffix("print()"),
        requires=_has_output,
    ),
    Mutation(
        key="trailing_space",
        title="хвостовой пробел в каждой строке",
        expected="WA",
        checks="хвостовые пробелы значимы — сравнение построчно строгое",
        transform=_prefix(_stdout_filter("text.replace('\\n', ' \\n')")),
        requires=_has_output,
    ),
    Mutation(
        key="upper_case",
        title="ответ в верхнем регистре",
        expected="WA",
        checks="регистр значим",
        transform=_prefix(_stdout_filter("text.upper()")),
        requires=_has_case_sensitive_text,
    ),
    Mutation(
        key="vertical_tab",
        title="вертикальная табуляция вместо перевода строки",
        expected="WA",
        checks=(
            "регрессия issue #843: VT/FF и прочие управляющие символы — данные "
            "внутри строки, а не разделители; иначе неверный вывод получал AC"
        ),
        transform=_prefix(_stdout_filter("text.replace('\\n', '\\x0b', 1)")),
        requires=_has_output,
    ),
    # Ниже — мутации, которые грейдер обязан ПРОСТИТЬ: политика сравнения
    # объявила эти различия незначимыми. Ожидание AC здесь так же строго, как
    # ожидание WA выше: ложный WA прогоняет студента по кругу над верным кодом.
    Mutation(
        key="float_noise",
        title="шум в младших разрядах float",
        expected="AC",
        checks="фолбэк normalize_floats (округление до 9 знаков) действительно работает",
        transform=_prefix(_float_noise_filter()),
        requires=_has_float,
    ),
    Mutation(
        key="crlf_newlines",
        title="переводы строк в стиле Windows",
        expected="AC",
        checks="CRLF-поток разбирается так же, как LF (кроссплатформенность)",
        transform=_prefix(_stdout_filter("text.replace('\\n', '\\r\\n')")),
        requires=_has_output,
    ),
    # Ниже — семейство `algorithmic` (issue #1057): ошибка в самом решении, а не
    # порча вывода. Так ошибаются люди, поэтому эти мутации проверяют не только
    # вердикт, но и внятность диагностики. Вердикт задан набором (`alternatives`),
    # а не одним значением, и AC на них означает «не проявилась на этих данных»,
    # а не «поймали дефект ядра» — см. docstring модуля.
    Mutation(
        key="off_by_one_range",
        title="off-by-one: диапазон короче на единицу",
        expected="WA",
        checks="самая частая ошибка новичка ловится, а не выдаётся за верное решение",
        transform=_ast_transform(lambda tree: _rewrite_with(_RangeStopShrink(), tree)),
        requires_source=_ast_requires(lambda tree: _rewrite_with(_RangeStopShrink(), tree)),
        family="algorithmic",
        alternatives=("RE",),
    ),
    Mutation(
        key="int_division_swap",
        title="обычное деление вместо целочисленного",
        expected="WA",
        checks="подмена // на / ловится: 3 и 3.0 — разный вывод, а не «то же число»",
        transform=_ast_transform(lambda tree: _rewrite_with(_FloorDivToTrueDiv(), tree)),
        requires_source=_ast_requires(lambda tree: _rewrite_with(_FloorDivToTrueDiv(), tree)),
        family="algorithmic",
        alternatives=("RE",),
    ),
    Mutation(
        key="boundary_flip",
        title="сдвинутая граница условия (< ↔ <=)",
        expected="WA",
        checks="ошибка на границе диапазона ловится там, где тесты её задевают",
        transform=_ast_transform(lambda tree: _rewrite_with(_BoundaryFlip(), tree)),
        requires_source=_ast_requires(lambda tree: _rewrite_with(_BoundaryFlip(), tree)),
        family="algorithmic",
        alternatives=("RE",),
    ),
    Mutation(
        key="reversed_order",
        title="обратный порядок сортировки",
        expected="WA",
        checks="порядок вывода значим — перевёрнутый ответ не считается верным",
        transform=_ast_transform(lambda tree: _rewrite_with(_SortReversed(), tree)),
        requires_source=_ast_requires(lambda tree: _rewrite_with(_SortReversed(), tree)),
        family="algorithmic",
    ),
    Mutation(
        key="accumulator_reset",
        title="накопитель обнуляется на каждой итерации",
        expected="WA",
        checks="потеря состояния между итерациями ловится (сумма/счётчик не копится)",
        transform=_ast_transform(_rewrite_accumulator_reset),
        requires_source=_ast_requires(_rewrite_accumulator_reset),
        family="algorithmic",
        alternatives=("RE",),
    ),
)


def mutation_by_key(key: str) -> Mutation | None:
    """Найти мутацию по ключу; ``None``, если такого ключа в каталоге нет."""
    for mutation in MUTATIONS:
        if mutation.key == key:
            return mutation
    return None


def applicable_mutations(expected_lines: Sequence[str], source: str = "") -> list[Mutation]:
    """Отобрать мутации, осмысленные для задачи с таким выводом и исходником."""
    return [mutation for mutation in MUTATIONS if mutation.applies_to(expected_lines, source)]
