"""Тесты для core/test_source_fetcher.py — конвертация ZIP/GitHub тестов в
Format 3 (issue #302: выделено из downloader.py). extract_external_test_links
(разбор ссылок) живёт в core/task_page_parser.py, но тестируется здесь рядом
с использующим её скачиванием."""

from __future__ import annotations

import io
import pathlib
import zipfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader.core import test_source_fetcher as fetcher
from stepik_grader.core.task_page_parser import extract_external_test_links
from stepik_grader.core.test_source_fetcher import (
    download_github_tests as _download_github_tests,
)
from stepik_grader.core.test_source_fetcher import (
    download_zip_tests as _download_zip_tests,
)

# ZIP-хост, для которого legitimate передавать авторизованную Stepik-сессию
# (issue #240) — используется во всех тестах конверсии, чтобы не завязывать их
# на allowlist сторонних хостов.
STEPIK_ZIP_URL = "https://stepik.org/media/attachments/lesson/1/tests.zip"

# issue #814: единый GitHub-URL для тестов валидации ответа API.
GH_URL = "https://github.com/o/r/tree/main/tests"

# ── вспомогательные фабрики моков ──────────────────────────────────────────


def _make_zip_response(*pairs: tuple[str, str]) -> MagicMock:
    """Возвращает mock HTTP-ответа с in-memory ZIP из пар (имя, содержимое)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in pairs:
            zf.writestr(name, content)
    buf.seek(0)
    resp = MagicMock()
    resp.content = buf.read()
    resp.raise_for_status = MagicMock()
    return resp


def _zip_session(*pairs: tuple[str, str]) -> MagicMock:
    """Mock requests.Session, чей .get возвращает ZIP-ответ."""
    session = MagicMock()
    session.get.return_value = _make_zip_response(*pairs)
    return session


# ── TestDownloadZipTestsConversion ─────────────────────────────────────────


class TestDownloadZipTestsConversion:
    """_download_zip_tests конвертирует zip Stepik в Format 3."""

    def test_basic_conversion(self, tmp_path: pathlib.Path) -> None:
        """ZIP с 1, 1.clue, 2, 2.clue → input.txt + output.txt."""
        session = _zip_session(
            ("1", "10\n20\n30"),
            ("1.clue", "60"),
            ("2", "5\n5\n5"),
            ("2.clue", "15"),
        )
        count = _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 2

        input_text = (tmp_path / "tests" / "input.txt").read_text(encoding="utf-8")
        output_text = (tmp_path / "tests" / "output.txt").read_text(encoding="utf-8")

        assert "# INPUT DATA:" in input_text
        assert "# TEST_1:" in input_text
        assert "# TEST_2:" in input_text
        assert "10\n20\n30" in input_text
        assert "5\n5\n5" in input_text

        assert "# OUTPUT DATA:" in output_text
        assert "# TEST_1:" in output_text
        assert "60" in output_text
        assert "15" in output_text

    def test_prefixed_zip(self, tmp_path: pathlib.Path) -> None:
        """ZIP с prefix-каталогом (tests/1, tests/1.clue) тоже работает."""
        session = _zip_session(
            ("tests/1", "abc"),
            ("tests/1.clue", "ABC"),
        )
        count = _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 1
        input_text = (tmp_path / "tests" / "input.txt").read_text(encoding="utf-8")
        assert "abc" in input_text
        assert "# TEST_1:" in input_text

    def test_blocks_sorted_numerically(self, tmp_path: pathlib.Path) -> None:
        """Блоки идут в числовом порядке, а не лексикографическом."""
        session = _zip_session(
            ("10", "ten"),
            ("10.clue", "TEN"),
            ("2", "two"),
            ("2.clue", "TWO"),
        )
        count = _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 2
        input_text = (tmp_path / "tests" / "input.txt").read_text(encoding="utf-8")
        assert input_text.index("# TEST_2:") < input_text.index("# TEST_10:")

    def test_empty_zip_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """ZIP без числовых файлов → 0 и файлы не создаются."""
        session = _zip_session(("readme.txt", "hello"))
        count = _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 0
        assert not (tmp_path / "tests" / "input.txt").exists()

    def test_bad_zip_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Невалидный ZIP → 0."""
        resp = MagicMock()
        resp.content = b"not a zip"
        resp.raise_for_status = MagicMock()
        session = MagicMock()
        session.get.return_value = resp
        assert _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session) == 0


