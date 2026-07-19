// app.js — entry: связывание слушателей и bootstrap; импортирует модули (#426).
import { loadGlossary, loadRules, parseGlossaryHash, renderGlossaryChips, selectGlossaryCard, selectRuleCard, setGlossaryView } from "./content.js";
import { $, applyTheme, cycleTheme, setSection, setTheme, state } from "./core.js";
import { downloadTask, loadAuthStatus, startBrowserAuth } from "./downloader.js";
import { cancelActiveRun, checkTermsTimer, closePalette, findReference, findSolutions, grade, loadCheckTerms, loadCommands, mountEditor, openPalette, paletteCommands, renderPaletteList, renderRecentPaths, runCommand, saveSolution, setMode, setResultTab, updateDirtyIndicator, updateMicroCustomVisibility } from "./grade.js";
import { cancelSandboxRun, runPlayground, runTrace } from "./sandbox.js";
import { drawMemArrows, renderTraceStep } from "./trace-player.js";

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
    badge.textContent = "🔒 OS-изоляция";
    badge.className = "badge badge-neutral";
    badge.title = "Код исполняется в OS-песочнице (--sandbox): изоляция ФС и сети.";
  } else {
    badge.textContent = "⚠ Без OS-изоляции";
    badge.className = "badge badge-warning";
    badge.title =
      "Код исполняется без OS-изоляции ФС/сети — запускайте только доверенные " +
      "решения. Для недоверенного кода перезапустите сервер с флагом --sandbox.";
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
const savedPath = localStorage.getItem("grader_path");
if (savedPath) $("#path").value = savedPath;

// issue #565: показать статус OS-изоляции и (однократно) уведомить о истории.
initExecModeBadge();
maybeShowHistoryNotice();
