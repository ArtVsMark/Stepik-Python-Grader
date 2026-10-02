"""Тесты импорта закреплённых решений Stepik (issue #55)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stepik_grader.core import stepik_client
from stepik_grader.core import stepik_reference as sr
from stepik_grader.core.step_content import pick_solutions_thread
from stepik_grader.core.test_loader import is_solution_file


def _comment(cid: int, sub: int, likes: int, *, pinned: bool = False) -> dict:
    return {"id": cid, "submission": sub, "epic_count": likes, "is_pinned": pinned}


def _submission(sub_id: int, code: str) -> dict:
    return {"id": sub_id, "reply": {"code": code}}


class TestSelectReferenceSolutions:
    def test_pinned_first_then_by_likes(self) -> None:
        comments = [_comment(1, 10, 50), _comment(2, 20, 189, pinned=True), _comment(3, 30, 80)]
        subs = [_submission(10, "a=1"), _submission(20, "b=2"), _submission(30, "c=3")]
        sel = sr.select_reference_solutions(comments, subs, max_top=5, min_likes=1)
        assert [r.comment_id for r in sel] == [2, 3, 1]  # pinned, затем 80, затем 50
        assert sel[0].is_pinned

    def test_zero_likes_filtered_from_top(self) -> None:
        comments = [_comment(1, 10, 100, pinned=True), _comment(2, 20, 0)]
        subs = [_submission(10, "a=1"), _submission(20, "b=2")]
        sel = sr.select_reference_solutions(comments, subs, max_top=5, min_likes=1)
        assert [r.comment_id for r in sel] == [1]  # нулёвое не берём в top

    def test_pinned_taken_even_with_low_likes(self) -> None:
        # эталон (pinned) берём всегда, даже если лайков меньше порога
        comments = [_comment(1, 10, 0, pinned=True)]
        subs = [_submission(10, "a=1")]
        sel = sr.select_reference_solutions(comments, subs, max_top=5, min_likes=10)
        assert [r.comment_id for r in sel] == [1]

    def test_duplicate_code_collapsed(self) -> None:
        comments = [_comment(1, 10, 100, pinned=True), _comment(2, 20, 90), _comment(3, 30, 80)]
        subs = [_submission(10, "x=1"), _submission(20, "y=2"), _submission(30, "x=1")]  # 30==10
        sel = sr.select_reference_solutions(comments, subs, max_top=5, min_likes=1)
        assert [r.comment_id for r in sel] == [1, 2]  # дубль #3 схлопнут

    def test_no_pinned_best_by_likes_becomes_reference(self) -> None:
        comments = [_comment(1, 10, 50), _comment(2, 20, 120)]
        subs = [_submission(10, "a=1"), _submission(20, "b=2")]
        sel = sr.select_reference_solutions(comments, subs, max_top=5, min_likes=1)
        assert sel[0].comment_id == 2  # лучший по лайкам — эталон

    def test_max_top_limits_count(self) -> None:
        comments = [_comment(0, 0, 500, pinned=True)] + [
            _comment(i, i, 100 - i) for i in range(1, 10)
        ]
        subs = [_submission(0, "p=0")] + [_submission(i, f"s={i}") for i in range(1, 10)]
        sel = sr.select_reference_solutions(comments, subs, max_top=3, min_likes=1)
        assert len(sel) == 4  # эталон + 3 топовых

    def test_empty_when_no_code(self) -> None:
        comments = [_comment(1, 10, 100, pinned=True)]
        subs = [{"id": 10, "reply": {"code": ""}}]  # код пустой
        assert sr.select_reference_solutions(comments, subs) == []

    def test_missing_submission_skipped(self) -> None:
        comments = [_comment(1, 999, 100, pinned=True)]  # submission 999 нет в subs
        assert sr.select_reference_solutions(comments, [_submission(10, "a=1")]) == []


class TestReferenceSlots:
    def test_filename_format(self) -> None:
        assert sr.reference_slot_filename(3, 100) == "task3_100.py"

    @pytest.mark.parametrize("name", ["task3_100.py", "task7_101.py", "task12_105.py"])
    def test_names_compatible_with_modes_2_4(self, name: str) -> None:
        # ключевой AC: reference-имя подхватывается find_all_solution_files
        assert is_solution_file(name) is True

    def test_next_free_slot_empty_dir(self, tmp_path: Path) -> None:
        assert sr.next_free_reference_slot(tmp_path, 3) == 100

    def test_next_free_slot_skips_existing(self, tmp_path: Path) -> None:
        (tmp_path / "task3_100.py").write_text("x", encoding="utf-8")
        (tmp_path / "task3_101.py").write_text("x", encoding="utf-8")
        assert sr.next_free_reference_slot(tmp_path, 3) == 102


def _write_meta(task_dir: Path, **overrides) -> None:
    meta = {"lesson_id": 571244, "step_position": 3, "step_id": 2506803}
    meta.update(overrides)
    (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _patch_chain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    threads: list[dict] | None = None,
    most_liked: list[int] | None = None,
    comments: list[dict] | None = None,
    submissions: list[dict] | None = None,
) -> None:
    monkeypatch.setattr(sr, "load_secrets_dict", lambda _p: {})
    monkeypatch.setattr(sr, "create_user_session", lambda _s, _p: MagicMock())
    monkeypatch.setattr(
        sr,
        "fetch_step_data",
        lambda _s, _l, _p: {"discussion_threads": ["t1", "t2"]},
    )
    monkeypatch.setattr(
        sr,
        "fetch_discussion_threads",
        lambda _s, _ids: (
            threads
            if threads is not None
            else [{"thread": "default"}, {"thread": "solutions", "discussion_proxy": "p2"}]
        ),
    )
    monkeypatch.setattr(
        sr,
        "fetch_discussion_proxy",
        lambda _s, _pid: {
            "discussions_most_liked": most_liked if most_liked is not None else [1, 2]
        },
    )
    monkeypatch.setattr(
        sr,
        "fetch_comments_with_submissions",
        lambda _s, _ids: (
            comments
            if comments is not None
            else [_comment(1, 10, 189, pinned=True), _comment(2, 20, 50)],
            submissions
            if submissions is not None
            else [_submission(10, "print(1)"), _submission(20, "print(2)")],
        ),
    )


class TestImportOrchestration:
    def test_happy_path_saves_files_and_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_meta(tmp_path)
        _patch_chain(monkeypatch)
        saved = sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "secrets.json")
        assert [p.name for p in saved] == ["task3_100.py", "task3_101.py"]
        assert (tmp_path / "task3_100.py").read_text(encoding="utf-8") == "print(1)\n"
        # привязка записана в meta
        meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        assert meta["stepik_references"][0]["is_pinned"] is True
        assert meta["stepik_references"][0]["file"] == "task3_100.py"

    def test_missing_meta_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            sr.import_references_from_task_dir(tmp_path)

    def test_corrupt_meta_raises(self, tmp_path: Path) -> None:
        (tmp_path / "meta.json").write_text("{ не json", encoding="utf-8")
        with pytest.raises(ValueError):  # JSONDecodeError наследует ValueError
            sr.import_references_from_task_dir(tmp_path)

    def test_incomplete_meta_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "meta.json").write_text(json.dumps({"step_id": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="lesson_id"):
            sr.import_references_from_task_dir(tmp_path)

    def test_no_solutions_thread_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_meta(tmp_path)
        _patch_chain(monkeypatch, threads=[{"thread": "default"}])
        with pytest.raises(ValueError, match="ветк"):
            sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "s.json")

    def test_no_solutions_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_meta(tmp_path)
        _patch_chain(monkeypatch, most_liked=[])
        with pytest.raises(ValueError, match="нет решений"):
            sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "s.json")

    def test_no_code_extracted_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_meta(tmp_path)
        _patch_chain(
            monkeypatch,
            comments=[_comment(1, 10, 100, pinned=True)],
            submissions=[{"id": 10, "reply": {"code": ""}}],
        )
        with pytest.raises(ValueError, match="извлечь"):
            sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "s.json")


class TestPickSolutionsThread:
    def test_finds_solutions(self) -> None:
        threads = [{"thread": "default"}, {"thread": "solutions", "discussion_proxy": "p2"}]
        assert pick_solutions_thread(threads)["discussion_proxy"] == "p2"

    def test_none_when_absent(self) -> None:
        assert pick_solutions_thread([{"thread": "default"}]) is None

    def test_none_when_empty(self) -> None:
        assert pick_solutions_thread([]) is None


class TestFetchFunctions:
    def test_comments_uses_expand_submission(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_cached(_session, url, params=None):
            captured["url"], captured["params"] = url, params
            return {"comments": [{"id": 1}], "submissions": [{"id": 10}]}

        monkeypatch.setattr(stepik_client, "_cached_api_get", fake_cached)
        comments, subs = stepik_client.fetch_comments_with_submissions(MagicMock(), [1, 2])
        assert captured["url"].endswith("/api/comments")
        assert captured["params"]["expand"] == "submission"
        assert captured["params"]["ids[]"] == [1, 2]
        assert comments == [{"id": 1}] and subs == [{"id": 10}]

    def test_comments_empty_shortcircuits(self) -> None:
        assert stepik_client.fetch_comments_with_submissions(MagicMock(), []) == ([], [])

    def test_threads_passes_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_cached(_session, url, params=None):
            captured["url"], captured["params"] = url, params
            return {"discussion-threads": [{"thread": "solutions"}]}

        monkeypatch.setattr(stepik_client, "_cached_api_get", fake_cached)
        result = stepik_client.fetch_discussion_threads(MagicMock(), ["77-1-1", "77-1-2"])
        assert captured["url"].endswith("/api/discussion-threads")
        assert captured["params"]["ids[]"] == ["77-1-1", "77-1-2"]
        assert result == [{"thread": "solutions"}]

    def test_threads_empty_shortcircuits(self) -> None:
        assert stepik_client.fetch_discussion_threads(MagicMock(), []) == []

    def test_proxy_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            stepik_client, "_cached_api_get", lambda _s, _u, params=None: {"discussion-proxies": []}
        )
        with pytest.raises(ValueError, match="proxy"):
            stepik_client.fetch_discussion_proxy(MagicMock(), "p2")


# ---------------------------------------------------------------------------
# Идемпотентность импорта и устойчивость к смене формата API (issue #944)
# ---------------------------------------------------------------------------


class TestRepeatedImport:
    """Повторный импорт не должен множить файлы и терять привязку."""

    def test_second_import_creates_no_duplicates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Тот же reference, импортированный дважды, лежит в папке один раз.

        issue #944: дедуп по коду работал только внутри партии, а слот брался
        первый свободный — в папке оказывалось 12 файлов вместо 6, и режимы 2-4
        гоняли один и тот же reference дважды, искажая сравнение решений.
        """
        _write_meta(tmp_path)
        _patch_chain(monkeypatch)
        first = sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "secrets.json")

        second = sr.import_references_from_task_dir(
            tmp_path, secrets_path=tmp_path / "secrets.json"
        )

        assert [p.name for p in first] == ["task3_100.py", "task3_101.py"]
        assert second == [], "повторный импорт создал файлы заново"
        assert sorted(p.name for p in tmp_path.glob("task3_1*.py")) == [
            "task3_100.py",
            "task3_101.py",
        ]

    def test_second_import_keeps_previous_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Привязка ранее импортированных файлов остаётся в meta.json (issue #944).

        Раньше список заменялся целиком, и прежние файлы становились «ничьими»:
        на диске лежат, а meta про них не знает.
        """
        _write_meta(tmp_path)
        _patch_chain(monkeypatch)
        sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "secrets.json")

        sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "secrets.json")

        meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
        files = [entry["file"] for entry in meta["stepik_references"]]
        assert files == ["task3_100.py", "task3_101.py"]


class TestApiShapeChanges:
    """Смена формата ответа Stepik даёт понятную ошибку, а не голый трейсбек."""

    def test_thread_without_proxy_raises_value_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ветка решений без `discussion_proxy` — ValueError с текстом (issue #944)."""
        _write_meta(tmp_path)
        _patch_chain(monkeypatch, threads=[{"thread": "solutions"}])

        with pytest.raises(ValueError, match="discussion_proxy"):
            sr.import_references_from_task_dir(tmp_path, secrets_path=tmp_path / "secrets.json")

    def test_threads_as_strings_do_not_crash(self) -> None:
        """Список строк вместо словарей не роняет выбор ветки (issue #944).

        Прежний прямой `.get` давал `AttributeError: 'str' object has no
        attribute 'get'`.
        """
        assert pick_solutions_thread(["мусор", "из", "кэша"]) is None  # type: ignore[list-item]
