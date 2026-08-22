"""У каждого гарда есть исполнитель, и он объявлен (issue #1348).

Две проверки состояния трекера были написаны, покрыты тестами — и не
запускались **ничем**: ни `ci.yml`, ни `preflight.py`, ни pre-commit, ни
расписанием. При этом свод обещал, что они «проверяют». Правило исполнялось
ровно тогда, когда кто-то вспомнит команду; за месяц не вспомнил никто.

Дефект здесь не в конкретном скрипте, а в том, что **отсутствие исполнителя
ничем не обнаруживалось**: гард-сирота выглядит снаружи точно так же, как
работающий. Поэтому тест проверяет не «эти два запускаются», а весь класс:
каждый `scripts/check_*.py` объявлен в реестре ниже вместе с тем, кто его
запускает, и заявленный исполнитель действительно на него ссылается.

Новый гард без исполнителя роняет этот тест — то есть автор обязан ответить на
вопрос «кто это запускает?» до мержа, а не через месяц.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_SCRIPTS = _ROOT / "scripts"

#: Кто запускает каждый гард. Ключ — имя файла, значение — путь исполнителя и
#: причина, по которой выбран именно он. Скрипт, которого здесь нет, роняет
#: тест: «никто» — не вариант, а именно тот дефект, из которого вырос issue.
_RUNNERS: dict[str, tuple[str, str]] = {
    "check_attribution.py": (
        "scripts/check_pr_ready.py",
        "импортируется гейтом мержа: подпись сверяется до слияния, после — поздно",
    ),
    "check_changelog_translated.py": (
        ".github/workflows/ci.yml",
        "запись уезжает в CHANGELOG и на PyPI — проверяется на каждый PR",
    ),
    "check_contrast.py": (
        "tests/test_contrast.py",
        "осознанно тестом, а не джобом: предмет — файлы репозитория, а не трекер",
    ),
    "check_docs_guardrails.py": (".github/workflows/ci.yml", "бюджеты и ссылки документации"),
    "check_good_first_issues_bilingual.py": (
        ".github/workflows/tracker-guardrails.yml",
        "предмет — состояние трекера: расписание, а не прогон на каждый PR (квота)",
    ),
    "check_issue_checklists.py": (
        ".github/workflows/tracker-guardrails.yml",
        "предмет — состояние трекера: расписание, а не прогон на каждый PR (квота)",
    ),
    "check_locale_guardrails.py": (".github/workflows/ci.yml", "полнота локалей"),
    "check_rule_bindings.py": (
        ".github/workflows/ci.yml",
        "формат ответа каталогу — на каждый PR; полнота против каталога — "
        "по расписанию в tracker-guardrails.yml, ей нужен клон каталога",
    ),
    "check_pip_audit_report.py": (
        ".github/workflows/ci.yml",
        "разбор отчёта pip-audit в цепочке поставок",
    ),
    "check_pr_ready.py": (
        ".github/workflows/ci.yml",
        "гейт мержа; вызывается и вручную перед слиянием",
    ),
    "check_ruff_pin.py": (".github/workflows/ci.yml", "пины инструментов вердикта"),
    "check_secret_dumps.py": (".github/workflows/ci.yml", "реестр точек дампа секретов"),
    "check_test_isolation.py": (".github/workflows/ci.yml", "изоляция тестов"),
    "check_ui_locale_guardrails.py": (".github/workflows/ci.yml", "UI-строки без хардкода"),
    "check_version_consistency.py": (".github/workflows/ci.yml", "дрейф версии в доках"),
    "check_web_imports.py": (".github/workflows/ci.yml", "импорты ES-модулей веб-слоя"),
    "check_wheel_contents.py": (".github/workflows/release.yml", "содержимое колеса при релизе"),
    "check_work_overlap.py": (
        "docs/agent/preflight.md",
        "механизм ДОБРОВОЛЬНЫЙ (opt-in pre-push хук `--install-hook`): "
        "список чужих веток стоит обращения к API, поэтому предпушевой гейт его "
        "не зовёт — и это названо явно, по правилу «правило без механизма»",
    ),
    "check_workflow_guardrails.py": (".github/workflows/ci.yml", "пины и таймауты в workflow'ах"),
}


def _guard_scripts() -> list[str]:
    return sorted(path.name for path in _SCRIPTS.glob("check_*.py"))


def test_every_guard_declares_its_runner() -> None:
    """Гард без объявленного исполнителя — тот самый дефект, а не мелочь."""
    declared = set(_RUNNERS)
    present = set(_guard_scripts())

    orphans = sorted(present - declared)
    assert not orphans, (
        f"гард без объявленного исполнителя: {orphans}. "
        "Подключите его (workflow, preflight, pre-commit или тест) и впишите сюда — "
        "иначе он повторит issue #1348: написан, покрыт тестами и не запускается."
    )

    stale = sorted(declared - present)
    assert not stale, f"в реестре есть исчезнувшие скрипты: {stale}"


@pytest.mark.parametrize("script", _guard_scripts())
def test_declared_runner_actually_calls_the_guard(script: str) -> None:
    """Заявленный исполнитель действительно ссылается на скрипт.

    Без этой проверки реестр стал бы декларацией, разошедшейся с фактом, —
    ровно тем, чем был свод до issue #1348.
    """
    runner, why = _RUNNERS[script]
    path = _ROOT / runner
    assert path.exists(), f"{script}: исполнитель {runner} не найден"

    text = path.read_text(encoding="utf-8")
    needle = script.removesuffix(".py")
    assert needle in text, f"{script}: {runner} на него не ссылается (заявлено: {why})"


def test_tracker_guards_run_on_a_schedule() -> None:
    """Приёмка #1348: нарушение в трекере находится без участия человека.

    Красный до правки: workflow не существовал, и обе проверки не запускались
    ничем — свод обещал механизм, которого не было.
    """
    workflow = _ROOT / ".github" / "workflows" / "tracker-guardrails.yml"
    assert workflow.exists(), "нет workflow, запускающего проверки трекера"

    text = workflow.read_text(encoding="utf-8")
    assert "schedule:" in text, "без расписания проверка снова ждёт, что кто-то вспомнит"
    assert "workflow_dispatch:" in text, "нужен ручной запуск: проверить, не дожидаясь суток"
    assert "check_issue_checklists" in text
    assert "check_good_first_issues_bilingual" in text


def test_tracker_guards_warn_without_failing_the_run() -> None:
    """Предупреждают о вероятном: чинить в трекере может быть нечего.

    Чек-лист мог отсутствовать намеренно, `good first issue` — ждать перевода
    первые минуты. Красный прогон здесь требовал бы починки там, где её нет.
    """
    text = (_ROOT / ".github" / "workflows" / "tracker-guardrails.yml").read_text(encoding="utf-8")
    assert "|| true" in text, "нарушение в трекере не должно ронять прогон"
    assert "GITHUB_STEP_SUMMARY" in text, (
        "итог обязан попадать в summary прогона: предупреждение, "
        "спрятанное в лог, — это снова то, о чём никто не вспомнит"
    )
