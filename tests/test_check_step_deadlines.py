"""У сетевого шага свой дедлайн, а не общий (issue #1384, правило 100).

Дедлайн job'а старт не покрывает: зависшая установка съедает его целиком, и
причина называется неверно — «job превысил лимит» вместо «упала установка».
Прецедент #1271: `e2e` встал на установке Playwright и держал прогон три с
половиной часа.

Гейт проверяется тем, что обязан отвергнуть, и тем, что обязан пропустить:
краснеющий на половине шагов гейт отключают целиком, и вместе с ним исчезает
проверка.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    path = _ROOT / "scripts" / "check_step_deadlines.py"
    spec = importlib.util.spec_from_file_location("check_step_deadlines", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_step_deadlines", module)
    spec.loader.exec_module(module)
    return module


guard = _load()

_JOB_HEAD = "jobs:\n  build:\n    timeout-minutes: 15\n    steps:\n"


def test_network_step_without_deadline_is_flagged() -> None:
    source = _JOB_HEAD + '      - name: Install\n        run: pip install -e ".[dev]"\n'

    problems = guard.steps_without_deadline({"ci.yml": source})

    assert len(problems) == 1
    assert problems[0][0] == "ci.yml"


def test_step_with_its_own_deadline_passes() -> None:
    source = (
        _JOB_HEAD + "      - name: Install\n        timeout-minutes: 10\n"
        '        run: pip install -e ".[dev]"\n'
    )

    assert guard.steps_without_deadline({"ci.yml": source}) == []


def test_job_deadline_does_not_count_for_its_steps() -> None:
    """Главное свойство: общий предел не заменяет шаговый.

    У job'а в примере `timeout-minutes` есть — и всё равно шаг обязан краснеть,
    иначе гейт проверял бы не то, ради чего написан.
    """
    source = _JOB_HEAD + "      - name: Download\n        uses: actions/download-artifact@v8\n"

    assert len(guard.steps_without_deadline({"ci.yml": source})) == 1


def test_local_step_is_not_watched() -> None:
    """Предмет узкий: у шага без сети старт не зависает."""
    source = _JOB_HEAD + "      - name: Тесты\n        run: pytest -q\n"

    assert guard.steps_without_deadline({"ci.yml": source}) == []


def test_comment_does_not_make_a_step_networked() -> None:
    """Слова «pip install» в объяснении соседа — не признак сетевого шага."""
    source = (
        _JOB_HEAD + "      - name: Тесты\n"
        "        # без pip install: зависимости уже стоят шагом выше\n"
        "        run: pytest -q\n"
    )

    assert guard.steps_without_deadline({"ci.yml": source}) == []


def test_several_steps_are_reported_each() -> None:
    source = (
        _JOB_HEAD + "      - name: Раз\n        run: pip install a\n"
        "      - name: Два\n        uses: actions/download-artifact@v8\n"
    )

    assert len(guard.steps_without_deadline({"ci.yml": source})) == 2


def test_live_workflows_pass() -> None:
    """Живой предмет: гейт, который не гоняли по настоящему файлу, — обещание."""
    assert guard.steps_without_deadline() == []
