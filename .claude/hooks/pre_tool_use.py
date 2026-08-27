#!/usr/bin/env python3
"""Хук перед вызовом инструмента: два правила окна, которые CI поймать не может.

Гейт краснеет **после** правки: он видит артефакт, а не действие. Часть правил
говорит именно о действии окна — и такое правило до сих пор держалось только
памятью. Прецеденты из каталога:

* **013** — код с экранированием, переданный через heredoc без кавычек в
  делимитере: оболочка превращает ``\\n`` в настоящий перевод строки, и в файл
  уезжает `SyntaxError`. Заметно это не сразу, а на прогоне.
* **012** — пуш в ветку, которую ведёт другое окно: даже «тривиальный» конфликт
  разрешает её владелец, потому что соседнее окно может вести её прямо сейчас.

Хук блокирует ровно эти два случая (код возврата 2 — отказ, текст ошибки уходит
модели). Всё остальное пропускается: хук, который спорит с половиной команд,
отключают целиком, и вместе с ним исчезают обе проверки.

**Молчание — тоже ответ.** Если разобрать команду не удалось, вызов
пропускается: ложный отказ здесь дороже пропуска, потому что чинить его будет
человек, а не машина.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys

__all__ = ["escaped_heredoc", "foreign_branch_push", "main"]

#: Любой heredoc — и с кавычками, и без. Кавычки в делимитере разбираются
#: отдельно: с ними оболочка тело не трогает, и правило 013 к нему не относится,
#: но тело всё равно надо пропустить, чтобы не принять его содержимое за команду.
_ANY_HEREDOC = re.compile(r"<<-?\s*(?P<tag>[\"']?[A-Za-z_][A-Za-z0-9_]*[\"']?)")

#: Экранирование, которое оболочка съест по дороге.
_ESCAPES = ("\\n", "\\t", "\\r", "\\\\")


def escaped_heredoc(command: str) -> str | None:
    """Причина отказа, если код с экранированием едет через heredoc (правило 013).

    Разбор идёт последовательно и **пропускает тела**: `<<PY` внутри текста,
    который пишется в файл, — это данные, а не команда оболочки. Без этого хук
    отвергал бы собственные тесты, где такая команда приведена примером, — то
    есть ровно тот ложный отказ, из-за которого хуки отключают целиком.
    """
    position = 0
    while (match := _ANY_HEREDOC.search(command, position)) is not None:
        tag = match.group("tag").strip("\"'")
        quoted = match.group("tag")[0] in "\"'"
        body_start = match.end()
        end = command.find(f"\n{tag}", body_start)
        body = command[body_start : end if end != -1 else len(command)]
        if not quoted and any(escape in body for escape in _ESCAPES):
            return (
                f"Правило 013: heredoc с делимитером <<{tag} (без кавычек) раскрывает "
                "экранирование — \\n станет настоящим переводом строки, и в файл уедет "
                f"SyntaxError. Возьмите делимитер в кавычки (<<'{tag}') либо напишите "
                "файлом через Write."
            )
        position = (end + len(tag) + 1) if end != -1 else len(command)
    return None


def _current_branch() -> str | None:
    """Имя текущей ветки; None — если спросить не удалось."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def foreign_branch_push(command: str, current: str | None = None) -> str | None:
    """Причина отказа, если пуш адресован чужой ветке (правило 012)."""
    if "git push" not in command:
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None  # неразбираемая команда — пропускаем: ложный отказ дороже

    try:
        index = next(
            position
            for position, token in enumerate(parts)
            if token == "push" and position and parts[position - 1].endswith("git")
        )
    except StopIteration:
        return None

    tail = [token for token in parts[index + 1 :] if not token.startswith("-")]
    if len(tail) < 2:
        return None  # `git push` или `git push origin` — адресована текущей ветке
    target = tail[1].split(":")[-1].removeprefix("refs/heads/")
    branch = current if current is not None else _current_branch()
    if branch is None or target == branch:
        return None
    return (
        f"Правило 012: пуш адресован ветке «{target}», а окно стоит на «{branch}». "
        "Правку в чужую ветку вносит её владелец — соседнее окно может вести её "
        "прямо сейчас. Скажите, что и как разрешать, либо переключитесь на ветку."
    )


def _force_utf8_stdio() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли (issue #1108).

    Причина отказа русская, а на Windows кодовая страница бывает
    западноевропейской — кириллицы в ней нет. Без этого хук падал бы на самой
    печати: вызов оказался бы заблокирован, но БЕЗ объяснения, то есть окно
    получило бы отказ без причины. No-op на потоках без ``reconfigure``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    """0 — вызов разрешён; 2 — отказ с причиной в stderr."""
    _force_utf8_stdio()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0

    command = str(payload.get("tool_input", {}).get("command", ""))
    for reason in (escaped_heredoc(command), foreign_branch_push(command)):
        if reason:
            print(reason, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
