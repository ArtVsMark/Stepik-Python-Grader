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


class TestCoverageGateIsExplicit:
    """Порог покрытия задан флагом в шаге прогона — и не исчезает молча (issue #954)."""

    def test_missing_flag_is_caught(self) -> None:
        """Без --cov-fail-under гейта покрытия нет вовсе."""
        errors: list[str] = []

        _MODULE.check_coverage_gate_is_explicit(
            errors, source="jobs:\n  test:\n    steps:\n      - run: pytest -q\n"
        )

        assert any("--cov-fail-under" in error for error in errors), errors

    def test_lowered_threshold_is_caught(self) -> None:
        """Тихое снижение порога — тот же «зелёный независимо от кода»."""
        errors: list[str] = []

        _MODULE.check_coverage_gate_is_explicit(
            errors, source="      - run: pytest -q --cov-fail-under=10\n"
        )

        assert any("ниже принятого порога" in error for error in errors), errors

    def test_explicit_threshold_passes(self) -> None:
        """Заявленный порог проходит проверку."""
        errors: list[str] = []

        _MODULE.check_coverage_gate_is_explicit(
            errors, source="      - run: pytest -q --cov-fail-under=85\n"
        )

        assert errors == []

    def test_real_ci_declares_the_gate(self) -> None:
        """Guard-the-guard: настоящий ci.yml проходит собственную проверку."""
        errors: list[str] = []

        _MODULE.check_coverage_gate_is_explicit(errors)

        assert errors == []


class TestReleasePublishesVerifiedAssets:
    """Пропажа ассетов и содержимое колеса под гейтом (issue #953)."""

    def test_missing_fail_on_unmatched_is_caught(self) -> None:
        """Без флага пустой glob публикует релиз без файлов и молчит."""
        errors: list[str] = []

        _MODULE.check_release_publishes_verified_assets(
            errors,
            source="      - uses: softprops/action-gh-release\n        with:\n"
            "          files: dist/*\n"
            "      - run: python scripts/check_wheel_contents.py dist/*.whl\n",
        )

        assert any("fail_on_unmatched_files" in error for error in errors), errors

    def test_missing_wheel_check_is_caught(self) -> None:
        """Без проверки содержимого сломанный package-data уезжает на PyPI."""
        errors: list[str] = []

        _MODULE.check_release_publishes_verified_assets(
            errors,
            source="      - uses: softprops/action-gh-release\n        with:\n"
            "          fail_on_unmatched_files: true\n",
        )

        assert any("check_wheel_contents" in error for error in errors), errors

    def test_healthy_release_passes(self) -> None:
        """Обе защиты на месте — претензий нет."""
        errors: list[str] = []

        _MODULE.check_release_publishes_verified_assets(
            errors,
            source="      - run: python scripts/check_wheel_contents.py dist/*.whl\n"
            "      - uses: softprops/action-gh-release\n        with:\n"
            "          fail_on_unmatched_files: true\n",
        )

        assert errors == []

    def test_real_release_workflow_is_guarded(self) -> None:
        """Guard-the-guard: настоящий release.yml проходит собственную проверку."""
        errors: list[str] = []

        _MODULE.check_release_publishes_verified_assets(errors)

        assert errors == []


