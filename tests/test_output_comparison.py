"""Прогонные тесты сравнения вывода: что грейдер прощает, а что нет (issue #786).

Сердцевина вердикта — сравнение фактического вывода с ожидаемым
(``grader_core._map_outcome_to_result``) — до сих пор проверялась только
unit-тестами самих нормализаторов (``tests/test_normalizers.py``): ни один тест
не запускал реальное решение и не спрашивал, каким вердиктом обернётся
хвостовой пробел, BOM в файле ожиданий или CRLF. Здесь каждый кейс — настоящий
прогон через ``run_tests`` (реальный subprocess), а утверждение — вердикт
AC/WA, то есть ровно то, что увидит студент.

Таблица «прощает / не прощает», которую фиксирует этот файл, продублирована для
пользователя в [configuration.md § Как сравнивается вывод](../docs/use/configuration.md);
расхождение между ними означает, что документация врёт.

Известные дефекты помечены ``xfail(strict=True)`` со ссылкой на issue: пока
дефект жив, кейс «ожидаемо падает» и не красит прогон; после фикса он начнёт
проходить, strict превратит это в падение — сигнал снять маркер вместе с
закрытием issue. Молчаливых ``skip``/нестрогих ``xfail`` здесь нет намеренно.

**Сейчас таких кейсов нет ни одного, и это состояние, а не забытое правило.**
Различить их читателю было нечем (issue #1004, находка `ADD-5-05`): абзац выше
обещал маркеры, в файле не было ни одного, и «известных дефектов не осталось»
выглядело точно так же, как «соглашение бросили». Теперь соглашение держит
проверка: появившийся ``xfail`` обязан быть строгим и называть issue.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator

import pytest

from stepik_grader import config
from stepik_grader.core.grader_core import resolve_test_dir, run_tests
from stepik_grader.core.result import CaseResult

# Прогонные кейсы тривиальны по времени; потолок нужен только чтобы зависший
# subprocess падал сам, а не по глобальному дедлайну pytest-timeout.
_TIMEOUT = 15.0
_SOLUTION = "task.py"


# ---------------------------------------------------------------------------
# Конструкторы «лаборатории»: решение + каталог тестов в одном из трёх форматов
# ---------------------------------------------------------------------------


def _task_dir(tmp_path: pathlib.Path, name: str, solution: str) -> pathlib.Path:
    """Создать каталог задачи с решением и пустым ``tests/``."""
    task_dir = tmp_path / name
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / _SOLUTION).write_text(solution, encoding="utf-8")
    return task_dir


def _grade(task_dir: pathlib.Path) -> CaseResult:
    """Прогнать решение по кейсам каталога и вернуть результат первого кейса."""
    result = run_tests(task_dir / _SOLUTION, task_dir / "tests", timeout=_TIMEOUT)
    cases = result["cases"]
    assert cases, f"тест-кейсы не загрузились из {task_dir / 'tests'}"
    return cases[0]


def _named(
    tmp_path: pathlib.Path,
    solution: str,
    *,
    expected: bytes,
    stdin: bytes = b"",
    name: str = "named",
) -> CaseResult:
    """Формат 2 (``input_1.txt`` + ``expected_1.txt``) — байты пишутся как есть.

    Файлы задаются байтами, а не строками: BOM, CRLF и отсутствие финального
    перевода строки — сами по себе предмет проверки, и ``write_text`` их бы
    незаметно причесал.
    """
    task_dir = _task_dir(tmp_path, name, solution)
    (task_dir / "tests" / "input_1.txt").write_bytes(stdin)
    (task_dir / "tests" / "expected_1.txt").write_bytes(expected)
    return _grade(task_dir)


def _legacy(
    tmp_path: pathlib.Path,
    solution: str,
    *,
    expected: bytes,
    stdin: bytes = b"",
    name: str = "legacy",
) -> CaseResult:
    """Формат 1 (``tests/1`` + ``tests/1.clue``)."""
    task_dir = _task_dir(tmp_path, name, solution)
    (task_dir / "tests" / "1").write_bytes(stdin)
    (task_dir / "tests" / "1.clue").write_bytes(expected)
    return _grade(task_dir)


def _testblock(
    tmp_path: pathlib.Path,
    solution: str,
    *,
    expected: str,
    stdin: str = "5",
    name: str = "testblock",
) -> CaseResult:
    """Формат 3 (``input.txt`` + ``output.txt`` с маркерами ``# TEST_N:``)."""
    task_dir = _task_dir(tmp_path, name, solution)
    (task_dir / "tests" / "input.txt").write_text(f"# TEST_1:\n{stdin}", encoding="utf-8")
    (task_dir / "tests" / "output.txt").write_text(f"# TEST_1:\n{expected}", encoding="utf-8")
    return _grade(task_dir)


def _print_separated(code_point: int) -> str:
    """Решение, печатающее ``a<символ>b`` одним ``print`` (символ — через ``chr``).

    Литерал в исходнике этого файла не годится: часть таких символов сама
    разрезает строку при чтении файла, и кейс молча превратился бы в другой.
    """
    return f"print('a' + chr({code_point}) + 'b')"


# ---------------------------------------------------------------------------
# Числа с плавающей точкой: расхождение за 9-м знаком прощается
#
# Единственная нормализация в сравнении — построчный round(float, 9)
# (core/normalizers.normalize_floats). Она включается ТОЛЬКО когда дословное
# сравнение не совпало И число строк одинаково.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "solution,expected,verdict,why",
    [
        ("print(1/3)", b"0.333333333\n", "AC", "хвост за 9-м знаком отбрасывается"),
        ("print(0.1+0.2)", b"0.3\n", "AC", "классическая ошибка двоичного float"),
        ("print(1/3)\nprint(2/3)", b"0.333333333\n0.666666667\n", "AC", "построчно"),
        ("print(0.1234567894)", b"0.123456789\n", "AC", "10-й знак отбрасывается"),
        ("print(1e-07)", b"0.0000001\n", "AC", "экспонента и десятичная — одно число"),
        ("print(1e16)", b"10000000000000000.0\n", "AC", "то же для больших чисел"),
        (
            "print('Ответ: 3.14159265358979')",
            "Ответ: 3.141592654\n".encode(),
            "AC",
            "число внутри текста тоже нормализуется",
        ),
        ("print(float('inf'))", b"inf\n", "AC", "inf сравнивается дословно"),
        ("print(float('nan'))", b"nan\n", "AC", "nan сравнивается дословно"),
        ("print('Python 3.10.5')", b"Python 3.10.5\n", "AC", "версия не считается float"),
        # --- не прощается ---
        # issue #940: незначащие нули ожидания — это требование формата
        # «вывести с точностью до сотых», а не другая запись той же величины.
        ("print(12.3)", b"12.30\n", "WA", "решение не выполнило требование «до сотых»"),
        ("print(1.5)", b"1.50\n", "WA", "то же для одного знака вместо двух"),
        ("print(100.0)", b"100.00\n", "WA", "нули после точки значимы, если их ждут"),
        ('print(f"{12.3:.2f}")', b"12.30\n", "AC", "формат соблюдён — толерантность не нужна"),
        (
            "print(0.1+0.2)",
            b"0.30000000000000004\n",
            "AC",
            "у решения знаков не меньше — прежняя толерантность цела",
        ),
        ("print(0.12345679)", b"0.12345678\n", "WA", "расхождение внутри 9 знаков — ошибка"),
        ("print(5.0)", b"5\n", "WA", "целое и float — разные строки, regex не тронет '5'"),
        ("print(-0.0)", b"0.0\n", "WA", "знак нуля сохраняется"),
        ("print('3,14')", b"3.14\n", "WA", "запятая как десятичный разделитель не понимается"),
        (
            "print(1/3)\nprint('лишняя строка')",
            b"0.333333333\n",
            "WA",
            "при разном числе строк нормализация вообще не применяется",
        ),
    ],
)
def test_float_comparison(
    tmp_path: pathlib.Path, solution: str, expected: bytes, verdict: str, why: str
) -> None:
    """Округление до 9 знаков прощает хвост float, но не расхождение внутри."""
    assert _named(tmp_path, solution, expected=expected)["verdict"] == verdict, why


