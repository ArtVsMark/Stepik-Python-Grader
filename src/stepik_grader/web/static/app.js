// app.js — клиентская логика веб-интерфейса грейдера (issue #58, эпик #80; issue #125;
// редизайн под маску эпика #123).
//
// type="module" (issue #265, для static import ниже) — раньше это был
// классический script; в index.html не было ни inline-скриптов, ни
// on*="..."-атрибутов, зовущих функции этого файла как globals, так что
// переход на module-scope (топ-level function здесь больше не попадают на
// window) ничего не ломает.
// issue #295: single self-contained vendored bundle (esbuild, no CDN/import
// map) instead of 5 bare-specifier imports resolved via an import map to
// separate esm.sh per-package bundles -- see static/vendor/VERSIONS.md.
import {
  EditorState,
  EditorView, lineNumbers, keymap, placeholder as cmPlaceholder,
  defaultKeymap, history, historyKeymap, indentWithTab,
  syntaxHighlighting, defaultHighlightStyle, indentOnInput,
  python,
} from "/static/vendor/codemirror-bundle@6.mjs";

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
  glossary: {
    query: "", section: "", kind: "", status: "", sort: "az",
    cards: [], missing: [], sections: [], total: 0,
    selectedId: null, view: "cards",
  },
  solutions: [], // режим 1 — файлы, найденные /api/solutions в указанной папке
  selectedSolutionFile: null, // режим 1 — выбранный для проверки файл (полный путь)
  activeRunId: null, // issue #262 — id текущего опрашиваемого async job (tests/bench/microbench)
  // issue #317 — id текущего прогона песочницы; issue #319 — trace: активный
  // JSON-трейс пошагового плеера ({steps, stdout, truncated, error, lines, idx})
  // либо null, когда плеер не открыт.
  sandbox: { activeRunId: null, trace: null },
  // issue #297 — baseline для индикатора несохранённых изменений и optimistic
  // locking: код и mtime на момент последней загрузки/сохранения файла.
  savedCode: "",
  savedMtime: null,
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
  switch_section: () => {
    // issue #317: циклическое переключение по всем разделам (раньше — только
    // check↔glossary, теряя downloader/sandbox — ср. находка аудита #331).
    const order = ["check", "downloader", "glossary", "sandbox"];
    const i = order.indexOf(state.section);
    setSection(order[(i + 1) % order.length]);
  },
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
  $("#view-sandbox").hidden = section !== "sandbox";
  if (section === "glossary" && !state.glossary.cards.length) loadGlossary();
  if (section === "sandbox") mountSandboxEditor(); // issue #317: ленивый монтаж
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

// ---------------------------------------------------------------------------
// CodeMirror 6 editor (issue #265) — mode 1's code editor, mounted into the
// #solution-editor div (was a plain <textarea> before this issue). Kept as
// a small get/set API (getEditorCode/setEditorCode) so the rest of app.js
// doesn't need to know CodeMirror's own state/dispatch model — every former
// `$("#solution-editor").value` read/write below became one of these calls.
// ---------------------------------------------------------------------------
let cmView = null;
let sandboxView = null; // issue #317: отдельный редактор песочницы

