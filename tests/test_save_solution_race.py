"""Параллельные сохранения не затирают друг друга (issue #922, ``RUN-3-02``).

Сервер многопоточный (``ThreadingHTTPServer``), а создание нового файла шло в
два шага: ``_next_solution_filename`` смотрел на папку, запись выполнялась
отдельно. Между шагами успевал вклиниться соседний запрос — и оба получали одно
имя. Восемь одновременных сохранений давали восемь ответов ``ok: true``, четыре
файла на диске и четыре молча потерянных решения.

Цена ошибки несимметрична: пропавшее решение не видно ни в ответе, ни в
интерфейсе — ученик узнаёт о потере, когда возвращается к задаче.

Тест бьёт по чистой функции ``save_solution`` из нескольких потоков: HTTP-слой
здесь ничего не добавляет — гонка живёт ниже него, а поднятый сервер только
сделал бы проверку медленнее и менее детерминированной.
"""

from __future__ import annotations

import concurrent.futures
import pathlib

from stepik_grader.web.viewmodels import save_solution

#: Восемь — с запасом: гонка воспроизводилась уже на четырёх, а больше потоков
#: лишь удлиняют прогон.
_WRITERS = 8


def _save_all(folder: pathlib.Path, codes: list[str]) -> list[dict]:
    """Сохранить все тексты одновременно, каждый — новым файлом."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(codes)) as pool:
        return list(pool.map(lambda code: save_solution(folder, None, code), codes))


class TestConcurrentNewFiles:
    """Сколько ответов «сохранено», столько и решений на диске."""

    def test_every_successful_save_has_its_own_file(self, tmp_path: pathlib.Path) -> None:
        codes = [f"# решение {i}\n" for i in range(_WRITERS)]

        results = _save_all(tmp_path, codes)

        saved = [r for r in results if r.get("ok")]
        on_disk = {p.read_text(encoding="utf-8") for p in tmp_path.iterdir()}
        # Главное: ни один ответ «сохранено» не оказался ложью.
        assert {r["path"] for r in saved} == {str(p) for p in tmp_path.iterdir()}
        assert on_disk == set(codes)

    def test_no_two_saves_report_the_same_path(self, tmp_path: pathlib.Path) -> None:
        """Разные ответы — разные имена: одно имя на двоих и есть потеря."""
        results = _save_all(tmp_path, [f"# {i}\n" for i in range(_WRITERS)])

        paths = [r["path"] for r in results if r.get("ok")]

        assert len(set(paths)) == len(paths)

    def test_an_existing_series_is_continued_not_restarted(self, tmp_path: pathlib.Path) -> None:
        """Граница с другой стороны: маска имён не изменилась.

        Атомарный захват имени не должен превратиться в «пиши куда попало» —
        серия ``task4_*`` продолжается, а не начинается заново.
        """
        (tmp_path / "task4_1.py").write_text("# уже есть\n", encoding="utf-8")

        results = _save_all(tmp_path, [f"# {i}\n" for i in range(3)])

        names = sorted(pathlib.Path(r["path"]).name for r in results if r.get("ok"))
        assert names == ["task4_2.py", "task4_3.py", "task4_4.py"]


class TestSingleSaveUnchanged:
    """Обычный путь тот же — иначе починка гонки была бы сменой поведения."""

    def test_the_first_file_is_still_task_1(self, tmp_path: pathlib.Path) -> None:
        data = save_solution(tmp_path, None, "print(1)\n")

        assert data["ok"] is True
        assert pathlib.Path(data["path"]).name == "task_1.py"
        assert (tmp_path / "task_1.py").read_text(encoding="utf-8") == "print(1)\n"

    def test_a_missing_folder_is_still_a_message_not_a_crash(self, tmp_path: pathlib.Path) -> None:
        data = save_solution(tmp_path / "нет-такой", None, "print(1)\n")

        assert data["ok"] is False
        assert "message" in data
