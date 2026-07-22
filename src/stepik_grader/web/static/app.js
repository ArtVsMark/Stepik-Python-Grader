// app.js — entry: связывание слушателей и bootstrap; импортирует модули (#426).
import { loadGlossary, loadRules, parseGlossaryHash, renderGlossaryChips, selectGlossaryCard, selectRuleCard, setGlossaryView } from "./content.js";
import { $, applyTheme, applyUiLocale, cycleTheme, setSection, state, syncLangButtons, t } from "./core.js";
import { downloadTask, loadAuthStatus, startBrowserAuth } from "./downloader.js";
import { cancelActiveRun, checkTermsTimer, findReference, findSolutions, grade, loadCheckTerms, loadCommands, mountEditor, renderRecentPaths, runCommand, saveSolution, setMode, setResultTab, updateDirtyIndicator, updateMicroCustomVisibility } from "./grade.js";
import { cancelSandboxRun, runPlayground, runTrace } from "./sandbox.js";
import { drawMemArrows, renderTraceStep } from "./trace-player.js";

function setLang(value) {
  state.lang = value;
  localStorage.setItem("grader_lang", value);
  syncLangButtons();
  // issue #545: статическую оболочку (data-i18n в index.html) локализуем сразу —
  // fire-and-forget, applyUiLocale берёт каталог из кеша (или один раз фетчит),
  // UI не блокируется.
  applyUiLocale(value);
  // issue #659: data-i18n на кнопке темы несёт ключ СИСТЕМНОГО состояния
  // (нужен до старта JS), поэтому после перелокализации разметки подпись
  // надо вернуть к фактическому режиму — иначе она соврёт при light/dark.
  applyTheme();
  // issue #546: динамика JS-рендеров берёт язык из state.lang при следующем
  // рендере; здесь обновляем то, что уже на экране и не перерисуется само —
  // тултип бейджа OS-изоляции и (если открыты «Настройки») статус истории.
  initExecModeBadge();
  if (state.section === "settings") setSection("settings"); // хук → syncSettingsControls
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

// issue #565 — статус OS-изоляции (бейдж в шапке) и однократное уведомление о
// локальном сборе истории. Флаги приходят с сервера в data-атрибутах <body>;
// читаем dataset (без inline-eval, работает под строгим CSP #563).
function initExecModeBadge() {
  const badge = $("#exec-mode-badge");
  if (!badge) return;
  const sandboxed = document.body.dataset.sandbox === "true";
  if (sandboxed) {
    badge.textContent = t("execmode.sandboxed");
    badge.className = "badge badge-neutral";
    badge.title = t("execmode.sandboxed_title");
  } else {
    badge.textContent = t("execmode.unsandboxed");
    badge.className = "badge badge-warning";
    badge.title = t("execmode.unsandboxed_title");
  }
  badge.hidden = false;
}

function maybeShowHistoryNotice() {
  const notice = $("#history-notice");
  if (!notice) return;
  // Уведомляем только если история реально пишется и уведомление ещё не видели.
  if (document.body.dataset.recordHistory !== "true") return;
  if (localStorage.getItem("grader_history_notice_seen") === "1") return;
  notice.hidden = false;
  const dismiss = $("#history-notice-dismiss");
  if (dismiss) {
    dismiss.addEventListener("click", () => {
      localStorage.setItem("grader_history_notice_seen", "1");
      notice.hidden = true;
    });
  }
}

// issue #660 — стартовый экран-онбординг. Первый показ по data-onboarding-seen
// (флаг в .grader_settings.json, инжектится сервером в <body>); повторно —
// кнопкой #onboarding-open. Focus-trap, Escape и возврат фокуса — как в модалке
// AI-согласия (core.js _requestAiConsent).
let _onboardingReturnFocus = null;

function _persistOnboardingSeen(seen) {
  // Галка «не показывать» отмечена → seen=true (больше не всплывать); снята →
  // false (вернуть авто-показ при следующем запуске). fire-and-forget: закрытие
  // не ждёт сети и не падает при офлайне; сервер пишет флаг при изменении.
  fetch("/api/v1/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ onboarding_seen: seen }),
  }).catch(() => {});
  document.body.dataset.onboardingSeen = seen ? "true" : "false";
}

