// app.js — клиентская логика веб-интерфейса грейдера (issue #58, эпик #80; issue #125;
// редизайн под маску эпика #123).
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
  mode: localStorage.getItem("grader_mode") || "tests", // "file" | "tests" | "bench" | "microbench"
  configTab: "path", // "path" | "params"
  resultTab: "table", // "table" | "detail" | "log" | "reference"
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
  solutions: [], // режим 1 — файлы, найденные /api/solutions в указанной папке
  selectedSolutionFile: null, // режим 1 — выбранный для проверки файл (полный путь)
  activeRunId: null, // issue #262 — id текущего опрашиваемого async job (bench/microbench)
};
// Старый "compare" (до фикса режима 1) мог остаться в localStorage — откатываемся
// на дефолт, а не оставляем режим, для которого больше нет кнопки/логики.
if (!["file", "tests", "bench", "microbench"].includes(state.mode)) state.mode = "tests";

function getSelectedCase() {
  if (state.selectedRow == null || state.selectedCase == null) return null;
  const rows = state.lastResult && state.lastResult.rows;
  const row = rows && rows[state.selectedRow];
  return (row && row.cases && row.cases[state.selectedCase]) || null;
}

// ---------------------------------------------------------------------------
// UI-примитивы (issue #123-redesign) — переиспользуемые рендер-хелперы поверх
// design-токенов макета: badge/kpi/code-block/skeleton.
// ---------------------------------------------------------------------------

const VERDICT_BADGE = {
  AC: "badge badge-success", OK: "badge badge-success",
  WA: "badge badge-error", FAIL: "badge badge-error",
  RE: "badge badge-error", ERR: "badge badge-error",
  TLE: "badge badge-warning",
  "NO TESTS": "badge badge-neutral",
  SIMILAR: "verdict-similar",
  SLOWER: "verdict-slower", MUCH_SLOWER: "verdict-slower",
  FASTER: "verdict-faster",
  REFERENCE: "badge badge-primary",
};

function renderVerdict(v) {
  const cls = VERDICT_BADGE[v] || "badge badge-neutral";
  return '<span class="' + cls + '">' + esc(v) + "</span>";
}

function kpiCard(label, value, delta, deltaVariant) {
  return (
    '<div class="kpi-card"><div class="kpi-label">' + esc(label) + '</div>' +
    '<div class="kpi-value">' + esc(value) + "</div>" +
    (delta ? '<div class="kpi-delta ' + esc(deltaVariant || "neutral") + '">' + esc(delta) + "</div>" : "") +
    "</div>"
  );
}

function kpiGrid(items) {
  return '<div class="kpi-grid">' + items.map(i => kpiCard(i.label, i.value, i.delta, i.variant)).join("") + "</div>";
}

function codeBlock(code) {
  return '<div class="code-block">' + esc(code) + "</div>";
}

function skeletonBlock() {
  return (
    '<div style="padding:var(--space-4)">' +
    '<div class="skeleton skeleton-heading"></div>' +
    '<div class="skeleton skeleton-text"></div>' +
    '<div class="skeleton skeleton-text"></div>' +
    '<div class="skeleton skeleton-text"></div>' +
    "</div>"
  );
}

function emptyState(title, hint) {
  return (
    '<div class="empty-state"><h3>' + esc(title) + "</h3>" +
    (hint ? "<p>" + esc(hint) + "</p>" : "") +
    "</div>"
  );
}

// -- Command registry: one filter, two surfaces (command palette / action cards) --