class TestJobTimeouts:
    """У каждого job'а свой предел (issue #1271).

    Без `timeout-minutes` действует умолчание GitHub — шесть часов. Замер 19.08:
    `e2e` встал на установке Playwright и три с половиной часа держал прогон
    `main`, а с ним всю очередь мержа; красных проверок при этом не было ни
    одной — гейт очереди читает «идёт прогон» как нормальную работу.
    """

    _WITH = "jobs:\n  build:\n    timeout-minutes: 20\n    runs-on: ubuntu-latest\n"
    _WITHOUT = "jobs:\n  build:\n    runs-on: ubuntu-latest\n"

    def test_job_without_timeout_is_named(self) -> None:
        assert _MODULE.jobs_without_timeout(self._WITHOUT) == ["build"]

    def test_job_with_timeout_passes(self) -> None:
        assert _MODULE.jobs_without_timeout(self._WITH) == []

    def test_trigger_keys_are_not_jobs(self) -> None:
        """`push` и `schedule` стоят на том же отступе — таймаут у них не требуется.

        Первая редакция проверки требовала: гейт краснел на триггерах, то есть
        на том, у чего таймаута не бывает вовсе.
        """
        source = (
            "on:\n  push:\n    branches: [main]\n  schedule:\n"
            "    - cron: '0 0 * * *'\n\n" + self._WITH
        )

        assert _MODULE.jobs_without_timeout(source) == []

    def test_keys_after_jobs_section_are_ignored(self) -> None:
        """Разбор кончается на первом ключе верхнего уровня после `jobs:`."""
        source = self._WITH + "\nconcurrency:\n  group: x\n"

        assert _MODULE.jobs_without_timeout(source) == []

    def test_missing_jobs_section_is_not_a_crash(self) -> None:
        assert _MODULE.jobs_without_timeout("on:\n  push:\n") == []

    def test_check_reports_every_offender(self) -> None:
        errors: list[str] = []

        _MODULE.check_every_job_has_a_timeout(errors, {"ci.yml": self._WITHOUT})

        assert len(errors) == 1
        assert "build" in errors[0] and "6 часов" in errors[0]

    def test_missing_files_are_red(self, monkeypatch) -> None:
        """Пустой вход обязан быть красным: проверка без файлов зеленела бы на пустоте."""
        errors: list[str] = []
        monkeypatch.setattr(_MODULE, "_WORKFLOWS", pathlib.Path("нет-такого-каталога"))

        _MODULE.check_every_job_has_a_timeout(errors)

        assert len(errors) == 1
        assert "файлов нет" in errors[0]

    def test_every_workflow_is_looked_at(self) -> None:
        """Смотрим на ВСЕ файлы, а не на два поимённо.

        Первая редакция знала только `ci.yml` и `release.yml`, и новый workflow
        заводился без таймаута незамеченным — сторож, видящий не всё, хуже
        отсутствующего.
        """
        errors: list[str] = []
        seen = sorted(path.name for path in _MODULE._WORKFLOWS.glob("*.yml"))

        _MODULE.check_every_job_has_a_timeout(errors)

        assert errors == []
        assert len(seen) > 2, f"в каталоге всего {len(seen)} файл(ов) — проверять нечего"

    def test_real_workflows_pass(self) -> None:
        """И живые файлы проекта — тоже: гейт обязан быть зелёным на настоящем входе."""
        errors: list[str] = []

        _MODULE.check_every_job_has_a_timeout(errors)

        assert errors == []


class TestQueueMoverToken:
    """Двигатель очереди не должен обновлять ветку штатным токеном (issue #1287).

    Пуш от `GITHUB_TOKEN` не запускает другие workflow — защита от рекурсии.
    Подставь его сюда, и голова очереди обновится, а прогон на PR не стартует:
    PR застрянет **без единой красной проверки**, то есть беда опять будет
    выглядеть нормальной работой.
    """

    _GOOD = (
        "jobs:\n  move:\n    steps:\n"
        "      - name: Обновить голову очереди из main\n"
        "        env:\n          GH_TOKEN: ${{ env.MERGE_QUEUE_TOKEN }}\n"
        "        run: python scripts/gh_rest.py update-branch 1\n"
    )

    def test_dedicated_token_passes(self) -> None:
        errors: list[str] = []

        _MODULE.check_queue_mover_uses_its_own_token(errors, self._GOOD)

        assert errors == []

    def test_github_token_is_rejected(self) -> None:
        errors: list[str] = []
        source = self._GOOD.replace("env.MERGE_QUEUE_TOKEN", "secrets.GITHUB_TOKEN")

        _MODULE.check_queue_mover_uses_its_own_token(errors, source)

        assert len(errors) == 1
        assert "GITHUB_TOKEN" in errors[0]

    def test_step_without_token_is_rejected(self) -> None:
        """Без GH_TOKEN скрипт возьмёт чужой токен из окружения или упадёт."""
        errors: list[str] = []
        source = (
            "jobs:\n  move:\n    steps:\n"
            "      - name: Обновить\n        run: python scripts/gh_rest.py update-branch 1\n"
        )

        _MODULE.check_queue_mover_uses_its_own_token(errors, source)

        assert len(errors) == 1
        assert "GH_TOKEN" in errors[0]

    def test_renamed_step_is_loud(self) -> None:
        """Шага не нашли — это «проверка сторожит пустоту», а не «всё хорошо»."""
        errors: list[str] = []

        _MODULE.check_queue_mover_uses_its_own_token(errors, "jobs:\n  move:\n    steps: []\n")

        assert len(errors) == 1
        assert "update-branch" in errors[0]

    def test_missing_file_is_red(self, monkeypatch) -> None:
        errors: list[str] = []
        monkeypatch.setattr(_MODULE, "_QUEUE_MOVER", pathlib.Path("нет-такого.yml"))

        _MODULE.check_queue_mover_uses_its_own_token(errors)

        assert len(errors) == 1
        assert "файла нет" in errors[0]

    def test_real_workflow_passes(self) -> None:
        """И живой файл проекта: гейт обязан быть зелёным на настоящем входе."""
        errors: list[str] = []

        _MODULE.check_queue_mover_uses_its_own_token(errors)

        assert errors == []