# ---------------------------------------------------------------------------
# Пробелы: не прощаются ни с одной стороны
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "solution,expected,verdict,why",
    [
        # issue #1111: хвостовой пробел незначим в режиме по умолчанию `stepik` —
        # как у чекера платформы. Проверка обратного поведения — в
        # TestStrictCompareMode ниже.
        ("print('hello ')", b"hello\n", "AC", "хвостовой пробел в выводе решения"),
        ("print('hello')", b"hello \n", "AC", "хвостовой пробел в файле ожиданий"),
        ("print('  *')", b"  *\n", "AC", "ведущие пробелы значимы и сохраняются"),
        ("print(' hello')", b"hello\n", "WA", "ведущий пробел значим — обрезается только хвост"),
        ("print('a' + chr(0xA0) + 'b')", b"a b\n", "WA", "неразрывный пробел ≠ обычный"),
        ("print('a\\tb')", b"a b\n", "WA", "табуляция внутри строки ≠ пробел"),
    ],
)
def test_whitespace_is_significant(
    tmp_path: pathlib.Path, solution: str, expected: bytes, verdict: str, why: str
) -> None:
    """Сравнение дословное: любой пробельный символ значим."""
    assert _named(tmp_path, solution, expected=expected)["verdict"] == verdict, why


# ---------------------------------------------------------------------------
# Переводы строк и BOM: CRLF прощается, BOM — нет
# ---------------------------------------------------------------------------


