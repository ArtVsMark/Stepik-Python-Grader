"""Тесты scripts/corpus_fetch.py и `stepik_client.fetch_section_units`.

Сетевой слой мокается целиком: сбор базы обращается к Stepik, а тесты обязаны
работать без сети и без чужого аккаунта. Проверяется логика обхода курса
(порядок, фильтр по типу шага, построение URL) и устойчивость сбора к сбою
отдельного шага — на базе из сотен задач один недоступный шаг не должен ронять
весь прогон.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

import stepik_grader
from stepik_grader.core.stepik_client import fetch_section_units

_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS_DIR / "corpus_fetch.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_corpus_fetch", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # См. test_corpus_mutations: dataclass резолвит аннотации через sys.modules.
    sys.modules[spec.name] = module
    added = str(_SCRIPTS_DIR) not in sys.path
    if added:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        if added:
            sys.path.remove(str(_SCRIPTS_DIR))
    return module


_MODULE = _load_module()


class TestFetchSectionUnits:
    """`fetch_section_units` — связка «секция → уроки» для обхода курса."""

    def test_units_are_taken_by_ids_not_by_section_filter(self) -> None:
        """Живой Stepik на ``?section=`` не отвечает — юниты берутся по ``ids[]``.

        Тест закрепляет форму запроса, а не результат: на моках оба варианта
        зелёные, и ровно поэтому дефект дожил до живого прогона.
        """
        session = MagicMock()
        with (
            patch(
                "stepik_grader.core.stepik_client.fetch_section_data",
                return_value={"id": 5, "units": [1, 2]},
            ),
            patch(
                "stepik_grader.core.stepik_client._cached_api_get",
                return_value={"units": [{"id": 1, "lesson": 10}, {"id": 2, "lesson": 11}]},
            ) as mock_get,
        ):
            units = fetch_section_units(session, 5)

        assert [u["lesson"] for u in units] == [10, 11]
        assert mock_get.call_args.kwargs["params"] == {"ids[]": [1, 2]}
        assert "section" not in mock_get.call_args.kwargs["params"]

    def test_large_section_is_requested_page_by_page(self) -> None:
        """Секция длиннее страницы API запрашивается частями, а не теряется молча."""
        unit_ids = list(range(1, 46))
        session = MagicMock()

        def _page(_session: object, _url: str, params: dict[str, object]) -> dict[str, object]:
            ids = params["ids[]"]
            assert isinstance(ids, list)
            return {"units": [{"id": uid, "lesson": 100 + uid} for uid in ids]}

        with (
            patch(
                "stepik_grader.core.stepik_client.fetch_section_data",
                return_value={"id": 5, "units": unit_ids},
            ),
            patch(
                "stepik_grader.core.stepik_client._cached_api_get",
                side_effect=_page,
            ) as mock_get,
        ):
            units = fetch_section_units(session, 5)

        assert [len(call.kwargs["params"]["ids[]"]) for call in mock_get.call_args_list] == [
            20,
            20,
            5,
        ]
        assert [u["id"] for u in units] == unit_ids

    def test_order_follows_the_section_not_the_reply(self) -> None:
        """Порядок юнитов задаёт секция: по нему нумеруются задачи при обходе."""
        with (
            patch(
                "stepik_grader.core.stepik_client.fetch_section_data",
                return_value={"id": 5, "units": [7, 3, 9]},
            ),
            patch(
                "stepik_grader.core.stepik_client._cached_api_get",
                return_value={"units": [{"id": 9}, {"id": 7}, {"id": 3}]},
            ),
        ):
            units = fetch_section_units(MagicMock(), 5)

        assert [u["id"] for u in units] == [7, 3, 9]

    def test_empty_section_is_not_an_error(self) -> None:
        """Секция без юнитов (черновик, скрытый модуль) — пустой список, не исключение."""
        with (
            patch(
                "stepik_grader.core.stepik_client.fetch_section_data",
                return_value={"id": 5, "units": []},
            ),
            patch("stepik_grader.core.stepik_client._cached_api_get") as mock_get,
        ):
            assert fetch_section_units(MagicMock(), 5) == []

        mock_get.assert_not_called()


class TestCourseStep:
    def test_url_is_canonical_step_link(self) -> None:
        step = _MODULE.CourseStep(
            lesson_id=571244,
            position=3,
            step_id=99,
            title="Урок",
            section_id=1,
            section_title="Section 1",
            section_position=1,
        )

        assert step.url == "https://stepik.org/lesson/571244/step/3"


class TestIterCourseSteps:
    """Обход курса: порядок программы и фильтр по типу шага."""

    @staticmethod
    def _patch_api(
        *,
        sections: list[int],
        units: dict[int, list[dict[str, object]]],
        lessons: dict[int, dict[str, object]],
        code_steps: set[tuple[int, int]],
    ) -> list[object]:
        """Замокать цепочку обхода; ``code_steps`` — пары «урок, позиция» с кодом."""
        return [
            patch.object(_MODULE, "fetch_course_data", return_value={"sections": sections}),
            patch.object(
                _MODULE,
                "fetch_section_data",
                side_effect=lambda _s, sid: {
                    "id": sid,
                    "title": f"Section {sid}",
                    "position": sid,
                },
            ),
            patch.object(_MODULE, "fetch_section_units", side_effect=lambda _s, sid: units[sid]),
            patch.object(_MODULE, "fetch_lesson_data", side_effect=lambda _s, lid: lessons[lid]),
            patch.object(
                _MODULE,
                "fetch_step_data",
                side_effect=lambda _s, lid, pos: {
                    "block": {"name": "code" if (lid, pos) in code_steps else "text"}
                },
            ),
        ]

    def test_walks_course_in_program_order(self) -> None:
        patches = self._patch_api(
            sections=[1, 2],
            units={1: [{"id": 11, "lesson": 100}], 2: [{"id": 22, "lesson": 200}]},
            lessons={
                100: {"title": "Первый урок", "steps": [1001, 1002]},
                200: {"title": "Второй урок", "steps": [2001]},
            },
            code_steps={(100, 1), (100, 2), (200, 1)},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            steps = list(_MODULE.iter_course_steps(MagicMock(), 63085))

        assert [s.url for s in steps] == [
            "https://stepik.org/lesson/100/step/1",
            "https://stepik.org/lesson/100/step/2",
            "https://stepik.org/lesson/200/step/1",
        ]
        assert steps[0].title == "Первый урок"

    def test_code_only_filters_out_text_steps(self) -> None:
        """По умолчанию берём только задачи с кодом — у текста нет ни тестов, ни решения."""
        patches = self._patch_api(
            sections=[1],
            units={1: [{"id": 11, "lesson": 100}]},
            lessons={100: {"title": "Урок", "steps": [1001, 1002, 1003]}},
            code_steps={(100, 2)},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            steps = list(_MODULE.iter_course_steps(MagicMock(), 63085))

        assert [s.position for s in steps] == [2]

    def test_all_steps_mode_keeps_everything(self) -> None:
        patches = self._patch_api(
            sections=[1],
            units={1: [{"id": 11, "lesson": 100}]},
            lessons={100: {"title": "Урок", "steps": [1001, 1002]}},
            code_steps=set(),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            steps = list(_MODULE.iter_course_steps(MagicMock(), 63085, code_only=False))

        assert len(steps) == 2

    def test_unit_without_lesson_is_skipped(self) -> None:
        patches = self._patch_api(
            sections=[1],
            units={1: [{"id": 11}, {"id": 12, "lesson": 100}]},
            lessons={100: {"title": "Урок", "steps": [1001]}},
            code_steps={(100, 1)},
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            steps = list(_MODULE.iter_course_steps(MagicMock(), 63085))

        assert [s.lesson_id for s in steps] == [100]


class TestReadStepUrls:
    def test_skips_blanks_and_comments(self, tmp_path: pathlib.Path) -> None:
        list_path = tmp_path / "steps.txt"
        list_path.write_text(
            "https://stepik.org/lesson/1/step/1\n"
            "\n"
            "# выключенный шаг\n"
            "  https://stepik.org/lesson/2/step/3  \n",
            encoding="utf-8",
        )

        assert _MODULE.read_step_urls(list_path) == [
            "https://stepik.org/lesson/1/step/1",
            "https://stepik.org/lesson/2/step/3",
        ]


class TestFetchSteps:
    """Сбор базы: один сбойный шаг не должен ронять сотни остальных.

    Загрузчик подменяется атрибутом пакета, а не записью в ``sys.modules``:
    ``fetch_steps`` берёт его через ``from stepik_grader import downloader``, и
    у уже импортированного пакета такой импорт читает атрибут, минуя
    ``sys.modules``. Подмена словаря работала бы, только пока никакой другой
    тест не успел импортировать модуль раньше.
    """

    @staticmethod
    def _patch_downloader(mock: MagicMock) -> object:
        return patch.object(stepik_grader, "downloader", mock, create=True)

    def test_downloads_each_step(self, tmp_path: pathlib.Path) -> None:
        downloader = MagicMock()
        with self._patch_downloader(downloader):
            downloaded, errors = _MODULE.fetch_steps(
                MagicMock(), ["url-1", "url-2"], tmp_path / "base"
            )

        assert (downloaded, errors) == (2, [])
        assert downloader.process_step_url.call_count == 2
        assert (tmp_path / "base").is_dir()

    def test_failed_step_is_collected_not_raised(self, tmp_path: pathlib.Path) -> None:
        downloader = MagicMock()
        downloader.process_step_url.side_effect = [ValueError("шаг не найден"), None]
        with self._patch_downloader(downloader):
            downloaded, errors = _MODULE.fetch_steps(
                MagicMock(), ["url-bad", "url-ok"], tmp_path / "base"
            )

        assert downloaded == 1
        assert len(errors) == 1
        assert "url-bad" in errors[0]
        assert "шаг не найден" in errors[0]


class TestMain:
    @staticmethod
    def _patch_auth() -> object:
        return patch.multiple(
            _MODULE,
            load_secrets_dict=MagicMock(return_value={"access_token": "t"}),
            create_user_session=MagicMock(return_value=MagicMock()),
        )

    def test_dry_run_lists_steps_without_downloading(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        step = _MODULE.CourseStep(
            lesson_id=100,
            position=1,
            step_id=1001,
            title="????",
            section_id=1,
            section_title="Section 1",
            section_position=1,
        )
        with (
            self._patch_auth(),
            patch.object(_MODULE, "iter_course_steps", return_value=iter([step])),
            patch.object(_MODULE, "fetch_steps") as mock_fetch,
        ):
            code = _MODULE.main(
                ["--course", "63085", "--target", str(tmp_path), "--dry-run", "--secrets", "s.json"]
            )

        assert code == 0
        mock_fetch.assert_not_called()
        assert "https://stepik.org/lesson/100/step/1" in capsys.readouterr().out

    def test_missing_secrets_reports_instead_of_crashing(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.object(_MODULE, "load_secrets_dict", side_effect=FileNotFoundError("нет файла")):
            code = _MODULE.main(["--course", "1", "--secrets", str(tmp_path / "нет.json")])

        assert code == 1
        assert "Не удалось авторизоваться" in capsys.readouterr().err

    def test_empty_step_list_is_reported(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            self._patch_auth(),
            patch.object(_MODULE, "iter_course_steps", return_value=iter([])),
        ):
            code = _MODULE.main(["--course", "63085", "--target", str(tmp_path)])

        assert code == 1
        assert "Нечего скачивать" in capsys.readouterr().err

    def test_limit_caps_step_count(self, tmp_path: pathlib.Path) -> None:
        steps = [
            _MODULE.CourseStep(
                lesson_id=100,
                position=i,
                step_id=1000 + i,
                title="Урок",
                section_id=1,
                section_title="Section 1",
                section_position=1,
            )
            for i in range(1, 6)
        ]
        with (
            self._patch_auth(),
            patch.object(_MODULE, "iter_course_steps", return_value=iter(steps)),
            patch.object(_MODULE, "fetch_steps", return_value=(2, [])) as mock_fetch,
        ):
            code = _MODULE.main(["--course", "63085", "--target", str(tmp_path), "--limit", "2"])

        assert code == 0
        assert len(mock_fetch.call_args.args[1]) == 2

    def test_errors_make_exit_nonzero(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        step = _MODULE.CourseStep(
            lesson_id=100,
            position=1,
            step_id=1001,
            title="Урок",
            section_id=1,
            section_title="Section 1",
            section_position=1,
        )
        with (
            self._patch_auth(),
            patch.object(_MODULE, "iter_course_steps", return_value=iter([step])),
            patch.object(_MODULE, "fetch_steps", return_value=(0, ["url: сбой"])),
        ):
            code = _MODULE.main(["--course", "63085", "--target", str(tmp_path)])

        assert code == 1
        assert "Ошибок: 1" in capsys.readouterr().err

    def test_inventory_json_has_expected_hierarchy(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        steps = [
            _MODULE.CourseStep(
                lesson_id=200,
                position=1,
                step_id=2001,
                title="Lesson 2",
                section_id=2,
                section_title="Section 2",
                section_position=2,
            ),
            _MODULE.CourseStep(
                lesson_id=100,
                position=1,
                step_id=1001,
                title="Lesson 1",
                section_id=1,
                section_title="Section 1",
                section_position=1,
            ),
        ]

        with (
            self._patch_auth(),
            patch.object(
                _MODULE,
                "iter_course_steps",
                return_value=iter(steps),
            ),
        ):
            code = _MODULE.main(["--course", "63085", "--inventory", "json"])

        assert code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["course"]["id"] == 63085
        assert payload["course"]["sections"][0]["id"] == 1
        assert payload["course"]["sections"][1]["id"] == 2

        step = payload["course"]["sections"][0]["lessons"][0]["steps"][0]
        assert step == {
            "step_id": 1001,
            "position": 1,
            "url": "https://stepik.org/lesson/100/step/1",
        }

    def test_dry_run_reports_per_section_totals(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        steps = [
            _MODULE.CourseStep(
                lesson_id=100,
                position=1,
                step_id=1001,
                title="Lesson 1",
                section_id=1,
                section_title="Section 1",
                section_position=1,
            ),
            _MODULE.CourseStep(
                lesson_id=100,
                position=2,
                step_id=1002,
                title="Lesson 1",
                section_id=1,
                section_title="Section 1",
                section_position=1,
            ),
            _MODULE.CourseStep(
                lesson_id=200,
                position=1,
                step_id=2001,
                title="Lesson 2",
                section_id=2,
                section_title="Section 2",
                section_position=2,
            ),
        ]

        with (
            self._patch_auth(),
            patch.object(
                _MODULE,
                "iter_course_steps",
                return_value=iter(steps),
            ),
            patch.object(_MODULE, "fetch_steps") as mock_fetch,
        ):
            code = _MODULE.main(
                [
                    "--course",
                    "63085",
                    "--target",
                    str(tmp_path),
                    "--dry-run",
                ]
            )

        assert code == 0
        mock_fetch.assert_not_called()

        output = capsys.readouterr().err
        assert "Section 1 \u2014 2" in output
        assert "Section 2 \u2014 1" in output

    def test_inventory_rejects_limit(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with self._patch_auth():
            with pytest.raises(SystemExit):
                _MODULE.main(
                    [
                        "--course",
                        "63085",
                        "--inventory",
                        "json",
                        "--limit",
                        "2",
                    ]
                )
