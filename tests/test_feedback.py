"""Tests for core/feedback.py — канал обратной связи (issue #753, эпик #751).

Проверяется то, что делает канал безопасным и рабочим: контракт `id` полей с
YAML-формами, редакция секретов, сворачивание домашнего пути, отсутствие имени
машины в окружении и укладывание URL в лимит длины без молчаливой потери данных.
"""

from __future__ import annotations

import os
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


# Домашний каталог Windows-пользователя с длинным именем: у такого есть 8.3-дубль
# (`IVANPE~1`), в котором имя усечено, но узнаваемо.
_FAKE_HOME = "C:\\Users\\ivan.petrov"
_FAKE_SHORT_HOME = "C:\\Users\\IVANPE~1"
# Что не должно уехать на GitHub ни в каком написании: полное имя пользователя и
# его 8.3-огрызок.
_LEAK_MARKERS = ("ivan.petrov", "ivanpe~1")

# Написания, в которых домашний каталог попадает в свободный текст поля «Логи»:
# регистр буквы диска и имён нормализуется по-разному разными инструментами,
# слеши приходят и прямыми, и экранированными (JSON/repr), а старые .bat/cmd и
# java-обёртки печатают 8.3-имя.
_HOME_WRITINGS: dict[str, str] = {
    "native": f"{_FAKE_HOME}\\tasks\\a.py",
    "lower": "c:\\users\\ivan.petrov\\tasks\\a.py",
    "upper": "C:\\USERS\\IVAN.PETROV\\TASKS\\A.PY",
    "mixed-case-drive": "c:\\Users\\Ivan.Petrov\\tasks\\a.py",
    "posix-slashes": "C:/Users/ivan.petrov/tasks/a.py",
    "posix-slashes-lower": "c:/users/ivan.petrov/tasks/a.py",
    "escaped": '{"path": "C:\\\\Users\\\\ivan.petrov\\\\tasks\\\\a.py"}',
    "escaped-lower": '{"path": "c:\\\\users\\\\ivan.petrov\\\\tasks\\\\a.py"}',
    "short-8.3": f"{_FAKE_SHORT_HOME}\\tasks\\a.py",
    "short-8.3-lower": "c:\\users\\ivanpe~1\\tasks\\a.py",
    "short-8.3-posix": "C:/Users/IVANPE~1/tasks/a.py",
}
_WRITING_IDS = list(_HOME_WRITINGS)
_WRITINGS = [_HOME_WRITINGS[key] for key in _WRITING_IDS]


def _leaks_username(text: str) -> bool:
    """Осталось ли в тексте узнаваемое имя пользователя — в сыром или в percent-виде.

    URL проверяется и после `unquote_plus`: имя из латиницы percent-encoding не
    трогает, но проверка «только сырой строки» молча ослабла бы на кириллическом
    имени пользователя.
    """
    haystacks = (text.lower(), urllib.parse.unquote_plus(text).lower())
    return any(marker in haystack for haystack in haystacks for marker in _LEAK_MARKERS)


