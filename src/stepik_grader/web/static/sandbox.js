// sandbox.js — песочница: запуск/трейс, редактор, карточки ошибок (#426).
import { $, esc, fetchCodeTerms, makeEditor, registerSectionHook, renderTermsInto, state } from "./core.js";
import { showTracePlayer } from "./trace-player.js";

let sandboxView = null; // issue #317: отдельный редактор песочницы

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
function getSandboxCode() {
  return sandboxView ? sandboxView.state.doc.toString() : "";
}

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


// issue #426 — регистрация раздела «Песочница» (ленивый монтаж редактора).
registerSectionHook("sandbox", () => {
  mountSandboxEditor();
  loadCodeTerms();
});

export {
  cancelSandboxRun,
  renderSandboxError,
  runPlayground,
  runTrace,
  setSandboxStatus,
};
