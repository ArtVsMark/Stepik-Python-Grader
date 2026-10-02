"""test_source_fetcher.py — скачивание тестов из внешних источников (issue #302).

Архитектурный слой: Infrastructure — сеть (Stepik ZIP / GitHub Contents API),
конвертация во внутренний Format 3. Выделено из ``downloader.py`` (SRP): здесь
только «URL → файлы на диске», без оркестрации/конфига/интерактива.

Зависимости: ``core/stepik_client`` (HTTP + allowlist/токен-безопасность,
issue #240), ``core/parsers`` (подсчёт кейсов), ``core/tests_writer`` (запись
Format 3). НЕ импортирует ``downloader`` (issue #302 AC — новые модули без
обратных импортов).
"""

from __future__ import annotations

import io
import pathlib
import re
import zipfile
from urllib.parse import quote

import requests

from stepik_grader.core.parsers import parse_testblock_file
from stepik_grader.core.stepik_client import (
    ExternalUrlRejected,
    external_download_get,
    is_stepik_url,
)
from stepik_grader.core.tests_writer import (
    backup_dir_for,
    reset_tests_dir,
    write_testblock_tests,
)

__all__ = ["download_github_tests", "download_zip_tests"]

_GITHUB_TREE_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:tree|blob)/(?P<branch>[^/]+)/(?P<path>.+)"
)
_GITHUB_CONTENTS_API = "https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"

# issue #814 (NETW-03): ZIP приходит по ссылке из HTML задачи, то есть из
# недоверенного источника. Без потолка распаковки крошечный архив разворачивался
# в гигабайты в памяти (классический zip bomb): ``zf.read`` читает член целиком.
# Лимиты щедрые по меркам тест-кейсов (текстовые файлы на килобайты) и
# несопоставимо малые по меркам бомбы.
_MAX_MEMBER_BYTES = 8 * 1024 * 1024  # 8 МБ на один файл
_MAX_TOTAL_BYTES = 64 * 1024 * 1024  # 64 МБ на весь архив


class _ZipTooLarge(Exception):
    """Распаковка вышла за лимит — обрабатывается на месте, наружу не летит."""


def _reset_tests_dir_reporting_backup(tests_dir: pathlib.Path) -> None:
    """Освободить ``tests/`` под новый набор и сказать, что спасено (issue #951).

    Перенос молчал бы так же, как молчало удаление: пользователь должен видеть,
    что его дописанные кейсы не исчезли, а лежат рядом. Повторный вызов внутри
    :func:`write_testblock_tests` найдёт каталог уже пустым и ничего не сделает.
    """
    moved = reset_tests_dir(tests_dir)
    if moved:
        backup = backup_dir_for(tests_dir)
        print(
            f"  🗂 Прежнее содержимое tests/ ({moved} шт.) перенесено "
            f"в {backup.name}/ — дописанные вручную кейсы не потеряны"
        )


def _read_member(zf: zipfile.ZipFile, name: str, *, budget: list[int] | None = None) -> bytes:
    """Прочитать член архива, соблюдая лимиты размера (issue #814, ``NETW-03``).

    Проверяется ЗАЯВЛЕННЫЙ размер (``file_size`` из заголовка) до чтения — иначе
    смысла в лимите нет: бомба разворачивается ровно в момент ``read``.
    ``budget`` — общий остаток на архив (список из одного числа, чтобы менять
    по ссылке).
    """
    info = zf.getinfo(name)
    if info.file_size > _MAX_MEMBER_BYTES:
        raise _ZipTooLarge(f"{name}: {info.file_size} Б > лимита {_MAX_MEMBER_BYTES} Б")
    if budget is not None:
        budget[0] -= info.file_size
        if budget[0] < 0:
            raise _ZipTooLarge(f"суммарный размер архива превысил {_MAX_TOTAL_BYTES} Б")
    return zf.read(name)


