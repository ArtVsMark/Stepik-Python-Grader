// content.js — глоссарий, правила (PEP), «Подучить» (hash-навигация, #426).
import {
  $,
  clearLoadError,
  esc,
  fetchJsonOrThrow,
  getSelectedCase,
  kpiGrid,
  registerSectionHook,
  renderLoadError,
  setSection,
  state,
  t,
  tOr,
  tp,
} from "./core.js";

function openGlossaryForSelectedCase() {
  const c = getSelectedCase();
  let id = null;
  if (c && c.glossary_ids && c.glossary_ids.length) {
    id = c.glossary_ids[0];
  } else if (c && c.glossary && c.glossary.anchor) {
    // Fallback: glossary_ids сервер отдаёт только при RE (viewmodels), а якорь
    // карточки есть у любой подсказки. Раньше id выковыривался regex'ом из
    // внешнего URL витрины — того поля больше нет (issue #684).
    id = c.glossary.anchor;
  }
  setSection("glossary");
  if (id) {
    selectGlossaryCard(id);
    _noteGlossaryHit(id, c);
  }
}

// issue #1220: отметить, что в карточку пришли ИЗ ОШИБКИ. Пишется только этот
// переход, а не любое открытие раздела: измеряем связку «упал → понял», а не
// листание справочника. Отправка — «выстрелил и забыл»: отметка не должна ни
// задерживать показ карточки, ни ломать его, если история выключена.
function _noteGlossaryHit(cardId, c) {
  const payload = { card_id: cardId };
  if (c && c.failure_kind) payload.failure_kind = c.failure_kind;
  if (c && c.glossary && c.glossary.exception) payload.error_class = c.glossary.exception;
  fetch("/api/glossary/hit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

// -- Result-panel tabs (Таблица / Разбор) -------------------------------------

// issue #685: навигация по разделам — раскрываемые семейства. Порядок кнопок —
// по частоте использования (модули и типы данных ученик открывает чаще всего);
// сам состав семейств и принадлежность раздела считает сервер
// (`glossary_adapter._card_group`) и присылает полем `group` каждой карточки,
// поэтому правило классификации здесь НЕ повторяется — только порядок показа.
// «other» («Прочее») страхует раздел, который забыли классифицировать: кнопка
// появляется, лишь если такие карточки реально есть.
// id — серверное значение грани ?group= (не переводится), labelKey — ключ
// каталога (перевод при рендере через t(), иначе язык застыл бы на импорте).
const GLOSSARY_GROUPS = [
  { id: "modules", labelKey: "glossary.group_modules" }, // i18n-exempt: серверный фильтр
  { id: "types", labelKey: "glossary.group_types" }, // i18n-exempt: серверный фильтр
  { id: "syntax", labelKey: "glossary.group_syntax" }, // i18n-exempt: серверный фильтр
  { id: "builtins", labelKey: "glossary.group_builtins" }, // i18n-exempt: серверный фильтр
  { id: "io", labelKey: "glossary.group_io" }, // i18n-exempt: серверный фильтр
  { id: "algorithms", labelKey: "glossary.group_algorithms" }, // i18n-exempt: серверный фильтр
  { id: "other", labelKey: "glossary.group_other" }, // i18n-exempt: серверный фильтр
];

async function loadGlossary() {
  const g = state.glossary;
  const params = new URLSearchParams();
  if (g.query) params.set("q", g.query);
  if (g.section) params.set("section", g.section);
  if (g.kind) params.set("kind", g.kind);
  if (g.status) params.set("status", g.status);
  if (g.sort) params.set("sort", g.sort);
  if (g.group) params.set("group", g.group);
  params.set("lang", state.lang); // issue #363: язык summary/body карточек
  try {
    g.cards = await fetchJsonOrThrow("/api/glossary?" + params);
    g.failed = false;
  } catch (e) {
    g.cards = [];
    g.failed = true;  // issue #806: сбой ≠ «Ничего не найдено»
  }
  // Карта разделов/семейств и общий счётчик — из первой невыбранной загрузки.
  if (!g.sections.length && !g.query && !g.section && !g.kind && !g.group) {
    indexGlossarySections(g.cards);
    await probeGlossaryDrafts();
  } else {
    indexGlossarySectionLabels(g.cards); // подписи следуют за ?lang= каждой выдачи
  }
  // Контролы перерисовываются на КАЖДОЙ загрузке: смена языка (issue #363
  // сбрасывает кеш карточек и перезагружает раздел) обязана перевести и подписи
  // семейств — при активном семействе ветка выше не выполняется. Сами кнопки
  // пересоздаются только при смене языка/состава, иначе обновляется лишь их
  // состояние — иначе клик уносил бы фокус с только что нажатой кнопки.
  renderGlossaryGroups();
  renderGlossaryFilterPill();
  renderGlossaryGroupSections();
  renderGlossaryList();
  renderGlossaryCount();
  updateGlossarySidebarBadge();
}

function indexGlossarySections(cards) {
  const g = state.glossary;
  g.total = cards.length;
  g.sections = [...new Set(cards.map(c => c.section).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, "ru")
  );
  g.sectionGroups = {};
  g.sectionCounts = {};
  g.groupCounts = {};
  cards.forEach(c => {
    const group = c.group || "other"; // i18n-exempt: серверное значение фильтра
    g.groupCounts[group] = (g.groupCounts[group] || 0) + 1;
    if (c.section) {
      g.sectionGroups[c.section] = group;
      g.sectionCounts[c.section] = (g.sectionCounts[c.section] || 0) + 1;
    }
  });
  indexGlossarySectionLabels(cards);
}

// issue #685: подписи разделов приходят с сервера (`section_label`, локаль
// ?lang=) — имя раздела остаётся серверным значением фильтра. Карта обновляется
// на КАЖДОЙ загрузке, а не только на первой: после смены языка при раскрытом
// семействе приходят карточки только этого семейства, и его разделы обязаны
// перевестись сразу.
function indexGlossarySectionLabels(cards) {
  const g = state.glossary;
  cards.forEach(c => {
    if (c.section) g.sectionLabels[c.section] = c.section_label || c.section;
  });
}

function glossarySectionLabel(section) {
  return state.glossary.sectionLabels[section] || section;
}

// issue #685: селект «Статус» — контрол не для ученика: в комплектной базе
// черновиков нет, «Черновики» дало бы гарантированно пустой список. Один
// дешёвый probe (?status=draft вернёт пустой массив) решает, показывать ли его
// вообще — на своём store с автодрафтами фильтр остаётся доступен.
async function probeGlossaryDrafts() {
  try {
    const r = await fetch("/api/glossary?status=draft");
    state.glossary.hasDrafts = (await r.json()).length > 0;
  } catch (e) {
    state.glossary.hasDrafts = false;
  }
  const sel = $("#glossary-status");
  if (sel) sel.hidden = !state.glossary.hasDrafts;
}

let glossaryGroupsKey = null; // «состав кнопок + язык», под который отрисован ряд

function renderGlossaryGroups() {
  const el = $("#glossary-groups");
  if (!el) return;
  const g = state.glossary;
  const groups = GLOSSARY_GROUPS.filter(gr => g.groupCounts[gr.id]);
  const key = groups.map(gr => gr.id + ":" + g.groupCounts[gr.id]).join(",") + "|" + state.lang;
  if (key !== glossaryGroupsKey) {
    el.innerHTML = groups
      .map(
        gr =>
          '<button type="button" class="chip chip-group" data-glgroup="' + esc(gr.id) +
          '" title="' + esc(t(gr.labelKey)) + '"><span class="chip-label">' +
          esc(t(gr.labelKey)) +
          '</span><span class="chip-count">' + g.groupCounts[gr.id] + "</span></button>"
      )
      .join("");
    el.querySelectorAll("button[data-glgroup]").forEach(b =>
      b.addEventListener("click", () => toggleGlossaryGroup(b.dataset.glgroup))
    );
    glossaryGroupsKey = key;
  }
  el.querySelectorAll("button[data-glgroup]").forEach(b => {
    const chosen = g.group === b.dataset.glgroup;
    b.classList.toggle("active", chosen);
    b.setAttribute("aria-expanded", String(chosen && g.expanded));
  });
}

// «Пилюля» текущего фильтра: семейство и (если выбран) раздел — то, что раньше
// было видно лишь по подсветке чипа в раскрытой панели. Панель после выбора
// раздела сворачивается, поэтому выбор должен оставаться на виду; крестик
// снимает фильтр целиком (клик по самому семейству лишь сворачивает панель и
// выбор больше НЕ теряет).
function renderGlossaryFilterPill() {
  const el = $("#glossary-filter-pill");
  if (!el) return;
  const g = state.glossary;
  if (!g.group) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const groupMeta = GLOSSARY_GROUPS.find(gr => gr.id === g.group);
  const parts = [groupMeta ? t(groupMeta.labelKey) : g.group];
  if (g.section) parts.push(glossarySectionLabel(g.section));
  el.hidden = false;
  el.innerHTML =
    '<span class="glossary-pill"><span class="chip-label">' +
    esc(parts.join(" › ")) +
    '</span><button type="button" class="glossary-pill-clear" aria-label="' +
    esc(t("glossary.filter_clear")) + '" title="' + esc(t("glossary.filter_clear")) +
    '">✕</button></span>';
  el.querySelector(".glossary-pill-clear").addEventListener("click", clearGlossaryFilter);
}

// Разделы раскрытого семейства: «Все» + сами разделы со счётчиками. Свёрнутое
// семейство панель не рисует — под рукой остаётся только то, что выбрано.
// Панель пересобирается на каждый рендер (в отличие от ряда семейств): подписи
// разделов приходят с сервера и обновляются уже ПОСЛЕ первого рендера при смене
// языка — кеш по ключу «семейство+язык» законсервировал бы русские подписи в
// англоязычном UI. Фокус при пересборке восстанавливается по data-section,
// поэтому клавиатурная навигация не сбрасывается на начало.
function renderGlossaryGroupSections() {
  const el = $("#glossary-group-sections");
  if (!el) return;
  const g = state.glossary;
  if (!g.group || !g.expanded) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const focused = document.activeElement;
  const focusedSection =
    focused && el.contains(focused) ? focused.dataset.section : null;
  const sections = g.sections.filter(s => g.sectionGroups[s] === g.group);
  el.innerHTML = [
    '<button type="button" class="chip' + (g.section ? "" : " active") + '" data-section="">' +
      '<span class="chip-label">' + esc(t("glossary.section_all_short")) + "</span></button>",
  ]
    .concat(
      sections.map(
        s =>
          '<button type="button" class="chip' + (g.section === s ? " active" : "") +
          '" data-section="' + esc(s) + '" title="' + esc(glossarySectionLabel(s)) +
          '"><span class="chip-label">' + esc(glossarySectionLabel(s)) +
          '</span><span class="chip-count">' + (g.sectionCounts[s] || 0) + "</span></button>"
      )
    )
    .join("");
  el.querySelectorAll("button[data-section]").forEach(b =>
    b.addEventListener("click", () => selectGlossarySection(b.dataset.section))
  );
  el.hidden = false;
  if (focusedSection !== null) {
    const restored = el.querySelector(
      'button[data-section="' + CSS.escape(focusedSection) + '"]'
    );
    if (restored) restored.focus();
  }
}

function renderGlossaryCount() {
  const el = $("#glossary-count");
  if (!el) return;
  const g = state.glossary;
  el.textContent = g.total ? t("glossary.count_shown", { shown: g.cards.length, total: g.total }) : "";
}

// Выбор раздела СВОРАЧИВАЕТ панель: список из 30 модулей не должен занимать
// экран после того, как выбор сделан. Что именно выбрано, видно по «пилюле»
// (renderGlossaryFilterPill), а вернуться к списку — клик по семейству.
// Пустая строка — «Все» (фильтр = всё семейство).
function selectGlossarySection(section) {
  const g = state.glossary;
  g.section = g.section === section ? "" : section;
  g.expanded = false;
  renderGlossaryGroups();
  renderGlossaryFilterPill();
  renderGlossaryGroupSections();
  loadGlossary();
}

// issue #685: клик по семейству выбирает его (фильтр = всё семейство) и
// раскрывает разделы; повторный клик по УЖЕ выбранному только сворачивает
// панель, сохраняя выбранный раздел, — раскрытие отделено от фильтра. Снимает
// фильтр крестик «пилюли» (clearGlossaryFilter), а не сворачивание. Раскрыто
// одно семейство за раз; раздел при смене семейства сбрасывается, иначе
// пересечение фильтров дало бы пустую выдачу.
function toggleGlossaryGroup(group) {
  const g = state.glossary;
  if (g.group === group) {
    g.expanded = !g.expanded;
  } else {
    g.group = group;
    g.section = "";
    g.expanded = true;
  }
  renderGlossaryGroups();
  renderGlossaryFilterPill();
  renderGlossaryGroupSections();
  loadGlossary();
}

function clearGlossaryFilter() {
  const g = state.glossary;
  g.group = "";
  g.section = "";
  g.expanded = false;
  renderGlossaryGroups();
  renderGlossaryFilterPill();
  renderGlossaryGroupSections();
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
  clearLoadError(el);
  if (state.glossary.failed) {
    el.innerHTML = ""; // issue #806: сбой ≠ «ничего не найдено»
    renderLoadError(el, loadGlossary);
    return;
  }
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
  // issue #685: раздел в мете — локализованная подпись сервера (section_label),
  // с откатом на карту списка и само имя, если карточка пришла без неё.
  const sectionLabel = card.section
    ? card.section_label || glossarySectionLabel(card.section)
    : "";
  const meta = [card.kind, sectionLabel, card.subcat].filter(Boolean).map(esc).join(" · ");
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
  // issue #684: связи карточки. Ссылки — обычные #/glossary/<id>: hash-роутер
  // (app.js) их уже открывает, поэтому обработчики не нужны, а «открыть в новой
  // вкладке» работает само. Подпись берём из загруженного списка (там человеческий
  // title), с откатом на id, если карточка вне текущей выборки.
  const related = glossaryLinkList(t("glossary.related"), card.related, id => id);
  // related_errors хранит ИМЕНА исключений ("ValueError"), а id карточки — в
  // нижнем регистре ("valueerror"); у модульных (sqlite3.DataError) id совпадает
  // с именем, поэтому пробуем имя как есть, затем lower-case.
  const relatedErrors = glossaryLinkList(
    t("glossary.related_errors"), card.related_errors, name => name.toLowerCase()
  );
  // Единственная внешняя ссылка карточки — официальная docs.python.org.
  // Ссылки на внешний Glossary-Python здесь нет (issue #684): он копия этой
  // базы, и переход туда уводил бы студента на устаревшую версию карточки.
  const links = [
    card.docs_url
      ? '<a href="' + esc(card.docs_url) + '" target="_blank" rel="noopener">' + esc(t("glossary.docs_python")) + "</a>"
      : "",
  ].filter(Boolean).map(a => "<p>" + a + "</p>").join("");
  el.innerHTML =
    "<h2>" + esc(card.title) + draftBadge + verBadge + "</h2>" +
    '<div class="hint">' + meta + "</div>" +
    (card.summary ? "<p>" + esc(card.summary) + "</p>" : "") +
    (card.body ? "<div>" + esc(card.body) + "</div>" : "") +
    syntax +
    examples +
    related +
    relatedErrors +
    links;
}

// Блок связей карточки: подпись + ссылки на другие карточки через "·".
// toId переводит элемент списка в id карточки (для related — он и есть id,
// для related_errors — имя исключения приводится к нижнему регистру).
function glossaryLinkList(label, items, toId) {
  if (!items || !items.length) return "";
  const known = new Map((state.glossary.cards || []).map(c => [c.id, c.title]));
  const links = items
    .map(item => {
      const id = toId(item);
      const text = known.get(id) || item;
      return '<a href="#/glossary/' + encodeURIComponent(id) + '">' + esc(text) + "</a>";
    })
    .join(" · ");
  return (
    '<div class="glossary-links"><div class="form-label">' +
    esc(label) +
    "</div><p>" +
    links +
    "</p></div>"
  );
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
    r.cards = await fetchJsonOrThrow("/api/rules?" + params);
    r.failed = false;
  } catch (e) {
    r.cards = [];
    r.failed = true;  // issue #806: не выдаём сбой за «ничего не найдено»
  }
  renderRulesChips();
  renderRulesList();
  const cnt = $("#rules-count");
  if (cnt) cnt.textContent = tp(r.cards.length, "rules.n_rules");
}

function renderRulesChips() {
  const el = $("#rules-chips");
  if (!el) return;
  // issue #806: параметр `tag`, а не `t` — не затеняем функцию перевода.
  // Здесь она пока не вызывается, но первая же будущая строка с t() упала бы
  // ровно так же, как это случилось в renderTermCards.
  el.innerHTML = RULE_TAGS.map(tag => {
    const active = state.rules.tag === tag ? " active" : "";
    return (
      '<button type="button" class="chip' + active + '" data-tag="' + esc(tag) + '">' +
      esc(tag) +
      "</button>"
    );
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
  clearLoadError(el);
  if (state.rules.failed) {
    el.innerHTML = ""; // issue #806
    renderLoadError(el, loadRules);
    return;
  }
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

// issue #822 (COMM-02): что означает тип падения и что с ним делать. Раньше
// карточка печатала сырой ключ («output-format», «wrong-answer»), и раздел судил,
// но не учил: виден английский жаргон, не видно, что чинить. Ключи RE
// («runtime-error:IndexError») сюда не попадают — у них уже есть glossary_id и
// имя исключения само по себе информативно; lint-коды ведут в «Правила».
// glossary — карточка, в которую ведёт ссылка, когда своей у типа падения нет.
const FAILURE_KIND = {
  "output-format": { title: "insights.kind_output_format", hint: "insights.kind_output_format_hint", glossary: "str.strip" },
  "wrong-answer": { title: "insights.kind_wrong_answer", hint: "insights.kind_wrong_answer_hint" },
  "timeout": { title: "insights.kind_timeout", hint: "insights.kind_timeout_hint", glossary: "range" },
  "slow": { title: "insights.kind_slow", hint: "insights.kind_slow_hint" },
  "runtime-error": { title: "insights.kind_runtime_error", hint: "insights.kind_runtime_error_hint" },
};

async function loadInsights() {
  // issue #806: сбой отличается от «данных пока нет». Раньше любая ошибка
  // превращалась в пустой список, и раздел рапортовал «Пока пусто — и это
  // отлично 🎉» на упавшем сервере.
  try {
    state.insights.cards = await fetchJsonOrThrow("/api/insights");
    state.insights.failed = false;
  } catch (e) {
    state.insights.cards = [];
    state.insights.failed = true;
  }
  renderInsights();
  updateInsightsBadge();
}

function renderInsights() {
  const empty = $("#insights-empty");
  const list = $("#insights-cards");
  if (!list) return;
  const cards = state.insights.cards;
  clearLoadError(list);
  if (state.insights.failed) {
    // issue #806: «Пока пусто — и это отлично 🎉» на упавшем сервере читалось
    // как «ошибок у тебя нет», хотя данных просто не дошло.
    empty.hidden = true;
    list.hidden = true;
    renderLoadError(list, loadInsights);
    return;
  }
  empty.hidden = cards.length > 0;
  list.hidden = cards.length === 0;
  list.innerHTML = cards
    .map(c => {
      const st = INSIGHT_STATUS[c.status] || { icon: "•", label: c.status };
      const kind = FAILURE_KIND[c.key];
      // Понятное название вместо жаргонного ключа; сам ключ остаётся в title —
      // он стабильный идентификатор и по нему ищут в доках (issue #822).
      const label = kind
        ? '<span class="insight-key" title="' + esc(c.key) + '">' + esc(t(kind.title)) + "</span>"
        : '<span class="insight-key">' + esc(c.key) + "</span>";
      const advice = kind ? '<div class="hint insight-advice">' + esc(t(kind.hint)) + "</div>" : "";
      const target = c.glossary_id || (kind && kind.glossary) || "";
      const ref = target
        ? ' · <a href="#/glossary/' + esc(target) + '">' + esc(t("insights.link_glossary")) + "</a>"
        : c.category === "lint"
          ? ' · <a href="#/rules/' + esc(c.key) + '">' + esc(t("insights.link_rule")) + "</a>"
          : "";
      return (
        '<li class="insight-card insight-' + esc(c.status) + '">' +
        '<div class="insight-head"><span class="insight-status">' + st.icon + " " + esc(t(st.label)) +
        "</span> " + label + "</div>" +
        advice +
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
  // issue #806: см. loadInsights — «Пока пусто 📊» на сбое вводило в заблуждение.
  try {
    state.progress.report = await fetchJsonOrThrow("/api/progress");
    state.progress.failed = false;
  } catch (e) {
    state.progress.report = null;
    state.progress.failed = true;
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

// Подпись задачи в таблице (зеркалит progress_export._task_label): записи,
// сделанные до нормализации ключа, хранят "." — точка не название задачи.
function taskLabel(key) {
  return key && key !== "." ? key : t("progress.no_task");
}

function renderProgress() {
  const empty = $("#progress-empty");
  const content = $("#progress-content");
  if (!content) return;
  const rep = state.progress.report;
  clearLoadError(content);
  if (state.progress.failed) {
    // issue #806: «Пока пусто 📊» вместо честного «не загрузилось».
    empty.hidden = true;
    content.hidden = true;
    renderLoadError(content, loadProgress);
    return;
  }
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
          // issue #821: подпись берётся из каталога по id бейджа — сервер
          // присылает её только как запасной вариант, иначе в EN-интерфейсе
          // чипы оставались русскими рядом с локализованными KPI.
          b =>
            '<span class="chip' + (b.earned ? "" : " chip-locked") + '">' +
            (b.earned ? "🏅 " : "🔒 ") +
            esc(tOr("progress.badge_" + b.id, b.label || b.id)) + "</span>",
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
            "<tr><td>" + esc(taskLabel(t.task_key)) + "</td><td>" + esc(t.attempts) + "</td><td>" +
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
  selectGlossaryCard,
  selectRuleCard,
  setGlossaryView,
};
