"""Тесты scripts/check_workflow_guardrails.py — инварианты CI и релиза (issue #988).

Смысл этих тестов — не в том, что guard зелен на текущем репозитории (это
проверяет и сам прогон в CI), а в том, что он **краснеет** на каждом дефекте,
ради которого заведён. Guard, зелёный при любом входе, ничем не лучше его
отсутствия — именно так и жил дефект REL-1-01: ошибка в workflow не видна ни
линтеру, ни тестам и проявляется один раз, в момент релиза.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_workflow_guardrails.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_workflow_guardrails", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


# Релизный job в исправном виде: checkout первым, проверка dist, twine, permissions.
_HEALTHY_RELEASE = """name: Release

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Check built artifacts
        run: twine check dist/*

  github-release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: Download dist artifact
        uses: actions/download-artifact@v8
      - name: Fail if dist is empty
        run: |
          if [ -z "$(ls -A dist 2>/dev/null)" ]; then exit 1; fi

  pypi-publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v8
"""


class TestReleasePipeline:
    """Каждая проверка обязана падать на своём дефекте."""

    def test_healthy_pipeline_passes(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_pipeline(errors, source=_HEALTHY_RELEASE)
        assert errors == []

    def test_checkout_after_download_is_caught(self) -> None:
        """Тот самый дефект: checkout стирает скачанный dist/, релиз без ассетов."""
        broken = _HEALTHY_RELEASE.replace(
            "      - uses: actions/checkout@v7\n"
            "      - name: Download dist artifact\n"
            "        uses: actions/download-artifact@v8\n",
            "      - name: Download dist artifact\n"
            "        uses: actions/download-artifact@v8\n"
            "      - uses: actions/checkout@v7\n",
        )
        errors: list[str] = []

        _MODULE.check_release_pipeline(errors, source=broken)

        assert any("ПОСЛЕ" in error for error in errors), errors

    def test_missing_empty_dist_check_is_caught(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_pipeline(
            errors, source=_HEALTHY_RELEASE.replace('if [ -z "$(ls -A dist 2>/dev/null)" ]', "true")
        )
        assert any("непуст" in error for error in errors), errors

    def test_missing_twine_check_is_caught(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_pipeline(
            errors, source=_HEALTHY_RELEASE.replace("twine check dist/*", "echo built")
        )
        assert any("twine" in error for error in errors), errors

    def test_missing_permissions_is_caught(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_pipeline(
            errors, source=_HEALTHY_RELEASE.replace("permissions:\n  contents: read\n", "")
        )
        assert any("permissions" in error for error in errors), errors

    def test_renamed_job_fails_loudly(self) -> None:
        """Переименованный job — ошибка, а не тихий пропуск проверки.

        Это и есть «пустой вход»: если бы guard молча пропускал отсутствующий
        job, любое переименование обнулило бы проверку порядка шагов, оставив её
        зелёной, — тот самый шаблон, из-за которого дефект и дожил до релиза.
        """
        errors: list[str] = []

        _MODULE.check_release_pipeline(
            errors, source=_HEALTHY_RELEASE.replace("  github-release:", "  publish-release:")
        )

        assert any("не найден" in error for error in errors), errors

    def test_empty_workflow_is_not_silently_green(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_pipeline(errors, source="")
        assert errors


class TestExtractJob:
    """Разбор job'ов не должен захватывать соседей."""

    def test_stops_at_next_job(self) -> None:
        job = _MODULE.extract_job(_HEALTHY_RELEASE, "github-release")

        assert any("download-artifact" in line for line in job)
        assert not any("pypi-publish" in line for line in job)

    def test_unknown_job_is_empty(self) -> None:
        assert _MODULE.extract_job(_HEALTHY_RELEASE, "нет-такого") == []


_HEALTHY_GATES = """name: Release

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Documentation guardrails
        run: python scripts/check_docs_guardrails.py
      - name: Release notes exist
        run: python scripts/extract_release_notes.py "${GITHUB_REF_NAME}" --out /dev/null

  github-release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
"""


class TestReleaseGatesMatchPromises:
    """Обещанное документацией стоит гейтом до ЛЮБОЙ публикации (issue #988)."""

    def test_healthy_gates_pass(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_gates_match_promises(errors, source=_HEALTHY_GATES)
        assert errors == []

    def test_missing_changelog_rotation_gate_is_caught(self) -> None:
        """versioning.md обещает «без ротации CHANGELOG релиз падает» — это должно быть правдой."""
        errors: list[str] = []

        _MODULE.check_release_gates_match_promises(
            errors, source=_HEALTHY_GATES.replace("check_docs_guardrails.py", "echo skip")
        )

        assert any("check_docs_guardrails" in error for error in errors), errors

    def test_release_notes_gate_must_be_in_verify(self) -> None:
        """Проверка в job'е публикации GitHub Release не мешает PyPI опубликоваться.

        Оба публикующих job'а зависят от `verify` и независимы друг от друга по
        построению — значит гейт обязан стоять в `verify`, иначе PyPI выйдет с
        любым состоянием CHANGELOG.
        """
        errors: list[str] = []

        _MODULE.check_release_gates_match_promises(
            errors, source=_HEALTHY_GATES.replace("extract_release_notes.py", "echo skip")
        )

        assert any("release notes" in error for error in errors), errors

    def test_renamed_verify_job_fails_loudly(self) -> None:
        errors: list[str] = []
        _MODULE.check_release_gates_match_promises(
            errors, source=_HEALTHY_GATES.replace("  verify:", "  preflight:")
        )
        assert any("не найден" in error for error in errors), errors


class TestCiTriggers:
    """PR, созданный черновиком, обязан получать проверки."""

    def test_ready_for_review_present(self) -> None:
        errors: list[str] = []
        _MODULE.check_ci_listens_to_ready_for_review(
            errors,
            source=(
                "on:\n  workflow_dispatch:\n"
                "  pull_request:\n    types: [opened, ready_for_review]\n"
            ),
        )
        assert errors == []

    def test_default_types_are_caught(self) -> None:
        """Дефолтные типы GitHub не включают ready_for_review — это и ловим."""
        errors: list[str] = []

        _MODULE.check_ci_listens_to_ready_for_review(
            errors, source="on:\n  pull_request:\n    branches: [main]\n"
        )

        assert any("ready_for_review" in error for error in errors), errors

    def test_missing_manual_dispatch_is_caught(self) -> None:
        """Без workflow_dispatch единственный способ перезапустить CI — холостой пуш."""
        errors: list[str] = []

        _MODULE.check_ci_listens_to_ready_for_review(
            errors,
            source="on:\n  pull_request:\n    types: [opened, ready_for_review]\n",
        )

        assert any("workflow_dispatch" in error for error in errors), errors

    def test_missing_pull_request_trigger_is_caught(self) -> None:
        errors: list[str] = []
        _MODULE.check_ci_listens_to_ready_for_review(errors, source="on:\n  push:\n")
        assert any("pull_request" in error for error in errors), errors


def test_repo_workflows_pass_guard() -> None:
    """Действующие workflow репозитория соответствуют инвариантам."""
    assert _MODULE.main() == 0
