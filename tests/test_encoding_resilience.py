"""Устойчивость к не-UTF8 байтам в локальных данных (issue #792).

Четыре независимых места читали чужие байты и падали **не тем** исключением:
`UnicodeDecodeError` — подкласс `ValueError`, а перехваты стояли на `OSError`.
На Windows-RU это означало, что один посторонний байт (правка файла сторонним
редактором, решение в cp1251, оборванная синхронизация) ронял интерактивное
меню, команду `stats` или сам грейдинг — вместо того чтобы деградировать до
пустого/дефолтного значения.

Каждый тест здесь подсовывает ровно такой байт и проверяет, что функция
возвращает осмысленный результат, а не поднимает исключение. Кодировка вывода
`ruff` проверяется отдельно: там дефект был не в падении, а в мусоре на экране.
"""

from __future__ import annotations

import pathlib

from stepik_grader.core import lint, stats
from stepik_grader.core.cache import GraderCache
from stepik_grader.core.mode_detector import _detect_run_mode
from stepik_grader.core.user_settings import load_settings

# Байт 0xff недопустим в UTF-8 в любой позиции — самый короткий способ
# получить ровно ту ошибку, что ловили в проде.
_BAD_BYTE = b"\xff"


# ---------------------------------------------------------------------------
# FST-01 — пользовательские настройки
# ---------------------------------------------------------------------------


def test_settings_survive_invalid_utf8_inside_value(tmp_path: pathlib.Path) -> None:
    """Битый байт в значении не роняет меню, а валидные поля переживают правку.

    Байт заменяется на U+FFFD, JSON остаётся разбираемым — портится ровно одно
    значение, остальные настройки пользователь не теряет.
    """
    path = tmp_path / ".grader_settings.json"
    path.write_bytes(b'{"record_history": true, "junk": "' + _BAD_BYTE + b'"}')

    settings = load_settings(path)

    assert settings.record_history is True


def test_settings_survive_invalid_utf8_breaking_json(tmp_path: pathlib.Path) -> None:
    """Байт в структуре ломает JSON — тогда дефолты, но по-прежнему без падения."""
    path = tmp_path / ".grader_settings.json"
    path.write_bytes(b'{"record_history": true, ' + _BAD_BYTE + b"}")

    settings = load_settings(path)

    assert settings.record_history is None
    assert settings.ai_hint_consent is None


def test_settings_still_read_valid_file(tmp_path: pathlib.Path) -> None:
    """Обычный файл читается как раньше — фикс не поменял нормальный путь."""
    path = tmp_path / ".grader_settings.json"
    path.write_text('{"record_history": true}', encoding="utf-8")

    assert load_settings(path).record_history is True


# ---------------------------------------------------------------------------
# FST-02 — журнал статистики
# ---------------------------------------------------------------------------


def test_stats_summary_survives_invalid_utf8(tmp_path: pathlib.Path) -> None:
    """Испорченная строка журнала пропускается, а не роняет команду `stats`.

    Байт кладётся в СТРУКТУРУ строки, а не в значение: после замены на U+FFFD
    такая строка перестаёт быть валидным JSON и отбрасывается — а прежде на ней
    падала вся команда.
    """
    path = tmp_path / ".grader_stats.jsonl"
    good = b'{"mode": 1, "verdicts": {"AC": 2}, "total_time": 1.5, "os": "Windows"}\n'
    broken = b'{"mode": 2, ' + _BAD_BYTE + b"}\n"
    path.write_bytes(good + broken + good)

    summary = stats.read_summary(path)

    # Две валидные записи прочитаны, битая пропущена — сводка не потеряна целиком.
    assert summary["total_runs"] == 2


