"""Mock-тесты оркестрации downloader.py (issue #302).

Покрывают координатор-слой: build_task_directory, save_task_files (все 4
источника тестов), process_step_url и main(). Чистые unit-тесты вынесенных
модулей — в test_task_page_parser.py / test_tests_writer.py /
test_step_content.py / test_downloader_config.py / test_test_source_fetcher.py.
Сеть и пользовательский ввод замоканы; файловый I/O направлен в tmp_path.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from stepik_grader import downloader
from stepik_grader.core.storage import load_json_file
from stepik_grader.downloader import build_task_directory, save_task_files


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
        meta = load_json_file(tmp_path / "meta.json")
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

    def test_existing_nonempty_solution_not_overwritten(self, tmp_path: pathlib.Path):
        """issue #554: повторное скачивание не затирает написанное решение студента."""
        main_file = tmp_path / "task3_1.py"
        student_code = "# моё решение\nprint(input())\n"
        main_file.write_text(student_code, encoding="utf-8")
        step = self._step(text="")
        save_task_files(tmp_path, step, None, self._meta(), self._meta(), self._meta(), MagicMock())
        assert main_file.read_text(encoding="utf-8") == student_code

    def test_empty_solution_is_filled_from_template(self, tmp_path: pathlib.Path):
        """issue #554: пустой (пробельный) task{N}_1.py всё ещё заполняется шаблоном."""
        main_file = tmp_path / "task3_1.py"
        main_file.write_text("   \n", encoding="utf-8")
        step = self._step(text="")
        save_task_files(tmp_path, step, None, self._meta(), self._meta(), self._meta(), MagicMock())
        assert main_file.read_text(encoding="utf-8") == "def f(): pass"

    def test_non_utf8_solution_preserved(self, tmp_path: pathlib.Path):
        """issue #554: решение в не-UTF-8 (cp1251) сохраняется, загрузчик не падает."""
        main_file = tmp_path / "task3_1.py"
        raw = "# решение\nprint('привет')\n".encode("cp1251")
        main_file.write_bytes(raw)
        step = self._step(text="")
        save_task_files(tmp_path, step, None, self._meta(), self._meta(), self._meta(), MagicMock())
        assert main_file.read_bytes() == raw


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

    def test_auth_error_aborts(self, tmp_path: pathlib.Path, capsys):
        """Ошибка авторизации → дружелюбная подсказка со следующим шагом + return
        до цикла обработки URL (issue #433 crit 3)."""
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
        out = capsys.readouterr().out
        assert "diagnostic_stepik" in out  # следующий шаг, а не голый текст ошибки
        assert downloader.STEPIK_OAUTH_APPS_URL in out

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


