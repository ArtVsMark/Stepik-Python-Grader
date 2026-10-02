// feedback.js — канал обратной связи: черновик issue + предпросмотр (issue #754).
//
// Модалка НИЧЕГО не отправляет. Она просит сервер собрать черновик
// (POST /api/feedback → prefilled-URL к GitHub Issue Forms + предпросмотр полей)
// и ставит полученную ссылку на кнопку-<a>. Issue публикует сам пользователь,
// кнопкой Submit в форме GitHub: у грейдера нет ни токена, ни сервера для этого
// (эпик #751). Сборка URL, редакция секретов и усечение живут в
// core/feedback.py — здесь только рендер и переходы.
import { $, esc, openModal, state, t } from "./core.js";

const KIND_HINT_KEYS = {
  bug: "feedback.kind_hint_bug",
  idea: "feedback.kind_hint_idea",
  "task-problem": "feedback.kind_hint_task",
};

// id полей YAML-форм → ключ подписи в предпросмотре. Поле без подписи
// печатается своим id: предпросмотр не должен молчать о том, что уедет.
const FIELD_LABEL_KEYS = {
  "what-happened": "feedback.field_description",
  idea: "feedback.field_description",
  details: "feedback.field_description",
  environment: "feedback.field_environment",
  commit: "feedback.field_commit",
  "step-url": "feedback.field_step_url",
  logs: "feedback.field_logs",
};

const DRAFT_DEBOUNCE_MS = 500;

let feedbackKind = "bug";
let draftTimer = null;
// Закрывающая функция открытого окна: её отдаёт openModal, она же снимает
// слушатели и возвращает фокус. null — окно закрыто.
let closeModal = null;

function _kindHint() {
  const hint = $("#feedback-kind-hint");
  if (hint) hint.textContent = t(KIND_HINT_KEYS[feedbackKind] || KIND_HINT_KEYS.bug);
}

function _renderPreview(draft) {
  const body = $("#feedback-preview-body");
  const warnings = $("#feedback-warnings");
  if (!body) return;
  if (!draft) {
    // Сеть/сервер не ответили: ссылка остаётся дефолтной (пустая форма), и об
    // этом надо сказать — иначе пользователь ждёт заполненных полей и не найдёт их.
    body.innerHTML = '<p class="msg-neutral">' + esc(t("feedback.preview_error")) + "</p>";
    if (warnings) warnings.hidden = true;
    return;
  }
  body.innerHTML = draft.fields
    .map(field => {
      const key = FIELD_LABEL_KEYS[field.id];
      return (
        '<div class="feedback-preview-field"><strong>' +
        esc(key ? t(key) : field.id) +
        "</strong><pre>" +
        esc(field.value) +
        "</pre></div>"
      );
    })
    .join("");
  if (!warnings) return;
  const notes = [];
  if (draft.truncated.length) {
    notes.push(t("feedback.truncated", { fields: draft.truncated.join(", ") }));
  }
  if (draft.dropped.length) {
    notes.push(t("feedback.dropped", { fields: draft.dropped.join(", ") }));
  }
  warnings.textContent = notes.join(" ");
  warnings.hidden = notes.length === 0;
}

