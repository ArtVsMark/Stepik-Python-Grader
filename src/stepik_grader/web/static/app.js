// app.js — клиентская логика веб-интерфейса грейдера (issue #58, эпик #80 Tier 1; issue #125).
const $ = s => document.querySelector(s);
// issue #214: экранируем и кавычки — esc() используется не только в текстовом
// контексте (innerHTML), но и внутри HTML-атрибутов (errorCard() вставляет
// esc(g.url) в href="..."); без \"/' значение могло бы разорвать атрибут.
const HT = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c => HT[c]);

// ---------------------------------------------------------------------------
// State (issue #125) — единый источник состояния для split-pane workspace,
// command palette, action cards и сценарных кнопок.
// ---------------------------------------------------------------------------
const state = {
  section: localStorage.getItem("grader_section") || "check", // "check" | "glossary"
  mode: localStorage.getItem("grader_mode") || "tests", // "tests" | "bench"
  lastResult: null,
  selectedRow: null,
  selectedCase: null,
  explainOpen: false,
  commands: [], // fetched once from /api/commands
  paletteOpen: false,
  paletteActiveIndex: 0,
  paletteReturnFocus: null,
  theme: localStorage.getItem("grader_theme") || "system", // "system" | "light" | "dark"
  glossary: { query: "", cards: [], missing: [], selectedId: null, view: "cards" },
};

function getSelectedCase() {
  if (state.selectedRow == null || state.selectedCase == null) return null;
  const rows = state.lastResult && state.lastResult.rows;
  const row = rows && rows[state.selectedRow];
  return (row && row.cases && row.cases[state.selectedCase]) || null;
}

// -- Command registry: one filter, three surfaces (palette/action cards/scenario buttons) --

function contextTags() {
  const tags = new Set();
  const c = getSelectedCase();
  if (c) {
    if (c.stdin) tags.add("has_stdin");
    if (c.actual || c.expected) tags.add("has_output");
    if (["WA", "RE", "TLE"].includes(c.verdict)) tags.add("is_failure");
    if (c.glossary_ids && c.glossary_ids.length) tags.add("has_glossary");
  } else if (state.mode === "bench") {
    tags.add("bench_mode");
  } else if (state.lastResult && state.lastResult.rows && state.lastResult.rows.length) {
    if (state.lastResult.rows.every(r => r.status === "OK")) tags.add("all_ac");
  }
  return tags;
}

function visibleCommands() {
  const tags = contextTags();
  return state.commands.filter(cmd => cmd.when === "always" || tags.has(cmd.when));
}

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => {});
  }
}

const ACTION_HANDLERS = {
  run_again: () => grade(),
  copy_input: () => copyToClipboard((getSelectedCase() || {}).stdin || ""),
  copy_output: () => {
    const c = getSelectedCase() || {};
    copyToClipboard(c.actual || c.expected || "");
  },
  explain_error: () => toggleExplain(),
  open_glossary: () => openGlossaryForSelectedCase(),
  toggle_theme: () => cycleTheme(),
  switch_section: () => setSection(state.section === "check" ? "glossary" : "check"),
};

function runCommand(id) {
  const handler = ACTION_HANDLERS[id];
  if (handler) handler();
}

function renderCommandButtons(el, commands) {
  if (!el) return;
  el.innerHTML = commands
    .map(
      c =>
        '<button class="action-card" data-cmd="' + esc(c.id) + '">' + esc(c.title.ru) + "</button>"
    )
    .join("");
  el.querySelectorAll("[data-cmd]").forEach(btn =>
    btn.addEventListener("click", () => runCommand(btn.dataset.cmd))
  );
}

function renderScenarioButtons() {
  const el = $("#scenario-buttons");
  if (getSelectedCase()) {
    if (el) el.innerHTML = "";
    return;
  }
  renderCommandButtons(el, visibleCommands());
}

function renderActionCards() {
  const el = $("#detail-actions");
  if (!getSelectedCase()) {
    if (el) el.innerHTML = "";
    return;
  }
  renderCommandButtons(el, visibleCommands());
}

async function loadCommands() {
  try {
    const r = await fetch("/api/commands");
    state.commands = await r.json();
  } catch (e) {
    state.commands = [];
  }
  renderScenarioButtons();
}

// -- Command palette (Ctrl+K / ⌘K) -------------------------------------------

