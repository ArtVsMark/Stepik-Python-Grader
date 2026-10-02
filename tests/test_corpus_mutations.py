"""Тесты scripts/corpus_mutations.py — каталог мутаций прогонного корпуса.

Скрипт лежит в scripts/ (не на sys.path) — грузим по пути, тем же приёмом, что
test_generate_glossary_badge.py и соседи.

Главная проверка здесь — не форма каталога, а то, что **фильтр stdout
действительно преобразует вывод так, как обещает мутация**. Ошибка в
сгенерированном префиксе не видна глазами: она проявится ложным расхождением на
корпусе, которое будет выглядеть как дефект ядра.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "corpus_mutations.py"

# Вердикты, которые каталог вправе ожидать: CANCELLED и SANDBOX_VIOLATION —
# состояния прогона, а не свойства решения, мутацией их не получить.
_ALLOWED_VERDICTS = {"AC", "WA", "TLE", "RE"}

# Эталон-минимум для проверки трансформаций: печатает две строки, обе с числом
# и буквами — так к нему применимы все мутации каталога сразу.
_SAMPLE_SOLUTION = 'print("Ответ 3.14")\nprint("готово")\n'

# Эталон для семейства `algorithmic` (issue #1057): в нём есть всё, что правят
# AST-мутации, — диапазон, целочисленное деление, граничное сравнение,
# сортировка и накапливаемое между итерациями состояние. Печатает
# «6 / 3 / мало / a b» при вводе «4».
_ALGORITHMIC_SOLUTION = """n = int(input())
total = 0
for i in range(n):
    total = total + i
