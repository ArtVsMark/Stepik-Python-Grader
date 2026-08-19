// navigation.js — навигация по скачанным задачам (issue #1179).
//
// После массового скачивания в работе оказывается глава целиком, и на каждую
// следующую задачу приходилось заново вписывать путь. Панель убирает это
// трение, оставаясь АЛЬТЕРНАТИВОЙ полю пути, а не заменой: работа с
// произвольной папкой вне курса — рабочий сценарий, и к тому же первый по
// времени.
import { $, esc, state, t, toast } from "./core.js";

const LEVELS = ["course", "section", "step"];
const _MODE_KEY = "grader_nav_mode"; // "browse" | "path"
const _LEVEL_KEY = "grader_nav_level";
const _POS_KEY = "grader_nav_path"; // путь задачи, на которой стояли

// Плоский список шагов сквозным потоком: стрелка «Шаг» листает через границы
// глав, а смена главы не теряется — она видна в подписи контекста.
let _flat = [];
let _index = -1;
let _onPick = null; // grade.js передаёт сюда «путь выбран» — панель не знает про поиск решений
let _root = ""; // корень скачанных задач из конфига загрузчика

function _mode() {
  return localStorage.getItem(_MODE_KEY) === "browse" ? "browse" : "path";
}

function _level() {
  const saved = localStorage.getItem(_LEVEL_KEY);
  return LEVELS.includes(saved) ? saved : "step";
}

/** Развернуть дерево в плоский список шагов, сохранив хлебные крошки. */
function _flatten(courses) {
  const out = [];
  for (const course of courses) {
    for (const section of course.children) {
      for (const lesson of section.children) {
        for (const task of lesson.tasks) {
          out.push({
            ...task,
            course: course.title,
            section: section.title,
            lesson: lesson.title,
            courseId: course.id,
            sectionId: section.id,
          });
        }
      }
    }
  }
  return out;
}

async function _fetchIndex(root, { refresh = false } = {}) {
  const params = { path: root, lang: state.lang };
  if (refresh) params.refresh = "1";
  try {
    const r = await fetch("/api/tasks/index?" + new URLSearchParams(params));
    if (!r.ok) return null;
    const data = await r.json();
    return data && data.kind === "index" ? data : null;
  } catch {
    return null;
  }
}

/** Корень скачанных задач — из конфига загрузчика, а НЕ из поля пути.
 *
 * Поле `#path` указывает на ОДНУ задачу (или папку решений), а дереву нужен
 * корень, куда загрузчик складывает курсы. Первая редакция индексировала
 * `#path` — тогда дерево строилось от одной задачи и листать было нечего.
 */
async function _downloadRoot() {
  try {
    const r = await fetch("/api/downloader/config");
    if (!r.ok) return "";
    const cfg = await r.json();
    return cfg.root_dir || "";
  } catch {
    return "";
  }
}

/**
 * Загрузить инвентарь и показать панель.
 *
 * Умный дефолт: скачанных задач меньше двух — открывается «Путь», панели
 * нечего показывать. Дальше решает пользователь, и его выбор переживает
 * перезагрузку.
 */
async function initNavigation(onPick) {
  _onPick = onPick;
  _root = await _downloadRoot();
  const data = _root ? await _fetchIndex(_root) : null;
  _flat = data ? _flatten(data.courses) : [];

  if (_flat.length < 2 && !localStorage.getItem(_MODE_KEY)) {
    localStorage.setItem(_MODE_KEY, "path");
  }
  _restorePosition();
  _applyMode();
  render();
}

function _restorePosition() {
  const saved = localStorage.getItem(_POS_KEY);
  const current = ($("#path")?.value || "").trim();
  const wanted = current || saved;
  _index = wanted ? _flat.findIndex(item => item.path === wanted) : -1;
  if (_index < 0 && _flat.length && !current) _index = 0;
}

function _applyMode() {
  const browse = _mode() === "browse";
  const panel = $("#nav-panel");
  if (panel) panel.hidden = !browse;
  const browseTab = $("#nav-tab-browse");
  const pathTab = $("#nav-tab-path");
  if (browseTab) {
    browseTab.classList.toggle("active", browse);
    browseTab.setAttribute("aria-pressed", String(browse));
  }
  if (pathTab) {
    pathTab.classList.toggle("active", !browse);
    pathTab.setAttribute("aria-pressed", String(!browse));
  }
}

