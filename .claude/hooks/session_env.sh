#!/usr/bin/env bash
# Окружение проверки облачного окна: `.venv` на планке проекта (issue #1560).
#
# Облачное окно стартует без того, что нужно для проверки перед толчком:
# Python планки в образе может не быть (замер 01.10: 3.10–3.13, системный
# `python3` — 3.11), а `.venv` с зависимостями тестов собирали руками. Здесь это
# делает механизм — вторым хуком `SessionStart`, отдельно от дайджеста правил:
# `session_start.py` обязан оставаться быстрым и не ходить в сеть, а этот ходит.
#
# Свойства, без которых хук был бы вреднее, чем его отсутствие:
#
# 1. **Только в облаке.** Вне `CLAUDE_CODE_REMOTE=true` — выход без действий:
#    на машине владельца окружение его, и пересобирать его нельзя.
# 2. **Планка — из `pyproject.toml`, без Python.** Хук не может опираться на
#    интерпретатор, который сам же и ставит.
# 3. **Сбой не роняет старт.** Каждый отказ — строка с названным шагом и код 0;
#    `.venv` попадает в `PATH`, только если собран целиком (метка пишется
#    последней). Недоступный PyPI иначе превращал бы открытие окна в лотерею.
# 4. **Повтор дешёвый.** Метка хранит планку и отпечаток `pyproject.toml`:
#    совпала — только `PATH`, без единой установки.
#
# Bash, а не Python: хук запускается окружением процесса окна, куда `PATH` из
# `CLAUDE_ENV_FILE` не доходит, — там системный python образа, а не планка.

set -u

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" 2>/dev/null || exit 0

# Где живёт свежий `uv`. Сайт установщика закрыт сетевой политикой, PyPI открыт:
# `uv` ставится пакетом в отдельное окружение. Переопределяется для тестов.
uv_home="${SESSION_ENV_UV_HOME:-/opt/uv}"

say() { printf 'окружение проверки: %s\n' "$*"; }

floor=$(sed -n 's/^requires-python *= *"[^0-9]*\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' pyproject.toml 2>/dev/null | head -n 1)
if [ -z "$floor" ]; then
    say "планка не найдена (requires-python в pyproject.toml) — окружение не собрано"
    exit 0
fi

stamp=".venv/.session-env"
want="$floor $(cksum < pyproject.toml | cut -d ' ' -f 1)"

if [ -x .venv/bin/python ] && [ "$(cat "$stamp" 2>/dev/null)" = "$want" ]; then
    :
else
    py=$(command -v "python$floor" 2>/dev/null || true)
    if [ -z "$py" ]; then
        if [ ! -x "$uv_home/bin/uv" ]; then
            base=$(command -v python3 2>/dev/null || true)
            if [ -z "$base" ] || ! "$base" -m venv "$uv_home" >/dev/null 2>&1 \
                || ! "$uv_home/bin/pip" install -q -U uv >/dev/null 2>&1; then
                say "шаг «uv из PyPI» не удался — Python $floor не поставлен, .venv не тронут"
                exit 0
            fi
        fi
        if ! "$uv_home/bin/uv" python install "$floor" >/dev/null 2>&1; then
            say "шаг «uv python install $floor» не удался — .venv не тронут"
            exit 0
        fi
        py=$("$uv_home/bin/uv" python find "$floor" 2>/dev/null || true)
        if [ -z "$py" ]; then
            say "Python $floor поставлен, но не найден — .venv не тронут"
            exit 0
        fi
    fi
    rm -rf .venv
    if ! "$py" -m venv .venv >/dev/null 2>&1; then
        say "шаг «venv на Python $floor» не удался"
        exit 0
    fi
    if ! .venv/bin/python -m pip install -q -e ".[dev]" >/dev/null 2>&1; then
        say "шаг «pip install -e .[dev]» не удался — .venv собран не целиком, в PATH не попадёт"
        exit 0
    fi
    printf '%s\n' "$want" > "$stamp"
    say "собрано на Python $floor"
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    printf 'export PATH="%s/.venv/bin:$PATH"\n' "$root" >> "$CLAUDE_ENV_FILE"
fi
exit 0
