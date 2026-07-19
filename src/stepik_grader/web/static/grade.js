// grade.js — проверка (режимы 1–4), рендер результата, палитра команд (#426).
import { openGlossaryForSelectedCase } from "./content.js";
import { $, SECTIONS, codeBlock, cycleTheme, esc, fetchCodeTerms, getSelectedCase, kpiGrid, makeEditor, renderTermsInto, setSection, skeletonBlock, state } from "./core.js";

const VERDICT_BADGE = {
  AC: "badge badge-success", OK: "badge badge-success",
  WA: "badge badge-error", FAIL: "badge badge-error",
  RE: "badge badge-error", ERR: "badge badge-error",
  TLE: "badge badge-warning",
  // issue #331: вердикты из docs/result-contract.md, которых раньше не знал
  // рендер (падали в нейтральный fallback). CANCELLED — не провал (отмена
  // пользователем, #262/#296), нейтральный; SANDBOX_VIOLATION — провал
  // изоляции (#266, только --sandbox), красный. В web SANDBOX_VIOLATION пока
  // не пробрасывается (сервер не запускает --sandbox), запись — на будущее.
  CANCELLED: "badge badge-neutral",
  SANDBOX_VIOLATION: "badge badge-error",
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
    // issue #428: циклическое переключение по ВСЕМ разделам через единый
    // реестр SECTIONS (раньше — жёсткий 4-элементный список, терявший
    // rules/insights/settings — рецидив #317, находка аудита #331).
    const i = SECTIONS.indexOf(state.section);
    setSection(SECTIONS[(i + 1) % SECTIONS.length]);
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
  let cmds = visibleCommands();
  // issue #368 (2.и): в режиме 2 (папка) действия разбора — только копирование
  // входа/выхода; run_again остаётся в палитре (Ctrl+K). Контент разбора (diff,
  // диагностика, глоссарий) не режется — это не «действия».
  if (state.mode === "tests") {
    cmds = cmds.filter(c => c.id === "copy_input" || c.id === "copy_output");
  }
  renderCommandButtons(el, cmds);
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

function setResultTab(tab) {
  state.resultTab = tab;
  document.querySelectorAll("[data-restab]").forEach(b => {
    const active = b.dataset.restab === tab;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  $("#restab-table").hidden = tab !== "table";
  $("#restab-detail").hidden = tab !== "detail";
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
  // issue #366: параметры режимов 3/4 — инлайн под полем пути (вкладок больше нет).
  $("#repeats-group").hidden = m !== "bench";
  $("#microbench-config").hidden = m !== "microbench";
  resetFilePicker();
  localStorage.setItem("grader_mode", m);
  if (m === "microbench") updateMicroCustomVisibility();
  // issue #324/#366 (2.з): «Функции в коде» — только режим 1. В режиме 2 путь —
  // папка, панель почти всегда пустует и мешает; в 3/4 неуместна.
  $("#check-terms-block").hidden = m !== "file";
  // issue #370 (2.к): режимы 3/4 — вертикальный стек (конфиг компактной полосой
  // сверху, результаты во всю ширину); режимы 1/2 остаются двухколоночными.
  const checkPane = $("#view-check .split-pane");
  if (checkPane) checkPane.classList.toggle("split-pane--stacked", m === "bench" || m === "microbench");
  loadCheckTerms();
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

// ---------------------------------------------------------------------------
// CodeMirror 6 editor (issue #265) — mode 1's code editor, mounted into the
// #solution-editor div (was a plain <textarea> before this issue). Kept as
// a small get/set API (getEditorCode/setEditorCode) so the rest of app.js
// doesn't need to know CodeMirror's own state/dispatch model — every former
// `$("#solution-editor").value` read/write below became one of these calls.
// ---------------------------------------------------------------------------
let cmView = null;
let checkTermsTimer = null; // issue #323: debounce обновления «Функции в коде»
function mountEditor() {
  const mount = document.getElementById("solution-editor");
  cmView = makeEditor(mount, () => {
    updateRunButtonState();
    updateDirtyIndicator(); // issue #297
    clearTimeout(checkTermsTimer); // issue #323: обновить панель под новый код
    checkTermsTimer = setTimeout(loadCheckTerms, 400);
  }, "Редактор решения");
  // <label for="solution-editor"> can't natively focus a contenteditable div
  // the way it would a real <textarea> -- wire it manually (issue #265
  // acceptance criterion: editor must be keyboard-reachable).
  const label = document.querySelector('label[for="solution-editor"]');
  if (label) label.addEventListener("click", () => cmView.focus());
}

// issue #317: редактор песочницы монтируется лениво при первом входе в раздел.
// issue #321: правка кода (debounce) обновляет панель «Функции в коде».
async function loadCheckTerms() {
  const el = $("#check-terms");
  if (!el) return;
  let body = null;
  const code = getEditorCode();
  if (code.trim()) {
    body = { code };
  } else if (state.selectedSolutionFile) {
    body = { path: state.selectedSolutionFile };
  }
  if (!body) {
    el.innerHTML = '<li class="empty">Начните вводить код или выберите решение</li>';
    return;
  }
  renderTermsInto(el, await fetchCodeTerms(body), "Знакомых функций не найдено");
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

// issue #55: импорт закреплённого решения Stepik (+топовые) в папку задачи как
// task{N}_{100+}.py. Берёт тот же путь папки, что «Найти решения»; при успехе
// обновляет список — импортированные reference-файлы появляются как решения.
async function findReference() {
  const folder = $("#path").value.trim();
  const list = $("#solutions-list");
  if (!folder) {
    list.innerHTML = '<li class="empty">Укажите папку задачи (со скачанной meta.json).</li>';
    return;
  }
  const btn = $("#find-reference-btn");
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Импорт…";
  list.innerHTML = '<li class="empty">Импорт эталона из Stepik…</li>';
  try {
    const r = await fetch("/api/import-reference", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: folder }),
    });
    const data = await r.json();
    if (!data.ok) {
      list.innerHTML = '<li class="empty">' + esc(data.message) + "</li>";
      return;
    }
    await refreshSolutionsList(); // покажет task{N}_{100+}.py среди решений
  } catch (e) {
    list.innerHTML = '<li class="empty">Ошибка запроса.</li>';
  } finally {
    btn.disabled = false;
    btn.textContent = original;
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
  const q = new URLSearchParams({ path, mode: backendMode, lang: state.lang }); // issue #364
  try {
    const r = await fetch("/api/grade?" + q.toString());
    const data = await r.json();
    state.lastResult = data;
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
    // issue #364: язык сообщений сервер читает из ?lang= POST-запроса (не из тела).
    const createResp = await fetch("/api/v1/runs?" + new URLSearchParams({ lang: state.lang }), {
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
    '<div class="progress-fill"></div></div>' +
    '<div class="progress-label">' + done + " / " + total + "</div>";
  // issue #563: ширину ставим через CSSOM (el.style.width), а не инлайновым
  // style= в HTML-строке — иначе строгий CSP (style-src 'self') её блокирует.
  const fill = $("#bar").querySelector(".progress-fill");
  if (fill) fill.style.width = pct + "%";
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
  h += '<div class="data-table-wrap">' +
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
      '<tr class="caserow" id="c' + i + '"><td colspan="6">' +
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
  el.classList.toggle("is-open");
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
  // issue #368 (2.е): ядро разбора — сравнение «Ожидалось / Получено». Для WA —
  // двухколоночно (на узкой панели колонки стекаются), плюс diff.
  if (c.verdict === "WA") {
    h +=
      '<div class="compare-grid">' +
      '<div><div class="field-label">Ожидалось</div>' + codeBlock(c.expected) + "</div>" +
      '<div><div class="field-label">Получено</div>' + codeBlock(c.actual) + "</div>" +
      "</div>";
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
  // issue #368 (2.е): «Сырой stdout/stderr» — коллапсируемая секция (единственное,
  // что бывший «Лог» давал сверх «Деталей»); свёрнута по умолчанию.
  const raw = c.actual || c.stderr || c.error || "";
  h +=
    '<details class="raw-output"><summary>Сырой stdout/stderr</summary>' +
    codeBlock(raw || "(пусто)") +
    "</details>";
  h += '<div id="detail-actions" class="action-cards"></div>';
  content.innerHTML = h;
  renderActionCards();
}

function renderBench(rows) {
  const ranked = rows.filter(r => !r.error);
  const similarCount = ranked.filter(r => r.verdict === "SIMILAR" || r.verdict === "REFERENCE").length;
  $("#bar").textContent = "";
  // issue #370: KPI унифицированы с режимом 4 — «Решений / Лучшая медиана / Схожих».
  let h = kpiGrid([
    { label: "Решений", value: rows.length },
    { label: "Лучшая медиана", value: ranked.length ? ranked[0].median : "—" },
    { label: "Схожих", value: ranked.length ? similarCount + " / " + ranked.length : "—" },
  ]);
  h += benchTable(rows, {
    fields: { min: "min", median: "median", mean: "mean", max: "max", stdev: "stdev" },
    memLabel: "Память, МБ",
  });
  $("#out").innerHTML = h;
}

// issue #370: единая структура таблиц режимов 3/4 = CLI-репортер
// (Файл/Runs/Min/Median/Mean/Max/Std dev/Память/%/Вердикт). Различия между
// режимами — только имена полей (единицы s vs µs) и подпись памяти (issue #66).
function benchTable(rows, { fields, memLabel, memTitle }) {
  const memTh =
    '<th scope="col"' + (memTitle ? ' title="' + esc(memTitle) + '"' : "") + ">" + esc(memLabel) + "</th>";
  let h =
    '<div class="data-table-wrap">' +
    '<table class="data-table"><thead><tr><th scope="col">Файл</th><th scope="col">Runs</th>' +
    '<th scope="col">Min</th><th scope="col">Median</th><th scope="col">Mean</th>' +
    '<th scope="col">Max</th><th scope="col">Std dev</th>' +
    memTh +
    '<th scope="col">%</th><th scope="col">Вердикт</th></tr></thead><tbody>';
  rows.forEach(row => {
    if (row.error) {
      h +=
        '<tr><td class="mono">' + esc(row.file) + '</td><td colspan="8">' + esc(row.error) +
        "</td><td>" + renderVerdict(row.verdict) + "</td></tr>";
      return;
    }
    h +=
      '<tr><td class="mono">' + esc(row.file) + '</td><td class="mono">' + row.runs + "</td>" +
      '<td class="mono">' + esc(row[fields.min]) + "</td>" +
      '<td class="mono">' + esc(row[fields.median]) + "</td>" +
      '<td class="mono">' + esc(row[fields.mean]) + "</td>" +
      '<td class="mono">' + esc(row[fields.max]) + "</td>" +
      '<td class="mono">' + esc(row[fields.stdev]) + "</td>" +
      '<td class="mono">' + (row.memory_mb ?? "—") + "</td>" +
      '<td class="mono">' + row.relative + "%</td>" +
      "<td>" + renderVerdict(row.verdict) + "</td></tr>";
  });
  return h + "</tbody></table></div>";
}

function renderMicrobench(data) {
  const rows = data.rows;
  const ok = rows.filter(r => !r.error);
  const similarCount = ok.filter(r => r.verdict === "SIMILAR").length;
  $("#bar").textContent = "";
  let h = "";
  if (data.other_groups && data.other_groups.length) {
    h +=
      '<p class="hint pad-t3-x4">Группа «' +
      esc(data.group) + '» · остальные (не показаны): ' + data.other_groups.map(esc).join(", ") +
      "</p>";
  }
  h += kpiGrid([
    { label: "Решений", value: rows.length },
    { label: "Лучшая медиана, µs", value: ok.length ? ok[0].median_us : "—" },
    { label: "Схожих", value: ok.length ? similarCount + " / " + ok.length : "—" },
  ]);
  h += benchTable(rows, {
    fields: { min: "min_us", median: "median_us", mean: "mean_us", max: "max_us", stdev: "stdev_us" },
    memLabel: "Py-heap, МБ",
    memTitle: "tracemalloc (stdin) / RSS (function), issue #66",
  });
  $("#out").innerHTML = h;
}

function errorCard(g) {
  return (
    '<div class="errcard"><span class="errcard-ex">💡 ' + esc(g.exception) + "</span> " +
    esc(g.hint) +
    ' <a href="' + esc(g.url) + '" target="_blank" rel="noopener">' +
    "открыть карточку в глоссарии →</a></div>"
  );
}

// -- Недавние пути (в левой панели «Конфигурация») ---------------------------

function addRecentPath(path) {
  let recent = JSON.parse(localStorage.getItem("grader_recent_paths") || "[]");
  recent = [path, ...recent.filter(p => p !== path)].slice(0, 8);
  localStorage.setItem("grader_recent_paths", JSON.stringify(recent));
  renderRecentPaths(recent);
}

function renderRecentPaths(recent) {
  recent = recent || JSON.parse(localStorage.getItem("grader_recent_paths") || "[]");
  const el = $("#recent-paths-datalist");
  if (!el) return;
  // issue #364: недавние пути — <option> нативного datalist у поля #path. Выбор
  // подставляет путь; запуск — по Enter/«Запустить» (как при ручном вводе).
  el.innerHTML = recent.map(p => '<option value="' + esc(p) + '"></option>').join("");
}

// -- Глоссарий: поиск / карточка / очередь пополнения (J7) --------------------

// issue #329: быстрые фильтры-чипы по разделам. Разнотипное НЕ объединяется —
// «Списки», «Кортежи», «Словари», «Множества» — отдельные чипы (в отличие от
// внешнего Glossary-Python, где они слиты в «Списки · кортежи»/«Словари · множества»).
export {
  addRecentPath,
  cancelActiveRun,
  checkTermsTimer,
  closePalette,
  findReference,
  findSolutions,
  grade,
  loadCheckTerms,
  loadCommands,
  mountEditor,
  openPalette,
  paletteCommands,
  renderPaletteList,
  renderRecentPaths,
  runCommand,
  saveSolution,
  setMode,
  setResultTab,
  updateDirtyIndicator,
  updateMicroCustomVisibility,
};