def download_zip_tests(
    task_dir: pathlib.Path,
    zip_url: str,
    session: requests.Session,
) -> int:
    """Скачивает ZIP со Stepik и конвертирует в Format 3 (input.txt + output.txt).

    Ожидает ZIP с файлами: 1, 1.clue, 2, 2.clue, ... (формат Stepik).
    Конвертирует в единый формат # TEST_N: совместимый с grader Format 3.
    Возвращает количество тест-кейсов.

    ``zip_url`` может указывать на произвольный сторонний хост (ссылка из
    текста задачи), поэтому авторизованная ``session`` используется только
    для самого Stepik (issue #240) — иначе для не-Stepik хостов запрос идёт
    через :func:`external_download_get` без OAuth Bearer-токена и с проверкой
    allowlist/private-address.
    """
    try:
        if is_stepik_url(zip_url):
            response = session.get(zip_url, timeout=30)
        else:
            response = external_download_get(zip_url)
        response.raise_for_status()
    except (requests.RequestException, ExternalUrlRejected) as exc:
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
    budget = [_MAX_TOTAL_BYTES]
    try:
        _collect_zip_pairs(zf, names, strip_prefix, pairs, budget)
    except _ZipTooLarge as exc:
        print(f"  ⚠️ ZIP отклонён — слишком большая распаковка ({exc}): {zip_url}")
        return 0

    if not pairs:
        print(f"  ⚠️ В ZIP не найдены файлы формата N / N.clue: {zip_url}")
        return 0

    # Декодируем байты из ZIP и отдаём готовые тексты writer'у (issue #302 —
    # общий Format 3 в core/tests_writer.write_testblock_tests).
    text_pairs = {
        idx: (
            pair.get("input", b"").decode("utf-8", errors="replace").rstrip("\n"),
            pair.get("clue", b"").decode("utf-8", errors="replace").rstrip("\n"),
        )
        for idx, pair in pairs.items()
    }
    _reset_tests_dir_reporting_backup(task_dir / "tests")
    count = write_testblock_tests(task_dir / "tests", text_pairs)
    print(f"  📦 ZIP сконвертирован в Format 3: {count} тест(ов) → tests/input.txt + output.txt")
    return count


def _collect_zip_pairs(
    zf: zipfile.ZipFile,
    names: list[str],
    strip_prefix: str,
    pairs: dict[int, dict[str, bytes]],
    budget: list[int],
) -> None:
    """Разобрать члены архива в пары ``N → {input, clue}`` (issue #814).

    Вынесено из :func:`download_zip_tests`, чтобы превышение лимита прерывало
    разбор целиком одним ``raise``, а не проверялось после каждого чтения.
    """
    for name in names:
        clean = (
            name[len(strip_prefix) :] if strip_prefix and name.startswith(strip_prefix) else name
        )
        clean = clean.strip("/")
        if not clean:
            continue
        # issue #814 (NETW-01): именно ``isdecimal``, а не ``isdigit``. Последний
        # истинен для «²» и «①», которые ``int()`` не принимает, — один такой
        # символ в имени члена архива ронял всё скачивание ValueError'ом мимо
        # перехватов (проверено прогоном на крафт-ZIP). ``isdecimal`` — ровно то
        # подмножество, что ``int()`` разбирает, включая арабо-индийские цифры.
        if clean.isdecimal():
            idx = int(clean)
            pairs.setdefault(idx, {})["input"] = _read_member(zf, name, budget=budget)
        elif "." in clean:
            stem, ext = clean.rsplit(".", 1)
            if stem.isdecimal() and ext == "clue":
                idx = int(stem)
                pairs.setdefault(idx, {})["clue"] = _read_member(zf, name, budget=budget)


def _fetch_text(url: str | None) -> str:
    """Скачать текстовый файл кейса, декодируя явным UTF-8 (issue #942).

    ``url`` ``None`` — файла в паре нет (``N`` без ``N.clue``), возвращается
    пустая строка: пара всё равно попадёт в набор, чтобы неполнота была видна
    как пустой кейс, а не как молча пропущенный номер.

    Декодируем ``content`` сами, а не берём ``response.text``: без ``charset``
    в заголовке ``requests`` разбирает ``text/*`` как latin-1 по букве
    HTTP-спеки, и кириллический кейс превращался в мусор — тот же ложный ``WA``,
    что и битая кодировка на диске. ``errors="replace"`` — как в ZIP-ветке.
    """
    if url is None:
        return ""
    response = external_download_get(url)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace").rstrip("\n")


