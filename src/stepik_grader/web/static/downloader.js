// downloader.js — загрузчик задач Stepik + браузерный OAuth (#426).
//
// issue #723/#724/#725: раздел разведён на три состояния вместо «всегда мастер
// OAuth». Первый запуск — развилка «настроить впервые / указать готовый
// secrets.json»; дальше — компактная строка состояния с проверкой файла. Куда
// скачивать, показывается значением из stepik_config.json (а не пустым полем с
// плейсхолдером) и меняется по явной кнопке. Главное действие — URL + «Скачать»
// — живёт в шапке раздела, результат появляется под панелью доступа.
import { $, codeBlock, esc, kpiGrid, registerSectionHook, setSection, skeletonBlock, t } from "./core.js";
import { addRecentPath, setMode } from "./grade.js";

// Локальное состояние панели доступа: какую ветку развилки выбрал пользователь.
// Не сохраняется — «второй раз» распознаётся по самому конфигу (есть рабочий
// secrets.json → компактный вид), а не по флагу в хранилище.
let authChoice = null; // null | "wizard" | "existing"
let lastConfig = null;

function renderDownloaderResult(data) {
  const panel = $("#downloader-result-panel");
  const content = $("#downloader-content");
  if (panel) panel.hidden = false;

  if (!data.ok) {
    content.innerHTML = '<p class="msg">' + esc(data.message) + "</p>";
    return;
  }

  let h = kpiGrid([
    { label: t("downloader.kpi_tests"), value: data.tests.count },
    { label: t("downloader.kpi_files"), value: data.files.length },
  ]);
  if (data.message) h += '<p class="msg">' + esc(data.message) + "</p>";
  h += '<div class="field-label">' + esc(t("downloader.col_path")) + "</div>" + codeBlock(data.path);
  h += '<div class="field-label">' + esc(t("downloader.col_files")) + "</div>" + codeBlock(data.files.join("\n"));
  if (data.files.length) {
    h +=
      '<button id="downloader-goto-check" class="btn btn-secondary mt-3">' +
      esc(t("downloader.goto_check")) + "</button>";
  }
  content.innerHTML = h;

  const gotoBtn = $("#downloader-goto-check");
  if (gotoBtn) {
    gotoBtn.addEventListener("click", () => {
      $("#path").value = data.path;
      addRecentPath(data.path);
      setMode("tests");
      setSection("check");
    });
  }
}

// -- Конфиг загрузчика: куда скачивать (issue #725) ---------------------------

async function loadDownloaderConfig() {
  try {
    const r = await fetch("/api/downloader/config");
    lastConfig = await r.json();
  } catch {
    lastConfig = null;
  }
  renderRootRow();
  return lastConfig;
}

function renderRootRow(editing = false) {
  const row = $("#downloader-root-row");
  if (!row || !lastConfig) return;
  if (!editing) {
    const isDefault = !lastConfig.configured;
    row.innerHTML =
      '<span class="config-label">' + esc(t("downloader.root_current")) + "</span> " +
      '<code class="config-value">' + esc(lastConfig.root_dir) + "</code>" +
      (isDefault ? ' <span class="hint-inline">' + esc(t("downloader.root_default_note")) + "</span>" : "") +
      ' <button id="downloader-root-edit" class="btn btn-secondary btn-sm">' +
      esc(t("downloader.root_change")) + "</button>";
    return;
  }
  row.innerHTML =
    '<span class="config-label">' + esc(t("downloader.root_current")) + "</span> " +
    '<input class="form-input config-input" id="downloader-root" type="text" value="' +
    esc(lastConfig.root_dir) + '" aria-label="' + esc(t("downloader.root_current")) + '">' +
    ' <button id="downloader-root-save" class="btn btn-primary btn-sm">' + esc(t("downloader.root_save")) + "</button>" +
    ' <button id="downloader-root-cancel" class="btn btn-secondary btn-sm">' + esc(t("common.cancel")) + "</button>" +
    '<div id="downloader-root-msg" class="hint"></div>';
}

async function saveRootDir() {
  const input = $("#downloader-root");
  const msg = $("#downloader-root-msg");
  const value = input ? input.value.trim() : "";
  if (!value) return;
  try {
    const r = await fetch("/api/downloader/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root_dir: value }),
    });
    const data = await r.json();
    if (!r.ok || !data.ok) {
      if (msg) msg.textContent = data.message || t("downloader.root_save_failed");
      return;
    }
    lastConfig = data;
    renderRootRow();
  } catch (e) {
    if (msg) msg.textContent = t("common.request_error_detail", { detail: String(e) });
  }
}

// -- Авторизация Stepik: мастер первого запуска (issue #402/#723) -------------
// GET /api/auth/status → развилка/форма → POST /api/auth/start →
// polling /api/v1/runs/{id}. Браузерный OAuth убирает CLI-стену онбординга:
// авторизоваться можно прямо из web, без ручного создания secrets.json.

