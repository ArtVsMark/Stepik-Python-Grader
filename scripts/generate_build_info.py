#!/usr/bin/env python3
"""scripts/generate_build_info.py — положить в пакет ``_build_info.json`` (issue #1262).

Что и зачем. В окне лаунчера человеку нужна логическая версия проекта
(``1.10.234`` — «234 принятых изменения после тега ``v1.10.0``»), та же, что в
бейдже README. Считается она по git-истории, а в установленном через pipx пакете
истории нет; дёргать git при старте окна нельзя (на macOS + 3.14 git-подпроцесс
подвисает на десятки секунд, issue #1166/#1149). Значит версия считается **один
раз при сборке** — этим скриптом — и уезжает в колесо файлом, который
``stepik_grader.build_info`` просто читает.

Полная PEP 440-версия и хеш кладутся рядом: логическая форма отвечает человеку,
эти две — машине (сопоставление сборки с коммитом в отчёте о проблеме).

Запуск (в CI — перед ``python -m build``)::

    python scripts/generate_build_info.py            # в src/stepik_grader/
    python scripts/generate_build_info.py --print    # показать и не писать

Файл в репозиторий не коммитится (он в ``.gitignore``): это артефакт сборки, а
не исходник — иначе в git лежала бы версия, устаревшая на каждый следующий PR.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import contextlib

from version import latest_release_tag, project_version

# issue #1394: консоль Windows работает в cp1251/cp866, и печать символов вне
# этой кодировки роняет скрипт `UnicodeEncodeError` прямо в CI-джобе.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["build_info", "main", "write_build_info"]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TARGET = _ROOT / "src" / "stepik_grader" / "_build_info.json"


def _git(*args: str) -> str | None:
    """``git`` в корне репозитория; ``None`` — git недоступен или ответил ошибкой.

    Кодировка задана явно: ``text=True`` без неё берёт локальную кодовую
    страницу, а под Windows это cp1251 — темы коммитов проекта по-русски, и
    чтение падало бы ``UnicodeDecodeError``. ``errors="replace"`` — битый символ
    в выводе не должен ронять сборку (issue #1042, тот же разбор в
    ``scripts/version.py``).
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _scm_options() -> dict[str, Any]:
    """Настройки ``[tool.setuptools_scm]`` из ``pyproject.toml`` (пусто — нет секции).

    Отдаются как ``Any``, а не ``str``: значения уходят в ``get_version`` через
    ``**``, и её сигнатура для разных ключей ждёт разные типы.
    """
    try:
        import tomllib

        with (_ROOT / "pyproject.toml").open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, ValueError, ImportError):
        return {}
    section = config.get("tool", {}).get("setuptools_scm", {})
    return {k: v for k, v in section.items() if isinstance(v, str)}


def _pep440_version() -> str:
    """Версия PEP 440 от ``setuptools-scm``; пусто — вычислить нечем.

    Порядок источников не случаен. В релизном job'е пакет НЕ установлен —
    ``python -m build`` собирает его из исходников, — поэтому
    ``importlib.metadata`` там ничего не знает, и первым спрашивается сам
    ``setuptools_scm``: он и есть тот, кто вычислит версию через минуту при
    сборке. Метаданные остаются запасным путём для окружения, где пакет уже
    поставлен (``pip install -e .`` у разработчика).
    """
    try:
        import setuptools_scm

        # Настройки берутся из pyproject.toml, а не повторяются здесь: иначе
        # файл сборки объявлял бы одну схему (`guess-next-dev` по умолчанию),
        # а колесо через минуту собиралось бы по другой (`post-release`), и два
        # числа в одном JSON расходились бы молча.
        return str(setuptools_scm.get_version(root=str(_ROOT), **_scm_options()))
    except Exception:
        pass
    try:
        import importlib.metadata

        return importlib.metadata.version("stepik-python-grader")
    except Exception:
        return ""


def _is_released(commit: str) -> bool:
    """Стоит ли HEAD ровно на релизном теге ``vX.Y.0``."""
    tag = latest_release_tag()
    if tag is None or not commit:
        return False
    tagged = _git("rev-list", "-n", "1", tag)
    return bool(tagged) and tagged == commit


def build_info() -> dict[str, object]:
    """Собрать содержимое ``_build_info.json``."""
    commit = _git("rev-parse", "HEAD") or ""
    return {
        "version": project_version(),
        "pep440": _pep440_version(),
        "commit": commit[:9],
        "released": _is_released(commit),
    }


def write_build_info(
    target: pathlib.Path = _TARGET, info: dict[str, object] | None = None
) -> dict[str, object]:
    """Записать файл сборки; вернуть то, что записано."""
    data = build_info() if info is None else info
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    """Записать (или показать) сведения о сборке; 0 — успех."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--print", action="store_true", help="показать и не писать файл")
    parser.add_argument("--out", type=pathlib.Path, default=_TARGET, help="куда писать")
    args = parser.parse_args(argv)

    if args.print:
        print(json.dumps(build_info(), ensure_ascii=False, indent=2))
        return 0

    info = build_info()
    # Пустое значение — «посчитать не удалось», и молча записывать его нельзя:
    # файл уедет в колесо, окно покажет пустоту, а узнаем мы об этом от
    # пользователя. Правило проекта: пустой вход обязан быть красным.
    missing = [key for key in ("version", "pep440") if not str(info.get(key) or "").strip()]
    if missing:
        print(
            f"не удалось вычислить: {', '.join(missing)}. Нужны git-история с тегами "
            "(fetch-depth: 0) и setuptools-scm — файл сборки не записан",
            file=sys.stderr,
        )
        return 1

    write_build_info(args.out, info)
    print(f"{args.out}: версия {info['version']}, релиз={info['released']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