def test_crlf_in_expected_file_is_forgiven(tmp_path: pathlib.Path) -> None:
    """CRLF в файле ожиданий не мешает: файлы читаются в universal-newlines."""
    result = _named(tmp_path, "print('a')\nprint('b')", expected=b"a\r\nb\r\n")
    assert result["verdict"] == "AC"


def test_crlf_in_legacy_clue_is_forgiven(tmp_path: pathlib.Path) -> None:
    """То же для формата 1 (`.clue`) — путь чтения общий (`load_text_lines`)."""
    result = _legacy(tmp_path, "print('a')\nprint('b')", expected=b"a\r\nb\r\n")
    assert result["verdict"] == "AC"


def test_crlf_in_input_file_does_not_reach_stdin(tmp_path: pathlib.Path) -> None:
    """CRLF во входном файле не доезжает до решения: `input()` получает чистую строку."""
    result = _named(tmp_path, "print(repr(input()))", expected=b"'5'\n", stdin=b"5\r\n")
    assert result["verdict"] == "AC"


def test_missing_trailing_newline_in_expected_is_forgiven(tmp_path: pathlib.Path) -> None:
    """Файл ожиданий без финального перевода строки эквивалентен файлу с ним."""
    result = _named(tmp_path, "print('a')", expected=b"a")
    assert result["verdict"] == "AC"


def test_bom_in_expected_file_does_not_fail_a_correct_solution(tmp_path: pathlib.Path) -> None:
    """BOM в файле ожиданий больше не даёт WA верному решению (issue #939).

    Ловушка Windows: «Блокнот» и Excel сохраняют UTF-8 с BOM. Раньше маркер
    оставался первым символом первой строки и верное решение получало `WA`;
    теперь он срезается при чтении файла тестов.
    """
    result = _named(tmp_path, "print('a')", expected=b"\xef\xbb\xbfa\n")
    assert result["verdict"] == "AC"
    assert result["expected"] == ["a"]


def test_bom_printed_by_solution_fails(tmp_path: pathlib.Path) -> None:
    """BOM в выводе решения — такой же значимый символ, как любой другой."""
    result = _named(tmp_path, "print('\\ufeffa')", expected=b"a\n")
    assert result["verdict"] == "WA"


def test_bom_in_input_file_does_not_leak_into_stdin(tmp_path: pathlib.Path) -> None:
    """BOM во ВХОДНОМ файле больше не утекает в stdin решения (issue #939).

    Раньше первый `input()` получал маркер, и для студента это выглядело как
    `ValueError` в `int(input())` на совершенно верном коде.
    """
    result = _named(tmp_path, "print(repr(input()))", expected=b"'5'\n", stdin=b"\xef\xbb\xbf5\n")
    assert result["verdict"] == "AC"
    # repr() показал бы невидимый маркер, если бы он дошёл до решения.
    assert result["output"] == [repr("5")]


# ---------------------------------------------------------------------------
# Пустой вывод и пустые строки
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "solution,expected,verdict,why",
    [
        ("pass", b"", "AC", "пустой файл ожиданий = решение не печатает ничего"),
        # issue #1111: три кейса ниже ждали WA. Их и завернул грейдер на реальной
        # базе — при том, что Stepik те же решения принял.
        ("print()", b"", "AC", "пустой файл и одна пустая строка — одно и то же"),
        ("print()", b"\n", "AC", "файл из одного перевода строки = одна пустая строка"),
        ("print('a')\nprint()", b"a\n", "AC", "хвостовая пустая строка незначима"),
        ("print('a')", b"a\n\n\n", "AC", "пустые строки в конце файла ожиданий незначимы"),
        ("print('\\na')", b"a\n", "WA", "пустая строка В НАЧАЛЕ значима — обрезается только хвост"),
    ],
)
def test_empty_output_edges(
    tmp_path: pathlib.Path, solution: str, expected: bytes, verdict: str, why: str
) -> None:
    """Хвостовые пустые строки незначимы, ведущие — значимы (issue #1111)."""
    assert _named(tmp_path, solution, expected=expected)["verdict"] == verdict, why


