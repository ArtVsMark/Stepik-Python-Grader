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
применимости по ожидаемым строкам эталона (:attr:`Mutation.requires`), а
раннер пропускает неприменимые — вместо того чтобы записать заведомо ложное
ожидание.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from stepik_grader.core.result import Verdict

__all__ = [
    "MUTATIONS",
    "Mutation",
    "applicable_mutations",
    "mutation_by_key",
]


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
    """

    key: str
    title: str
    expected: Verdict
    checks: str
    transform: Callable[[str], str]
    requires: Callable[[Sequence[str]], bool] = field(default=_always)

    def apply(self, source: str) -> str:
        """Применить мутацию к исходнику эталона."""
        return self.transform(source)

    def applies_to(self, expected_lines: Sequence[str]) -> bool:
        """Вернуть True, если мутация осмысленна для задачи с таким ожиданием."""
        return self.requires(expected_lines)


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
)


def mutation_by_key(key: str) -> Mutation | None:
    """Найти мутацию по ключу; ``None``, если такого ключа в каталоге нет."""
    for mutation in MUTATIONS:
        if mutation.key == key:
            return mutation
    return None


def applicable_mutations(expected_lines: Sequence[str]) -> list[Mutation]:
    """Отобрать мутации, осмысленные для задачи с таким ожидаемым выводом."""
    return [mutation for mutation in MUTATIONS if mutation.applies_to(expected_lines)]