# ── TestDownloadZipExternalSecurity ────────────────────────────────────────


class TestDownloadZipExternalSecurity:
    """_download_zip_tests не течёт OAuth-токен на сторонние хосты (issue #240)."""

    def test_stepik_url_uses_authorized_session(self, tmp_path: pathlib.Path) -> None:
        """ZIP на stepik.org скачивается через переданную (авторизованную) сессию."""
        session = _zip_session(("1", "a"), ("1.clue", "A"))
        count = _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 1
        session.get.assert_called_once_with(STEPIK_ZIP_URL, timeout=30)

    def test_non_allowlisted_host_rejected_without_touching_session(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Хост не из allowlist → 0, авторизованная сессия даже не вызывается."""
        session = MagicMock()
        count = _download_zip_tests(tmp_path, "http://evil.example/tests.zip", session)
        assert count == 0
        session.get.assert_not_called()
        assert "Не удалось скачать ZIP" in capsys.readouterr().out

    def test_loopback_ip_literal_rejected(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Loopback/private IP-литерал отклоняется даже без allowlist-хоста."""
        session = MagicMock()
        count = _download_zip_tests(tmp_path, "http://127.0.0.1/tests.zip", session)
        assert count == 0
        session.get.assert_not_called()
        assert "Не удалось скачать ZIP" in capsys.readouterr().out

    def test_allowlisted_external_host_uses_unauthenticated_request(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Allowlisted внешний хост (не stepik.org) идёт через external_download_get,
        а не через переданную авторизованную сессию."""
        session = MagicMock()
        gh_resp = _make_zip_response(("1", "a"), ("1.clue", "A"))
        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get", return_value=gh_resp
        ) as mock_get:
            count = _download_zip_tests(
                tmp_path, "https://raw.githubusercontent.com/o/r/tests.zip", session
            )
        assert count == 1
        mock_get.assert_called_once_with("https://raw.githubusercontent.com/o/r/tests.zip")
        session.get.assert_not_called()


# ── TestGithubTreeRegex ────────────────────────────────────────────────────


class TestGithubTreeRegex:
    """_GITHUB_TREE_RE распознаёт GitHub tree/blob URL."""

    def test_github_url_regex(self) -> None:
        """Паттерн распознаёт github.com/owner/repo/tree/branch/path."""
        m = fetcher._GITHUB_TREE_RE.search(
            "https://github.com/python-generation/Professional/tree/main/"
            "Module_3/Module_3.1/Module_3.1.20"
        )
        assert m is not None
        assert m.group("owner") == "python-generation"
        assert m.group("repo") == "Professional"
        assert m.group("branch") == "main"
        assert m.group("path") == "Module_3/Module_3.1/Module_3.1.20"

    def test_blob_url(self) -> None:
        """Паттерн распознаёт blob-вариант."""
        m = fetcher._GITHUB_TREE_RE.search("https://github.com/owner/repo/blob/dev/dir/sub")
        assert m is not None
        assert m.group("branch") == "dev"
        assert m.group("path") == "dir/sub"

    def test_non_tree_url_no_match(self) -> None:
        """URL без tree/blob не распознаётся."""
        assert fetcher._GITHUB_TREE_RE.search("https://github.com/owner/repo") is None


# ── TestDownloadGithubTests ────────────────────────────────────────────────


class TestDownloadGithubTests:
    """_download_github_tests скачивает тесты через GitHub Contents API.

    GitHub — всегда сторонний хост (issue #240), поэтому функция больше не
    принимает Stepik-сессию — вместо неё патчится
    ``stepik_grader.core.test_source_fetcher.external_download_get``.
    """

    def test_invalid_url_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Нераспознанный URL возвращает 0."""
        with patch("stepik_grader.core.test_source_fetcher.external_download_get") as mock_get:
            assert _download_github_tests(tmp_path, "https://example.com/x") == 0
        mock_get.assert_not_called()

    def test_input_output_txt_format(self, tmp_path: pathlib.Path) -> None:
        """Директория с input.txt + output.txt скачивается напрямую."""
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = [
            {
                "name": "input.txt",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/main/input.txt",
            },
            {
                "name": "output.txt",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/main/output.txt",
            },
        ]
        input_resp = MagicMock()
        input_resp.content = b"# INPUT DATA:\n\n# TEST_1:\n5\n\n# TEST_2:\n7\n"
        input_resp.raise_for_status = MagicMock()
        output_resp = MagicMock()
        output_resp.content = b"# OUTPUT DATA:\n\n# TEST_1:\n25\n\n# TEST_2:\n49\n"
        output_resp.raise_for_status = MagicMock()

        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get",
            side_effect=[api_resp, input_resp, output_resp],
        ):
            count = _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        assert count == 2
        assert (tmp_path / "tests" / "input.txt").read_bytes() == input_resp.content

    def test_numeric_clue_format(self, tmp_path: pathlib.Path) -> None:
        """Директория с N + N.clue конвертируется в Format 3."""
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = [
            {
                "name": "1",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/1",
            },
            {
                "name": "1.clue",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/1.clue",
            },
        ]
        in_resp = MagicMock()
        in_resp.text = "3 4"
        clue_resp = MagicMock()
        clue_resp.text = "7"

        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get",
            side_effect=[api_resp, in_resp, clue_resp],
        ):
            count = _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        assert count == 1
        input_text = (tmp_path / "tests" / "input.txt").read_text(encoding="utf-8")
        output_text = (tmp_path / "tests" / "output.txt").read_text(encoding="utf-8")
        assert "# TEST_1:" in input_text
        assert "3 4" in input_text
        assert "7" in output_text

    def test_api_returns_non_list_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Если API вернул не список — 0."""
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {"message": "Not Found"}
        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get", return_value=api_resp
        ):
            count = _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        assert count == 0

    def test_api_request_exception_returns_zero(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """requests.RequestException (сеть недоступна, DNS, таймаут и т.п.) → 0.

        Issue #49 Q-01: GitHub API errors were previously uncovered.
        """
        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get",
            side_effect=requests.ConnectionError("network unreachable"),
        ):
            count = _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        assert count == 0
        assert "GitHub API недоступен" in capsys.readouterr().out

    def test_api_raise_for_status_error_returns_zero(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """HTTP-ошибка (404/500) через raise_for_status() тоже перехватывается."""
        api_resp = MagicMock()
        api_resp.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get", return_value=api_resp
        ):
            count = _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        assert count == 0
        assert "GitHub API недоступен" in capsys.readouterr().out

    def test_no_recognized_files_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Список файлов не содержит ни input.txt/output.txt, ни N/N.clue — 0.

        Issue #49 Q-01: the `if not pairs` branch was previously uncovered.
        """
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = [
            {
                "name": "README.md",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/README.md",
            },
            {
                "name": "solution.py",
                "type": "file",
                "download_url": "https://raw.githubusercontent.com/o/r/solution.py",
            },
        ]
        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get", return_value=api_resp
        ):
            count = _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        assert count == 0

    def test_uses_external_download_without_stepik_session(self, tmp_path: pathlib.Path) -> None:
        """GitHub-загрузка не получает и не использует авторизованную Stepik-сессию.

        Regression test для issue #240 (F-01): раньше сюда передавалась
        авторизованная сессия с Bearer-токеном; теперь функция даже не
        принимает такой параметр.
        """
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = []
        with patch(
            "stepik_grader.core.test_source_fetcher.external_download_get", return_value=api_resp
        ) as mock_get:
            _download_github_tests(tmp_path, "https://github.com/o/r/tree/main/dir")
        mock_get.assert_called_once()
        assert "session" not in mock_get.call_args.kwargs


