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
// контексте (innerHTML), но и внутри HTML-атрибутов (карточка глоссария
// вставляет esc(card.docs_url) в href="..."); без \"/' значение могло бы
// разорвать атрибут.
const HT = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c => HT[c]);

// ---------------------------------------------------------------------------
// Единый реестр разделов (issue #428): порядок = порядок Ctrl-переключения
// команды «Переключить раздел». И setSection() (показ/скрытие #view-*), и
// switch_section() ходят по нему — новый раздел добавляется один раз здесь и
// забыть его нельзя (устраняет рецидив #317: switch_section циклил 4 из 7).
// ---------------------------------------------------------------------------
const SECTIONS = ["check", "downloader", "glossary", "rules", "insights", "progress", "sandbox", "settings"];

// State (issue #125) — единый источник состояния для split-pane workspace,
// action cards и сценарных кнопок (палитра удалена — issue #658).
// ---------------------------------------------------------------------------
const state = {
  section: localStorage.getItem("grader_section") || "check", // одно из SECTIONS
  // issue #364 — язык сообщений сервера (?lang=); issue #1131 — стартовый
  // язык от сервера (--serve --lang) вместо жёсткого "ru": флаг был
  // единственным явным выбором языка, и страница его игнорировала.
  // localStorage сильнее: переключатель в шапке — более поздний выбор.
  lang: localStorage.getItem("grader_lang") || document.body.dataset.startLang || "ru",
  mode: localStorage.getItem("grader_mode") || "tests", // "file" | "tests" | "bench" | "microbench"
  resultTab: "table", // "table" | "detail"
  lastResult: null,
  selectedRow: null,
  selectedCase: null,
  explainOpen: false,
  commands: [], // fetched once from /api/commands
  theme: localStorage.getItem("grader_theme") || "system", // "system" | "light" | "dark"
  // issue #685: group — выбранное семейство разделов ("modules"/"types"/…/""),
  // expanded — раскрыта ли под ним панель разделов (раскрытие отделено от
  // фильтра: выбор раздела сворачивает панель, но фильтр остаётся);
  // sectionGroups — карта «раздел → семейство», sectionCounts/groupCounts —
  // счётчики для подписей, sectionLabels — подписи разделов по ?lang= (всё
  // считает сервер); hasDrafts — есть ли в источнике не-ready карточки (иначе
  // селект «Статус» не показывается); sort по умолчанию — по релевантности.
  glossary: {
    query: "", section: "", kind: "", status: "", sort: "relevance", group: "",
    expanded: false,
    cards: [], missing: [], sections: [], sectionGroups: {}, sectionCounts: {},
    sectionLabels: {}, groupCounts: {}, hasDrafts: false, total: 0,
    selectedId: null, view: "cards",
  },
  rules: { query: "", tag: "", cards: [], selectedId: null }, // issue #348
  insights: { cards: [] }, // issue #348
  progress: { report: null }, // issue #538 — агрегатный отчёт из /api/progress
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

// issue #726: ANSI-escape в выводе решения. Причину лечит рантайм
// (PYTHON_COLORS=0 в core/runner.py), но stderr может прийти и из другой среды
// (чужой раннер, сохранённая история) — в DOM цветовые коды выглядят мусором
// «▮[35m», поэтому вырезаем их на входе в разметку.
// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
const stripAnsi = s => (s ?? "").toString().replace(ANSI_RE, "");

/**
 * Короткая суть многострочной ошибки — для узкой ячейки таблицы (issue #726).
 *
 * У traceback'а содержательная строка последняя («NameError: name 'data' is
 * not defined»); строки кадров начинаются с отступа. Полный текст показывается
 * рядом отдельным блоком, здесь нужна именно одна строка.
 */
function errorSummary(text) {
  const lines = stripAnsi(text)
    .split("\n")
    .map(l => l.trimEnd())
    .filter(l => l.trim() !== "");
  if (!lines.length) return "";
  const meaningful = lines.filter(l => !/^\s/.test(l) && !/^Traceback/.test(l));
  return (meaningful.length ? meaningful[meaningful.length - 1] : lines[lines.length - 1]).trim();
}

function skeletonBlock() {
  return (
    '<div class="pad-4">' +
    '<div class="skeleton skeleton-heading"></div>' +
    '<div class="skeleton skeleton-text"></div>' +
    '<div class="skeleton skeleton-text"></div>' +
    '<div class="skeleton skeleton-text"></div>' +
    "</div>"
  );
}

/**
 * Скелетон + невидимая подпись для скринридера (issue #637).
 *
 * Единый язык ожидания: раньше одни места рисовали скелетон, другие — текст
 * («Поиск…», «Выполняется»). Свели к скелетону, но текст не выбрасываем, а
 * прячем в sr-only: сам по себе скелетон — чистая декорация, и незрячий
 * пользователь остался бы вообще без сигнала, что идёт загрузка.
 *
 * @param {string} label — уже локализованный текст статуса
 */
function skeletonWithLabel(label) {
  return '<span class="sr-only" role="status">' + esc(label) + "</span>" + skeletonBlock();
}

/** Скелетон-заглушка для списков (`<ul>`): три строки-плейсхолдера. */
function skeletonListItems(label) {
  return (
    '<li class="sr-only" role="status">' + esc(label) + "</li>" +
    '<li class="skeleton skeleton-text"></li>'.repeat(3)
  );
}

function emptyState(title, hint) {
  return (
    '<div class="empty-state"><h3>' + esc(title) + "</h3>" +
    (hint ? "<p>" + esc(hint) + "</p>" : "") +
    "</div>"
  );
}

// -- Command registry: единый фильтр под карточки действий (issue #658: вторая
// поверхность, палитра команд, удалена — все её команды и так были кнопками) --

const THEME_STATE_KEYS = {
  system: "topbar.theme_state_system",
  light: "topbar.theme_state_light",
  dark: "topbar.theme_state_dark",
};

function applyTheme() {
  const root = document.documentElement;
  const btn = $("#theme-toggle");
  if (state.theme === "system") {
    root.removeAttribute("data-theme");
    btn.textContent = "🌓";
  } else {
    root.setAttribute("data-theme", state.theme);
    btn.textContent = state.theme === "dark" ? "🌙" : "☀️";
  }
  // issue #659: подпись называет ТЕКУЩИЙ режим. Иконка его и раньше отражала,
  // но «системная» на глаз неотличима от остальных, а скринридер видел лишь
  // статичное «Переключить тему» — состояние было доступно только зрячему и
  // только по догадке. Раньше его показывал селект в «Настройках»; селект
  // убран, поэтому состояние обязано читаться прямо здесь.
  //
  // Ключи перечислены явной картой, а не собираются конкатенацией: guardrail
  // локалей (check_ui_locale_guardrails) разбирает вызовы переводчика
  // статически, и склейка строки с переменной дала бы ему обрубок префикса
  // вместо трёх реальных ключей.
  const label = t(THEME_STATE_KEYS[state.theme]);
  btn.title = label;
  btn.setAttribute("aria-label", label);
}

/** Отразить текущий язык на сегментированном переключателе topbar (#659). */
function syncLangButtons() {
  document.querySelectorAll("#lang-switch .lang-btn").forEach(btn => {
    const active = btn.dataset.lang === state.lang;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });
}

function cycleTheme() {
  state.theme = state.theme === "system" ? "light" : state.theme === "light" ? "dark" : "system";
  localStorage.setItem("grader_theme", state.theme);
  applyTheme();
}

// -- Настройки (issue #364) --------------------------------------------------

function syncSettingsControls() {
  // issue #659: селектов темы и языка здесь больше нет — они переехали в
  // topbar тумблерами. Раздел остался под редкие настройки.
  // issue #565: реальный статус локальной истории (флаг с сервера в <body>) —
  // runtime-тумблера нет, история задаётся флагом старта сервера.
  const hist = $("#history-status");
  if (hist) {
    const on = document.body.dataset.recordHistory === "true";
    hist.textContent = on ? t("settings.history_status_on") : t("settings.history_status_off");
  }
}

// -- Section switch (Проверка решений / Глоссарий) ----------------------------

/**
 * Показать/скрыть блок с коротким входом вместо телепортации (issue #634).
 *
 * `hidden` — это `display: none`, а его нельзя анимировать переходом, поэтому
 * вход оформлен CSS-анимацией `.view-enter`. Класс снимается и ставится заново
 * через принудительный reflow: без этого повторный показ того же блока
 * анимацию не перезапустит (браузер не увидит смены состояния).
 *
 * Анимация запускается только на переходе скрыт → показан, чтобы перерисовка
 * уже видимого блока не дёргалась.
 */
function revealWithMotion(el, visible) {
  if (!el) return;
  const wasHidden = el.hidden;
  el.hidden = !visible;
  if (visible && wasHidden) {
    el.classList.remove("view-enter");
    void el.offsetWidth;
    el.classList.add("view-enter");
  }
}

// issue #804 (FER-03): URL адресует раздел. Пишем ровно тот формат, что уже
// стоит в `href` пунктов сайдбара (`#check`), — второй формат разошёлся бы с
// разметкой. Через `replaceState`, а не `location.hash`: переключение раздела —
// смена вида, а не шаг истории, и лишние записи ломали бы «Назад».
// `window.` обязателен: строкой 5 в модуль импортирован CodeMirror'овский
// `history` (расширение undo/redo) — он затеняет глобальный `History`, и голое
// `history.replaceState` здесь падает с TypeError.
function syncSectionHash(section) {
  const target = "#" + section;
  if (location.hash !== target) window.history.replaceState(null, "", target);
}

// `syncHash: false` — когда хэш адресует точнее (карточка `#/glossary/<id>`)
// или ещё не разобран роутером (старт приложения): раздел из localStorage не
// имеет права затирать прямую ссылку.
function setSection(section, { syncHash = true } = {}) {
  state.section = section;
  localStorage.setItem("grader_section", section);
  if (syncHash) syncSectionHash(section);
  document.querySelectorAll("[data-section]").forEach(a => {
    const active = a.dataset.section === section;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  // issue #428: показать выбранный #view-*, скрыть остальные — по единому
  // реестру SECTIONS (id раздела == суффикс #view-<section>).
  SECTIONS.forEach(s => {
    revealWithMotion($("#view-" + s), section === s);
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
      color: "var(--color-text-muted)", // issue #805: номера строк — текст, 4.5:1
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
      EditorView.contentAttributes.of({ "aria-label": label || t("editor.default_label") }),
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

// issue #806: сетевой сбой должен выглядеть как сбой. Загрузчики разделов
// ловили любую ошибку в пустой список, и «Подучить» рапортовал «Пока пусто — и
// это отлично 🎉» на упавшем сервере: пользователь считал отсутствие данных
// нормой. Здесь — общий фетч, который отличает не-200 от пустых данных, и
// баннер ошибки с кнопкой «Повторить» вместо жизнерадостной пустоты.
async function fetchJsonOrThrow(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

/**
 * Показать «не загрузилось» с кнопкой повтора вместо пустого состояния (#806).
 *
 * Сбой сети и «данных действительно нет» — разные вещи, и рисовать их одинаково
 * нельзя: раздел «Подучить» на упавшем сервере рапортовал «Пока пусто — и это
 * отлично 🎉». Блок вставляется РЯДОМ с якорем, а не внутрь него: разметка
 * пустого состояния (`#insights-empty`, `#progress-empty`) статична и живёт в
 * шаблоне — затерев её innerHTML, вернуть текст после «Повторить» было бы
 * нечем.
 *
 * @param {Element|null} anchor — элемент, после которого встанет блок ошибки
 * @param {Function} [onRetry] — обработчик кнопки «Повторить»
 */
function renderLoadError(anchor, onRetry) {
  if (!anchor || !anchor.parentNode) return;
  clearLoadError(anchor);
  const box = document.createElement("div");
  box.className = "msg";
  box.dataset.loadError = "";
  box.innerHTML =
    esc(t("common.load_failed")) +
    ' <button type="button" class="btn btn-secondary btn-sm" data-retry>' +
    esc(t("common.retry")) +
    "</button>";
  anchor.parentNode.insertBefore(box, anchor.nextSibling);
  const btn = box.querySelector("[data-retry]");
  if (btn && typeof onRetry === "function") btn.addEventListener("click", onRetry);
}

/** Убрать блок ошибки загрузки рядом с ``anchor``, если он там есть (#806). */
function clearLoadError(anchor) {
  if (!anchor || !anchor.parentNode) return;
  anchor.parentNode.querySelectorAll(":scope > [data-load-error]").forEach(el => el.remove());
}

function renderTermCards(terms) {
  // issue #806: параметр называется `term`, а НЕ `t` — иначе он затеняет
  // функцию перевода, и вызов `t("terms.no_card")` ниже падает TypeError
  // («t is not a function») на первом же термине без карточки. Панель
  // «Функции в коде» при этом переставала рисоваться целиком.
  return terms
    .map(term => {
      const kind = term.kind
        ? '<span class="term-card-kind">' + esc(term.kind) + "</span>"
        : "";
      if (!term.has_card) {
        // концепция без карточки — приглушённо, без ссылки (открывать нечего)
        return (
          '<li class="term-card term-card-nocard"><span class="term-card-link">' +
          '<span class="term-card-title">' +
          esc(term.title) +
          "</span>" +
          kind +
          '<span class="term-card-summary hint">' + esc(t("terms.no_card")) + "</span></span></li>"
        );
      }
      const uncertain = term.confidence === "low" ? " term-card-uncertain" : "";
      const summary = term.summary
        ? '<span class="term-card-summary">' + esc(term.summary) + "</span>"
        : "";
      return (
        '<li class="term-card' +
        uncertain +
        '"><a class="term-card-link" href="#/glossary/' +
        encodeURIComponent(term.id) +
        '"><span class="term-card-title">' +
        esc(term.title) +
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

// ---------------------------------------------------------------------------
// issue #545 — i18n статической оболочки. Каталог ui.json (ru/en) применяется к
// узлам index.html, размеченным data-i18n (textContent) и
// data-i18n-placeholder/-title/-aria-label (соответствующие атрибуты). Каталог
// грузится один раз (fetch same-origin — разрешён строгим CSP default-src
// 'self', issue #563) и кешируется в модульной переменной; смена языка идёт из
// кеша, без повторного запроса. Отсутствующий ключ — console.warn + оставляем
// текущий текст (мягкий guardrail; жёсткая проверка полноты — issue #547), без
// тихого RU-fallback как «нормы». Динамические литералы JS-рендеров здесь НЕ
// трогаются — это отдельный issue #546.
// ---------------------------------------------------------------------------
let _uiCatalog = null;

async function _loadUiCatalog() {
  if (_uiCatalog) return _uiCatalog;
  const resp = await fetch("/static/locales/ui.json");
  _uiCatalog = await resp.json();
  return _uiCatalog;
}

// issue #546 — синхронный доступ к каталогу для ДИНАМИЧЕСКИХ строк JS-рендеров
// (то, что data-i18n покрыть не может: результаты грейда, статусы прогона,
// тултипы, empty-state, собираемые в JS). Каталог уже кеширован в _uiCatalog;
// гарантия «загружен до первого t()» — top-level await ниже (core.js импортируют
// все рендер-модули, поэтому их выполнение ждёт загрузку). {name} в строке
// подставляется из params. Отсутствующий ключ → видимый маркер ⟦key⟧ + warn, а
// НЕ тихий RU-fallback (acceptance #546; жёсткая проверка полноты — #547).
function t(key, params) {
  const dict = (_uiCatalog && _uiCatalog[state.lang]) || {};
  if (!Object.hasOwn(dict, key)) {
    console.warn("t: ключ «" + key + "» отсутствует в локали «" + state.lang + "»");
    return "⟦" + key + "⟧"; // ⟦key⟧ — видимый маркер пропущенного перевода
  }
  let str = dict[key];
  if (params) {
    str = str.replace(/\{(\w+)\}/g, (m, name) =>
      Object.hasOwn(params, name) ? String(params[name]) : m
    );
  }
  return str;
}

// issue #821 — подпись по ключу, но с серверным запасным вариантом. Нужна там,
// где набор ключей задаёт СЕРВЕР (бейджи достижений): новый бейдж, которого ещё
// нет в каталоге, должен показать серверную подпись, а не маркер ⟦key⟧. Для
// обычных статических ключей это не годится — там пропуск обязан быть виден.
function tOr(key, fallback) {
  const dict = (_uiCatalog && _uiCatalog[state.lang]) || {};
  return Object.hasOwn(dict, key) ? t(key) : fallback;
}

// issue #546 — выбор формы множественного числа. RU: три формы (one/few/many);
// EN: две (one/many, «few» не используется). Возвращает суффикс каталожного
// ключа: строки лежат под <key>.one/.few/.many в обоих языках (в en .few — копия
// .many, чтобы паритет ключей #547 держался).
function pluralForm(n, lang) {
  if (lang === "en") return n === 1 ? "one" : "many";
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "one";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "few";
  return "many";
}

// issue #546 — плюрализованный перевод: tp(5, "grade.n_solutions") → строка формы
// «many» с уже подставленным {n}. Доп. params сливаются поверх {n}.
function tp(n, key, params) {
  return t(key + "." + pluralForm(n, state.lang), { ...params, n });
}

async function applyUiLocale(lang) {
  let cat;
  try {
    cat = await _loadUiCatalog();
  } catch {
    return; // каталог недоступен — разметка остаётся с RU-fallback из index.html
  }
  const dict = cat[lang] || {};
  const apply = (attr, set) => {
    document.querySelectorAll("[" + attr + "]").forEach(el => {
      const key = el.getAttribute(attr);
      if (Object.hasOwn(dict, key)) set(el, dict[key]);
      else console.warn("applyUiLocale: ключ «" + key + "» отсутствует в локали «" + lang + "»");
    });
  };
  apply("data-i18n", (el, v) => { el.textContent = v; });
  apply("data-i18n-placeholder", (el, v) => { el.placeholder = v; });
  apply("data-i18n-title", (el, v) => { el.setAttribute("title", v); });
  apply("data-i18n-aria-label", (el, v) => { el.setAttribute("aria-label", v); });
  // issue #821: у contenteditable-редакторов нет атрибута placeholder — подсказка
  // рисуется из data-placeholder через CSS ::before, поэтому ей нужен свой
  // компаньон-ключ.
  apply("data-i18n-data-placeholder", (el, v) => { el.setAttribute("data-placeholder", v); });
  document.documentElement.lang = lang;
}

// issue #543 — AI-подсказка по упавшему кейсу (POST /api/v1/hint, async-job).
// Единая точка для grade/playground: consent-гейт → сабмит → опрос → рендер.
// Приватность: код/ввод-вывод уходят AI-провайдеру только после явного
// однократного согласия; согласие помнится (localStorage + server-side).
const _AI_CONSENT_KEY = "grader_ai_consent";

function _aiConsentGranted() {
  return localStorage.getItem(_AI_CONSENT_KEY) === "1";
}

// Модальное окно согласия (index.html #ai-consent-overlay). Promise<boolean>:
// true — пользователь согласился (запоминаем), false — отменил.
function _requestAiConsent() {
  return new Promise(resolve => {
    const overlay = $("#ai-consent-overlay");
    const accept = $("#ai-consent-accept");
    const decline = $("#ai-consent-decline");
    if (!overlay || !accept || !decline) {
      resolve(false);
      return;
    }
    // issue #637: куда вернуть фокус при закрытии. Без этого он улетает в
    // начало документа, и клавиатурный пользователь теряет место в интерфейсе.
    const returnFocus = document.activeElement;

    const done = ok => {
      overlay.hidden = true;
      accept.removeEventListener("click", onAccept);
      decline.removeEventListener("click", onDecline);
      overlay.removeEventListener("keydown", onKeydown);
      if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
      resolve(ok);
    };
    const onAccept = () => {
      localStorage.setItem(_AI_CONSENT_KEY, "1");
      done(true);
    };
    const onDecline = () => done(false);

    // issue #637: focus-trap. Разметка объявляет aria-modal, но сам атрибут
    // ничего не удерживает — это лишь обещание скринридеру. Tab свободно уходил
    // на страницу под оверлеем, где пользователь мог нажимать кнопки, пока
    // модалка «ждёт» ответа. Плюс Escape: диалог с двумя вариантами обязан
    // закрываться отказом, а не только кликом.
    const onKeydown = e => {
      if (e.key === "Escape") {
        e.preventDefault();
        onDecline();
        return;
      }
      if (e.key !== "Tab") return;
      const stops = [accept, decline];
      const edge = e.shiftKey ? stops[0] : stops[stops.length - 1];
      if (document.activeElement === edge) {
        e.preventDefault();
        (e.shiftKey ? stops[stops.length - 1] : stops[0]).focus();
      }
    };

    accept.addEventListener("click", onAccept);
    decline.addEventListener("click", onDecline);
    overlay.addEventListener("keydown", onKeydown);
    overlay.hidden = false;
    accept.focus();
  });
}

// POST /api/v1/hint + опрос /api/v1/runs/<id>. Возвращает {hint, configured},
// {consentRequired: true} либо null (сеть/ошибка/таймаут).
//
// issue #931: 403 больше не сваливается в общую «ошибку». Согласие привязано к
// получателю, и сервер отвечает 403, когда адрес провайдера сменился с того, на
// который согласие давали. Для пользователя это не сбой, а вопрос, который
// нужно задать заново — иначе он видит «не удалось» и не понимает, почему.
async function _submitHint(payload) {
  let runId;
  try {
    const resp = await fetch("/api/v1/hint?lang=" + encodeURIComponent(state.lang), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (resp.status === 403) return { consentRequired: true };
    if (resp.status !== 202) return null;
    runId = (await resp.json()).run_id;
  } catch {
    return null;
  }
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 400));
    let data;
    try {
      const r = await fetch("/api/v1/runs/" + encodeURIComponent(runId));
      data = await r.json();
    } catch {
      continue;
    }
    if (data.status === "done") return data.result || null;
    if (data.status === "error" || data.status === "cancelled") return null;
  }
  return null;
}

// Единый flow «Объяснить (AI)»: рендерит результат/статусы в outEl.
async function explainFailureWithAi(payload, outEl) {
  if (!outEl) return;
  if (!_aiConsentGranted()) {
    const ok = await _requestAiConsent();
    if (!ok) return; // отказ — ничего не отправляем
  }
  outEl.hidden = false;
  outEl.innerHTML = '<p class="msg-neutral">' + esc(t("ai.hint_loading")) + "</p>";
  let res = await _submitHint({ ...payload, consent: true, lang: state.lang });
  // issue #931: получатель сменился — спрашиваем заново, ровно один раз.
  // Локальный флаг сбрасываем: он помнил согласие на ПРЕЖНИЙ адрес и иначе
  // молчал бы при каждом следующем запросе.
  if (res && res.consentRequired) {
    localStorage.removeItem(_AI_CONSENT_KEY);
    const again = await _requestAiConsent();
    if (!again) {
      outEl.hidden = true;
      return;
    }
    res = await _submitHint({ ...payload, consent: true, lang: state.lang });
    if (res && res.consentRequired) res = null;
  }
  if (!res) {
    outEl.innerHTML = '<p class="msg">' + esc(t("ai.hint_error")) + "</p>";
    return;
  }
  if (!res.hint) {
    const key = res.configured ? "ai.hint_empty" : "ai.hint_not_configured";
    outEl.innerHTML = '<p class="msg-neutral">' + esc(t(key)) + "</p>";
    return;
  }
  outEl.innerHTML =
    '<div class="errcard ai-hint"><strong>' +
    esc(t("ai.hint_title")) +
    "</strong> " +
    esc(res.hint) +
    "</div>";
}

// issue #426 — раздел «Настройки» (синхронизация контролов) — core-резидент.
registerSectionHook("settings", () => syncSettingsControls());

// issue #546 — ГАРАНТИЯ: каталог ui.json загружен до первого синхронного
// t()/tp(). Top-level await блокирует выполнение всех импортирующих core.js
// модулей (а его импортируют все рендеры) до завершения загрузки. Строгий CSP
// (#563) не мешает — это fetch same-origin, не eval. Сбой сети → t() отдаёт
// видимые маркеры, приложение всё равно поднимается (не виснет на await).
try {
  await _loadUiCatalog();
} catch {
  // каталог недоступен — t() вернёт ⟦key⟧-маркеры (осознанно, не тихий RU)
}

/**
 * Короткое ненавязчивое уведомление в углу экрана (issue #633).
 *
 * До этого действия молчали: copyToClipboard глушил ошибку в `.catch(() => {})`,
 * а сообщения об ошибках затирали панель результатов `#out` — причём на вкладке
 * «Разбор» она вообще скрыта, так что текст уходил в никуда.
 *
 * Ошибки получают `role="alert"` и живут дольше — их нельзя пропустить;
 * остальные — `role="status"`, чтобы скринридер не прерывал пользователя.
 * Сообщение подставляется через textContent (не innerHTML) — на вход приходят
 * в том числе строки от сервера.
 *
 * @param {string} message — готовый локализованный текст
 * @param {"info"|"success"|"error"} [kind]
 * @param {{timeout?: number}} [options]
 */
function toast(message, kind = "info", options = {}) {
  const stack = $("#toast-stack");
  if (!stack) return;

  const el = document.createElement("div");
  el.className = "toast toast-" + kind;
  el.setAttribute("role", kind === "error" ? "alert" : "status");

  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  el.appendChild(text);

  // issue #805 (DESW-06): сообщение об ошибке часто единственный канал
  // диагностики, а уезжало оно по таймеру — перечитать, выделить и вставить в
  // issue было нечем (WCAG 2.2.1). Отсюда кнопка закрытия у каждого тоста,
  // пауза, пока на него смотрят, и отсутствие автозакрытия у ошибок.
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.textContent = "✕";
  close.setAttribute("aria-label", t("toast.close_aria"));
  el.appendChild(close);

  stack.appendChild(el);

  // Кадр на применение начального состояния — иначе элемент сразу окажется в
  // конечном и переход появления не отработает.
  requestAnimationFrame(() => el.classList.add("toast-visible"));

  let timer = null;
  const dismiss = () => {
    el.classList.remove("toast-visible");
    window.setTimeout(() => el.remove(), 200);
  };
  // Ошибка ждёт закрытия рукой; `options.timeout` перекрывает это решение
  // явным числом, если вызывающему нужно иное.
  const life = options.timeout ?? (kind === "error" ? null : 2500);
  const arm = () => {
    if (life !== null) timer = window.setTimeout(dismiss, life);
  };
  const hold = () => {
    window.clearTimeout(timer);
    timer = null;
  };

  close.addEventListener("click", dismiss);
  // Наведение и фокус внутри тоста откладывают уход: пока текст читают или
  // выделяют, он не должен исчезать из-под курсора.
  el.addEventListener("mouseenter", hold);
  el.addEventListener("focusin", hold);
  el.addEventListener("mouseleave", arm);
  el.addEventListener("focusout", arm);
  arm();
}

// issue #1178 — общий помощник модальных окон. До него focus-trap существовал
// тремя независимыми копиями (согласие на AI здесь же, онбординг в app.js,
// обратная связь в feedback.js), и они разошлись: фикс #804 — вешать keydown на
// document, а не на оверлей — попал только в одну. Разница не видна, пока
// кто-нибудь не проверит клавиатурой все окна подряд, поэтому четвёртая копия
// не пишется; существующие мигрируют отдельной задачей (#1225).
//
// Что помощник обязан делать, кроме собственно ловушки Tab:
//   * слушать keydown на `document`, а НЕ на оверлее (#804, FER-04). После
//     клика по подложке фокус уходит на `body`, а `body` не потомок оверлея —
//     событие до него не доходит, и Escape с Tab становятся мёртвыми;
//   * возвращать фокус туда, откуда окно открыли, иначе клавиатурный
//     пользователь теряет место в интерфейсе (#637);
//   * брать список остановок в момент нажатия, а не при открытии: содержимое
//     окна может дорисоваться позже (условие приезжает по сети).
function openModal(overlay, { onClose, initialFocus, closeOnBackdrop = true } = {}) {
  if (!overlay) return () => {};
  const returnFocus = document.activeElement;
  let closed = false;

  const stops = () =>
    [...overlay.querySelectorAll("a[href], button, input, select, textarea, [tabindex]")].filter(
      el => !el.disabled && el.tabIndex !== -1 && el.offsetParent !== null,
    );

  const close = () => {
    if (closed) return;
    closed = true;
    overlay.hidden = true;
    document.removeEventListener("keydown", onKeydown);
    overlay.removeEventListener("mousedown", onBackdrop);
    // Фокус возвращается СЛЕДУЮЩИМ тактом, а не сразу. Закрытие по подложке
    // приходит на `mousedown`, и браузер после нас доигрывает mouseup/click по
    // уже скрытому оверлею — синхронно поставленный фокус тут же сбрасывается
    // на body. Поймано прогоном в браузере: после Escape фокус возвращался, а
    // после клика по подложке — нет; в тестах без настоящего движка событий
    // такое не воспроизводится вовсе.
    if (returnFocus && typeof returnFocus.focus === "function") {
      setTimeout(() => returnFocus.focus(), 0);
    }
    if (onClose) onClose();
  };

  const onKeydown = e => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const list = stops();
    if (!list.length) return;
    const first = list[0];
    const last = list[list.length - 1];
    // Фокус мог оказаться вне окна (клик по подложке увёл его на body) —
    // возвращаем его внутрь, иначе Tab уходит гулять по странице под оверлеем.
    if (!overlay.contains(document.activeElement)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
      return;
    }
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const onBackdrop = e => {
    // Клик мимо диалога закрывает окно. Проверяем сам оверлей, а не потомков:
    // клик внутри диалога всплывает сюда же.
    if (closeOnBackdrop && e.target === e.currentTarget) close();
  };

  overlay.hidden = false;
  document.addEventListener("keydown", onKeydown);
  overlay.addEventListener("mousedown", onBackdrop);
  const target = initialFocus || stops()[0];
  if (target && typeof target.focus === "function") target.focus();
  return close;
}

export {
  $,
  SECTIONS,
  applyTheme,
  applyUiLocale,
  codeBlock,
  cycleTheme,
  errorSummary,
  esc,
  explainFailureWithAi,
  fetchCodeTerms,
  getSelectedCase,
  kpiGrid,
  makeEditor,
  openModal,
  registerSectionHook,
  renderTermsInto,
  revealWithMotion,
  setSection,
  skeletonBlock,
  skeletonListItems,
  skeletonWithLabel,
  syncSectionHash,
  clearLoadError,
  fetchJsonOrThrow,
  renderLoadError,
  state,
  stripAnsi,
  syncLangButtons,
  t,
  tOr,
  toast,
  tp,
};
