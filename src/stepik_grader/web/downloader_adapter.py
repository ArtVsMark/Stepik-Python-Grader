"""downloader_adapter.py — тонкий web-адаптер над downloader.py (issue #186).

Архитектурный слой: Application/UI (web-адаптер), как и ``viewmodels.py``/
``glossary_adapter.py``. Никакой новой бизнес-логики скачивания — только
пас-through над уже готовыми ``downloader.process_step_url``/
``core.oauth_flow``/``core.test_loader.load_test_cases``. DAG: ``web →
downloader → core`` ациклично.

Отличие от CLI-пути (``downloader.main()``): все интерактивные функции
(``load_or_create_config``/``create_or_update_config``/``normalize_config_paths``
— все зовут ``input()``) здесь не используются — веб-сервер не может ждать
консольный ввод. Конфиг читается напрямую с тихими дефолтами; OAuth —
только через ``core.oauth_flow.try_create_session_without_browser`` (никогда
не открывает браузер/не блокирует поток — см. её docstring).
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import requests

from stepik_grader import downloader
from stepik_grader.core.oauth_flow import load_secrets_dict, try_create_session_without_browser
from stepik_grader.core.storage import load_json_file
from stepik_grader.core.test_loader import load_test_cases

__all__ = ["download_task"]

_INPUT_N_RE = re.compile(r"^input_\d+\.txt$")


def _absolute(value: str, base: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _resolve_config(root_override: str | None) -> tuple[pathlib.Path, pathlib.Path]:
    """(root_dir, secrets_path) без интерактивного создания/правки конфига.

    В отличие от ``downloader.load_or_create_config``/``normalize_config_paths``
    (обе зовут ``input()``), тихо использует дефолты
    (``downloader.DEFAULT_ROOT_DIR``, ``"secrets.json"``), если
    ``stepik_config.json`` отсутствует или его нельзя прочитать — ошибка
    (например, отсутствие ``secrets.json``) проявится позже как понятное
    сообщение в ``download_task``, а не как молчаливый interactive re-entry.
    """
    cwd = pathlib.Path.cwd()
    root_dir_value = downloader.DEFAULT_ROOT_DIR
    secrets_value = "secrets.json"
    config_path = cwd / downloader.CONFIG_FILE
    if config_path.exists():
        try:
            data = load_json_file(config_path)
        except (OSError, ValueError):
            data = {}
        root_dir_value = str(data.get("root_dir") or root_dir_value)
        secrets_value = str(data.get("secrets_path") or secrets_value)
    if root_override:
        root_dir_value = root_override
    return _absolute(root_dir_value, cwd), _absolute(secrets_value, cwd)


def _detect_format(tests_dir: pathlib.Path) -> str:
    """Формат тест-кейсов по содержимому tests/ — post-hoc, не завязан на source."""
    if (tests_dir / "input.txt").exists() and (tests_dir / "output.txt").exists():
        return "python_generation"
    if any(_INPUT_N_RE.match(f.name) for f in tests_dir.iterdir() if f.is_file()):
        return "named"
    return "legacy"


def download_task(url: str, *, root: str | None = None) -> dict[str, Any]:
    """Скачать задачу+тесты со Stepik по URL шага — режим #186 (раздел «Загрузчик задач»).

    Возвращает ``DownloadedTask`` (docs/web-current.md): ``{"ok", "path", "files",
    "tests": {"count","source","format"}, "message"}``. ``ok=False`` — ошибка
    (нет secrets/OAuth/сеть/битый URL); ``ok=True`` с пустым ``tests`` и
    предупреждением в ``message`` — тесты не найдены (не ошибка, файлы задачи
    всё равно скачаны).
    """
    root_dir, secrets_path = _resolve_config(root)

    try:
        secrets = load_secrets_dict(secrets_path)
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        return {
            "ok": False,
            "message": (
                f"Не найден или некорректен secrets.json ({exc}). "
                "См. docs/installation.md § Работа с API Stepik (OAuth)."
            ),
        }

    session = try_create_session_without_browser(secrets, secrets_path)
    if session is None:
        return {
            "ok": False,
            "message": (
                "Нужна авторизация Stepik: токен недействителен, и обновить его "
                "не удалось без браузера. Выполните `python -m stepik_grader.downloader` "
                "в терминале один раз для входа через браузер, затем повторите здесь. "
                "См. docs/installation.md § Работа с API Stepik (OAuth)."
            ),
        }

    root_dir.mkdir(parents=True, exist_ok=True)
    try:
        task_dir, _count, source = downloader.process_step_url(url, session, root_dir)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    except requests.RequestException as exc:
        return {"ok": False, "message": f"Сетевая ошибка при обращении к Stepik: {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"Непредвиденная ошибка: {exc}"}

    files = sorted(p.name for p in task_dir.iterdir() if p.is_file())
    tests_dir = task_dir / "tests"
    fmt = _detect_format(tests_dir) if tests_dir.is_dir() else "legacy"
    # count — независимая перепроверка тем же кодом, что реально грейдит
    # (устойчивее к любому будущему расхождению, чем просто доверять save_task_files).
    real_count = len(load_test_cases(tests_dir)) if tests_dir.is_dir() else 0

    try:
        path_str = str(task_dir.relative_to(pathlib.Path.cwd()))
    except ValueError:
        path_str = str(task_dir)

    message = "" if real_count > 0 else "⚠️ Тесты не найдены — файлы задачи скачаны, tests/ пуста."
    return {
        "ok": True,
        "path": path_str,
        "files": files,
        "tests": {"count": real_count, "source": source if real_count else "none", "format": fmt},
        "message": message,
    }