def test_stats_record_run_survives_invalid_utf8_during_rotation(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """Ротация журнала с битым байтом не роняет ЗАПИСЬ нового прогона.

    Коварная часть находки: ротация вызывается перед каждой записью, поэтому
    посторонний байт ронял не только чтение сводки, но и сам прогон — правда,
    лишь после того, как журнал перерастёт лимит.
    """
    path = tmp_path / ".grader_stats.jsonl"
    path.write_bytes(b'{"mode": 1, "x": "' + _BAD_BYTE + b'"}\n' * 50)
    monkeypatch.setattr(stats, "_MAX_BYTES", 10)  # заставить ротацию сработать

    stats.record_run(1, {"AC": 1}, 0.5, stats_path=path)

    assert path.exists()


# ---------------------------------------------------------------------------
# FST-04 — кэш результатов
# ---------------------------------------------------------------------------


def test_cache_get_survives_non_dict_entry(tmp_path: pathlib.Path) -> None:
    """Не-словарная запись кэша даёт промах, а не AttributeError.

    Прежняя проверка `if not entry` пропускала непустую строку из
    повреждённого или чужого `results.json`, и следующий `entry.get(...)`
    падал мимо всех перехватов.
    """
    cache_dir = tmp_path / ".grader_cache"
    cache_dir.mkdir()
    solution = tmp_path / "task.py"
    solution.write_text("print(1)\n", encoding="utf-8")

    cache = GraderCache(cache_dir=cache_dir)
    cache._data["entries"] = {cache._key(solution): "не словарь"}

    assert cache.get(solution, "sha-solution", "sha-tests", env="sha-env") is None


# ---------------------------------------------------------------------------
# PY-03 — решение в чужой кодировке
# ---------------------------------------------------------------------------


def test_run_mode_detection_survives_cp1251_solution(tmp_path: pathlib.Path) -> None:
    """Решение в cp1251 (обычное дело на Windows-RU) не роняет грейдинг."""
    solution = tmp_path / "task.py"
    solution.write_bytes("# комментарий по-русски\nprint(input())\n".encode("cp1251"))
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    assert _detect_run_mode(solution, tests_dir) == "stdin"


def test_run_mode_detection_survives_cp1251_type_file(tmp_path: pathlib.Path) -> None:
    """То же для `.type`-файла рядом с тестами."""
    solution = tmp_path / "task.py"
    solution.write_text("def solve(x):\n    return x\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "1.type").write_bytes("функция".encode("cp1251"))

    # Не «function»: строка не совпала с маркером — но и не падение.
    assert _detect_run_mode(solution, tests_dir) == "function"  # по AST: файл function-only


# ---------------------------------------------------------------------------
# PY-08 — вывод ruff
# ---------------------------------------------------------------------------


def test_lint_reads_ruff_output_as_utf8(tmp_path: pathlib.Path) -> None:
    """Кириллица в сообщении ruff доходит целой, а не мохрованием.

    `text=True` без явной кодировки декодирует системной кодовой страницей: на
    Windows-RU «щ» превращалось в «С‰», а редкий байт давал UnicodeDecodeError
    мимо перехвата (тот ловит только OSError/SubprocessError).
    """
    path = tmp_path / "task.py"
    path.write_text("def f():\n    длинное_имя = 1\n    return 2\n", encoding="utf-8")

    findings = lint.run_lint(path, select="F,E")

    messages = " ".join(str(getattr(f, "message", f)) for f in findings)
    assert "длинное_имя" in messages, f"имя пришло искажённым: {messages!r}"


# ---------------------------------------------------------------------------
# PY-04 — CONFIG.encoding не должен управлять потоками решения
# ---------------------------------------------------------------------------


def test_child_streams_stay_utf8_regardless_of_config(tmp_path: pathlib.Path, monkeypatch) -> None:
    """Смена `CONFIG.encoding` не ломает сравнение вывода.

    Раннеры принудительно ставят решению `PYTHONIOENCODING=utf-8`, поэтому
    кодировать stdin и декодировать stdout чем-то другим значит гарантированно
    рассинхронизировать стороны: пользователь, поставивший `encoding = "cp1251"`
    ради чтения своих файлов, получал тихие ложные `WA` с кириллицей — и
    отладить это со стороны было практически невозможно.
    """
    from stepik_grader.core import grader_core

    monkeypatch.setattr(grader_core, "ENCODING", "cp1251")

    solution = tmp_path / "task.py"
    solution.write_text("print(input().upper())\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "input_1.txt").write_text("привет", encoding="utf-8")
    (tests_dir / "expected_1.txt").write_text("ПРИВЕТ", encoding="utf-8")

    result = grader_core.run_tests(solution, tests_dir, timeout=15.0)

    assert result["cases"][0]["verdict"] == "AC", result["cases"][0]
