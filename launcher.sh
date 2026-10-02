#!/bin/sh
# launcher.sh — запуск окна-лаунчера из клона репозитория (issue #1185).
#
# Нужен тем, у кого есть клон: после `pipx install` то же окно открывает команда
# `stepik-grader-gui` из любого каталога, и этот файл не нужен.
#
# Ищет `.venv` рядом с собой, а не полагается на активированное окружение:
# «сначала activate, потом запуск» — ровно то трение, которое файл и убирает.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -x "$here/.venv/bin/python" ]; then
    python="$here/.venv/bin/python"
elif [ -x "$here/.venv/Scripts/python.exe" ]; then   # venv, созданный на Windows
    python="$here/.venv/Scripts/python.exe"
else
    echo "Виртуальное окружение не найдено: $here/.venv" >&2
    echo "Создайте его и поставьте пакет:" >&2
    echo "    python -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi

exec "$python" -m stepik_grader.launcher "$@"
