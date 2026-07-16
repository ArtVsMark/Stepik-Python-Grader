// trace-player.js — пошаговый плеер трейса + диаграмма памяти (#426).
import { $, esc, state } from "./core.js";
import { renderSandboxError, setSandboxStatus } from "./sandbox.js";

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

export {
  drawMemArrows,
  renderTraceStep,
  showTracePlayer,
};
