"""Полный CI до попадания в `main`: очередь мержей (issue #1227 → #1235).

Прежде каждый мерж разворачивал полную матрицу — около пятнадцати job'ов. Она
упиралась в лимит одновременных job'ов, прогон вставал в очередь, а следующий
мерж вытеснял его оттуда **до старта**: в очереди одной concurrency-группы
GitHub держит максимум один ожидающий. Замер 19.08.2026 — шесть мержей подряд,
шесть `cancelled`, ни одного выполненного.

Первым решением (#1229) прогон `main` урезали до одной ОС. Отмены исчезли ценой
слепой зоны: `main` перестал проверяться на Windows и macOS до ночного прогона —
а ОС-специфики в проекте много по устройству (Job Objects против bubblewrap
против `sandbox-exec`, ACL против POSIX-битов, кодировки консоли, короткие пути
Windows).

Сегодняшнее устройство — **полная матрица на любом событии** (#1231) плюс
**защита `main`** (#1235, вариант B): обязательные проверки и «ветка обязана
быть свежей». Очередь держится не размером матрицы, а **темпом мержа**:
следующий PR не мержится, пока не завершился прогон предыдущего, — это
проверяет `check_pr_ready.py`, отвечая «ждать». Плата — мерж примерно раз в
четверть часа.

Merge queue закрыла бы это штатно, но личному аккаунту она недоступна: условие
— тип владельца (организация либо Enterprise Cloud), а не видимость
репозитория.

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
    """Конфиг готов к очереди, хотя включить её на личном аккаунте нельзя."""

    def test_merge_group_trigger_exists(self, ci_yml: str) -> None:
        """Сегодня не срабатывает (очередь — только для организаций), но оставлен.

        Он безвреден, а без него очередь после возможного переезда в
        организацию мержила бы вслепую: в интерфейсе всё выглядит включённым, а
        обязательных проверок на кандидате нет.
        """
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
        """Браузерные journey — единственное, что проверяет страницу целиком.

        Раньше здесь сторожилось условие `!= 'push'`: journey пропускались на
        `main`, а кандидату очереди достались бы по остаточному принципу.
        Теперь (issue #1231) у job'а условия нет вовсе — он выполняется на любом
        событии, и это сильнее прежней проверки.
        """
        block = _job_block(ci_yml, "e2e")

        assert "if: github.event_name" not in block, (
            "e2e снова зависит от события — на main страница перестанет проверяться целиком"
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


class TestMatrixIsTheSameForEveryEvent:
    """Матрица не зависит от события: `main` проверяется как PR (issue #1231).

    Короткий прогон (#1229) убрал отмены в очереди, но ценой слепой зоны:
    `main` переставал проверяться на Windows и macOS до ночного прогона. Зона
    не теоретическая — полная матрица на PR в тот же день поймала падение
    `test_local_runner_times_out` на `macos-latest × 3.14`, невидимое для одной
    ОС. Очередь держится темпом мержа, а не размером матрицы.
    """

    def test_matrix_does_not_branch_on_event(self, ci_yml: str) -> None:
        """Ветвление вернулось бы незаметно: YAML остался бы валидным."""
        block = _job_block(ci_yml, "test")
        assert "github.event_name" not in block, (
            "матрица снова различает событие — main опять проверяется не как PR"
        )

    @pytest.mark.parametrize("os_name", ["ubuntu-latest", "windows-latest", "macos-latest"])
    def test_every_os_runs_on_push_too(self, ci_yml: str, os_name: str) -> None:
        """Три ОС на любом событии, включая пуш в `main`."""
        block = _job_block(ci_yml, "test")
        assert os_name in block

    @pytest.mark.parametrize("version", ["3.12", "3.13"])
    def test_both_supported_versions_run(self, ci_yml: str, version: str) -> None:
        block = _job_block(ci_yml, "test")
        assert f'"{version}"' in block

    def test_experimental_314_still_covered(self, ci_yml: str) -> None:
        """issue #454: 3.14 обещана `requires-python` и покрывается на трёх ОС."""
        block = _job_block(ci_yml, "test")
        assert '"3.14"' in block
        assert block.count('python-version: "3.14"') == 3

    def test_experimental_314_does_not_block(self, ci_yml: str) -> None:
        """Экспериментальные комбинации не должны ронять мерж."""
        block = _job_block(ci_yml, "test")
        assert "continue-on-error" in block


class TestNightlyCrossOsCoverage:
    def test_schedule_trigger_exists(self, ci_yml: str) -> None:
        assert "schedule:" in ci_yml
        assert re.search(r'cron:\s*"[^"]+"', ci_yml), "расписание без cron-выражения"

    def test_nightly_has_its_own_concurrency_group(self, ci_yml: str) -> None:
        """Иначе ночной прогон встанет в очередь к пушам и будет ими вытеснен."""
        group = re.search(r"^concurrency:\n  group: (.+)$", ci_yml, re.MULTILINE)
        assert group, "concurrency.group не найден"
        assert "schedule" in group.group(1)

    def test_combined_badge_is_published_from_the_merge_run(self, ci_yml: str) -> None:
        """Прогон `push` снова видит три ОС — бейджу больше незачем ждать ночи.

        Отставание на сутки было платой за короткий прогон (issue #1227): его
        число не cross-OS, и публиковать его под «all OS» значило соврать. С
        возвратом полной матрицы (issue #1231) плата отпала.
        """
        block = _job_block(ci_yml, "coverage-combine")
        assert 'github.event_name }}" != "push"' not in block, (
            "combined-бейдж снова ждёт ночного прогона, хотя матрица уже полная"
        )

    def test_combined_badge_still_guarded_against_degradation(self, ci_yml: str) -> None:
        """Недосчитанное число под «all OS» — точная ложь, и это важнее свежести.

        Если ожидаемых coverage-данных нет (упал, например, `sandbox-linux`),
        бейдж не публикуется вовсе: прошлое честное значение остаётся в силе.
        """
        block = _job_block(ci_yml, "coverage-combine")
        guard = re.search(
            r'if \[ "\$\{\{ steps\.combine\.outputs\.degraded \}\}" != "true" \]'
            r".*coverage-combined",
            block,
            re.DOTALL,
        )
        assert guard, "бейдж «all OS» публикуется даже при деградации данных"

    def test_badges_are_committed_on_schedule_too(self, ci_yml: str) -> None:
        """Иначе ночной прогон посчитает покрытие и никуда его не запишет."""
        block = _job_block(ci_yml, "coverage-combine")
        assert "github.event_name == 'schedule'" in block


class TestNoiseIsNotNormalised:
    """Предупреждение, которое горит всегда, перестают читать."""

    @pytest.mark.parametrize("source", ["ubuntu-latest", "windows-latest", "macos-latest"])
    def test_every_os_is_required_for_the_combined_report(self, ci_yml: str, source: str) -> None:
        """Матрица снова полная, значит артефакты всех трёх ОС обязаны быть.

        Прежде на `push` требовалась одна ubuntu: windows и macos там не
        запускались, и требовать их значило бы выдавать ``::warning::`` на
        каждом мерже. Теперь их отсутствие — настоящая деградация, а не
        особенность события.
        """
        block = _job_block(ci_yml, "coverage-combine")
        assert f"--require {source}" in block


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


class TestBadgesStayOutOfMain:
    """Бейджи публикуются веткой `badges`, а не коммитом в `main` (issue #1235).

    Два следствия, и оба ломаются молча. **Первое** — защита `main`: обязательные
    проверки запрещают прямой push кому угодно, а обход для GitHub Actions
    личному репозиторию недоступен, поэтому вернувшийся `push origin HEAD:main`
    не «слегка нарушит правило», а насовсем сломает обновление бейджей.
    **Второе** — README ссылается на ветку по имени: разъедься она с CI, и
    бейджи отдадут 404, о чём не скажет ни один прогон.
    """

    def test_badge_job_does_not_push_to_main(self, ci_yml: str) -> None:
        assert "push origin HEAD:main" not in ci_yml

    def test_badge_job_publishes_to_the_badges_branch(self, ci_yml: str) -> None:
        block = _job_block(ci_yml, "coverage-combine")
        assert "push origin badges" in block
        assert "worktree add" in block

    @pytest.mark.parametrize("readme", ["README.md", "README.en.md"])
    def test_readme_reads_badges_from_that_branch(self, readme: str) -> None:
        text = (pathlib.Path(__file__).parent.parent / readme).read_text(encoding="utf-8")
        assert "Stepik-Python-Grader/main/.github/badges/" not in text
        assert "Stepik-Python-Grader/badges/.github/badges/" in text


class TestDocumentationTellsTheTruth:
    def test_claude_md_says_the_queue_is_unavailable(self) -> None:
        """Первая редакция #1235 утверждала обратное — и по ней уже был сделан PR.

        Merge queue есть у репозиториев организации; у личного аккаунта опции
        нет в защите ветки вовсе. Контракт обязан говорить это прямо, иначе
        следующий заход снова пойдёт искать несуществующую галочку.
        """
        text = " ".join(_CLAUDE_MD.read_text(encoding="utf-8").split())

        assert "Merge queue нам недоступна" in text
        assert "организации" in text

    def test_claude_md_spells_out_the_merge_order(self) -> None:
        """Вариант B — это порядок действий, а не настройка: его и записываем."""
        text = " ".join(_CLAUDE_MD.read_text(encoding="utf-8").split())

        assert "auto-merge" in text
        assert "Обновить ветку из `main`" in text

    def test_claude_md_says_the_badge_is_nightly(self) -> None:
        """Иначе отставание бейджа на сутки выглядит как поломка."""
        text = " ".join(_CLAUDE_MD.read_text(encoding="utf-8").split())
        assert "НОЧНУЮ сборку" in text or "ночную сборку" in text