# ---------------------------------------------------------------------------
# Многострочный stdin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "solution,stdin,expected,why",
    [
        (
            "import sys\nfor line in sys.stdin:\n    print(line.rstrip())",
            b"1\n2\n3",
            b"1\n2\n3\n",
            "все строки входного файла доезжают до решения",
        ),
        (
            "a=input()\nb=input()\nc=input()\nprint(a+b+c)",
            b"x\ny\nz",
            b"xyz\n",
            "input() читает их по одной",
        ),
        (
            "import sys\nprint(len(sys.stdin.read().splitlines()))",
            b"a\n\nb",
            b"3\n",
            "пустая строка внутри входа сохраняется",
        ),
        (
            "print(input())",
            b"x",
            b"x\n",
            "входной файл без финального перевода строки",
        ),
        (
            "import sys\nprint(len(sys.stdin.read()))",
            b"",
            b"1\n",
            "пустой входной файл даёт stdin из одного '\\n' (input_lines=[''])",
        ),
    ],
)
def test_multiline_stdin(
    tmp_path: pathlib.Path, solution: str, stdin: bytes, expected: bytes, why: str
) -> None:
    """Многострочный вход подаётся решению целиком и без искажений."""
    assert _named(tmp_path, solution, expected=expected, stdin=stdin)["verdict"] == "AC", why


# ---------------------------------------------------------------------------
# Юникод: сравнение идёт в UTF-8 на обеих сторонах
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "solution,stdin,expected,why",
    [
        ("print('Привет')", b"", "Привет\n".encode(), "кириллица в выводе"),
        ("print('🐍')", b"", "🐍\n".encode(), "символ вне BMP (эмодзи)"),
        ("print(input().upper())", "мир".encode(), "МИР\n".encode(), "кириллица через stdin"),
        ("print('ﬁ')", b"", "ﬁ\n".encode(), "лигатура не раскладывается в 'fi'"),
    ],
)
def test_unicode_roundtrip(
    tmp_path: pathlib.Path, solution: str, stdin: bytes, expected: bytes, why: str
) -> None:
    """Не-ASCII проходит цепочку stdin → решение → сравнение без потерь."""
    assert _named(tmp_path, solution, expected=expected, stdin=stdin)["verdict"] == "AC", why


def test_unicode_is_compared_without_nfc_normalization(tmp_path: pathlib.Path) -> None:
    """Разные юникод-формы одной буквы не приравниваются: 'й' NFC ≠ 'и'+U+0306 NFD."""
    result = _named(tmp_path, "print('и' + chr(0x306))", expected="й\n".encode())
    assert result["verdict"] == "WA"


# ---------------------------------------------------------------------------
# Разделители строк за пределами '\n' (находка #786, фикс #843)
#
# `str.splitlines()` режет ещё по восьми символам (VT, FF, FS, GS, RS, NEL,
# U+2028/U+2029). Вывод из ОДНОЙ строки с таким символом внутри превращался в
# две — и совпадал с ожиданием из двух настоящих строк: неверный вывод получал
# AC. Теперь обе стороны сравнения режутся только по `\n`, `\r\n` и `\r`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code_point,name",
    [
        (0x0B, "VT — вертикальная табуляция"),
        (0x0C, "FF — перевод страницы"),
        (0x1C, "FS — разделитель файлов"),
        (0x1D, "GS — разделитель групп"),
        (0x1E, "RS — разделитель записей"),
        (0x85, "NEL — юникодный перевод строки"),
        (0x2028, "U+2028 — LINE SEPARATOR"),
        (0x2029, "U+2029 — PARAGRAPH SEPARATOR"),
    ],
)
def test_exotic_separator_must_not_equal_a_real_newline(
    tmp_path: pathlib.Path, code_point: int, name: str
) -> None:
    """Одна строка с экзотическим разделителем ≠ две настоящие строки (issue #843)."""
    result = _named(tmp_path, _print_separated(code_point), expected=b"a\nb\n")
    assert result["verdict"] == "WA", name


def test_carriage_return_stays_a_line_separator(tmp_path: pathlib.Path) -> None:
    """CR остаётся разделителем — и это не исключение из #843, а симметрия.

    `\\r\\n` — перевод строки Windows (именно так `print` пишет в pipe), а
    одиночный `\\r` считает разделителем и `Path.read_text` при чтении файла
    ожиданий. Трактуй мы его как данные, стороны сравнения снова разошлись бы:
    файл дал бы две строки, вывод — одну.
    """
    result = _named(tmp_path, _print_separated(0x0D), expected=b"a\nb\n")
    assert result["verdict"] == "AC"


