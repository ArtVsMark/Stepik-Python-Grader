"""downloader.py — бизнес-логика: конфиг, файловая система, оркестрация.

Архитектурный слой: Domain / Application.
Отвечает за:
  - управление конфигом (stepik_config.json) и secrets.json,
  - разбор URL шага Stepik,
  - построение директорий задач (slugify, build_task_directory),
  - сохранение файлов задачи (task{N}_1.py, task{N}_2.py, solution.py, meta.json, task.md),
  - извлечение тест-кейсов из HTML-таблицы в тексте задачи,
  - скачивание тестов из ZIP- или GitHub-ссылок если таблица не полная/отсутствует,
  - оркестрацию вызовов к Stepik API через stepik_client.

HTTP/OAuth логика вынесена в stepik_client.py.
Файловый I/O вынесен в storage.py.

Схема именования рабочих файлов:
  task{step_position}_1.py  — основное решение (заполняется из template_code)
  task{step_position}_2.py  — заглушка для альтернативного решения 1
  task{step_position}_3.py  — (добавляется вручную) альтернативное решение 2
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import zipfile
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from core.oauth_flow import create_user_session, load_secrets_dict
from core.stepik_client import (
    fetch_course_data,
    fetch_lesson_data,
    fetch_section_data,
    fetch_step_data,
    fetch_submission_data,
    fetch_unit_data,
)
from core.storage import load_json_file, save_json_file

CONFIG_FILE = "stepik_config.json"

DEFAULT_ROOT_DIR = "StepikTasks"


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Преобразует текст в slug для имени директории. Макс 80 символов."""
    text = text.strip().lower().replace("ё", "е")
    text = re.sub(r'[<>:"/\\|?*]+', "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[-\s]+", "-", text, flags=re.UNICODE)
    text = text.strip(".- ")
    return text[:80] or "task"


def ask_value(prompt: str, default: str = "") -> str:
    """Запрашивает значение у пользователя с опциональным дефолтом."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------


def create_or_update_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Интерактивно создаёт или перезаписывает stepik_config.json."""
    print("\nНастройка конфигурации...")
    root_dir = ask_value(
        "Укажи корневую папку для всех задач Stepik",
        DEFAULT_ROOT_DIR,
    )
    secrets_path = ask_value("Укажи путь к secrets.json", "secrets.json")
    config: dict[str, Any] = {"root_dir": root_dir, "secrets_path": secrets_path}
    save_json_file(config_path, config)
    print(f"✅ Конфиг сохранён: {config_path.resolve()}")
    return config


def load_or_create_config(config_path: pathlib.Path) -> dict[str, Any]:
    """Загружает конфиг; если не существует — запускает интерактивное создание."""
    if not config_path.exists():
        print("⚠️ Конфиг не найден. Будет создан новый.")
        return create_or_update_config(config_path)
    config = load_json_file(config_path)
    print("\nТекущая конфигурация:")
    print(f"root_dir:     {config.get('root_dir', '')}")
    print(f"secrets_path: {config.get('secrets_path', '')}")
    change = input("Нужно изменить настройку? [y/N]: ").strip().lower()
    if change in {"y", "yes", "д", "да"}:
        return create_or_update_config(config_path)
    return config


def normalize_config_paths(
    config: dict[str, Any],
    config_path: pathlib.Path,
) -> dict[str, Any]:
    """Нормализует пути в конфиге до абсолютных; повторно запрашивает при ошибках."""
    root_dir_value = str(config.get("root_dir", "")).strip()
    secrets_value = str(config.get("secrets_path", "")).strip()
    if not root_dir_value or not secrets_value:
        print("⚠️ В конфиге не хватает обязательных полей.")
        config = create_or_update_config(config_path)
        root_dir_value = str(config["root_dir"]).strip()
        secrets_value = str(config["secrets_path"]).strip()
    root_dir = pathlib.Path(root_dir_value)
    secrets_path = pathlib.Path(secrets_value)
    if not root_dir.is_absolute():
        root_dir = pathlib.Path.cwd() / root_dir
    if not secrets_path.is_absolute():
        secrets_path = pathlib.Path.cwd() / secrets_path
    if not secrets_path.exists() or not secrets_path.is_file():
        print(f"⚠️ Файл secrets не найден: {secrets_path}")
        config = create_or_update_config(config_path)
        root_dir = pathlib.Path(str(config["root_dir"]))
        secrets_path = pathlib.Path(str(config["secrets_path"]))
        if not root_dir.is_absolute():
            root_dir = pathlib.Path.cwd() / root_dir
        if not secrets_path.is_absolute():
            secrets_path = pathlib.Path.cwd() / secrets_path
    normalized: dict[str, Any] = {
        "root_dir": str(root_dir),
        "secrets_path": str(secrets_path),
    }
    save_json_file(config_path, normalized)
    return normalized


