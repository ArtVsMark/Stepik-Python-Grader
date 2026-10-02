"""Тесты для core/tests_writer.py — запись форматов тест-кейсов (issue #302).

Выделено из test_downloader_extra.py: save_tests (Format 1: N/N.clue/N.type) +
write_testblock_tests (Format 3: input.txt/output.txt с # TEST_N:).
"""

from __future__ import annotations

import pathlib

import pytest

from stepik_grader.core.tests_writer import (
    backup_dir_for,
    reset_tests_dir,
    save_tests,
    write_testblock_tests,
)


class TestSaveTests:
    """save_tests пишет N / N.clue / N.type (Format 1)."""

    def test_writes_files(self, tmp_path: pathlib.Path):
        tests = [("in1", "out1", "stdin"), ("a=1", "1", "function")]
        count = save_tests(tmp_path, tests)
        assert count == 2
        tdir = tmp_path / "tests"
        assert (tdir / "1").read_text() == "in1"
        assert (tdir / "1.clue").read_text() == "out1"
        assert not (tdir / "1.type").exists()
        assert (tdir / "2.type").read_text() == "function"


class TestWriteTestblockTests:
    """write_testblock_tests пишет input.txt/output.txt с маркерами (Format 3)."""

    def test_writes_format3_with_markers(self, tmp_path: pathlib.Path):
        tests_dir = tmp_path / "tests"
        count = write_testblock_tests(tests_dir, {1: ("10\n20", "60"), 2: ("5", "15")})
        assert count == 2
        input_text = (tests_dir / "input.txt").read_text(encoding="utf-8")
        output_text = (tests_dir / "output.txt").read_text(encoding="utf-8")
        assert input_text.startswith("# INPUT DATA:\n")
        assert output_text.startswith("# OUTPUT DATA:\n")
        assert "# TEST_1:\n10\n20\n" in input_text
        assert "# TEST_2:\n5\n" in input_text
        assert "# TEST_1:\n60\n" in output_text

    def test_blocks_sorted_numerically(self, tmp_path: pathlib.Path):
        tests_dir = tmp_path / "tests"
        write_testblock_tests(tests_dir, {10: ("ten", "TEN"), 2: ("two", "TWO")})
        input_text = (tests_dir / "input.txt").read_text(encoding="utf-8")
        assert input_text.index("# TEST_2:") < input_text.index("# TEST_10:")


class TestReDownloadCleansStale:
    """issue #394: перезапись очищает tests/ — устаревшие артефакты не остаются
    и не дают смешанный набор с тихим неверным вердиктом."""

    def test_fewer_cases_removes_stale_files(self, tmp_path: pathlib.Path):
        save_tests(
            tmp_path,
            [("i1", "o1", "stdin"), ("i2", "o2", "stdin"), ("i3", "o3", "stdin")],
        )
        tdir = tmp_path / "tests"
        assert (tdir / "3").exists()

        # перескачивание с меньшим числом кейсов
        save_tests(tmp_path, [("j1", "p1", "stdin")])

        assert (tdir / "1").read_text() == "j1"
        assert not (tdir / "2").exists()
        assert not (tdir / "3").exists()
        assert not (tdir / "3.clue").exists()

    def test_format_switch_removes_old_format1_files(self, tmp_path: pathlib.Path):
        save_tests(tmp_path, [("i1", "o1", "stdin")])
        tdir = tmp_path / "tests"
        assert (tdir / "1").exists()

        # перескачивание в Format 3 — старые N/N.clue не должны перебивать вердикт
        write_testblock_tests(tdir, {1: ("10", "20")})

        assert (tdir / "input.txt").exists()
        assert not (tdir / "1").exists()
        assert not (tdir / "1.clue").exists()

    def test_symlinked_tests_dir_does_not_crash(self, tmp_path: pathlib.Path) -> None:
        """issue #394 regression: tests/ как симлинк не роняет запись —
        shutil.rmtree(символической ссылки) кидал OSError, а старый
        mkdir(exist_ok=True) не падал. Чистим содержимое, а не узел."""
        real = tmp_path / "real_tests"
        real.mkdir()
        (real / "stale").write_text("old", encoding="utf-8")
        link = tmp_path / "tests"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported on this platform/privilege")

        # save_tests пишет в tmp_path/tests (симлинк) — не должно бросать
        save_tests(tmp_path, [("i1", "o1", "stdin")])

        assert (link / "1").read_text() == "i1"
        assert not (link / "stale").exists()  # устаревшее очищено сквозь симлинк


