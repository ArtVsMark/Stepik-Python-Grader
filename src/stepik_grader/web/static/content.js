// content.js — глоссарий, правила (PEP), «Подучить» (hash-навигация, #426).
import { $, esc, getSelectedCase, registerSectionHook, setSection, state, t, tp } from "./core.js";

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

// issue #546: label — КЛЮЧ каталога (перевод при рендере через t()), а не
// готовая строка (иначе язык зафиксировался бы на импорте). Поле section —
// значение фильтра, сопоставляется с именами разделов сервера: НЕ переводить.
const GLOSSARY_CHIPS = [
  { labelKey: "glossary.chip_str", section: "Строки (str)" },
  { labelKey: "glossary.chip_list", section: "Списки (list)" },
  { labelKey: "glossary.chip_tuple", section: "Кортежи (tuple)" },
  { labelKey: "glossary.chip_dict", section: "Словари (dict)" },
  { labelKey: "glossary.chip_set", section: "Множества (set)" },
  { labelKey: "glossary.chip_builtins", section: "Встроенные функции" },
  { labelKey: "glossary.chip_exceptions", section: "Исключения" },
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
        esc(ch.section) + '">' + esc(t(ch.labelKey)) + "</button>"
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
  sel.innerHTML = ['<option value="">' + esc(t("glossary.section_all")) + "</option>"]
    .concat(state.glossary.sections.map(s => '<option value="' + esc(s) + '">' + esc(s) + "</option>"))
    .join("");
  sel.value = state.glossary.section;
}

