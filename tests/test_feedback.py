"""Tests for core/feedback.py — канал обратной связи (issue #753, эпик #751).

Проверяется то, что делает канал безопасным и рабочим: контракт `id` полей с
YAML-формами, редакция секретов, сворачивание домашнего пути, отсутствие имени
машины в окружении и укладывание URL в лимит длины без молчаливой потери данных.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import urllib.parse

import pytest

from stepik_grader.core import feedback

_TEMPLATE_DIR = pathlib.Path(__file__).parent.parent / ".github" / "ISSUE_TEMPLATE"
# `id: foo-bar` на любом уровне вложенности YAML-формы. Полноценный YAML-парсер
# не нужен и недоступен: PyYAML не в зависимостях проекта (CLAUDE.md запрещает
# добавлять их без явного указания), а формат этих файлов машинно однороден.
_ID_RE = re.compile(r"^\s*id:\s*(\S+)\s*$", re.MULTILINE)


def _query(url: str) -> dict[str, list[str]]:
    """Разобрать query-часть prefilled-URL в словарь параметров."""
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


class TestTemplateContract:
    """`_FIELD_IDS`/`_TEMPLATES` обязаны совпадать с YAML-формами: неизвестный
    id GitHub игнорирует молча, и расхождение потеряло бы данные незаметно."""

    @pytest.mark.parametrize("kind", list(feedback.FeedbackKind))
    def test_template_file_exists(self, kind: feedback.FeedbackKind) -> None:
        assert (_TEMPLATE_DIR / feedback._TEMPLATES[kind]).is_file()

    @pytest.mark.parametrize("kind", list(feedback.FeedbackKind))
    def test_field_ids_match_form(self, kind: feedback.FeedbackKind) -> None:
        raw = (_TEMPLATE_DIR / feedback._TEMPLATES[kind]).read_text(encoding="utf-8")
        assert set(_ID_RE.findall(raw)) == set(feedback._FIELD_IDS[kind])

    def test_every_kind_has_template(self) -> None:
        assert set(feedback._TEMPLATES) == set(feedback.FeedbackKind)
        assert set(feedback._FIELD_IDS) == set(feedback.FeedbackKind)

    def test_sacrifice_order_names_are_real_fields(self) -> None:
        known = set().union(*feedback._FIELD_IDS.values())
        assert set(feedback._SACRIFICE_ORDER) <= known


class TestKindFromStr:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("bug", feedback.FeedbackKind.BUG),
            ("  IDEA ", feedback.FeedbackKind.IDEA),
            ("task-problem", feedback.FeedbackKind.TASK_PROBLEM),
        ],
    )
    def test_valid(self, raw: str, expected: feedback.FeedbackKind) -> None:
        assert feedback.kind_from_str(raw) is expected

    @pytest.mark.parametrize("raw", ["", "bugs", "task_problem", "🐞"])
    def test_garbage_is_none(self, raw: str) -> None:
        assert feedback.kind_from_str(raw) is None


class TestCollectEnvironment:
    def test_contains_version_os_python(self) -> None:
        env = feedback.collect_environment(channel="CLI (интерактивное меню)")
        assert "Версия грейдера:" in env
        assert "ОС:" in env
        assert "Python:" in env
        assert "CLI (интерактивное меню)" in env

    def test_hostname_is_not_leaked(self) -> None:
        """Имя машины часто содержит имя владельца — в обращение не попадает."""
        import platform

        node = platform.node()
        env = feedback.collect_environment(channel="web (--serve)")
        if node:  # на CI бывает пусто
            assert node not in env

    def test_sandbox_default_says_disabled(self) -> None:
        env = feedback.collect_environment(channel="web (--serve)")
        assert "--sandbox не задан" in env

    def test_sandbox_reported_when_active(self) -> None:
        env = feedback.collect_environment(channel="web (--serve)", sandbox="да (--sandbox)")
        assert "Песочница: да (--sandbox)" in env

    def test_lang_optional(self) -> None:
        assert "Локаль" not in feedback.collect_environment(channel="CLI")
        assert "Локаль интерфейса: en" in feedback.collect_environment(channel="CLI", lang="en")


class TestCollectCommit:
    """Поле `commit` привязывает отчёт к точке истории — но только там, где есть git."""

    def test_returns_oneline_in_repo(self) -> None:
        commit = feedback.collect_commit(cwd=pathlib.Path(__file__).parent.parent)
        assert commit is not None
        # `git log --oneline -1` → «<hash> <subject>», в одну строку.
        assert "\n" not in commit
        assert re.match(r"^[0-9a-f]{7,}\s", commit), commit

    def test_none_outside_repo(self, tmp_path: pathlib.Path) -> None:
        """Вне git-репозитория поле просто не заполняется, а не роняет обращение."""
        assert feedback.collect_commit(cwd=tmp_path) is None

    def test_none_when_git_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """git не установлен (OSError на exec) — тихий None, обратная связь работает."""

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("git not found")

        monkeypatch.setattr(feedback.subprocess, "run", _boom)
        assert feedback.collect_commit() is None

    def test_none_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Зависший git не держит обращение — TimeoutExpired тоже даёт None."""

        def _hang(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=5.0)

        monkeypatch.setattr(feedback.subprocess, "run", _hang)
        assert feedback.collect_commit() is None

    def test_commit_field_accepted_by_bug_and_task_forms(self) -> None:
        for kind in (feedback.FeedbackKind.BUG, feedback.FeedbackKind.TASK_PROBLEM):
            prepared = feedback.prepare_issue(kind, {"commit": "7a9d8e1 Merge pull request #741"})
            assert prepared.fields["commit"].startswith("7a9d8e1")

    def test_commit_field_rejected_by_idea_form(self) -> None:
        """У формы «Идея» такого поля нет — GitHub бы молча его проглотил."""
        with pytest.raises(ValueError, match="неизвестные поля формы idea"):
            feedback.prepare_issue(feedback.FeedbackKind.IDEA, {"commit": "7a9d8e1 subject"})