async function _fetchDraft() {
  const summary = $("#feedback-summary");
  const stepUrl = $("#feedback-step-url");
  try {
    const resp = await fetch("/api/feedback?lang=" + encodeURIComponent(state.lang), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: feedbackKind,
        summary: summary ? summary.value : "",
        step_url: stepUrl ? stepUrl.value : "",
      }),
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

// issue #803 (FES-03): токен актуальности черновика. Debounce гасил только
// таймер, но не сам запрос: набранный раньше текст мог ответить позже нового и
// перезаписать предпросмотр вместе со ссылкой. Приём тот же, что у `_routeSeq`
// в роутере и `authPollRunId` в загрузчике.
let _draftSeq = 0;

/** Обе ссылки — в неактивное состояние: показанному черновика не отвечает. */
function _disableFeedbackLinks() {
  for (const id of ["#feedback-open-github", "#feedback-discussions"]) {
    const el = $(id);
    if (!el) continue;
    el.removeAttribute("href");
    el.setAttribute("aria-disabled", "true");
  }
}

async function refreshFeedbackDraft() {
  const seq = ++_draftSeq;
  // issue #803 (FES-02): пока новый черновик не пришёл, прежняя ссылка уже
  // не соответствует форме — гасим её сразу, а не только при провале. Иначе
  // между сменой типа и ответом сервера кнопка ведёт в форму прежнего вида.
  _disableFeedbackLinks();
  const draft = await _fetchDraft();
  if (seq !== _draftSeq) return; // ответ устарел — его обогнал более свежий
  _renderPreview(draft);
  const link = $("#feedback-open-github");
  if (!link) return;
  if (!draft) {
    // Черновик не собрался: ссылку НЕ выдумываем (адресов GitHub в статике нет —
    // инвариант «никаких внешних href в index.html»). Кнопка остаётся неактивной,
    // а причину показывает предпросмотр.
    return;
  }
  link.href = draft.url;
  link.removeAttribute("aria-disabled");
  const discussions = $("#feedback-discussions");
  if (discussions) {
    discussions.href = draft.discussions_url;
    discussions.removeAttribute("aria-disabled");
  }
}

function _scheduleDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(refreshFeedbackDraft, DRAFT_DEBOUNCE_MS);
}

function setFeedbackKind(kind) {
  feedbackKind = kind;
  document.querySelectorAll("[data-fbkind]").forEach(btn => {
    const active = btn.dataset.fbkind === kind;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  // Ссылка на шаг Stepik осмысленна только для «задача проверяется неправильно» —
  // в остальных формах такого поля просто нет (core/feedback.py:_FIELD_IDS).
  const stepRow = $("#feedback-step-row");
  if (stepRow) stepRow.hidden = kind !== "task-problem";
  _kindHint();
  refreshFeedbackDraft();
}

function openFeedback() {
  const overlay = $("#feedback-overlay");
  if (!overlay) return;
  // issue #1225: ловушка Tab, Escape и возврат фокуса — в общем помощнике
  // core.js, своей копии здесь больше нет. Пока слушатель висел на самом
  // оверлее, клик по подложке уводил фокус на body — и клавиатура переставала
  // управлять окном, хотя мышью всё работало.
  //
  // closeOnBackdrop: false — поведение сохранено намеренно: в окне лежит
  // написанный, но ещё не отправленный текст, и промах мимо диалога не должен
  // его смахивать.
  closeModal = openModal(overlay, {
    initialFocus: $("#feedback-summary"),
    closeOnBackdrop: false,
    onClose: () => {
      clearTimeout(draftTimer);
      closeModal = null;
    },
  });
  _kindHint();
  // Предпросмотр готовим сразу: окружение уже известно, и пользователь видит,
  // что уйдёт, до того как что-то напишет.
  refreshFeedbackDraft();
}

function closeFeedback() {
  if (closeModal) closeModal();
}

function initFeedback() {
  const overlay = $("#feedback-overlay");
  if (!overlay) return;
  const openBtn = $("#feedback-open");
  if (openBtn) openBtn.addEventListener("click", openFeedback);
  const closeX = $("#feedback-close-x");
  if (closeX) closeX.addEventListener("click", closeFeedback);
  const cancel = $("#feedback-cancel");
  if (cancel) cancel.addEventListener("click", closeFeedback);
  document
    .querySelectorAll("[data-fbkind]")
    .forEach(btn => btn.addEventListener("click", () => setFeedbackKind(btn.dataset.fbkind)));
  const summary = $("#feedback-summary");
  if (summary) summary.addEventListener("input", _scheduleDraft);
  const stepUrl = $("#feedback-step-url");
  if (stepUrl) stepUrl.addEventListener("input", _scheduleDraft);
  // Клик по кнопке «Открыть форму на GitHub» закрывает модалку: пользователь уже
  // ушёл в новую вкладку, возвращаться ему некуда.
  const link = $("#feedback-open-github");
  if (link) link.addEventListener("click", closeFeedback);
}

export { closeFeedback, initFeedback, openFeedback, refreshFeedbackDraft, setFeedbackKind };
