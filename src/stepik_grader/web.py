"""web.py — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1).

Application/UI слой. Поднимает stdlib ``http.server`` на 127.0.0.1 (только
localhost, не торчит в сеть, **без новых зависимостей**) и отдаёт
одностраничный интерфейс: поле пути → таблица результатов с AC/WA, временем,
памятью и diff при WA. Для тех, кому консольное меню — барьер (новички, IDE).

Переиспользует ``core/grader_core.run_tests`` и ``core/test_loader``
(``find_all_solution_files``/``resolve_test_dir``) — не дублирует логику
грейдинга. Зависимость ``web → core`` ациклична (core не импортирует web/cli).

Threat model тот же, что у CLI: решения запускаются в subprocess без
OS-sandbox (см. ``core/executor.py``, CLAUDE.md). Сервер слушает только
127.0.0.1 — запускай для своих решений на своей машине.
"""

from __future__ import annotations

import json
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from stepik_grader.core.grader_core import run_tests
from stepik_grader.core.test_loader import find_all_solution_files, resolve_test_dir

__all__ = ["grade_path", "run_server"]


def _rel(path: str, base: str) -> str:
    """Путь относительно base (для компактного отображения), с fallback."""
    try:
        return str(pathlib.Path(path).relative_to(base))
    except ValueError:
        return pathlib.Path(path).name


def _case_view(index: int, case: dict[str, Any]) -> dict[str, Any]:
    """Компактное представление одного тест-кейса для UI."""
    return {
        "n": index,
        "verdict": case.get("verdict") or ("RE" if case.get("error") else "?"),
        "time": round(case.get("time", 0.0), 4),
        "error": case.get("error", ""),
        # diff показываем только для непрошедших — иначе пусто.
        "diff": "" if case.get("passed") else case.get("diff", ""),
    }


def grade_path(path: str) -> dict[str, Any]:
    """Прогрейдить один файл решения или папку с решениями.

    Возвращает JSON-совместимый dict:
        kind    — "file" | "dir" | "error"
        base    — базовый путь (для относительных имён)
        message — текст ошибки (только при kind == "error")
        rows    — список строк-результатов (по одному решению)
    """
    p = pathlib.Path(path).expanduser()
    if p.is_file():
        solutions, base, kind = [str(p)], str(p.parent), "file"
    elif p.is_dir():
        solutions, base, kind = find_all_solution_files(str(p)), str(p), "dir"
    else:
        return {"kind": "error", "message": f"Путь не найден: {path}", "rows": []}

    if not solutions:
        return {"kind": "error", "message": f"Решения не найдены в: {path}", "rows": []}

    rows: list[dict[str, Any]] = []
    for sol in solutions:
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not pathlib.Path(test_dir).is_dir():
            rows.append({"file": _rel(sol, base), "status": "NO TESTS", "passed": 0, "total": 0})
            continue
        res = run_tests(sol, test_dir)
        ok = res["total"] > 0 and res["passed"] == res["total"]
        rows.append(
            {
                "file": _rel(sol, base),
                "status": "OK" if ok else "FAIL",
                "passed": res["passed"],
                "total": res["total"],
                "failed": res["failed"],
                "errors": res["errors"],
                "total_time": round(res["total_time"], 4),
                "avg_time": round(res["avg_time"], 4),
                "memory_mb": round(res["peak_memory_mb"], 2),
                "first_fail": res["first_fail"],
                "cases": [_case_view(i, c) for i, c in enumerate(res["cases"], 1)],
            }
        )
    return {"kind": kind, "base": base, "rows": rows}


