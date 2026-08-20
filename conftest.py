"""conftest.py — корневой конфиг pytest.

src/-layout (Issue #35 / CLAUDE.md Sprint 8.2): исходники живут в
src/stepik_grader/, а не в корне репозитория. Явно добавляем src/ в
sys.path, чтобы `import stepik_grader` работал в тестах даже без
`pip install -e .` (хотя обычная разработка предполагает editable install —
см. CONTRIBUTING.md).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# issue #57: включаем встроенный плагин pytester, чтобы tests/test_pytest_plugin.py
# мог запускать вложенный pytest-процесс над временными файлами-решениями.
pytest_plugins = ["pytester"]


# issue #1278: раннер, объявивший медленный спавн, получает и больший таймаут
# теста. Переменная `STEPIK_GRADER_LAUNCH_TIMEOUT_S` поднимает порог НАШЕГО
# сторожа (`core/spawn.py`, `scripts/preflight.py`), но тесты, зовущие
# `subprocess.run` напрямую, до него не доходят: первым стреляет глобальный
# дедлайн pytest-timeout, который про медленный раннер ничего не знает. То есть
# окружение уже сказало «здесь спавн медленный», а до лимита, который реально
# срабатывает, объявление не доезжало.
def pytest_configure(config):
    """Масштабировать дедлайн теста тем же множителем, что и порог спавна.

    Множитель, а не константа: раннер, у которого один спавн вместо двадцати
    секунд занимает шестьдесят, медленный не для одного вызова, а для всех — а
    тест обычно порождает несколько процессов. Без переменной множитель равен
    единице, и дедлайн остаётся прежним.

    Порог берётся из `core/spawn.py`, а не повторяется здесь: два места с одним
    числом однажды разъезжаются, и разъедутся молча. Пакет недоступен (сломан
    импорт, урезанное окружение) — тихо ничего не меняем: конфиг pytest не то
    место, где стоит ронять весь набор.
    """
    try:
        from stepik_grader.core.spawn import DEFAULT_LAUNCH_TIMEOUT_S, launch_timeout_s
    except Exception:  # pragma: no cover - пакет недоступен, дедлайн остаётся прежним
        return

    factor = launch_timeout_s() / DEFAULT_LAUNCH_TIMEOUT_S
    current = getattr(config.option, "timeout", None)
    if factor <= 1 or not current:
        return
    config.option.timeout = current * factor
