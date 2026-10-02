"""Тесты scripts/check_catalogue_name.py — имя каталога не должно быть редиректом.

Каталог переименовали, и не сломалось ничего: клон по старому имени GitHub
переадресует. Поэтому заметить переименование было нечем — механизмы оставались
зелёными, а репозиторий уже назывался иначе.

Отдельный предмет здесь — САМА проверка. Первая её редакция спрашивала HTML-
страницу и считала «редиректа нет» по любому ответу, не являющемуся 3xx, — в том
числе по 403, которым облачной сессии отвечает прокси. Проверка, написанная
против ложного зелёного, зеленела сама, ничего не спросив. Поэтому здесь
проверяется не только находка, но и то, что неопределённый ответ становится
третьим исходом.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_catalogue_name.py"


@pytest.fixture
def guard() -> ModuleType:
    """Свежий модуль на каждый тест."""
    spec = importlib.util.spec_from_file_location("_check_catalogue_name", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bindings(root: pathlib.Path, url: str) -> pathlib.Path:
    target = root / "bindings.json"
    target.write_text(json.dumps({"catalogue": url}), encoding="utf-8")
    return target


def test_a_renamed_catalogue_is_a_finding(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Канон отличается от объявленного — переименование."""
    monkeypatch.setattr(guard, "canonical_name", lambda _repo: "ArtVsMark/Новое-Имя")
    path = _bindings(tmp_path, "https://github.com/ArtVsMark/Старое-Имя")

    assert guard.main(["--bindings", str(path)]) == guard.EXIT_FINDING


def test_a_canonical_name_is_clean(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Канон совпал — находки нет."""
    monkeypatch.setattr(guard, "canonical_name", lambda repo: repo)
    path = _bindings(tmp_path, "https://github.com/ArtVsMark/Каталог")

    assert guard.main(["--bindings", str(path)]) == guard.EXIT_OK


def test_case_differences_are_not_a_rename(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Регистр имени репозитория GitHub не различает — и мы не различаем."""
    monkeypatch.setattr(guard, "canonical_name", lambda _repo: "artvsmark/КАТАЛОГ")
    path = _bindings(tmp_path, "https://github.com/ArtVsMark/каталог")

    assert guard.main(["--bindings", str(path)]) == guard.EXIT_OK


def test_a_refused_request_is_the_third_outcome(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """«Не спросили» — 2, а не «всё в порядке».

    Прежняя редакция читала 403 прокси как «редиректа нет» и отвечала нулём.
    Это и есть ложный зелёный, ради которого проверка написана.
    """

    def refuse(_repo: str) -> str:
        raise RuntimeError("repos/…: GitHub отказал (403)")

    monkeypatch.setattr(guard, "canonical_name", refuse)
    path = _bindings(tmp_path, "https://github.com/ArtVsMark/Каталог")

    assert guard.main(["--bindings", str(path)]) == guard.EXIT_BROKEN


def test_an_answer_without_a_name_is_not_an_answer(
    guard: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ответ без `full_name` каноном не является."""
    monkeypatch.setattr(guard.gh_rest, "_get", lambda _path: {})

    with pytest.raises(RuntimeError, match="full_name"):
        guard.canonical_name("ArtVsMark/Каталог")


def test_an_empty_catalogue_field_names_the_subject(
    guard: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Пустое поле — отказ с адресом файла, а не молчание."""
    path = _bindings(tmp_path, "")

    assert guard.main(["--bindings", str(path)]) == guard.EXIT_BROKEN