@pytest.fixture
def windows_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows-домашний каталог с 8.3-дублём — воспроизводимо на любой ОС прогона.

    Реальный `Path.home()` для этого не годится: 8.3-имя существует только на
    Windows и только у длинных имён, а регистронезависимость — свойство
    платформы. Утечка же должна ловиться на всех трёх ОС матрицы CI, поэтому
    подменяются и home, и оба платформенных ответа (`_case_insensitive_paths`,
    `_short_path`).
    """
    monkeypatch.setattr(feedback.Path, "home", staticmethod(lambda: pathlib.Path(_FAKE_HOME)))
    monkeypatch.setattr(feedback, "_case_insensitive_paths", lambda: True)
    monkeypatch.setattr(feedback, "_short_path", lambda _path: _FAKE_SHORT_HOME)


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

    def test_foreign_repository_is_not_collected(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Чужой git молчит: его заголовок ушёл бы в ПУБЛИЧНЫЙ issue (issue #964).

        Реальный прогон из рабочего репозитория возвращал
        «секретный проект заказчика: …» — форма обращения ведёт на GitHub, и
        пользователь опубликовал бы тайну своего работодателя, ничего не набирая.
        """
        alien = tmp_path / "alien"
        alien.mkdir()
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "T"],
            ["commit", "-q", "--allow-empty", "-m", "секретный проект заказчика"],
        ):
            subprocess.run(["git", *args], cwd=alien, check=True, capture_output=True)

        assert feedback.collect_commit(cwd=alien) is None

    def test_marker_decides_not_the_remote(self, tmp_path: pathlib.Path) -> None:
        """Признак «наш клон» — файл пакета, а не remote: форк и зеркало законны."""
        clone = tmp_path / "fork"
        (clone / "src" / "stepik_grader").mkdir(parents=True)
        (clone / "src" / "stepik_grader" / "__init__.py").write_text("", encoding="utf-8")
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "T"],
            ["add", "-A"],
            ["commit", "-q", "-m", "форк грейдера"],
        ):
            subprocess.run(["git", *args], cwd=clone, check=True, capture_output=True)

        commit = feedback.collect_commit(cwd=clone)

        assert commit is not None and "форк грейдера" in commit

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


