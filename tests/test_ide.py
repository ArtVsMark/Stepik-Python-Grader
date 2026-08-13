"""Tests for ide.py + cli --init-vscode (эпик #80 Tier 2 / issue #58)."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

from stepik_grader import cli, ide
from stepik_grader.cli import options


class TestWriteVscodeTasks:
    def test_creates_valid_tasks_json(self, tmp_path: pathlib.Path) -> None:
        written, path = ide.write_vscode_tasks(tmp_path)
        assert written is True
        assert path == tmp_path / ".vscode" / "tasks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == "2.0.0"
        labels = [t["label"] for t in data["tasks"]]
        assert any("текущий файл" in lbl for lbl in labels)
        # Задачи запускаются через интерпретатор VS Code, а не консольную
        # команду stepik-grader (иначе нужен активированный venv в PATH).
        assert all(t["command"] == "${command:python.interpreterPath}" for t in data["tasks"])
        assert all(t["type"] == "process" for t in data["tasks"])
        assert all(t["args"][:2] == ["-m", "stepik_grader.grader"] for t in data["tasks"])

    def test_default_task_grades_current_file(self, tmp_path: pathlib.Path) -> None:
        _, path = ide.write_vscode_tasks(tmp_path)
        tasks = json.loads(path.read_text(encoding="utf-8"))["tasks"]
        default = next(t for t in tasks if t.get("group", {}).get("isDefault"))
        assert default["args"] == ["-m", "stepik_grader.grader", "--mode", "1", "--file", "${file}"]

    def test_does_not_overwrite_existing(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".vscode" / "tasks.json"
        path.parent.mkdir()
        path.write_text("KEEP ME", encoding="utf-8")
        written, returned = ide.write_vscode_tasks(tmp_path)
        assert written is False
        assert returned == path
        assert path.read_text(encoding="utf-8") == "KEEP ME"

    def test_overwrite_true_replaces(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".vscode" / "tasks.json"
        path.parent.mkdir()
        path.write_text("OLD", encoding="utf-8")
        written, _ = ide.write_vscode_tasks(tmp_path, overwrite=True)
        assert written is True
        assert "stepik_grader.grader" in path.read_text(encoding="utf-8")

    def test_vscode_tasks_constant_is_serializable(self) -> None:
        # Гарантируем, что шаблон всегда валидно сериализуется в JSON.
        assert json.loads(json.dumps(ide.VSCODE_TASKS))["version"] == "2.0.0"


class TestInitVscodeCli:
    def test_main_writes_tasks(self, tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        cli.main(["--init-vscode"])
        assert (tmp_path / ".vscode" / "tasks.json").is_file()
        assert "VS Code" in capsys.readouterr().out

    def test_main_reports_existing(self, tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".vscode").mkdir()
        (tmp_path / ".vscode" / "tasks.json").write_text("x", encoding="utf-8")
        cli.main(["--init-vscode"])
        out = capsys.readouterr().out
        assert "exists" in out.lower() or "существует" in out
        # Существующий файл не тронут.
        assert (tmp_path / ".vscode" / "tasks.json").read_text(encoding="utf-8") == "x"


class TestVscodeTasksMatchCli:
    """Шаблон задач не расходится с реальным CLI (issue #997, STR-1-07).

    ``VSCODE_TASKS`` — статический JSON: переименуй флаг в парсере, и задачи
    VS Code сломаются молча, у пользователя, а не в CI. Гейт сверяет шаблон с
    самим парсером, поэтому дрейф падает здесь.
    """

    def _task_args(self) -> list[list[str]]:
        return [task["args"] for task in ide.VSCODE_TASKS["tasks"]]

    def test_every_flag_exists_in_parser(self) -> None:
        parser = options._build_arg_parser()
        known = {opt for action in parser._actions for opt in action.option_strings}

        used = {arg for args in self._task_args() for arg in args if arg.startswith("--")}
        assert used, "в шаблоне не осталось флагов — гейт перестал что-либо проверять"
        assert used <= known, f"флаги из tasks.json исчезли из CLI: {sorted(used - known)}"

    def test_every_task_is_accepted_by_parser(self) -> None:
        """Каждая задача разбирается парсером целиком, включая значения --mode."""
        parser = options._build_arg_parser()
        for args in self._task_args():
            # ${file}/${fileDirname} подставляет VS Code — здесь достаточно,
            # что парсер принимает форму команды.
            cli_args = [a for a in args if a not in ("-m", "stepik_grader.grader")]
            parser.parse_args(cli_args)

    def test_module_target_is_importable(self) -> None:
        """``-m stepik_grader.grader`` указывает на существующую точку входа."""
        assert importlib.util.find_spec("stepik_grader.grader") is not None


class TestModuleEntryPoint:
    """``python -m stepik_grader.ide`` не притворяется успешной командой."""

    def test_direct_run_explains_and_fails(self) -> None:
        """issue #997 (INS-3-04): был тихий no-op с кодом 0."""
        proc = subprocess.run(
            [sys.executable, "-m", "stepik_grader.ide"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        assert proc.returncode != 0
        assert "--init-vscode" in proc.stderr
        assert proc.stdout.strip() == ""