async function loadAuthStatus() {
  const panel = $("#auth-panel");
  if (!panel) return;
  panel.innerHTML = '<p class="msg">' + esc(t("auth.checking")) + "</p>";
  try {
    const r = await fetch("/api/auth/status");
    renderAuthPanel(await r.json());
  } catch (e) {
    panel.innerHTML = '<p class="msg">' + esc(t("auth.check_failed", { detail: String(e) })) + "</p>";
  }
}

function renderAuthPanel(data) {
  const panel = $("#auth-panel");
  if (!panel) return;
  const path = (data && data.secrets_path) || "secrets.json";

  // Доступ есть — строка состояния, без единого поля ввода.
  if (data && data.authorized) {
    panel.innerHTML =
      '<div class="auth-ok"><span class="auth-state">' + esc(t("auth.active")) + "</span> " +
      '<code class="config-value">' + esc(path) + "</code> " +
      '<button id="auth-recheck" class="btn btn-secondary btn-sm">' + esc(t("auth.recheck")) + "</button> " +
      '<button id="auth-reconfigure" class="btn btn-secondary btn-sm">' + esc(t("auth.reconfigure")) + "</button></div>";
    return;
  }

  // Креды есть, протух только токен — одна кнопка, без повторного ввода.
  if (data && data.reason === "no_token" && authChoice === null) {
    panel.innerHTML =
      '<div class="auth-ok"><span class="auth-state auth-state-warn">' + esc(t("auth.token_expired")) + "</span> " +
      '<code class="config-value">' + esc(path) + "</code> " +
      '<button id="auth-start" class="btn btn-primary btn-sm">' + esc(t("auth.reauth_btn")) + "</button> " +
      '<button id="auth-reconfigure" class="btn btn-secondary btn-sm">' + esc(t("auth.reconfigure")) + "</button>" +
      '<div id="auth-progress" class="hint" hidden></div></div>';
    return;
  }

  if (authChoice === null) {
    panel.innerHTML = renderAuthChoice();
    return;
  }
  panel.innerHTML = authChoice === "existing" ? renderExistingForm(path) : renderWizardForm();
}

/** Развилка первого запуска: мастер или готовый файл (issue #723). */
function renderAuthChoice() {
  return (
    '<div class="auth-choice">' +
    '<p class="msg">' + esc(t("auth.choice_intro")) + "</p>" +
    '<div class="row">' +
    '<button id="auth-choice-wizard" class="btn btn-primary">' + esc(t("auth.choice_setup")) + "</button>" +
    '<button id="auth-choice-existing" class="btn btn-secondary">' + esc(t("auth.choice_existing")) + "</button>" +
    "</div></div>"
  );
}

/** Повторный запуск: путь к готовому secrets.json + проверка (issue #723). */
function renderExistingForm(path) {
  return (
    '<div class="auth-form">' +
    '<div class="form-group"><label class="form-label" for="auth-secrets-path">' +
    esc(t("auth.secrets_path_label")) + "</label>" +
    '<input class="form-input" id="auth-secrets-path" type="text" value="' + esc(path) + '"></div>' +
    '<div class="row">' +
    '<button id="auth-check-file" class="btn btn-primary">' + esc(t("auth.check_file")) + "</button>" +
    '<button id="auth-back" class="btn btn-secondary">' + esc(t("common.cancel")) + "</button>" +
    "</div>" +
    '<div id="auth-file-msg" class="hint"></div></div>'
  );
}

/** Первый запуск: создание secrets.json с OAuth-кредами (issue #402). */
function renderWizardForm() {
  const redirect = "http://localhost:8080/callback";
  return (
    '<div class="auth-form">' +
    '<p class="msg">' + esc(t("auth.form_intro")) + " <code>secrets.json</code>.</p>" +
    '<p class="hint">' + esc(t("auth.form_step1_pre")) + " " +
    '<a href="https://stepik.org/oauth2/applications/" target="_blank" rel="noopener">' +
    "stepik.org/oauth2/applications</a>" +
    esc(t("auth.form_step1_post")) + " <code>" + redirect + "</code>.</p>" +
    '<div class="form-group"><label class="form-label" for="auth-client-id">' + esc(t("auth.client_id")) + "</label>" +
    '<input class="form-input" id="auth-client-id" type="text" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="auth-client-secret">' + esc(t("auth.client_secret")) + "</label>" +
    '<input class="form-input" id="auth-client-secret" type="password" autocomplete="off"></div>' +
    '<div class="row">' +
    '<button id="auth-start" class="btn btn-primary">' + esc(t("auth.start_btn")) + "</button>" +
    '<button id="auth-back" class="btn btn-secondary">' + esc(t("common.cancel")) + "</button>" +
    "</div>" +
    '<div id="auth-progress" class="hint" hidden></div>' +
    "</div>"
  );
}

