"""Tests for core/stats.py — opt-in локальная статистика запусков (issue #268)."""

from __future__ import annotations

import json
import pathlib

from stepik_grader.core import stats


class TestRecordRun:
    def test_appends_one_jsonl_line(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".grader_stats.jsonl"
        stats.record_run(2, {"AC": 3, "WA": 1}, 1.5, stats_path=path)

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["mode"] == 2
        assert entry["verdicts"] == {"AC": 3, "WA": 1}
        assert entry["total_time"] == 1.5
        assert "os" in entry
        assert "ts" in entry

    def test_multiple_calls_append_multiple_lines(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".grader_stats.jsonl"
        stats.record_run(1, {"AC": 1}, 0.1, stats_path=path)
        stats.record_run(1, {"WA": 1}, 0.2, stats_path=path)
        stats.record_run(3, {"SIMILAR": 1}, 0.3, stats_path=path)

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_directory_instead_of_file_is_graceful(self, tmp_path: pathlib.Path) -> None:
        """A path that can't be opened for append (e.g. it's a directory) must
        not raise -- best-effort, same principle as GraderCache (issue #56)."""
        path = tmp_path / "not_a_file"
        path.mkdir()
        stats.record_run(1, {"AC": 1}, 0.1, stats_path=path)  # must not raise

    def test_unwritable_parent_directory_is_graceful(self, tmp_path: pathlib.Path) -> None:
        """Parent directory doesn't exist -- open() raises FileNotFoundError,
        a subclass of OSError, caught the same way."""
        path = tmp_path / "no" / "such" / "dir" / ".grader_stats.jsonl"
        stats.record_run(1, {"AC": 1}, 0.1, stats_path=path)  # must not raise


class TestReadSummary:
    def test_missing_file_returns_empty_summary(self, tmp_path: pathlib.Path) -> None:
        summary = stats.read_summary(stats_path=tmp_path / "does-not-exist.jsonl")
        assert summary == {
            "total_runs": 0,
            "by_mode": {},
            "by_os": {},
            "verdict_totals": {},
            "total_time": 0.0,
        }

    def test_aggregates_across_multiple_records(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".grader_stats.jsonl"
        stats.record_run(1, {"AC": 2, "WA": 1}, 1.0, stats_path=path)
        stats.record_run(1, {"AC": 1}, 0.5, stats_path=path)
        stats.record_run(2, {"AC": 5}, 2.0, stats_path=path)

        summary = stats.read_summary(stats_path=path)
        assert summary["total_runs"] == 3
        assert summary["by_mode"] == {1: 2, 2: 1}
        assert summary["verdict_totals"] == {"AC": 8, "WA": 1}
        assert summary["total_time"] == 3.5
        assert list(summary["by_os"].values()) == [3]  # all recorded on this machine's OS

    def test_malformed_lines_are_skipped_not_fatal(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".grader_stats.jsonl"
        valid_entry = {"v": 1, "mode": 1, "os": "Linux", "verdicts": {"AC": 1}, "total_time": 1.0}
        path.write_text(
            "not json at all\n" + json.dumps(valid_entry) + "\n"
            "{{{broken\n"
            "\n"  # blank line
             + json.dumps("just a string, not an object") + "\n",
            encoding="utf-8",
        )
        summary = stats.read_summary(stats_path=path)
        assert summary["total_runs"] == 1
        assert summary["verdict_totals"] == {"AC": 1}

    def test_entries_missing_optional_fields_are_tolerated(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / ".grader_stats.jsonl"
        path.write_text(json.dumps({"mode": 1}) + "\n", encoding="utf-8")
        summary = stats.read_summary(stats_path=path)
        assert summary["total_runs"] == 1
        assert summary["by_os"] == {}
        assert summary["verdict_totals"] == {}
        assert summary["total_time"] == 0.0


class TestRotation:
    def test_rotates_when_file_exceeds_max_bytes(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        path = tmp_path / ".grader_stats.jsonl"
        # Force a tiny rotation threshold so the test doesn't need to write 1 MiB.
        monkeypatch.setattr(stats, "_MAX_BYTES", 200)

        for i in range(20):
            stats.record_run(1, {"AC": i}, float(i), stats_path=path)

        lines = path.read_text(encoding="utf-8").splitlines()
        # Rotation keeps the newest half -- the very last entry written must
        # still be present, and the file must not have grown unbounded.
        last_entry = json.loads(lines[-1])
        assert last_entry["verdicts"] == {"AC": 19}
        assert len(lines) < 20  # some older entries were dropped by rotation
