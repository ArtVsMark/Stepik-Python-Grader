#!/usr/bin/env python3
"""scripts/generate_release_badge.py — бейдж «релиз и PyPI сошлись» (issue #1063).

Проблема, которую он решает. В README было три бейджа про версию, и два из них —
``Release`` (последний git-тег) и ``PyPI`` (опубликованный пакет) — показывали
одно и то же число, потому что публикация идёт по тегу. Выглядели они
дублирующими, но дублирующими были только **пока всё хорошо**: их смысл
проявляется в момент расхождения — тег ``v1.11.0`` уже есть, а на PyPI всё ещё
``1.10.0``, значит публикация упала и пользователи ставят старое. Заметить это
можно было, лишь сравнив две картинки глазами и вспомнив, что они обязаны
совпадать.

Этот бейдж сравнивает их сам и остаётся один вместо двух:

* сошлись — зелёный ``1.10``;
* разошлись — красный ``1.11 ≠ pypi 1.10``, то есть сломанная публикация видна
  из шапки README;
* PyPI недоступен — серый ``1.10 · pypi ?``: проверка не выполнена, и бейдж
  говорит об этом прямо, вместо того чтобы зеленеть на нехватке данных.

Почему ``1.10``, а не ``1.10.0``. По схеме проекта (docs/dev/versioning.md) теги
бывают только ``vX.Y.0``: MINOR растёт на тег, а PATCH считается отдельно и в
тег не попадает. Ноль в конце — не патч, а константа, и место в шапке он занимал
зря. Текущий счётчик изменений показывает соседний бейдж ``version``
(``scripts/generate_version_badge.py``).

Запуск::

    python scripts/generate_release_badge.py [--out PATH] [--offline]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import urllib.error
import urllib.request
from collections.abc import Callable

__all__ = [
    "PYPI_PROJECT_URL",
    "build_badge_payload",
    "fetch_pypi_version",
    "latest_release_tag",
    "load_latest_release_tag_fn",
    "main",
    "short_version",
]

PYPI_PROJECT_URL = "https://pypi.org/pypi/stepik-python-grader/json"

_REQUEST_TIMEOUT_SECONDS = 10

_VERSION_SCRIPT = pathlib.Path(__file__).resolve().parent / "version.py"


def load_latest_release_tag_fn() -> Callable[[], str | None]:
    """Загрузить ``latest_release_tag()`` из ``scripts/version.py`` по пути.

    ``scripts/`` — не пакет (нет ``__init__.py``), поэтому обычный import не
    сработает; тот же приём, которым `generate_version_badge.py` берёт оттуда
    ``project_version``.

    Определение «релизного тега» живёт в одном месте намеренно. Оно неочевидно
    (рядом с релизными в репозитории есть служебные теги вроде
    ``v-checkpoint-2026-06-24``, подходящие под наивное ``v*``), и вторая копия
    маски разъехалась бы с первой при первом же изменении схемы — а разъехавшись,
    бейдж показал бы «расхождение с PyPI» на ровном месте, то есть соврал бы
    ровно тем сигналом, ради которого заведён.
    """
    spec = importlib.util.spec_from_file_location("_project_version_module", _VERSION_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.latest_release_tag  # type: ignore[no-any-return]


def latest_release_tag() -> str | None:
    """Последний релизный тег (``v1.10.0``) или ``None``, если такого нет.

    ``None`` — нормальное состояние клона без тегов: так клонирует облачная
    сессия и так ведёт себя ``actions/checkout`` без ``fetch-depth: 0``.
    Вызывающая сторона обязана отличать это от «тег есть, но не совпал».
    """
    return load_latest_release_tag_fn()()


def fetch_pypi_version(url: str = PYPI_PROJECT_URL) -> str | None:
    """Версия последнего релиза пакета на PyPI или ``None``, если узнать не вышло.

    Сеть, недоступный индекс и сменившаяся форма ответа дают один и тот же
    ``None``: бейдж на нехватке данных обязан честно сказать «не проверено», а
    не выдать зелёный.
    """
    try:
        with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    info = payload.get("info") if isinstance(payload, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) and version else None


def short_version(version: str) -> str:
    """Убрать из версии всегда нулевой PATCH: ``v1.10.0`` → ``1.10``.

    Не трогает то, что на схему не похоже: предрелизные и dev-версии
    (``1.10.0.post3``, ``1.11.0rc1``) остаются как есть — иначе бейдж показал бы
    неправду именно в тот момент, когда на PyPI лежит не то, что ожидается.
    """
    trimmed = version.lstrip("v")
    parts = trimmed.split(".")
    if len(parts) == 3 and parts[2] == "0" and all(part.isdigit() for part in parts):
        return f"{parts[0]}.{parts[1]}"
    return trimmed


def build_badge_payload(tag: str | None, pypi_version: str | None) -> dict[str, str | int]:
    """Собрать shields.io endpoint badge по паре «тег, версия на PyPI».

    Три исхода — по одному на каждое состояние, которое стоит различать:
    сошлись, разошлись, проверить не удалось.
    """
    label = "release/pypi"

    if tag is None and pypi_version is None:
        return {"schemaVersion": 1, "label": label, "message": "неизвестно", "color": "lightgrey"}

    if pypi_version is None:
        return {
            "schemaVersion": 1,
            "label": label,
            "message": f"{short_version(tag or '')} · pypi ?",
            "color": "lightgrey",
        }

    if tag is None:
        # Тегов не видно — это клон без них, а не расхождение: красным пугать не за что.
        return {
            "schemaVersion": 1,
            "label": label,
            "message": f"pypi {short_version(pypi_version)}",
            "color": "lightgrey",
        }

    if tag.lstrip("v") == pypi_version:
        return {
            "schemaVersion": 1,
            "label": label,
            "message": short_version(tag),
            "color": "brightgreen",
        }

    # Подпись без слов: бейдж стоит и в русском README, и в английской витрине
    # (README.en.md), а «≠» одинаково читается в обеих.
    return {
        "schemaVersion": 1,
        "label": label,
        "message": f"{short_version(tag)} ≠ pypi {short_version(pypi_version)}",
        "color": "red",
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path(".github/badges/release.json"),
        help="Куда записать shields.io endpoint JSON.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Не ходить в PyPI (бейдж скажет, что проверка не выполнена).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Точка входа: сверить тег с PyPI, записать badge JSON, напечатать сводку."""
    args = _build_arg_parser().parse_args(argv)

    tag = latest_release_tag()
    pypi_version = None if args.offline else fetch_pypi_version()
    payload = build_badge_payload(tag, pypi_version)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Release badge written to {args.out}: {payload['message']}")


if __name__ == "__main__":
    main()