class _Handler(BaseHTTPRequestHandler):
    """GET / → страница; GET /api/grade?path=… → JSON-результаты."""

    def do_GET(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, "text/html; charset=utf-8", _INDEX_HTML.encode("utf-8"))
        elif parsed.path == "/api/grade":
            path = (parse_qs(parsed.query).get("path") or [""])[0].strip()
            data = (
                grade_path(path)
                if path
                else {"kind": "error", "message": "Укажите путь к файлу или папке.", "rows": []}
            )
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:  # noqa: N802
        """Приглушить пер-запросный лог в stdout (иначе шумно)."""


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Запустить веб-интерфейс на http://host:port (Ctrl+C — остановить).

    Слушает только localhost. ``ThreadingHTTPServer`` — чтобы медленный
    грейдинг одного запроса не блокировал отдачу страницы другому.
    """
    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"🌐 Веб-интерфейс грейдера: {url}  (Ctrl+C — остановить)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


# Одностраничный интерфейс — всё inline (HTML+CSS+JS), без внешних ресурсов,
# работает офлайн. Светлая/тёмная тема через prefers-color-scheme.
_INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stepik Python Grader</title>
<style>
  :root { color-scheme: light dark; --ok:#1a7f37; --fail:#cf222e; --bd:#8884; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
         max-width: 1000px; margin-inline: auto; line-height: 1.5; }
  h1 { font-size: 1.3rem; margin: 0 0 1rem; }
  .row { display: flex; gap: .5rem; flex-wrap: wrap; }
  input[type=text] { flex: 1; min-width: 240px; padding: .55rem .7rem;
         border: 1px solid var(--bd); border-radius: 8px; font-size: 1rem;
         background: transparent; color: inherit; }
  button { padding: .55rem 1.1rem; border: 0; border-radius: 8px; cursor: pointer;
         font-size: 1rem; background: #2563eb; color: #fff; }
  button:disabled { opacity: .5; cursor: default; }
  .hint { color: #8888; font-size: .85rem; margin: .4rem 0 1rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--bd); }
  th { font-size: .8rem; text-transform: uppercase; letter-spacing: .03em; opacity: .7; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .badge { font-weight: 600; }
  .OK { color: var(--ok); } .FAIL, .RE { color: var(--fail); }
  .file { cursor: pointer; }
  .file:hover { text-decoration: underline; }
  pre { background: #8881; padding: .6rem; border-radius: 6px; overflow-x: auto;
        font-size: .85rem; margin: .3rem 0; }
  .msg { color: var(--fail); margin-top: 1rem; }
  .caserow td { border-bottom: 0; padding-top: 0; }
</style>
</head>
<body>
<h1>🐍 Stepik Python Grader</h1>
<div class="row">
  <input id="path" type="text" placeholder="Путь к файлу решения (.py) или папке с решениями"
         autofocus>
  <button id="run">Проверить</button>
</div>
<div class="hint">Локально, только на этой машине. Enter или «Проверить» —
  запустить. Клик по имени файла — раскрыть тест-кейсы и diff.</div>
<div id="out"></div>
<script>
const $ = s => document.querySelector(s);
const esc = s => (s ?? "").replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

async function grade() {
  const path = $("#path").value.trim();
  if (!path) return;
  const btn = $("#run"); btn.disabled = true; btn.textContent = "Проверка…";
  $("#out").innerHTML = "";
  try {
    const r = await fetch("/api/grade?path=" + encodeURIComponent(path));
    render(await r.json());
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + '</p>';
  } finally { btn.disabled = false; btn.textContent = "Проверить"; }
}

function render(data) {
  if (data.kind === "error") {
    $("#out").innerHTML = '<p class="msg">' + esc(data.message) + '</p>';
    return;
  }
  let h = '<table><thead><tr><th>Файл</th><th>Passed</th><th>Статус</th>'
        + '<th>Σ время</th><th>Avg</th><th>Память, МБ</th></tr></thead><tbody>';
  data.rows.forEach((row, i) => {
    const cls = row.status === "OK" ? "OK" : "FAIL";
    h += '<tr>'
       + '<td class="file" onclick="toggle(' + i + ')">' + esc(row.file) + '</td>'
       + '<td class="num">' + (row.passed ?? 0) + '/' + (row.total ?? 0) + '</td>'
       + '<td class="badge ' + cls + '">' + esc(row.status) + '</td>'
       + '<td class="num">' + (row.total_time ?? "—") + '</td>'
       + '<td class="num">' + (row.avg_time ?? "—") + '</td>'
       + '<td class="num">' + (row.memory_mb ?? "—") + '</td></tr>';
    h += '<tr class="caserow" id="c' + i + '" style="display:none"><td colspan="6">'
       + casesHtml(row.cases) + '</td></tr>';
  });
  h += '</tbody></table>';
  $("#out").innerHTML = h;
}

function casesHtml(cases) {
  if (!cases || !cases.length) return '<em>нет тест-кейсов</em>';
  return cases.map(c => {
    let s = '<div><span class="badge ' + (c.verdict === "AC" ? "OK" : "FAIL") + '">'
          + '#' + c.n + ' ' + esc(c.verdict) + '</span> · ' + c.time + ' s';
    if (c.error) s += '<pre>' + esc(c.error) + '</pre>';
    else if (c.diff) s += '<pre>' + esc(c.diff) + '</pre>';
    return s + '</div>';
  }).join("");
}

function toggle(i) {
  const el = $("#c" + i);
  el.style.display = el.style.display === "none" ? "table-row" : "none";
}

$("#run").addEventListener("click", grade);
$("#path").addEventListener("keydown", e => { if (e.key === "Enter") grade(); });
</script>
</body>
</html>
"""
