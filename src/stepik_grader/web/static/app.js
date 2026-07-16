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
  syntaxHighlighting, HighlightStyle, indentOnInput,
  python, tags,
} from "/static/vendor/codemirror-bundle@6.mjs";

const $ = s => document.querySelector(s);
// issue #214: экранируем и кавычки — esc() используется не только в текстовом
// контексте (innerHTML), но и внутри HTML-атрибутов (errorCard() вставляет
// esc(g.url) в href="..."); без \"/' значение могло бы разорвать атрибут.
const HT = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c => HT[c]);

// ---------------------------------------------------------------------------
// Единый реестр разделов (issue #428): порядок = порядок Ctrl-переключения
// команды «Переключить раздел». И setSection() (показ/скрытие #view-*), и
// switch_section() ходят по нему — новый раздел добавляется один раз здесь и
// забыть его нельзя (устраняет рецидив #317: switch_section циклил 4 из 7).
// ---------------------------------------------------------------------------
const SECTIONS = ["check", "downloader", "glossary", "rules", "insights", "sandbox", "settings"];

// State (issue #125) — единый источник состояния для split-pane workspace,
// command palette, action cards и сценарных кнопок.
// ---------------------------------------------------------------------------
const state = {
  section: localStorage.getItem("grader_section") || "check", // одно из SECTIONS
  lang: localStorage.getItem("grader_lang") || "ru", // issue #364 — язык сообщений сервера (?lang=)
  mode: localStorage.getItem("grader_mode") || "tests", // "file" | "tests" | "bench" | "microbench"
  resultTab: "table", // "table" | "detail"
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
  rules: { query: "", tag: "", cards: [], selectedId: null }, // issue #348
  insights: { cards: [] }, // issue #348
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
  syncSettingsControls(); // держим select «Настроек» в согласии с топбар-тумблером
}

// -- Настройки (issue #364) --------------------------------------------------

function setTheme(value) {
  state.theme = value;
  localStorage.setItem("grader_theme", value);
  applyTheme();
}

function setLang(value) {
  state.lang = value;
  localStorage.setItem("grader_lang", value);
  // issue #363: контент глоссария локализуется сервером по ?lang=. Сбрасываем
  // кеш карточек и, если раздел открыт, перезагружаем — summary/body сменят
  // язык без перезагрузки страницы; ранее открытая карточка переоткрывается.
  const openId = state.glossary.selectedId;
  state.glossary.cards = [];
  state.glossary.sections = [];
  if (state.section === "glossary") {
    loadGlossary().then(() => {
      if (openId) selectGlossaryCard(openId, { fromHash: true });
    });
  }
}

// Синхронизировать контролы раздела «Настройки» с текущим состоянием (тему
// можно менять и топбар-тумблером — тогда select не должен отставать).
function syncSettingsControls() {
  const themeSel = $("#settings-theme");
  const langSel = $("#settings-lang");
  if (themeSel) themeSel.value = state.theme;
  if (langSel) langSel.value = state.lang;
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
  // issue #428: показать выбранный #view-*, скрыть остальные — по единому
  // реестру SECTIONS (id раздела == суффикс #view-<section>).
  SECTIONS.forEach(s => {
    $("#view-" + s).hidden = section !== s;
  });
  if (section === "glossary" && !state.glossary.cards.length) loadGlossary();
  if (section === "rules" && !state.rules.cards.length) loadRules();
  if (section === "insights") loadInsights();
  if (section === "downloader") loadAuthStatus(); // issue #402: статус OAuth
  if (section === "sandbox") {
    mountSandboxEditor(); // issue #317: ленивый монтаж
    loadCodeTerms(); // issue #321: обновить «Функции в коде» под текущий код
  }
  if (section === "settings") syncSettingsControls(); // issue #364
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

// -- Result-panel tabs (Таблица / Разбор) -------------------------------------

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
let sandboxView = null; // issue #317: отдельный редактор песочницы

// issue #425: подсветка синтаксиса на CSS-переменных темы (--cm-*), а не на
// светлом defaultHighlightStyle из бандла (keyword давал ~1.85:1 на тёмном
// фоне — код был нечитаем). Все --cm-* заданы в app.css для light и dark с
// контрастом ≥4.5:1 к фону редактора (--color-surface, проверяет
// scripts/check_contrast.py). Токены-теги и HighlightStyle экспортируются
// пересобранным бандлом (static/vendor/VERSIONS.md).
const themeHighlightStyle = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--cm-keyword)" },
  { tag: [tags.atom, tags.bool, tags.null, tags.number], color: "var(--cm-literal)" },
  {
    tag: [tags.string, tags.special(tags.string), tags.docString, tags.character, tags.inserted],
    color: "var(--cm-string)",
  },
  { tag: [tags.regexp, tags.escape], color: "var(--cm-string)" },
  {
    tag: [tags.function(tags.variableName), tags.function(tags.propertyName), tags.labelName],
    color: "var(--cm-function)",
  },
  { tag: [tags.typeName, tags.className, tags.namespace, tags.self], color: "var(--cm-type)" },
  { tag: [tags.operator, tags.derefOperator], color: "var(--cm-operator)" },
  {
    tag: [tags.comment, tags.lineComment, tags.blockComment, tags.meta],
    color: "var(--cm-comment)",
    fontStyle: "italic",
  },
  { tag: tags.invalid, color: "var(--cm-invalid)" },
  { tag: tags.strong, fontWeight: "bold" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.link, textDecoration: "underline" },
]);