class TestReleaseNotesAreTranslated:
    """Гейт перевода стоит в `verify` и стоит там со `--strict` (issue #1290)."""

    _GOOD = (
        "jobs:\n  verify:\n    steps:\n"
        "      - name: Release notes\n"
        '        run: python scripts/extract_release_notes.py "$REF" --out release-notes.md\n'
        "      - name: Translated\n"
        "        run: python scripts/check_changelog_translated.py --strict release-notes.md\n"
        "  build:\n    steps: []\n"
    )

    def test_strict_gate_in_verify_passes(self) -> None:
        errors: list[str] = []

        _MODULE.check_release_notes_are_translated(errors, self._GOOD)

        assert errors == []

    def test_missing_gate_is_rejected(self) -> None:
        errors: list[str] = []
        source = (
            "jobs:\n  verify:\n    steps:\n      - run: python scripts/extract_release_notes.py\n"
        )

        _MODULE.check_release_notes_are_translated(errors, source)

        assert len(errors) == 1
        assert "check_changelog_translated.py" in errors[0]

    def test_gate_without_strict_is_rejected(self) -> None:
        """Без `--strict` скрипт возвращает 0 — гейт, который ничего не держит."""
        errors: list[str] = []
        source = self._GOOD.replace(
            "check_changelog_translated.py --strict release-notes.md",
            "check_changelog_translated.py release-notes.md",
        )

        _MODULE.check_release_notes_are_translated(errors, source)

        assert len(errors) == 1
        assert "--strict" in errors[0]

    def test_missing_file_is_red(self, monkeypatch) -> None:
        errors: list[str] = []
        monkeypatch.setattr(_MODULE, "_RELEASE", pathlib.Path("нет-такого.yml"))

        _MODULE.check_release_notes_are_translated(errors)

        assert len(errors) == 1
        assert "файла нет" in errors[0]

    def test_real_workflow_passes(self) -> None:
        errors: list[str] = []

        _MODULE.check_release_notes_are_translated(errors)

        assert errors == []


