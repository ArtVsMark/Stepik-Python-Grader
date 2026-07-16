// core.js — общие примитивы, состояние, редактор, нав-хаб (issue #426).
import {
  EditorState,
  EditorView, lineNumbers, keymap, placeholder as cmPlaceholder,
  defaultKeymap, history, historyKeymap, indentWithTab,
  syntaxHighlighting, HighlightStyle, indentOnInput,
  python, tags,
} from "/static/vendor/codemirror-bundle@6.mjs";

// issue #426 — реестр ленивых загрузчиков разделов. Каждый feature-модуль
// регистрирует свой хук при импорте; setSection() лишь дёргает его по имени
// раздела. Так core остаётся листом графа импортов (не импортирует фичи), а
// новый раздел подключается одним registerSectionHook в своём модуле.
const _sectionHooks = {};
function registerSectionHook(name, fn) {
  _sectionHooks[name] = fn;
}

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
  // issue #426: ленивая загрузка раздела — через реестр хуков (каждый feature-
  // модуль регистрирует свой при импорте), чтобы core не импортировал фичи.
  const hook = _sectionHooks[section];
  if (hook) hook();
}

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

// issue #426 — раздел «Настройки» (синхронизация контролов) — core-резидент.
registerSectionHook("settings", () => syncSettingsControls());

export {
  $,
  SECTIONS,
  applyTheme,
  codeBlock,
  cycleTheme,
  esc,
  fetchCodeTerms,
  getSelectedCase,
  kpiGrid,
  makeEditor,
  registerSectionHook,
  renderTermsInto,
  setSection,
  setTheme,
  skeletonBlock,
  state,
};