// Фабрика CodeMirror-редактора: общий theme/набор расширений для редактора
// режима 1 и песочницы (issue #317). onChange (опц.) — колбэк на docChanged.
function makeEditor(mount, onChange) {
  const theme = EditorView.theme({
    "&": { color: "var(--color-text)", backgroundColor: "transparent" },
    ".cm-content": {
      fontFamily: "var(--font-mono)",
      fontSize: "var(--text-xs)",
      lineHeight: "1.7",
      padding: "var(--space-2) var(--space-3)",
      caretColor: "var(--color-primary)",
    },
    "&.cm-editor": { minHeight: "240px" },
    ".cm-scroller": { overflow: "auto" },
    ".cm-gutters": {
      backgroundColor: "var(--color-surface-offset)",
      color: "var(--color-text-faint)",
      border: "none",
    },
    ".cm-activeLineGutter": { backgroundColor: "var(--color-surface-offset-2)" },
    "&.cm-focused": { outline: "none" }, // focus ring is :focus-within in app.css
    ".cm-placeholder": { color: "var(--color-text-faint)" },
  });
  return new EditorView({
    doc: "",
    parent: mount,
    extensions: [
      lineNumbers(),
      history(),
      indentOnInput(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
      python(),
      theme,
      cmPlaceholder(mount.dataset.placeholder || ""),
      EditorView.updateListener.of(update => {
        if (update.docChanged && onChange) onChange();
      }),
    ],
  });
}

function mountEditor() {
  const mount = document.getElementById("solution-editor");
  cmView = makeEditor(mount, () => {
    updateRunButtonState();
    updateDirtyIndicator(); // issue #297
  });
  // <label for="solution-editor"> can't natively focus a contenteditable div
  // the way it would a real <textarea> -- wire it manually (issue #265
  // acceptance criterion: editor must be keyboard-reachable).
  const label = document.querySelector('label[for="solution-editor"]');
  if (label) label.addEventListener("click", () => cmView.focus());
}

// issue #317: редактор песочницы монтируется лениво при первом входе в раздел.
function mountSandboxEditor() {
  if (sandboxView) return;
  const mount = document.getElementById("sandbox-editor");
  sandboxView = makeEditor(mount, null);
  const label = document.querySelector('label[for="sandbox-editor"]');
  if (label) label.addEventListener("click", () => sandboxView.focus());
}

function getSandboxCode() {
  return sandboxView ? sandboxView.state.doc.toString() : "";
}

function getEditorCode() {
  return cmView ? cmView.state.doc.toString() : "";
}

function setEditorCode(text) {
  if (!cmView) return;
  cmView.dispatch({ changes: { from: 0, to: cmView.state.doc.length, insert: text || "" } });
}

function updateRunButtonState() {
  if (state.mode !== "file") {
    $("#run").disabled = false;
    return;
  }
  const hasFolder = $("#path").value.trim().length > 0;
  const hasCode = getEditorCode().trim().length > 0;
  $("#run").disabled = !(hasFolder && hasCode);
}

function resetFilePicker() {
  state.solutions = [];
  state.selectedSolutionFile = null;
  const list = $("#solutions-list");
  if (list) list.innerHTML = "";
  setEditorCode("");
  markEditorSaved("", null); // issue #297
  updateRunButtonState();
}

async function findSolutions() {
  state.selectedSolutionFile = null;
  setEditorCode("");
  markEditorSaved("", null); // issue #297
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
  try {
    const r = await fetch("/api/source?" + new URLSearchParams({ path: fullPath }));
    const data = await r.json();
    const code = data.kind === "error" ? "" : data.source;
    setEditorCode(code);
    // issue #297: baseline для dirty-индикатора и optimistic locking (mtime).
    markEditorSaved(code, data.kind === "error" ? null : data.mtime);
  } catch (e) {
    setEditorCode("");
    markEditorSaved("", null);
  }
  updateRunButtonState();
}

// issue #297: зафиксировать текущее содержимое как «сохранённое» — сбрасывает
// индикатор несохранённых изменений и запоминает mtime для optimistic locking.
function markEditorSaved(code, mtime) {
  state.savedCode = code;
  state.savedMtime = mtime;
  updateDirtyIndicator();
}

// issue #297: показать/скрыть «● не сохранено» и включить кнопку «Сохранить»
// по факту расхождения редактора с последним сохранённым содержимым.
function updateDirtyIndicator() {
  const dirty = state.mode === "file" && getEditorCode() !== state.savedCode;
  const badge = $("#editor-dirty");
  if (badge) badge.hidden = !dirty;
  const saveBtn = $("#save-solution-btn");
  if (saveBtn) saveBtn.disabled = !dirty || !$("#path").value.trim();
}

async function grade() {
  const isFileMode = state.mode === "file";
  const folder = $("#path").value.trim();
  // issue #297: режим 1 больше НЕ сохраняет перед грейдом. Код исполняется из
  // временного файла (POST /api/v1/runs с code в теле, mode="tests") — целевой
  // файл не перезаписывается, гонки save→grade между двумя окнами больше нет.
  // path для резолюции tests/: выбранный файл, иначе — сама папка (runs.py
  // кладёт временный файл рядом и находит tests/ там же).
  let code = null;
  let path;
  if (isFileMode) {
    code = getEditorCode();
    if (!folder || !code.trim()) return;
    path = state.selectedSolutionFile || folder;
  } else {
    path = folder;
  }
  if (!path) return;
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
  // Режимы 1/3/4 идут через async job (/api/v1/runs): режим 1 — ради code в
  // теле (issue #297), режимы 3/4 — ради прогресса/отмены долгого бенча
  // (issue #262). Режим 2 (папка, tests) остаётся на синхронном /api/grade —
  // там нет редактируемого кода, нет гонки.
  if (isFileMode) {
    await gradeAsync(path, "tests", code);
    return;
  }
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
    announceResult(summaryFromResult(data)); // issue #298
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
    announceResult("Ошибка запроса"); // issue #298
  } finally {
    _finishGradeUI();
  }
}