// Фабрика CodeMirror-редактора: общий theme/набор расширений для редактора
// режима 1 и песочницы (issue #317). onChange (опц.) — колбэк на docChanged.
// label (issue #409) — доступное имя для .cm-content (скринридеры, WCAG 4.1.2):
// нативный <label for=…> не достаёт до contenteditable-узла CodeMirror.
function makeEditor(mount, onChange, label) {
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
    ".cm-placeholder": { color: "var(--color-text-placeholder)" },
  });
  return new EditorView({
    doc: "",
    parent: mount,
    extensions: [
      lineNumbers(),
      history(),
      indentOnInput(),
      syntaxHighlighting(themeHighlightStyle, { fallback: true }),
      keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
      python(),
      theme,
      cmPlaceholder(mount.dataset.placeholder || ""),
      EditorView.contentAttributes.of({ "aria-label": label || "Редактор кода" }),
      EditorView.updateListener.of(update => {
        if (update.docChanged && onChange) onChange();
      }),
    ],
  });
}

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
let sandboxTermsTimer = null;
function mountSandboxEditor() {
  if (sandboxView) return;
  const mount = document.getElementById("sandbox-editor");
  sandboxView = makeEditor(mount, () => {
    clearTimeout(sandboxTermsTimer);
    sandboxTermsTimer = setTimeout(loadCodeTerms, 400);
  }, "Редактор кода песочницы");
  const label = document.querySelector('label[for="sandbox-editor"]');
  if (label) label.addEventListener("click", () => sandboxView.focus());
}

