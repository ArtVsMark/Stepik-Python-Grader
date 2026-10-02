"""Хук перед вызовом инструмента: два правила, которые CI поймать не может.

Гейт видит артефакт, а не действие, поэтому «не пиши код с экранированием через
heredoc» (правило 013) и «не пушь в чужую ветку» (правило 012) до сих пор
держались памятью окна.

Тесты проверяют обе стороны. Отвергающий случай — очевидная половина; вторая
важнее: хук обязан молчать на всём остальном. Хук, который спорит с половиной
команд, отключают целиком, и вместе с ним исчезают обе проверки. Отдельно
закреплён случай, на котором первая версия и споткнулась: heredoc, приведённый
ПРИМЕРОМ внутри тела другого heredoc, — это данные, а не команда оболочки.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from typing import Any

_HOOK = pathlib.Path(__file__).parent.parent / ".claude" / "hooks" / "pre_tool_use.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("pre_tool_use", _HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("pre_tool_use", module)
    spec.loader.exec_module(module)
    return module


hook = _load()


def _run(command: str, tool: str = "Bash") -> subprocess.CompletedProcess[str]:
    """Прогнать хук целиком: предмет здесь — его ответ площадке, а не функция."""
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({"tool_name": tool, "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class TestEscapedHeredoc:
    """Правило 013: экранирование через heredoc без кавычек в делимитере."""

    _BAD = 'cat > x.py <<PY\nprint("a\\nb")\nPY'

    def test_unquoted_heredoc_with_escape_is_refused(self) -> None:
        result = _run(self._BAD)

        assert result.returncode == 2
        assert "013" in result.stderr

    def test_quoted_heredoc_is_allowed(self) -> None:
        """С кавычками оболочка тело не трогает — предмета правила нет."""
        assert hook.escaped_heredoc("cat > x.py <<'PY'\nprint(\"a\\nb\")\nPY") is None

    def test_unquoted_heredoc_without_escapes_is_allowed(self) -> None:
        assert hook.escaped_heredoc("cat > x.py <<PY\nprint(1)\nPY") is None

    def test_example_inside_a_body_is_not_a_command(self) -> None:
        """На этом первая версия и споткнулась — отвергала собственные тесты."""
        command = "cat > t.py <<'OUTER'\nпример: cat > x.py <<PY\nprint(\"a\\nb\")\nPY\nOUTER"

        assert hook.escaped_heredoc(command) is None

    def test_second_heredoc_after_the_first_is_still_checked(self) -> None:
        """Пропуск тела не должен превращаться в пропуск остальной команды."""
        command = "cat > a.py <<'A'\nтекст\nA\ncat > b.py <<B\nprint(\"a\\nb\")\nB"

        assert hook.escaped_heredoc(command) is not None

    def test_plain_command_is_allowed(self) -> None:
        assert _run("ls -la && echo готово").returncode == 0


class TestForeignBranchPush:
    """Правило 012: в чужую ветку не пушить."""

    def test_push_to_another_branch_is_refused(self) -> None:
        reason = hook.foreign_branch_push("git push -u origin agent/чужая", current="agent/своя")

        assert reason is not None
        assert "012" in reason

    def test_push_to_current_branch_is_allowed(self) -> None:
        assert hook.foreign_branch_push("git push -u origin своя", current="своя") is None

    def test_bare_push_is_allowed(self) -> None:
        """`git push` и `git push origin` адресованы текущей ветке."""
        assert hook.foreign_branch_push("git push", current="своя") is None
        assert hook.foreign_branch_push("git push origin", current="своя") is None

    def test_refspec_is_understood(self) -> None:
        """`HEAD:ветка` адресует ту же чужую ветку, только другой записью."""
        reason = hook.foreign_branch_push("git push origin HEAD:refs/heads/чужая", current="своя")

        assert reason is not None

    def test_unparsable_command_is_allowed(self) -> None:
        """Ложный отказ дороже пропуска: чинить его будет человек, а не машина."""
        assert hook.foreign_branch_push('git push origin "', current="своя") is None

    def test_unrelated_git_command_is_allowed(self) -> None:
        assert hook.foreign_branch_push("git log --oneline -3", current="своя") is None


def test_refusal_reaches_the_window_in_a_narrow_console() -> None:
    """Отказ без причины хуже отказа: окно не поймёт, что чинить.

    Кодировка `cp1252` воспроизводит Windows-раннер, где кириллицы в кодовой
    странице нет: без принудительного UTF-8 хук падал бы на самой печати
    причины — вызов заблокирован, а почему, не сказано.
    """
    import os

    result = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": 'cat > x.py <<PY\nprint("a\\nb")\nPY'}}
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )

    assert result.returncode == 2
    assert "013" in result.stderr, "причина отказа не дошла до окна"


def test_other_tools_are_not_touched() -> None:
    """Матчер стоит на Bash: остальные инструменты хук не смотрит."""
    assert _run('cat > x.py <<PY\nprint("a\\nb")\nPY', tool="Write").returncode == 0


def test_empty_input_is_not_a_crash() -> None:
    """Пустой вход — не отказ: хук на старте вызова не имеет права падать."""
    result = subprocess.run(
        [sys.executable, str(_HOOK)], input="", capture_output=True, text=True, encoding="utf-8"
    )

    assert result.returncode == 0