/** Соседи текущего шага на выбранном уровне: индексы «назад» и «вперёд». */
function _neighbours() {
  if (!_flat.length) return { prev: -1, next: -1 };
  if (_index < 0) {
    // Путь указывает вне скачанного курса — но панель существует ровно для
    // того, чтобы задачу ВЫБРАТЬ. Обе стрелки неактивными оставлять нельзя:
    // с холодного старта (поле пути = рабочая папка) войти в дерево стало бы
    // нечем, и панель была бы бесполезна именно в тот момент, когда нужна.
    return { prev: _flat.length - 1, next: 0 };
  }
  const level = _level();
  if (level === "step") {
    return { prev: _index - 1, next: _index + 1 >= _flat.length ? -1 : _index + 1 };
  }
  const key = level === "course" ? "courseId" : "sectionId";
  const current = _flat[_index][key];
  // «Вперёд» — первый шаг следующей группы, а не следующий шаг: иначе стрелка
  // «Глава» вела бы внутрь текущей главы.
  const next = _flat.findIndex((item, i) => i > _index && item[key] !== current);
  let prev = -1;
  for (let i = _index - 1; i >= 0; i--) {
    if (_flat[i][key] !== current) {
      const groupKey = _flat[i][key];
      prev = i;
      while (prev > 0 && _flat[prev - 1][key] === groupKey) prev--;
      break;
    }
  }
  return { prev, next };
}

function _counterText() {
  const level = _level();
  if (level === "step") return `${_index + 1} / ${_flat.length}`;
  const key = level === "course" ? "courseId" : "sectionId";
  const groups = [...new Set(_flat.map(item => item[key]))];
  const at = groups.indexOf(_flat[_index][key]) + 1;
  const word = level === "course" ? t("nav.counter_course") : t("nav.counter_section");
  return `${word} ${at} / ${groups.length}`;
}

function _contextText(item) {
  const parts = [item.section, item.lesson].filter(Boolean);
  if (item.step_position != null) parts.push(`${t("nav.step")} ${item.step_position}`);
  const head = parts.join(" · ");
  return item.title ? `${head} — ${item.title}` : head;
}

/** Перерисовать панель под текущую позицию. */
function render() {
  const prevBtn = $("#nav-prev");
  const nextBtn = $("#nav-next");
  const counter = $("#nav-counter");
  const context = $("#nav-context");
  if (!prevBtn || !nextBtn) return;

  const level = $("#nav-level");
  if (level) level.value = _level();

  if (_index < 0 || !_flat.length) {
    // Путь указывает вне скачанного курса — честно говорим об этом, а не
    // показываем чужую позицию. Но стрелки при непустом дереве остаются
    // рабочими: ими и входят в курс.
    const { prev, next } = _neighbours();
    prevBtn.disabled = prev < 0;
    nextBtn.disabled = next < 0;
    if (counter) counter.textContent = "";
    if (context) context.textContent = _flat.length ? t("nav.outside") : t("nav.empty");
    _renderList();
    return;
  }

  const { prev, next } = _neighbours();
  // `disabled` только на краях диапазона, и с подсказкой: серая кнопка без
  // объяснения читается как «сломалось».
  prevBtn.disabled = prev < 0;
  nextBtn.disabled = next < 0;
  prevBtn.title = prev < 0 ? t("nav.at_start") : t("nav.prev_aria");
  nextBtn.title = next < 0 ? t("nav.at_end") : t("nav.next_aria");

  if (counter) counter.textContent = _counterText();
  if (context) context.textContent = _contextText(_flat[_index]);
  _renderList();
}

function _statusLabel(status) {
  if (status === "solved") return t("nav.status_solved");
  if (status === "in_progress") return t("nav.status_in_progress");
  return t("nav.status_untouched");
}

