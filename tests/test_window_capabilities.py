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


# --- есть, но не то (issue #1508) ------------------------------------------------


def _fake_build(root: pathlib.Path, name: str, revision: str, version: str) -> pathlib.Path:
    """Каталог билда с исполняемым файлом, который печатает заданную версию.

    Имя каталога — ровно то, которого ждёт playwright; содержимое — чужое.
    Это и есть подстановка, рекомендованная в `docs/agent/environments.md`.
    """
    binary_name = "headless_shell" if "headless" in name else "chrome"
    build = root / f"{name.replace('-', '_')}-{revision}"
    (build / "chrome-linux").mkdir(parents=True)
    binary = build / "chrome-linux" / binary_name
    binary.write_text(f'#!/bin/sh\necho "Chromium {version} "\n', encoding="utf-8")
    binary.chmod(0o755)
    return build


def _wanted_builds() -> dict[str, tuple[str, str]]:
    """Что ждёт установленный playwright: имя → (ревизия, версия браузера)."""
    import playwright

    registry = pathlib.Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    entries = json.loads(registry.read_text(encoding="utf-8"))["browsers"]
    return {
        str(e["name"]): (str(e["revision"]), str(e.get("browserVersion") or ""))
        for e in entries
        if str(e.get("name", "")) in _MODULE._CHROMIUM_BUILDS
    }


def test_the_version_is_asked_of_the_binary(tmp_path: pathlib.Path) -> None:
    """Версию говорит сам бинарь — имя каталога о содержимом не свидетельствует."""
    build = _fake_build(tmp_path, "chromium", "1", "151.0.7922.34")

    assert _MODULE.installed_browser_version(build) == "151.0.7922.34"


def test_a_build_without_a_binary_says_nothing(tmp_path: pathlib.Path) -> None:
    """Не нашли, что спросить, — это «не прочитали», а не «версия сошлась»."""
    (tmp_path / "chrome-linux").mkdir(parents=True)

    assert _MODULE.installed_browser_version(tmp_path) is None


def test_a_substituted_build_is_not_reported_as_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Главный случай: каталог назван верно, внутри браузер другой версии.

    Прежняя проверка смотрела только имя каталога и отвечала «нужные билды на
    месте» — то есть подтверждала среду, которой нет. Зелёный e2e после такого
    ответа читался как проверенная поверхность.
    """
    pytest.importorskip("playwright")
    for name, (revision, expected) in _wanted_builds().items():
        assert expected, "в реестре playwright нет browserVersion — сверять нечем"
        _fake_build(
            tmp_path,
            name,
            revision,
            "151.0.7922.34" if expected != "151.0.7922.34" else "141.0.7390.37",
        )
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    found = _MODULE._probe_browser()

    assert found.works, "подменённый браузер работает — отрицать это неверно"
    assert found.substituted, "но это не то же самое, что заявленная среда"
    assert "подменены" in found.detail


def test_the_substitution_names_both_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Без обеих версий сообщение не даёт решить, чему верить."""
    pytest.importorskip("playwright")
    wanted = _wanted_builds()
    for name, (revision, _expected) in wanted.items():
        _fake_build(tmp_path, name, revision, "151.0.7922.34")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    found = _MODULE._probe_browser()

    expected_version = next(iter(wanted.values()))[1]
    if expected_version != "151.0.7922.34":
        assert "151.0.7922.34" in found.detail
        assert expected_version in found.detail


def test_a_matching_build_is_not_called_a_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Версии сошлись — прежний ответ, без лишнего шума.

    Проверка, поднимающая тревогу на верной среде, — та же беда, что и молчание
    на неверной: её снимут первой же правкой.
    """
    pytest.importorskip("playwright")
    for name, (revision, expected) in _wanted_builds().items():
        _fake_build(tmp_path, name, revision, expected)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    found = _MODULE._probe_browser()

    assert found.works
    assert not found.substituted


def test_an_unreadable_version_is_not_a_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Версию спросить не удалось — обвинять в подмене не за что.

    Иначе окно, где бинарь не запускается по чужой причине (нет прав, другая
    раскладка каталога), получало бы предупреждение о подмене, которой нет.
    """
    pytest.importorskip("playwright")
    for name, (revision, _expected) in _wanted_builds().items():
        build = tmp_path / f"{name.replace('-', '_')}-{revision}"
        (build / "chrome-linux").mkdir(parents=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    found = _MODULE._probe_browser()

    assert found.works
    assert not found.substituted


def test_the_report_warns_about_a_substitution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Счёт «работает N из M» подмену не показывает — нужна отдельная строка."""
    swapped = _MODULE.Capability("браузер (e2e)", True, "билды подменены", substituted=True)
    monkeypatch.setattr(_MODULE, "measure", lambda: [swapped])

    _MODULE.main([])

    said = capsys.readouterr().out
    assert "ВНИМАНИЕ" in said
    assert "зелёный ничего не подтверждает" in said


def test_machine_output_carries_the_substitution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Читающий JSON обязан видеть то же, что и читающий текст."""
    swapped = _MODULE.Capability("браузер (e2e)", True, "билды подменены", substituted=True)
    monkeypatch.setattr(_MODULE, "measure", lambda: [swapped])

    _MODULE.main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["substituted"] is True
