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
    """Создать пустые .coverage.<suffix> файлы (детектору важно лишь имя)."""
    artifacts.mkdir(parents=True, exist_ok=True)
    for suffix in suffixes:
        (artifacts / f".coverage.{suffix}").write_text("", encoding="utf-8")


# --- discover_coverage_files -------------------------------------------------


def test_discover_coverage_files_lists_suffixes_sorted(tmp_path: pathlib.Path) -> None:
    art = tmp_path / "coverage-artifacts"
    _touch_coverage(art, "ubuntu-latest", "sandbox-linux")
    # Посторонний файл не должен попасть в выборку.
    (art / "notes.txt").write_text("x", encoding="utf-8")
    assert _MODULE.discover_coverage_files(art) == ["sandbox-linux", "ubuntu-latest"]


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
