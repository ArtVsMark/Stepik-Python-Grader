"""Тесты для downloader.py — конвертация ZIP/GitHub тестов в Format 3."""

from __future__ import annotations

import io
import pathlib
import zipfile
from unittest.mock import MagicMock

import downloader
from downloader import (
    _download_github_tests,
    _download_zip_tests,
    extract_external_test_links,
)

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
        count = _download_zip_tests(tmp_path, "http://x/tests.zip", session)
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
        count = _download_zip_tests(tmp_path, "http://x/tests.zip", session)
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
        count = _download_zip_tests(tmp_path, "http://x/tests.zip", session)
        assert count == 2
        input_text = (tmp_path / "tests" / "input.txt").read_text(encoding="utf-8")
        assert input_text.index("# TEST_2:") < input_text.index("# TEST_10:")

    def test_empty_zip_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """ZIP без числовых файлов → 0 и файлы не создаются."""
        session = _zip_session(("readme.txt", "hello"))
        count = _download_zip_tests(tmp_path, "http://x/tests.zip", session)
        assert count == 0
        assert not (tmp_path / "tests" / "input.txt").exists()

    def test_bad_zip_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Невалидный ZIP → 0."""
        resp = MagicMock()
        resp.content = b"not a zip"
        resp.raise_for_status = MagicMock()
        session = MagicMock()
        session.get.return_value = resp
        assert _download_zip_tests(tmp_path, "http://x/tests.zip", session) == 0


# ── TestGithubTreeRegex ────────────────────────────────────────────────────


class TestGithubTreeRegex:
    """_GITHUB_TREE_RE распознаёт GitHub tree/blob URL."""

    def test_github_url_regex(self) -> None:
        """Паттерн распознаёт github.com/owner/repo/tree/branch/path."""
        m = downloader._GITHUB_TREE_RE.search(
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
        m = downloader._GITHUB_TREE_RE.search(
            "https://github.com/owner/repo/blob/dev/dir/sub"
        )
        assert m is not None
        assert m.group("branch") == "dev"
        assert m.group("path") == "dir/sub"

    def test_non_tree_url_no_match(self) -> None:
        """URL без tree/blob не распознаётся."""
        assert (
            downloader._GITHUB_TREE_RE.search("https://github.com/owner/repo") is None
        )


# ── TestDownloadGithubTests ────────────────────────────────────────────────


class TestDownloadGithubTests:
    """_download_github_tests скачивает тесты через GitHub Contents API."""

    def test_invalid_url_returns_zero(self, tmp_path: pathlib.Path) -> None:
        """Нераспознанный URL возвращает 0."""
        session = MagicMock()
        assert _download_github_tests(tmp_path, "https://example.com/x", session) == 0
        session.get.assert_not_called()

    def test_input_output_txt_format(self, tmp_path: pathlib.Path) -> None:
        """Директория с input.txt + output.txt скачивается напрямую."""
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = [
            {
                "name": "input.txt",
                "type": "file",
                "download_url": "http://raw/input.txt",
            },
            {
                "name": "output.txt",
                "type": "file",
                "download_url": "http://raw/output.txt",
            },
        ]
        input_resp = MagicMock()
        input_resp.content = b"# INPUT DATA:\n\n# TEST_1:\n5\n\n# TEST_2:\n7\n"
        input_resp.raise_for_status = MagicMock()
        output_resp = MagicMock()
        output_resp.content = b"# OUTPUT DATA:\n\n# TEST_1:\n25\n\n# TEST_2:\n49\n"
        output_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.side_effect = [api_resp, input_resp, output_resp]

        count = _download_github_tests(
            tmp_path,
            "https://github.com/o/r/tree/main/dir",
            session,
        )
        assert count == 2
        assert (tmp_path / "tests" / "input.txt").read_bytes() == input_resp.content

    def test_numeric_clue_format(self, tmp_path: pathlib.Path) -> None:
        """Директория с N + N.clue конвертируется в Format 3."""
        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = [
            {"name": "1", "type": "file", "download_url": "http://raw/1"},
            {"name": "1.clue", "type": "file", "download_url": "http://raw/1.clue"},
        ]
        in_resp = MagicMock()
        in_resp.text = "3 4"
        clue_resp = MagicMock()
        clue_resp.text = "7"

        session = MagicMock()
        session.get.side_effect = [api_resp, in_resp, clue_resp]

        count = _download_github_tests(
            tmp_path,
            "https://github.com/o/r/tree/main/dir",
            session,
        )
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
        session = MagicMock()
        session.get.return_value = api_resp
        count = _download_github_tests(
            tmp_path, "https://github.com/o/r/tree/main/dir", session
        )
        assert count == 0


# ── TestExtractExternalTestLinks ───────────────────────────────────────────


class TestExtractExternalTestLinks:
    """extract_external_test_links находит ZIP и GitHub ссылки."""

    def test_finds_zip_link(self) -> None:
        html = '<a href="https://stepik.org/media/attachments/lesson/570048/tests_2491371.zip">ZIP</a>'
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
        from grader import load_test_cases

        session = _zip_session(
            ("1", "10\n20\n30"),
            ("1.clue", "60"),
            ("2", "1\n2"),
            ("2.clue", "3"),
        )
        count = _download_zip_tests(tmp_path, "http://x/tests.zip", session)
        assert count == 2

        cases = load_test_cases(str(tmp_path / "tests"))
        assert len(cases) == 2
        assert cases[0].index == 1
        assert cases[0].input_lines == ["10", "20", "30"]
        assert cases[0].expected_lines == ["60"]
        assert cases[1].input_lines == ["1", "2"]
        assert cases[1].expected_lines == ["3"]