function _renderList() {
  const list = $("#nav-list");
  if (!list || list.hidden) return;
  const level = _level();

  if (level === "step") {
    list.innerHTML = _flat
      .map(
        (item, i) =>
          `<li role="option" data-idx="${i}" aria-selected="${i === _index}"` +
          (i === _index ? ' class="active"' : "") +
          `><span>${esc(_contextText(item))}</span>` +
          `<span class="nav-status nav-status-${esc(item.status)}">` +
          `${esc(_statusLabel(item.status))}</span></li>`,
      )
      .join("");
    return;
  }

  // Уровни выше шага показывают прогресс группы: «5 из 12 решено».
  const key = level === "course" ? "courseId" : "sectionId";
  const seen = new Map();
  _flat.forEach((item, i) => {
    const bucket = seen.get(item[key]) || { first: i, title: "", solved: 0, total: 0 };
    bucket.title = level === "course" ? item.course : item.section;
    bucket.total += 1;
    if (item.status === "solved") bucket.solved += 1;
    seen.set(item[key], bucket);
  });
  const currentKey = _index >= 0 ? _flat[_index][key] : null;
  list.innerHTML = [...seen.entries()]
    .map(
      ([groupKey, bucket]) =>
        `<li role="option" data-idx="${bucket.first}" aria-selected="${groupKey === currentKey}"` +
        (groupKey === currentKey ? ' class="active"' : "") +
        `><span>${esc(bucket.title)}</span>` +
        `<span class="nav-status">${esc(t("nav.solved_of", { solved: bucket.solved, total: bucket.total }))}</span></li>`,
    )
    .join("");
}

function _goto(index) {
  if (index < 0 || index >= _flat.length) return;
  _index = index;
  const item = _flat[index];
  localStorage.setItem(_POS_KEY, item.path);
  const field = $("#path");
  if (field) field.value = item.path;
  render();
  if (_onPick) _onPick(item.path);
}

/** Путь ввели руками — поставить панель на эту задачу или сказать «вне курса». */
function syncFromPath(path) {
  const target = (path || "").trim();
  _index = target ? _flat.findIndex(item => item.path === target) : -1;
  render();
}

/** Пересканировать дерево — скачали новую главу. */
async function rescan() {
  if (!_root) _root = await _downloadRoot();
  const data = _root ? await _fetchIndex(_root, { refresh: true }) : null;
  if (!data) {
    toast(t("nav.rescan_failed"), "error");
    return;
  }
  _flat = _flatten(data.courses);
  _restorePosition();
  render();
}

// -- события ---------------------------------------------------------------

function _step(direction) {
  const { prev, next } = _neighbours();
  const target = direction < 0 ? prev : next;
  if (target >= 0) _goto(target);
}

$("#nav-tab-browse")?.addEventListener("click", () => {
  localStorage.setItem(_MODE_KEY, "browse");
  _applyMode();
  render();
});
$("#nav-tab-path")?.addEventListener("click", () => {
  localStorage.setItem(_MODE_KEY, "path");
  _applyMode();
});
$("#nav-prev")?.addEventListener("click", () => _step(-1));
$("#nav-next")?.addEventListener("click", () => _step(1));
$("#nav-level")?.addEventListener("change", e => {
  localStorage.setItem(_LEVEL_KEY, e.target.value);
  render();
});
$("#nav-list-toggle")?.addEventListener("click", () => {
  const list = $("#nav-list");
  const toggle = $("#nav-list-toggle");
  if (!list || !toggle) return;
  list.hidden = !list.hidden;
  toggle.setAttribute("aria-expanded", String(!list.hidden));
  if (!list.hidden) {
    _renderList();
    list.focus();
  }
});
$("#nav-list")?.addEventListener("click", e => {
  const li = e.target.closest("li[data-idx]");
  if (li) _goto(Number(li.dataset.idx));
});

// Alt+←/→, а не голые стрелки: последние заняты редактором кода. На краю
// диапазона не делают ничего — как и кнопка.
document.addEventListener("keydown", e => {
  if (!e.altKey || _mode() !== "browse") return;
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    _step(-1);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    _step(1);
  }
});

export { initNavigation, render as renderNavigation, rescan, syncFromPath };