# ---------------------------------------------------------------------------
# URL-парсинг
# ---------------------------------------------------------------------------


def parse_stepik_step_url(step_url: str) -> tuple[int, int]:
    """Извлекает (lesson_id, step_position) из URL шага Stepik."""
    parsed = urlparse(step_url.strip())
    match = re.search(r"lesson/(\d+)/step/(\d+)", parsed.path)
    if not match:
        raise ValueError(
            "Не удалось распознать URL шага. Ожидается формат:\n"
            "https://stepik.org/lesson/569749/step/4?unit=564263"
        )
    return int(match.group(1)), int(match.group(2))


# ---------------------------------------------------------------------------
# Извлечение кода
# ---------------------------------------------------------------------------


def extract_python_code(step: dict[str, Any]) -> str | None:
    """Извлекает Python code_template из объекта шага или из блока Markdown."""
    block: dict[str, Any] = step.get("block") or {}
    for option in block.get("options") or []:
        if isinstance(option, dict) and option.get("code_template"):
            return str(option["code_template"])
    text = str(block.get("text", ""))
    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def extract_submission_code(submission: dict[str, Any] | None) -> str | None:
    """Извлекает Python-код из объекта последнего сабмишна или возвращает None."""
    if not submission:
        return None
    reply: dict[str, Any] = submission.get("reply") or {}
    code = reply.get("code")
    return str(code) if code else None


# ---------------------------------------------------------------------------
# Извлечение имени функции из шаблона кода
# ---------------------------------------------------------------------------