def test_exotic_separator_is_data_on_both_sides(tmp_path: pathlib.Path) -> None:
    """Тот же символ в выводе и в ожидании — совпадение, а не ложное различие."""
    result = _named(tmp_path, _print_separated(0x0B), expected=b"a\x0bb\n")
    assert result["verdict"] == "AC"


# ---------------------------------------------------------------------------
# Формат 3: `.strip()` блока съедает значимые пробелы (issue #783)
#
# Контрольная группа — те же данные в форматах 1 и 2, где пробелы сохраняются:
# она доказывает, что дело в разборе блоков, а не в сравнении вывода.
# ---------------------------------------------------------------------------


def test_leading_spaces_survive_in_named_format(tmp_path: pathlib.Path) -> None:
    """Формат 2: ведущие пробелы в ожидании сохраняются — верное решение получает AC."""
    assert _named(tmp_path, "print('  *')", expected=b"  *\n")["verdict"] == "AC"


def test_leading_spaces_survive_in_legacy_format(tmp_path: pathlib.Path) -> None:
    """Формат 1: то же самое — контрольная группа для формата 3."""
    assert _legacy(tmp_path, "print('  *')", expected=b"  *\n")["verdict"] == "AC"


def test_blank_line_inside_testblock_survives(tmp_path: pathlib.Path) -> None:
    """Формат 3: пустые строки ВНУТРИ блока не теряются — режутся только края."""
    result = _testblock(tmp_path, "print('a\\n\\nb')", expected="a\n\nb")
    assert result["verdict"] == "AC"


@pytest.mark.parametrize(
    "solution,expected,name",
    [
        ("print('  *')", "  *", "ведущие пробелы (ёлочка, таблица, отступы)"),
        ("print('a   ')", "a   ", "хвостовые пробелы"),
    ],
)
def test_testblock_format_keeps_significant_spaces(
    tmp_path: pathlib.Path, solution: str, expected: str, name: str
) -> None:
    """Формат 3: пробелы по краям блока значимы так же, как в форматах 1 и 2 (issue #783)."""
    assert _testblock(tmp_path, solution, expected=expected)["verdict"] == "AC", name


@pytest.mark.parametrize(
    "solution,expected,verdict,name",
    [
        ("print('\\na')", "\na", "WA", "ведущая пустая строка"),
        # issue #1111: два кейса ниже давали WA, пока хвост был значим. Формат
        # по-прежнему срезает край блока — но теперь это различие не влияет на
        # вердикт, потому что режим `stepik` его и не различает.
        ("print('a\\n')", "a\n", "AC", "хвостовая пустая строка"),
        ("print('   ')", "   ", "AC", "блок из одних пробелов"),
    ],
)
def test_testblock_format_drops_blank_edges(
    tmp_path: pathlib.Path, solution: str, expected: str, verdict: str, name: str
) -> None:
    """Формат 3: пустые строки по краям блока теряются — цена формата, а не дефект.

    В `input.txt`/`output.txt` блок ограничен маркерами `# TEST_N:`, поэтому
    пустая строка на его краю неотличима от отбивки перед следующим маркером.
    Фикс #783 сохранил пробелы ВНУТРИ строк, а край режется по-прежнему. Что
    изменилось в #1111: срезанный **хвост** больше не меняет вердикт — режим
    `stepik` хвостовые пустые строки и пробелы не различает. Ведущая пустая
    строка значима, и задача с ней по-прежнему задаётся форматом 1 или 2.
    """
    assert _testblock(tmp_path, solution, expected=expected)["verdict"] == verdict, name


def test_testblock_stdin_keeps_leading_spaces(tmp_path: pathlib.Path) -> None:
    """Формат 3: пробелы значимы и на стороне ВХОДА — блок доезжает до `stdin` как есть."""
    result = _testblock(tmp_path, "print(repr(input()))", stdin="  5", expected="'  5'")
    assert result["verdict"] == "AC"


def test_testblock_code_block_with_indent_still_runs_as_function(
    tmp_path: pathlib.Path,
) -> None:
    """Формат 3: блок-код с отступом остаётся function-маршрутом, а не уезжает в stdin.

    Пробелы по краям строк теперь сохраняются (issue #783), поэтому блок вида
    `  print(solve(2))` дошёл бы до `ast.parse` с ведущим отступом —
    синтаксическая ошибка, и вызов молча подался бы решению на `stdin`.
    """
    result = _testblock(
        tmp_path,
        "def solve(x):\n    return x * 2\n",
        stdin="  print(solve(21))",
        expected="42",
    )
    assert result["verdict"] == "AC"


# ---------------------------------------------------------------------------
# Результат объясняет вердикт (issue #935)
# ---------------------------------------------------------------------------


