"""web.py — локальный веб-интерфейс грейдера (issue #58, эпик #80 Tier 1).

Application/UI слой. Поднимает stdlib ``http.server`` на 127.0.0.1 (только
localhost, не торчит в сеть, **без новых зависимостей**) и отдаёт
одностраничный интерфейс: поле пути → таблица результатов. Два режима:
корректность (AC/WA, diff при WA) и бенчмарк (min/median/вердикт сравнения).
Для тех, кому консольное меню — барьер (новички, IDE).

Переиспользует ``core/grader_core`` (``run_tests``/``run_benchmark``),
``core/test_loader`` (``find_all_solution_files``/``resolve_test_dir``),
``core/microbench_runner.apply_relative_ranking`` и ``core/reporter.fmt_time``
— логика грейдинга и форматирования не дублируется. ``web → core`` ациклично.

Threat model тот же, что у CLI: решения запускаются в subprocess без
OS-sandbox (см. ``core/executor.py``, CLAUDE.md). Сервер слушает только
127.0.0.1 — запускай для своих решений на своей машине.
"""

from __future__ import annotations

import html
import json
import os
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from stepik_grader.core.glossary import lookup_from_error
from stepik_grader.core.grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    run_benchmark,
    run_tests,
)
from stepik_grader.core.microbench_runner import apply_relative_ranking
from stepik_grader.core.reporter import fmt_time
from stepik_grader.core.test_loader import find_all_solution_files, resolve_test_dir

__all__ = ["grade_benchmark", "grade_path", "run_server"]


def _rel(path: str, base: str) -> str:
    """Путь относительно base (для компактного отображения), с fallback."""
    try:
        return str(pathlib.Path(path).relative_to(base))
    except ValueError:
        return pathlib.Path(path).name


def _resolve_solutions(path: str) -> tuple[str, str, list[str]] | dict[str, Any]:
    """Вернуть (kind, base, solutions) для файла/папки или error-dict.

    kind — "file" | "dir". Общий вход для обоих режимов грейдинга.
    """
    p = pathlib.Path(path).expanduser()
    if p.is_file():
        return "file", str(p.parent), [str(p)]
    if p.is_dir():
        solutions = find_all_solution_files(str(p))
        if not solutions:
            return {"kind": "error", "message": f"Решения не найдены в: {path}", "rows": []}
        return "dir", str(p), solutions
    return {"kind": "error", "message": f"Путь не найден: {path}", "rows": []}


def _case_view(index: int, case: dict[str, Any]) -> dict[str, Any]:
    """Компактное представление одного тест-кейса для UI."""
    error = case.get("error", "")
    view: dict[str, Any] = {
        "n": index,
        "verdict": case.get("verdict") or ("RE" if error else "?"),
        "time": round(case.get("time", 0.0), 4),
        "error": error,
        # diff показываем только для непрошедших — иначе пусто.
        "diff": "" if case.get("passed") else case.get("diff", ""),
    }
    # issue #72: карточка ошибки — тип исключения, пояснение, ссылка на глоссарий.
    entry = lookup_from_error(error) if error else None
    if entry is not None:
        view["glossary"] = {
            "exception": entry.exception,
            "hint": entry.hint,
            "url": entry.url,
        }
    return view


def grade_path(path: str) -> dict[str, Any]:
    """Прогрейдить файл/папку на корректность (режим 1/2).

    Возвращает JSON-совместимый dict: kind ("file"|"dir"|"error"), mode="tests",
    base, rows (по одному решению) либо message при ошибке.
    """
    resolved = _resolve_solutions(path)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

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
                "total_time": round(res["total_time"], 4),
                "avg_time": round(res["avg_time"], 4),
                "memory_mb": round(res["peak_memory_mb"], 2),
                "cases": [_case_view(i, c) for i, c in enumerate(res["cases"], 1)],
            }
        )
    return {"kind": kind, "mode": "tests", "base": base, "rows": rows}