// issue #297: явное сохранение кода режима 1 на диск (кнопка «Сохранить») —
// отдельно от грейда. Optimistic locking через expected_mtime: если файл
// изменился на диске с момента загрузки, бэкенд отвечает conflict=True и НЕ
// перезаписывает; пользователю показывается предупреждение, повторный клик
// «Сохранить» перезаписывает (mtime уже обновлён на конфликтный — совпадёт).
async function saveSolution() {
  const folder = $("#path").value.trim();
  const code = getEditorCode();
  if (!folder) return;
  const saveBtn = $("#save-solution-btn");
  saveBtn.disabled = true;
  try {
    const body = { folder, path: state.selectedSolutionFile || null, code };
    if (state.selectedSolutionFile && state.savedMtime != null) {
      body.expected_mtime = state.savedMtime;
    }
    const resp = await fetch("/api/save-solution", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const saved = await resp.json();
    if (!saved.ok) {
      $("#out").innerHTML = '<p class="msg">' + esc(saved.message) + "</p>";
      // Конфликт: обновляем baseline mtime на актуальный с диска НЕ можем (не
      // знаем его), но повторный клик пойдёт без ложного конфликта, если
      // пользователь осознанно перезапишет — упрощаем: снимаем optimistic-guard
      // на следующий клик, обнулив ожидаемый mtime.
      if (saved.conflict) state.savedMtime = null;
      updateDirtyIndicator();
      return;
    }
    state.selectedSolutionFile = saved.path;
    markEditorSaved(code, saved.mtime);
    await refreshSolutionsList();
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Не удалось сохранить код: ' + esc(String(e)) + "</p>";
  } finally {
    updateDirtyIndicator();
  }
}

function _finishGradeUI() {
  updateRunButtonState();
  $("#run").textContent = "▶ Запустить";
  renderDetailPanel();
  renderResultSummaryBadges();
  if (state.resultTab === "log") renderLogTab();
  if (state.resultTab === "reference") renderReferenceTab();
  // issue #298 (a11y): после завершения прогона фокус уходит на панель
  // результатов (tabindex="-1"), чтобы клавиатурный/скринридер-пользователь
  // оказался у сводки, а не остался на кнопке «Запустить».
  const panel = $("#restab-table");
  if (panel && state.resultTab === "table") panel.focus();
}

// issue #298 (a11y): одна строка-сводка исхода в polite live region
// (#result-announce) — озвучивается по ЗАВЕРШЕНИИ прогона, не на каждый
// прогресс-тик. Сброс перед записью: часть скринридеров не переобъявляет
// идентичный текст, очистка + следующий кадр гарантирует событие.
function announceResult(text) {
  const region = $("#result-announce");
  if (!region) return;
  region.textContent = "";
  requestAnimationFrame(() => {
    region.textContent = text || "";
  });
}

// Краткая человекочитаемая сводка результата грейда для screen-reader-объявления.
function summaryFromResult(data) {
  if (!data || data.kind === "error") {
    return data && data.message ? data.message : "Ошибка проверки";
  }
  if (!data.rows || !data.rows.length) return "Результатов нет";
  if (data.mode === "bench" || data.mode === "microbench") {
    const label = data.mode === "bench" ? "Бенчмарк" : "Микробенчмарк";
    return label + " завершён: " + data.rows.length + " " + _plur(data.rows.length, "решение", "решения", "решений");
  }
  const ok = data.rows.filter(r => r.status === "OK").length;
  if (data.rows.length === 1) {
    const r = data.rows[0];
    return "Проверка завершена: " + r.file + " — " + r.status + ", " + r.passed + " из " + r.total;
  }
  return (
    "Проверка завершена: " + data.rows.length + " " + _plur(data.rows.length, "решение", "решения", "решений") +
    ", " + ok + " OK, " + (data.rows.length - ok) + " FAIL"
  );
}

// Русская плюрализация (1 решение / 2 решения / 5 решений).
function _plur(n, one, few, many) {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// issue #262 — async job model: POST /api/v1/runs, затем polling
// GET /api/v1/runs/{id} каждые 600мс до status="done"/"error"/"cancelled",
// вместо единственного блокирующего /api/grade. Режим 1 (issue #297) идёт
// сюда с mode="tests" и code в теле — грейд из временного файла без записи на
// диск. Режимы 3/4 (bench/microbench) — ради прогресса/отмены долгого бенча.
// Режим 2 (папка, tests) остаётся на синхронном /api/grade.
async function gradeAsync(path, backendMode, code = null) {
  const params = {};
  if (backendMode === "bench") params.repeats = Number($("#repeats").value);
  if (backendMode === "microbench") params.number = getMicroNumber();

  const reqBody = { path, mode: backendMode, params };
  if (code != null) reqBody.code = code; // issue #297 — код режима 1 в теле
  let created;
  try {
    const createResp = await fetch("/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reqBody),
    });
    created = await createResp.json();
    if (createResp.status !== 202) {
      $("#out").innerHTML =
        '<p class="msg">' + esc(created.message || "Не удалось запустить задачу.") + "</p>";
      announceResult(created.message || "Не удалось запустить задачу"); // issue #298
      _finishGradeUI();
      return;
    }
  } catch (e) {
    $("#out").innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
    announceResult("Ошибка запроса"); // issue #298
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
        announceResult("Ошибка запроса"); // issue #298
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
        announceResult(summaryFromResult(data.result)); // issue #298
      } else if (data.status === "cancelled") {
        // issue #296: отдельный нейтральный статус — не провал грейдера,
        // не показываем красным (.msg), как настоящую ошибку.
        $("#out").innerHTML =
          '<p class="msg-neutral">' + esc(data.message || "Задача отменена.") + "</p>";
        announceResult(data.message || "Прогон отменён"); // issue #298
      } else {
        $("#out").innerHTML =
          '<p class="msg">' + esc(data.message || "Задача завершилась с ошибкой.") + "</p>";
        announceResult(data.message || "Задача завершилась с ошибкой"); // issue #298
      }
      resolve();
    };
    poll();
  });

  _finishGradeUI();
}