class TestScrubPathsWritings:
    """Имя пользователя не должно уехать в публичный issue ни в одном написании.

    Написание пути — не под контролем грейдера: регистр буквы диска нормализуют
    по-разному разные инструменты, а «Логи» в форме обратной связи — свободный
    текст, куда вставляют вывод посторонних программ (эпик #751).
    """

    @pytest.mark.parametrize("writing", _WRITINGS, ids=_WRITING_IDS)
    def test_collapsed_in_text(self, windows_home: None, writing: str) -> None:
        scrubbed = feedback.scrub_paths(writing)
        assert "~" in scrubbed
        assert not _leaks_username(scrubbed), scrubbed

    @pytest.mark.parametrize("writing", _WRITINGS, ids=_WRITING_IDS)
    def test_username_absent_from_prefilled_url(self, windows_home: None, writing: str) -> None:
        """Главная проверка приватности: имени нет в URL, который откроет браузер.

        Проверяется именно итоговый URL, а не работа `str.replace`: между
        `scrub_paths` и ссылкой лежат редакция секретов, усечение и
        percent-encoding, и утечка может пережить любой из этих шагов.
        """
        prepared = feedback.prepare_issue(feedback.FeedbackKind.BUG, {"logs": writing})
        assert "logs" in prepared.fields, "поле выброшено — проверять стало нечего"
        assert not _leaks_username(prepared.fields["logs"]), prepared.fields["logs"]
        assert not _leaks_username(prepared.url), prepared.url

    def test_username_absent_when_pasted_among_other_output(self, windows_home: None) -> None:
        """Реалистичный случай: вставленный вывод чужой программы, все написания разом."""
        logs = "\n".join(
            [
                "Traceback (most recent call last):",
                *(f'  File "{writing}", line 1' for writing in _WRITINGS),
                "PermissionError: [Errno 13] Permission denied",
            ]
        )
        prepared = feedback.prepare_issue(feedback.FeedbackKind.BUG, {"logs": logs})
        assert not _leaks_username(prepared.url), prepared.url
        # Свернулось, но текст остался читаемым — обращение не должно превращаться
        # в набор тильд без диагностической ценности.
        assert "PermissionError" in prepared.fields["logs"]

    def test_case_sensitive_platform_keeps_other_case(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """На POSIX регистр значим: `/home/Ivan` и `/home/ivan` — разные каталоги.

        Сворачивать чужой путь по своему home там нельзя — это уже не защита
        приватности, а искажение присланного пути.
        """
        monkeypatch.setattr(feedback.Path, "home", staticmethod(lambda: pathlib.Path("/home/ivan")))
        monkeypatch.setattr(feedback, "_case_insensitive_paths", lambda: False)
        monkeypatch.setattr(feedback, "_short_path", lambda _path: None)
        assert feedback.scrub_paths("/home/ivan/tasks/a.py") == "~/tasks/a.py"
        assert feedback.scrub_paths("/home/Ivan/tasks/a.py") == "/home/Ivan/tasks/a.py"

    def test_variants_cover_both_names_without_duplicates(self, windows_home: None) -> None:
        """Три написания у длинного имени и три у 8.3-дубля, без повторов."""
        variants = feedback._home_variants(_FAKE_HOME)
        assert set(variants) == {
            _FAKE_HOME,
            "C:/Users/ivan.petrov",
            "C:\\\\Users\\\\ivan.petrov",
            _FAKE_SHORT_HOME,
            "C:/Users/IVANPE~1",
            "C:\\\\Users\\\\IVANPE~1",
        }
        assert len(variants) == len(set(variants))

    def test_no_duplicate_variants_when_short_name_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """8.3 отключены в системе — GetShortPathNameW отдаёт длинный путь; дубля нет."""
        monkeypatch.setattr(feedback, "_case_insensitive_paths", lambda: True)
        monkeypatch.setattr(feedback, "_short_path", lambda path: path.upper())
        variants = feedback._home_variants(_FAKE_HOME)
        assert len(variants) == 3

    def test_regex_metacharacters_in_home_are_literal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Путь идёт в регулярку — спецсимволы в имени пользователя обязаны экранироваться."""
        home = "C:\\Users\\i.v+a(n)"
        monkeypatch.setattr(feedback.Path, "home", staticmethod(lambda: pathlib.Path(home)))
        monkeypatch.setattr(feedback, "_case_insensitive_paths", lambda: True)
        monkeypatch.setattr(feedback, "_short_path", lambda _path: None)
        assert feedback.scrub_paths(f"{home}\\a.py") == "~\\a.py"
        # Метасимволы не превратились в шаблон: похожий, но другой путь не трогаем.
        assert feedback.scrub_paths("C:\\Users\\iXvXaXn\\a.py") == "C:\\Users\\iXvXaXn\\a.py"


class TestShortPath:
    """8.3-имя спрашивается у ОС: номер в `~N` зависит от коллизий и не угадывается."""

    @pytest.mark.skipif(os.name == "nt", reason="проверяется поведение вне Windows")
    def test_none_outside_windows(self) -> None:
        assert feedback._short_path(str(pathlib.Path.home())) is None

    @pytest.mark.skipif(os.name != "nt", reason="GetShortPathNameW — только Windows")
    def test_points_to_the_same_directory(self) -> None:
        """Живой вызов ctypes: под моком опечатка в имени функции осталась бы незаметной."""
        home = pathlib.Path.home()
        short = feedback._short_path(str(home))
        if short is None:
            pytest.skip("8.3-имена отключены в системе (NtfsDisable8dot3NameCreation)")
        assert pathlib.Path(short).resolve() == home.resolve()

    @pytest.mark.skipif(os.name != "nt", reason="GetShortPathNameW — только Windows")
    def test_none_for_missing_directory(self, tmp_path: pathlib.Path) -> None:
        """Каталога нет — тихий None, а не исключение из ctypes посреди сбора обращения."""
        assert feedback._short_path(str(tmp_path / "нет-такого-каталога")) is None

    @pytest.mark.skipif(os.name != "nt", reason="ветка достижима только под Windows")
    def test_none_when_winapi_call_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сбой WinAPI не должен ронять обращение — сворачивание просто теряет 8.3."""
        import ctypes

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("kernel32 unavailable")

        monkeypatch.setattr(ctypes, "create_unicode_buffer", _boom)
        assert feedback._short_path(str(pathlib.Path.home())) is None
        # И весь тракт остаётся рабочим: домашний путь по-прежнему сворачивается.
        home = str(pathlib.Path.home())
        assert feedback.scrub_paths(f"{home}\\a.py") == "~\\a.py"


class TestCaseInsensitivePaths:
    def test_matches_platform(self) -> None:
        """Критерий берётся у stdlib (`os.path.normcase`), своего списка ОС здесь нет."""
        assert feedback._case_insensitive_paths() is (os.name == "nt")


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