# ── TestExtractExternalTestLinks ───────────────────────────────────────────


class TestExtractExternalTestLinks:
    """extract_external_test_links находит ZIP и GitHub ссылки."""

    def test_finds_zip_link(self) -> None:
        html = (
            '<a href="https://stepik.org/media/attachments/lesson/570048/tests_2491371.zip">ZIP</a>'
        )
        zip_links, gh_links = extract_external_test_links(html)
        assert len(zip_links) == 1
        assert "tests_2491371.zip" in zip_links[0]

    def test_finds_github_link(self) -> None:
        html = (
            '<a href="https://github.com/python-generation/Professional/'
            'tree/main/Module_3">тесты</a>'
        )
        zip_links, gh_links = extract_external_test_links(html)
        assert len(gh_links) == 1
        assert "github.com" in gh_links[0]

    def test_dedup(self) -> None:
        html = '<a href="http://x/a.zip">1</a><a href="http://x/a.zip">2</a>'
        zip_links, _ = extract_external_test_links(html)
        assert zip_links == ["http://x/a.zip"]


# ── TestZipConversionRoundtrip ─────────────────────────────────────────────


class TestZipConversionRoundtrip:
    """Полный цикл: ZIP → Format 3 → grader.load_test_cases читает кейсы."""

    def test_zip_to_format3_to_grader(self, tmp_path: pathlib.Path) -> None:
        """ZIP в стиле Stepik → input.txt/output.txt → корректные TestCase."""
        from stepik_grader.grader import load_test_cases

        session = _zip_session(
            ("1", "10\n20\n30"),
            ("1.clue", "60"),
            ("2", "1\n2"),
            ("2.clue", "3"),
        )
        count = _download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 2

        cases = load_test_cases(tmp_path / "tests")
        assert len(cases) == 2
        assert cases[0].index == 1
        assert cases[0].input_lines == ["10", "20", "30"]
        assert cases[0].expected_lines == ["60"]
        assert cases[1].input_lines == ["1", "2"]
        assert cases[1].expected_lines == ["3"]