function paletteCommands() {
  const q = ($("#palette-input").value || "").trim().toLowerCase();
  let cmds = visibleCommands();
  if (q) {
    cmds = cmds.filter(
      c =>
        c.title.ru.toLowerCase().includes(q) ||
        c.title.en.toLowerCase().includes(q) ||
        (c.keywords || []).some(k => k.toLowerCase().includes(q))
    );
  }
  return cmds;
}

function openPalette() {
  state.paletteOpen = true;
  state.paletteActiveIndex = 0;
  state.paletteReturnFocus = document.activeElement;
  $("#palette-overlay").hidden = false;
  $("#palette-input").value = "";
  renderPaletteList();
  $("#palette-input").focus();
}

function closePalette() {
  state.paletteOpen = false;
  $("#palette-overlay").hidden = true;
  if (state.paletteReturnFocus && state.paletteReturnFocus.focus) {
    state.paletteReturnFocus.focus();
  }
}

function renderPaletteList() {
  const cmds = paletteCommands();
  const list = $("#palette-list");
  if (!cmds.length) {
    list.innerHTML = '<li class="empty">Ничего не найдено</li>';
    return;
  }
  state.paletteActiveIndex = Math.min(state.paletteActiveIndex, cmds.length - 1);
  list.innerHTML = cmds
    .map((c, i) => {
      const active = i === state.paletteActiveIndex ? " active" : "";
      const kbd = c.shortcut ? '<span class="kbd">' + esc(c.shortcut) + "</span>" : "";
      return (
        '<li data-idx="' + i + '" class="' + active + '"><span>' + esc(c.title.ru) + "</span>" + kbd + "</li>"
      );
    })
    .join("");
  list.querySelectorAll("li[data-idx]").forEach(li =>
    li.addEventListener("click", () => {
      const cmd = cmds[Number(li.dataset.idx)];
      closePalette();
      if (cmd) runCommand(cmd.id);
    })
  );
}

// -- Theme toggle -------------------------------------------------------------

function applyTheme() {
  const root = document.documentElement;
  if (state.theme === "system") {
    root.style.colorScheme = "light dark";
    root.removeAttribute("data-theme");
    $("#theme-toggle").textContent = "🌓";
  } else {
    root.style.colorScheme = state.theme;
    root.setAttribute("data-theme", state.theme);
    $("#theme-toggle").textContent = state.theme === "dark" ? "🌙" : "☀️";
  }
}

function cycleTheme() {
  state.theme = state.theme === "system" ? "light" : state.theme === "light" ? "dark" : "system";
  localStorage.setItem("grader_theme", state.theme);
  applyTheme();
}

// -- Section switch (Проверка решений / Глоссарий) ----------------------------

function setSection(section) {
  state.section = section;
  localStorage.setItem("grader_section", section);
  $("#sec-check").classList.toggle("active", section === "check");
  $("#sec-glossary").classList.toggle("active", section === "glossary");
  $("#view-check").hidden = section !== "check";
  $("#view-glossary").hidden = section !== "glossary";
  if (section === "glossary" && !state.glossary.cards.length) loadGlossary("");
}