function updateProgressBar(done, total) {
  // issue #298 (a11y): role="progressbar" + aria-value* так, чтобы assistive
  // tech озвучивала прогресс долгого прогона. Неопределённый прогресс (total
  // ещё не посчитан) — progressbar без aria-valuenow + aria-valuetext.
  if (!total) {
    $("#bar").innerHTML =
      '<div class="progress-track" role="progressbar" aria-label="Прогресс проверки" ' +
      'aria-valuetext="Выполняется">' +
      '<div class="progress-fill progress-indeterminate"></div></div>';
    return;
  }
  const pct = Math.min(100, Math.round((done / total) * 100));
  $("#bar").innerHTML =
    '<div class="progress-track" role="progressbar" aria-label="Прогресс проверки" ' +
    'aria-valuemin="0" aria-valuemax="' + total + '" aria-valuenow="' + done + '">' +
    '<div class="progress-fill" style="width:' + pct + '%"></div></div>' +
    '<div class="progress-label">' + done + " / " + total + "</div>";
}

function cancelActiveRun() {
  if (!state.activeRunId) return;
  const cancelBtn = $("#cancel-run");
  cancelBtn.disabled = true;
  fetch("/api/v1/runs/" + state.activeRunId + "/cancel", { method: "POST" }).catch(() => {});
}

// -- Песочница: запуск произвольного кода со stdin (issue #317) ---------------
// Тот же async-путь, что грейдинг (POST /api/v1/runs + polling), но mode=
// "playground": без тестов, результат — {status, stdout, stderr, exit_code}.

const SANDBOX_STATUS = {
  OK: ["Успешно", "badge-success"],
  RE: ["Ошибка выполнения", "badge-error"],
  TLE: ["Превышено время", "badge-warning"],
  CANCELLED: ["Отменено", "badge-neutral"],
};