class TestDownloaderCli:
    """issue #997: точка входа разбирает аргументы и сообщает исход процессу."""

    def test_help_prints_usage_instead_of_running_wizard(self, tmp_path, monkeypatch, capsys):
        """`--help` печатает справку и не создаёт stepik_config.json (INS-5-01).

        Модуль сразу уходил в мастер конфигурации, поэтому документированная
        команда не разбирала собственных флагов: `--help` съедался первым
        вопросом, в текущем каталоге появлялся конфиг, а на закрытом stdin
        прогон падал с кодом 0.
        """
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc:
            downloader.run_cli(["--help"])

        assert exc.value.code == 0
        assert "--lang" in capsys.readouterr().out
        assert not (tmp_path / "stepik_config.json").exists()

    def test_config_failure_returns_nonzero_and_names_the_file(self, tmp_path, monkeypatch, capsys):
        """Сбой конфига → код 1 и путь к файлу (INS-3-03, RUN-5-05, JRN-3A-01)."""
        monkeypatch.chdir(tmp_path)
        with patch(
            "stepik_grader.downloader.load_or_create_config", side_effect=RuntimeError("битый json")
        ):
            code = downloader.run_cli([])

        out = capsys.readouterr().out.replace("\n", "")
        assert code == 1
        assert "битый json" in out
        assert "stepik_config.json" in out  # файл назван, а не «ошибка конфига»

    def test_urls_from_arguments_skip_the_prompt(self, tmp_path, monkeypatch):
        """URL из аргументов скачиваются без единого вопроса — режим для скриптов."""
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        monkeypatch.chdir(tmp_path)
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", return_value={}),
            patch("stepik_grader.downloader.create_user_session", return_value=MagicMock()),
            patch("builtins.input") as mock_input,
            patch(
                "stepik_grader.downloader.process_step_url",
                return_value=(tmp_path / "task", None, None),
            ) as mock_proc,
        ):
            code = downloader.run_cli(["https://stepik.org/lesson/1/step/1"])

        mock_input.assert_not_called()
        assert mock_proc.call_count == 1
        assert code == 0

    def test_failed_step_gives_nonzero_code(self, tmp_path, monkeypatch):
        """Отказ по шагу — тоже исход: скрипт, заказавший URL, должен узнать."""
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        monkeypatch.chdir(tmp_path)
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", return_value={}),
            patch("stepik_grader.downloader.create_user_session", return_value=MagicMock()),
            patch(
                "stepik_grader.downloader.process_step_url",
                side_effect=ValueError("нет такого шага"),
            ),
        ):
            code = downloader.run_cli(["https://stepik.org/lesson/1/step/1"])

        assert code == 1

    def test_eof_in_url_loop_finishes_normally(self, tmp_path, monkeypatch):
        """Закрытый stdin завершает ввод, а не роняет трейсбек (RUN-5-04).

        «Ввода больше нет» — то же самое, что пустая строка, и обрабатываться
        должно так же.
        """
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        monkeypatch.chdir(tmp_path)
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", return_value={}),
            patch("stepik_grader.downloader.create_user_session", return_value=MagicMock()),
            patch("builtins.input", side_effect=EOFError),
        ):
            code = downloader.run_cli([])

        assert code == 0


class TestMainReturnsDownloadedPaths:
    """issue #822 (PROD-09): main() отдаёт скачанные каталоги — меню предлагает по ним проверку.

    Раньше возвращался ``None``, и замкнуть цикл download→grade было не на чем:
    пользователь набирал многосегментный путь с кириллическими slug'ами руками
    ровно в момент активации, «задача скачана → первый зелёный прогон».
    """

    @staticmethod
    def _run(tmp_path: pathlib.Path, urls: list[str], side_effect: object) -> object:
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", return_value={}),
            patch("stepik_grader.downloader.create_user_session", return_value=MagicMock()),
            patch("builtins.input", side_effect=[*urls, ""]),
            patch("stepik_grader.downloader.process_step_url", side_effect=side_effect),
        ):
            return downloader.main()

    def test_returns_task_dirs_in_download_order(self, tmp_path: pathlib.Path):
        first, second = tmp_path / "t1", tmp_path / "t2"
        result = self._run(
            tmp_path,
            ["http://a", "http://b"],
            [(first, 3, "attempt"), (second, 5, "html_table")],
        )
        assert result == [first, second]

    def test_failed_url_is_not_reported_as_downloaded(self, tmp_path: pathlib.Path):
        """Отказ по одному URL не должен предлагать проверку несуществующей задачи."""
        ok = tmp_path / "ok"
        result = self._run(
            tmp_path,
            ["http://bad", "http://good"],
            [ValueError("bad url"), (ok, 1, "attempt")],
        )
        assert result == [ok]

    def test_config_error_returns_empty_list(self, tmp_path: pathlib.Path):
        """Ранний выход отдаёт список, а не ``None`` — вызывающему нечего проверять."""
        with patch(
            "stepik_grader.downloader.load_or_create_config", side_effect=RuntimeError("boom")
        ):
            assert downloader.main() == []

    def test_auth_error_returns_empty_list(self, tmp_path: pathlib.Path):
        cfg = {"root_dir": str(tmp_path), "secrets_path": str(tmp_path / "s.json")}
        with (
            patch("stepik_grader.downloader.load_or_create_config", return_value=cfg),
            patch("stepik_grader.downloader.normalize_config_paths", return_value=cfg),
            patch("stepik_grader.downloader.load_secrets_dict", side_effect=RuntimeError("no")),
        ):
            assert downloader.main() == []
