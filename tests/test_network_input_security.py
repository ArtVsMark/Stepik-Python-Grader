"""Враждебный вход из сети: имена в ФС, HTML страницы задачи, запись кейсов.

issue #838. Срез «данные из сети» прежде разбирал только `test_source_fetcher`,
хотя недоверенных входа три:

* **имена файлов из ответа API** (`slugify`/`build_task_directory`) — названия
  курса/урока/шага выбирает не пользователь, а они становятся путями;
* **HTML страницы задачи** (`task_page_parser`) — разбирается регулярками и
  `HTMLParser`, а найденные ссылки уходят прямо в загрузчик;
* **тексты кейсов** (`tests_writer`) — приходят из API/HTML и пишутся на диск.

Тесты сгруппированы по этим трём поверхностям. Path traversal проверяется на
`slugify`: сам `tests_writer` пишет файлы по порядковому номеру (`tests/1`), имя
из сети до него не доходит — это инвариант, который тест ниже и закрепляет.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from stepik_grader.core import task_page_parser, tests_writer
from stepik_grader.downloader import build_task_directory
from stepik_grader.downloader_config import slugify

# Потолки времени намеренно щедрые: тест ловит катастрофическое поведение
# (ReDoS, квадратичный разбор), а не регрессию в разы на медленном раннере.
_PARSE_BUDGET_S = 5.0


# ---------------------------------------------------------------------------
# 1. Имена из ответа API → путь на диске
# ---------------------------------------------------------------------------


class TestNamesFromApiBecomePaths:
    @pytest.mark.parametrize(
        "hostile",
        [
            "../../../etc",
            "..",
            "../",
            "..\\..\\windows",
            "/etc/passwd",
            "C:\\Windows\\System32",
            "a/b/c",
            "тест/../../секрет",
        ],
    )
    def test_slug_never_escapes_one_level(self, hostile: str) -> None:
        """Никакое название не даёт разделитель пути или переход наверх."""
        slug = slugify(hostile)
        assert "/" not in slug
        assert "\\" not in slug
        assert slug not in {"", ".", ".."}
        assert not slug.startswith("..")

    def test_task_directory_stays_under_root(self, tmp_path: pathlib.Path) -> None:
        """Враждебные названия всех уровней не выводят папку задачи из root_dir."""
        task_dir = build_task_directory(tmp_path, "../..", "..", "../../etc", 1, "../../..")
        assert task_dir.resolve().is_relative_to(tmp_path.resolve())

    @pytest.mark.parametrize("reserved", ["CON", "con", "NUL", "com1", "LPT9", "aux"])
    def test_windows_reserved_names_are_escaped(self, reserved: str) -> None:
        """Имя устройства Windows нельзя создать как папку — уводим от него."""
        assert slugify(reserved).endswith("_")

    def test_ordinary_name_is_not_mangled(self) -> None:
        """Обычное название по-прежнему читаемо — экранирование не задевает его."""
        assert slugify("Урок 3: Списки и словари") == "урок-3-списки-и-словари"

    def test_length_is_capped(self) -> None:
        """Длинное название не упирается в лимит имени файла ОС."""
        assert len(slugify("x" * 500)) <= 80

    def test_empty_name_has_a_fallback(self) -> None:
        assert slugify("   ...   ") == "task"


# ---------------------------------------------------------------------------
# 2. HTML страницы задачи
# ---------------------------------------------------------------------------


class TestHostileTaskPageHtml:
    def test_broken_markup_does_not_raise(self) -> None:
        """Незакрытые/переставленные теги — не ошибка, а просто нет кейсов."""
        broken = "<table><tr><td>1<td>2</tr></table></td></tr><table><tr>"
        assert isinstance(task_page_parser.extract_tests_from_html(broken), list)

    def test_nested_tables_do_not_raise(self) -> None:
        inner = "<table><tr><td>a</td><td>b</td><td>c</td></tr></table>"
        html = f"<table><tr><td>{inner}</td></tr></table>"
        assert isinstance(task_page_parser.extract_tests_from_html(html), list)

    def test_row_with_too_few_cells_is_skipped(self) -> None:
        html = "<table><tr><td>1</td><td>2</td></tr></table>"
        assert task_page_parser.extract_tests_from_html(html) == []

    def test_cell_markup_is_data_not_code(self) -> None:
        """Разметка внутри ячейки остаётся текстом: мы её нигде не исполняем."""
        html = "<table><tr><td>#</td><td>1</td><td><script>alert(1)</script>2</td></tr></table>"
        tests = task_page_parser.extract_tests_from_html(html)
        assert tests == [("1", "2", "stdin")]

    def test_huge_table_parses_in_bounded_time(self) -> None:
        """Огромная страница разбирается линейно, а не подвешивает загрузчик."""
        html = "<table>" + "<tr><td>#</td><td>1</td><td>2</td></tr>" * 20_000 + "</table>"
        started = time.perf_counter()
        tests = task_page_parser.extract_tests_from_html(html)
        assert len(tests) == 20_000
        assert time.perf_counter() - started < _PARSE_BUDGET_S

    # ids заданы явно: без них pytest кладёт весь payload в node id, а тот
    # уезжает в переменную окружения PYTEST_CURRENT_TEST — на Windows лимит
    # 32767 символов, и прогон падал ValueError ещё до самого теста.
    @pytest.mark.parametrize(
        "payload",
        [
            'href="' + "a" * 200_000,  # кавычка не закрыта
            'href="' + "a/" * 100_000 + '.zip"',  # длинный путь до .zip
            '<a href="' * 20_000,  # много незавершённых атрибутов
        ],
        ids=["unterminated-quote", "long-path-to-zip", "many-open-attrs"],
    )
    def test_link_regexes_do_not_blow_up(self, payload: str) -> None:
        """Патологические строки не дают экспоненциального бэктрекинга."""
        started = time.perf_counter()
        task_page_parser.extract_external_test_links(payload)
        assert time.perf_counter() - started < _PARSE_BUDGET_S

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd.zip",
            "FILE:///etc/passwd.zip",
            "javascript:alert(1).zip",
            "data:application/zip;base64,AAAA.zip",
        ],
    )
    def test_non_http_schemes_are_dropped(self, url: str) -> None:
        """Ссылка из недоверенного HTML не отправит загрузчик за пределы сети."""
        zips, _ = task_page_parser.extract_external_test_links(f'href="{url}"')
        assert zips == []

    def test_http_links_still_pass(self) -> None:
        html = 'href="https://example.org/tests.zip" href="/relative/tests.zip"'
        zips, _ = task_page_parser.extract_external_test_links(html)
        assert zips == ["https://example.org/tests.zip", "/relative/tests.zip"]

    def test_github_links_are_scheme_checked_too(self) -> None:
        html = 'href="javascript:github.com/x" href="https://github.com/user/repo"'
        _, github = task_page_parser.extract_external_test_links(html)
        assert github == ["https://github.com/user/repo"]

    @pytest.mark.parametrize(
        "cell",
        [
            "x = 1\x00",  # null-байт: ast.parse документированно бросает ValueError
            "(" * 5_000 + "1" + ")" * 5_000,  # предел вложенности парсера
            "x" * 100_000,
            "\ud800",  # одиночный суррогат
            "def f(:",  # синтаксический мусор
        ],
        ids=["null-byte", "deep-nesting", "very-long-name", "lone-surrogate", "broken-syntax"],
    )
    def test_is_function_style_survives_hostile_cell(self, cell: str) -> None:
        """Ячейка таблицы — недоверенный вход; определение режима не падает."""
        assert task_page_parser.is_function_style(cell) in (True, False)


# ---------------------------------------------------------------------------
# 3. Запись кейсов на диск
# ---------------------------------------------------------------------------


class TestWritingCasesFromNetwork:
    def test_file_names_come_from_index_not_from_network(self, tmp_path: pathlib.Path) -> None:
        """Инвариант: имена файлов кейсов — порядковые номера, а не данные извне."""
        tests_writer.save_tests(tmp_path, [("../../evil", "../../evil", "stdin")])
        written = sorted(p.name for p in (tmp_path / "tests").iterdir())
        assert written == ["1", "1.clue"]

    def test_surrogates_do_not_abort_the_download(self, tmp_path: pathlib.Path) -> None:
        """Невалидный UTF-8 из API заменяется с предупреждением, а не роняет всё."""
        with pytest.warns(UserWarning, match="UTF-8"):
            saved = tests_writer.save_tests(tmp_path, [("\ud800", "ok", "stdin")])
        assert saved == 1
        assert (tmp_path / "tests" / "1.clue").read_text(encoding="utf-8") == "ok"

    def test_control_characters_are_written_as_is(self, tmp_path: pathlib.Path) -> None:
        """Валидные (пусть и странные) символы не трогаем — это данные кейса."""
        tests_writer.save_tests(tmp_path, [("a\x01b", "c\td", "stdin")])
        assert (tmp_path / "tests" / "1").read_text(encoding="utf-8") == "a\x01b"

    def test_testblock_format_survives_surrogates(self, tmp_path: pathlib.Path) -> None:
        with pytest.warns(UserWarning, match="UTF-8"):
            tests_writer.write_testblock_tests(tmp_path, {1: ("\ud800", "out")})
        assert (tmp_path / "input.txt").exists()

    def test_stale_files_are_removed_before_write(self, tmp_path: pathlib.Path) -> None:
        """Перескачивание не оставляет чужих файлов — иначе вердикт по смеси."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "99.clue").write_text("устаревший", encoding="utf-8")
        tests_writer.save_tests(tmp_path, [("1", "2", "stdin")])
        assert not (tests_dir / "99.clue").exists()