// issue #319: POST /api/v1/runs + polling, общий для песочницы (playground) и
// трейса. Возвращает терминальный статус job'ы ({status, result?, message?}) —
// вызывающий сам рендерит результат/ошибку. Отмена/смена прогона отслеживается
// через state.sandbox.activeRunId (как раньше в runPlayground).
async function submitSandboxRun(body) {
  let created;
  try {
    const resp = await fetch("/api/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    created = await resp.json();
    if (resp.status !== 202) return { status: "error", message: created.message || "Не удалось запустить." };
  } catch (e) {
    return { status: "error", message: "Ошибка запроса: " + String(e) };
  }
  const runId = created.run_id;
  state.sandbox.activeRunId = runId;
  return await new Promise(resolve => {
    const poll = async () => {
      if (state.sandbox.activeRunId !== runId) return resolve({ status: "cancelled" });
      let data;
      try {
        const r = await fetch("/api/v1/runs/" + runId);
        if (state.sandbox.activeRunId !== runId) return resolve({ status: "cancelled" });
        data = await r.json();
      } catch (e) {
        return resolve({ status: "error", message: "Ошибка запроса: " + String(e) });
      }
      if (data.status === "queued" || data.status === "running") {
        setTimeout(poll, 300);
        return;
      }
      resolve(data);
    };
    poll();
  });
}

function _startSandboxUI(busyMsg) {
  state.sandbox.trace = null; // закрыть прежний плеер
  $("#sandbox-run").disabled = true;
  $("#sandbox-step").disabled = true;
  const cancelBtn = $("#sandbox-cancel");
  cancelBtn.hidden = false;
  cancelBtn.disabled = false;
  $("#sandbox-empty").hidden = true;
  const out = $("#sandbox-output");
  out.hidden = false;
  out.innerHTML = '<p class="hint">' + esc(busyMsg) + "</p>";
  setSandboxStatus(null);
}

async function runPlayground() {
  const code = getSandboxCode();
  if (!code.trim() || state.sandbox.activeRunId) return;
  _startSandboxUI("Выполняется…");
  const data = await submitSandboxRun({ mode: "playground", code, stdin: $("#sandbox-stdin").value });
  if (data.status === "done") renderSandboxResult(data.result);
  else if (data.status === "cancelled") renderSandboxError(data.message || "Прогон отменён.", true);
  else renderSandboxError(data.message || "Ошибка выполнения.");
  _finishSandboxUI();
}

// issue #319: пошаговый трейс — тот же async-путь, но mode="trace"; результат —
// JSON-трейс (docs/trace-format.md), открываемый плеером.
async function runTrace() {
  const code = getSandboxCode();
  if (!code.trim() || state.sandbox.activeRunId) return;
  _startSandboxUI("Трассировка…");
  const data = await submitSandboxRun({ mode: "trace", code, stdin: $("#sandbox-stdin").value });
  if (data.status === "done") showTracePlayer(data.result, code);
  else if (data.status === "cancelled") renderSandboxError(data.message || "Прогон отменён.", true);
  else renderSandboxError(data.message || "Ошибка выполнения.");
  _finishSandboxUI();
}

function _finishSandboxUI() {
  state.sandbox.activeRunId = null;
  $("#sandbox-run").disabled = false;
  $("#sandbox-step").disabled = false;
  $("#sandbox-cancel").hidden = true;
}

function cancelSandboxRun() {
  if (!state.sandbox.activeRunId) return;
  $("#sandbox-cancel").disabled = true;
  fetch("/api/v1/runs/" + state.sandbox.activeRunId + "/cancel", { method: "POST" }).catch(() => {});
}

function setSandboxStatus(status) {
  const el = $("#sandbox-status");
  const entry = status && SANDBOX_STATUS[status];
  if (!entry) {
    el.hidden = true;
    return;
  }
  el.className = "badge " + entry[1];
  el.textContent = entry[0];
  el.hidden = false;
}

function renderSandboxResult(r) {
  setSandboxStatus(r.status);
  const parts = [
    '<div class="form-label">Вывод (stdout)</div>',
    '<pre class="code-block">' +
      (r.stdout ? esc(r.stdout) : '<span class="hint">(пусто)</span>') +
      "</pre>",
  ];
  if (r.stderr) {
    parts.push('<div class="form-label">Ошибки (stderr)</div>');
    parts.push('<pre class="code-block sandbox-stderr">' + esc(r.stderr) + "</pre>");
  }
  const meta = [];
  if (r.exit_code != null) meta.push("код выхода: " + r.exit_code);
  if (r.duration_ms != null) meta.push(r.duration_ms + " мс");
  if (r.truncated) meta.push("вывод обрезан");
  if (meta.length) parts.push('<div class="hint">' + esc(meta.join(" · ")) + "</div>");
  $("#sandbox-output").innerHTML = parts.join("");
}

function renderSandboxError(msg, neutral = false) {
  setSandboxStatus(neutral ? "CANCELLED" : "RE");
  $("#sandbox-output").innerHTML =
    '<p class="' + (neutral ? "msg-neutral" : "msg") + '">' + esc(msg) + "</p>";
}

// ───────────────────────── пошаговый плеер (issue #319) ─────────────────────
// Листает JSON-трейс core/tracer.py (docs/trace-format.md) без повторного
// исполнения — как Python Tutor. Код показывается замороженным снимком
// (подсветка активной строки) — не в живом CodeMirror: вендоренный бандл не
// экспортирует Decoration, а снимок и надёжнее (лайв-редактор не виртуализирует
// строки под нами). state.sandbox.trace хранит шаги + позицию плеера.

const _FMT_MAX_DEPTH = 2; // глубже — компактный repr, чтобы не рисовать вложенность целиком
const _FMT_MAX_ELEMS = 8; // элементов контейнера в inline-значении (n показываем всегда)

function showTracePlayer(trace, code) {
  const steps = (trace && trace.steps) || [];
  if (!steps.length) {
    // трейс не собрался (сбой/таймаут subprocess) либо программа без шагов
    const msg = trace && trace.error ? trace.error.message : "Трейс пуст — нет шагов.";
    return renderSandboxError(msg || "Не удалось оттрассировать.");
  }
  state.sandbox.trace = {
    steps,
    stdout: (trace && trace.stdout) || "",
    truncated: !!(trace && trace.truncated),
    error: (trace && trace.error) || null,
    lines: code.split("\n"),
    idx: 0,
  };
  setSandboxStatus(trace.error ? "RE" : "OK");
  $("#sandbox-output").innerHTML = tracePlayerShell(state.sandbox.trace);
  wireTraceControls();
  renderTraceStep(0);
}

function tracePlayerShell(tr) {
  const m = tr.steps.length;
  const codeHtml = tr.lines
    .map(
      (ln, i) =>
        '<div class="trace-line" data-line="' +
        (i + 1) +
        '"><span class="trace-lineno">' +
        (i + 1) +
        '</span><span class="trace-linetext">' +
        (esc(ln) || " ") +
        "</span></div>"
    )
    .join("");
  const trunc = tr.truncated
    ? '<div class="trace-truncated hint">⚠ показаны первые ' + m + " шагов (лимит/таймаут)</div>"
    : "";
  return (
    '<div class="trace-player">' +
    '<div class="trace-controls" role="group" aria-label="Управление трейсом">' +
    '<button class="btn-icon" data-trace="first" title="В начало" aria-label="В начало">⏮</button>' +
    '<button class="btn-icon" data-trace="prev" title="Назад (←)" aria-label="Предыдущий шаг">◀</button>' +
    '<input type="range" id="trace-slider" class="trace-slider" min="0" max="' +
    (m - 1) +
    '" value="0" aria-label="Позиция в трейсе">' +
    '<button class="btn-icon" data-trace="next" title="Вперёд (→)" aria-label="Следующий шаг">▶</button>' +
    '<button class="btn-icon" data-trace="last" title="В конец" aria-label="В конец">⏭</button>' +
    '<span id="trace-step-label" class="trace-step-label" aria-live="polite" aria-atomic="true"></span>' +
    "</div>" +
    trunc +
    '<div class="trace-split">' +
    '<pre class="trace-code" id="trace-code">' +
    codeHtml +
    "</pre>" +
    '<div class="trace-frames" id="trace-frames"></div>' +
    "</div>" +
    '<div class="form-label">Вывод (stdout)</div>' +
    '<pre class="code-block trace-stdout" id="trace-stdout"></pre>' +
    '<div class="msg trace-error" id="trace-error" hidden></div>' +
    "</div>"
  );
}

function wireTraceControls() {
  const out = $("#sandbox-output");
  out.querySelectorAll("[data-trace]").forEach(btn =>
    btn.addEventListener("click", () => {
      const tr = state.sandbox.trace;
      if (!tr) return;
      const last = tr.steps.length - 1;
      const target = { first: 0, prev: tr.idx - 1, next: tr.idx + 1, last }[btn.dataset.trace];
      renderTraceStep(target);
    })
  );
  $("#trace-slider").addEventListener("input", e => renderTraceStep(parseInt(e.target.value, 10)));
}

function renderTraceStep(idx) {
  const tr = state.sandbox.trace;
  if (!tr) return;
  idx = Math.max(0, Math.min(tr.steps.length - 1, idx));
  tr.idx = idx;
  const step = tr.steps[idx];
  const prev = idx > 0 ? tr.steps[idx - 1] : null;

  // 1. подсветка активной строки в снимке кода (красная на шаге-исключении)
  const codeEl = $("#trace-code");
  codeEl
    .querySelectorAll(".trace-line.is-active, .trace-line.is-error")
    .forEach(el => el.classList.remove("is-active", "is-error"));
  const lineEl = codeEl.querySelector('.trace-line[data-line="' + step.line + '"]');
  if (lineEl) {
    // строка красная на шаге-исключении и на финальном шаге упавшей программы
    // (виновная строка остаётся подсвеченной, а не гаснет на return-раскрутке)
    const isErr = step.event === "exception" || (tr.error && idx === tr.steps.length - 1);
    lineEl.classList.add(isErr ? "is-error" : "is-active");
    lineEl.scrollIntoView({ block: "nearest" });
  }

  // 2. кадры стека: текущий сверху, «Глобальные» снизу; изменившиеся — подсветка
  $("#trace-frames").innerHTML = renderTraceFrames(step, prev);

  // 3. вывод программы, наросший к этому шагу (срез по stdout_len)
  $("#trace-stdout").textContent = tr.stdout.slice(0, step.stdout_len);

  // 4. блок исключения — на шаге exception и на последнем шаге, если error есть
  const errEl = $("#trace-error");
  const showErr = tr.error && (step.event === "exception" || idx === tr.steps.length - 1);
  if (showErr) {
    const gid = tr.error.type.toLowerCase();
    errEl.hidden = false;
    errEl.innerHTML =
      "⛔ <strong>" +
      esc(tr.error.type) +
      "</strong>: " +
      esc(tr.error.message) +
      ' <a class="trace-error-link" href="#/glossary/' +
      encodeURIComponent(gid) +
      '">открыть в глоссарии →</a>';
  } else {
    errEl.hidden = true;
  }

  // 5. состояние контролов (слайдер, счётчик, дизейбл крайних кнопок)
  const last = tr.steps.length - 1;
  $("#trace-slider").value = String(idx);
  $("#trace-step-label").textContent = "шаг " + (idx + 1) + " из " + tr.steps.length;
  const out = $("#sandbox-output");
  out.querySelector('[data-trace="first"]').disabled = idx === 0;
  out.querySelector('[data-trace="prev"]').disabled = idx === 0;
  out.querySelector('[data-trace="next"]').disabled = idx === last;
  out.querySelector('[data-trace="last"]').disabled = idx === last;
}

function renderTraceFrames(step, prev) {
  const heap = step.heap || {};
  const frames = step.stack || [];
  const out = [];
  for (let k = frames.length - 1; k >= 0; k--) {
    const fr = frames[k];
    const prevFr = prev && prev.stack && prev.stack[k]; // тот же уровень стека в пред. шаге
    const title = k === 0 ? "Глобальные" : esc(fr.func) + "()";
    const names = Object.keys(fr.locals || {});
    let body;
    if (!names.length) {
      body = '<div class="hint">нет переменных</div>';
    } else {
      const rows = names.map(name => {
        const ref = fr.locals[name];
        const changed =
          !prevFr ||
          !(name in (prevFr.locals || {})) ||
          JSON.stringify(prevFr.locals[name]) !== JSON.stringify(ref);
        return (
          '<tr class="' +
          (changed ? "is-changed" : "") +
          '"><td class="trace-var-name">' +
          esc(name) +
          '</td><td class="trace-var-val">' +
          fmtRef(ref, heap, 0) +
          "</td></tr>"
        );
      });
      body = '<table class="trace-vars">' + rows.join("") + "</table>";
    }
    out.push(
      '<div class="trace-frame"><div class="trace-frame-title">' + title + "</div>" + body + "</div>"
    );
  }
  return out.join("");
}

// Отрендерить <ref> (docs/trace-format.md § Ссылки) в короткую HTML-строку.
function fmtRef(ref, heap, depth) {
  if (ref === null) return "None";
  if (ref === true) return "True";
  if (ref === false) return "False";
  if (typeof ref === "number") return String(ref);
  if (typeof ref === "string") return esc(JSON.stringify(ref)); // строка в кавычках, экранирована
  if (ref && typeof ref === "object") {
    if ("big" in ref) return esc(ref.big); // большой int — repr строкой
    if ("ref" in ref) {
      const obj = heap[ref.ref];
      return obj ? fmtObj(obj, heap, depth) : "?";
    }
  }
  return esc(String(ref));
}

function fmtObj(obj, heap, depth) {
  const kind = obj.kind;
  if (kind === "func") return "ƒ " + esc(obj.name);
  if (kind === "module") return "module " + esc(obj.name);
  if (kind === "opaque") return esc(obj.repr);
  if (depth > _FMT_MAX_DEPTH) return obj.repr ? esc(obj.repr) : esc(obj.type) + "(…)";
  if (kind === "seq") {
    const pair =
      obj.type === "tuple"
        ? ["(", ")"]
        : obj.type === "set" || obj.type === "frozenset"
          ? ["{", "}"]
          : ["[", "]"];
    const shown = (obj.elems || []).slice(0, _FMT_MAX_ELEMS).map(e => fmtRef(e, heap, depth + 1));
    if (obj.n > shown.length) shown.push("…" + obj.n);
    return esc(pair[0]) + shown.join(", ") + esc(pair[1]);
  }
  if (kind === "map") {
    const shown = (obj.entries || [])
      .slice(0, _FMT_MAX_ELEMS)
      .map(([k, v]) => fmtRef(k, heap, depth + 1) + ": " + fmtRef(v, heap, depth + 1));
    if (obj.n > shown.length) shown.push("…" + obj.n);
    return "{" + shown.join(", ") + "}";
  }
  if (kind === "object") {
    const attrs = Object.keys(obj.attrs || {})
      .slice(0, _FMT_MAX_ELEMS)
      .map(k => esc(k) + "=" + fmtRef(obj.attrs[k], heap, depth + 1));
    return esc(obj.type) + "(" + attrs.join(", ") + ")";
  }
  return esc(obj.type || "?");
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

// issue #329: быстрые фильтры-чипы по разделам. Разнотипное НЕ объединяется —
// «Списки», «Кортежи», «Словари», «Множества» — отдельные чипы (в отличие от
// внешнего Glossary-Python, где они слиты в «Списки · кортежи»/«Словари · множества»).
const GLOSSARY_CHIPS = [
  { label: "Строки", section: "Строки (str)" },
  { label: "Списки", section: "Списки (list)" },
  { label: "Кортежи", section: "Кортежи (tuple)" },
  { label: "Словари", section: "Словари (dict)" },
  { label: "Множества", section: "Множества (set)" },
  { label: "Встроенные", section: "Встроенные функции" },
  { label: "Исключения", section: "Исключения" },
];

async function loadGlossary() {
  const g = state.glossary;
  const params = new URLSearchParams();
  if (g.query) params.set("q", g.query);
  if (g.section) params.set("section", g.section);
  if (g.kind) params.set("kind", g.kind);
  if (g.status) params.set("status", g.status);
  if (g.sort) params.set("sort", g.sort);
  try {
    const r = await fetch("/api/glossary?" + params);
    g.cards = await r.json();
  } catch (e) {
    g.cards = [];
  }
  // Список разделов и общий счётчик — из первой невыбранной загрузки (все карточки).
  if (!g.sections.length && !g.query && !g.section && !g.kind) {
    g.total = g.cards.length;
    g.sections = [...new Set(g.cards.map(c => c.section).filter(Boolean))].sort((a, b) =>
      a.localeCompare(b, "ru")
    );
    renderGlossaryChips();
    renderGlossaryFilters();
  }
  renderGlossaryList();
  renderGlossaryCount();
  updateGlossarySidebarBadge();
}

function renderGlossaryChips() {
  const el = $("#glossary-chips");
  if (!el) return;
  const avail = new Set(state.glossary.sections);
  el.innerHTML = GLOSSARY_CHIPS.filter(ch => avail.has(ch.section))
    .map(ch => {
      const active = state.glossary.section === ch.section ? " active" : "";
      return (
        '<button type="button" class="chip' + active + '" data-section="' +
        esc(ch.section) + '">' + esc(ch.label) + "</button>"
      );
    })
    .join("");
  el.querySelectorAll(".chip").forEach(b =>
    b.addEventListener("click", () => toggleGlossarySection(b.dataset.section))
  );
}

function renderGlossaryFilters() {
  const sel = $("#glossary-section");
  if (!sel) return;
  sel.innerHTML = ['<option value="">Все разделы</option>']
    .concat(state.glossary.sections.map(s => '<option value="' + esc(s) + '">' + esc(s) + "</option>"))
    .join("");
  sel.value = state.glossary.section;
}

function renderGlossaryCount() {
  const el = $("#glossary-count");
  if (!el) return;
  const g = state.glossary;
  el.textContent = g.total ? "Показано " + g.cards.length + " из " + g.total : "";
}

function toggleGlossarySection(section) {
  const g = state.glossary;
  g.section = g.section === section ? "" : section;
  const sel = $("#glossary-section");
  if (sel) sel.value = g.section;
  renderGlossaryChips();
  loadGlossary();
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
  const n = state.glossary.total || state.glossary.cards.length;
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
      const draft = c.status === "draft" ? " gloss-draft" : ""; // issue #328: пометка черновика
      return '<li data-id="' + esc(c.id) + '" class="' + sel + draft + '">' + esc(c.title) + "</li>";
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

function selectGlossaryCard(id, opts = {}) {
  state.glossary.selectedId = id;
  renderGlossaryList();
  // issue #329: отражаем выбор в URL-хэше (#/glossary/<id>) — карточка шарится
  // и открывается по прямой ссылке. fromHash=true — не переписывать хэш обратно.
  if (!opts.fromHash) {
    const target = "#/glossary/" + encodeURIComponent(id);
    if (location.hash !== target) location.hash = target;
  }
  const card = state.glossary.cards.find(c => c.id === id);
  if (card) {
    renderGlossaryDetail(card);
    return;
  }
  // Deep-link (open_glossary/хэш) может указывать на карточку вне текущей выборки.
  fetch("/api/glossary/" + encodeURIComponent(id))
    .then(r => (r.ok ? r.json() : null))
    .then(c => {
      if (c) renderGlossaryDetail(c);
    })
    .catch(() => {});
}

// issue #329: маршрутизация по URL-хэшу #/glossary/<id> — прямые ссылки на карточку.
function parseGlossaryHash() {
  const m = location.hash.match(/^#\/glossary\/(.+)$/);
  return m ? decodeURIComponent(m[1]) : null;
}

async function routeFromHash() {
  const id = parseGlossaryHash();
  if (!id) return;
  if (state.section !== "glossary") setSection("glossary");
  if (!state.glossary.cards.length) await loadGlossary();
  if (state.glossary.selectedId !== id) selectGlossaryCard(id, { fromHash: true });
}

function renderGlossaryDetail(card) {
  $("#glossary-empty").hidden = true;
  const el = $("#glossary-detail-content");
  el.hidden = false;
  const meta = [card.kind, card.section, card.subcat].filter(Boolean).map(esc).join(" · ");
  const draftBadge =
    card.status === "draft"
      ? ' <span class="badge badge-warning">черновик</span>' // issue #328
      : "";
  const verBadge = card.version
    ? ' <span class="badge badge-neutral">Python ' + esc(card.version) + "</span>"
    : "";
  const syntax = card.syntax
    ? '<div class="form-label">Синтаксис</div><pre class="code-block">' + esc(card.syntax) + "</pre>"
    : "";
  const examples = card.examples && card.examples.length
    ? '<div class="form-label">Примеры</div>' +
      card.examples.map(ex => '<pre class="code-block">' + esc(ex) + "</pre>").join("")
    : "";
  const links = [
    card.docs_url
      ? '<a href="' + esc(card.docs_url) + '" target="_blank" rel="noopener">Документация Python →</a>'
      : "",
    card.url
      ? '<a href="' + esc(card.url) + '" target="_blank" rel="noopener">Открыть во внешнем глоссарии →</a>'
      : "",
  ].filter(Boolean).map(a => "<p>" + a + "</p>").join("");
  el.innerHTML =
    "<h2>" + esc(card.title) + draftBadge + verBadge + "</h2>" +
    '<div class="hint">' + meta + "</div>" +
    (card.summary ? "<p>" + esc(card.summary) + "</p>" : "") +
    (card.body ? "<div>" + esc(card.body) + "</div>" : "") +
    syntax +
    examples +
    links;
}

function setGlossaryView(view) {
  state.glossary.view = view;
  document.querySelectorAll("[data-glview]").forEach(b => b.classList.toggle("active", b.dataset.glview === view));
  $("#glossary-cards").hidden = view !== "cards";
  $("#glossary-missing").hidden = view !== "missing";
  const filters = $("#glossary-filters");
  if (filters) filters.hidden = view !== "cards"; // фильтры только для карточек (issue #329)
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
$("#sandbox-run").addEventListener("click", runPlayground); // issue #317
$("#sandbox-step").addEventListener("click", runTrace); // issue #319
$("#sandbox-cancel").addEventListener("click", cancelSandboxRun);

// issue #319: навигация плеера стрелками ← →. Работает только в песочнице при
// открытом трейсе и когда фокус не в поле ввода/редакторе/слайдере (у слайдера
// стрелки нативно двигают позицию → его input-слушатель сам вызовет шаг).
document.addEventListener("keydown", e => {
  if (state.section !== "sandbox" || !state.sandbox.trace) return;
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  const t = e.target;
  if (t && ((t.matches && t.matches("input, textarea")) || (t.closest && t.closest(".cm-editor"))))
    return;
  e.preventDefault();
  renderTraceStep(state.sandbox.trace.idx + (e.key === "ArrowRight" ? 1 : -1));
});
$("#path").addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  if (state.mode === "file") findSolutions();
  else grade();
});
$("#find-solutions-btn").addEventListener("click", findSolutions);
$("#save-solution-btn").addEventListener("click", saveSolution); // issue #297
$("#path").addEventListener("input", updateDirtyIndicator); // issue #297 — папка влияет на доступность «Сохранить»
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
  state.glossary.query = e.target.value;
  glossarySearchTimer = setTimeout(() => loadGlossary(), 200);
});
$("#glossary-section").addEventListener("change", e => {
  state.glossary.section = e.target.value;
  renderGlossaryChips();
  loadGlossary();
});
$("#glossary-kind").addEventListener("change", e => {
  state.glossary.kind = e.target.value;
  loadGlossary();
});
$("#glossary-status").addEventListener("change", e => {
  state.glossary.status = e.target.value;
  loadGlossary();
});
$("#glossary-sort").addEventListener("change", e => {
  state.glossary.sort = e.target.value;
  loadGlossary();
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

window.addEventListener("hashchange", routeFromHash); // issue #329: deep-link на карточку

applyTheme();
mountEditor();
setSection(state.section);
setMode(state.mode);
setConfigTab(state.configTab);
setResultTab(state.resultTab);
renderHistory();
renderRecentPaths();
loadCommands();
routeFromHash(); // открыть карточку из #/glossary/<id>, если ссылка прямая (issue #329)

// Восстановить последний путь.
const saved = localStorage.getItem("grader_path");
if (saved) $("#path").value = saved;