function renderGlossaryCount() {
  const el = $("#glossary-count");
  if (!el) return;
  const g = state.glossary;
  el.textContent = g.total ? t("glossary.count_shown", { shown: g.cards.length, total: g.total }) : "";
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
    el.innerHTML = '<li class="empty">' + esc(t("common.nothing_found")) + "</li>";
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
    el.innerHTML = '<li class="empty">' + esc(t("glossary.missing_empty")) + "</li>";
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

function renderGlossaryDetail(card) {
  $("#glossary-empty").hidden = true;
  const el = $("#glossary-detail-content");
  el.hidden = false;
  const meta = [card.kind, card.section, card.subcat].filter(Boolean).map(esc).join(" · ");
  const draftBadge =
    card.status === "draft"
      ? ' <span class="badge badge-warning">' + esc(t("glossary.draft_badge")) + "</span>" // issue #328
      : "";
  const verBadge = card.version
    ? ' <span class="badge badge-neutral">Python ' + esc(card.version) + "</span>"
    : "";
  const syntax = card.syntax
    ? '<div class="form-label">' + esc(t("glossary.syntax")) + '</div><pre class="code-block">' + esc(card.syntax) + "</pre>"
    : "";
  const examples = card.examples && card.examples.length
    ? '<div class="form-label">' + esc(t("glossary.examples")) + "</div>" +
      card.examples.map(ex => '<pre class="code-block">' + esc(ex) + "</pre>").join("")
    : "";
  const links = [
    card.docs_url
      ? '<a href="' + esc(card.docs_url) + '" target="_blank" rel="noopener">' + esc(t("glossary.docs_python")) + "</a>"
      : "",
    card.url
      ? '<a href="' + esc(card.url) + '" target="_blank" rel="noopener">' + esc(t("glossary.external_link")) + "</a>"
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
  if (cnt) cnt.textContent = tp(r.cards.length, "rules.n_rules");
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
    el.innerHTML = '<li class="empty">' + esc(t("common.nothing_found")) + "</li>";
    return;
  }
  el.innerHTML = state.rules.cards
    .map(c => {
      const sel = c.id === state.rules.selectedId ? " selected" : "";
      // issue #403: подсветить правила, которые пользователь нарушал лично.
      const violated = c.violated ? " violated" : "";
      const badge = c.violated
        ? ' <span class="rule-violated" title="' + esc(t("rules.violated_title")) + '">⚠</span>'
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
    ? '<div class="form-label">' + esc(t("rules.example_bad")) + '</div><pre class="code-block code-bad">' + esc(card.example_bad) + "</pre>"
    : "";
  const good = card.example_good
    ? '<div class="form-label">' + esc(t("rules.example_good")) + '</div><pre class="code-block code-good">' + esc(card.example_good) + "</pre>"
    : "";
  const links = [
    card.pep_url ? '<a href="' + esc(card.pep_url) + '" target="_blank" rel="noopener">' + esc(t("rules.pep_link")) + "</a>" : "",
    card.docs_url ? '<a href="' + esc(card.docs_url) + '" target="_blank" rel="noopener">' + esc(t("rules.docs_link")) + "</a>" : "",
  ].filter(Boolean).map(a => "<p>" + a + "</p>").join("");
  el.innerHTML =
    '<h2><span class="rule-code">' + esc(card.id) + "</span> " + esc(card.title) + sev + "</h2>" +
    (card.summary ? "<p>" + esc(card.summary) + "</p>" : "") +
    (card.body ? '<div class="rule-body">' + esc(card.body) + "</div>" : "") +
    bad + good + links;
}

// -- Раздел «Подучить» (issue #348) -------------------------------------------

// issue #546: label — КЛЮЧ каталога (перевод при рендере через t()), иконки —
// как есть. Fallback для неизвестного статуса задаётся в renderInsights.
const INSIGHT_STATUS = {
  active: { icon: "🔥", label: "insights.status_active" },
  fading: { icon: "🌤", label: "insights.status_fading" },
  watch: { icon: "👀", label: "insights.status_watch" },
  archived: { icon: "✅", label: "insights.status_archived" },
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
        ? ' · <a href="#/glossary/' + esc(c.glossary_id) + '">' + esc(t("insights.link_glossary")) + "</a>"
        : c.category === "lint"
          ? ' · <a href="#/rules/' + esc(c.key) + '">' + esc(t("insights.link_rule")) + "</a>"
          : "";
      return (
        '<li class="insight-card insight-' + esc(c.status) + '">' +
        '<div class="insight-head"><span class="insight-status">' + st.icon + " " + esc(t(st.label)) +
        '</span> <span class="insight-key">' + esc(c.key) + "</span></div>" +
        '<div class="hint">' + tp(c.runs_considered, "insights.seen_in", { hits: c.hits }) + ref + "</div>" +
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

// -- Прогресс: агрегатный отчёт из истории (issue #538) -----------------------

async function loadProgress() {
  try {
    const r = await fetch("/api/progress");
    state.progress.report = await r.json();
  } catch (e) {
    state.progress.report = null;
  }
  renderProgress();
}

// Человекочитаемое время до первого AC (зеркалит progress_export._fmt_secs).
function fmtSecs(secs) {
  if (secs == null) return "—";
  if (secs < 90) return Math.round(secs) + t("progress.unit_sec");
  if (secs < 5400) return Math.round(secs / 60) + t("progress.unit_min");
  return (secs / 3600).toFixed(1) + t("progress.unit_hour");
}

function renderProgress() {
  const empty = $("#progress-empty");
  const content = $("#progress-content");
  if (!content) return;
  const rep = state.progress.report;
  // Пустая/отсутствующая история → build_progress_report даёт total_runs=0.
  const isEmpty = !rep || !rep.total_runs;
  empty.hidden = !isEmpty;
  content.hidden = isEmpty;
  if (isEmpty) return;

  const verdicts = rep.verdicts || {};
  const totalCases = Object.values(verdicts).reduce((a, b) => a + b, 0);
  $("#progress-kpis").innerHTML = kpiGrid([
    { label: t("progress.kpi_solved"), value: rep.solved_tasks + " / " + rep.total_tasks },
    { label: t("progress.kpi_streak"), value: rep.streak || 0 },
    { label: t("progress.kpi_runs"), value: rep.total_runs },
    { label: t("progress.kpi_ac_cases"), value: (verdicts.AC || 0) + (totalCases ? " / " + totalCases : "") },
  ]);

  // issue #540: видимые достижения — заслуженные бейджи ярче, ещё не взятые приглушены.
  const badges = rep.badges || [];
  $("#progress-badges").innerHTML = badges.length
    ? '<h2 class="section-heading">' + esc(t("progress.achievements")) + '</h2><div class="chip-row">' +
      badges
        .map(
          b =>
            '<span class="chip' + (b.earned ? "" : " chip-locked") + '">' +
            (b.earned ? "🏅 " : "🔒 ") + esc(b.label) + "</span>",
        )
        .join("") +
      "</div>"
    : "";

  const vItems = Object.entries(verdicts);
  $("#progress-verdicts").innerHTML = vItems.length
    ? '<h2 class="section-heading">' + esc(t("progress.verdicts")) + '</h2><div class="chip-row">' +
      vItems.map(([k, n]) => '<span class="chip">' + esc(k) + ": " + esc(n) + "</span>").join("") +
      "</div>"
    : "";

  const tasks = rep.tasks || [];
  $("#progress-tasks").innerHTML = tasks.length
    ? '<h2 class="section-heading">' + esc(t("progress.tasks_heading")) + '</h2><table class="data-table"><thead><tr>' +
      "<th>" + esc(t("progress.col_task")) + "</th><th>" + esc(t("progress.col_attempts")) +
      "</th><th>" + esc(t("progress.col_solved")) + "</th><th>" + esc(t("progress.col_time_to_ac")) +
      "</th></tr></thead><tbody>" +
      tasks
        .map(
          t =>
            "<tr><td>" + esc(t.task_key) + "</td><td>" + esc(t.attempts) + "</td><td>" +
            (t.solved ? "✓" : "—") + "</td><td>" + esc(fmtSecs(t.seconds_to_first_ac)) + "</td></tr>",
        )
        .join("") +
      "</tbody></table>"
    : "";
}

// -- Загрузчик задач: скачивание со Stepik (issue #186) -----------------------


// issue #426 — регистрация разделов «Глоссарий»/«Правила»/«Подучить».
registerSectionHook("glossary", () => {
  if (!state.glossary.cards.length) loadGlossary();
});
registerSectionHook("rules", () => {
  if (!state.rules.cards.length) loadRules();
});
registerSectionHook("insights", () => loadInsights());
registerSectionHook("progress", () => loadProgress());

export {
  loadGlossary,
  loadRules,
  openGlossaryForSelectedCase,
  parseGlossaryHash,
  renderGlossaryChips,
  selectGlossaryCard,
  selectRuleCard,
  setGlossaryView,
};
