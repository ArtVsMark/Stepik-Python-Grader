"""Тесты scripts/generate_release_badge.py — бейдж «релиз и PyPI сошлись» (issue #1063).

Скрипт лежит в scripts/ (не на sys.path) — грузим по пути, тем же приёмом, что
test_generate_glossary_badge.py и соседи.

Главное здесь — что бейдж **краснеет на расхождении**. Бейдж, зеленеющий всегда,
бесполезен ровно так же, как два одинаковых бейджа, которые он заменил: смысл
пары «тег ↔ PyPI» появляется только в момент, когда публикация отстала.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "generate_release_badge.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_release_badge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


class TestShortVersion:
    """Всегда нулевой PATCH из подписи убирается, всё остальное — нет."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("v1.10.0", "1.10"),
            ("1.10.0", "1.10"),
            ("v2.0.0", "2.0"),
        ],
    )
    def test_release_tag_loses_trailing_zero(self, raw: str, expected: str) -> None:
        assert _MODULE.short_version(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["1.10.0.post3", "1.11.0rc1", "1.10.2", "1.10", "какая-то-строка"],
    )
    def test_non_release_versions_are_left_intact(self, raw: str) -> None:
        """Обрезать можно только то, что точно соответствует схеме тегов.

        Иначе бейдж соврал бы там, где это опаснее всего: `1.10.0.post3` на PyPI
        — это НЕ релиз `1.10`, и показать их одинаково значит спрятать проблему.
        """
        assert _MODULE.short_version(raw) == raw.lstrip("v")


class TestBadgePayload:
    """Три состояния: сошлись, разошлись, проверить не удалось."""

    def test_match_is_green_and_short(self) -> None:
        payload = _MODULE.build_badge_payload("v1.10.0", "1.10.0")

        assert payload["message"] == "1.10"
        assert payload["color"] == "brightgreen"
        assert payload["label"] == "release/pypi"

    def test_mismatch_is_red_and_names_both_sides(self) -> None:
        """Сломанная публикация видна из шапки README, а не при сверке глазами."""
        payload = _MODULE.build_badge_payload("v1.11.0", "1.10.0")

        assert payload["color"] == "red"
        assert "1.11" in str(payload["message"])
        assert "1.10" in str(payload["message"])

    def test_mismatch_message_is_language_neutral(self) -> None:
        """Бейдж стоит и в русском README, и в английской витрине — слов в нём нет."""
        message = str(_MODULE.build_badge_payload("v1.11.0", "1.10.0")["message"])

        assert message == "1.11 ≠ pypi 1.10"

    def test_unreachable_pypi_does_not_go_green(self) -> None:
        """Нехватка данных — не повод зеленеть: проверка не выполнена, так и сказано."""
        payload = _MODULE.build_badge_payload("v1.10.0", None)

        assert payload["color"] != "brightgreen"
        assert "1.10" in str(payload["message"])
        assert "?" in str(payload["message"])

    def test_clone_without_tags_says_so(self) -> None:
        """Клон без тегов (облачная сессия, checkout без fetch-depth) — не расхождение."""
        payload = _MODULE.build_badge_payload(None, "1.10.0")

        assert payload["color"] != "red"
        assert "1.10" in str(payload["message"])

    def test_nothing_known_at_all(self) -> None:
        payload = _MODULE.build_badge_payload(None, None)

        assert payload["color"] == "lightgrey"
        assert payload["schemaVersion"] == 1

    def test_payload_is_valid_shields_endpoint(self) -> None:
        payload = _MODULE.build_badge_payload("v1.10.0", "1.10.0")

        assert set(payload) == {"schemaVersion", "label", "message", "color"}
        assert json.loads(json.dumps(payload, ensure_ascii=False))["schemaVersion"] == 1


class TestFetchPypiVersion:
    """Сеть замокана: реальных запросов в тестах нет."""

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> TestFetchPypiVersion._FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def test_reads_version_from_info(self) -> None:
        body = json.dumps({"info": {"version": "1.10.0"}}).encode("utf-8")
        with patch.object(_MODULE.urllib.request, "urlopen", return_value=self._FakeResponse(body)):
            assert _MODULE.fetch_pypi_version() == "1.10.0"

    def test_network_error_is_none_not_crash(self) -> None:
        """Недоступный индекс не должен ронять обновление бейджей в CI."""
        with patch.object(
            _MODULE.urllib.request, "urlopen", side_effect=OSError("сеть недоступна")
        ):
            assert _MODULE.fetch_pypi_version() is None

    def test_unexpected_shape_is_none(self) -> None:
        """Сменившаяся форма ответа — тоже «не проверено», а не трейсбек."""
        body = json.dumps({"неожиданно": "другое"}).encode("utf-8")
        with patch.object(_MODULE.urllib.request, "urlopen", return_value=self._FakeResponse(body)):
            assert _MODULE.fetch_pypi_version() is None

    def test_broken_json_is_none(self) -> None:
        with patch.object(
            _MODULE.urllib.request,
            "urlopen",
            return_value=self._FakeResponse("{не json".encode()),
        ):
            assert _MODULE.fetch_pypi_version() is None


class TestLatestReleaseTag:
    """Тег читается из git, отсутствие тегов — не ошибка."""

    def test_reads_tag_from_git(self) -> None:
        with patch.object(subprocess, "check_output", return_value="v1.10.0\n"):
            assert _MODULE.latest_release_tag() == "v1.10.0"

    def test_repo_without_tags_is_none(self) -> None:
        with patch.object(
            subprocess,
            "check_output",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            assert _MODULE.latest_release_tag() is None

    def test_missing_git_is_none(self) -> None:
        with patch.object(subprocess, "check_output", side_effect=OSError("нет git")):
            assert _MODULE.latest_release_tag() is None


class TestMain:
    """CLI пишет файл в формате, который читает shields.io."""

    def test_writes_badge_file(self, tmp_path: pathlib.Path, capsys) -> None:
        out = tmp_path / "release.json"
        with (
            patch.object(_MODULE, "latest_release_tag", return_value="v1.10.0"),
            patch.object(_MODULE, "fetch_pypi_version", return_value="1.10.0"),
        ):
            _MODULE.main(["--out", str(out)])

        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["message"] == "1.10"
        assert "release.json" in capsys.readouterr().out

    def test_offline_flag_skips_network(self, tmp_path: pathlib.Path) -> None:
        """`--offline` — для окружения без сети: бейдж честно говорит «не проверено»."""
        out = tmp_path / "release.json"
        with (
            patch.object(_MODULE, "latest_release_tag", return_value="v1.10.0"),
            patch.object(_MODULE, "fetch_pypi_version") as mock_fetch,
        ):
            _MODULE.main(["--out", str(out), "--offline"])

        mock_fetch.assert_not_called()
        assert "?" in json.loads(out.read_text(encoding="utf-8"))["message"]