function _onboardingKeydown(e) {
  if (e.key === "Escape") {
    e.preventDefault();
    closeOnboarding();
    return;
  }
  if (e.key !== "Tab") return;
  const stops = $("#onboarding-overlay").querySelectorAll("button, input");
  if (!stops.length) return;
  const first = stops[0];
  const last = stops[stops.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

function openOnboarding() {
  const overlay = $("#onboarding-overlay");
  if (!overlay) return;
  _onboardingReturnFocus = document.activeElement;
  overlay.hidden = false;
  overlay.addEventListener("keydown", _onboardingKeydown);
  const first = $("#onboarding-go-check");
  if (first) first.focus();
}

function closeOnboarding() {
  const overlay = $("#onboarding-overlay");
  if (!overlay) return;
  overlay.hidden = true;
  overlay.removeEventListener("keydown", _onboardingKeydown);
  // Галка checked (дефолт) → больше не авто-показ; снята → покажется снова.
  const dontShow = $("#onboarding-dont-show");
  _persistOnboardingSeen(dontShow ? dontShow.checked : true);
  if (_onboardingReturnFocus && typeof _onboardingReturnFocus.focus === "function") {
    _onboardingReturnFocus.focus();
  }
}

function _onboardingGo(section) {
  closeOnboarding();
  setSection(section);
}

function initOnboarding() {
  const overlay = $("#onboarding-overlay");
  if (!overlay) return;
  const openBtn = $("#onboarding-open");
  if (openBtn) openBtn.addEventListener("click", openOnboarding);
  const goCheck = $("#onboarding-go-check");
  if (goCheck) goCheck.addEventListener("click", () => _onboardingGo("check"));
  const goDl = $("#onboarding-go-downloader");
  if (goDl) goDl.addEventListener("click", () => _onboardingGo("downloader"));
  const closeBtn = $("#onboarding-close");
  if (closeBtn) closeBtn.addEventListener("click", closeOnboarding);
  const closeX = $("#onboarding-close-x");
  if (closeX) closeX.addEventListener("click", closeOnboarding);
  // Первый показ: сервер прислал data-onboarding-seen="false" (флаг ещё не стоял).
  if (document.body.dataset.onboardingSeen !== "true") openOnboarding();
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
// issue #637: клавиатурные потоки для частых действий раздела «Проверка».
// Без мыши нельзя было ни запустить прогон из редактора, ни сохранить файл —
// power-user поток отсутствовал, хотя редактор кода полноэкранный и работа в
// нём клавиатурная по природе.
const _MODE_BY_DIGIT = { 1: "file", 2: "tests", 3: "bench", 4: "microbench" };

/** Печатает ли пользователь прямо сейчас (поле ввода или редактор кода). */
function _isTyping(target) {
  if (!target) return false;
  if (target.isContentEditable) return true; // CodeMirror — contenteditable
  return Boolean(target.matches && target.matches("input, textarea, select"));
}

document.addEventListener("keydown", e => {
  if (state.section !== "check") return;
  const mod = e.ctrlKey || e.metaKey;

  // Ctrl/⌘+Enter — запустить проверку. Намеренно работает и внутри редактора:
  // это главный сценарий, ради которого хоткей и нужен.
  if (mod && e.key === "Enter") {
    e.preventDefault();
    grade();
    return;
  }

  // Ctrl/⌘+S — сохранить решение. preventDefault обязателен: иначе браузер
  // откроет собственный диалог сохранения страницы. Уважаем disabled-состояние
  // кнопки, чтобы хоткей не делал того, чего не делает клик.
  if (mod && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    const saveBtn = $("#save-solution-btn");
    if (saveBtn && !saveBtn.disabled) saveSolution();
    return;
  }

  // Цифры 1-4 — режим проверки. Только когда пользователь НЕ печатает: иначе
  // ввод «2 3» в поле stdin или кода переключал бы режимы под руками.
  if (!mod && !e.altKey && !_isTyping(e.target)) {
    const mode = _MODE_BY_DIGIT[e.key];
    if (mode) {
      e.preventDefault();
      setMode(mode);
    }
  }
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
// issue #659: язык переключается в topbar. Селекты «Настроек» убраны — они
// дублировали тумблеры; тема живёт на #theme-toggle (cycleTheme выше).
document.querySelectorAll("#lang-switch .lang-btn").forEach(btn =>
  btn.addEventListener("click", () => {
    if (btn.dataset.lang !== state.lang) setLang(btn.dataset.lang);
  })
);

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
syncLangButtons(); // issue #659 — язык из localStorage отражаем на тумблере
applyUiLocale(state.lang); // issue #545 — восстановленный из localStorage язык применяем к разметке при загрузке
mountEditor();
setSection(state.section);
setMode(state.mode);
setResultTab(state.resultTab);
renderRecentPaths();
loadCommands();
routeFromHash(); // открыть карточку из #/glossary/<id>, если ссылка прямая (issue #329)

// Восстановить последний путь.
const savedPath = localStorage.getItem("grader_path");
if (savedPath) $("#path").value = savedPath;

// issue #565: показать статус OS-изоляции и (однократно) уведомить о истории.
initExecModeBadge();
maybeShowHistoryNotice();
// issue #660: стартовый экран-онбординг — авто при первом запуске, далее по кнопке.
initOnboarding();