def extract_function_name(template_code: str) -> str | None:
    """Парсит template_code через ast и возвращает имя первой функции.

    Возвращает None если в шаблоне нет определений функций или код
    не является валидным Python.
    """
    try:
        tree = ast.parse(template_code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None


# ---------------------------------------------------------------------------
# Извлечение тест-кейсов из HTML-таблицы
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    """Вытаскивает текст из <td> ячеек HTML-таблицы построчно."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_th: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag == "td":
            self._current_cell = []
        elif tag == "th":
            self._in_th = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._current_cell is not None:
            cell_text = "".join(self._current_cell).strip()
            if self._current_row is not None:
                self._current_row.append(cell_text)
            self._current_cell = None
        elif tag == "th":
            self._in_th = False
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self._rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None and not self._in_th:
            self._current_cell.append(data)

    @property
    def rows(self) -> list[list[str]]:
        return self._rows


def _is_function_style(input_text: str) -> bool:
    """Возвращает True если входные данные — объявление переменных (function-mode).

    Использует AST-анализ вместо regex-эвристики:
      - парсит input_text через ast.parse
      - если на верхнем уровне есть вызов функции (print, input и т.п.) — это stdin-режим
      - если есть хотя бы одно присваивание и нет вызовов на верхнем уровне — function-mode

    Примеры function-mode (→ True):
        date1 = date(2021, 11, 1)
        date2 = date(2021, 11, 22)

    Примеры stdin-mode (→ False):
        date1 = date(2021, 11, 1)
        print(my_func(date1))          ← вызов на верхнем уровне

        n = int(input())               ← вызов input()
    """
    stripped = input_text.strip()
    if not stripped:
        return False
    try:
        tree = ast.parse(stripped)
    except SyntaxError:
        # Если не парсится — не можем определить режим, считаем stdin
        return False

    has_assignment = False
    for node in tree.body:
        # Вызов функции на верхнем уровне (print, input, my_func(...)) → stdin-режим
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return False
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            has_assignment = True

    return has_assignment


def extract_tests_from_html(html: str) -> list[tuple[str, str, str]]:
    """Парсит HTML-таблицу тест-кейсов Stepik.

    Возвращает список троек (input_data, expected_output, test_type).
    test_type: "stdin" | "function".
    Пустой список если таблица не найдена или в ней < 3 колонок.
    """
    parser = _TableParser()
    parser.feed(html)
    tests: list[tuple[str, str, str]] = []
    for row in parser.rows:
        if len(row) < 3:  # noqa: PLR2004
            continue
        input_data = row[1].strip()
        expected = row[2].strip()
        if not input_data or not expected:
            continue
        test_type = "function" if _is_function_style(input_data) else "stdin"
        tests.append((input_data, expected, test_type))
    return tests


def save_tests(task_dir: pathlib.Path, tests: list[tuple[str, str, str]]) -> int:
    """Записывает тест-кейсы в tests/N, tests/N.clue, tests/N.type.

    Возвращает количество сохранённых тестов.
    """
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for i, (input_data, expected, test_type) in enumerate(tests, start=1):
        (tests_dir / str(i)).write_text(input_data, encoding="utf-8")
        (tests_dir / f"{i}.clue").write_text(expected, encoding="utf-8")
        if test_type == "function":
            (tests_dir / f"{i}.type").write_text("function", encoding="utf-8")
    return len(tests)


# ---------------------------------------------------------------------------
# Скачивание тестов из внешнего источника (ZIP / GitHub)
# ---------------------------------------------------------------------------

_ZIP_URL_RE = re.compile(r'href=["\']([^"\']*\.zip)["\']', re.IGNORECASE)
_GITHUB_URL_RE = re.compile(r'href=["\']([^"\']*github\.com[^"\']*)["\']', re.IGNORECASE)
_GITHUB_TREE_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:tree|blob)/(?P<branch>[^/]+)/(?P<path>.+)"
)
_GITHUB_CONTENTS_API = "https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"


def extract_external_test_links(html: str) -> tuple[list[str], list[str]]:
    """Извлекает ZIP- и GitHub-ссылки из HTML текста задачи.

    Возвращает кортеж (zip_links, github_links) без дубликатов.
    """

    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    zip_links = _unique(_ZIP_URL_RE.findall(html))
    github_links = _unique(_GITHUB_URL_RE.findall(html))
    return zip_links, github_links


def _download_zip_tests(
    task_dir: pathlib.Path,
    zip_url: str,
    session: requests.Session,
) -> int:
    """Скачивает ZIP со Stepik и конвертирует в Format 3 (input.txt + output.txt).

    Ожидает ZIP с файлами: 1, 1.clue, 2, 2.clue, ... (формат Stepik).
    Конвертирует в единый формат # TEST_N: совместимый с grader Format 3.
    Возвращает количество тест-кейсов.
    """
    try:
        response = session.get(zip_url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ⚠️ Не удалось скачать ZIP: {zip_url} ({exc})")
        return 0

    try:
        zf = zipfile.ZipFile(io.BytesIO(response.content))
    except zipfile.BadZipFile:
        print(f"  ⚠️ Скачанный файл не является ZIP: {zip_url}")
        return 0

    # Убираем общий prefix-каталог если он есть (e.g. "tests/1" → "1")
    names = zf.namelist()
    strip_prefix = ""
    for name in names:
        if "/" in name:
            strip_prefix = name.split("/")[0] + "/"
            break

    # Собираем пары N → (input_bytes, clue_bytes)
    pairs: dict[int, dict[str, bytes]] = {}
    for name in names:
        clean = (
            name[len(strip_prefix) :] if strip_prefix and name.startswith(strip_prefix) else name
        )
        clean = clean.strip("/")
        if not clean:
            continue
        if clean.isdigit():
            idx = int(clean)
            pairs.setdefault(idx, {})["input"] = zf.read(name)
        elif "." in clean:
            stem, ext = clean.rsplit(".", 1)
            if stem.isdigit() and ext == "clue":
                idx = int(stem)
                pairs.setdefault(idx, {})["clue"] = zf.read(name)

    if not pairs:
        print(f"  ⚠️ В ZIP не найдены файлы формата N / N.clue: {zip_url}")
        return 0

    # Строим input.txt и output.txt с маркерами # TEST_N:
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    input_lines = ["# INPUT DATA:\n"]
    output_lines = ["# OUTPUT DATA:\n"]

    for idx in sorted(pairs.keys()):
        pair = pairs[idx]
        inp_text = pair.get("input", b"").decode("utf-8", errors="replace").rstrip("\n")
        clue_text = pair.get("clue", b"").decode("utf-8", errors="replace").rstrip("\n")
        input_lines.append(f"\n# TEST_{idx}:\n{inp_text}\n")
        output_lines.append(f"\n# TEST_{idx}:\n{clue_text}\n")

    (tests_dir / "input.txt").write_text("".join(input_lines), encoding="utf-8")
    (tests_dir / "output.txt").write_text("".join(output_lines), encoding="utf-8")

    count = len(pairs)
    print(f"  📦 ZIP сконвертирован в Format 3: {count} тест(ов) → tests/input.txt + output.txt")
    return count


def _download_github_tests(
    task_dir: pathlib.Path,
    gh_url: str,
    session: requests.Session,
) -> int:
    """Скачать тесты с GitHub через API содержимого репозитория.

    Поддерживает два формата:
    1. Директория с input.txt + output.txt (Format 3) — скачивается напрямую
    2. Директория с числовыми файлами N + N.clue — конвертируется в Format 3

    Возвращает количество тест-кейсов (0 при ошибке).
    """
    match = _GITHUB_TREE_RE.search(gh_url)
    if not match:
        print(f"  ⚠️ Не удалось распознать GitHub URL: {gh_url}")
        return 0

    owner = match.group("owner")
    repo = match.group("repo")
    branch = match.group("branch")
    path = match.group("path").rstrip("/")

    api_url = _GITHUB_CONTENTS_API.format(owner=owner, repo=repo, path=path, branch=branch)

    try:
        resp = session.get(api_url, timeout=30, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        contents = resp.json()
    except requests.RequestException as exc:
        print(f"  ⚠️ GitHub API недоступен: {exc}")
        return 0

    if not isinstance(contents, list):
        print(f"  ⚠️ GitHub API вернул не список файлов: {gh_url}")
        return 0

    tests_dir = task_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    file_map = {
        item["name"]: item["download_url"] for item in contents if item.get("type") == "file"
    }

    # Вариант А: input.txt + output.txt уже есть (Format 3)
    if "input.txt" in file_map and "output.txt" in file_map:
        for fname in ("input.txt", "output.txt"):
            r = session.get(file_map[fname], timeout=30)
            r.raise_for_status()
            (tests_dir / fname).write_bytes(r.content)
        from grader import _parse_testblock_file

        text = (tests_dir / "input.txt").read_text(encoding="utf-8")
        count = len(_parse_testblock_file(text))
        print(f"  🔗 GitHub: скачаны input.txt + output.txt (Format 3): {count} тест(ов)")
        return count

    # Вариант Б: числовые файлы N + N.clue
    pairs: dict[int, dict[str, str]] = {}
    for fname, url in file_map.items():
        if fname.isdigit():
            pairs.setdefault(int(fname), {})["input_url"] = url
        elif "." in fname:
            stem, ext = fname.rsplit(".", 1)
            if stem.isdigit() and ext == "clue":
                pairs.setdefault(int(stem), {})["clue_url"] = url

    if not pairs:
        print(f"  ⚠️ GitHub: файлы не распознаны в {gh_url}")
        return 0

    input_lines = ["# INPUT DATA:\n"]
    output_lines = ["# OUTPUT DATA:\n"]
    for idx in sorted(pairs.keys()):
        pair = pairs[idx]
        inp_text = ""
        clue_text = ""
        if "input_url" in pair:
            inp_text = session.get(pair["input_url"], timeout=30).text.rstrip("\n")
        if "clue_url" in pair:
            clue_text = session.get(pair["clue_url"], timeout=30).text.rstrip("\n")
        input_lines.append(f"\n# TEST_{idx}:\n{inp_text}\n")
        output_lines.append(f"\n# TEST_{idx}:\n{clue_text}\n")

    (tests_dir / "input.txt").write_text("".join(input_lines), encoding="utf-8")
    (tests_dir / "output.txt").write_text("".join(output_lines), encoding="utf-8")
    count = len(pairs)
    print(f"  🔗 GitHub: сконвертировано {count} тест(ов) → Format 3")
    return count


# ---------------------------------------------------------------------------
# Построение директорий и сохранение файлов задачи
# ---------------------------------------------------------------------------


def build_task_directory(
    root_dir: pathlib.Path,
    course_title: str,
    section_title: str,
    lesson_title: str,
    step_position: int,
    step_title: str,
) -> pathlib.Path:
    """Строит путь к директории задачи по иерархии курс/секция/урок/шаг."""
    step_dir_name = f"{step_position:02d}"
    if step_title.strip():
        step_dir_name = f"{step_position:02d}-{slugify(step_title)}"

    parts = [
        slugify(course_title),
        slugify(section_title),
        slugify(lesson_title),
        step_dir_name,
    ]
    return root_dir.joinpath(*parts)


def save_task_files(
    task_dir: pathlib.Path,
    step: dict[str, Any],
    submission: dict[str, Any] | None,
    lesson: dict[str, Any],
    section: dict[str, Any],
    course: dict[str, Any],
    session: requests.Session,
) -> None:
    """Сохраняет рабочие файлы, solution.py, meta.json, task.md и tests/ в task_dir.

    Схема рабочих файлов:
      task{pos}_1.py  — основное решение (из template_code или пустая заглушка)
      task{pos}_2.py  — заглушка для альтернативного решения 1 (всегда создаётся)

    Порядок поиска тестов:
      1. ZIP-ссылка в HTML (скачать и сконвертировать в Format 3);
      2. HTML-таблица в тексте задачи;
      3. Ссылка на GitHub (скачать через GitHub Contents API);
      4. Ничего нет — предупреждение, остальные файлы уже сохранены.
    """
    task_dir.mkdir(parents=True, exist_ok=True)

    template_code = extract_python_code(step)
    submitted_code = extract_submission_code(submission)
    step_position = int(step.get("position") or 0)

    # Рабочие файлы: task{pos}_1.py и task{pos}_2.py
    main_file = task_dir / f"task{step_position}_1.py"
    alt_file = task_dir / f"task{step_position}_2.py"

    main_content = template_code if template_code else ""
    main_file.write_text(main_content, encoding="utf-8")

    if not alt_file.exists():
        alt_file.write_text("", encoding="utf-8")

    if submitted_code:
        (task_dir / "solution.py").write_text(submitted_code, encoding="utf-8")

    # Определяем имя функции из template_code для function-mode runner
    function_name: str | None = None
    if template_code:
        function_name = extract_function_name(template_code)

    meta: dict[str, Any] = {
        "step_id": step.get("id"),
        "step_position": step.get("position"),
        "step_title": step.get("title", ""),
        "lesson_id": lesson.get("id"),
        "lesson_title": lesson.get("title", ""),
        "section_id": section.get("id"),
        "section_title": section.get("title", ""),
        "course_id": course.get("id"),
        "course_title": course.get("title", ""),
        "submission_id": submission.get("id") if submission else None,
        "submission_status": submission.get("status") if submission else None,
        # Имя функции для function-mode runner в grader.py.
        # None если задача не является функциональной (stdin-режим).
        "function_name": function_name,
    }
    save_json_file(task_dir / "meta.json", meta)

    block: dict[str, Any] = step.get("block") or {}
    text = str(block.get("text", ""))
    if not text:
        return

    (task_dir / "task.md").write_text(text, encoding="utf-8")

    zip_links, github_links = extract_external_test_links(text)

    # 1. ZIP — скачиваем автоматически
    for zip_url in zip_links:
        print(f"  📦 Найдена ZIP-ссылка: {zip_url}")
        count = _download_zip_tests(task_dir, zip_url, session)
        if count:
            print(f"  📦 Скачано тестов из ZIP: {count}")
            return

    # 2. HTML-таблица
    tests = extract_tests_from_html(text)
    if tests:
        count = save_tests(task_dir, tests)
        print(f"  📋 Извлечено тестов из таблицы: {count}")
        return

    # 3. GitHub — скачиваем через GitHub Contents API
    if github_links:
        for gh_url in github_links:
            print(f"  🔗 Пробую скачать тесты с GitHub: {gh_url}")
            count = _download_github_tests(task_dir, gh_url, session)
            if count:
                print(f"  🔗 Скачано {count} тестов с GitHub")
                return
        print("  ⚠️ GitHub: ни одна ссылка не дала тестов")
        return

    # 4. Ничего не нашли
    print("  ⚠️ Тесты не найдены (нет ZIP, таблицы и GitHub-ссылок) — остальные файлы сохранены")


# ---------------------------------------------------------------------------
# Оркестрация: один шаг
# ---------------------------------------------------------------------------


def process_step_url(
    step_url: str,
    session: requests.Session,
    root_dir: pathlib.Path,
) -> None:
    """Скачивает все данные одного шага и сохраняет в файловую систему."""
    parsed_url = urlparse(step_url)
    url_params = parse_qs(parsed_url.query)
    unit_id_list = url_params.get("unit", [])
    unit_id = int(unit_id_list[0]) if unit_id_list else None

    lesson_id, step_position = parse_stepik_step_url(step_url)

    print(f"  Получаю данные урока {lesson_id}...")
    lesson = fetch_lesson_data(session, lesson_id)
    lesson_title = str(lesson.get("title") or f"lesson-{lesson_id}")

    print("  Получаю данные юнита...")
    unit = fetch_unit_data(session, lesson_id, unit_id)
    section_id = int(unit.get("section") or 0)

    print(f"  Получаю данные секции {section_id}...")
    section = fetch_section_data(session, section_id)
    section_title = str(section.get("title") or f"section-{section_id}")
    course_id = int(section.get("course") or 0)

    print(f"  Получаю данные курса {course_id}...")
    course = fetch_course_data(session, course_id)
    course_title = str(course.get("title") or f"course-{course_id}")

    print(f"  Получаю данные шага {step_position}...")
    step = fetch_step_data(session, lesson_id, step_position)
    step_id = int(step.get("id") or 0)
    step_title = str(step.get("title") or "").strip()

    print(f"  Получаю последний ответ для шага {step_id}...")
    submission = fetch_submission_data(session, step_id)

    task_dir = build_task_directory(
        root_dir,
        course_title,
        section_title,
        lesson_title,
        step_position,
        step_title,
    )

    print(f"  Сохраняю файлы в: {task_dir}")
    save_task_files(task_dir, step, submission, lesson, section, course, session)
    print(f"  ✅ Шаг сохранён: {task_dir}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    """Главная функция: конфиг → авторизация → цикл обработки URL шагов."""
    config_path = pathlib.Path(CONFIG_FILE)

    try:
        config = load_or_create_config(config_path)
        config = normalize_config_paths(config, config_path)
    except Exception as error:  # noqa: BLE001
        print(f"❌ Ошибка работы с конфигом: {error}")
        return

    root_dir = pathlib.Path(str(config["root_dir"]))
    secrets_path = pathlib.Path(str(config["secrets_path"]))

    try:
        secrets = load_secrets_dict(secrets_path)
        session = create_user_session(secrets, secrets_path)
    except Exception as error:  # noqa: BLE001
        print(f"❌ Ошибка подготовки данных: {error}")
        return

    print("\nВведите URL шагов (по одному, пустая строка — завершение):")
    while True:
        step_url = input("URL шага: ").strip()
        if not step_url:
            break
        try:
            process_step_url(step_url, session, root_dir)
        except Exception as error:  # noqa: BLE001
            print(f"❌ Ошибка обработки шага: {error}")


if __name__ == "__main__":
    main()