function contextTags() {
  const tags = new Set();
  const c = getSelectedCase();
  if (c) {
    if (c.stdin) tags.add("has_stdin");
    if (c.actual || c.expected) tags.add("has_output");
    if (["WA", "RE", "TLE"].includes(c.verdict)) tags.add("is_failure");
    if (c.glossary_ids && c.glossary_ids.length) tags.add("has_glossary");
  } else if (state.mode === "bench" || state.mode === "microbench") {
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
    root.removeAttribute("data-theme");
    $("#theme-toggle").textContent = "🌓";
  } else {
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
  document.querySelectorAll("[data-section]").forEach(a => {
    const active = a.dataset.section === section;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("#view-check").hidden = section !== "check";
  $("#view-downloader").hidden = section !== "downloader";
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

// -- Config-panel tabs (Путь / Параметры) -------------------------------------

function setConfigTab(tab) {
  state.configTab = tab;
  document.querySelectorAll("[data-conftab]").forEach(b => {
    const active = b.dataset.conftab === tab;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  $("#conftab-path").hidden = tab !== "path";
  $("#conftab-params").hidden = tab !== "params";
}

// -- Result-panel tabs (Таблица / Детали / Лог / Эталон) ----------------------

function setResultTab(tab) {
  state.resultTab = tab;
  document.querySelectorAll("[data-restab]").forEach(b => {
    const active = b.dataset.restab === tab;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  $("#restab-table").hidden = tab !== "table";
  $("#restab-detail").hidden = tab !== "detail";
  $("#restab-log").hidden = tab !== "log";
  $("#restab-reference").hidden = tab !== "reference";
  if (tab === "log") renderLogTab();
  if (tab === "reference") renderReferenceTab();
}

function renderLogTab() {
  const el = $("#log-content");
  const c = getSelectedCase();
  if (!c) {
    el.innerHTML = emptyState("Нет данных", "Выберите тест-кейс во вкладке «Таблица».");
    return;
  }
  let h = "";
  if (c.stdin) h += '<div class="field-label">stdin</div>' + codeBlock(c.stdin);
  const out = c.actual || c.stderr || c.error || "";
  h += '<div class="field-label">stdout/stderr</div>' + (out ? codeBlock(out) : codeBlock("(пусто)"));
  el.innerHTML = h;
}

function renderReferenceTab() {
  const el = $("#reference-content");
  const src = state.lastResult && state.lastResult.reference_source;
  if (!src) {
    el.innerHTML = emptyState(
      "Эталон не выбран",
      "«Найти эталонное решение» пока не реализовано (issue #55) — здесь появится его код."
    );
    return;
  }
  el.innerHTML = '<div class="field-label">' + esc(state.lastResult.reference_file || "reference") + "</div>" + codeBlock(src);
}

// -- Проверка решений: grade/render -------------------------------------------

function setMode(m) {
  state.mode = m;
  document.querySelectorAll("[data-mode]").forEach(b => {
    const active = b.dataset.mode === m;
    b.classList.toggle("active", active);
    b.setAttribute("aria-pressed", String(active));
  });
  $("#file-picker-group").hidden = m !== "file";
  $("#microbench-config").hidden = m !== "microbench";
  const repeatsGroup = $("#repeats").closest(".form-group");
  if (repeatsGroup) repeatsGroup.hidden = m !== "bench";
  updateParamsTabAvailability(m);
  resetFilePicker();
  localStorage.setItem("grader_mode", m);
  if (m === "microbench") updateMicroCustomVisibility();
}

// -- Режим 4 «Microbench» — профиль-селектор (_MICRO_PROFILES из cli/interactive.py) --

function updateMicroCustomVisibility() {
  const group = $("#micro-custom-group");
  if (group) group.hidden = $("#micro-profile").value !== "custom";
}

function getMicroNumber() {
  const profile = $("#micro-profile").value;
  if (profile !== "custom") return Number(profile);
  const custom = Number($("#micro-custom").value) || 1000;
  return Math.min(500000, Math.max(100, custom));
}

// -- Режим 1 «Один файл» — параметры недоступны, скрыть вкладку целиком;
// режим 2 «Тесты» — параметров реально нет (repeats только для bench), вкладка
// видна, но серая/некликабельная; bench/microbench — вкладка активна.
function updateParamsTabAvailability(m) {
  const tab = document.querySelector('[data-conftab="params"]');
  if (!tab) return;
  tab.hidden = m === "file";
  const disabled = m === "tests";
  tab.classList.toggle("disabled", disabled);
  tab.setAttribute("aria-disabled", String(disabled));
  if ((tab.hidden || disabled) && state.configTab === "params") setConfigTab("path");
}

function updateRunButtonState() {
  if (state.mode !== "file") {
    $("#run").disabled = false;
    return;
  }
  const hasFolder = $("#path").value.trim().length > 0;
  const hasCode = $("#solution-editor").value.trim().length > 0;
  $("#run").disabled = !(hasFolder && hasCode);
}

function resetFilePicker() {
  state.solutions = [];
  state.selectedSolutionFile = null;
  const list = $("#solutions-list");
  if (list) list.innerHTML = "";
  $("#solution-editor").value = "";
  updateRunButtonState();
}

async function findSolutions() {
  state.selectedSolutionFile = null;
  $("#solution-editor").value = "";
  updateRunButtonState();
  await refreshSolutionsList();
}

// Перезагрузить список решений без сброса выбора/содержимого редактора —
// используется после save-solution, чтобы показать новый/изменённый файл
// (findSolutions() выше сбрасывает выбор — это нужно только для явного
// клика «Найти решения»).
async function refreshSolutionsList() {
  const folder = $("#path").value.trim();
  const list = $("#solutions-list");
  if (!folder) return;
  list.innerHTML = '<li class="empty">Поиск…</li>';
  try {
    const r = await fetch("/api/solutions?" + new URLSearchParams({ path: folder }));
    const data = await r.json();
    if (data.kind === "error") {
      state.solutions = [];
      list.innerHTML = '<li class="empty">' + esc(data.message) + "</li>";
      return;
    }
    state.solutions = data.files || [];
    renderSolutionsList();
  } catch (e) {
    state.solutions = [];
    list.innerHTML = '<li class="empty">Ошибка запроса.</li>';
  }
}

function renderSolutionsList() {
  const el = $("#solutions-list");
  if (!state.solutions.length) {
    el.innerHTML = '<li class="empty">Решения не найдены.</li>';
    return;
  }
  el.innerHTML = state.solutions
    .map(f => {
      const name = f.split(/[\\/]/).pop();
      const sel = f === state.selectedSolutionFile ? " selected" : "";
      return '<li data-file="' + esc(f) + '" class="' + sel + '">' + esc(name) + "</li>";
    })
    .join("");
  el.querySelectorAll("li[data-file]").forEach(li =>
    li.addEventListener("click", () => selectSolutionFile(li.dataset.file))
  );
}

async function selectSolutionFile(fullPath) {
  state.selectedSolutionFile = fullPath;
  renderSolutionsList();
  const editor = $("#solution-editor");
  try {
    const r = await fetch("/api/source?" + new URLSearchParams({ path: fullPath }));
    const data = await r.json();
    editor.value = data.kind === "error" ? "" : data.source;
  } catch (e) {
    editor.value = "";
  }
  updateRunButtonState();
}

async function grade() {
  const isFileMode = state.mode === "file";
  if (isFileMode) {
    const folder = $("#path").value.trim();
    const code = $("#solution-editor").value;
    if (!folder || !code.trim()) return;
    try {
      const saveResp = await fetch("/api/save-solution", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder, path: state.selectedSolutionFile || null, code }),
      });
      const saved = await saveResp.json();
      if (!saved.ok) {
        $("#out").innerHTML = '<p class="msg">' + esc(saved.message) + "</p>";
        return;
      }
      state.selectedSolutionFile = saved.path;
      await refreshSolutionsList();
    } catch (e) {
      $("#out").innerHTML = '<p class="msg">Не удалось сохранить код: ' + esc(String(e)) + "</p>";
      return;
    }
  }
  const path = isFileMode ? state.selectedSolutionFile || "" : $("#path").value.trim();
  if (!path) return;
  const folder = $("#path").value.trim();
  if (folder) {
    localStorage.setItem("grader_path", folder);
    addRecentPath(folder);
  }
  const btn = $("#run");
  btn.disabled = true;
  btn.textContent = "Проверка…";
  $("#bar").innerHTML = "";
  $("#out").innerHTML = skeletonBlock();
  state.selectedRow = null;
  state.selectedCase = null;
  state.explainOpen = false;
  // issue #262 — любой новый запуск (любого режима) обесценивает предыдущий
  // async job: его poll-цикл, если ещё жив, увидит несовпадение id и молча
  // остановится, не трогая DOM текущего запуска (см. gradeAsync()).
  state.activeRunId = null;
  $("#cancel-run").hidden = true;
  const backendMode = state.mode === "bench" || state.mode === "microbench" ? state.mode : "tests";
  if (backendMode === "bench" || backendMode === "microbench") {
    await gradeAsync(path, backendMode);
    return;
  }
  const q = new URLSearchParams({ path, mode: backendMode });
  try {
    const r = await fetch("/api/grade?" + q.toString());
    const data = await r.json();
    state.lastResult = data;
    addHistoryEntry(path, state.mode, data);
    render(data);
    updateCheckSidebarBadge(data);
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
  } finally {
    _finishGradeUI();
  }
}

function _finishGradeUI() {
  updateRunButtonState();
  $("#run").textContent = "▶ Запустить";
  renderDetailPanel();
  renderResultSummaryBadges();
  if (state.resultTab === "log") renderLogTab();
  if (state.resultTab === "reference") renderReferenceTab();
}

// issue #262 — async job model (bench/microbench): POST /api/v1/runs, затем
// polling GET /api/v1/runs/{id} каждые 600мс до status="done"/"error",
// вместо единственного блокирующего /api/grade (который держал бы вкладку
// открытой на всю длительность бенчмарка). Режимы 1/2 (файл/тесты) не
// затронуты — остаются на синхронном пути в grade().
async function gradeAsync(path, backendMode) {
  const params = {};
  if (backendMode === "bench") params.repeats = Number($("#repeats").value);
  if (backendMode === "microbench") params.number = getMicroNumber();

  let created;
  try {
    const createResp = await fetch("/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, mode: backendMode, params }),
    });
    created = await createResp.json();
    if (createResp.status !== 202) {
      $("#out").innerHTML =
        '<p class="msg">' + esc(created.message || "Не удалось запустить задачу.") + "</p>";
      _finishGradeUI();
      return;
    }
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
    _finishGradeUI();
    return;
  }

  const runId = created.run_id;
  state.activeRunId = runId;
  const cancelBtn = $("#cancel-run");
  cancelBtn.hidden = false;
  cancelBtn.disabled = false;

  await new Promise(resolve => {
    const poll = async () => {
      if (state.activeRunId !== runId) return resolve(); // superseded — stop quietly
      let data;
      try {
        const statusResp = await fetch("/api/v1/runs/" + runId);
        if (state.activeRunId !== runId) return resolve(); // superseded while awaiting
        data = await statusResp.json();
      } catch (e) {
        if (state.activeRunId !== runId) return resolve();
        $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
        return resolve();
      }
      updateProgressBar(data.progress.done, data.progress.total);
      if (data.status === "queued" || data.status === "running") {
        setTimeout(poll, 600);
        return;
      }
      cancelBtn.hidden = true;
      $("#bar").innerHTML = "";
      if (data.status === "done") {
        state.lastResult = data.result;
        addHistoryEntry(path, state.mode, data.result);
        render(data.result);
        updateCheckSidebarBadge(data.result);
      } else {
        $("#out").innerHTML =
          '<p class="msg">' + esc(data.message || "Задача завершилась с ошибкой.") + "</p>";
      }
      resolve();
    };
    poll();
  });

  _finishGradeUI();
}

function updateProgressBar(done, total) {
  if (!total) {
    $("#bar").innerHTML =
      '<div class="progress-track"><div class="progress-fill progress-indeterminate"></div></div>';
    return;
  }
  const pct = Math.min(100, Math.round((done / total) * 100));
  $("#bar").innerHTML =
    '<div class="progress-track"><div class="progress-fill" style="width:' + pct + '%"></div></div>' +
    '<div class="progress-label">' + done + " / " + total + "</div>";
}

function cancelActiveRun() {
  if (!state.activeRunId) return;
  const cancelBtn = $("#cancel-run");
  cancelBtn.disabled = true;
  fetch("/api/v1/runs/" + state.activeRunId + "/cancel", { method: "POST" }).catch(() => {});
}

function updateCheckSidebarBadge(data) {
  const el = $("#sidebar-badge-check");
  const n = (data && data.rows && data.rows.length) || 0;
  el.textContent = String(n);
  el.hidden = n === 0;
}

function render(data) {
  if (data.kind === "error") {
    $("#out").innerHTML = '<p class="msg">' + esc(data.message) + "</p>";
    return;
  }
  if (data.mode === "bench") return renderBench(data.rows);
  if (data.mode === "microbench") return renderMicrobench(data);
  renderTests(data.rows);
}

function renderResultSummaryBadges() {
  const el = $("#result-summary-badges");
  const data = state.lastResult;
  if (!el) return;
  if (!data || data.kind === "error" || !data.rows || !data.rows.length) {
    el.innerHTML = "";
    return;
  }
  if (data.mode === "bench" || data.mode === "microbench") {
    const counts = {};
    data.rows.forEach(r => {
      if (r.verdict) counts[r.verdict] = (counts[r.verdict] || 0) + 1;
    });
    el.innerHTML = Object.entries(counts)
      .map(([v, n]) => renderVerdict(v).replace("</span>", " ×" + n + "</span>"))
      .join(" ");
  } else {
    const ok = data.rows.filter(r => r.status === "OK").length;
    el.innerHTML =
      renderVerdict("OK").replace("</span>", " ×" + ok + "</span>") +
      " " +
      renderVerdict("FAIL").replace("</span>", " ×" + (data.rows.length - ok) + "</span>");
  }
}

function renderTests(rows) {
  const ok = rows.filter(r => r.status === "OK").length;
  $("#bar").textContent = "";
  let h = kpiGrid([
    { label: "Решений", value: rows.length },
    { label: "OK", value: ok, delta: rows.length ? Math.round((ok / rows.length) * 100) + "%" : "", variant: "up" },
    { label: "FAIL", value: rows.length - ok, variant: (rows.length - ok) ? "down" : "neutral" },
  ]);
  h += '<div class="data-table-wrap" style="padding:0 var(--space-4) var(--space-4)">' +
    '<table class="data-table"><thead><tr><th scope="col">Файл</th><th scope="col">Passed</th>' +
    '<th scope="col">Статус</th><th scope="col">Σ время</th><th scope="col">Avg</th>' +
    '<th scope="col">Память, МБ</th></tr></thead><tbody>';
  rows.forEach((row, i) => {
    h +=
      '<tr><td class="file-cell mono" data-toggle="' + i + '">' + esc(row.file) + '</td>' +
      '<td class="mono">' + (row.passed ?? 0) + "/" + (row.total ?? 0) + "</td>" +
      "<td>" + renderVerdict(row.status) + "</td>" +
      '<td class="mono">' + (row.total_time ?? "—") + "</td>" +
      '<td class="mono">' + (row.avg_time ?? "—") + "</td>" +
      '<td class="mono">' + (row.memory_mb ?? "—") + "</td></tr>";
    h +=
      '<tr class="caserow" id="c' + i + '" style="display:none"><td colspan="6">' +
      casesHtml(i, row.cases) +
      "</td></tr>";
  });
  $("#out").innerHTML = h + "</tbody></table></div>";
  $("#out")
    .querySelectorAll("[data-toggle]")
    .forEach(td => td.addEventListener("click", () => toggleRow(Number(td.dataset.toggle))));
  wireCaseRowClicks();
}

function casesHtml(rowIndex, cases) {
  if (!cases || !cases.length) return "<em>нет тест-кейсов</em>";
  return (
    '<table class="data-table">' +
    cases
      .map((c, j) => {
        const sel = state.selectedRow === rowIndex && state.selectedCase === j ? " selected" : "";
        return (
          '<tr class="case-row' + sel + '" data-row="' + rowIndex + '" data-case="' + j + '">' +
          "<td>#" + c.n + " " + renderVerdict(c.verdict) + "</td>" +
          '<td class="mono">' + c.time + " s</td></tr>"
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
  setResultTab("detail");
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
    '<div class="bar">#' + c.n + " " + renderVerdict(c.verdict) + " · " + c.time + " s</div>";
  if (c.stdin) h += '<div class="field-label">Вход (stdin)</div>' + codeBlock(c.stdin);
  if (c.verdict === "WA") {
    h += '<div class="field-label">Ожидалось</div>' + codeBlock(c.expected);
    h += '<div class="field-label">Получено</div>' + codeBlock(c.actual);
    if (c.diff) h += '<div class="field-label">Diff</div>' + codeBlock(c.diff);
  } else if (c.actual) {
    h += '<div class="field-label">Вывод</div>' + codeBlock(c.actual);
  }
  if (c.verdict === "RE" || c.verdict === "TLE") {
    h += '<div class="field-label">Диагностика</div>' + codeBlock(c.stderr || c.error || "");
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
  const refRow = rows.find(r => r.verdict === "REFERENCE");
  const best = refRow || ranked[0];
  const similarCount = ranked.filter(r => r.verdict === "SIMILAR" || r.verdict === "REFERENCE").length;
  $("#bar").textContent = "";
  let h = kpiGrid([
    { label: "Файлов", value: rows.length },
    { label: best && best.verdict === "REFERENCE" ? "Эталон" : "Медиана", value: best ? best.median : "—" },
    { label: "Схожих", value: ranked.length ? similarCount + " / " + ranked.length : "—" },
  ]);
  h += '<div class="data-table-wrap" style="padding:0 var(--space-4) var(--space-4)">' +
    '<table class="data-table"><thead><tr><th scope="col">Файл</th><th scope="col">Runs</th>' +
    '<th scope="col">Min</th><th scope="col">Median</th><th scope="col">%</th>' +
    '<th scope="col">Вердикт</th><th scope="col">Память, МБ</th></tr></thead><tbody>';
  rows.forEach(row => {
    if (row.error) {
      h +=
        '<tr><td class="mono">' + esc(row.file) + '</td><td colspan="5">' + esc(row.error) +
        "</td><td></td></tr>";
      return;
    }
    h +=
      '<tr><td class="mono">' + esc(row.file) + '</td><td class="mono">' + row.runs + "</td>" +
      '<td class="mono">' + esc(row.min) + "</td>" +
      '<td class="mono">' + esc(row.median) + "</td>" +
      '<td class="mono">' + row.relative + "%</td>" +
      "<td>" + renderVerdict(row.verdict) + "</td>" +
      '<td class="mono">' + (row.memory_mb ?? "—") + "</td></tr>";
  });
  $("#out").innerHTML = h + "</tbody></table></div>";
}

function renderMicrobench(data) {
  const rows = data.rows;
  const ok = rows.filter(r => !r.error);
  const similarCount = ok.filter(r => r.verdict === "SIMILAR").length;
  $("#bar").textContent = "";
  let h = "";
  if (data.other_groups && data.other_groups.length) {
    h +=
      '<p class="hint" style="padding:var(--space-3) var(--space-4) 0">Группа «' +
      esc(data.group) + '» · остальные (не показаны): ' + data.other_groups.map(esc).join(", ") +
      "</p>";
  }
  h += kpiGrid([
    { label: "Файлов", value: rows.length },
    { label: "Медиана, µs", value: ok.length ? ok[0].median_us : "—" },
    { label: "Схожих", value: ok.length ? similarCount + " / " + ok.length : "—" },
  ]);
  h += '<div class="data-table-wrap" style="padding:0 var(--space-4) var(--space-4)">' +
    '<table class="data-table"><thead><tr><th scope="col">Файл</th><th scope="col">Runs</th>' +
    '<th scope="col">Min µs</th><th scope="col">Median µs</th><th scope="col">Mean µs</th>' +
    '<th scope="col">Max µs</th><th scope="col">StdDev µs</th><th scope="col">%</th>' +
    '<th scope="col">Вердикт</th>' +
    '<th scope="col" title="tracemalloc (stdin) / RSS (function), issue #66">Py-heap, МБ</th>' +
    "</tr></thead><tbody>";
  rows.forEach(row => {
    if (row.error) {
      h +=
        '<tr><td class="mono">' + esc(row.file) + '</td><td colspan="7">' + esc(row.error) +
        "</td><td>" + renderVerdict(row.verdict) + "</td></tr>";
      return;
    }
    h +=
      '<tr><td class="mono">' + esc(row.file) + '</td><td class="mono">' + row.runs + "</td>" +
      '<td class="mono">' + row.min_us + "</td>" +
      '<td class="mono">' + row.median_us + "</td>" +
      '<td class="mono">' + row.mean_us + "</td>" +
      '<td class="mono">' + row.max_us + "</td>" +
      '<td class="mono">' + row.stdev_us + "</td>" +
      '<td class="mono">' + row.relative + "%</td>" +
      "<td>" + renderVerdict(row.verdict) + "</td>" +
      '<td class="mono">' + (row.memory_mb ?? "—") + "</td></tr>";
  });
  $("#out").innerHTML = h + "</tbody></table></div>";
}

function errorCard(g) {
  return (
    '<div class="errcard"><span class="errcard-ex">💡 ' + esc(g.exception) + "</span> " +
    esc(g.hint) +
    ' <a href="' + esc(g.url) + '" target="_blank" rel="noopener">' +
    "открыть карточку в глоссарии →</a></div>"
  );
}

// -- История / Недавние пути (в левой панели «Конфигурация») -----------------

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
      // Недавние пути — всегда папки; в режиме «Один файл» сначала нужно
      // найти решения и выбрать конкретный файл, поэтому не грейдим сразу.
      if (state.mode !== "file") grade();
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
        : mode === "microbench"
          ? "микробенч · " + rows.length
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
      const mode = li.dataset.mode;
      setMode(mode); // сбрасывает file-picker state (resetFilePicker внутри setMode)
      if (mode === "file") {
        // В истории режима «Один файл» path — это конкретный проверенный
        // файл, а не папка; папку для #path восстанавливаем эвристически.
        state.selectedSolutionFile = li.dataset.path;
        $("#path").value = li.dataset.path.replace(/[\\/][^\\/]*$/, "");
        updateRunButtonState();
      } else {
        $("#path").value = li.dataset.path;
      }
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
  updateGlossarySidebarBadge();
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

function updateGlossarySidebarBadge() {
  const el = $("#sidebar-badge-glossary");
  const n = state.glossary.cards.length;
  el.textContent = String(n);
  el.hidden = n === 0;
  const widget = $("#glossary-widget-badge");
  if (widget) widget.textContent = String(n);
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
  document.querySelectorAll("[data-glview]").forEach(b => b.classList.toggle("active", b.dataset.glview === view));
  $("#glossary-cards").hidden = view !== "cards";
  $("#glossary-missing").hidden = view !== "missing";
  if (view === "missing" && !state.glossary.missing.length) loadMissing();
}

// -- Загрузчик задач: скачивание со Stepik (issue #186) -----------------------

function renderDownloaderResult(data) {
  const empty = $("#downloader-empty");
  const content = $("#downloader-content");
  empty.hidden = true;
  content.hidden = false;

  if (!data.ok) {
    content.innerHTML = '<p class="msg">' + esc(data.message) + "</p>";
    return;
  }

  let h = kpiGrid([
    { label: "Тестов", value: data.tests.count },
    { label: "Файлов", value: data.files.length },
  ]);
  if (data.message) h += '<p class="msg">' + esc(data.message) + "</p>";
  h += '<div class="field-label">Путь</div>' + codeBlock(data.path);
  h += '<div class="field-label">Файлы</div>' + codeBlock(data.files.join("\n"));
  if (data.files.length) {
    h +=
      '<button id="downloader-goto-check" class="btn btn-secondary" style="margin-top:var(--space-3)">' +
      "Перейти к проверке</button>";
  }
  content.innerHTML = h;

  const gotoBtn = $("#downloader-goto-check");
  if (gotoBtn) {
    gotoBtn.addEventListener("click", () => {
      $("#path").value = data.path;
      addRecentPath(data.path);
      setMode("tests");
      setSection("check");
    });
  }
}

async function downloadTask() {
  const url = $("#downloader-url").value.trim();
  if (!url) return;
  const root = $("#downloader-root").value.trim();
  const btn = $("#downloader-run");
  btn.disabled = true;
  btn.textContent = "Скачивание…";
  $("#downloader-empty").hidden = true;
  const content = $("#downloader-content");
  content.hidden = false;
  content.innerHTML = skeletonBlock();
  try {
    const r = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(root ? { url, root } : { url }),
    });
    const data = await r.json();
    renderDownloaderResult(data);
  } catch (e) {
    content.innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Скачать";
  }
}

// -- Wiring / init -------------------------------------------------------------

document
  .querySelectorAll("[data-section]")
  .forEach(b => b.addEventListener("click", e => { e.preventDefault(); setSection(b.dataset.section); }));
document
  .querySelectorAll("[data-mode]")
  .forEach(b => b.addEventListener("click", () => setMode(b.dataset.mode)));
document
  .querySelectorAll("[data-glview]")
  .forEach(b => b.addEventListener("click", () => setGlossaryView(b.dataset.glview)));
document
  .querySelectorAll("[data-conftab]")
  .forEach(b => b.addEventListener("click", () => setConfigTab(b.dataset.conftab)));
document
  .querySelectorAll("[data-restab]")
  .forEach(b => b.addEventListener("click", () => setResultTab(b.dataset.restab)));

$("#run").addEventListener("click", grade);
$("#cancel-run").addEventListener("click", cancelActiveRun);
$("#path").addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  if (state.mode === "file") findSolutions();
  else grade();
});
$("#find-solutions-btn").addEventListener("click", findSolutions);
$("#micro-profile").addEventListener("change", updateMicroCustomVisibility);
$("#downloader-run").addEventListener("click", downloadTask);
$("#downloader-url").addEventListener("keydown", e => {
  if (e.key === "Enter") downloadTask();
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
setConfigTab(state.configTab);
setResultTab(state.resultTab);
renderHistory();
renderRecentPaths();
loadCommands();

// Восстановить последний путь.
const saved = localStorage.getItem("grader_path");
if (saved) $("#path").value = saved;
