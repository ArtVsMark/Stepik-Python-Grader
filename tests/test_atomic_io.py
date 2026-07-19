"""Tests for atomic_io.py — общий атомарный JSON-писатель (issue #551, ADR-0011).

Проверяет контракт leaf-хелпера ``atomic_write_json``: round-trip любого
JSON-значения, создание директорий, отсутствие временных остатков, сохранность
кириллицы (``ensure_ascii=False``) и — главное — crash-safety: обрыв записи не
оставляет усечённый файл (temp-then-replace), а прежняя версия цели уцелевает.
Атомарность под конкуренцией: параллельные писатели одной цели никогда не видят
битый/усечённый JSON (уникальный ``mkstemp`` + ``os.replace``).
"""

from __future__ import annotations

import json
import pathlib
import threading

import pytest

from stepik_grader import atomic_io
from stepik_grader.atomic_io import atomic_write_json


def test_roundtrip_dict(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"a": 1, "b": [2, 3]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [2, 3]}


def test_roundtrip_list(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, [1, "two", {"three": 3}])
    assert json.loads(path.read_text(encoding="utf-8")) == [1, "two", {"three": 3}]


def test_creates_parent_directories(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "deep" / "nested" / "data.json"
    atomic_write_json(path, {"ok": True})
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


def test_no_tmp_leftover_after_success(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"ok": True})
    assert path.exists()
    assert list(tmp_path.iterdir()) == [path]  # ровно цель, никаких *.tmp остатков


def test_preserves_cyrillic_without_ascii_escaping(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"термин": "значение"})
    raw = path.read_text(encoding="utf-8")
    assert "термин" in raw  # ensure_ascii=False → без \uXXXX-эскейпинга
    assert json.loads(raw) == {"термин": "значение"}


@pytest.mark.parametrize("fsync", [True, False])
def test_roundtrip_both_fsync_modes(tmp_path: pathlib.Path, fsync: bool) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"fsync": fsync}, fsync=fsync)
    assert json.loads(path.read_text(encoding="utf-8")) == {"fsync": fsync}


def test_overwrite_replaces_previous_content(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"v": 1})
    atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}
    assert list(tmp_path.iterdir()) == [path]


def test_crash_during_write_leaves_previous_target_intact(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обрыв записи (сбой fsync) НЕ трогает существующую цель и не оставляет temp.

    Это и есть смысл temp-then-replace: прежний ``open("w")`` сначала обрезал
    цель, поэтому краш посреди записи рвал файл. Здесь fsync падает уже после
    записи temp, но ДО ``replace`` — цель обязана остаться прежней.
    """
    path = tmp_path / "data.json"
    atomic_write_json(path, {"v": "old"})  # предыдущая полная версия

    def _boom(_fd: int) -> None:
        raise OSError("disk full during fsync")

    monkeypatch.setattr(atomic_io.os, "fsync", _boom)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_json(path, {"v": "new"}, fsync=True)

    assert json.loads(path.read_text(encoding="utf-8")) == {"v": "old"}  # цель цела
    assert list(tmp_path.iterdir()) == [path]  # temp best-effort убран


def test_crash_during_write_creates_no_partial_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Если цели ещё не было, сбой записи не создаёт усечённый файл на её месте."""
    path = tmp_path / "data.json"

    def _boom(_fd: int) -> None:
        raise OSError("disk full during fsync")

    monkeypatch.setattr(atomic_io.os, "fsync", _boom)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_json(path, {"v": "new"}, fsync=True)

    assert not path.exists()  # частичной цели нет
    assert list(tmp_path.iterdir()) == []  # temp убран


def test_replace_failure_propagates_and_cleans_temp(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сбой самого ``replace`` пробрасывается наружу, temp убирается."""
    path = tmp_path / "data.json"

    def _boom(self: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
        raise OSError("replace failed")

    monkeypatch.setattr(pathlib.Path, "replace", _boom)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_json(path, {"v": 1})

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []  # temp best-effort убран


def test_concurrent_writers_keep_file_valid_and_leak_no_temp(tmp_path: pathlib.Path) -> None:
    """Параллельные писатели одной цели: финал — полная валидная версия, без temp-остатков.

    Уникальный ``mkstemp`` (а не общий ``.tmp``) гарантирует, что писатели не
    делят временный файл, а ``os.replace`` атомарен — конкурентные замены не
    затирают друг друга в недопустимое состояние, финал — вывод одного из
    писателей целиком, все temp'ы поглощены своими ``replace``.

    Намеренно БЕЗ конкурентного чтения: на Windows ``os.replace`` не может
    заменить файл, открытый на чтение другим потоком (замена упала бы
    ``PermissionError`` — это семантика файловых блокировок ОС, а не порча;
    атомарность записи от этого не страдает, читатель держит прежний inode). На
    POSIX читатель дополнительно никогда не видит усечённую версию (inode-swap);
    эту гарантию для читателя кросс-платформенно покрывают crash-safety-тесты
    выше (цель либо старая полная, либо новая полная, но не усечённая).
    """
    path = tmp_path / "data.json"
    errors: list[Exception] = []

    def _writer(writer_id: int) -> None:
        try:
            for _ in range(25):
                atomic_write_json(path, {"writer": writer_id, "payload": list(range(50))})
        except Exception as exc:  # копим для ассерта в главном потоке
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"конкурентная запись дала ошибку: {errors}"
    data = json.loads(path.read_text(encoding="utf-8"))  # финал — полный валидный JSON
    assert data["payload"] == list(range(50))  # версия одного писателя целиком
    assert 0 <= data["writer"] < 6
    assert list(tmp_path.iterdir()) == [path]  # без осиротевших temp
