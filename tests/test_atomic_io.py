"""Tests for atomic_io.py — общий атомарный JSON-писатель (issue #551, ADR-0011).

Проверяет контракт leaf-хелпера ``atomic_write_json``: round-trip любого
JSON-значения, создание директорий, отсутствие временных остатков, сохранность
кириллицы (``ensure_ascii=False``) и — главное — crash-safety: обрыв записи не
оставляет усечённый файл (temp-then-replace), а прежняя версия цели уцелевает.
Потокобезопасность примитива проверяется на РАЗНЫХ целях (уникальный ``mkstemp``,
нет разделяемого состояния); сериализация писателей ОДНОЙ цели — забота
вызывающего (см. ``append_missing_entries``), а не самого примитива.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time

import pytest

from stepik_grader import atomic_io
from stepik_grader.atomic_io import atomic_write_json, atomic_write_text


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


def test_atomic_write_is_thread_safe_across_distinct_targets(tmp_path: pathlib.Path) -> None:
    """``atomic_write_json`` безопасен для конкурентного вызова из многих потоков.

    У функции нет разделяемого изменяемого состояния, а ``mkstemp`` даёт
    уникальные temp'ы — поэтому N потоков, каждый пишущий СВОЮ цель по многу раз,
    все завершаются без ошибок, финалы валидны и полны, ни одного осиротевшего
    ``*.tmp`` не остаётся (уникальность temp-имён под нагрузкой).

    Сериализация писателей ОДНОЙ цели — забота вызывающего (``append_missing_entries``
    оборачивает вызов в процессный лок ``_MISSING_QUEUE_LOCK``): сам примитив её не
    обещает, а на Windows конкурентный ``os.replace`` одной цели вообще может дать
    ``PermissionError`` (sharing violation при одновременной замене) — это семантика
    ОС, а не порча. Атомарность одной записи (читатель не видит усечённого файла)
    кросс-платформенно покрывают crash-safety-тесты выше.
    """
    errors: list[Exception] = []

    def _writer(writer_id: int) -> None:
        target = tmp_path / f"data_{writer_id}.json"
        try:
            for n in range(25):
                atomic_write_json(target, {"writer": writer_id, "n": n, "payload": list(range(50))})
        except Exception as exc:  # копим для ассерта в главном потоке
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"конкурентная запись дала ошибку: {errors}"
    # Только цели, без осиротевших temp по всем потокам.
    assert sorted(tmp_path.iterdir()) == [tmp_path / f"data_{i}.json" for i in range(6)]
    for i in range(6):
        data = json.loads((tmp_path / f"data_{i}.json").read_text(encoding="utf-8"))
        assert data["writer"] == i
        assert data["n"] == 24  # последняя запись цикла
        assert data["payload"] == list(range(50))  # полная валидная версия


# ---------------------------------------------------------------------------
# issue #1136 (LNCH-3-06) — межпроцессная блокировка записи
# ---------------------------------------------------------------------------


def test_file_lock_yields_true_and_creates_sidecar(tmp_path: pathlib.Path) -> None:
    """Лок берётся и живёт в файле-спутнике, а не в самой цели.

    Цель заменяется через `os.replace`, то есть меняет inode: блокировка,
    взятая на прежнем inode, после замены не значит ничего.
    """
    target = tmp_path / "settings.json"

    with atomic_io.file_lock(target) as acquired:
        assert acquired is True
        assert (tmp_path / ("settings.json" + atomic_io.LOCK_SUFFIX)).exists()


def test_file_lock_serialises_two_threads(tmp_path: pathlib.Path) -> None:
    """Второй войти внутрь не может, пока первый не вышел."""
    target = tmp_path / "settings.json"
    order: list[str] = []
    first_inside = threading.Event()

    def slow() -> None:
        with atomic_io.file_lock(target):
            order.append("first-in")
            first_inside.set()
            time.sleep(0.3)
            order.append("first-out")

    def fast() -> None:
        first_inside.wait(timeout=5)
        with atomic_io.file_lock(target):
            order.append("second-in")

    threads = [threading.Thread(target=slow), threading.Thread(target=fast)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert order == ["first-in", "first-out", "second-in"]


def test_file_lock_gives_up_after_timeout_but_still_runs(tmp_path: pathlib.Path) -> None:
    """Не дождались — блок исполняется без блокировки, а не отменяется.

    Отказать в сохранении хуже потерянного обновления: пользователь решил бы,
    что настройка не работает вовсе.
    """
    target = tmp_path / "settings.json"
    ran: list[bool] = []

    with atomic_io.file_lock(target):
        thread_result: list[bool] = []

        def contender() -> None:
            with atomic_io.file_lock(target, timeout=0.05) as acquired:
                thread_result.append(acquired)
                ran.append(True)

        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=5)

    assert ran == [True], "блок не исполнился без блокировки"
    assert thread_result == [False], "лок отдан как взятый, хотя занят"


def test_file_lock_survives_unwritable_directory(tmp_path: pathlib.Path) -> None:
    """Спутник создать негде — работаем как раньше, без блокировки."""
    target = tmp_path / "no-such-dir" / "settings.json"

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(atomic_io.os, "open", refuse)
        with atomic_io.file_lock(target) as acquired:
            assert acquired is False


def test_file_lock_releases_on_exception(tmp_path: pathlib.Path) -> None:
    """Исключение внутри блока не оставляет лок взятым навсегда."""
    target = tmp_path / "settings.json"

    with pytest.raises(RuntimeError), atomic_io.file_lock(target):
        raise RuntimeError("сбой во время записи")

    with atomic_io.file_lock(target) as acquired:
        assert acquired is True, "лок не освобождён после исключения"


# ---------------------------------------------------------------------------
# issue #996 — единый писатель: права цели и уборка temp при ЛЮБОМ прерывании.
#
# ARCH-3-05: писателей было два, и расходились они ровно правами. Здешний
# всегда оставлял 0600, а `core/storage.save_json_file` наследовал права уже
# существующего файла — один и тот же `chmod g+r` на конфиг сохранялся или
# отменялся в зависимости от того, какой модуль его записал.
#
# PY-3-05: оба ловили только `OSError`. Ctrl+C — это `KeyboardInterrupt`, он
# `BaseException` и проходил насквозь, оставляя temp навсегда.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX-биты режима; на Windows chmod — no-op")
class TestExistingFileKeepsItsMode:
    def test_widened_mode_survives_rewrite(self, tmp_path: pathlib.Path) -> None:
        """Права существующего файла — решение пользователя, а не наше."""
        target = tmp_path / "config.json"
        atomic_write_json(target, {"v": 1})
        target.chmod(0o644)

        atomic_write_json(target, {"v": 2})

        assert target.stat().st_mode & 0o777 == 0o644
        assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}

    def test_new_file_stays_owner_only(self, tmp_path: pathlib.Path) -> None:
        """Наследовать не у кого — остаётся 0600 от `mkstemp`."""
        target = tmp_path / "fresh.json"

        atomic_write_json(target, {"v": 1})

        assert target.stat().st_mode & 0o777 == 0o600

    def test_text_writer_agrees_with_json_writer(self, tmp_path: pathlib.Path) -> None:
        """Две функции одного модуля не должны расходиться в правах."""
        target = tmp_path / "report.txt"
        atomic_write_text(target, "первая версия")
        target.chmod(0o644)

        atomic_write_text(target, "вторая версия")

        assert target.stat().st_mode & 0o777 == 0o644


class TestInterruptLeavesNoTemp:
    def _temp_files(self, directory: pathlib.Path) -> list[pathlib.Path]:
        return [p for p in directory.iterdir() if p.name.endswith(".tmp")]

    def test_keyboard_interrupt_during_write_cleans_temp(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C ровно во время записи — самый обычный способ прервать прогон."""
        target = tmp_path / "data.json"

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(atomic_io.os, "replace", _interrupt)

        with pytest.raises(KeyboardInterrupt):
            atomic_write_json(target, {"v": 1})

        assert not self._temp_files(tmp_path)

    def test_interrupt_is_not_swallowed(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Уборка temp'а не превращает прерванную запись в успешную."""
        target = tmp_path / "data.json"
        monkeypatch.setattr(
            atomic_io.os, "replace", lambda *a, **k: (_ for _ in ()).throw(SystemExit(2))
        )

        with pytest.raises(SystemExit):
            atomic_write_json(target, {"v": 1})

        assert not target.exists()

    def test_text_writer_cleans_temp_too(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "report.txt"

        def _interrupt(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(atomic_io.os, "replace", _interrupt)

        with pytest.raises(KeyboardInterrupt):
            atomic_write_text(target, "текст")

        assert not self._temp_files(tmp_path)