def grade_benchmark(path: str, *, repeats: int = 15) -> dict[str, Any]:
    """Бенчмаркнуть файл/папку (режим 3) и ранжировать по медиане.

    Строки отсортированы от быстрого к медленному; вердикт SIMILAR/SLOWER/
    MUCH_SLOWER — относительно самого быстрого (как в CLI mode 3). Ошибочные
    решения идут в конец.
    """
    resolved = _resolve_solutions(path)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

    results: dict[str, dict[str, Any]] = {}
    for sol in solutions:
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not pathlib.Path(test_dir).is_dir():
            results[sol] = {"error": "тесты не найдены", "runs": 0}
        else:
            results[sol] = run_benchmark(sol, test_dir, repeats=max(1, repeats))
    apply_relative_ranking(
        results,
        similar_threshold=SIMILAR_THRESHOLD,
        much_slower_threshold=MUCH_SLOWER_THRESHOLD,
    )

    ok = {s: d for s, d in results.items() if not d.get("error")}
    rows: list[dict[str, Any]] = []
    for sol in sorted(ok, key=lambda s: ok[s]["median"]):
        d = ok[sol]
        rows.append(
            {
                "file": _rel(sol, base),
                "runs": d["runs"],
                "min": fmt_time(d["min"]),
                "median": fmt_time(d["median"]),
                "relative": round(d.get("relative", 1.0) * 100, 1),
                "verdict": d.get("verdict", "SIMILAR"),
                "memory_mb": round(d["peak_memory_mb"], 2),
            }
        )
    for sol, d in results.items():
        if d.get("error"):
            rows.append({"file": _rel(sol, base), "verdict": "ERR", "error": d["error"]})
    return {"kind": kind, "mode": "bench", "base": base, "rows": rows}


class _Handler(BaseHTTPRequestHandler):
    """GET / → страница; GET /api/grade?path=…&mode=tests|bench → JSON."""

    def do_GET(self) -> None:  # noqa: N802 (имя задано BaseHTTPRequestHandler)
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = _INDEX_HTML.replace("__DEFAULT_PATH__", html.escape(os.getcwd(), quote=True))
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
        elif parsed.path == "/api/grade":
            qs = parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0].strip()
            mode = (qs.get("mode") or ["tests"])[0]
            if not path:
                data: dict[str, Any] = {
                    "kind": "error",
                    "message": "Укажите путь к файлу или папке.",
                    "rows": [],
                }
            elif mode == "bench":
                data = grade_benchmark(path, repeats=_int(qs.get("repeats"), 15))
            else:
                data = grade_path(path)
            self._send(200, "application/json; charset=utf-8", _json(data))
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


def _int(values: list[str] | None, default: int) -> int:
    """Первое значение из query как int, иначе default (без падения)."""
    try:
        return int((values or [str(default)])[0])
    except (TypeError, ValueError):
        return default


