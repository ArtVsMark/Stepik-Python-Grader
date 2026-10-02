"""test_property.py — property-based тесты (hypothesis, issue #405).

Первый hypothesis-модуль проекта. Дополняет пример-ориентированные тесты
инвариантами, проверяемыми на тысячах автосгенерированных входов:

- ``parse_testblock_file`` (core/parsers.py) — число блоков ВСЕГДА равно числу
  маркеров ``# TEST_N:`` (контракт синхронности input/output индексов, #405 T3);
- ``normalize_floats`` (core/normalizers.py) — не бросает на произвольном тексте,
  идемпотентна и сохраняет число строк (защитный контракт нормализатора вывода).

``deadline=None`` сознательно: функции тривиально быстрые, но общие CI-раннеры
дают джиттер таймингов — фиксированный дедлайн hypothesis ложно падал бы
(health-check по времени), тогда как реальный контракт здесь чисто
функциональный, без бюджета латентности.
"""

from __future__ import annotations

import re

import pytest

from stepik_grader.core.normalizers import normalize_floats
from stepik_grader.core.parsers import parse_testblock_file

# issue #646 (T3): без hypothesis в venv жёсткий top-level import обрушивал ВСЮ
# коллекцию pytest, а не один модуль. importorskip пропускает только этот файл.
pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

# Печатный ASCII без '#' — исключает и маркер ``# TEST_N:``, и все юникод-границы
# строк, на которые дробит ``str.splitlines()`` (\n, \r, \x0b, \x1c-\x1e,  …),
# чтобы сконструированный файл имел ровно столько строк, сколько мы вписали.
_SAFE_LINE = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="#"),
    max_size=24,
)

_MARKER_RE = re.compile(r"#\s*TEST_\d+:")


# ---------------------------------------------------------------------------
# parse_testblock_file — блоков ровно столько же, сколько маркеров
# ---------------------------------------------------------------------------


@given(st.text())
@settings(deadline=None, max_examples=300)
def test_blockcount_equals_markercount_for_arbitrary_text(text: str) -> None:
    """Инвариант на ЛЮБОМ тексте: len(blocks) == число строк-маркеров ``# TEST_N:``.

    Один маркер открывает ровно один блок (первый маркер — начинает, каждый
    следующий — сбрасывает предыдущий, финал сбрасывает последний), поэтому число
    блоков тождественно числу маркеров. Оракул считает маркеры тем же regex, что
    и сама функция, — расхождение означало бы рассинхрон input/output индексов.
    """
    marker_count = sum(1 for line in text.splitlines() if _MARKER_RE.match(line.strip()))
    assert len(parse_testblock_file(text)) == marker_count


@given(st.lists(_SAFE_LINE, min_size=1, max_size=6))
@settings(deadline=None, max_examples=200)
def test_constructed_file_reconstructs_each_block(bodies: list[str]) -> None:
    """Сконструированный ``# TEST_i:``-файл даёт ровно N блоков, каждый = тело дословно.

    Пробелы по краям строки — значимые данные и сохраняются (issue #783); тело
    целиком из пробелов — отбивка, и схлопывается в ``''``, как и пустое тело
    (индексы input/output не должны разъезжаться).
    """
    lines: list[str] = []
    for i, body in enumerate(bodies, 1):
        lines.append(f"# TEST_{i}:")
        if body:
            lines.append(body)
    blocks = parse_testblock_file("\n".join(lines))
    assert len(blocks) == len(bodies)
    assert blocks == [body if body.strip() else "" for body in bodies]


# ---------------------------------------------------------------------------
# normalize_floats — защитный контракт нормализатора вывода
# ---------------------------------------------------------------------------


@given(st.text())
@settings(deadline=None, max_examples=300)
def test_normalize_floats_never_raises(text: str) -> None:
    """На произвольном тексте нормализатор возвращает строку, а не бросает."""
    assert isinstance(normalize_floats(text), str)