def download_github_tests(
    task_dir: pathlib.Path,
    gh_url: str,
) -> int:
    """Скачать тесты с GitHub через API содержимого репозитория.

    Поддерживает два формата:
    1. Директория с input.txt + output.txt (Format 3) — скачивается напрямую
    2. Директория с числовыми файлами N + N.clue — конвертируется в Format 3

    Возвращает количество тест-кейсов (0 при ошибке).

    GitHub — всегда сторонний (не-Stepik) хост, поэтому скачивание идёт через
    :func:`external_download_get` без OAuth Bearer-токена (issue #240).
    """
    match = _GITHUB_TREE_RE.search(gh_url)
    if not match:
        print(f"  ⚠️ Не удалось распознать GitHub URL: {gh_url}")
        return 0

    owner = match.group("owner")
    repo = match.group("repo")
    branch = match.group("branch")
    path = match.group("path").rstrip("/")

    # issue #814 (NETW-04): owner/repo/path/branch приходят из HTML задачи и
    # подставлялись в URL как есть — имя ветки с `#` обрубало бы запрос, а с `?`
    # или `&` дописывало чужие query-параметры к вызову API. `safe="/"` оставляет
    # разделители пути, экранируя всё остальное.
    api_url = _GITHUB_CONTENTS_API.format(
        owner=quote(owner, safe=""),
        repo=quote(repo, safe=""),
        path=quote(path, safe="/"),
        branch=quote(branch, safe=""),
    )

    try:
        resp = external_download_get(api_url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        contents = resp.json()
    except (requests.RequestException, ExternalUrlRejected) as exc:
        print(f"  ⚠️ GitHub API недоступен: {exc}")
        return 0

    if not isinstance(contents, list):
        print(f"  ⚠️ GitHub API вернул не список файлов: {gh_url}")
        return 0

    # issue #942: каталог НЕ создаётся заранее — при обрыве сети на первом же
    # файле пустой ``tests/`` выглядел бы как «набор скачан, но пуст». Создание
    # и сброс делает общий путь записи, уже после получения всех данных.
    tests_dir = task_dir / "tests"

    # issue #814 (NETW-02): ответ API — недоверенный JSON, а индексация шла
    # напрямую. Файл без `download_url` (submodule, symlink, урезанная выдача
    # прокси) давал KeyError мимо перехвата выше: скачивание падало трейсбеком
    # вместо понятного «не удалось». Читаем через `.get` и берём только записи,
    # у которых обе строки на месте.
    file_map: dict[str, str] = {}
    for item in contents:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = item.get("name")
        url = item.get("download_url")
        if isinstance(name, str) and isinstance(url, str) and name and url:
            file_map[name] = url

    # Вариант А: input.txt + output.txt уже есть (Format 3)
    if "input.txt" in file_map and "output.txt" in file_map:
        # issue #942: сначала оба файла в память, и только потом — на диск.
        # Раньше запись шла по ходу скачивания: обрыв на втором файле оставлял
        # свежий ``input.txt`` рядом со СТАРЫМ ``output.txt``, и прогон сверял
        # новые входные данные со старыми ожиданиями.
        try:
            blobs: dict[str, bytes] = {}
            for fname in ("input.txt", "output.txt"):
                response = external_download_get(file_map[fname])
                response.raise_for_status()
                blobs[fname] = response.content
        except (requests.RequestException, ExternalUrlRejected) as exc:
            print(f"  ⚠️ GitHub: не удалось скачать файлы тестов: {exc}")
            return 0

        # issue #942: через общий путь сброса. Прямая запись мимо него оставляла
        # висящие Format-1 файлы (``N``/``N.clue``) прошлого скачивания, а
        # автодетект отдаёт Format 1 приоритет — грейдер молча прогонял СТАРЫЙ
        # набор. Триггер: перекачка шага, тесты которого раньше пришли из ZIP.
        _reset_tests_dir_reporting_backup(tests_dir)
        for fname, blob in blobs.items():
            (tests_dir / fname).write_bytes(blob)

        # issue #942: декодируем те же байты, а не перечитываем файл строгим
        # UTF-8 — набор в cp1251 давал ``UnicodeDecodeError`` уже ПОСЛЕ записи,
        # то есть после подмены каталога, и наружу летел трейсбек вместо счёта.
        count = len(parse_testblock_file(blobs["input.txt"].decode("utf-8", errors="replace")))
        print(f"  🔗 GitHub: скачаны input.txt + output.txt (Format 3): {count} тест(ов)")
        return count

    # Вариант Б: числовые файлы N + N.clue
    pairs: dict[int, dict[str, str]] = {}
    for fname, url in file_map.items():
        # issue #814 (NETW-01): `isdecimal`, а не `isdigit` — см. ZIP-ветку выше.
        if fname.isdecimal():
            pairs.setdefault(int(fname), {})["input_url"] = url
        elif "." in fname:
            stem, ext = fname.rsplit(".", 1)
            if stem.isdecimal() and ext == "clue":
                pairs.setdefault(int(stem), {})["clue_url"] = url

    if not pairs:
        print(f"  ⚠️ GitHub: файлы не распознаны в {gh_url}")
        return 0

    # Скачиваем содержимое (сеть — здесь), тексты отдаём writer'у (issue #302).
    # issue #942: вся ветка под перехватом — обрыв на середине пачки уносил
    # трейсбек мимо счётчика, хотя запись ещё не начиналась; и `raise_for_status`
    # на каждом файле, иначе 404-страница GitHub легла бы в тест-кейс как данные.
    text_pairs: dict[int, tuple[str, str]] = {}
    try:
        for idx, pair in pairs.items():
            text_pairs[idx] = (
                _fetch_text(pair.get("input_url")),
                _fetch_text(pair.get("clue_url")),
            )
    except (requests.RequestException, ExternalUrlRejected) as exc:
        print(f"  ⚠️ GitHub: не удалось скачать файлы тестов: {exc}")
        return 0

    _reset_tests_dir_reporting_backup(tests_dir)
    count = write_testblock_tests(tests_dir, text_pairs)
    print(f"  🔗 GitHub: сконвертировано {count} тест(ов) → Format 3")
    return count