def test_truncated_output_says_so(tmp_path: pathlib.Path, monkeypatch) -> None:
    """WA от обрезки вывода несёт причину, а не выглядит обычным несовпадением.

    issue #935 (RUN-1-02): пометка об обрезке уходила в stderr, а AC/WA-ветка
    хардкодила пустой `error` — студент искал несуществующую ошибку в своём
    коде, хотя вывод обрезал сам грейдер.
    """
    from stepik_grader import config as config_mod

    # CONFIG — frozen dataclass, поэтому лимит задаётся так же, как у
    # пользователя: настоящим файлом конфигурации. Через переменную окружения,
    # а не через set_config_path: monkeypatch откатит её сам, и тест перестаёт
    # зависеть от порядка — глобальный путь конфига общий на весь прогон, и
    # его ручной сброс в None затирал состояние соседних тестов.
    cfg = tmp_path / "tiny.toml"
    cfg.write_text("[tool.stepik-grader]\nmax_output_bytes = 3\n", encoding="utf-8")
    monkeypatch.setenv("STEPIK_GRADER_CONFIG", str(cfg))
    config_mod.reset_config_cache()
    try:
        result = _named(tmp_path, 'print("y")\nprint("y")\nprint("y")', expected=b"y\ny\ny\n")
    finally:
        config_mod.reset_config_cache()

    assert result["verdict"] == "WA"
    assert "обрезан" in result["error"], result["error"]


def test_clean_run_has_no_spurious_error(tmp_path: pathlib.Path) -> None:
    """Обычный WA остаётся без служебной пометки (guard к issue #935)."""
    result = _named(tmp_path, 'print("a")', expected=b"b\n")

    assert result["verdict"] == "WA"
    assert result["error"] == ""


def test_dropped_format3_blocks_reach_the_result(tmp_path: pathlib.Path) -> None:
    """Урезанный набор формата 3 виден в результате, а не только в stderr.

    issue #935 (RUN-2-05): три блока входа против одного блока ожиданий давали
    «1/1 OK» и чистый JSON — CI не отличал полный прогон от урезанного.
    """
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.py").write_text("print(int(input()) * 2)\n", encoding="utf-8")
    (task_dir / "tests" / "input.txt").write_text(
        "# TEST_1:\n5\n\n# TEST_2:\n7\n\n# TEST_3:\n9\n", encoding="utf-8"
    )
    (task_dir / "tests" / "output.txt").write_text("# TEST_1:\n10\n", encoding="utf-8")

    with pytest.warns(UserWarning):
        result = run_tests(task_dir / "task.py", task_dir / "tests", timeout=_TIMEOUT)

    assert result["passed"] == 1
    assert result["warnings"], "предупреждение о неполном наборе не дошло до результата"
    assert "блок" in result["warnings"][0]


def test_borrowed_test_dir_reaches_the_result(tmp_path: pathlib.Path) -> None:
    """Набор из родительской папки назван вслух, а не подставлен молча.

    issue #917 (RUN-2-08/PY-1-06): решение в подпапке без собственных тестов
    грейдится набором уровнем выше. Если наверху тесты другой задачи, студент
    видит сплошной WA и правит верное решение — причина не была названа нигде.
    """
    course_dir = tmp_path / "lesson1"
    (course_dir / "step2").mkdir(parents=True)
    (course_dir / "input.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")
    (course_dir / "output.txt").write_text("# TEST_1:\n10\n", encoding="utf-8")
    solution = course_dir / "step2" / "task.py"
    solution.write_text("print(int(input()) * 2)\n", encoding="utf-8")

    test_dir = resolve_test_dir(solution)
    assert test_dir == course_dir, "предусловие находки: набор берётся у родителя"

    with pytest.warns(UserWarning, match="рядом с решением тест-кейсов нет"):
        result = run_tests(solution, test_dir, timeout=_TIMEOUT)

    assert result["warnings"], "предупреждение о чужом наборе не дошло до результата"
    assert str(course_dir.resolve()) in result["warnings"][-1]


def test_own_tests_subdir_is_not_reported_as_borrowed(tmp_path: pathlib.Path) -> None:
    """Штатный ``tests/`` рядом с решением предупреждения не порождает.

    Guard к issue #917: предупреждение обязано отличать «набор одолжен» от
    «набор на своём месте», иначе оно превращается в шум на каждом прогоне.
    """
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.py").write_text("print(int(input()) * 2)\n", encoding="utf-8")
    (task_dir / "tests" / "input.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")
    (task_dir / "tests" / "output.txt").write_text("# TEST_1:\n10\n", encoding="utf-8")

    result = run_tests(
        task_dir / "task.py", resolve_test_dir(task_dir / "task.py"), timeout=_TIMEOUT
    )

    assert result["warnings"] == []


