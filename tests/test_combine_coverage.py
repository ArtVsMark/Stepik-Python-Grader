"""Tests for scripts/combine_coverage.py — cross-OS combine с сигналом (issue #559).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем
же приёмом, что и test_generate_coverage_badge.py / test_version_script.py.

Ключевой сценарий приёмки (#559): симуляция отсутствия .coverage.sandbox-linux
→ combine ДЕГРАДИРУЕТ с громким сигналом (::warning:: + degraded=true), а не
проглатывается молча; строгий --fail-under применяется только при полных данных.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType

import coverage
import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "combine_coverage.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_combine_coverage", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _touch_coverage(artifacts: pathlib.Path, *suffixes: str) -> None:
    """Создать .coverage.<suffix> с настоящими данными измерений.

    issue #789: пустышки больше не годятся — детектор проверяет, что файл
    читается как база coverage и содержит измерения, а не только имя.
    """
    artifacts.mkdir(parents=True, exist_ok=True)
    for suffix in suffixes:
        data = coverage.CoverageData(basename=str(artifacts / f".coverage.{suffix}"))
        data.add_lines({f"{suffix}_module.py": [1, 2, 3]})
        data.write()


# --- discover_coverage_files -------------------------------------------------


def test_discover_coverage_files_lists_suffixes_sorted(tmp_path: pathlib.Path) -> None:
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest", "sandbox-linux")
    # Посторонний файл не должен попасть в выборку.
    (art / "notes.txt").write_text("x", encoding="utf-8")
    assert _MODULE.discover_coverage_files(art) == ["sandbox-linux", "ubuntu-latest"]


def test_discover_skips_empty_and_broken_artifacts(tmp_path: pathlib.Path) -> None:
    """issue #789: имени файла мало — пустой и битый артефакт не «данные».

    Оборванная загрузка оставляет файл нулевого размера, частичная — мусор
    вместо базы SQLite. И то, и другое раньше считалось присутствующим, поэтому
    строгий cross-OS gate применялся к недосчитанным данным.
    """
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest")
    (art / ".coverage.empty").write_bytes(b"")
    (art / ".coverage.broken").write_bytes(b"not a database")

    assert _MODULE.discover_coverage_files(art) == ["ubuntu-latest"]
    assert _MODULE.unusable_files(art) == [
        ("broken", _MODULE.unusable_reason(art / ".coverage.broken")),
        ("empty", "файл пуст"),
    ]
    assert "не читается" in _MODULE.unusable_reason(art / ".coverage.broken")


def test_empty_artifact_degrades_instead_of_failing_gate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """issue #789 приёмка: пустой артефакт → внятная причина, а не голое падение.

    Замерено до фикса: строгий gate применялся к пустым данным и падал с «No
    data to report» — сообщение не называло ни файл, ни причину.
    """
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest")
    (art / ".coverage.sandbox-linux").write_bytes(b"")

    calls: list[list[str]] = []
    monkeypatch.setattr(_MODULE, "_run_coverage", lambda a: calls.append(a) or 0)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    rc = _MODULE.main(
        ["--artifacts", str(art), "--require", "ubuntu-latest", "--require", "sandbox-linux"]
    )

    assert rc == 0  # деградация, а не красный job
    out = capsys.readouterr().out
    assert ".coverage.sandbox-linux: файл пуст" in out  # названы и файл, и причина
    assert not any(any(a.startswith("--fail-under") for a in c) for c in calls)


def test_total_artifact_loss_degrades_not_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issue #789: когда не приехал НИ ОДИН артефакт, job не краснеет.

    Найдено прогоном сверх аудита: `coverage combine` на пустом каталоге даёт
    «No data to combine» и код 1, а он возвращался раньше проверки деградации —
    то есть полная потеря артефактов роняла job вопреки замыслу, при котором
    нехватка данных даёт предупреждение.
    """
    art = tmp_path / "coverage-artifacts"
    art.mkdir()

    monkeypatch.setattr(_MODULE, "_run_coverage", lambda a: 1)  # combine не смог
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    rc = _MODULE.main(
        ["--artifacts", str(art), "--require", "ubuntu-latest", "--require", "sandbox-linux"]
    )

    assert rc == 0


