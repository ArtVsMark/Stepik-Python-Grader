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
    for mutation in _MODULE.MUTATIONS:
        assert mutation.apply(_SAMPLE_SOLUTION) != _SAMPLE_SOLUTION, mutation.key


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
    compile(mutation.apply(_SAMPLE_SOLUTION), f"<{key}>", "exec")


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


def test_empty_expectation_drops_output_mutations() -> None:
    """Задаче без вывода не назначаются мутации, портящие вывод."""
    keys = {mutation.key for mutation in _MODULE.applicable_mutations([])}
    assert "no_output" not in keys
    assert "trailing_space" not in keys
    assert "runtime_error" in keys