class TestStrictCompareMode:
    """`--compare strict` возвращает побайтовую сверку (issue #1111).

    Режим по умолчанию (`stepik`) прощает хвостовые пробелы и хвостовые пустые
    строки — так их не различает чекер платформы. Но у побайтовой сверки есть
    своя аудитория: авторы задач и прогонный корпус, которым важно, что вывод
    совпадает буква в букву. Если бы `strict` молча вёл себя как `stepik`,
    настройка была бы декорацией.
    """

    @pytest.fixture(autouse=True)
    def _strict(self) -> Iterator[None]:
        config.override_config(compare_mode="strict")
        yield
        config.reset_config_cache()

    @pytest.mark.parametrize(
        "solution,expected,why",
        [
            ("print('hello ')", b"hello\n", "хвостовой пробел в выводе решения"),
            ("print('hello')", b"hello \n", "хвостовой пробел в файле ожиданий"),
            ("print()", b"", "пустой файл ≠ вывод из одной пустой строки"),
            ("print('a')\nprint()", b"a\n", "лишняя пустая строка в конце"),
        ],
    )
    def test_trailing_difference_is_wa(
        self, tmp_path: pathlib.Path, solution: str, expected: bytes, why: str
    ) -> None:
        assert _named(tmp_path, solution, expected=expected)["verdict"] == "WA", why

    def test_exact_match_still_passes(self, tmp_path: pathlib.Path) -> None:
        """Совпадающий вывод остаётся `AC` — строгость не ломает верное решение."""
        assert _named(tmp_path, "print('hello')", expected=b"hello\n")["verdict"] == "AC"


def test_complete_run_has_empty_warnings(tmp_path: pathlib.Path) -> None:
    """Полный набор не порождает предупреждений (guard к issue #935)."""
    task_dir = tmp_path / "task"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.py").write_text("print(int(input()) * 2)\n", encoding="utf-8")
    (task_dir / "tests" / "input.txt").write_text("# TEST_1:\n5\n", encoding="utf-8")
    (task_dir / "tests" / "output.txt").write_text("# TEST_1:\n10\n", encoding="utf-8")

    result = run_tests(task_dir / "task.py", task_dir / "tests", timeout=_TIMEOUT)

    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# Таблица здесь и таблица в документации — одно и то же (issue #1004,
# находка `ADD-5-04`). Докстринг модуля это утверждал с самого начала:
# «расхождение между ними означает, что документация врёт». Утверждение было,
# механизма под ним не было — и строка про обрезку по `max_output_bytes`
# действительно жила только тестом.
# ---------------------------------------------------------------------------

_CONFIGURATION_MD = pathlib.Path(__file__).parent.parent / "docs" / "use" / "configuration.md"

#: Строка пользовательской таблицы → фраза, обязанная встретиться в ЭТОМ файле.
#:
#: Сторона документа **выводится** из самого документа, а не переписана сюда:
#: правится он руками и дрейфует именно он. Сторона теста объявлена — это
#: ratchet: новая строка в доке без прогонного кейса краснеет, и автор отвечает
#: вслух, каким тестом правило доказано.
_DOC_ROW_TO_TEST_PHRASE = {
    "Расхождение float за 9-м знаком": "хвост за 9-м знаком отбрасывается",
    "Разная запись одного числа": "экспонента и десятичная — одно число",
    "Число внутри текста": "число внутри текста тоже нормализуется",
    "`CRLF` в файлах тестов": "def test_crlf_in_expected_file_is_forgiven",
    "Отсутствие финального перевода строки": (
        "def test_missing_trailing_newline_in_expected_is_forgiven"
    ),
    "**Пробел в конце строки**": "хвостовой пробел в выводе решения",
    "**Пустые строки в конце вывода**": "хвостовая пустая строка незначима",
    "Пробел в начале строки": "ведущий пробел значим — обрезается только хвост",
    "Другой пробельный символ внутри строки": "неразрывный пробел ≠ обычный",
    "Пустая строка в начале вывода": "пустая строка В НАЧАЛЕ значима",
    "Целое против дробного": "целое и float — разные строки",
    "Расхождение внутри 9 знаков": "расхождение внутри 9 знаков — ошибка",
    "**Знаков меньше, чем требует ожидание**": "решение не выполнило требование «до сотых»",
    "Запятая как десятичный разделитель": "запятая как десятичный разделитель не понимается",
    "Разные юникод-формы одной буквы": "def test_unicode_is_compared_without_nfc_normalization",
    "Вывод, обрезанный по `max_output_bytes`": "def test_truncated_output_says_so",
}


