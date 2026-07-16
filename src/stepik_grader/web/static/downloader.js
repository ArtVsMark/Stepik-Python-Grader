// downloader.js — загрузчик задач Stepik + браузерный OAuth (#426).
import { $, codeBlock, esc, kpiGrid, registerSectionHook, setSection, skeletonBlock } from "./core.js";
import { addRecentPath, setMode } from "./grade.js";

function renderDownloaderResult(data) {
  const empty = $("#downloader-empty");
  const content = $("#downloader-content");
  empty.hidden = true;
  content.hidden = false;

  if (!data.ok) {
    content.innerHTML = '<p class="msg">' + esc(data.message) + "</p>";
    return;
  }

  let h = kpiGrid([
    { label: "Тестов", value: data.tests.count },
    { label: "Файлов", value: data.files.length },
  ]);
  if (data.message) h += '<p class="msg">' + esc(data.message) + "</p>";
  h += '<div class="field-label">Путь</div>' + codeBlock(data.path);
  h += '<div class="field-label">Файлы</div>' + codeBlock(data.files.join("\n"));
  if (data.files.length) {
    h +=
      '<button id="downloader-goto-check" class="btn btn-secondary" style="margin-top:var(--space-3)">' +
      "Перейти к проверке</button>";
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

// -- Авторизация Stepik: мастер первого запуска (issue #402) -------------------
// GET /api/auth/status → форма (client_id/secret) → POST /api/auth/start →
// polling /api/v1/runs/{id}. Браузерный OAuth убирает CLI-стену онбординга:
// авторизоваться можно прямо из web, без ручного создания secrets.json.

async function loadAuthStatus() {
  const panel = $("#auth-panel");
  if (!panel) return;
  panel.hidden = false;
  panel.innerHTML = '<p class="msg">Проверка доступа к Stepik…</p>';
  try {
    const r = await fetch("/api/auth/status");
    renderAuthPanel(await r.json());
  } catch (e) {
    panel.innerHTML = '<p class="msg">Не удалось проверить доступ: ' + esc(String(e)) + "</p>";
  }
}

function renderAuthPanel(data) {
  const panel = $("#auth-panel");
  if (!panel) return;
  if (data && data.authorized) {
    panel.innerHTML =
      '<div class="auth-ok">✅ Доступ к Stepik активен. ' +
      '<button id="auth-recheck" class="btn btn-secondary btn-sm">Проверить токен</button></div>';
    return;
  }
  const redirect = "http://localhost:8080/callback";
  panel.innerHTML =
    '<div class="auth-form">' +
    '<p class="msg">Для загрузки задач нужен доступ к Stepik — авторизуйтесь прямо здесь, ' +
    "без ручного создания <code>secrets.json</code>.</p>" +
    '<p class="hint">1. Создайте приложение на ' +
    '<a href="https://stepik.org/oauth2/applications/" target="_blank" rel="noopener">' +
    "stepik.org/oauth2/applications</a>: Client type = Confidential, grant type = " +
    "Authorization code, Redirect uris = <code>" +
    redirect +
    "</code>.</p>" +
    '<div class="form-group"><label class="form-label" for="auth-client-id">Client id</label>' +
    '<input class="form-input" id="auth-client-id" type="text" autocomplete="off"></div>' +
    '<div class="form-group"><label class="form-label" for="auth-client-secret">Client secret</label>' +
    '<input class="form-input" id="auth-client-secret" type="password" autocomplete="off"></div>' +
    '<button id="auth-start" class="btn btn-primary">🔑 Авторизоваться в браузере</button>' +
    '<div id="auth-progress" class="hint" hidden></div>' +
    "</div>";
}

async function startBrowserAuth() {
  const idEl = $("#auth-client-id");
  const secEl = $("#auth-client-secret");
  const progress = $("#auth-progress");
  const btn = $("#auth-start");
  const clientId = idEl ? idEl.value.trim() : "";
  const clientSecret = secEl ? secEl.value.trim() : "";
  if (progress) progress.hidden = false;
  if (!clientId || !clientSecret) {
    if (progress) progress.textContent = "Укажите client_id и client_secret.";
    return;
  }
  if (btn) btn.disabled = true;
  if (progress)
    progress.textContent =
      "Открываю браузер… завершите вход в открывшейся вкладке (до 2 минут).";
  try {
    const resp = await fetch("/api/auth/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId, client_secret: clientSecret }),
    });
    const created = await resp.json();
    if (!created.run_id) {
      if (progress) progress.textContent = created.message || "Не удалось запустить авторизацию.";
      if (btn) btn.disabled = false;
      return;
    }
    pollAuth(created.run_id);
  } catch (e) {
    if (progress) progress.textContent = "Ошибка запроса: " + String(e);
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
          progress.textContent = data.message || "Не удалось получить статус авторизации.";
        if (btn) btn.disabled = false;
        return;
      }
      if (data.status === "done") {
        loadAuthStatus(); // перерисует панель как «Доступ активен»
        return;
      }
      if (data.status === "error" || data.status === "cancelled") {
        if (progress)
          progress.textContent =
            data.message || "Не удалось авторизоваться. Проверьте client_id/client_secret.";
        if (btn) btn.disabled = false;
        return;
      }
      setTimeout(step, 800);
    } catch (e) {
      if (progress) progress.textContent = "Ошибка опроса: " + String(e);
      if (btn) btn.disabled = false;
    }
  };
  step();
}

async function downloadTask() {
  const url = $("#downloader-url").value.trim();
  if (!url) return;
  const root = $("#downloader-root").value.trim();
  const btn = $("#downloader-run");
  btn.disabled = true;
  btn.textContent = "Скачивание…";
  $("#downloader-empty").hidden = true;
  const content = $("#downloader-content");
  content.hidden = false;
  content.innerHTML = skeletonBlock();
  try {
    const r = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(root ? { url, root } : { url }),
    });
    const data = await r.json();
    renderDownloaderResult(data);
  } catch (e) {
    content.innerHTML = '<p class="msg">Ошибка запроса: ' + esc(String(e)) + "</p>";
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Скачать";
  }
}


// issue #426 — регистрация раздела «Загрузчик задач» (статус OAuth).
registerSectionHook("downloader", () => loadAuthStatus());

export {
  downloadTask,
  loadAuthStatus,
  startBrowserAuth,
};
