"""Тесты эндпоинтов условия задачи (issue #1177).

Два уровня: адаптер (`web/statement_adapter`) — прямыми вызовами на временном
каталоге, и обе ручки — живым HTTP-запросом к поднятому серверу. Второй уровень
нужен потому, что часть контракта живёт именно в веб-слое: конфайнмент пути,
коды ответов и Content-Type.
"""

from __future__ import annotations

import base64
import json
import pathlib
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator

import pytest

from stepik_grader.web import server as web_server
from stepik_grader.web.statement_adapter import (
    STATEMENT_SCHEMA,
    read_image,
    read_statement,
)

#: Настоящий PNG 1×1 — важно, что это байты, а не текст: ручка отдаёт их как есть.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

_PIC_URL = "https://stepik.org/media/attachments/1/pic.png"


def _make_task(
    root: pathlib.Path,
    *,
    html: str | None = None,
    legacy_md: str | None = None,
    attachments: list[dict[str, object]] | None = None,
    with_pic: bool = False,
    meta: dict[str, object] | None = None,
) -> pathlib.Path:
    """Каталог задачи во временной папке — как его кладёт загрузчик."""
    task = root / "04-summa"
    task.mkdir(exist_ok=True)
    if html is not None:
        (task / "task.html").write_text(html, encoding="utf-8")
    if legacy_md is not None:
        (task / "task.md").write_text(legacy_md, encoding="utf-8")
    if with_pic:
        (task / "pic.png").write_bytes(PNG)
    payload: dict[str, object] = {
        "course_title": "Поколение Python",
        "section_title": "Списки",
        "lesson_title": "Урок 3",
        "step_title": "Сумма",
        "step_position": 4,
    }
    if attachments is not None:
        payload["attachments"] = attachments
    if meta is not None:
        payload = {**payload, **meta}
    (task / "meta.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return task


class TestReadStatement:
    def test_header_comes_from_meta(self, tmp_path: pathlib.Path) -> None:
        task = _make_task(tmp_path, html="<p>условие</p>")
        data = read_statement(task)

        assert data["kind"] == "statement"
        assert data["schema"] == STATEMENT_SCHEMA
        assert data["header"]["course_title"] == "Поколение Python"
        assert data["header"]["step_position"] == 4

    def test_body_is_sanitized(self, tmp_path: pathlib.Path) -> None:
        task = _make_task(tmp_path, html='<p onclick="x()">текст</p><script>alert(1)</script>')
        html = read_statement(task)["html"]

        assert "onclick" not in html
        assert "alert" not in html
        assert "текст" in html

    def test_missing_statement_says_so_explicitly(self, tmp_path: pathlib.Path) -> None:
        """Пустая строка на клиенте неотличима от «загрузилось и оказалось пустым»."""
        task = _make_task(tmp_path)
        data = read_statement(task)

        assert data["kind"] == "empty"
        assert data["reason"] == "no_statement"
        assert data["header"]["step_title"] == "Сумма", "шапка нужна и без условия"

    def test_missing_directory_is_a_different_reason(self, tmp_path: pathlib.Path) -> None:
        data = read_statement(tmp_path / "нет-такого")

        assert data["kind"] == "empty"
        assert data["reason"] == "not_a_task_dir"

    def test_legacy_task_md_is_still_readable(self, tmp_path: pathlib.Path) -> None:
        """До #1176 сырой HTML лежал в `task.md`; такие задачи не перекачивают."""
        task = _make_task(tmp_path, legacy_md="<h2>Старая</h2><p>условие</p>")
        html = read_statement(task)["html"]

        assert "<h2>Старая</h2>" in html

    def test_task_html_wins_over_task_md(self, tmp_path: pathlib.Path) -> None:
        """У задач нового формата откат не должен срабатывать никогда."""
        task = _make_task(tmp_path, html="<p>новое</p>", legacy_md="<p>старое</p>")

        assert "новое" in read_statement(task)["html"]
        assert "старое" not in read_statement(task)["html"]

    def test_broken_meta_does_not_hide_the_statement(self, tmp_path: pathlib.Path) -> None:
        """Условие важнее шапки: задача покажется, пусть и без хлебных крошек."""
        task = _make_task(tmp_path, html="<p>условие</p>")
        (task / "meta.json").write_text("{это не json", encoding="utf-8")
        data = read_statement(task)

        assert data["kind"] == "statement"
        assert "условие" in data["html"]
        assert data["header"]["course_title"] == ""

    def test_attachment_presence_is_checked_on_disk(self, tmp_path: pathlib.Path) -> None:
        """`meta.json` говорит, что файл когда-то приехал; вопрос — лежит ли сейчас."""
        task = _make_task(
            tmp_path,
            html="<p>условие</p>",
            with_pic=True,
            attachments=[
                {"name": "pic.png", "url": _PIC_URL, "status": "saved"},
                {
                    "name": "нет.txt",
                    "url": "https://stepik.org/media/attachments/1/x",
                    "status": "saved",
                },
            ],
        )
        by_name = {item["name"]: item for item in read_statement(task)["attachments"]}

        assert by_name["pic.png"]["present"] is True
        assert by_name["pic.png"]["size"] == len(PNG)
        assert by_name["нет.txt"]["present"] is False
        assert by_name["нет.txt"]["size"] is None

    def test_local_image_src_points_at_the_image_route(self, tmp_path: pathlib.Path) -> None:
        """Голое имя файла в браузере резолвится от корня сервера и даёт 404."""
        task = _make_task(
            tmp_path,
            html=f'<img src="{_PIC_URL}" alt="схема">',
            with_pic=True,
            attachments=[{"name": "pic.png", "url": _PIC_URL, "status": "saved"}],
        )
        html = read_statement(task)["html"]

        assert "/api/task/image?" in html
        assert "name=pic.png" in html
        assert _PIC_URL not in html

    def test_failed_attachment_is_not_substituted(self, tmp_path: pathlib.Path) -> None:
        """Обещать локальный файл, которого нет, хуже, чем не показать картинку."""
        task = _make_task(
            tmp_path,
            html=f'<img src="{_PIC_URL}" alt="схема">',
            attachments=[{"name": "pic.png", "url": _PIC_URL, "status": "failed"}],
        )
        html = read_statement(task)["html"]

        assert "<img" not in html
        assert "схема" in html


class TestImageAllowlist:
    """Имя сверяется со списком вложений, а не используется как путь (ср. #811).

    Проверяется публичная ``read_image``, а не внутренняя проверка: важно, что
    наружу ничего не уходит, а не как именно это устроено внутри.
    """

    @pytest.fixture
    def task(self, tmp_path: pathlib.Path) -> pathlib.Path:
        task = _make_task(
            tmp_path,
            html="<p>условие</p>",
            with_pic=True,
            attachments=[{"name": "pic.png", "url": _PIC_URL, "status": "saved"}],
        )
        (task / "secrets.json").write_text('{"access_token": "СЕКРЕТ"}', encoding="utf-8")
        (task / "sneaky.png").write_bytes(PNG)
        return task

    def test_listed_attachment_is_served(self, task: pathlib.Path) -> None:
        found = read_image(task, "pic.png")

        assert found is not None
        assert found == (PNG, "image/png")

    @pytest.mark.parametrize(
        "name",
        ["secrets.json", "meta.json", "task.html", "../secrets.json", "sub/pic.png", ""],
    )
    def test_anything_else_is_refused(self, task: pathlib.Path, name: str) -> None:
        assert read_image(task, name) is None

    def test_file_on_disk_but_not_in_meta_is_refused(self, task: pathlib.Path) -> None:
        """Именно поэтому проверка идёт по списку, а не по «файл существует»."""
        assert (task / "sneaky.png").is_file()
        assert read_image(task, "sneaky.png") is None

    def test_svg_is_refused_even_if_listed(self, tmp_path: pathlib.Path) -> None:
        """SVG носит скрипт, а отдаётся со своего origin — обошёл бы всю очистку."""
        task = _make_task(
            tmp_path,
            html="<p>x</p>",
            attachments=[
                {
                    "name": "d.svg",
                    "url": "https://stepik.org/media/attachments/1/d.svg",
                    "status": "saved",
                }
            ],
        )
        (task / "d.svg").write_text("<svg onload='alert(1)'/>", encoding="utf-8")

        assert read_image(task, "d.svg") is None

    def test_listed_but_deleted_is_refused(self, task: pathlib.Path) -> None:
        (task / "pic.png").unlink()
        assert read_image(task, "pic.png") is None


@pytest.fixture
def live_server(tmp_path: pathlib.Path) -> Iterator[tuple[str, pathlib.Path]]:
    """Поднятый сервер с workspace во временной папке."""
    httpd = web_server._GraderServer(
        ("127.0.0.1", 0),
        web_server._Handler,
        workspace=tmp_path,
        confine=True,
        sandbox=False,
        record_history=False,
        lang="ru",
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}", tmp_path
    finally:
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()


def _fetch(base: str, route: str, params: dict[str, str]) -> tuple[int, str, bytes]:
    url = f"{base}{route}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


class TestStatementEndpoint:
    def test_returns_sanitized_statement(self, live_server: tuple[str, pathlib.Path]) -> None:
        base, workspace = live_server
        task = _make_task(workspace, html="<p>условие</p><script>alert(1)</script>")

        status, ctype, body = _fetch(base, "/api/task/statement", {"path": str(task)})
        data = json.loads(body)

        assert status == 200
        assert "application/json" in ctype
        assert data["kind"] == "statement"
        assert "alert" not in data["html"]

    def test_missing_path_is_an_error_not_a_crash(
        self, live_server: tuple[str, pathlib.Path]
    ) -> None:
        base, _ = live_server
        status, _, body = _fetch(base, "/api/task/statement", {})

        assert status == 200
        assert json.loads(body)["kind"] == "error"

    def test_path_outside_workspace_is_refused(self, live_server: tuple[str, pathlib.Path]) -> None:
        base, workspace = live_server
        status, _, _ = _fetch(base, "/api/task/statement", {"path": str(workspace.parent)})

        assert status == 403


class TestImageEndpoint:
    def test_serves_the_bytes_with_the_right_type(
        self, live_server: tuple[str, pathlib.Path]
    ) -> None:
        base, workspace = live_server
        task = _make_task(
            workspace,
            html="<p>x</p>",
            with_pic=True,
            attachments=[{"name": "pic.png", "url": _PIC_URL, "status": "saved"}],
        )

        status, ctype, body = _fetch(
            base, "/api/task/image", {"path": str(task), "name": "pic.png"}
        )

        assert status == 200
        assert ctype == "image/png"
        assert body == PNG, "байты обязаны доехать без перекодирования"

    @pytest.mark.parametrize("name", ["secrets.json", "meta.json", "task.html", "../secrets.json"])
    def test_other_files_are_not_served(
        self, live_server: tuple[str, pathlib.Path], name: str
    ) -> None:
        """Прямая проверка того, чем оказался `_get_source` в #811."""
        base, workspace = live_server
        task = _make_task(
            workspace,
            html="<p>x</p>",
            with_pic=True,
            attachments=[{"name": "pic.png", "url": _PIC_URL, "status": "saved"}],
        )
        (task / "secrets.json").write_text('{"access_token": "СЕКРЕТ"}', encoding="utf-8")

        status, _, body = _fetch(base, "/api/task/image", {"path": str(task), "name": name})

        assert status == 404
        assert b"\xd0\xa1\xd0\x95\xd0\x9a\xd0\xa0\xd0\x95\xd0\xa2" not in body, "секрет утёк в тело"

    def test_missing_name_is_refused(self, live_server: tuple[str, pathlib.Path]) -> None:
        base, workspace = live_server
        task = _make_task(workspace, html="<p>x</p>")

        status, _, _ = _fetch(base, "/api/task/image", {"path": str(task)})

        assert status == 404

    def test_path_outside_workspace_is_refused(self, live_server: tuple[str, pathlib.Path]) -> None:
        base, workspace = live_server
        status, _, _ = _fetch(
            base, "/api/task/image", {"path": str(workspace.parent), "name": "pic.png"}
        )

        assert status == 403