class TestScrubPaths:
    def test_home_collapsed(self) -> None:
        home = str(pathlib.Path.home())
        assert feedback.scrub_paths(f"файл {home}/proj/task.py") == "файл ~/proj/task.py"

    def test_posix_slashes_variant(self) -> None:
        home = str(pathlib.Path.home()).replace("\\", "/")
        assert "~" in feedback.scrub_paths(f"путь {home}/x")

    def test_escaped_backslashes_variant(self) -> None:
        """Путь из JSON/repr приходит с двойными слешами — тоже сворачивается."""
        home = str(pathlib.Path.home()).replace("\\", "\\\\")
        assert "~" in feedback.scrub_paths(f'{{"path": "{home}\\\\x"}}')

    def test_text_without_home_untouched(self) -> None:
        assert feedback.scrub_paths("обычный текст") == "обычный текст"


class TestPrepareIssue:
    def test_url_targets_template_and_fields(self) -> None:
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG, {"what-happened": "падает", "environment": "Python: 3.12"}
        )
        assert prepared.url.startswith(f"{feedback.REPO_URL}/issues/new?")
        query = _query(prepared.url)
        assert query["template"] == ["bug_report.yml"]
        assert query["what-happened"] == ["падает"]
        assert query["environment"] == ["Python: 3.12"]

    def test_empty_values_dropped(self) -> None:
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.IDEA, {"idea": "нужен экспорт", "problem": "   "}
        )
        assert "problem" not in prepared.fields
        assert "problem" not in _query(prepared.url)

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(ValueError, match="неизвестные поля формы idea"):
            feedback.prepare_issue(feedback.FeedbackKind.IDEA, {"logs": "трейсбек"})

    def test_secrets_redacted(self) -> None:
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG,
            {"logs": "GET /api Authorization: Bearer abcdef1234567890\naccess_token=zzzz999888"},
        )
        logs = prepared.fields["logs"]
        assert "abcdef1234567890" not in logs
        assert "zzzz999888" not in logs
        assert "***redacted***" in logs

    def test_registered_secret_redacted(self) -> None:
        from stepik_grader.core import diag_log

        diag_log.register_secret("s3cret-value-01234")
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG, {"what-happened": "упало на s3cret-value-01234"}
        )
        assert "s3cret-value-01234" not in prepared.fields["what-happened"]

    def test_home_path_scrubbed(self) -> None:
        home = str(pathlib.Path.home())
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG, {"what-happened": f"не найден {home}/tests"}
        )
        assert home not in prepared.fields["what-happened"]
        assert home not in prepared.url

    def test_long_field_truncated_and_reported(self) -> None:
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG,
            {"what-happened": "падает", "logs": "x" * (feedback.FIELD_BUDGET_CHARS + 500)},
        )
        assert "logs" in prepared.truncated
        assert len(prepared.fields["logs"]) <= feedback.FIELD_BUDGET_CHARS + len("\n…")
        assert prepared.fields["logs"].endswith("…")

    def test_url_fits_the_limit(self) -> None:
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG,
            {
                "environment": feedback.collect_environment(channel="CLI"),
                "what-happened": "кириллица " * 400,
                "steps": "шаги " * 400,
                "logs": "трейсбек " * 800,
                "extra": "ещё " * 400,
            },
        )
        assert len(prepared.url) <= feedback.MAX_URL_LENGTH
        assert prepared.truncated or prepared.dropped

    def test_key_fields_survive_pressure(self) -> None:
        """Под давлением лимита жертвуются вторичные поля, а описание и
        окружение остаются — без них обращение бессмысленно."""
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG,
            {
                "environment": feedback.collect_environment(channel="CLI"),
                "what-happened": "коротко и по делу",
                "logs": "трейсбек " * 2000,
                "extra": "приложение " * 500,
            },
        )
        assert "what-happened" in prepared.fields
        assert "environment" in prepared.fields
        assert len(prepared.url) <= feedback.MAX_URL_LENGTH

    def test_pathological_single_field_still_fits(self) -> None:
        """Одно патологически длинное КЛЮЧЕВОЕ поле (не в списке жертв) — URL
        всё равно укладывается в лимит, а не уезжает 414-м на GitHub."""
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.IDEA, {"idea": "щ" * 100_000}, field_budget=100_000
        )
        assert len(prepared.url) <= feedback.MAX_URL_LENGTH
        assert "idea" in prepared.truncated
        assert "idea" in prepared.fields  # ключевое поле не выбрасывается

    def test_dropped_field_reported(self) -> None:
        """Жертва, которую пришлось выбросить целиком, объявляется в ``dropped``."""
        prepared = feedback.prepare_issue(
            feedback.FeedbackKind.BUG,
            {"what-happened": "ы" * 2000, "logs": "z" * 2000, "extra": "e" * 2000},
            max_url_length=1200,
            field_budget=2000,
        )
        assert prepared.dropped
        assert set(prepared.dropped) <= set(feedback._SACRIFICE_ORDER)
        assert "what-happened" in prepared.fields
        assert len(prepared.url) <= 1200

    def test_nothing_is_sent(self) -> None:
        """prepare_issue только считает: ни сети, ни браузера (никаких побочек).

        Гарантия проверяется отсутствием сетевых импортов в модуле — открытие
        браузера живёт в CLI/web-слое, где его вызывает явное решение пользователя.
        """
        source = pathlib.Path(feedback.__file__).read_text(encoding="utf-8")
        assert "webbrowser" not in source
        assert "urllib.request" not in source
        assert "requests" not in source
