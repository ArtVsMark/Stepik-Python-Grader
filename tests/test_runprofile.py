"""Tests for core/runprofile.py — паспорт условий прогона (issue #984).

Паспорт отвечает на вопрос «чем именно проверяли»: он печатается в шапке
отчёта и входит в ключ кэша, поэтому здесь проверяется ровно две вещи — что
он снимается с ДЕЙСТВУЮЩИХ настроек и активного runner'а, и что его отпечаток
меняется тогда и только тогда, когда меняются условия исполнения.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from stepik_grader.core.runprofile import RunProfile, current_profile


def _profile(**overrides: object) -> RunProfile:
    """Паспорт с дефолтами теста; ``overrides`` меняет отдельные поля."""
    base = RunProfile(
        runner="LocalRunner",
        sandbox_backend=None,
        timeout_seconds=10.0,
        max_output_bytes=1024,
        max_memory_mb=512,
        sandbox_max_cpu_seconds=10.0,
        sandbox_max_processes=32,
        sandbox_max_output_bytes=1024,
        measure_child_memory=True,
        encoding="utf-8",
        python="3.12.7",
        package_version="1.10.0",
        config_path=None,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_fingerprint_is_stable_for_same_conditions() -> None:
    """Одинаковые условия — одинаковый отпечаток (иначе кэш бесполезен)."""
    assert _profile().fingerprint == _profile().fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runner", "SandboxRunner"),
        ("sandbox_backend", "WindowsSandboxRunner"),
        ("timeout_seconds", 0.5),
        ("max_output_bytes", 2048),
        ("max_memory_mb", None),
        ("sandbox_max_cpu_seconds", 1.0),
        ("sandbox_max_processes", 8),
        ("sandbox_max_output_bytes", 2048),
        ("measure_child_memory", False),
        ("encoding", "cp1251"),
        ("python", "3.13.1"),
        ("package_version", "1.11.0"),
    ],
)
def test_fingerprint_changes_with_execution_conditions(field: str, value: object) -> None:
    """Любое условие исполнения меняет отпечаток — вердикт пересчитывается.

    Репро SBX-5-01/CNC-5-05: вердикт из-под ``--sandbox`` отдавался обычному
    прогону и наоборот, потому что ключ кэша об изоляции не знал вовсе.
    """
    assert _profile(**{field: value}).fingerprint != _profile().fingerprint


def test_fingerprint_ignores_config_path(tmp_path: pathlib.Path) -> None:
    """Путь конфига в отпечаток не входит — значимы значения, а не файл.

    Иначе одна и та же конфигурация, скопированная в соседнюю папку, давала бы
    промах кэша на ровном месте.
    """
    assert _profile(config_path=tmp_path / "pyproject.toml").fingerprint == _profile().fingerprint


def test_describe_names_sandbox_backend() -> None:
    """Гарантии изоляции у трёх backend'ов разные — в отчёте виден конкретный."""
    assert _profile().describe() == "LocalRunner"
    assert (
        _profile(runner="SandboxRunner", sandbox_backend="LinuxSandboxRunner").describe()
        == "SandboxRunner (LinuxSandboxRunner)"
    )


def test_isolated_reflects_sandbox() -> None:
    assert _profile().isolated is False
    assert _profile(sandbox_backend="LinuxSandboxRunner").isolated is True


def test_current_profile_reads_active_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Паспорт снимается в момент вызова: ``--sandbox`` подменяет runner позже."""
    from stepik_grader.core import runner as runner_mod

    class FakeSandboxRunner:
        backend_name = "FakeBackend"

        def run(self, spec: object) -> object:  # pragma: no cover — не вызывается
            raise AssertionError("паспорт не должен запускать код")

    monkeypatch.setattr(runner_mod, "_RUNNER", FakeSandboxRunner())

    profile = current_profile()

    assert profile.runner == "FakeSandboxRunner"
    assert profile.sandbox_backend == "FakeBackend"
    assert profile.isolated is True


def test_current_profile_reads_current_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Значения берутся из действующего конфига, а не из снимка времени импорта."""
    from stepik_grader.config import GraderConfig
    from stepik_grader.core import runprofile

    monkeypatch.setattr(
        runprofile, "get_config", lambda: GraderConfig(timeout_seconds=3.5, encoding="cp1251")
    )

    profile = current_profile()

    assert profile.timeout_seconds == 3.5
    assert profile.encoding == "cp1251"
