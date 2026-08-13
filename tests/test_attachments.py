"""Тесты вложений условия: `core/attachments.py` и парсер ссылок (issue #1112).

Дефект пришёл с реальной базы: условие говорит «вам доступен текстовый файл
`files.txt`», решение открывает его по имени, а загрузчик файл не забирал.
Принятое платформой решение падало локально `FileNotFoundError` — и глоссарий
объяснял студенту его несуществующую ошибку.

Сеть мокается целиком: тесты обязаны работать без токена и без Stepik.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader.core.attachments import (
    MAX_ATTACHMENTS,
    download_attachments,
    safe_attachment_name,
)
from stepik_grader.core.task_page_parser import extract_attachment_links

_HOST = "https://stepik.org/media/attachments/lesson/569749"


def _response(content: bytes = b"data") -> MagicMock:
    response = MagicMock()
    response.content = content
    response.raise_for_status.return_value = None
    return response


class TestExtractAttachmentLinks:
    """Что считается вложением условия, а что нет."""

    def test_finds_attachment_link(self) -> None:
        html = f'<p>Файл <a href="{_HOST}/files.txt">files.txt</a></p>'

        assert extract_attachment_links(html) == [f"{_HOST}/files.txt"]

    def test_zip_is_left_to_the_tests_path(self) -> None:
        """Архив разбирает путь внешних тестов — иначе рядом ляжет сырой ZIP."""
        html = f'<a href="{_HOST}/tests.zip">tests</a>'

        assert extract_attachment_links(html) == []

    def test_non_attachment_links_are_ignored(self) -> None:
        html = '<a href="https://stepik.org/lesson/1/step/1">шаг</a><a href="/course/1">курс</a>'

        assert extract_attachment_links(html) == []

    def test_duplicates_collapse(self) -> None:
        html = f'<a href="{_HOST}/f.txt">a</a><a href="{_HOST}/f.txt">b</a>'

        assert extract_attachment_links(html) == [f"{_HOST}/f.txt"]

    @pytest.mark.parametrize("scheme", ["file:///etc/passwd", "javascript:alert(1)"])
    def test_dangerous_schemes_are_dropped(self, scheme: str) -> None:
        """HTML недоверенный: результат уходит прямо в загрузчик (issue #838)."""
        html = f'<a href="{scheme}/media/attachments/x.txt">x</a>'

        assert extract_attachment_links(html) == []


class TestSafeAttachmentName:
    """Имя приходит из недоверенного HTML и становится путём на диске."""

    @pytest.mark.parametrize(
        "url,expected,why",
        [
            (f"{_HOST}/files.txt", "files.txt", "обычное имя"),
            (f"{_HOST}/%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D0%B5.txt", "данные.txt", "percent-encoding"),
            ("https://host/a/b/../../etc/passwd", "passwd", "traversal срезается до basename"),
            ("https://host/media/attachments/", "", "имени нет вовсе"),
            (f"{_HOST}/a b;rm -rf.txt", "a_b_rm_-rf.txt", "разделители и пробелы обезврежены"),
        ],
    )
    def test_name_is_sanitised(self, url: str, expected: str, why: str) -> None:
        assert safe_attachment_name(url) == expected, why

    def test_name_is_capped(self) -> None:
        assert len(safe_attachment_name(f"{_HOST}/{'x' * 500}.txt")) <= 120


class TestDownloadAttachments:
    """Скачивание: best-effort, но без молчания и без затирания чужого труда."""

    def test_saves_file_next_to_the_task(self, tmp_path: pathlib.Path) -> None:
        session = MagicMock()
        session.get.return_value = _response(b"1 2 3")

        report = download_attachments(tmp_path, [f"{_HOST}/files.txt"], session)

        assert (tmp_path / "files.txt").read_bytes() == b"1 2 3"
        assert report == [{"name": "files.txt", "url": f"{_HOST}/files.txt", "status": "saved"}]

    def test_existing_file_is_not_overwritten(self, tmp_path: pathlib.Path) -> None:
        """Файл правят руками; перекачка шага не имеет права стирать эту работу."""
        mine = "мои данные".encode()
        (tmp_path / "files.txt").write_bytes(mine)
        session = MagicMock()
        session.get.return_value = _response("с сервера".encode())

        report = download_attachments(tmp_path, [f"{_HOST}/files.txt"], session)

        assert (tmp_path / "files.txt").read_bytes() == mine
        assert report[0]["status"] == "exists"
        session.get.assert_not_called()

    def test_network_failure_is_reported_not_raised(self, tmp_path: pathlib.Path) -> None:
        """Один недоступный файл не роняет скачивание задачи, но и не молчит."""
        session = MagicMock()
        session.get.side_effect = requests.RequestException("нет сети")

        report = download_attachments(tmp_path, [f"{_HOST}/files.txt"], session)

        assert report[0]["status"] == "failed"
        assert "нет сети" in report[0]["error"]
        assert not (tmp_path / "files.txt").exists()

    def test_third_party_host_goes_without_token(self, tmp_path: pathlib.Path) -> None:
        """Токен Stepik не уходит на чужой хост — то же правило, что у ZIP (issue #240)."""
        session = MagicMock()
        with patch(
            "stepik_grader.core.attachments.external_download_get",
            return_value=_response(b"x"),
        ) as external:
            download_attachments(tmp_path, ["https://example.com/media/attachments/f.txt"], session)

        external.assert_called_once()
        session.get.assert_not_called()

    def test_number_of_attachments_is_capped(self, tmp_path: pathlib.Path) -> None:
        """Сотня ссылок — это неверно разобранная разметка, а не щедрый автор."""
        session = MagicMock()
        session.get.return_value = _response()
        links = [f"{_HOST}/f{n}.txt" for n in range(MAX_ATTACHMENTS + 5)]

        report = download_attachments(tmp_path, links, session)

        assert len(report) == MAX_ATTACHMENTS

    def test_nameless_link_is_skipped(self, tmp_path: pathlib.Path) -> None:
        session = MagicMock()

        report = download_attachments(tmp_path, ["https://stepik.org/media/attachments/"], session)

        assert report[0]["status"] == "skipped"
        session.get.assert_not_called()
