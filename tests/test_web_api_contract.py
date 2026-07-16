"""test_web_api_contract.py — контракт «docs/api.md ↔ маршруты web/server.py» (issue #439).

Дешёвый guard без сети и без OpenAPI-оверхеда: сверяет множество эндпоинтов,
задокументированных секциями ``## `METHOD /path`` в ``docs/api.md``, с реальными
маршрутами ``_Handler`` — декларативными таблицами ``_API_{GET,POST}_{EXACT,
PREFIX}`` (issue #427) плюс спец-роут ``GET /``. Падает, если появился маршрут
без документации ИЛИ раздел в доке без маршрута (двусторонний дрейф).

Маршруты берём импортом атрибутов класса (не парсингом исходника) — это
устойчиво к переформатированию ``server.py``. ``/static/*`` намеренно вне
контракта: в api.md это отдельная секция без ``METHOD /path``, а не дискретный
эндпоинт (фиксированный allowlist, не файловый сервер).
"""

from __future__ import annotations

import pathlib
import re

from stepik_grader.web.server import _Handler

_API_MD = pathlib.Path(__file__).parent.parent / "docs" / "api.md"

# `## `GET /api/rules/<code>`` (док) ≡ префикс `/api/rules/` (server) — оба к
# канону `/api/rules/{}`: сегмент-плейсхолдер и хвост-префикса эквивалентны.
_PLACEHOLDER = re.compile(r"<[^/>]+>")
_HEADING = re.compile(r"^## `(GET|POST|PUT|PATCH|DELETE) ([^`]+)`", re.MULTILINE)


def _canon(path: str) -> str:
    """Канон пути: `<id>`/`<code>`-плейсхолдеры → `{}` (сравнимо с префиксами)."""
    return _PLACEHOLDER.sub("{}", path)


def _documented_endpoints() -> set[tuple[str, str]]:
    """(METHOD, canon-path) из секций ``## `METHOD /path`` в docs/api.md."""
    text = _API_MD.read_text(encoding="utf-8")
    return {(m.group(1), _canon(m.group(2))) for m in _HEADING.finditer(text)}


def _server_routes() -> set[tuple[str, str]]:
    """(METHOD, canon-path) из таблиц маршрутов ``_Handler`` + спец-роут ``GET /``.

    Префиксная запись → ``prefix + "{}" (+ suffix)``: тот же канон, что у
    ``<id>``-плейсхолдеров в доке (напр. POST-префикс с суффиксом ``/cancel`` →
    ``/api/v1/runs/{}/cancel``). ``GET /`` (индекс) и ``/static/*`` живут в
    do_GET отдельно от таблиц; ``/`` документирован как эндпоинт — добавляем
    явно, статику — нет (см. docstring модуля).
    """
    routes: set[tuple[str, str]] = {("GET", "/")}
    for path in _Handler._API_GET_EXACT:
        routes.add(("GET", path))
    for path in _Handler._API_POST_EXACT:
        routes.add(("POST", path))
    for entry in _Handler._API_GET_PREFIX:
        suffix = entry[2] if len(entry) > 2 else ""
        routes.add(("GET", entry[0] + "{}" + suffix))
    for entry in _Handler._API_POST_PREFIX:
        suffix = entry[2] if len(entry) > 2 else ""
        routes.add(("POST", entry[0] + "{}" + suffix))
    return routes


def test_api_md_matches_server_routes() -> None:
    """docs/api.md и таблицы маршрутов server.py описывают одно множество эндпоинтов."""
    documented = _documented_endpoints()
    actual = _server_routes()

    undocumented = actual - documented
    assert not undocumented, (
        f"маршруты в server.py без раздела `## `METHOD /path`` в docs/api.md: "
        f"{sorted(undocumented)}"
    )

    missing = documented - actual
    assert not missing, (
        f"разделы в docs/api.md без соответствующего маршрута в server.py: {sorted(missing)}"
    )


def test_parser_extracts_known_endpoints() -> None:
    """Sanity: парсер доки не пустой (иначе сломанный regex дал бы ложно-зелёный контракт)."""
    documented = _documented_endpoints()
    assert ("GET", "/") in documented
    assert ("POST", "/api/v1/runs") in documented
    assert ("GET", "/api/v1/runs/{}") in documented  # плейсхолдер <id> нормализован
    assert len(documented) >= 20, (
        f"ожидалось ≥20 задокументированных эндпоинтов, got {len(documented)}"
    )