class TestPrOpenerUsesItsOwnToken:
    """Открыватель PR ходит PAT владельца: с GITHUB_TOKEN автором станет бот."""

    _GOOD = (
        "jobs:\n  open-pulls:\n    steps:\n"
        "      - name: Открыть PR\n"
        "        env:\n          GH_TOKEN: ${{ env.MERGE_QUEUE_TOKEN }}\n"
        "        run: python scripts/open_agent_prs.py\n"
    )

    def test_dedicated_token_passes(self) -> None:
        errors: list[str] = []

        _MODULE.check_pr_opener_uses_its_own_token(errors, self._GOOD)

        assert errors == []

    def test_github_token_is_rejected(self) -> None:
        errors: list[str] = []
        source = self._GOOD.replace("env.MERGE_QUEUE_TOKEN", "secrets.GITHUB_TOKEN")

        _MODULE.check_pr_opener_uses_its_own_token(errors, source)

        assert len(errors) == 1
        assert "бот" in errors[0]

    def test_step_without_token_is_rejected(self) -> None:
        errors: list[str] = []
        source = (
            "jobs:\n  open-pulls:\n    steps:\n"
            "      - name: Открыть PR\n        run: python scripts/open_agent_prs.py\n"
        )

        _MODULE.check_pr_opener_uses_its_own_token(errors, source)

        assert len(errors) == 1
        assert "GH_TOKEN" in errors[0]

    def test_renamed_step_is_loud(self) -> None:
        """Шага не нашли — это «сторожим пустоту», а не «всё хорошо»."""
        errors: list[str] = []

        _MODULE.check_pr_opener_uses_its_own_token(errors, "jobs:\n  open:\n    steps: []\n")

        assert len(errors) == 1
        assert "open_agent_prs.py" in errors[0]

    def test_missing_file_is_red(self, monkeypatch) -> None:
        errors: list[str] = []
        monkeypatch.setattr(_MODULE, "_PR_OPENER", pathlib.Path("нет-такого.yml"))

        _MODULE.check_pr_opener_uses_its_own_token(errors)

        assert len(errors) == 1
        assert "файла нет" in errors[0]

    def test_real_workflow_passes(self) -> None:
        errors: list[str] = []

        _MODULE.check_pr_opener_uses_its_own_token(errors)

        assert errors == []


class TestRunBlocksAreValidShell:
    """Шаг `run:` обязан разбираться как shell (issue #1384).

    Прецедент: шаг открывал группировку `{` и не закрывал её. YAML при этом
    валиден, ревью не спотыкается, линтер молчит — падает прогон, где шага
    никто не ждёт.
    """

    _BROKEN = (
        "jobs:\n  x:\n    steps:\n      - name: сломанный\n"
        "        run: |\n          {\n            echo «привет»\n"
    )
    _WHOLE = (
        "jobs:\n  x:\n    steps:\n      - name: целый\n"
        '        run: |\n          {\n            echo "готово"\n'
        '          } >> "$GITHUB_STEP_SUMMARY"\n'
    )

    def test_unclosed_group_is_red(self) -> None:
        errors: list[str] = []

        _MODULE.check_run_blocks_are_valid_shell(errors, {"злой.yml": self._BROKEN})

        assert len(errors) == 1
        assert "сломанный" in errors[0]

    def test_whole_script_passes(self) -> None:
        errors: list[str] = []

        _MODULE.check_run_blocks_are_valid_shell(errors, {"добрый.yml": self._WHOLE})

        assert errors == []

    def test_platform_expression_is_not_a_syntax_error(self) -> None:
        """`${{ ... }}` для bash не синтаксис — до разбора оно заменяется."""
        source = (
            "jobs:\n  x:\n    steps:\n      - name: с выражением\n"
            '        run: |\n          echo "${{ github.event.number }}"\n'
        )
        errors: list[str] = []

        _MODULE.check_run_blocks_are_valid_shell(errors, {"выражение.yml": source})

        assert errors == []

    def test_block_body_ends_with_the_indent(self) -> None:
        """Тело блока — до первой строки с отступом не глубже `run:`."""
        source = (
            "jobs:\n  x:\n    steps:\n      - name: первый\n"
            "        run: |\n          echo один\n"
            "      - name: второй\n        run: |\n          echo два\n"
        )

        found = _MODULE.run_scripts(source)

        assert [name for name, _ in found] == ["первый", "второй"]
        assert found[0][1].strip() == "echo один"

    def test_repo_workflows_are_whole(self) -> None:
        """Живые файлы: гейт, который не гоняли по настоящему предмету, — обещание."""
        errors: list[str] = []

        _MODULE.check_run_blocks_are_valid_shell(errors)

        assert errors == []
