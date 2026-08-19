"""Полный CI до попадания в `main`: очередь мержей (issue #1227 → #1235).

Прежде каждый мерж разворачивал полную матрицу — около пятнадцати job'ов. Она
упиралась в лимит одновременных job'ов, прогон вставал в очередь, а следующий
мерж вытеснял его оттуда **до старта**: в очереди одной concurrency-группы
GitHub держит максимум один ожидающий. Замер 19.08.2026 — шесть мержей подряд,
шесть `cancelled`, ни одного выполненного.

Первым решением (#1229) прогон `main` урезали до одной ОС. Отмены исчезли ценой
слепой зоны: `main` перестал проверяться на Windows и macOS до ночного прогона.
Настоящее решение — **merge queue** (#1235): GitHub собирает ветку «`main` +
этот PR», гоняет на ней полную матрицу и мержит только при зелёном. Проверяется
будущее состояние `main`, а не устаревшая ветка PR, и у каждого кандидата свой
ref — вытеснять друг друга им нечем.

Главное, что здесь сторожится, — **триггер `merge_group`**. Без него проверки
на кандидате не запускаются вовсе, и очередь мержит вслепую; в интерфейсе при
этом всё выглядит включённым. Тихая ловушка, ради которой файл и существует.

**PyYAML в проекте нет** (ни в runtime, ни в `[dev]`), поэтому YAML разбирается
текстом — как и в соседних guardrail-тестах. Проверяются свойства конфига, а не
его формулировки.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_CI_YML = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
_CLAUDE_MD = pathlib.Path(__file__).parent.parent / "CLAUDE.md"


@pytest.fixture(scope="module")
def ci_yml() -> str:
    return _CI_YML.read_text(encoding="utf-8")


def _job_block(text: str, name: str) -> str:
    """Тело job'а до начала следующего (job — единственный ключ на двух пробелах)."""
    start = text.index(f"\n  {name}:\n")
    rest = text[start + 1 :]
    nxt = re.search(r"\n  [a-z][a-z0-9-]*:\n", rest[1:])
    return rest if nxt is None else rest[: nxt.start() + 1]


class TestMergeQueueIsWiredIn:
    """Тихая ловушка: очередь без своего триггера мержит вслепую."""

    def test_merge_group_trigger_exists(self, ci_yml: str) -> None:
        """Без него на кандидате не запускается НИ ОДНА проверка."""
        head = ci_yml[: ci_yml.index("\nconcurrency:")]

        assert re.search(r"^  merge_group:", head, re.MULTILINE), (
            "нет триггера merge_group — очередь будет мержить непроверенное"
        )

    @pytest.mark.parametrize("os_name", ["ubuntu-latest", "windows-latest", "macos-latest"])
    def test_candidate_runs_the_full_matrix(self, ci_yml: str, os_name: str) -> None:
        """Кандидат — последняя проверка перед main, урезать её нечем.

        Матрица сокращается только на `push`; `merge_group` попадает в ветку
        «иначе», то есть в полный набор.
        """
        block = _job_block(ci_yml, "test")

        assert os_name in block

    def test_candidate_runs_e2e(self, ci_yml: str) -> None:
        """Браузерные journey — единственное, что проверяет страницу целиком."""
        block = _job_block(ci_yml, "e2e")

        assert "github.event_name != 'push'" in block, (
            "e2e пропускается не только на push — кандидат очереди его лишится"
        )

    def test_candidate_is_never_cancelled(self, ci_yml: str) -> None:
        """Отменённая обязательная проверка выкидывает кандидата из очереди.

        Прежнее условие сравнивало ref с `refs/heads/main`, а у кандидата ref
        свой (`gh-readonly-queue/...`) — по названию ветки очередь от обычной не
        отличить, поэтому спрашиваем событие.
        """
        cancel = re.search(r"^  cancel-in-progress: (.+)$", ci_yml, re.MULTILINE)

        assert cancel, "cancel-in-progress не найден"
        assert "pull_request" in cancel.group(1)
        assert "refs/heads/main" not in cancel.group(1)


