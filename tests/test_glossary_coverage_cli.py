"""Smoke tests for the coverage scan CLI entrypoint (issue #198).

Покрывает ``python -m stepik_grader.glossary.coverage``: печать сводки
покрытия по категориям, запись missing-очереди (SQLite/WAL, issue #552),
обработку невалидного пути к базе карточек. Расчёт покрытия/пробелов уже покрыт
``tests/test_glossary_coverage.py`` — здесь только CLI-обвязка.
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.glossary import load_missing_queue
from stepik_grader.glossary.coverage import build_coverage_report, format_report_summary, main
from stepik_grader.glossary.stdlib_inventory import build_stdlib_inventory

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "docs" / "dev" / "examples" / "glossary.sample.json"


def test_format_report_summary_lists_all_categories_and_total() -> None:
    report = build_coverage_report(build_stdlib_inventory(), known=set())
    summary = format_report_summary(report)
    assert "builtins" in summary
    assert "exceptions" in summary
    assert "stdlib" in summary
    assert "total" in summary
    assert "missing" in summary


def test_main_smoke_prints_summary_without_cards(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    out = capsys.readouterr().out
    assert "coverage" in out.lower()
    assert "missing" in out.lower()


def _total_missing(output: str) -> int:
    """Число пробелов из строки «total N/M covered, K missing»."""
    line = next(ln for ln in output.splitlines() if ln.strip().startswith("total"))
    return int(line.split("covered, ")[1].split(" missing")[0])


def test_main_with_cards_reduces_reported_missing(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--empty-base"])
    empty_base = capsys.readouterr().out

    main(["--cards", str(SAMPLE_FIXTURE)])
    with_cards = capsys.readouterr().out

    assert _total_missing(with_cards) <= _total_missing(empty_base)


# issue #957 — запуск без флагов считал покрытие относительно ПУСТОЙ базы:
# команда из документации печатала «0/1009 covered» при полной базе внутри
# самого пакета, а вместе с --missing-out дозаписывала в очередь пополнения
# почти тысячу ложных пробелов.


def _category_missing(output: str, category: str) -> int:
    """Число пробелов в строке конкретной категории сводки («… , N missing»)."""
    line = next(ln for ln in output.splitlines() if ln.strip().startswith(category))
    return int(line.rsplit(",", 1)[1].split("missing")[0].strip())


def test_main_without_cards_uses_bundled_base(capsys: pytest.CaptureFixture[str]) -> None:
    """Дефолт — встроенная база: команда из документации показывает правду.

    Проверяем категории, а не итог: `exceptions` собираются обходом уже
    загруженных в процессе подклассов `BaseException`, поэтому под pytest в
    инвентарь попадают исключения самого pytest и его плагинов — в чистом
    процессе их нет. Итоговое число зависело бы от того, чем запущен тест.
    """
    main([])
    out = capsys.readouterr().out

    assert _category_missing(out, "builtins") == 0
    assert _category_missing(out, "stdlib") == 0


def test_empty_base_keeps_the_old_behaviour(capsys: pytest.CaptureFixture[str]) -> None:
    """Пустая база осталась доступной явным флагом — как режим отладки она осмысленна."""
    main(["--empty-base"])
    out = capsys.readouterr().out

    assert _category_missing(out, "builtins") > 0
    assert _category_missing(out, "stdlib") > 0


def test_default_run_does_not_pollute_the_queue(tmp_path: pathlib.Path) -> None:
    """Запуск по умолчанию не дозаписывает в очередь то, что в базе уже есть.

    Дефект #957 наполнял очередь пополнения почти тысячей ложных пробелов —
    каждой сущностью stdlib, потому что база считалась пустой.
    """
    out_path = tmp_path / "missing.db"

    main(["--missing-out", str(out_path)])

    kinds = {entry.kind for entry in load_missing_queue(out_path)}
    assert "function" not in kinds  # stdlib-функции покрыты встроенной базой
    assert "class" not in kinds


def test_main_writes_missing_queue(tmp_path: pathlib.Path) -> None:
    out_path = tmp_path / "missing.db"
    main(["--empty-base", "--missing-out", str(out_path)])
    assert out_path.exists()
    entries = load_missing_queue(out_path)
    assert len(entries) > 0
    assert all(entry.origin == "stdlib_scan" for entry in entries)


def test_main_writing_missing_queue_twice_is_idempotent(tmp_path: pathlib.Path) -> None:
    out_path = tmp_path / "missing.db"
    main(["--empty-base", "--missing-out", str(out_path)])
    first = load_missing_queue(out_path)
    main(["--empty-base", "--missing-out", str(out_path)])
    second = load_missing_queue(out_path)
    assert len(first) == len(second)


def test_main_restricts_modules_via_flag(tmp_path: pathlib.Path) -> None:
    # Ограничение --modules влияет только на не-exception сущности курируемых
    # модулей (kind function/class); exceptions собираются рекурсивным обходом
    # BaseException по ВСЕМ уже загруженным в процессе классам (см.
    # stdlib_inventory.py), поэтому их модули отфильтровываем из проверки.
    out_path = tmp_path / "missing.db"
    main(["--empty-base", "--modules", "math", "--missing-out", str(out_path)])
    entries = load_missing_queue(out_path)
    non_exception_modules = {
        entry.module
        for entry in entries
        if entry.kind != "exception" and entry.module != "builtins"
    }
    assert non_exception_modules == {"math"}


def test_module_run_is_quiet(tmp_path: pathlib.Path) -> None:
    """issue #896: `python -m …coverage` не печатает RuntimeWarning на каждый запуск.

    Пакет реэкспортировал символы `coverage` на уровне `__init__`, поэтому runpy
    находил подмодуль уже загруженным и предупреждал о «unpredictable
    behaviour». Проверяем подпроцессом с `-W error::RuntimeWarning`: внутри
    одного процесса дефект не воспроизводится — он в том, КАК модуль
    запускается.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "stepik_grader.glossary.coverage"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=tmp_path,  # не в репозитории: команду запускают откуда угодно
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr


def test_main_invalid_cards_path_exits_with_error(tmp_path: pathlib.Path) -> None:
    # Несуществующий путь — из tmp_path: абсолютный литерал в argv адресует
    # настоящий диск разработчика, даже когда тест ждёт отказа.
    with pytest.raises(SystemExit):
        main(["--cards", str(tmp_path / "does_not_exist.json")])


def test_main_unwritable_queue_warns_not_crashes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Незаписываемая очередь пополнения не роняет coverage-CLI (issue #551/#552).

    Путь-директория вместо файла: ``sqlite3`` не откроет её как БД →
    ``append_missing_entries`` заворачивает ошибку в ``GlossaryError``. CLI ловит
    её (и ``OSError``), печатает предупреждение и завершается штатно — сводка
    покрытия уже напечатана, скан не должен падать из-за неисправного backlog.
    """
    out_path = tmp_path / "queue_is_a_dir"
    out_path.mkdir()

    main(["--modules", "math", "--missing-out", str(out_path)])  # без исключения

    out = capsys.readouterr().out
    # "Warning" стоит в начале строки предупреждения — rich-перенос по ширине
    # консоли (CI не-TTY → 80) его не рвёт.
    assert "Warning" in out