class TestDownloadZipErrorPath:
    """download_zip_tests при сетевой ошибке возвращает 0."""

    def test_network_error_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Ошибка сети на stepik.org (использует переданную сессию) → 0."""
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("down")
        zip_url = "https://stepik.org/media/attachments/lesson/1/t.zip"
        assert _download_zip_tests(tmp_path, zip_url, session) == 0


# ── Данные из сети: имя файла, размер, ответ API, URL — issue #814 ─────────


class TestZipMemberNameRobustness:
    """`NETW-01`: имя члена архива приходит из недоверенного ZIP по ссылке из
    текста задачи, а `isdigit()` истинен для «²» и «①», которые `int()` не
    принимает. Один такой символ ронял ВСЁ скачивание `ValueError`'ом мимо
    перехватов — у вызывающей стороны (`downloader.py`) обёртки нет.
    """

    def test_superscript_digit_does_not_break_download(self, tmp_path: pathlib.Path) -> None:
        session = _zip_session(("t/1", "4"), ("t/1.clue", "5"), ("t/\u00b2", "junk"))
        count = fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, session)
        assert count == 1  # обычная пара разобрана, экзотическое имя пропущено

    def test_circled_digit_ignored(self, tmp_path: pathlib.Path) -> None:
        session = _zip_session(("t/1", "4"), ("t/1.clue", "5"), ("t/\u2460.clue", "junk"))
        assert fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, session) == 1

    def test_arabic_indic_digit_still_accepted(self, tmp_path: pathlib.Path) -> None:
        """`isdecimal` — не «только ASCII»: арабо-индийские цифры `int()` берёт,
        и терять такие кейсы было бы регрессом."""
        session = _zip_session(("t/\u0663", "4"), ("t/\u0663.clue", "5"))
        assert fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, session) == 1


class TestGithubMemberNameRobustness:
    """`NETW-01` в GitHub-ветке: тот же `isdigit` был и там.

    Отдельный тест обязателен — мутация «обратно isdigit» в этой ветке не
    ловилась набором для ZIP (проверено).
    """

    def test_superscript_name_does_not_break(self, tmp_path: pathlib.Path) -> None:
        payload = [
            {"type": "file", "name": "²", "download_url": "https://x/sup"},
            {"type": "file", "name": "1", "download_url": "https://x/1"},
            {"type": "file", "name": "1.clue", "download_url": "https://x/1c"},
        ]

        def _get(url: str, **_kw: object) -> MagicMock:
            resp = MagicMock()
            resp.json.return_value = payload
            resp.raise_for_status = MagicMock()
            resp.text = "4"
            resp.content = b"4"
            return resp

        with patch.object(fetcher, "external_download_get", side_effect=_get):
            assert fetcher.download_github_tests(tmp_path, GH_URL) == 1


class TestZipSizeLimits:
    """`NETW-03`: архив по ссылке из HTML задачи распаковывался без потолка —
    классический zip bomb, `zf.read` читает член целиком в память."""

    def test_oversized_member_is_rejected(self, tmp_path: pathlib.Path) -> None:
        """Один член сверх лимита НА ФАЙЛ, но в пределах общего бюджета.

        Размер подобран так, чтобы сработал именно лимит на член (8 МБ):
        с 200 МБ тест проходил бы и без него — за счёт суммарного бюджета
        (проверено мутацией).
        """
        session = _zip_session(("t/1", "A" * (10 * 1024 * 1024)), ("t/1.clue", "5"))
        assert fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, session) == 0

    def test_zip_bomb_is_rejected(self, tmp_path: pathlib.Path) -> None:
        """Классическая бомба: 200 МБ нулей жмутся в килобайты."""
        session = _zip_session(("t/1", "A" * (200 * 1024 * 1024)), ("t/1.clue", "5"))
        assert fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, session) == 0

    def test_oversized_total_is_rejected(self, tmp_path: pathlib.Path) -> None:
        """Каждый член в пределах лимита, а сумма — нет."""
        chunk = "A" * (7 * 1024 * 1024)
        pairs = []
        for i in range(1, 12):  # 11 × 7 МБ ≈ 77 МБ > 64 МБ
            pairs.extend([(f"t/{i}", chunk), (f"t/{i}.clue", "5")])
        assert fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, _zip_session(*pairs)) == 0

    def test_normal_archive_still_passes(self, tmp_path: pathlib.Path) -> None:
        """Контроль: обычные тест-кейсы (килобайты) лимит не задевает."""
        session = _zip_session(("t/1", "4" * 1000), ("t/1.clue", "5" * 1000))
        assert fetcher.download_zip_tests(tmp_path, STEPIK_ZIP_URL, session) == 1


class TestGithubApiResponseValidation:
    """`NETW-02`: ответ API индексировался напрямую (`item["download_url"]`).

    Файл без `download_url` (submodule, symlink, урезанная выдача прокси) давал
    `KeyError` МИМО перехвата сетевых ошибок — скачивание падало трейсбеком
    вместо понятного сообщения.
    """

    def _resp(self, payload: object) -> MagicMock:
        resp = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status = MagicMock()
        resp.content = b"4"
        resp.text = "4"
        return resp

    def test_file_without_download_url_does_not_raise(self, tmp_path: pathlib.Path) -> None:
        payload = [{"type": "file", "name": "1"}]  # download_url отсутствует
        with patch.object(fetcher, "external_download_get", return_value=self._resp(payload)):
            assert fetcher.download_github_tests(tmp_path, GH_URL) == 0

    def test_non_dict_entry_is_skipped(self, tmp_path: pathlib.Path) -> None:
        payload = ["строка вместо объекта", {"type": "file", "name": "1"}]
        with patch.object(fetcher, "external_download_get", return_value=self._resp(payload)):
            assert fetcher.download_github_tests(tmp_path, GH_URL) == 0

    def test_non_string_fields_are_skipped(self, tmp_path: pathlib.Path) -> None:
        payload = [{"type": "file", "name": 1, "download_url": None}]
        with patch.object(fetcher, "external_download_get", return_value=self._resp(payload)):
            assert fetcher.download_github_tests(tmp_path, GH_URL) == 0


class TestGithubUrlEncoding:
    """`NETW-04`: owner/repo/path/branch из HTML задачи подставлялись в URL как
    есть — `#` обрубал бы запрос, а `?`/`&` дописывали чужие query-параметры."""

    def test_branch_and_path_are_percent_encoded(self, tmp_path: pathlib.Path) -> None:
        calls: list[str] = []

        def _capture(url: str, **_kw: object) -> MagicMock:
            calls.append(url)
            resp = MagicMock()
            resp.json.return_value = []
            resp.raise_for_status = MagicMock()
            return resp

        with patch.object(fetcher, "external_download_get", side_effect=_capture):
            fetcher.download_github_tests(
                tmp_path, "https://github.com/o/r/tree/feat?x=1/tests dir"
            )

        assert calls, "запрос к API не сформирован"
        api_url = calls[0]
        assert "feat%3Fx%3D1" in api_url or "%3F" in api_url  # '?' экранирован
        assert " " not in api_url  # пробел не уехал в URL сырым