print(total)
print(total // 2)
if total >= 10:
    print("много")
else:
    print("мало")
print(" ".join(sorted(["b", "a"])))
"""


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_corpus_mutations", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Регистрация до exec_module обязательна: модуль объявляет dataclass'ы, а
    # при `from __future__ import annotations` dataclasses резолвит аннотации
    # через sys.modules[cls.__module__] и падает на незарегистрированном модуле.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _run_source(source: str, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[bytes]:
    """Выполнить исходник в отдельном процессе и вернуть сырой результат.

    Потоки решения принудительно переводятся в UTF-8 — тем же способом, что и в
    раннерах ядра: без этого кириллица в выводе на Windows-RU уходит в cp1251 и
    байтовые сравнения ниже ловят кодировку вместо мутации.
    """
    script = tmp_path / "mutant.py"
    script.write_text(source, encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        timeout=30,
        check=False,
        env=env,
    )


def _lf(payload: bytes) -> bytes:
    """Свести CRLF к LF — для вывода, идущего через текстовый ``print``.

    На Windows текстовый ``sys.stdout`` транслирует ``\\n`` в ``\\r\\n``, поэтому
    точное сравнение байт годится только для мутаций, которые пишут результат в
    ``sys.stdout.buffer`` сами. Для остальных (и для непорченого эталона)
    сравниваем с точностью до перевода строки: ядру эта разница безразлична —
    ``split_output_lines`` разбирает оба варианта одинаково.
    """
    return payload.replace(b"\r\n", b"\n")


def test_catalog_is_not_empty() -> None:
    assert _MODULE.MUTATIONS


def test_mutation_keys_are_unique() -> None:
    keys = [mutation.key for mutation in _MODULE.MUTATIONS]
    assert len(keys) == len(set(keys))


def test_expected_verdicts_are_reachable_by_mutation() -> None:
    for mutation in _MODULE.MUTATIONS:
        assert mutation.expected in _ALLOWED_VERDICTS, mutation.key


def test_catalog_covers_both_directions() -> None:
    """В каталоге есть и «поймай порчу», и «прости незначимое» (см. docstring модуля)."""
    expectations = {mutation.expected for mutation in _MODULE.MUTATIONS}
    assert "AC" in expectations
    assert expectations - {"AC"}


def test_mutation_by_key_finds_and_misses() -> None:
    assert _MODULE.mutation_by_key("timeout") is not None
    assert _MODULE.mutation_by_key("нет-такой-мутации") is None


def test_every_mutation_changes_the_source() -> None:
    """Применимая мутация обязана менять исходник — иначе она пустышка.

    Проверяется на исходнике, к которому применимы обе половины каталога:
    алгоритмические мутации правят код (`range`, `//`, сравнение, `sorted`,
    накопитель), и на выводе-эталоне из ``_SAMPLE_SOLUTION`` им нечего было бы
    менять.
    """
    for mutation in _MODULE.MUTATIONS:
        source = _ALGORITHMIC_SOLUTION if mutation.family == "algorithmic" else _SAMPLE_SOLUTION
        assert mutation.apply(source) != source, mutation.key


@pytest.mark.parametrize(
    "key",
    [mutation.key for mutation in _MODULE.MUTATIONS if mutation.key != "syntax_error"],
)
def test_mutants_stay_compilable(key: str) -> None:
    """Все мутанты, кроме синтаксической порчи, обязаны компилироваться.

    Иначе мутация втихую превратилась бы в ещё один syntax_error и проверяла бы
    не то, что заявлено (например, `trailing_space` дал бы RE вместо WA).
    """
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None
    source = _ALGORITHMIC_SOLUTION if mutation.family == "algorithmic" else _SAMPLE_SOLUTION
    compile(mutation.apply(source), f"<{key}>", "exec")


def test_syntax_error_mutation_really_breaks_syntax() -> None:
    mutation = _MODULE.mutation_by_key("syntax_error")
    assert mutation is not None
    with pytest.raises(SyntaxError):
        compile(mutation.apply(_SAMPLE_SOLUTION), "<syntax_error>", "exec")


def test_baseline_sample_output(tmp_path: pathlib.Path) -> None:
    """Опора для остальных проверок вывода: непорченый эталон печатает две строки."""
    result = _run_source(_SAMPLE_SOLUTION, tmp_path)
    assert _lf(result.stdout) == "Ответ 3.14\nготово\n".encode()


@pytest.mark.parametrize(
    ("key", "expected_stdout"),
    [
        ("no_output", b""),
        ("upper_case", "ОТВЕТ 3.14\nГОТОВО\n".encode()),
        ("trailing_space", "Ответ 3.14 \nготово \n".encode()),
        ("crlf_newlines", "Ответ 3.14\r\nготово\r\n".encode()),
        ("dropped_last_line", "Ответ 3.14\n".encode()),
        ("vertical_tab", "Ответ 3.14\x0bготово\n".encode()),
    ],
)
def test_stdout_filter_produces_exact_bytes(
    key: str, expected_stdout: bytes, tmp_path: pathlib.Path
) -> None:
    """Мутация выдаёт ровно те байты, на которые рассчитано её ожидание.

    Проверяются именно байты, а не текст: мутации про переводы строк
    (`crlf_newlines`, `vertical_tab`) осмысленны только на байтовом уровне, и
    ядро читает вывод решения тоже как байты. Сравнение точное и на Windows —
    эти мутации пишут результат в ``sys.stdout.buffer`` сами, минуя трансляцию
    переводов строк текстовым потоком.
    """
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None
    result = _run_source(mutation.apply(_SAMPLE_SOLUTION), tmp_path)
    assert result.stdout == expected_stdout


@pytest.mark.parametrize(
    ("key", "expected_stdout"),
    [
        ("extra_line", "corpus mutation\nОтвет 3.14\nготово\n".encode()),
        ("blank_line_append", "Ответ 3.14\nготово\n\n".encode()),
    ],
)
def test_print_based_mutations_add_expected_lines(
    key: str, expected_stdout: bytes, tmp_path: pathlib.Path
) -> None:
    """Мутации, дописывающие обычный ``print``, добавляют ровно ожидаемые строки.

    В отличие от соседнего теста сравнение с точностью до перевода строки: эти
    две мутации не подменяют поток, их вывод идёт через текстовый ``sys.stdout``
    и на Windows приходит с CRLF. Для вердикта это безразлично — обе строки
    лишние в любом варианте перевода строки.
    """
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None
    result = _run_source(mutation.apply(_SAMPLE_SOLUTION), tmp_path)
    assert _lf(result.stdout) == expected_stdout


def test_float_noise_perturbs_but_survives_rounding(tmp_path: pathlib.Path) -> None:
    """Шум меняет представление float'а, но исчезает при округлении до 9 знаков.

    Это ровно то, на чём держится ожидание AC: не проверив обе половины, легко
    получить либо мутацию-пустышку (вывод не изменился), либо ложное ожидание.
    """
    from stepik_grader.core.normalizers import normalize_floats

    mutation = _MODULE.mutation_by_key("float_noise")
    assert mutation is not None
    result = _run_source(mutation.apply(_SAMPLE_SOLUTION), tmp_path)
    mutated = result.stdout.decode("utf-8")

    assert mutated != "Ответ 3.14\nготово\n"
    assert normalize_floats(mutated) == normalize_floats("Ответ 3.14\nготово\n")


def test_float_noise_keeps_zero_intact(tmp_path: pathlib.Path) -> None:
    """Ноль остаётся нулём: относительный шум, а не абсолютная прибавка.

    Регрессия на первую находку корпуса — с абсолютным ``+1e-13`` вывод ``0.0``
    превращался в ``1e-13``, и стенд показывал ложное расхождение на задаче со
    средней температурой.
    """
    mutation = _MODULE.mutation_by_key("float_noise")
    assert mutation is not None
    result = _run_source(mutation.apply('print("0.0")\n'), tmp_path)
    assert result.stdout == b"0.0\n"


def test_applicability_predicates_filter_catalog() -> None:
    """Неприменимые мутации отсеиваются, а не дают заведомо ложное ожидание."""
    numeric_only = _MODULE.applicable_mutations(["42"])
    keys = {mutation.key for mutation in numeric_only}
    assert "upper_case" not in keys  # в «42» нечего переводить в верхний регистр
    assert "float_noise" not in keys  # и нет числа с десятичной точкой
    assert "timeout" in keys  # а зависание применимо к любой задаче

    with_text_and_float = _MODULE.applicable_mutations(["Ответ 3.14"])
    rich_keys = {mutation.key for mutation in with_text_and_float}
    assert {"upper_case", "float_noise"} <= rich_keys


# ── Семейство `algorithmic`: ошибка в решении, а не порча вывода (issue #1057) ──


_ALGORITHMIC_KEYS = (
    "off_by_one_range",
    "int_division_swap",
    "boundary_flip",
    "reversed_order",
    "accumulator_reset",
)


def _run_with_input(source: str, stdin: str, tmp_path: pathlib.Path) -> str:
    """Выполнить исходник, подав ``stdin``, и вернуть его вывод текстом."""
    script = tmp_path / "mutant.py"
    script.write_text(source, encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, str(script)],
        input=stdin.encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
        env=env,
    )
    return _lf(result.stdout).decode("utf-8")


def test_algorithmic_family_is_present() -> None:
    """В каталоге есть обе половины: порча вывода и ошибка в алгоритме."""
    families = {mutation.family for mutation in _MODULE.MUTATIONS}
    assert families == {"output", "algorithmic"}


@pytest.mark.parametrize("key", _ALGORITHMIC_KEYS)
def test_algorithmic_mutations_are_applicable_to_matching_source(key: str) -> None:
    """Предикат по исходнику пропускает решение, в котором есть что менять."""
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None
    assert mutation.applies_to(["6"], _ALGORITHMIC_SOLUTION)


@pytest.mark.parametrize("key", _ALGORITHMIC_KEYS)
def test_algorithmic_mutations_skip_source_without_target(key: str) -> None:
    """Нечего менять — мутация неприменима, а не применяется вхолостую.

    Это и есть защита от пустышки: без предиката по исходнику каталог записал бы
    ожидание WA для задачи, где мутант равен эталону, и корпус показал бы
    расхождение вместо честного пропуска.
    """
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None
    assert not mutation.applies_to(["готово"], _SAMPLE_SOLUTION)


def test_applicable_mutations_respect_source() -> None:
    """`applicable_mutations` фильтрует и по выводу, и по исходнику."""
    keys = {m.key for m in _MODULE.applicable_mutations(["6"], _SAMPLE_SOLUTION)}
    assert not keys & set(_ALGORITHMIC_KEYS)

    keys = {m.key for m in _MODULE.applicable_mutations(["6"], _ALGORITHMIC_SOLUTION)}
    assert set(_ALGORITHMIC_KEYS) <= keys


def test_source_is_required_for_algorithmic_mutations() -> None:
    """Без исходника алгоритмическая мутация отсеивается, а не применяется вслепую."""
    keys = {mutation.key for mutation in _MODULE.applicable_mutations(["6"])}
    assert not keys & set(_ALGORITHMIC_KEYS)


@pytest.mark.parametrize(
    ("key", "expected_output"),
    [
        # Эталон при вводе «4»: сумма 0+1+2+3 = 6, половина 3, ветка «мало», «a b».
        ("off_by_one_range", "3\n1\nмало\na b\n"),  # диапазон короче: 0+1+2 = 3
        ("int_division_swap", "6\n3.0\nмало\na b\n"),  # 6 / 2 печатается как 3.0
        ("boundary_flip", "6\n3\nмало\na b\n"),  # граница сдвинута, ветка та же
        ("reversed_order", "6\n3\nмало\nb a\n"),  # порядок сортировки обратный
        ("accumulator_reset", "3\n1\nмало\na b\n"),  # накопитель обнулён в цикле
    ],
)
def test_algorithmic_mutants_change_behaviour(
    key: str, expected_output: str, tmp_path: pathlib.Path
) -> None:
    """Мутант считает иначе, чем эталон, — и ровно так, как обещает каталог.

    Проверяется поведение, а не текст трансформации: `ast.unparse` может
    переписать код как угодно, значение имеет только результат прогона.
    """
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None

    baseline = _run_with_input(_ALGORITHMIC_SOLUTION, "4\n", tmp_path)
    assert baseline == "6\n3\nмало\na b\n"

    mutated = _run_with_input(mutation.apply(_ALGORITHMIC_SOLUTION), "4\n", tmp_path)
    assert mutated == expected_output


def test_boundary_flip_can_be_neutral(tmp_path: pathlib.Path) -> None:
    """Сдвиг границы может не проявиться — за это отвечает `may_be_neutral`.

    Здесь мутация применима (сравнение есть) и код меняется, но на этих данных
    ответ тот же. Раннер обязан считать такой исход нейтральным, а не дефектом
    ядра, — иначе корпус краснеет на ровном месте.
    """
    source = "values = [3, 25]\nprint(len([v for v in values if v >= 10]))\n"
    mutation = _MODULE.mutation_by_key("boundary_flip")
    assert mutation is not None
    assert mutation.may_be_neutral

    assert _run_with_input(mutation.apply(source), "", tmp_path) == _run_with_input(
        source, "", tmp_path
    )


def test_output_family_is_never_neutral() -> None:
    """Порча вывода видна всегда — послабление про нейтральность к ней не относится."""
    for mutation in _MODULE.MUTATIONS:
        if mutation.family == "output":
            assert not mutation.may_be_neutral, mutation.key


@pytest.mark.parametrize("key", _ALGORITHMIC_KEYS)
def test_algorithmic_expectations_are_not_ac(key: str) -> None:
    """Алгоритмическая мутация ломает решение — AC не может быть приемлемым исходом.

    Иначе нейтральный исход был бы неотличим от совпадения, и ложный AC ядра
    прошёл бы как успешная проверка.
    """
    mutation = _MODULE.mutation_by_key(key)
    assert mutation is not None
    assert "AC" not in mutation.accepted


def test_accepted_includes_alternatives() -> None:
    """`accepted` — это ожидание плюс альтернативы, а не одно значение."""
    mutation = _MODULE.mutation_by_key("off_by_one_range")
    assert mutation is not None
    assert mutation.accepted == ("WA", "RE")


def test_ast_mutations_do_not_touch_string_literals(tmp_path: pathlib.Path) -> None:
    """Правится дерево, а не текст: `//` внутри строки — не оператор деления.

    Текстовая замена испортила бы литерал (и вердикт стал бы про сломанный
    вывод, а не про подменённое деление).
    """
    source = 'print("путь//к//файлу")\nprint(7 // 2)\n'
    mutation = _MODULE.mutation_by_key("int_division_swap")
    assert mutation is not None

    assert _run_with_input(mutation.apply(source), "", tmp_path) == "путь//к//файлу\n3.5\n"


def test_unparseable_source_is_left_alone() -> None:
    """Неразбираемый исходник не портится вслепую и объявляется неприменимым."""
    broken = "def (\n"
    for key in _ALGORITHMIC_KEYS:
        mutation = _MODULE.mutation_by_key(key)
        assert mutation is not None
        assert not mutation.applies_to(["x"], broken), key
        assert mutation.apply(broken) == broken, key


def test_empty_expectation_drops_output_mutations() -> None:
    """Задаче без вывода не назначаются мутации, портящие вывод."""
    keys = {mutation.key for mutation in _MODULE.applicable_mutations([])}
    assert "no_output" not in keys
    assert "trailing_space" not in keys
    assert "runtime_error" in keys


_FUTURE_SOLUTION = (
    '"""Решение с импортом из __future__."""\n\nfrom __future__ import annotations\n\nprint(42)\n'
)

_PREFIX_KEYS = (
    "timeout",
    "runtime_error",
    "no_output",
    "extra_line",
    "dropped_last_line",
    "upper_case",
    "vertical_tab",
    "float_noise",
    "crlf_newlines",
    "trailing_space",
    "blank_line_append",
)


class TestPrefixRespectsFutureImports:
    """Префикс не смеет вставать выше `from __future__` (issue #921, `QA-3-02`).

    Такой импорт по правилам языка стоит в начале файла. Дописанный выше
    префикс делает файл некорректным — «from __future__ imports must occur at
    the beginning of the file», — и мутант отвечает `RE` вместо задуманного
    вердикта. Стенд показывал это расхождением с ожиданием, то есть **дефектом
    ядра**: двенадцать мутаций каталога из семнадцати строятся через `_prefix`,
    и на решении с таким импортом ложным становился почти весь прогон задачи.

    Коварство в том, что причина не в ядре и не в мутации, а в первой строке
    чужого решения.
    """

    @pytest.mark.parametrize("key", _PREFIX_KEYS)
    def test_mutant_still_compiles(self, key: str) -> None:
        mutation = _MODULE.mutation_by_key(key)
        assert mutation is not None

        compile(mutation.apply(_FUTURE_SOLUTION), "<mutant>", "exec")

    @pytest.mark.parametrize("key", _PREFIX_KEYS)
    def test_prefix_is_actually_inserted(self, key: str) -> None:
        """Уцелеть мало — мутация обязана сработать, а не тихо исчезнуть."""
        mutation = _MODULE.mutation_by_key(key)
        assert mutation is not None

        assert mutation.apply(_FUTURE_SOLUTION) != _FUTURE_SOLUTION

    def test_syntax_error_mutation_stays_broken(self) -> None:
        """Единственная мутация, которой ломать файл положено, — ломает."""
        mutation = _MODULE.mutation_by_key("syntax_error")
        assert mutation is not None

        with pytest.raises(SyntaxError):
            compile(mutation.apply(_FUTURE_SOLUTION), "<mutant>", "exec")

    def test_import_stays_first(self) -> None:
        """Импорт остаётся выше префикса — иначе он бы просто не действовал."""
        mutated = _MODULE._prefix("marker = 1")(_FUTURE_SOLUTION)
        lines = mutated.splitlines()

        assert lines.index("from __future__ import annotations") < lines.index("marker = 1")

    def test_multiline_import_is_kept_whole(self) -> None:
        """Импорт бывает в скобках на несколько строк — режем по концу узла."""
        source = "from __future__ import (\n    annotations,\n)\n\nprint(1)\n"

        compile(_MODULE._prefix("marker = 1")(source), "<mutant>", "exec")

    def test_solution_without_the_import_is_untouched(self) -> None:
        """Обычное решение по-прежнему получает префикс первой строкой."""
        assert _MODULE._prefix("marker = 1")("print(1)\n") == "marker = 1\nprint(1)\n"

    def test_unparseable_source_does_not_crash(self) -> None:
        """Неразбираемый исходник — не повод ронять каталог мутаций."""
        assert _MODULE._prefix("marker = 1")("def (\n") == "marker = 1\ndef (\n"

    def test_prefixed_mutant_runs_and_transforms_output(self, tmp_path: pathlib.Path) -> None:
        """Итог, ради которого всё: фильтр вывода работает и на таком решении."""
        mutation = _MODULE.mutation_by_key("upper_case")
        assert mutation is not None
        source = "from __future__ import annotations\n\nprint('ответ')\n"

        assert _run_with_input(mutation.apply(source), "", tmp_path) == "ОТВЕТ\n"