def _documented_comparison_rows() -> list[str]:
    """Первая колонка обеих таблиц «Что прощается» / «Что не прощается».

    Разбор останавливается на любом заголовке: ниже по документу лежит таблица
    диагностики с той же формой, и без этого она попадала бы в выборку.
    """
    rows: list[str] = []
    current: str | None = None
    for line in _CONFIGURATION_MD.read_text(encoding="utf-8").split("\n"):
        if line.startswith("#"):
            current = (
                line if line.startswith(("### Что прощается", "### Что не прощается")) else None
            )
            continue
        if current is None or not line.startswith("| ") or line.startswith(("|---", "| Что ")):
            continue
        rows.append(line.split("|")[1].strip())
    return rows


def test_every_documented_rule_is_proved_by_a_case_here() -> None:
    """Строка пользовательской таблицы доказана прогонным кейсом этого файла."""
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    undeclared = [
        row for row in _documented_comparison_rows() if row not in _DOC_ROW_TO_TEST_PHRASE
    ]
    unproved = [row for row, phrase in _DOC_ROW_TO_TEST_PHRASE.items() if phrase not in source]

    assert not undeclared and not unproved, (
        "таблица в docs/use/configuration.md разошлась с прогонными кейсами "
        "(issue #1004, находка `ADD-5-04`).\n"
        f"  строка доки без объявленного кейса: {', '.join(undeclared) or '—'}\n"
        f"  объявленный кейс не найден в этом файле: {', '.join(unproved) or '—'}"
    )


def test_no_declaration_outlives_its_documented_row() -> None:
    """Строку из доки убрали — объявление уходит вместе с ней.

    Иначе список только растёт и следующая строка проезжает под чужим,
    недействительным объяснением.
    """
    documented = set(_documented_comparison_rows())
    stale = sorted(set(_DOC_ROW_TO_TEST_PHRASE) - documented)

    assert not stale, f"объявление пережило строку документации: {', '.join(stale)}"


def test_no_anchor_matches_only_its_own_declaration() -> None:
    """Якорь обязан встретиться ВНЕ таблицы соответствия.

    Первая редакция объявляла два якоря по догадке (``trailing_blank``,
    ``leading_blank``), и они находились ровно в одном месте — в самом
    объявлении. Проверка «фраза есть в файле» на таких якорях выполняется
    всегда: сторож сверял объявление с самим собой и был зелёным по
    построению — тот же класс, что и правило, обеспеченное только текстом.
    """
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    start = source.index("_DOC_ROW_TO_TEST_PHRASE = {")
    body = source[:start] + source[source.index("\n}\n", start) :]
    only_in_declaration = [
        phrase for phrase in _DOC_ROW_TO_TEST_PHRASE.values() if phrase not in body
    ]

    assert not only_in_declaration, (
        "якорь встречается только в объявлении — сверять его с самим собой "
        f"бессмысленно: {', '.join(only_in_declaration)}"
    )


def test_the_documentation_tables_are_actually_read() -> None:
    """Guard-the-guard: таблицы прочитались.

    Пустой разбор совпал бы с пустым ожиданием, и обе проверки выше зеленели бы
    на любом документе — включая тот, где таблиц не осталось вовсе.
    """
    rows = _documented_comparison_rows()

    assert len(rows) > 10, rows
    assert "Пробел в начале строки" in rows


def test_expected_failures_stay_strict_and_named() -> None:
    """Появившийся ``xfail`` обязан быть строгим и называть issue.

    Нестрогий ``xfail`` — это молчаливый пропуск под другим именем: кейс не
    краснеет ни пока дефект жив, ни после того, как он починен, то есть
    маркер некому снять. А ``xfail`` без номера задачи не даёт ответить, чего
    он ждёт и когда его убирать.

    Разбор идёт по дереву, а не поиском подстроки: первая редакция сканировала
    текст и нашла собственную строку поиска — сторож упал на самом себе.
    """
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    markers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "xfail"
    ]

    for marker in markers:
        where = f"строка {marker.lineno}"
        strict = next((kw for kw in marker.keywords if kw.arg == "strict"), None)
        assert strict is not None and getattr(strict.value, "value", False) is True, (
            f"нестрогий xfail — это молчаливый пропуск ({where})"
        )
        reason = next((kw for kw in marker.keywords if kw.arg == "reason"), None)
        text = getattr(getattr(reason, "value", None), "value", "")
        assert "#" in str(text), f"xfail без ссылки на issue ({where})"
