// app.js — клиентская логика веб-интерфейса грейдера (issue #58, эпик #80 Tier 1; issue #125).
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