function openGlossaryForSelectedCase() {
  const c = getSelectedCase();
  let id = null;
  if (c && c.glossary_ids && c.glossary_ids.length) {
    id = c.glossary_ids[0];
  } else if (c && c.glossary && c.glossary.url) {
    const m = c.glossary.url.match(/#(.+)$/);
    if (m) id = m[1];
  }
  setSection("glossary");
  if (id) selectGlossaryCard(id);
}

// -- Проверка решений: grade/render -------------------------------------------

function setMode(m) {
  state.mode = m;
  $("#m-tests").classList.toggle("active", m === "tests");
  $("#m-bench").classList.toggle("active", m === "bench");
  $("#repeats").style.display = m === "bench" ? "" : "none";
  localStorage.setItem("grader_mode", m);
}

async function grade() {
  const path = $("#path").value.trim();
  if (!path) return;
  localStorage.setItem("grader_path", path);
  addRecentPath(path);
  const btn = $("#run");
  btn.disabled = true;
  btn.textContent = "Проверка…";
  $("#bar").textContent = "";
  $("#out").innerHTML = "";
  state.selectedRow = null;
  state.selectedCase = null;
  state.explainOpen = false;
  const q = new URLSearchParams({ path, mode: state.mode });
  if (state.mode === "bench") q.set("repeats", $("#repeats").value);
  try {
    const r = await fetch("/api/grade?" + q.toString());
    const data = await r.json();
    state.lastResult = data;
    addHistoryEntry(path, state.mode, data);
    render(data);
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
  } finally {
    btn.disabled = false;
    btn.textContent = "Проверить";
    renderDetailPanel();
    renderScenarioButtons();
  }
}

function render(data) {
  if (data.kind === "error") {
    $("#out").innerHTML = '<p class="msg">' + esc(data.message) + "</p>";
    return;
  }
  (data.mode === "bench" ? renderBench : renderTests)(data.rows);
}

function renderTests(rows) {
  const ok = rows.filter(r => r.status === "OK").length;
  $("#bar").textContent =
    "Решений: " + rows.length + " · OK: " + ok + " · FAIL: " + (rows.length - ok);
  let h =
    '<table><thead><tr><th scope="col">Файл</th><th scope="col">Passed</th>' +
    '<th scope="col">Статус</th><th scope="col">Σ время</th><th scope="col">Avg</th>' +
    '<th scope="col">Память, МБ</th></tr></thead><tbody>';
  rows.forEach((row, i) => {
    const cls = row.status === "OK" ? "OK" : "FAIL";
    h +=
      '<tr><td class="file" data-toggle="' + i + '">' + esc(row.file) + '</td>' +
      '<td class="num">' + (row.passed ?? 0) + "/" + (row.total ?? 0) + "</td>" +
      '<td class="badge ' + cls + '">' + esc(row.status) + "</td>" +
      '<td class="num">' + (row.total_time ?? "—") + "</td>" +
      '<td class="num">' + (row.avg_time ?? "—") + "</td>" +
      '<td class="num">' + (row.memory_mb ?? "—") + "</td></tr>";
    h +=
      '<tr class="caserow" id="c' + i + '" style="display:none"><td colspan="6">' +
      casesHtml(i, row.cases) +
      "</td></tr>";
  });
  $("#out").innerHTML = h + "</tbody></table>";
  $("#out")
    .querySelectorAll("[data-toggle]")
    .forEach(td => td.addEventListener("click", () => toggleRow(Number(td.dataset.toggle))));
  wireCaseRowClicks();
}

function casesHtml(rowIndex, cases) {
  if (!cases || !cases.length) return "<em>нет тест-кейсов</em>";
  return (
    "<table>" +
    cases
      .map((c, j) => {
        const sel = state.selectedRow === rowIndex && state.selectedCase === j ? " selected" : "";
        return (
          '<tr class="case-row' + sel + '" data-row="' + rowIndex + '" data-case="' + j + '">' +
          '<td><span class="badge ' + (c.verdict === "AC" ? "OK" : "FAIL") + '">#' +
          c.n + " " + esc(c.verdict) + "</span></td>" +
          '<td class="num">' + c.time + " s</td></tr>"
        );
      })
      .join("") +
    "</table>"
  );
}

function wireCaseRowClicks() {
  $("#out")
    .querySelectorAll("tr.case-row")
    .forEach(tr =>
      tr.addEventListener("click", () => selectCase(Number(tr.dataset.row), Number(tr.dataset.case)))
    );
}

function toggleRow(i) {
  const el = $("#c" + i);
  el.style.display = el.style.display === "none" ? "table-row" : "none";
}

function selectCase(rowIndex, caseIndex) {
  state.selectedRow = rowIndex;
  state.selectedCase = caseIndex;
  state.explainOpen = false;
  highlightSelectedCaseRow();
  renderDetailPanel();
  renderScenarioButtons();
}

function highlightSelectedCaseRow() {
  document.querySelectorAll("tr.case-row.selected").forEach(tr => tr.classList.remove("selected"));
  if (state.selectedRow == null) return;
  const sel = document.querySelector(
    'tr.case-row[data-row="' + state.selectedRow + '"][data-case="' + state.selectedCase + '"]'
  );
  if (sel) sel.classList.add("selected");
}

function toggleExplain() {
  state.explainOpen = !state.explainOpen;
  renderDetailPanel();
}

function renderDetailPanel() {
  const c = getSelectedCase();
  const empty = $("#detail-empty");
  const content = $("#detail-content");
  if (!c) {
    empty.hidden = false;
    content.hidden = true;
    content.innerHTML = "";
    return;
  }
  empty.hidden = true;
  content.hidden = false;

  let h =
    '<div class="bar">#' + c.n + ' <span class="badge ' + esc(c.verdict) + '">' + esc(c.verdict) +
    "</span> · " + c.time + " s</div>";
  if (c.stdin) h += '<div class="field-label">Вход (stdin)</div><pre>' + esc(c.stdin) + "</pre>";
  if (c.verdict === "WA") {
    h += '<div class="field-label">Ожидалось</div><pre>' + esc(c.expected) + "</pre>";
    h += '<div class="field-label">Получено</div><pre>' + esc(c.actual) + "</pre>";
    if (c.diff) h += '<div class="field-label">Diff</div><pre>' + esc(c.diff) + "</pre>";
  } else if (c.actual) {
    h += '<div class="field-label">Вывод</div><pre>' + esc(c.actual) + "</pre>";
  }
  if (c.verdict === "RE" || c.verdict === "TLE") {
    h += '<div class="field-label">Диагностика</div><pre>' + esc(c.stderr || c.error || "") + "</pre>";
    if (c.exit_code != null) h += '<div class="hint">exit code: ' + esc(c.exit_code) + "</div>";
    if (c.timeout_s != null) h += '<div class="hint">timeout: ' + esc(c.timeout_s) + " s</div>";
  }
  if (c.glossary) h += errorCard(c.glossary);
  if (state.explainOpen && c.suggestions && c.suggestions.length) {
    h +=
      '<div class="errcard severity-' + esc(c.severity || "error") + '"><strong>Подсказка:</strong> ' +
      c.suggestions.map(esc).join(" ") +
      "</div>";
  }
  h += '<div id="detail-actions" class="action-cards"></div>';
  content.innerHTML = h;
  renderActionCards();
}

function renderBench(rows) {
  const ranked = rows.filter(r => !r.error);
  const fast = ranked.length
    ? " · быстрейшее: " + esc(ranked[0].file) + " (" + esc(ranked[0].median) + ")"
    : "";
  $("#bar").textContent = "Решений: " + rows.length + fast;
  let h =
    '<table><thead><tr><th scope="col">Файл</th><th scope="col">Runs</th>' +
    '<th scope="col">Min</th><th scope="col">Median</th><th scope="col">Отн.</th>' +
    '<th scope="col">Вердикт</th><th scope="col">Память, МБ</th></tr></thead><tbody>';
  rows.forEach(row => {
    if (row.error) {
      h +=
        "<tr><td>" + esc(row.file) + '</td><td colspan="5" class="ERR">' + esc(row.error) +
        "</td><td></td></tr>";
      return;
    }
    h +=
      "<tr><td>" + esc(row.file) + '</td><td class="num">' + row.runs + "</td>" +
      '<td class="num">' + esc(row.min) + "</td>" +
      '<td class="num">' + esc(row.median) + "</td>" +
      '<td class="num">' + row.relative + "%</td>" +
      '<td class="badge ' + esc(row.verdict) + '">' + esc(row.verdict) + "</td>" +
      '<td class="num">' + (row.memory_mb ?? "—") + "</td></tr>";
  });
  $("#out").innerHTML = h + "</tbody></table>";
}

function errorCard(g) {
  return (
    '<div class="errcard"><span class="errcard-ex">💡 ' + esc(g.exception) + "</span> " +
    esc(g.hint) +
    ' <a href="' + esc(g.url) + '" target="_blank" rel="noopener">' +
    "открыть карточку в глоссарии →</a></div>"
  );
}

// -- Sidebar: история / недавние пути -----------------------------------------

function addRecentPath(path) {
  let recent = JSON.parse(localStorage.getItem("grader_recent_paths") || "[]");
  recent = [path, ...recent.filter(p => p !== path)].slice(0, 8);
  localStorage.setItem("grader_recent_paths", JSON.stringify(recent));
  renderRecentPaths(recent);
}

function renderRecentPaths(recent) {
  recent = recent || JSON.parse(localStorage.getItem("grader_recent_paths") || "[]");
  const el = $("#recent-paths-list");
  if (!recent.length) {
    el.innerHTML = '<li class="empty">Пока пусто</li>';
    return;
  }
  el.innerHTML = recent.map(p => '<li data-path="' + esc(p) + '">' + esc(p) + "</li>").join("");
  el.querySelectorAll("li[data-path]").forEach(li =>
    li.addEventListener("click", () => {
      $("#path").value = li.dataset.path;
      grade();
    })
  );
}

function addHistoryEntry(path, mode, data) {
  const rows = data.rows || [];
  const summary =
    data.kind === "error"
      ? "ошибка"
      : mode === "bench"
        ? "бенчмарк · " + rows.length
        : rows.filter(r => r.status === "OK").length + "/" + rows.length + " OK";
  let history = JSON.parse(localStorage.getItem("grader_history") || "[]");
  history = [{ path, mode, summary }, ...history].slice(0, 10);
  localStorage.setItem("grader_history", JSON.stringify(history));
  renderHistory(history);
}

function renderHistory(history) {
  history = history || JSON.parse(localStorage.getItem("grader_history") || "[]");
  const el = $("#history-list");
  if (!history.length) {
    el.innerHTML = '<li class="empty">Пока пусто</li>';
    return;
  }
  el.innerHTML = history
    .map(h => {
      const name = h.path.split(/[\\/]/).pop();
      return (
        '<li data-path="' + esc(h.path) + '" data-mode="' + esc(h.mode) + '">' +
        esc(name) + " — " + esc(h.summary) + "</li>"
      );
    })
    .join("");
  el.querySelectorAll("li[data-path]").forEach(li =>
    li.addEventListener("click", () => {
      $("#path").value = li.dataset.path;
      setMode(li.dataset.mode);
      grade();
    })
  );
}

// -- Глоссарий: поиск / карточка / очередь пополнения (J7) --------------------

async function loadGlossary(query) {
  state.glossary.query = query;
  try {
    const r = await fetch("/api/glossary?" + new URLSearchParams({ q: query }));
    state.glossary.cards = await r.json();
  } catch (e) {
    state.glossary.cards = [];
  }
  renderGlossaryList();
}

async function loadMissing() {
  try {
    const r = await fetch("/api/glossary/missing");
    state.glossary.missing = await r.json();
  } catch (e) {
    state.glossary.missing = [];
  }
  renderGlossaryMissing();
}

function renderGlossaryList() {
  const el = $("#glossary-cards");
  if (!state.glossary.cards.length) {
    el.innerHTML = '<li class="empty">Ничего не найдено</li>';
    return;
  }
  el.innerHTML = state.glossary.cards
    .map(c => {
      const sel = c.id === state.glossary.selectedId ? " selected" : "";
      return '<li data-id="' + esc(c.id) + '" class="' + sel + '">' + esc(c.title) + "</li>";
    })
    .join("");
  el.querySelectorAll("li[data-id]").forEach(li =>
    li.addEventListener("click", () => selectGlossaryCard(li.dataset.id))
  );
}

function renderGlossaryMissing() {
  const el = $("#glossary-missing");
  if (!state.glossary.missing.length) {
    el.innerHTML = '<li class="empty">Пусто</li>';
    return;
  }
  el.innerHTML = state.glossary.missing
    .map(e => "<li>" + esc(e.concept) + ' <span class="hint">(' + esc(e.kind) + ")</span></li>")
    .join("");
}

function selectGlossaryCard(id) {
  state.glossary.selectedId = id;
  renderGlossaryList();
  const card = state.glossary.cards.find(c => c.id === id);
  if (card) {
    renderGlossaryDetail(card);
    return;
  }
  // Deep-link (open_glossary) может указывать на карточку вне текущей выборки поиска.
  fetch("/api/glossary/" + encodeURIComponent(id))
    .then(r => (r.ok ? r.json() : null))
    .then(c => {
      if (c) renderGlossaryDetail(c);
    })
    .catch(() => {});
}

function renderGlossaryDetail(card) {
  $("#glossary-empty").hidden = true;
  const el = $("#glossary-detail-content");
  el.hidden = false;
  el.innerHTML =
    "<h2>" + esc(card.title) + "</h2>" +
    '<div class="hint">' + esc(card.kind) + (card.section ? " · " + esc(card.section) : "") + "</div>" +
    (card.summary ? "<p>" + esc(card.summary) + "</p>" : "") +
    (card.body ? "<div>" + esc(card.body) + "</div>" : "") +
    (card.url
      ? '<p><a href="' + esc(card.url) + '" target="_blank" rel="noopener">Открыть во внешнем глоссарии →</a></p>'
      : "");
}

function setGlossaryView(view) {
  state.glossary.view = view;
  $("#gl-view-cards").classList.toggle("active", view === "cards");
  $("#gl-view-missing").classList.toggle("active", view === "missing");
  $("#glossary-cards").hidden = view !== "cards";
  $("#glossary-missing").hidden = view !== "missing";
  if (view === "missing" && !state.glossary.missing.length) loadMissing();
}

// -- Split-pane resizable dividers --------------------------------------------

function makeResizable(dividerId, panelSelector, storageKey, sign) {
  const divider = $(dividerId);
  const panel = $(panelSelector);
  if (!divider || !panel) return;
  const saved = localStorage.getItem(storageKey);
  if (saved) panel.style.flexBasis = saved + "px";
  let dragging = false;
  let startX = 0;
  let startWidth = 0;
  divider.addEventListener("mousedown", e => {
    if (window.matchMedia("(max-width: 860px)").matches) return;
    dragging = true;
    startX = e.clientX;
    startWidth = panel.getBoundingClientRect().width;
    divider.classList.add("dragging");
    e.preventDefault();
  });
  window.addEventListener("mousemove", e => {
    if (!dragging) return;
    const w = Math.max(120, startWidth + sign * (e.clientX - startX));
    panel.style.flexBasis = w + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    divider.classList.remove("dragging");
    localStorage.setItem(storageKey, String(Math.round(panel.getBoundingClientRect().width)));
  });
}

// -- Wiring / init -------------------------------------------------------------

document
  .querySelectorAll("[data-section]")
  .forEach(b => b.addEventListener("click", () => setSection(b.dataset.section)));
document
  .querySelectorAll("[data-mode]")
  .forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
document
  .querySelectorAll("[data-glview]")
  .forEach(b => b.addEventListener("click", () => setGlossaryView(b.dataset.glview)));

$("#run").addEventListener("click", grade);
$("#path").addEventListener("keydown", e => {
  if (e.key === "Enter") grade();
});
$("#theme-toggle").addEventListener("click", cycleTheme);
$("#palette-btn").addEventListener("click", openPalette);
$("#palette-overlay").addEventListener("click", e => {
  if (e.target.id === "palette-overlay") closePalette();
});

let glossarySearchTimer = null;
$("#glossary-search").addEventListener("input", e => {
  clearTimeout(glossarySearchTimer);
  glossarySearchTimer = setTimeout(() => loadGlossary(e.target.value), 200);
});

$("#palette-input").addEventListener("input", () => {
  state.paletteActiveIndex = 0;
  renderPaletteList();
});
$("#palette-input").addEventListener("keydown", e => {
  const cmds = paletteCommands();
  if (e.key === "ArrowDown") {
    e.preventDefault();
    state.paletteActiveIndex = Math.min(state.paletteActiveIndex + 1, cmds.length - 1);
    renderPaletteList();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    state.paletteActiveIndex = Math.max(state.paletteActiveIndex - 1, 0);
    renderPaletteList();
  } else if (e.key === "Enter") {
    e.preventDefault();
    const cmd = cmds[state.paletteActiveIndex];
    closePalette();
    if (cmd) runCommand(cmd.id);
  } else if (e.key === "Escape") {
    e.preventDefault();
    closePalette();
  }
});
document.addEventListener("keydown", e => {
  const key = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && key === "k") {
    e.preventDefault();
    if (state.paletteOpen) closePalette();
    else openPalette();
  } else if (e.key === "Escape" && state.paletteOpen) {
    closePalette();
  }
});

applyTheme();
setSection(state.section);
setMode(state.mode);
renderHistory();
renderRecentPaths();
loadCommands();
makeResizable("#divider-1", "#sidebar", "grader_w_sidebar", 1);
makeResizable("#divider-2", "#detail-panel", "grader_w_detail", -1);
makeResizable("#divider-3", "#glossary-list-panel", "grader_w_glossary_list", 1);

// Восстановить последний путь.
const saved = localStorage.getItem("grader_path");
if (saved) $("#path").value = saved;
