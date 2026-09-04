"""Тесты пробы возможностей окна (issue #1445).

Проба отвечает на вопрос «поедет ли», а не «лежит ли», и разница между ними —
весь смысл файла. Поэтому проверяется она подделками окружения: каталог с
нужным билдом и без него, backend, который строится и который отказывает.

Отдельно проверяется, что отчёт **не** становится гейтом: у окон разные
возможности по замыслу, и отсутствующая песочница в облаке — свойство, а не
поломка. Красный прогон здесь означал бы «почини окружение», а чинить нечего.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "window_capabilities.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_window_capabilities", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


# --- отчёт, а не гейт ------------------------------------------------------------


def test_the_report_never_fails_the_run(capsys: pytest.CaptureFixture[str]) -> None:
    """Код возврата всегда 0 — иначе облако краснело бы за отсутствие песочницы."""
    assert _MODULE.main([]) == 0
    assert "Возможности окна" in capsys.readouterr().out


def test_every_capability_is_named_with_its_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    """Каждая проба печатает и предмет, и исход — «работает: N из M» в конце.

    Молчание про пробу означало бы и «не проверяли», и «всё хорошо».
    """
    _MODULE.main([])

    out = capsys.readouterr().out
    assert out.count("[") >= len(_MODULE.PROBES)
    assert "Работает:" in out


def test_machine_output_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """``--json`` разбирается: отчёт годится соседнему инструменту, а не только глазу."""
    assert _MODULE.main(["--json"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert len(payload) == len(_MODULE.PROBES)
    for entry in payload:
        assert {"name", "works", "detail"} <= set(entry)


# --- проба отвечает на «поедет ли», а не «лежит ли» -------------------------------


def test_a_directory_without_the_wanted_build_is_not_a_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Каталог браузеров есть, нужного билда в нём нет — значит не поедет.

    Ровно этот случай и был живым: `ls /opt/pw-browsers` показывал каталог,
    playwright ждал другой билд, прогон падал «Executable doesn't exist», а
    снаружи это читалось как «браузера здесь нет».
    """
    pytest.importorskip("playwright")
    (tmp_path / "chromium-1").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    found = _MODULE._probe_browser()

    assert not found.works
    assert "нет в" in found.detail


def test_a_missing_browsers_path_is_named_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменная не задана — так и сказано, а не «браузер сломан»."""
    pytest.importorskip("playwright")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    found = _MODULE._probe_browser()

    assert not found.works
    assert "PLAYWRIGHT_BROWSERS_PATH" in found.detail


def test_experimental_builds_are_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Реестр playwright перечисляет и `tip-of-tree` — набор ими не пользуется.

    Требовать их значило бы объявлять окно негодным из-за того, чего оно и не
    должно уметь: гейт, краснеющий на верном ответе, снимают первой же правкой.
    """
    assert _MODULE._CHROMIUM_BUILDS == frozenset({"chromium", "chromium-headless-shell"})


def test_the_sandbox_probe_builds_a_runner_not_a_path_check() -> None:
    """Песочница проверяется построением runner'а, а не поиском файла.

    `which bwrap` отвечает «лежит ли»; вопрос же в том, соберётся ли backend —
    он падает и по причинам, не связанным с наличием бинарника.
    """
    source = _SCRIPT.read_text(encoding="utf-8")

    assert "SandboxRunner()" in source
    assert 'shutil.which("bwrap")' not in source


# --- отказ одной пробы не уносит отчёт -------------------------------------------


def test_a_broken_probe_does_not_hide_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проба упала — остальные всё равно измерены.

    «Не знаю про остальные» хуже, чем «эта не работает»: отчёт нужен целиком,
    иначе окно снова маршрутизирует работу по догадке.
    """

    def _boom() -> _MODULE.Capability:
        raise RuntimeError("проба сломалась")

    monkeypatch.setattr(_MODULE, "PROBES", (_boom, _MODULE._probe_git_tags))

    found = _MODULE.measure()

    assert len(found) == 2
    assert not found[0].works and "проба упала" in found[0].detail
    assert found[1].name.startswith("теги")