@given(st.text())
@settings(deadline=None, max_examples=300)
def test_normalize_floats_is_idempotent(text: str) -> None:
    """f(f(x)) == f(x): round-до-9 — стабильная точка, повтор ничего не меняет."""
    once = normalize_floats(text)
    assert normalize_floats(once) == once


@given(st.text())
@settings(deadline=None, max_examples=300)
def test_normalize_floats_preserves_line_count(text: str) -> None:
    """Построчная обработка сохраняет число разделителей строк (split/join по '\\n')."""
    assert normalize_floats(text).count("\n") == text.count("\n")


# Три свойства выше — ЗАЩИТНЫЕ: «не бросает», «идемпотентна», «не теряет
# строк». Все три верны и для тождественной функции, то есть подмена
# `normalize_floats` на `lambda text: text` оставила бы их зелёными (issue
# #921, находка ADD-5-01). Дальше — свойства, которые тождественная функция
# нарушает: без них набор описывал бы форму ответа, но не сам ответ.


@given(
    whole=st.integers(min_value=-999, max_value=999),
    head=st.text(alphabet="0123456789", min_size=9, max_size=9),
    tail=st.text(alphabet="01234", min_size=1, max_size=1),
    rest=st.text(alphabet="0123456789", min_size=0, max_size=2),
)
@settings(deadline=None, max_examples=300)
def test_normalize_floats_collapses_noise_below_the_ninth_place(
    whole: int, head: str, tail: str, rest: str
) -> None:
    """Ради этого нормализатор и существует: шум ниже 9-го знака не должен решать.

    Два числа, различающиеся только с десятого знака, обязаны стать одним и тем
    же текстом — иначе верное решение получает WA из-за плавающей арифметики.
    Тождественная функция это свойство нарушает немедленно.

    Две границы области намеренные, и обе — про то, где «десятичный шум» вообще
    существует.

    Десятый знак ограничен ``0-4``: с ``5`` округление **переносит** разряд, и
    числа обязаны разойтись — это работа функции, а не нарушение. Случай
    переноса закреплён отдельным примером ниже: свойство, из которого выкинули
    неудобную половину, обязано называть её вслух.

    Значение ограничено пятнадцатью значащими цифрами (``whole`` до трёх знаков,
    хвост до трёх). Дальше рассуждение «десятый знак меньше пяти — переноса
    нет» перестаёт быть верным: оно десятичное, а ``round`` работает с двоичным
    ``float``. Поймано прогоном — ``524288.31008111145`` округляется ВВЕРХ,
    хотя десятый знак равен четырём: у такого порядка ulp уже крупнее самого
    хвоста, и «шум ниже девятого знака» там не десятичный, а представленческий.
    Это другое явление, и сваливать его в это свойство значило бы проверять
    двоичную арифметику под видом контракта нормализатора.
    """
    noisy = f"{whole}.{head}{tail}{rest}"
    quiet = f"{whole}.{head}" + "0" * (1 + len(rest))

    assert normalize_floats(noisy) == normalize_floats(quiet)


def test_normalize_floats_carries_at_the_ninth_place() -> None:
    """Половина разряда и выше — округляет ВВЕРХ, а не отбрасывается."""
    assert normalize_floats("0.0000000005") == "1e-09"
    assert normalize_floats("1.0000000005") == "1.000000001"


@given(
    whole=st.integers(min_value=1, max_value=1_000_000),
    frac=st.text(alphabet="0123456789", min_size=12, max_size=17),
)
@settings(deadline=None, max_examples=300)
def test_normalize_floats_keeps_at_most_nine_decimals(whole: int, frac: str) -> None:
    """Хвост длиннее девяти знаков не доживает до сравнения.

    Диапазон взят целой частью ≥1: у чисел меньше 1e-4 ``str`` переходит на
    научную нотацию (``1.23e-07``), где разрядность не выражена вовсе — это
    отдельное поведение, зафиксированное примерами в `test_normalizers.py`.
    """
    result = normalize_floats(f"{whole}.{frac}")

    assert "e" not in result, result
    assert len(result.split(".")[1]) <= 9, result
