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

from hypothesis import given, settings
from hypothesis import strategies as st

from stepik_grader.core.normalizers import normalize_floats
from stepik_grader.core.parsers import parse_testblock_file

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
    """Сконструированный ``# TEST_i:``-файл даёт ровно N блоков, каждый = body.strip().

    Пустые тела сохраняются как ``''`` (индексы input/output не должны разъезжаться).
    """
    lines: list[str] = []
    for i, body in enumerate(bodies, 1):
        lines.append(f"# TEST_{i}:")
        if body:
            lines.append(body)
    blocks = parse_testblock_file("\n".join(lines))
    assert len(blocks) == len(bodies)
    assert blocks == [body.strip() for body in bodies]


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
