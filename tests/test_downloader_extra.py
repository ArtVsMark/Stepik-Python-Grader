"""Дополнительные mock-тесты для downloader.py.

Покрывают непокрытые ветки: конфиг (ask_value/create/load/normalize),
парсинг URL, извлечение кода/функций, HTML-таблицы тестов, save_tests,
build_task_directory, save_task_files (все 4 источника тестов),
process_step_url и main(). Сеть и пользовательский ввод замоканы; файловый
I/O направлен в tmp_path.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from stepik_grader import downloader
from stepik_grader.downloader import (
    ask_value,
    build_task_directory,
    create_or_update_config,
    extract_function_name,
    extract_python_code,
    extract_submission_code,
    extract_tests_from_html,
    load_or_create_config,
    normalize_config_paths,
    parse_stepik_step_url,
    save_task_files,
    save_tests,
)


class TestAskValue:
    """ask_value возвращает ввод пользователя либо дефолт."""

    def test_returns_input(self):
        with patch("builtins.input", return_value="  myval  "):
            assert ask_value("prompt", "def") == "myval"

    def test_returns_default_on_empty(self):
        with patch("builtins.input", return_value=""):
            assert ask_value("prompt", "def") == "def"


class TestConfigFunctions:
    """create/load/normalize конфига — интерактивные ветки."""

    def test_create_or_update_config_writes(self, tmp_path: pathlib.Path):
        """Запрашивает поля и сохраняет конфиг через save_json_file."""
        cfg_path = tmp_path / "cfg.json"
        with patch("stepik_grader.downloader.ask_value", side_effect=["/root", "secrets.json"]):
            config = create_or_update_config(cfg_path)
        assert config == {"root_dir": "/root", "secrets_path": "secrets.json"}
        assert cfg_path.exists()

    def test_load_or_create_when_missing(self, tmp_path: pathlib.Path):
        """Отсутствующий конфиг → запуск create_or_update_config."""
        cfg_path = tmp_path / "nope.json"
        with patch(
            "stepik_grader.downloader.create_or_update_config", return_value={"root_dir": "r"}
        ) as mock_create:
            result = load_or_create_config(cfg_path)
        mock_create.assert_called_once()
        assert result == {"root_dir": "r"}

    def test_load_existing_no_change(self, tmp_path: pathlib.Path):
        """Существующий конфиг, пользователь не хочет менять → возвращается как есть."""
        cfg_path = tmp_path / "cfg.json"
        downloader.save_json_file(cfg_path, {"root_dir": "r", "secrets_path": "s"})
        with patch("builtins.input", return_value="n"):
            result = load_or_create_config(cfg_path)
        assert result["root_dir"] == "r"

    def test_load_existing_with_change(self, tmp_path: pathlib.Path):
        """Пользователь отвечает 'y' → перезапуск создания конфига."""
        cfg_path = tmp_path / "cfg.json"
        downloader.save_json_file(cfg_path, {"root_dir": "r", "secrets_path": "s"})
        with (
            patch("builtins.input", return_value="y"),
            patch(
                "stepik_grader.downloader.create_or_update_config", return_value={"new": 1}
            ) as mock_create,
        ):
            result = load_or_create_config(cfg_path)
        mock_create.assert_called_once()
        assert result == {"new": 1}

    def test_normalize_paths_makes_absolute(self, tmp_path: pathlib.Path):
        """Относительные пути становятся абсолютными; secrets-файл существует."""
        secrets = tmp_path / "secrets.json"
        secrets.write_text("{}", encoding="utf-8")
        cfg_path = tmp_path / "cfg.json"
        config = {"root_dir": "StepikTasks", "secrets_path": str(secrets)}
        result = normalize_config_paths(config, cfg_path)
        assert pathlib.Path(result["root_dir"]).is_absolute()
        assert pathlib.Path(result["secrets_path"]).is_absolute()

    def test_normalize_missing_fields_reprompts(self, tmp_path: pathlib.Path):
        """Пустые обязательные поля → повторный create_or_update_config."""
        secrets = tmp_path / "secrets.json"
        secrets.write_text("{}", encoding="utf-8")
        cfg_path = tmp_path / "cfg.json"
        config = {"root_dir": "", "secrets_path": ""}
        with patch(
            "stepik_grader.downloader.create_or_update_config",
            return_value={
                "root_dir": str(tmp_path / "r"),
                "secrets_path": str(secrets),
            },
        ) as mock_create:
            result = normalize_config_paths(config, cfg_path)
        mock_create.assert_called_once()
        assert pathlib.Path(result["secrets_path"]).is_absolute()

    def test_normalize_secrets_not_found_reprompts(self, tmp_path: pathlib.Path):
        """secrets-файл не существует → повторный запрос конфига."""
        good_secrets = tmp_path / "good.json"
        good_secrets.write_text("{}", encoding="utf-8")
        cfg_path = tmp_path / "cfg.json"
        config = {"root_dir": "r", "secrets_path": str(tmp_path / "missing.json")}
        with patch(
            "stepik_grader.downloader.create_or_update_config",
            return_value={"root_dir": "r2", "secrets_path": str(good_secrets)},
        ) as mock_create:
            result = normalize_config_paths(config, cfg_path)
        mock_create.assert_called_once()
        assert pathlib.Path(result["root_dir"]).is_absolute()


class TestParseStepikStepUrl:
    """parse_stepik_step_url извлекает (lesson_id, step_position)."""

    def test_valid_url(self):
        assert parse_stepik_step_url("https://stepik.org/lesson/569749/step/4?unit=1") == (
            569749,
            4,
        )

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="URL шага"):
            parse_stepik_step_url("https://stepik.org/course/1")


class TestExtractCode:
    """extract_python_code / extract_submission_code / extract_function_name."""

    def test_code_template_from_options(self):
        step = {"block": {"options": [{"code_template": "def f(): pass"}]}}
        assert extract_python_code(step) == "def f(): pass"

    def test_code_from_markdown_block(self):
        step = {"block": {"text": "blah ```python\nx = 1\n``` end"}}
        assert extract_python_code(step) == "x = 1"

    def test_code_none_when_absent(self):
        assert extract_python_code({"block": {"text": "no code"}}) is None

    def test_submission_code(self):
        assert extract_submission_code({"reply": {"code": "print(1)"}}) == "print(1)"

    def test_submission_code_none(self):
        assert extract_submission_code(None) is None
        assert extract_submission_code({"reply": {}}) is None

    def test_function_name_extracted(self):
        assert extract_function_name("def my_func(x):\n    return x") == "my_func"

    def test_function_name_async(self):
        assert extract_function_name("async def af():\n    pass") == "af"

    def test_function_name_none_no_func(self):
        assert extract_function_name("x = 1") is None

    def test_function_name_none_syntax_error(self):
        assert extract_function_name("def (((") is None


class TestExtractTestsFromHtml:
    """extract_tests_from_html парсит HTML-таблицу тест-кейсов."""

    def test_stdin_tests(self):
        html = (
            "<table>"
            "<tr><th>#</th><th>in</th><th>out</th></tr>"
            "<tr><td>1</td><td>print(1)</td><td>1</td></tr>"
            "</table>"
        )
        tests = extract_tests_from_html(html)
        assert len(tests) == 1
        assert tests[0][2] == "stdin"

    def test_function_style_detected(self):
        html = "<table><tr><td>1</td><td>x = 5</td><td>5</td></tr></table>"
        tests = extract_tests_from_html(html)
        assert tests[0][2] == "function"

    def test_short_rows_skipped(self):
        html = "<table><tr><td>only</td><td>two</td></tr></table>"
        assert extract_tests_from_html(html) == []

    def test_empty_cells_skipped(self):
        html = "<table><tr><td>1</td><td></td><td>out</td></tr></table>"
        assert extract_tests_from_html(html) == []


class TestSaveTests:
    """save_tests пишет N / N.clue / N.type."""

    def test_writes_files(self, tmp_path: pathlib.Path):
        tests = [("in1", "out1", "stdin"), ("a=1", "1", "function")]
        count = save_tests(tmp_path, tests)
        assert count == 2
        tdir = tmp_path / "tests"
        assert (tdir / "1").read_text() == "in1"
        assert (tdir / "1.clue").read_text() == "out1"
        assert not (tdir / "1.type").exists()
        assert (tdir / "2.type").read_text() == "function"


class TestBuildTaskDirectory:
    """build_task_directory строит иерархический путь."""

    def test_with_step_title(self, tmp_path: pathlib.Path):
        path = build_task_directory(tmp_path, "Course", "Section", "Lesson", 4, "Step Title")
        assert path.name == "04-step-title"
        assert path.parts[-4:] == ("course", "section", "lesson", "04-step-title")

    def test_without_step_title(self, tmp_path: pathlib.Path):
        path = build_task_directory(tmp_path, "C", "S", "L", 7, "")
        assert path.name == "07"


class TestSaveTaskFiles:
    """save_task_files сохраняет рабочие файлы и выбирает источник тестов."""

    def _step(self, text="", template="def f(): pass"):
        opts = [{"code_template": template}] if template else []
        return {
            "id": 1,
            "position": 3,
            "title": "T",
            "block": {"options": opts, "text": text},
        }

    def _meta(self):
        return {"id": 1, "title": "x"}

    def test_writes_working_files_and_meta(self, tmp_path: pathlib.Path):
        """Создаются task3_1.py, task3_2.py, solution.py, meta.json."""
        step = self._step(text="")
        submission = {"id": 9, "status": "correct", "reply": {"code": "print(1)"}}
        session = MagicMock()
        result = save_task_files(
            tmp_path,
            step,
            submission,
            self._meta(),
            self._meta(),
            self._meta(),
            session,
        )
        assert result == (0, "none")  # пустой block.text -> ранний выход
        assert (tmp_path / "task3_1.py").read_text() == "def f(): pass"
        assert (tmp_path / "task3_2.py").exists()
        assert (tmp_path / "solution.py").read_text() == "print(1)"
        meta = downloader.load_json_file(tmp_path / "meta.json")
        assert meta["function_name"] == "f"
        assert meta["submission_id"] == 9

    def test_no_text_returns_early(self, tmp_path: pathlib.Path):
        """Пустой block.text → task.md не создаётся, ранний выход."""
        step = self._step(text="")
        result = save_task_files(
            tmp_path, step, None, self._meta(), self._meta(), self._meta(), MagicMock()
        )
        assert result == (0, "none")
        assert not (tmp_path / "task.md").exists()

    def test_zip_source_used(self, tmp_path: pathlib.Path):
        """ZIP-ссылка в тексте → вызывается _download_zip_tests."""
        step = self._step(text='<a href="http://x/t.zip">z</a>')
        with patch("stepik_grader.downloader._download_zip_tests", return_value=3) as mock_zip:
            result = save_task_files(
                tmp_path,
                step,
                None,
                self._meta(),
                self._meta(),
                self._meta(),
                MagicMock(),
            )
        mock_zip.assert_called_once()
        assert result == (3, "zip")
        assert (tmp_path / "task.md").exists()

    def test_html_table_source_used(self, tmp_path: pathlib.Path):
        """Нет ZIP, но есть HTML-таблица → save_tests."""
        html = "<table><tr><td>1</td><td>print(1)</td><td>1</td></tr></table>"
        step = self._step(text=html)
        with patch("stepik_grader.downloader.save_tests", return_value=1) as mock_save:
            result = save_task_files(
                tmp_path,
                step,
                None,
                self._meta(),
                self._meta(),
                self._meta(),
                MagicMock(),
            )
        mock_save.assert_called_once()
        assert result == (1, "html_table")

    def test_github_source_used(self, tmp_path: pathlib.Path):
        """Нет ZIP/таблицы, есть GitHub-ссылка → _download_github_tests."""
        step = self._step(text='<a href="https://github.com/o/r/tree/main/d">gh</a>')
        with patch("stepik_grader.downloader._download_github_tests", return_value=2) as mock_gh:
            result = save_task_files(
                tmp_path,
                step,
                None,
                self._meta(),
                self._meta(),
                self._meta(),
                MagicMock(),
            )
        mock_gh.assert_called_once()
        assert result == (2, "github_link")

    def test_github_all_links_fail(self, tmp_path: pathlib.Path):
        """GitHub-ссылки есть, но все дали 0 → предупреждение, return."""
        step = self._step(text='<a href="https://github.com/o/r/tree/main/d">gh</a>')
        with patch("stepik_grader.downloader._download_github_tests", return_value=0):
            result = save_task_files(
                tmp_path,
                step,
                None,
                self._meta(),
                self._meta(),
                self._meta(),
                MagicMock(),
            )
        assert result == (0, "none")
        assert not (tmp_path / "tests" / "input.txt").exists()

    def test_no_tests_anywhere(self, tmp_path: pathlib.Path):
        """Текст без ссылок и таблицы → файлы сохранены, тестов нет."""
        step = self._step(text="just some description text")
        result = save_task_files(
            tmp_path, step, None, self._meta(), self._meta(), self._meta(), MagicMock()
        )
        assert result == (0, "none")
        assert (tmp_path / "task.md").exists()


class TestProcessStepUrl:
    """process_step_url оркеструет вызовы fetch_* и save_task_files."""

    def test_full_flow(self, tmp_path: pathlib.Path):
        """Все fetch_* замоканы → save_task_files вызывается с собранными данными."""
        session = MagicMock()
        with (
            patch(
                "stepik_grader.downloader.fetch_lesson_data", return_value={"id": 1, "title": "L"}
            ),
            patch("stepik_grader.downloader.fetch_unit_data", return_value={"section": 2}),
            patch(
                "stepik_grader.downloader.fetch_section_data",
                return_value={"id": 2, "course": 3, "title": "S"},
            ),
            patch(
                "stepik_grader.downloader.fetch_course_data", return_value={"id": 3, "title": "C"}
            ),
            patch(
                "stepik_grader.downloader.fetch_step_data",
                return_value={"id": 4, "position": 5, "title": "Step"},
            ),
            patch("stepik_grader.downloader.fetch_submission_data", return_value=None),
            patch(
                "stepik_grader.downloader.save_task_files", return_value=(3, "html_table")
            ) as mock_save,
        ):
            result = downloader.process_step_url(
                "https://stepik.org/lesson/1/step/5?unit=2", session, tmp_path
            )
        mock_save.assert_called_once()
        task_dir, count, source = result
        assert task_dir == downloader.build_task_directory(tmp_path, "C", "S", "L", 5, "Step")
        assert count == 3
        assert source == "html_table"


class TestMain:
    """main() — конфиг → авторизация → цикл URL."""

    def test_config_error_aborts(self):
        """Ошибка работы с конфигом → ранний return без авторизации."""
        with (
            patch(
                "stepik_grader.downloader.load_or_create_config", side_effect=RuntimeError("boom")
            ),
            patch("stepik_grader.downloader.create_user_session") as mock_sess,
        ):
            downloader.main()
        mock_sess.assert_not_called()

    def test_auth_error_aborts(self, tmp_path: pathlib.Path):
        """Ошибка авторизации → return до цикла обработки URL."""
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch(
                "stepik_grader.downloader.load_secrets_dict", side_effect=RuntimeError("no secrets")
            ),
            patch("builtins.input") as mock_input,
        ):
            downloader.main()
        mock_input.assert_not_called()

    def test_processes_urls_until_blank(self, tmp_path: pathlib.Path):
        """Цикл обрабатывает URL пока не введена пустая строка."""
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", return_value={}),
            patch("stepik_grader.downloader.create_user_session", return_value=MagicMock()),
            patch("builtins.input", side_effect=["http://step/url", ""]),
            patch("stepik_grader.downloader.process_step_url") as mock_proc,
        ):
            downloader.main()
        assert mock_proc.call_count == 1

    def test_process_error_is_caught(self, tmp_path: pathlib.Path):
        """Ошибка в process_step_url не прерывает цикл."""
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", return_value={}),
            patch("stepik_grader.downloader.create_user_session", return_value=MagicMock()),
            patch("builtins.input", side_effect=["http://bad", ""]),
            patch("stepik_grader.downloader.process_step_url", side_effect=ValueError("bad url")),
        ):
            downloader.main()  # не должно бросить


class TestDownloadZipErrorPath:
    """_download_zip_tests при сетевой ошибке возвращает 0."""

    def test_network_error_returns_zero(self, tmp_path: pathlib.Path):
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("down")
        assert downloader._download_zip_tests(tmp_path, "http://x/t.zip", session) == 0