def test_combine_failure_on_complete_data_still_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Контроль к предыдущему: при полных данных сбой combine — настоящая
    поломка, и код возврата по-прежнему пробрасывается."""
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest", "sandbox-linux")

    monkeypatch.setattr(_MODULE, "_run_coverage", lambda a: 3 if a[0] == "combine" else 0)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    rc = _MODULE.main(
        ["--artifacts", str(art), "--require", "ubuntu-latest", "--require", "sandbox-linux"]
    )

    assert rc == 3


def test_discover_coverage_files_missing_dir_is_empty(tmp_path: pathlib.Path) -> None:
    assert _MODULE.discover_coverage_files(tmp_path / "nope") == []


# --- missing_required --------------------------------------------------------


def test_missing_required_all_present() -> None:
    present = ["ubuntu-latest", "windows-latest", "macos-latest", "sandbox-linux"]
    required = ["ubuntu-latest", "sandbox-linux"]
    assert _MODULE.missing_required(present, required) == []


def test_missing_required_detects_absence_preserving_order() -> None:
    present = ["ubuntu-latest", "macos-latest"]
    required = ["ubuntu-latest", "windows-latest", "sandbox-linux"]
    assert _MODULE.missing_required(present, required) == ["windows-latest", "sandbox-linux"]


# --- main: degraded (acceptance) ---------------------------------------------


def test_main_missing_sandbox_degrades_with_signal(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#559 приёмка: нет .coverage.sandbox-linux → warning + degraded=true, БЕЗ
    строгого --fail-under (иначе инфра-флейк ложно уронил бы combine)."""
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest", "windows-latest", "macos-latest")  # sandbox-linux нет

    calls: list[list[str]] = []
    monkeypatch.setattr(_MODULE, "_run_coverage", lambda a: calls.append(a) or 0)
    gh_output = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))

    rc = _MODULE.main(
        [
            "--artifacts",
            str(art),
            "--fail-under",
            "90",
            "--require",
            "ubuntu-latest",
            "--require",
            "windows-latest",
            "--require",
            "macos-latest",
            "--require",
            "sandbox-linux",
        ]
    )

    assert rc == 0  # деградация не роняет job — сигнал даёт warning
    out = capsys.readouterr().out
    assert "::warning" in out
    assert "sandbox-linux" in out
    # degraded=true проброшен в $GITHUB_OUTPUT (шаг бейджа его читает).
    assert "degraded=true" in gh_output.read_text(encoding="utf-8")
    # combine выполнен, но строгий --fail-under НЕ вызывался при деградации.
    assert ["combine", str(art)] in calls
    assert not any(any(arg.startswith("--fail-under") for arg in c) for c in calls)


# --- main: полные данные -----------------------------------------------------


def test_main_complete_data_runs_strict_gate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Все ожидаемые данные на месте → degraded=false + строгий --fail-under=90."""
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest", "windows-latest", "macos-latest", "sandbox-linux")

    calls: list[list[str]] = []
    monkeypatch.setattr(_MODULE, "_run_coverage", lambda a: calls.append(a) or 0)
    gh_output = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))

    rc = _MODULE.main(
        [
            "--artifacts",
            str(art),
            "--fail-under",
            "90",
            "--require",
            "ubuntu-latest",
            "--require",
            "windows-latest",
            "--require",
            "macos-latest",
            "--require",
            "sandbox-linux",
        ]
    )

    assert rc == 0
    assert "::warning" not in capsys.readouterr().out
    assert "degraded=false" in gh_output.read_text(encoding="utf-8")
    assert ["combine", str(art)] in calls
    assert ["report", "--fail-under=90"] in calls


def test_main_propagates_strict_gate_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При полных данных провал строгого gate пробрасывается ненулевым кодом."""
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest", "windows-latest", "macos-latest", "sandbox-linux")

    def fake_run(coverage_args: list[str]) -> int:
        # combine/report ок; строгий gate «падает» ниже порога.
        return 2 if any(a.startswith("--fail-under") for a in coverage_args) else 0

    monkeypatch.setattr(_MODULE, "_run_coverage", fake_run)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    rc = _MODULE.main(
        ["--artifacts", str(art), "--require", "ubuntu-latest", "--require", "sandbox-linux"]
    )
    assert rc == 2