// issue #322/#323: общий запрос/рендер мини-карточек «Функции в коде» —
// песочница (по коду) и режимы 1/2 (по коду редактора либо пути-файлу).
async function fetchCodeTerms(body) {
  try {
    const resp = await fetch("/api/code-terms?lang=" + encodeURIComponent(state.lang), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return (await resp.json()).terms || [];
  } catch {
    return null; // сеть моргнула — вызывающий не трогает прошлый список
  }
}

function renderTermCards(terms) {
  return terms
    .map(t => {
      const kind = t.kind ? '<span class="term-card-kind">' + esc(t.kind) + "</span>" : "";
      if (!t.has_card) {
        // концепция без карточки — приглушённо, без ссылки (открывать нечего)
        return (
          '<li class="term-card term-card-nocard"><span class="term-card-link">' +
          '<span class="term-card-title">' +
          esc(t.title) +
          "</span>" +
          kind +
          '<span class="term-card-summary hint">карточки ещё нет</span></span></li>'
        );
      }
      const uncertain = t.confidence === "low" ? " term-card-uncertain" : "";
      const summary = t.summary
        ? '<span class="term-card-summary">' + esc(t.summary) + "</span>"
        : "";
      return (
        '<li class="term-card' +
        uncertain +
        '"><a class="term-card-link" href="#/glossary/' +
        encodeURIComponent(t.id) +
        '"><span class="term-card-title">' +
        esc(t.title) +
        "</span>" +
        kind +
        summary +
        "</a></li>"
      );
    })
    .join("");
}

function renderTermsInto(el, terms, emptyMsg) {
  if (!el || terms === null) return; // null = ошибка сети, список не трогаем
  el.innerHTML = terms.length
    ? renderTermCards(terms)
    : '<li class="empty">' + esc(emptyMsg) + "</li>";
}

// песочница (issue #321): по коду редактора песочницы
async function loadCodeTerms() {
  const code = getSandboxCode();
  const el = $("#sandbox-terms");
  if (!el) return;
  if (!code.trim()) {
    el.innerHTML = '<li class="empty">Начните вводить код</li>';
    return;
  }
  renderTermsInto(el, await fetchCodeTerms({ code }), "Знакомых функций не найдено");
}

// режим 1 (issue #323/#366): панель питается кодом редактора либо выбранным в
// пикере файлом. В режимах 2/3/4 блок скрыт (setMode), так что путь-ветку
// режима 2 не запрашиваем (issue #366/2.з).
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

// issue #321: имя класса исключения из последней строки stderr (или null) —
// зеркалит серверный detector._last_exception_name для error card песочницы.
const _EXC_NO_SUFFIX = [
  "StopIteration",
  "StopAsyncIteration",
  "KeyboardInterrupt",
  "SystemExit",
  "GeneratorExit",
];
function extractExceptionType(text) {
  const lines = (text || "").trim().split("\n").filter(l => l.trim());
  if (!lines.length) return null;
  let cand = lines[lines.length - 1].trim().split(":")[0].trim();
  cand = cand.split(".").pop();
  if (!/^[A-Z]\w*$/.test(cand)) return null;
  return /(Error|Exception|Warning)$/.test(cand) || _EXC_NO_SUFFIX.includes(cand) ? cand : null;
}

// issue #321: error card под выводом при RE — тип исключения + deep-link в
// глоссарий (карточка есть → откроется; нет → раздел покажет «не найдено»).
function sandboxErrorCard(stderr) {
  const exc = extractExceptionType(stderr);
  if (!exc) return "";
  return (
    '<div class="sandbox-errcard">⛔ <strong>' +
    esc(exc) +
    '</strong> <a class="trace-error-link" href="#/glossary/' +
    encodeURIComponent(exc.toLowerCase()) +
    '">открыть карточку →</a></div>'
  );
}

function renderSandboxResult(r) {
  setSandboxStatus(r.status);
  const parts = [];
  if (r.status === "RE") parts.push(sandboxErrorCard(r.stderr));
  parts.push(
    '<div class="form-label">Вывод (stdout)</div>',
    '<pre class="code-block">' +
      (r.stdout ? esc(r.stdout) : '<span class="hint">(пусто)</span>') +
      "</pre>"
  );
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
    view: "table", // issue #320: "table" | "diagram"
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
    '<div class="trace-vars-head">' +
    '<span class="form-label">Переменные</span>' +
    '<div class="trace-view-toggle" role="group" aria-label="Вид переменных">' +
    '<button type="button" data-traceview="table" class="active">Таблица</button>' +
    '<button type="button" data-traceview="diagram">Диаграмма</button>' +
    "</div>" +
    "</div>" +
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
  out.querySelectorAll("[data-traceview]").forEach(btn =>
    btn.addEventListener("click", () => {
      const tr = state.sandbox.trace;
      if (!tr) return;
      tr.view = btn.dataset.traceview;
      out
        .querySelectorAll("[data-traceview]")
        .forEach(b => b.classList.toggle("active", b === btn));
      renderTraceStep(tr.idx);
    })
  );
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

  // 2. переменные: таблица кадров либо диаграмма связей (issue #320)
  if (tr.view === "diagram") renderTraceDiagram(step, prev);
  else $("#trace-frames").innerHTML = renderTraceFrames(step, prev);

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

// ─────────────────── диаграмма связей переменных (issue #320) ───────────────
// Memory-graph в духе Python Tutor: слева кадры (переменные), справа узлы
// heap-объектов; переменная-ссылка → стрелка в узел. Aliasing = две стрелки в
// один узел (узлы по heap-id), вложенность = стрелки между узлами. Примитивы —
// инлайн в кадре. Чистый SVG на vanilla JS (без внешних либ, политика #260/#265):
// боксы — HTML, стрелки — SVG-оверлей, чьи концы измеряются по DOM после верстки.

const _MEM_MAX_NODES = 40; // больше объектов — деградация в таблицу (Acceptance #320)

function renderTraceDiagram(step, prev) {
  const heap = step.heap || {};
  const ids = Object.keys(heap);
  const host = $("#trace-frames");
  if (ids.length > _MEM_MAX_NODES) {
    host.innerHTML =
      '<div class="hint mem-toobig">Слишком много объектов (' +
      ids.length +
      ") для диаграммы — показана таблица.</div>" +
      renderTraceFrames(step, prev);
    return;
  }
  host.innerHTML =
    '<div class="mem-graph"><div class="mem-cols" id="mem-cols">' +
    '<svg class="mem-arrows" id="mem-arrows" aria-hidden="true"></svg>' +
    '<div class="mem-col mem-frames-col">' +
    memFramesHtml(step, prev) +
    "</div>" +
    '<div class="mem-col mem-heap-col">' +
    memHeapHtml(heap) +
    "</div>" +
    "</div></div>";
  drawMemArrows();
}

function memFramesHtml(step, prev) {
  const heap = step.heap || {};
  const frames = step.stack || [];
  const out = [];
  for (let k = frames.length - 1; k >= 0; k--) {
    const fr = frames[k];
    const prevFr = prev && prev.stack && prev.stack[k];
    const title = k === 0 ? "Глобальные" : esc(fr.func) + "()";
    const names = Object.keys(fr.locals || {});
    const rows = names.map(name => {
      const ref = fr.locals[name];
      const changed =
        !prevFr ||
        !(name in (prevFr.locals || {})) ||
        JSON.stringify(prevFr.locals[name]) !== JSON.stringify(ref);
      const cls = "mem-var" + (changed ? " is-changed" : "");
      const isRef = ref && typeof ref === "object" && "ref" in ref;
      const slot = isRef
        ? '<span class="mem-anchor" data-to="' + esc(ref.ref) + '"></span>'
        : '<span class="mem-var-val">' + fmtRef(ref, heap, 0) + "</span>";
      return (
        '<div class="' + cls + '"><span class="mem-var-name">' + esc(name) + "</span>" + slot + "</div>"
      );
    });
    const body = rows.length ? rows.join("") : '<div class="hint">нет переменных</div>';
    out.push('<div class="mem-frame"><div class="mem-frame-title">' + title + "</div>" + body + "</div>");
  }
  return out.join("");
}

function memHeapHtml(heap) {
  return Object.keys(heap)
    .map(id => {
      const obj = heap[id];
      return (
        '<div class="mem-node" id="mem-node-' +
        esc(id) +
        '" data-node="' +
        esc(id) +
        '"><div class="mem-node-title">' +
        esc(obj.type || obj.kind || "?") +
        "</div>" +
        memNodeBody(obj, heap) +
        "</div>"
      );
    })
    .join("");
}

function memNodeBody(obj, heap) {
  const slot = ref =>
    ref && typeof ref === "object" && "ref" in ref
      ? '<span class="mem-anchor" data-to="' + esc(ref.ref) + '"></span>'
      : '<span class="mem-cell-val">' + fmtRef(ref, heap, 0) + "</span>";
  if (obj.kind === "seq") {
    const cells = (obj.elems || []).map(
      (e, i) => '<div class="mem-cell"><span class="mem-idx">' + i + "</span>" + slot(e) + "</div>"
    );
    if (obj.n > (obj.elems || []).length) cells.push('<div class="mem-cell hint">…' + obj.n + "</div>");
    return '<div class="mem-seq">' + cells.join("") + "</div>";
  }
  if (obj.kind === "map" || obj.kind === "object") {
    const entries =
      obj.kind === "map"
        ? (obj.entries || []).map(([k, v]) => [fmtRef(k, heap, 0), v])
        : Object.keys(obj.attrs || {}).map(a => [esc(a), obj.attrs[a]]);
    const rows = entries.map(
      ([k, v]) => '<div class="mem-entry"><span class="mem-key">' + k + "</span>" + slot(v) + "</div>"
    );
    if (obj.kind === "map" && obj.n > (obj.entries || []).length)
      rows.push('<div class="mem-entry hint">…' + obj.n + "</div>");
    return '<div class="mem-map">' + rows.join("") + "</div>";
  }
  if (obj.kind === "func") return '<div class="mem-cell-val">ƒ ' + esc(obj.name) + "</div>";
  if (obj.kind === "module") return '<div class="mem-cell-val">module ' + esc(obj.name) + "</div>";
  return '<div class="mem-cell-val">' + esc(obj.repr || obj.type || "?") + "</div>";
}

// Провести SVG-стрелки от каждого .mem-anchor[data-to] к узлу #mem-node-<id>.
// Координаты — относительно .mem-cols (position:relative), измеряются по DOM
// после вставки innerHTML (getBoundingClientRect форсит синхронную верстку).
function drawMemArrows() {
  const cols = $("#mem-cols");
  const svg = $("#mem-arrows");
  if (!cols || !svg) return;
  const base = cols.getBoundingClientRect();
  const w = cols.scrollWidth;
  const h = cols.scrollHeight;
  svg.setAttribute("width", String(w));
  svg.setAttribute("height", String(h));
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);
  const parts = [
    '<defs><marker id="mem-ah" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">' +
      '<path d="M0,0 L7,3 L0,6 Z" class="mem-arrowhead"/></marker></defs>',
  ];
  cols.querySelectorAll(".mem-anchor[data-to]").forEach(anchor => {
    const node = document.getElementById("mem-node-" + anchor.dataset.to);
    if (!node) return;
    const ar = anchor.getBoundingClientRect();
    const nr = node.getBoundingClientRect();
    const x1 = ar.left + ar.width / 2 - base.left;
    const y1 = ar.top + ar.height / 2 - base.top;
    const x2 = nr.left - base.left - 1;
    const y2 = nr.top + Math.min(13, nr.height / 2) - base.top;
    const dx = Math.max(24, (x2 - x1) / 2);
    parts.push('<circle class="mem-dot" cx="' + x1 + '" cy="' + y1 + '" r="3"/>');
    parts.push(
      '<path class="mem-arrow" d="M' +
        x1 +
        "," +
        y1 +
        " C" +
        (x1 + dx) +
        "," +
        y1 +
        " " +
        (x2 - dx) +
        "," +
        y2 +
        " " +
        x2 +
        "," +
        y2 +
        '" marker-end="url(#mem-ah)"/>'
    );
  });
  svg.innerHTML = parts.join(""); // SVG-namespace parsing (современные браузеры)
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
    '<div class="data-table-wrap" style="padding:0 var(--space-4) var(--space-4)">' +
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
      '<p class="hint" style="padding:var(--space-3) var(--space-4) 0">Группа «' +
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
  params.set("lang", state.lang); // issue #363: язык summary/body карточек
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
  fetch("/api/glossary/" + encodeURIComponent(id) + "?lang=" + encodeURIComponent(state.lang))
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
  // issue #348: единый hash-роутер (замена glossary-only, риск R6) — deep-links
  // #/rules/<code>, #/insights и #/glossary/<id>.
  const h = location.hash;
  const rule = h.match(/^#\/rules\/(.+)$/);
  if (rule) {
    const code = decodeURIComponent(rule[1]);
    if (state.section !== "rules") setSection("rules");
    if (!state.rules.cards.length) await loadRules();
    if (state.rules.selectedId !== code) selectRuleCard(code, { fromHash: true });
    return;
  }
  if (h === "#/insights") {
    if (state.section !== "insights") setSection("insights");
    return;
  }
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

// -- Раздел «Правила (PEP)» (issue #348) --------------------------------------

const RULE_TAGS = ["whitespace", "imports", "blank-lines", "comparisons", "statements", "pyflakes"];

async function loadRules() {
  const r = state.rules;
  const params = new URLSearchParams();
  if (r.query) params.set("q", r.query);
  if (r.tag) params.set("tag", r.tag);
  try {
    const resp = await fetch("/api/rules?" + params);
    r.cards = await resp.json();
  } catch (e) {
    r.cards = [];
  }
  renderRulesChips();
  renderRulesList();
  const cnt = $("#rules-count");
  if (cnt) cnt.textContent = r.cards.length + " правил";
}

function renderRulesChips() {
  const el = $("#rules-chips");
  if (!el) return;
  el.innerHTML = RULE_TAGS.map(t => {
    const active = state.rules.tag === t ? " active" : "";
    return '<button type="button" class="chip' + active + '" data-tag="' + esc(t) + '">' + esc(t) + "</button>";
  }).join("");
  el.querySelectorAll(".chip").forEach(b =>
    b.addEventListener("click", () => {
      state.rules.tag = state.rules.tag === b.dataset.tag ? "" : b.dataset.tag;
      loadRules();
    })
  );
}

function renderRulesList() {
  const el = $("#rules-cards");
  if (!el) return;
  if (!state.rules.cards.length) {
    el.innerHTML = '<li class="empty">Ничего не найдено</li>';
    return;
  }
  el.innerHTML = state.rules.cards
    .map(c => {
      const sel = c.id === state.rules.selectedId ? " selected" : "";
      // issue #403: подсветить правила, которые пользователь нарушал лично.
      const violated = c.violated ? " violated" : "";
      const badge = c.violated
        ? ' <span class="rule-violated" title="Вы нарушали это правило">⚠</span>'
        : "";
      return '<li data-rule="' + esc(c.id) + '" class="' + sel + violated + '"><span class="rule-code">' +
        esc(c.id) + "</span> " + esc(c.title) + badge + "</li>";
    })
    .join("");
  el.querySelectorAll("li[data-rule]").forEach(li =>
    li.addEventListener("click", () => selectRuleCard(li.dataset.rule))
  );
}

function selectRuleCard(code, opts = {}) {
  state.rules.selectedId = code;
  renderRulesList();
  if (!opts.fromHash) {
    const target = "#/rules/" + encodeURIComponent(code);
    if (location.hash !== target) location.hash = target;
  }
  const card = state.rules.cards.find(c => c.id === code);
  if (card) {
    renderRuleDetail(card);
    return;
  }
  fetch("/api/rules/" + encodeURIComponent(code))
    .then(r => (r.ok ? r.json() : null))
    .then(c => { if (c) renderRuleDetail(c); })
    .catch(() => {});
}

function renderRuleDetail(card) {
  $("#rules-empty").hidden = true;
  const el = $("#rules-detail-content");
  el.hidden = false;
  const sev = card.severity ? ' <span class="badge badge-neutral">' + esc(card.severity) + "</span>" : "";
  const bad = card.example_bad
    ? '<div class="form-label">Как не надо</div><pre class="code-block code-bad">' + esc(card.example_bad) + "</pre>"
    : "";
  const good = card.example_good
    ? '<div class="form-label">Как надо</div><pre class="code-block code-good">' + esc(card.example_good) + "</pre>"
    : "";
  const links = [
    card.pep_url ? '<a href="' + esc(card.pep_url) + '" target="_blank" rel="noopener">PEP 8 →</a>' : "",
    card.docs_url ? '<a href="' + esc(card.docs_url) + '" target="_blank" rel="noopener">Документация →</a>' : "",
  ].filter(Boolean).map(a => "<p>" + a + "</p>").join("");
  el.innerHTML =
    '<h2><span class="rule-code">' + esc(card.id) + "</span> " + esc(card.title) + sev + "</h2>" +
    (card.summary ? "<p>" + esc(card.summary) + "</p>" : "") +
    (card.body ? '<div class="rule-body">' + esc(card.body) + "</div>" : "") +
    bad + good + links;
}

// -- Раздел «Подучить» (issue #348) -------------------------------------------

const INSIGHT_STATUS = {
  active: { icon: "🔥", label: "активна" },
  fading: { icon: "🌤", label: "угасает" },
  watch: { icon: "👀", label: "наблюдение" },
  archived: { icon: "✅", label: "в архиве" },
};

async function loadInsights() {
  try {
    const r = await fetch("/api/insights");
    state.insights.cards = await r.json();
  } catch (e) {
    state.insights.cards = [];
  }
  renderInsights();
  updateInsightsBadge();
}

function renderInsights() {
  const empty = $("#insights-empty");
  const list = $("#insights-cards");
  if (!list) return;
  const cards = state.insights.cards;
  empty.hidden = cards.length > 0;
  list.hidden = cards.length === 0;
  list.innerHTML = cards
    .map(c => {
      const st = INSIGHT_STATUS[c.status] || { icon: "•", label: c.status };
      const ref = c.glossary_id
        ? ' · <a href="#/glossary/' + esc(c.glossary_id) + '">→ глоссарий</a>'
        : c.category === "lint"
          ? ' · <a href="#/rules/' + esc(c.key) + '">→ правило</a>'
          : "";
      return (
        '<li class="insight-card insight-' + esc(c.status) + '">' +
        '<div class="insight-head"><span class="insight-status">' + st.icon + " " + esc(st.label) +
        '</span> <span class="insight-key">' + esc(c.key) + "</span></div>" +
        '<div class="hint">замечено в ' + c.hits + " из " + c.runs_considered + " прогонов" + ref + "</div>" +
        "</li>"
      );
    })
    .join("");
}

function updateInsightsBadge() {
  const el = $("#sidebar-badge-insights");
  if (!el) return;
  const active = state.insights.cards.filter(c => c.status === "active").length;
  el.textContent = String(active);
  el.hidden = active === 0;
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

// -- Авторизация Stepik: мастер первого запуска (issue #402) -------------------
// GET /api/auth/status → форма (client_id/secret) → POST /api/auth/start →
// polling /api/v1/runs/{id}. Браузерный OAuth убирает CLI-стену онбординга:
// авторизоваться можно прямо из web, без ручного создания secrets.json.

async function loadAuthStatus() {
  const panel = $("#auth-panel");
  if (!panel) return;
  panel.hidden = false;
  panel.innerHTML = '<p class="msg">Проверка доступа к Stepik…</p>';
  try {
    const r = await fetch("/api/auth/status");
    renderAuthPanel(await r.json());
  } catch (e) {
    panel.innerHTML = '<p class="msg">Не удалось проверить доступ: ' + esc(String(e)) + "</p>";
  }
}

function renderAuthPanel(data) {
  const panel = $("#auth-panel");
  if (!panel) return;
  if (data && data.authorized) {
    panel.innerHTML =
      '<div class="auth-ok">✅ Доступ к Stepik активен. ' +
      '<button id="auth-recheck" class="btn btn-secondary btn-sm">Проверить токен</button></div>';
    return;
  }
  const redirect = "http://localhost:8080/callback";
  panel.innerHTML =
    '<div class="auth-form">' +
    '<p class="msg">Для загрузки задач нужен доступ к Stepik — авторизуйтесь прямо здесь, ' +
    "без ручного создания <code>secrets.json</code>.</p>" +
    '<p class="hint">1. Создайте приложение на ' +
    '<a href="https://stepik.org/oauth2/applications/" target="_blank" rel="noopener">' +
    "stepik.org/oauth2/applications</a>: Client type = Confidential, grant type = " +
    "Authorization code, Redirect uris = <code>" +
    redirect +
    "</code>.</p>" +
    '<div class="form-group"><label class="form-label" for="auth-client-id">Client id</label>' +
    '<input class="form-input" id="auth-client-id" type="text" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="auth-client-secret">Client secret</label>' +
    '<input class="form-input" id="auth-client-secret" type="password" autocomplete="off"></div>' +
    '<button id="auth-start" class="btn btn-primary">🔑 Авторизоваться в браузере</button>' +
    '<div id="auth-progress" class="hint" hidden></div>' +
    "</div>";
}

async function startBrowserAuth() {
  const idEl = $("#auth-client-id");
  const secEl = $("#auth-client-secret");
  const progress = $("#auth-progress");
  const btn = $("#auth-start");
  const clientId = idEl ? idEl.value.trim() : "";
  const clientSecret = secEl ? secEl.value.trim() : "";
  if (progress) progress.hidden = false;
  if (!clientId || !clientSecret) {
    if (progress) progress.textContent = "Укажите client_id и client_secret.";
    return;
  }
  if (btn) btn.disabled = true;
  if (progress)
    progress.textContent =
      "Открываю браузер… завершите вход в открывшейся вкладке (до 2 минут).";
  try {
    const resp = await fetch("/api/auth/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
    });
    const created = await resp.json();
    if (!created.run_id) {
      if (progress) progress.textContent = created.message || "Не удалось запустить авторизацию.";
      if (btn) btn.disabled = false;
      return;
    }
    pollAuth(created.run_id);
  } catch (e) {
    if (progress) progress.textContent = "Ошибка запроса: " + String(e);
    if (btn) btn.disabled = false;
  }
}

function pollAuth(runId) {
  const progress = $("#auth-progress");
  const btn = $("#auth-start");
  const step = async () => {
    try {
      const r = await fetch("/api/v1/runs/" + runId);
      const data = await r.json();
      if (!r.ok || !data.status) {
        // Job исчез (404 run_not_found) или ответ без статуса — не крутить опрос
        // бесконечно (issue #402).
        if (progress)
          progress.textContent = data.message || "Не удалось получить статус авторизации.";
        if (btn) btn.disabled = false;
        return;
      }
      if (data.status === "done") {
        loadAuthStatus(); // перерисует панель как «Доступ активен»
        return;
      }
      if (data.status === "error" || data.status === "cancelled") {
        if (progress)
          progress.textContent =
            data.message || "Не удалось авторизоваться. Проверьте client_id/client_secret.";
        if (btn) btn.disabled = false;
        return;
      }
      setTimeout(step, 800);
    } catch (e) {
      if (progress) progress.textContent = "Ошибка опроса: " + String(e);
      if (btn) btn.disabled = false;
    }
  };
  step();
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
$("#find-reference-btn").addEventListener("click", findReference); // issue #55
$("#save-solution-btn").addEventListener("click", saveSolution); // issue #297
$("#path").addEventListener("input", updateDirtyIndicator); // issue #297 — папка влияет на доступность «Сохранить»
$("#path").addEventListener("input", () => {
  // issue #323: путь к .py-файлу тоже наполняет «Функции в коде» (debounce)
  clearTimeout(checkTermsTimer);
  checkTermsTimer = setTimeout(loadCheckTerms, 400);
});
$("#micro-profile").addEventListener("change", updateMicroCustomVisibility);
$("#downloader-run").addEventListener("click", downloadTask);
$("#downloader-url").addEventListener("keydown", e => {
  if (e.key === "Enter") downloadTask();
});
// issue #402: кнопки мастера авторизации рендерятся динамически — делегирование.
$("#auth-panel").addEventListener("click", e => {
  const t = e.target;
  if (!t) return;
  if (t.id === "auth-start") startBrowserAuth();
  else if (t.id === "auth-recheck") loadAuthStatus();
});
$("#theme-toggle").addEventListener("click", cycleTheme);
$("#settings-theme").addEventListener("change", e => setTheme(e.target.value)); // issue #364
$("#settings-lang").addEventListener("change", e => setLang(e.target.value)); // issue #364
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
let rulesSearchTimer; // issue #348
$("#rules-search").addEventListener("input", e => {
  clearTimeout(rulesSearchTimer);
  state.rules.query = e.target.value;
  rulesSearchTimer = setTimeout(() => loadRules(), 200);
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

// issue #320: стрелки диаграммы — абсолютный SVG-оверлей, при ресайзе окна
// концы разъезжаются; переисчисляем их (сам layout боксов пересчитает браузер).
let _memResizeTimer = null;
window.addEventListener("resize", () => {
  const tr = state.sandbox.trace;
  if (state.section !== "sandbox" || !tr || tr.view !== "diagram" || !$("#mem-cols")) return;
  clearTimeout(_memResizeTimer);
  _memResizeTimer = setTimeout(drawMemArrows, 100);
});

applyTheme();
mountEditor();
setSection(state.section);
setMode(state.mode);
setResultTab(state.resultTab);
renderRecentPaths();
loadCommands();
routeFromHash(); // открыть карточку из #/glossary/<id>, если ссылка прямая (issue #329)

// Восстановить последний путь.
const saved = localStorage.getItem("grader_path");
if (saved) $("#path").value = saved;
