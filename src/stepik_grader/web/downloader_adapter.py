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
from stepik_grader.core.stepik_client import read_step_id
from stepik_grader.core.storage import load_json_file, save_json_file
from stepik_grader.core.test_loader import load_test_cases

__all__ = [
    "DEFAULT_SECRETS_NAME",
    "download_task",
    "read_config",
    "read_step_id",
    "secrets_path_for",
    "write_config",
]

_INPUT_N_RE = re.compile(r"^input_\d+\.txt$")

DEFAULT_SECRETS_NAME = "secrets.json"


def _absolute(value: str, base: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _relative_if_inside(path: pathlib.Path, base: pathlib.Path) -> str:
    """Путь относительно ``base``, если он внутри — иначе абсолютный (для UI)."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def read_config(workspace: pathlib.Path) -> dict[str, Any]:
    """Конфиг загрузчика для web: куда скачивать и где лежит ``secrets.json``.

    Единственный источник истины — ``stepik_config.json`` в ``workspace``, тот
    же файл, что читает и пишет CLI (``downloader_config.py``). До issue #723
    web-слой был расщеплён: статус авторизации смотрел жёстко в
    ``workspace/secrets.json``, а скачивание — в ``secrets_path`` из конфига,
    поэтому панель могла показывать «Доступ активен» при неработающем
    скачивании (и наоборот).

    Возвращает ``{"root_dir", "secrets_path", "root_dir_default",
    "secrets_exists", "configured"}``; пути — строками, относительными к
    ``workspace``, когда лежат внутри него (так их показывает UI и принимает
    обратно). ``configured`` — существует ли сам ``stepik_config.json``.
    """
    root_dir_value = downloader.DEFAULT_ROOT_DIR
    secrets_value = DEFAULT_SECRETS_NAME
    config_path = workspace / downloader.CONFIG_FILE
    configured = config_path.exists()
    if configured:
        try:
            data = load_json_file(config_path)
        except (OSError, ValueError):
            data = {}
        root_dir_value = str(data.get("root_dir") or root_dir_value)
        secrets_value = str(data.get("secrets_path") or secrets_value)
    root_dir = _absolute(root_dir_value, workspace)
    secrets_path = _absolute(secrets_value, workspace)
    return {
        "root_dir": _relative_if_inside(root_dir, workspace),
        "secrets_path": _relative_if_inside(secrets_path, workspace),
        "root_dir_default": downloader.DEFAULT_ROOT_DIR,
        "secrets_exists": secrets_path.is_file(),
        "configured": configured,
    }


def write_config(
    workspace: pathlib.Path,
    *,
    root_dir: pathlib.Path | None = None,
    secrets_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Обновить ``stepik_config.json`` (issue #723/#725) и вернуть новый конфиг.

    Пишутся только переданные поля, остальные сохраняют текущее значение —
    чтобы правка корневой папки из web не стирала путь к ``secrets.json``,
    выставленный когда-то из CLI. Формат файла тот же, что у
    ``downloader_config.create_or_update_config``: абсолютные пути строками.
    """
    current = read_config(workspace)
    new_root = root_dir if root_dir is not None else _absolute(current["root_dir"], workspace)
    new_secrets = (
        secrets_path if secrets_path is not None else _absolute(current["secrets_path"], workspace)
    )
    save_json_file(
        workspace / downloader.CONFIG_FILE,
        {"root_dir": str(new_root), "secrets_path": str(new_secrets)},
    )
    return read_config(workspace)


def secrets_path_for(workspace: pathlib.Path) -> pathlib.Path:
    """Абсолютный путь к ``secrets.json`` по конфигу (для ``/api/auth/*``)."""
    return _absolute(read_config(workspace)["secrets_path"], workspace)


def _resolve_config(
    root_override: str | None, workspace: pathlib.Path
) -> tuple[pathlib.Path, pathlib.Path]:
    """(root_dir, secrets_path) без интерактивного создания/правки конфига.

    В отличие от ``downloader.load_or_create_config``/``normalize_config_paths``
    (обе зовут ``input()``), тихо использует дефолты
    (``downloader.DEFAULT_ROOT_DIR``, ``"secrets.json"``), если
    ``stepik_config.json`` отсутствует или его нельзя прочитать — ошибка
    (например, отсутствие ``secrets.json``) проявится позже как понятное
    сообщение в ``download_task``, а не как молчаливый interactive re-entry.
    """
    config = read_config(workspace)
    root_value = root_override or config["root_dir"]
    return _absolute(root_value, workspace), _absolute(config["secrets_path"], workspace)


def _detect_format(tests_dir: pathlib.Path) -> str:
    """Формат тест-кейсов по содержимому tests/ — post-hoc, не завязан на source."""
    if (tests_dir / "input.txt").exists() and (tests_dir / "output.txt").exists():
        return "python_generation"
    if any(_INPUT_N_RE.match(f.name) for f in tests_dir.iterdir() if f.is_file()):
        return "named"
    return "legacy"


def download_task(
    url: str, *, root: str | None = None, workspace: pathlib.Path | None = None
) -> dict[str, Any]:
    """Скачать задачу+тесты со Stepik по URL шага — режим #186 (раздел «Загрузчик задач»).

    Возвращает ``DownloadedTask`` (docs/dev/web-contracts.md): ``{"ok", "path", "files",
    "tests": {"count","source","format"}, "message"}``. ``ok=False`` — ошибка
    (нет secrets/OAuth/сеть/битый URL); ``ok=True`` с пустым ``tests`` и
    предупреждением в ``message`` — тесты не найдены (не ошибка, файлы задачи
    всё равно скачаны).

    ``workspace`` — рабочая директория сервера (``--root``), относительно неё
    читается ``stepik_config.json``; ``None`` — текущая директория процесса
    (issue #723: раньше конфиг всегда искался в cwd, из-за чего при
    ``--serve --root <dir>`` web брал не тот конфиг, что видел пользователь).
    """
    base = workspace if workspace is not None else pathlib.Path.cwd()
    root_dir, secrets_path = _resolve_config(root, base)

    try:
        secrets = load_secrets_dict(secrets_path)
    except (FileNotFoundError, IsADirectoryError, ValueError) as exc:
        return {
            "ok": False,
            "message": (
                f"Не найден или некорректен secrets.json ({exc}). "
                "См. docs/use/installation.md § Работа с API Stepik (OAuth)."
            ),
        }

    # issue #806: обновление токена ходит в сеть, а oauth_flow ловит только
    # HTTPError — ConnectionError/Timeout (и OSError при перезаписи secrets)
    # уходили из адаптера в обработчик HTTP-запроса, роняя его вместо
    # ``{"ok": false}``. Ответ обязан оставаться контрактным при любом сбое.
    try:
        session = try_create_session_without_browser(secrets, secrets_path)
    except requests.RequestException as exc:
        return {"ok": False, "message": f"Сетевая ошибка при обновлении токена Stepik: {exc}"}
    except OSError as exc:
        return {"ok": False, "message": f"Не удалось сохранить обновлённый токен: {exc}"}
    if session is None:
        return {
            "ok": False,
            "message": (
                "Нужна авторизация Stepik: токен недействителен, и обновить его "
                "не удалось без браузера. Выполните `python -m stepik_grader.downloader` "
                "в терминале один раз для входа через браузер, затем повторите здесь. "
                "См. docs/use/installation.md § Работа с API Stepik (OAuth)."
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

    path_str = _relative_if_inside(task_dir, base)

    message = "" if real_count > 0 else "⚠️ Тесты не найдены — файлы задачи скачаны, tests/ пуста."
    return {
        "ok": True,
        "path": path_str,
        "files": files,
        "tests": {"count": real_count, "source": source if real_count else "none", "format": fmt},
        "message": message,
    }
