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
    "check_audit_registry.py": (
        ".github/workflows/tracker-guardrails.yml",
        "предмет — документ аудита против истории мержей: обращение к API, "
        "поэтому расписание, а не прогон на каждый PR",
    ),
    "check_branch_protection.py": (
        ".github/workflows/tracker-guardrails.yml",
        "предмет — настройки репозитория: они не меняются от коммита к коммиту, "
        "а расхождение означает, что публично заявленная гарантия отменена",
    ),
    "check_changelog_translated.py": (
        ".github/workflows/ci.yml",
        "запись уезжает в CHANGELOG и на PyPI — проверяется на каждый PR",
    ),
    "check_container_closure.py": (
        ".github/workflows/tracker-guardrails.yml",
        "правило 121: предмет — состояние трекера, поэтому расписание, а не прогон "
        "на каждый PR (квота общая на аккаунт)",
    ),
    "check_contrast.py": (
        "tests/test_contrast.py",
        "осознанно тестом, а не джобом: предмет — файлы репозитория, а не трекер",
    ),
    "check_contract_evolution.py": (
        ".github/workflows/ci.yml",
        "правило 113: контракт говорит, что стабильно, что расширяемо и как добавляют новое",
    ),
    "check_declared_outcomes.py": (
        ".github/workflows/ci.yml",
        "правило 145: у каждого объявленного исхода есть прогон, а долг виден числом",
    ),
    "check_docs_guardrails.py": (".github/workflows/ci.yml", "бюджеты и ссылки документации"),
    "check_gate_tests.py": (
        ".github/workflows/ci.yml",
        "правило 140: у гейта есть прогон того, что он обязан отвергнуть",
    ),
    "check_generated_sources.py": (
        ".github/workflows/ci.yml",
        "правило 118: у производного файла назван живой исходник",
    ),
    "check_showcase_links.py": (
        ".github/workflows/ci.yml",
        "правило 089: оригинал не ссылается на свою витрину",
    ),
    "check_glossary_examples.py": (
        ".github/workflows/ci.yml",
        "предмет — файлы базы глоссария: проверяется на каждый PR, как и прочие "
        "гарды содержимого репозитория",
    ),
    "check_good_first_issues_bilingual.py": (
        ".github/workflows/tracker-guardrails.yml",
        "предмет — состояние трекера: расписание, а не прогон на каждый PR (квота)",
    ),
    "check_issue_checklists.py": (
        ".github/workflows/tracker-guardrails.yml",
        "предмет — состояние трекера: расписание, а не прогон на каждый PR (квота)",
    ),
    "check_locale_guardrails.py": (".github/workflows/ci.yml", "полнота локалей"),
    "check_mcp_permissions.py": (
        ".github/workflows/ci.yml",
        "форма запрета MCP: именная запись отключается молча при переименовании",
    ),
    "check_rules_digest.py": (
        ".github/workflows/ci.yml",
        "второй рубеж: дайджест правил не разошёлся с ответом проекта, а хук SessionStart объявлен",
    ),
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
    "check_raw_values.py": (
        ".github/workflows/ci.yml",
        "правило 122: в ответ веб-слоя уходит число, а не его отформатированный вид",
    ),
    "check_ruff_pin.py": (".github/workflows/ci.yml", "пины инструментов вердикта"),
    "check_secret_dumps.py": (".github/workflows/ci.yml", "реестр точек дампа секретов"),
    "check_test_isolation.py": (".github/workflows/ci.yml", "изоляция тестов"),
    "check_marker_matching.py": (
        ".github/workflows/ci.yml",
        "правило 141: константа-маркер сверяется целиком, а не началом",
    ),
    "check_step_deadlines.py": (
        ".github/workflows/ci.yml",
        "правило 100: у сетевого шага свой дедлайн — общий предел job'а старт не покрывает",
    ),
    "check_three_outcomes.py": (
        ".github/workflows/ci.yml",
        "правило 039: скрипт, ходящий в GitHub, отличает «не отработала» от «чисто»",
    ),
    "check_truncation_marks.py": (
        ".github/workflows/ci.yml",
        "правило 016: обрезка по пределу оставляет признак обрыва",
    ),
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
    if needle in text:
        return

    # Ссылка может быть через один переход: ночной обход зовёт свои проверки
    # списком внутри `nightly_checks.py` — шаги workflow не тестируются, а этот
    # список тестируется (issue #1384). Дальше одного перехода не идём: цепочка
    # длиннее делает реестр нечитаемым, а его смысл — быстрый ответ «чем».
    for hop in ("nightly_checks",):
        if hop not in text:
            continue
        if needle in (_ROOT / "scripts" / f"{hop}.py").read_text(encoding="utf-8"):
            return

    raise AssertionError(f"{script}: {runner} на него не ссылается (заявлено: {why})")


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
    assert "nightly_checks" in text, "обход должен запускаться, а не только существовать"

    # Сами проверки перечислены в скрипте — шаги workflow не тестируются, а его
    # список тестируется (issue #1384, tests/test_nightly_checks.py).
    listed = (_ROOT / "scripts" / "nightly_checks.py").read_text(encoding="utf-8")
    assert "check_issue_checklists" in listed
    assert "check_good_first_issues_bilingual" in listed


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