/** Проверить указанный secrets.json: сохранить путь и показать вердикт (#723). */
async function checkSecretsFile() {
  const input = $("#auth-secrets-path");
  const msg = $("#auth-file-msg");
  const value = input ? input.value.trim() : "";
  if (!value) return;
  if (msg) msg.textContent = t("auth.checking");
  try {
    const r = await fetch("/api/downloader/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secrets_path: value }),
    });
    const data = await r.json();
    if (!r.ok || !data.ok) {
      if (msg) msg.textContent = data.message || t("auth.file_check_failed");
      return;
    }
    lastConfig = data;
    const auth = data.auth || {};
    if (auth.authorized || auth.reason === "no_token") {
      authChoice = null; // файл рабочий — дальше обычный компактный вид
      renderAuthPanel({ ...auth, secrets_path: data.secrets_path });
      return;
    }
    if (msg) msg.textContent = t("auth.file_missing", { path: data.secrets_path });
  } catch (e) {
    if (msg) msg.textContent = t("common.request_error_detail", { detail: String(e) });
  }
}

async function startBrowserAuth() {
  const idEl = $("#auth-client-id");
  const secEl = $("#auth-client-secret");
  const progress = $("#auth-progress");
  const btn = $("#auth-start");
  // Форма мастера может отсутствовать (повторная авторизация одной кнопкой):
  // тогда креды берёт сервер из secrets.json (issue #723).
  const clientId = idEl ? idEl.value.trim() : "";
  const clientSecret = secEl ? secEl.value.trim() : "";
  if (progress) progress.hidden = false;
  if (idEl && (!clientId || !clientSecret)) {
    if (progress) progress.textContent = t("auth.need_credentials");
    return;
  }
  if (btn) btn.disabled = true;
  if (progress) progress.textContent = t("auth.opening_browser");
  try {
    const body = idEl ? { client_id: clientId, client_secret: clientSecret } : {};
    const resp = await fetch("/api/auth/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const created = await resp.json();
    if (!created.run_id) {
      if (progress) progress.textContent = created.message || t("auth.start_failed");
      if (btn) btn.disabled = false;
      return;
    }
    pollAuth(created.run_id);
  } catch (e) {
    if (progress) progress.textContent = t("common.request_error_detail", { detail: String(e) });
    if (btn) btn.disabled = false;
  }
}

function pollAuth(runId) {
  const progress = $("#auth-progress");
  const btn = $("#auth-start");
  const step = async () => {
    try {
      const r = await fetch("/api/v1/runs/" + runId);
      const data = await r.json();
      if (!r.ok || !data.status) {
        // Job исчез (404 run_not_found) или ответ без статуса — не крутить опрос
        // бесконечно (issue #402).
        if (progress)
          progress.textContent = data.message || t("auth.status_failed");
        if (btn) btn.disabled = false;
        return;
      }
      if (data.status === "done") {
        authChoice = null;
        loadAuthStatus(); // перерисует панель как «Доступ активен»
        return;
      }
      if (data.status === "error" || data.status === "cancelled") {
        if (progress)
          progress.textContent = data.message || t("auth.auth_failed");
        if (btn) btn.disabled = false;
        return;
      }
      setTimeout(step, 800);
    } catch (e) {
      if (progress) progress.textContent = t("auth.poll_error", { detail: String(e) });
      if (btn) btn.disabled = false;
    }
  };
  step();
}

async function downloadTask() {
  const url = $("#downloader-url").value.trim();
  if (!url) return;
  const btn = $("#downloader-run");
  btn.disabled = true;
  btn.textContent = t("downloader.downloading");
  const panel = $("#downloader-result-panel");
  if (panel) panel.hidden = false;
  const content = $("#downloader-content");
  content.innerHTML = skeletonBlock();
  try {
    // Куда скачивать, сервер берёт из stepik_config.json — то же значение,
    // что показано строкой «Куда скачивать» (issue #725).
    const r = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await r.json();
    renderDownloaderResult(data);
  } catch (e) {
    content.innerHTML = '<p class="msg">' + esc(t("common.request_error_detail", { detail: String(e) })) + "</p>";
  } finally {
    btn.disabled = false;
    btn.textContent = t("downloader.download_btn");
  }
}

/** Делегирование кликов панели доступа и строки конфига (issue #723/#725). */
function handleDownloaderClick(e) {
  const id = e.target && e.target.id;
  if (!id) return;
  if (id === "auth-choice-wizard" || id === "auth-choice-existing") {
    authChoice = id === "auth-choice-wizard" ? "wizard" : "existing";
    loadAuthStatus();
    return;
  }
  if (id === "auth-back") {
    authChoice = null;
    loadAuthStatus();
    return;
  }
  if (id === "auth-reconfigure") {
    authChoice = "wizard";
    loadAuthStatus();
    return;
  }
  if (id === "auth-recheck") return void loadAuthStatus();
  if (id === "auth-check-file") return void checkSecretsFile();
  if (id === "auth-start") return void startBrowserAuth();
  if (id === "downloader-root-edit") return void renderRootRow(true);
  if (id === "downloader-root-cancel") return void renderRootRow(false);
  if (id === "downloader-root-save") return void saveRootDir();
}

// issue #426 — регистрация раздела «Загрузчик задач» (статус OAuth + конфиг).
registerSectionHook("downloader", () => {
  loadAuthStatus();
  loadDownloaderConfig();
});

export {
  downloadTask,
  handleDownloaderClick,
  loadAuthStatus,
  loadDownloaderConfig,
  startBrowserAuth,
};
