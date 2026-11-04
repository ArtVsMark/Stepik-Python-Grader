// statement.js — окно с условием задачи (issue #1178).
//
// Замыкает цикл работы над главой: выбрал задачу → прочитал условие → решил →
// проверил → отправил. До этого условие приходилось искать на стороне — на
// Stepik в браузере или в файле с разметкой, — хотя грейдер уже хранит его
// рядом с задачей.
//
// Отдельным модулем, а не в grade.js: тот уже полторы тысячи строк, а связь у
// окна с ним ровно одна — путь к задаче из поля `#path`.
import { $, esc, openModal, state, t, toast } from "./core.js";

// Тело условия приходит УЖЕ очищенным сервером (issue #1177: whitelist тегов,
// вырезанные `on*` и опасные схемы). Поэтому здесь `innerHTML`, а не `esc()`:
// разметка — смысл ответа, без неё останется слипшийся текст без таблиц
// примеров. Чистить второй раз на клиенте незачем и вредно: две реализации
// whitelist разойдутся, и та, что в JS, обходится проще.
let _lastPath = null;
let _lastData = null;

/** Активна ли кнопка: только режим 1 и только если условие есть. */
async function refreshStatementButton() {
  const btn = $("#statement-open");
  if (!btn) return;
  const path = ($("#path")?.value || "").trim();

  if (state.mode !== "file" || !path) {
    _setDisabled(btn, "statement.open_title");
    return;
  }
  if (path === _lastPath && _lastData) {
    _applyAvailability(btn, _lastData);
    return;
  }

  const data = await _fetchStatement(path);
  _lastPath = path;
  _lastData = data;
  _applyAvailability(btn, data);
}

function _applyAvailability(btn, data) {
  if (!data || data.kind !== "statement") {
    // «Условия нет» — кнопка неактивна с объяснением, а не пустое окно после
    // нажатия: пустое окно человек читает как поломку.
    _setDisabled(btn, "statement.none_title");
    return;
  }
  btn.disabled = false;
  btn.title = t("statement.open_title");
}

function _setDisabled(btn, titleKey) {
  btn.disabled = true;
  btn.title = t(titleKey);
}

async function _fetchStatement(path) {
  try {
    const r = await fetch(
      "/api/task/statement?" + new URLSearchParams({ path, lang: state.lang }),
    );
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

function _crumbs(header) {
  if (!header) return "";
  const parts = [header.course_title, header.section_title, header.lesson_title].filter(Boolean);
  if (header.step_position != null) parts.push(t("statement.step") + " " + header.step_position);
  return parts.map(esc).join(" · ");
}

function _renderAttachments(items) {
  const box = $("#statement-attachments");
  if (!box) return;
  const list = (items || []).filter(item => item.name);
  if (!list.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  // Вложение, которое не приехало, помечается СЛОВОМ, а не только цветом:
  // задача, ссылающаяся на files.txt, иначе выглядит как ошибка в коде
  // решения — ровно тот дефект, из которого выросла issue #1112.
  const rows = list
    .map(item =>
      item.present
        ? "<li>" + esc(item.name) + "</li>"
        : '<li class="statement-missing">' +
          esc(item.name) +
          " — " +
          esc(t("statement.attachment_missing")) +
          "</li>",
    )
    .join("");
  box.innerHTML =
    "<strong>" + esc(t("statement.attachments")) + "</strong><ul>" + rows + "</ul>";
  box.hidden = false;
}

/** Открыть окно с условием выбранной задачи. */
async function openStatement() {
  const btn = $("#statement-open");
  const overlay = $("#statement-overlay");
  const body = $("#statement-body");
  if (!btn || btn.disabled || !overlay || !body) return;

  const path = ($("#path").value || "").trim();
  const data = path === _lastPath && _lastData ? _lastData : await _fetchStatement(path);
  _lastPath = path;
  _lastData = data;

  if (!data || data.kind !== "statement") {
    toast(t("statement.none_title"), "error");
    _applyAvailability(btn, data);
    return;
  }

  const crumbs = $("#statement-crumbs");
  if (crumbs) crumbs.textContent = _crumbs(data.header);
  body.innerHTML = data.html || "";
  body.scrollTop = 0;
  _renderAttachments(data.attachments);

  // Закрытие, Escape, ловушка Tab и возврат фокуса — общим помощником, а не
  // четвёртой копией того же кода (см. его комментарий в core.js и issue #1225).
  const close = openModal(overlay, { initialFocus: $("#statement-close-x") });
  $("#statement-close-x").onclick = close;
}

/** Сбросить кэш: сменилась задача — прежнее условие к ней не относится. */
function resetStatement() {
  _lastPath = null;
  _lastData = null;
  const btn = $("#statement-open");
  if (btn) _setDisabled(btn, "statement.open_title");
}

const openBtn = $("#statement-open");
if (openBtn) openBtn.addEventListener("click", openStatement);

export { openStatement, refreshStatementButton, resetStatement };
