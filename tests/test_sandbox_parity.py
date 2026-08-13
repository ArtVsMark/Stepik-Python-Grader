"""Контракт паритета изоляции — одинаковый вердикт с --sandbox и без (issue #992).

Смысл этого файла — не проверить отдельную функцию, а закрепить свойство,
которого у продукта не было: `--sandbox` работал как **второй исполнитель без
контракта**. Восемь заявленных `high` аудита 2026-08-10 приходятся на него, и
шесть ломали ВЕРНОЕ решение — самый тяжёлый случай (SBX-1-01) давал `3/3 OK` без
изоляции и `0/3 FAIL` с ней, потому что сгенерированная обёртка попадала внутрь
под именем модуля решения и импортировала саму себя.

Проверяется то, что видит пользователь: вердикты кейсов. Задачи здесь —
настоящие папки с решением и тестами, прогоняемые обоими путями; сравниваются
не внутренности, а итог. Расхождение — красный тест, и это единственный способ
не отрастить второе поведение заново.

Без рабочего backend'а изоляции файл пропускается целиком и говорит, почему:
молчаливое «зелено, потому что не проверяли» — ровно тот шаблон, который и
позволил дефектам дожить до пользователя.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from stepik_grader.core import grader_core, runner
from stepik_grader.core.sandbox import SandboxRunner, SandboxUnavailableError


def _sandbox_or_skip() -> SandboxRunner:
    """Боевой ``SandboxRunner`` либо пропуск с явной причиной."""
    try:
        return SandboxRunner()
    except SandboxUnavailableError as exc:  # pragma: no cover - зависит от машины
        pytest.skip(f"backend изоляции недоступен на этой машине: {exc}")


def _write_task(
    root: pathlib.Path,
    *,
    solution: str,
    cases: list[tuple[str, str]],
    case_type: str | None,
    extra_modules: dict[str, str] | None = None,
) -> pathlib.Path:
    """Папка задачи: решение, соседние модули и тесты формата Legacy."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "solution.py").write_text(solution, encoding="utf-8")
    for name, source in (extra_modules or {}).items():
        (root / name).write_text(source, encoding="utf-8")

    tests_dir = root / "tests"
    tests_dir.mkdir(exist_ok=True)
    for index, (case_input, expected) in enumerate(cases, start=1):
        (tests_dir / str(index)).write_text(case_input, encoding="utf-8")
        (tests_dir / f"{index}.clue").write_text(expected, encoding="utf-8")
        if case_type is not None:
            (tests_dir / f"{index}.type").write_text(case_type, encoding="utf-8")
    return root / "solution.py"


def _verdicts(solution: pathlib.Path, *, sandboxed: bool) -> list[str]:
    """Вердикты кейсов одного прогона: ``['AC', 'AC', ...]``."""
    previous = runner.active_runner()
    runner.set_runner(_sandbox_or_skip() if sandboxed else runner.LocalRunner())
    try:
        result = grader_core.run_tests(solution, solution.parent / "tests")
    finally:
        runner.set_runner(previous)
    return [case["verdict"] for case in result["cases"]]


# Задачи контракта: verdict обязан совпасть с изоляцией и без неё. Каждая — не
# абстрактный пример, а форма, на которой изоляция уже ломалась.
_ADD = 'def add(a, b):\n    """Сумма."""\n    return a + b\n'
_NEIGHBOUR_SOLUTION = "from helpers import double\n\n\ndef calc(a):\n    return double(a) + 1\n"
_STDIN_ECHO = "print(int(input()) * 2)\n"


