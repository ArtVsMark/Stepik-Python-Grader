"""Tests for ide.py + cli --init-vscode (эпик #80 Tier 2 / issue #58)."""

from __future__ import annotations

import json
import pathlib

from stepik_grader import cli, ide


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