def _json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


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
# __DEFAULT_PATH__ подставляется сервером (текущая папка запуска).
_INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stepik Python Grader</title>
<style>
  :root { color-scheme: light dark; --ok:#1a7f37; --warn:#9a6700; --fail:#cf222e; --bd:#8884; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
         max-width: 1000px; margin-inline: auto; line-height: 1.5; }
  h1 { font-size: 1.3rem; margin: 0 0 1rem; }
  .row { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
  input[type=text] { flex: 1; min-width: 240px; padding: .55rem .7rem;
         border: 1px solid var(--bd); border-radius: 8px; font-size: 1rem;
         background: transparent; color: inherit; }
  select { padding: .5rem; border-radius: 8px; border: 1px solid var(--bd);
         background: transparent; color: inherit; }
  button { padding: .55rem 1.1rem; border: 0; border-radius: 8px; cursor: pointer;
         font-size: 1rem; background: #2563eb; color: #fff; }
  button:disabled { opacity: .5; cursor: default; }
  .seg { display: inline-flex; border: 1px solid var(--bd); border-radius: 8px; overflow: hidden; }
  .seg button { background: transparent; color: inherit; border-radius: 0; padding: .45rem .9rem; }
  .seg button.active { background: #2563eb; color: #fff; }
  .hint { color: #8888; font-size: .85rem; margin: .4rem 0 1rem; }
  .bar { margin: 1rem 0 .3rem; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin-top: .4rem; }
  th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--bd); }
  th { font-size: .8rem; text-transform: uppercase; letter-spacing: .03em; opacity: .7; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .badge { font-weight: 600; }
  .OK, .SIMILAR { color: var(--ok); } .SLOWER { color: var(--warn); }
  .FAIL, .RE, .ERR, .MUCH_SLOWER { color: var(--fail); }
  .file { cursor: pointer; } .file:hover { text-decoration: underline; }
  pre { background: #8881; padding: .6rem; border-radius: 6px; overflow-x: auto;
        font-size: .85rem; margin: .3rem 0; }
  .msg { color: var(--fail); margin-top: 1rem; }
  .caserow td { border-bottom: 0; padding-top: 0; }
  .errcard { margin: .4rem 0; padding: .5rem .7rem; border-left: 3px solid var(--warn);
        background: #eab30818; border-radius: 4px; font-size: .9rem; }
  .errcard-ex { font-weight: 600; }
  .errcard a { color: #2563eb; }
</style>
</head>
<body>
<h1>🐍 Stepik Python Grader</h1>
<div class="row" style="margin-bottom:.6rem">
  <span class="seg">
    <button id="m-tests" class="active" data-mode="tests">Корректность</button>
    <button id="m-bench" data-mode="bench">Бенчмарк</button>
  </span>
  <select id="repeats" style="display:none" title="Повторов на тест-кейс">
    <option value="5">низкий · 5</option>
    <option value="15" selected>средний · 15</option>
    <option value="50">высокий · 50</option>
  </select>
</div>
<div class="row">
  <input id="path" type="text" value="__DEFAULT_PATH__"
         placeholder="Путь к файлу решения (.py) или папке с решениями">
  <button id="run">Проверить</button>
</div>
<div class="hint">Локально, только на этой машине. Enter или «Проверить» — запустить.
  Клик по имени файла (в режиме корректности) — раскрыть тест-кейсы и diff.</div>
<div id="bar" class="bar"></div>
<div id="out"></div>
<script>
const $ = s => document.querySelector(s);
// issue #214: экранируем и кавычки — esc() используется не только в текстовом
// контексте (innerHTML), но и внутри HTML-атрибутов (errorCard() вставляет
// esc(g.url) в href="..."); без \"/' значение могло бы разорвать атрибут.
const HT = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c => HT[c]);
let mode = localStorage.getItem("grader_mode") || "tests";

function setMode(m) {
  mode = m;
  $("#m-tests").classList.toggle("active", m === "tests");
  $("#m-bench").classList.toggle("active", m === "bench");
  $("#repeats").style.display = m === "bench" ? "" : "none";
  localStorage.setItem("grader_mode", m);
}

async function grade() {
  const path = $("#path").value.trim();
  if (!path) return;
  localStorage.setItem("grader_path", path);
  const btn = $("#run"); btn.disabled = true; btn.textContent = "Проверка…";
  $("#bar").textContent = ""; $("#out").innerHTML = "";
  const q = new URLSearchParams({ path, mode });
  if (mode === "bench") q.set("repeats", $("#repeats").value);
  try {
    const r = await fetch("/api/grade?" + q.toString());
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
  (data.mode === "bench" ? renderBench : renderTests)(data.rows);
}

function renderTests(rows) {
  const ok = rows.filter(r => r.status === "OK").length;
  $("#bar").textContent =
    "Решений: " + rows.length + " · OK: " + ok + " · FAIL: " + (rows.length - ok);
  let h = '<table><thead><tr><th>Файл</th><th>Passed</th><th>Статус</th>'
        + '<th>Σ время</th><th>Avg</th><th>Память, МБ</th></tr></thead><tbody>';
  rows.forEach((row, i) => {
    const cls = row.status === "OK" ? "OK" : "FAIL";
    h += '<tr><td class="file" onclick="toggle(' + i + ')">' + esc(row.file) + '</td>'
       + '<td class="num">' + (row.passed ?? 0) + '/' + (row.total ?? 0) + '</td>'
       + '<td class="badge ' + cls + '">' + esc(row.status) + '</td>'
       + '<td class="num">' + (row.total_time ?? "—") + '</td>'
       + '<td class="num">' + (row.avg_time ?? "—") + '</td>'
       + '<td class="num">' + (row.memory_mb ?? "—") + '</td></tr>';
    h += '<tr class="caserow" id="c' + i + '" style="display:none"><td colspan="6">'
       + casesHtml(row.cases) + '</td></tr>';
  });
  $("#out").innerHTML = h + '</tbody></table>';
}

function renderBench(rows) {
  const ranked = rows.filter(r => !r.error);
  const fast = ranked.length
    ? " · быстрейшее: " + esc(ranked[0].file) + " (" + esc(ranked[0].median) + ")"
    : "";
  $("#bar").textContent = "Решений: " + rows.length + fast;
  let h = '<table><thead><tr><th>Файл</th><th>Runs</th><th>Min</th><th>Median</th>'
        + '<th>Отн.</th><th>Вердикт</th><th>Память, МБ</th></tr></thead><tbody>';
  rows.forEach(row => {
    if (row.error) {
      h += '<tr><td>' + esc(row.file) + '</td><td colspan="5" class="ERR">'
         + esc(row.error) + '</td><td></td></tr>';
      return;
    }
    h += '<tr><td>' + esc(row.file) + '</td>'
       + '<td class="num">' + row.runs + '</td>'
       + '<td class="num">' + esc(row.min) + '</td>'
       + '<td class="num">' + esc(row.median) + '</td>'
       + '<td class="num">' + row.relative + '%</td>'
       + '<td class="badge ' + esc(row.verdict) + '">' + esc(row.verdict) + '</td>'
       + '<td class="num">' + (row.memory_mb ?? "—") + '</td></tr>';
  });
  $("#out").innerHTML = h + '</tbody></table>';
}

function casesHtml(cases) {
  if (!cases || !cases.length) return '<em>нет тест-кейсов</em>';
  return cases.map(c => {
    let s = '<div><span class="badge ' + (c.verdict === "AC" ? "OK" : "FAIL") + '">#'
          + c.n + ' ' + esc(c.verdict) + '</span> · ' + c.time + ' s';
    if (c.error) s += '<pre>' + esc(c.error) + '</pre>';
    else if (c.diff) s += '<pre>' + esc(c.diff) + '</pre>';
    if (c.glossary) s += errorCard(c.glossary);
    return s + '</div>';
  }).join("");
}

function errorCard(g) {
  return '<div class="errcard"><span class="errcard-ex">💡 ' + esc(g.exception) + '</span> '
       + esc(g.hint) + ' <a href="' + esc(g.url) + '" target="_blank" rel="noopener">'
       + 'открыть карточку в глоссарии →</a></div>';
}

function toggle(i) {
  const el = $("#c" + i);
  el.style.display = el.style.display === "none" ? "table-row" : "none";
}

document.querySelectorAll(".seg button").forEach(b =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));
$("#run").addEventListener("click", grade);
$("#path").addEventListener("keydown", e => { if (e.key === "Enter") grade(); });

// Восстановить последний путь и режим.
const saved = localStorage.getItem("grader_path");
if (saved) $("#path").value = saved;
setMode(mode);
</script>
</body>
</html>
"""
