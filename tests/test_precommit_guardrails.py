"""Guard: guardrail-скрипты CI перечислены и в `.pre-commit-config.yaml` (issue #996).

`REL-2-08`: pre-commit гонял только ruff, а восемь проверок жили исключительно в
CI — о нарушении контрибьютор узнавал после пуша, кругом в 5–10 минут ожидания
вместо секунды на коммите. Все восемь укладываются в ~3.6 с суммарно.

Тест сторожит **дрейф в обе стороны**: скрипт, добавленный в `ci.yml`, но
забытый в pre-commit, снова уедет в «ловится только в CI»; хук, ссылающийся на
удалённый скрипт, будет молча падать у каждого коммитящего.

Разбор текстовый (stdlib-only, без PyYAML) — как остальные guardrail-тесты
проекта: PyYAML не входит ни в runtime-зависимости, ни в `[dev]`.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_PRECOMMIT = _ROOT / ".pre-commit-config.yaml"

#: Скрипты, которым в pre-commit не место, — с явной причиной у каждого.
#: Список намеренно короткий: пополнять его нужно осознанно, иначе он
#: превратится в способ обойти сам тест.
_EXEMPT = {
    # Требует сети (GitHub API) и номера PR — на коммите неприменим.
    "check_pr_ready",
    # Читает состояние трекера, а не рабочее дерево: коммит на него не влияет.
    "check_issue_checklists",
    "check_good_first_issues_bilingual",
    # Сверяет покрытие по артефактам CI-матрицы, которых локально нет.
    "check_coverage_combined",
    # Разбирает отчёт `pip-audit`, который создаётся шагом того же job'а: без
    # аргумента-файла скрипту нечего читать, а гонять сам аудит на каждом
    # коммите — поход в сеть за Advisory DB (issue #921, REL-3-05).
    "check_pip_audit_report",
}


def _scripts_in(path: pathlib.Path) -> set[str]:
    """Имена `scripts/check_*.py`, упомянутые в файле."""
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"scripts/(check_[a-z0-9_]+)\.py", text))


def test_ci_guardrails_are_in_precommit() -> None:
    """Скрипт гоняется в CI — значит и на коммите, пока не назван исключением."""
    missing = sorted(_scripts_in(_CI) - _scripts_in(_PRECOMMIT) - _EXEMPT)

    assert not missing, (
        "guardrail-скрипты гоняются только в CI — контрибьютор узнает о нарушении "
        "после пуша, а не на коммите:\n"
        + "\n".join(f"  scripts/{name}.py" for name in missing)
        + "\nДобавьте local-хук в .pre-commit-config.yaml либо впишите скрипт в "
        "_EXEMPT этого теста с причиной."
    )


def test_precommit_hooks_point_at_existing_scripts() -> None:
    """Хук на удалённый скрипт падал бы у каждого коммитящего."""
    phantom = sorted(
        name for name in _scripts_in(_PRECOMMIT) if not (_ROOT / "scripts" / f"{name}.py").is_file()
    )

    assert not phantom, (
        ".pre-commit-config.yaml ссылается на несуществующие скрипты:\n"
        + "\n".join(f"  scripts/{name}.py" for name in phantom)
    )


@pytest.mark.parametrize("field", ["language: system", "pass_filenames: false"])
def test_local_hooks_share_the_project_conventions(field: str) -> None:
    """`language: system` — интерпретатор из окружения, второй пин не заводится
    (issue #791); `pass_filenames: false` — скрипты обходят репозиторий сами и
    аргументов не принимают, список файлов сломал бы вызов."""
    text = _PRECOMMIT.read_text(encoding="utf-8")
    guardrail_hooks = text.count("entry: python scripts/check_")

    assert guardrail_hooks, "в pre-commit нет ни одного guardrail-хука"
    assert text.count(field) >= guardrail_hooks, (
        f"не у каждого guardrail-хука указано `{field}` "
        f"({text.count(field)} на {guardrail_hooks} хуков)"
    )