@pytest.mark.skipif(sys.platform == "win32", reason="контракт гоняется на POSIX-backend'ах")
class TestSandboxParity:
    """Одна задача — один вердикт, независимо от наличия изоляции."""

    def test_function_positional_args(self, tmp_path: pathlib.Path) -> None:
        """SBX-1-01: обёртка под именем решения импортировала саму себя.

        Симптом был ровно такой: без изоляции 3/3 AC, с изоляцией 3/3 RE
        («cannot import name 'add' from partially initialized module»).
        """
        solution = _write_task(
            tmp_path / "positional",
            solution=_ADD,
            cases=[("1\n2\n", "3\n"), ("2\n4\n", "6\n"), ("3\n6\n", "9\n")],
            case_type="function",
        )

        assert _verdicts(solution, sandboxed=True) == _verdicts(solution, sandboxed=False)

    def test_function_named_args(self, tmp_path: pathlib.Path) -> None:
        """Именованный стиль legacy-теста — тот же путь обёртки, другой стиль связывания."""
        solution = _write_task(
            tmp_path / "named",
            solution=_ADD,
            cases=[("a = 1\nb = 2\n", "3\n"), ("a = 10\nb = 5\n", "15\n")],
            case_type="function",
        )

        assert _verdicts(solution, sandboxed=True) == _verdicts(solution, sandboxed=False)

    def test_solution_importing_neighbour_module(self, tmp_path: pathlib.Path) -> None:
        """SBX-1-02: соседний модуль решения не попадал внутрь изоляции.

        Внутрь видно только то, что материализовано в рабочем каталоге, поэтому
        `helpers.py` рядом с решением обязан туда доехать — иначе верное решение
        падало ImportError'ом на всех кейсах.
        """
        solution = _write_task(
            tmp_path / "neighbour",
            solution=_NEIGHBOUR_SOLUTION,
            cases=[("1\n", "3\n"), ("2\n", "5\n"), ("5\n", "11\n")],
            case_type="function",
            extra_modules={"helpers.py": "def double(x):\n    return x * 2\n"},
        )

        assert _verdicts(solution, sandboxed=True) == _verdicts(solution, sandboxed=False)

    def test_stdin_task_stays_identical(self, tmp_path: pathlib.Path) -> None:
        """Обычный stdin-режим — контроль: он работал и обязан продолжать."""
        solution = _write_task(
            tmp_path / "stdin",
            solution=_STDIN_ECHO,
            cases=[("4\n", "8\n"), ("7\n", "14\n")],
            case_type=None,
        )

        assert _verdicts(solution, sandboxed=True) == _verdicts(solution, sandboxed=False)

    def test_wrong_solution_fails_the_same_way(self, tmp_path: pathlib.Path) -> None:
        """Паритет — не «везде AC»: неверное решение обязано падать одинаково.

        Иначе контракт удовлетворялся бы и режимом, который всегда зелёный.
        """
        solution = _write_task(
            tmp_path / "wrong",
            solution="def add(a, b):\n    return a * b\n",
            cases=[("2\n3\n", "5\n")],
            case_type="function",
        )

        sandboxed = _verdicts(solution, sandboxed=True)

        assert sandboxed == _verdicts(solution, sandboxed=False)
        assert sandboxed == ["WA"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-квоты: RLIMIT_CPU/SIGXCPU нет на Windows"
)
class TestCpuQuotaParity:
    """CPU-квота — backstop, а не второй таймаут (issue #986, PY-2-01/SBX-3-01)."""

    def test_quota_follows_timeout(self) -> None:
        """Длинный прогон получает квоту больше своего таймаута.

        Пока квота была константой из конфига, `timeout=15` при квоте 10 означал,
        что изоляция убьёт решение на 10-й секунде — раньше, чем истечёт время,
        которое пользователь ему разрешил.
        """
        from stepik_grader.core.sandbox._posix_bootstrap import cpu_quota_seconds

        assert cpu_quota_seconds(timeout=15.0, configured_max=10.0) > 15

    def test_quota_keeps_configured_floor(self) -> None:
        """Короткий таймаут не опускает квоту ниже настроенной."""
        from stepik_grader.core.sandbox._posix_bootstrap import cpu_quota_seconds

        assert cpu_quota_seconds(timeout=1.0, configured_max=10.0) == 10

    def test_hanging_solution_times_out_like_local(self, tmp_path: pathlib.Path) -> None:
        """Зависшее решение даёт TLE в обоих режимах, а не сбой на границе квоты."""
        from stepik_grader.core.runner import LocalRunner, RunSpec

        spec = RunSpec(path=None, code=b"while True:\n    pass\n", stdin=None, timeout=3.0)

        sandboxed = _sandbox_or_skip().run(spec)
        local = LocalRunner().run(spec)

        assert sandboxed.timed_out is local.timed_out is True
        assert sandboxed.sandbox_violation is None


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-квоты: RLIMIT_CPU/SIGXCPU нет на Windows"
)
class TestQuotaKillRecognition:
    """Убийство по квоте распознаётся в обеих формах кода возврата (issue #986)."""

    def test_direct_child_negative_code(self) -> None:
        import signal

        from stepik_grader.core.sandbox._posix_common import _killed_by

        assert _killed_by(-signal.SIGXCPU, signal.SIGXCPU) is True

    def test_via_bwrap_positive_code(self) -> None:
        """bwrap переживает своего ребёнка и отдаёт `128 + N`.

        Ровно этот случай не распознавался: код 137 (128+SIGKILL) не проходил
        проверку «отрицательный», и исчерпание квоты становилось невнятным RE.
        """
        import signal

        from stepik_grader.core.sandbox._posix_common import _killed_by

        assert _killed_by(128 + signal.SIGKILL, signal.SIGXCPU) is True
        assert _killed_by(128 + signal.SIGXCPU, signal.SIGXCPU) is True

    def test_plain_failure_is_not_a_quota_kill(self) -> None:
        """Обычное падение решения остаётся падением решения."""
        import signal

        from stepik_grader.core.sandbox._posix_common import _killed_by

        assert _killed_by(1, signal.SIGXCPU) is False
        assert _killed_by(0, signal.SIGXCPU) is False