class TestMatrixDependsOnEvent:
    """Размер матрицы разный: короткий пуш в main, полный набор до мержа."""

    def test_matrix_branches_on_event(self, ci_yml: str) -> None:
        block = _job_block(ci_yml, "test")
        assert "github.event_name == 'push'" in block, "матрица не различает событие"

    def test_push_runs_a_single_os(self, ci_yml: str) -> None:
        """Ради этого всё и делалось: короткий прогон не копит очередь."""
        block = _job_block(ci_yml, "test")
        assert '["ubuntu-latest"]' in block or "'[\"ubuntu-latest\"]'" in block

    @pytest.mark.parametrize("os_name", ["ubuntu-latest", "windows-latest", "macos-latest"])
    def test_full_matrix_survives_for_pull_request(self, ci_yml: str, os_name: str) -> None:
        """Проверять надо ДО мержа — полную матрицу на PR сокращать нельзя."""
        block = _job_block(ci_yml, "test")
        assert os_name in block

    def test_experimental_314_still_covered(self, ci_yml: str) -> None:
        """issue #454: 3.14 обещана `requires-python` и покрывается на трёх ОС."""
        block = _job_block(ci_yml, "test")
        assert '"3.14"' in block
        assert block.count('"python-version": "3.14"') == 3


class TestNightlyCrossOsCoverage:
    def test_schedule_trigger_exists(self, ci_yml: str) -> None:
        assert "schedule:" in ci_yml
        assert re.search(r'cron:\s*"[^"]+"', ci_yml), "расписание без cron-выражения"

    def test_nightly_has_its_own_concurrency_group(self, ci_yml: str) -> None:
        """Иначе ночной прогон встанет в очередь к пушам и будет ими вытеснен."""
        group = re.search(r"^concurrency:\n  group: (.+)$", ci_yml, re.MULTILINE)
        assert group, "concurrency.group не найден"
        assert "schedule" in group.group(1)

    def test_combined_badge_not_published_from_the_short_run(self, ci_yml: str) -> None:
        """Число одной ОС под бейджем «all OS» — это ложь, а не приближение."""
        block = _job_block(ci_yml, "coverage-combine")
        guard = re.search(
            r'if \[ "\$\{\{ github\.event_name \}\}" != "push" \].*coverage-combined',
            block,
            re.DOTALL,
        )
        assert guard, "cross-OS бейдж не защищён от публикации с короткого прогона"

    def test_badges_are_committed_on_schedule_too(self, ci_yml: str) -> None:
        """Иначе ночной прогон посчитает покрытие и никуда его не запишет."""
        block = _job_block(ci_yml, "coverage-combine")
        assert "github.event_name == 'schedule'" in block


class TestNoiseIsNotNormalised:
    def test_required_sources_depend_on_the_matrix_size(self, ci_yml: str) -> None:
        """Предупреждение, которое горит всегда, перестают читать.

        На коротком прогоне windows/macos нет по замыслу; требовать их значило
        бы выдавать ``::warning::`` на каждом мерже — и настоящую деградацию
        тогда пропустят вместе с шумом.
        """
        block = _job_block(ci_yml, "coverage-combine")
        assert "REQUIRED" in block
        assert "github.event_name == 'push'" in block


class TestConfigDoesNotMislead:
    """Ловушка, из-за которой дефект жил незамеченным."""

    def test_comment_says_cancel_in_progress_does_not_cover_pending(self, ci_yml: str) -> None:
        """Прежний комментарий читался как «прогоны main защищены». Они не были."""
        head = ci_yml[: ci_yml.index("jobs:")]
        assert "ОЖИДАЮЩИЙ" in head or "ожидающ" in head.lower()

    def test_measurement_is_kept_as_evidence(self, ci_yml: str) -> None:
        """Правила проекта растут из инцидентов, а не из общих соображений."""
        head = ci_yml[: ci_yml.index("jobs:")]
        assert "cancelled" in head


class TestDocumentationTellsTheTruth:
    def test_claude_md_names_the_silent_trap(self) -> None:
        """Настройка очереди без `merge_group` выглядит рабочей и мержит вслепую.

        Правило, которого нет в контракте, соблюдают до первой настройки с нуля.
        """
        text = " ".join(_CLAUDE_MD.read_text(encoding="utf-8").split())

        assert "merge_group" in text
        assert "вслепую" in text

    def test_claude_md_says_the_badge_is_nightly(self) -> None:
        """Иначе отставание бейджа на сутки выглядит как поломка."""
        text = " ".join(_CLAUDE_MD.read_text(encoding="utf-8").split())
        assert "НОЧНУЮ сборку" in text or "ночную сборку" in text