class TestBackupOfPreviousTests:
    """issue #951: перескачивание не уничтожает дописанные вручную кейсы.

    Очистка каталога сама по себе осмысленна (issue #394) — устаревшие файлы
    дают тихий неверный вердикт. Но удаление шло без разбора и без копии, а
    отличить свой ``5.clue`` от сгенерированного прошлым скачиванием по имени
    невозможно: имена совпадают ровно потому, что формат один. Поэтому прежнее
    содержимое переносится в ``tests.bak/``, а не стирается.
    """

    def test_manual_cases_survive_redownload(self, tmp_path: pathlib.Path) -> None:
        """Сценарий из постановки: дописанные кейсы доступны после save_tests."""
        save_tests(tmp_path, [("i1", "o1", "stdin")])
        tdir = tmp_path / "tests"
        (tdir / "input_4.txt").write_text("своё", encoding="utf-8")
        (tdir / "expected_4.txt").write_text("ожидание", encoding="utf-8")
        (tdir / "5.clue").write_text("контрпример", encoding="utf-8")

        save_tests(tmp_path, [("j1", "p1", "stdin")])  # «обновлю условие»

        backup = backup_dir_for(tdir)
        assert (backup / "input_4.txt").read_text(encoding="utf-8") == "своё"
        assert (backup / "expected_4.txt").read_text(encoding="utf-8") == "ожидание"
        assert (backup / "5.clue").read_text(encoding="utf-8") == "контрпример"
        assert (tdir / "1").read_text(encoding="utf-8") == "j1"  # новый набор на месте

    def test_second_redownload_keeps_the_manual_copy(self, tmp_path: pathlib.Path) -> None:
        """Копия не затирается следующим перескачиванием.

        Самый обидный сценарий: кейсы спаслись в ``tests.bak/``, а второе
        подряд скачивание положило бы туда только что сгенерированный набор —
        и спасённое исчезло бы шагом позже. Первая вытесненная версия
        сохраняется.
        """
        save_tests(tmp_path, [("i1", "o1", "stdin")])
        tdir = tmp_path / "tests"
        (tdir / "1.clue").write_text("моя правка", encoding="utf-8")

        save_tests(tmp_path, [("j1", "p1", "stdin")])
        save_tests(tmp_path, [("k1", "q1", "stdin")])

        assert (backup_dir_for(tdir) / "1.clue").read_text(encoding="utf-8") == "моя правка"

    def test_backup_survives_format_switch(self, tmp_path: pathlib.Path) -> None:
        """Смена формата тоже проходит через копию — очистка та же самая."""
        save_tests(tmp_path, [("i1", "o1", "stdin")])
        tdir = tmp_path / "tests"

        write_testblock_tests(tdir, {1: ("10", "20")})

        assert (tdir / "input.txt").exists()
        assert (backup_dir_for(tdir) / "1").read_text(encoding="utf-8") == "i1"

    def test_reset_reports_how_many_files_were_saved(self, tmp_path: pathlib.Path) -> None:
        """Число перенесённых возвращается — вызывающая сторона его показывает."""
        tdir = tmp_path / "tests"
        tdir.mkdir()
        for name in ("1", "1.clue", "мой_кейс.txt"):
            (tdir / name).write_text("x", encoding="utf-8")

        assert reset_tests_dir(tdir) == 3
        assert reset_tests_dir(tdir) == 0  # каталог уже пуст — повтор ничего не делает

    def test_nested_directories_are_moved_too(self, tmp_path: pathlib.Path) -> None:
        """Свой подкаталог внутри tests/ — тоже работа пользователя."""
        tdir = tmp_path / "tests"
        (tdir / "мои_кейсы").mkdir(parents=True)
        (tdir / "мои_кейсы" / "note.md").write_text("заметка", encoding="utf-8")

        save_tests(tmp_path, [("i1", "o1", "stdin")])

        assert (backup_dir_for(tdir) / "мои_кейсы" / "note.md").read_text(
            encoding="utf-8"
        ) == "заметка"

    def test_unwritable_backup_still_clears_the_dir(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Копия — не обязательство: если перенос невозможен, набор всё равно пишется.

        Иначе скачивание падало бы там, где раньше проходило: read-only рядом с
        задачей, чужие права, заблокированный файл. Свежий набор важнее копии.
        """
        save_tests(tmp_path, [("i1", "o1", "stdin")])
        tdir = tmp_path / "tests"

        def refuse_move(*_args: object, **_kwargs: object) -> None:
            raise OSError("нет прав на запись рядом с задачей")

        monkeypatch.setattr("stepik_grader.core.tests_writer.shutil.move", refuse_move)

        save_tests(tmp_path, [("j1", "p1", "stdin")])  # без исключения

        assert (tdir / "1").read_text(encoding="utf-8") == "j1"
