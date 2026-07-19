# HTTP API — справочник (`--serve`)

> Канонический справочник по HTTP API локального веб-интерфейса
> (`python -m stepik_grader.grader --serve`, issue #58 / эпик #80 Tier 1).
> Описывает то, что **реализовано сейчас** в `src/stepik_grader/web/server.py`
> — эндпоинты, параметры, лимиты, коды ответов, примеры curl. Не
> дизайн-документ: замыслы/нереализованные эндпоинты (например
> `POST /api/glossary/export`) — в
> [web-design.md](web-design.md#экспортсинхронизация-глоссария-во-внешний-проект).
> Дизайн будущего сетевого multi-tenant server mode (отдельная тема,
> issue #140/#156/#157) — в [server-mode.md](server-mode.md).
>
> Реализация UI, использующего этот API — [web-current.md](web-current.md).
> Контракт данных ответов (`ResultViewModel`/`ErrorCard`/...) —
> [result-contract.md](result-contract.md) и
> [web-current.md § Контракты данных](web-current.md#контракты-данных).

## Оглавление

- [Общие правила для всех `/api/*`](#общие-правила-для-всех-api)
- [`GET /`](#get-)
- [Статика (`/static/...`)](#статика-static)
- [`GET /api/grade`](#get-apigrade-deprecated-для-benchmicrobench)
- [`GET /api/solutions`](#get-apisolutions)
- [`GET /api/source`](#get-apisource)
- [`GET /api/glossary`](#get-apiglossary)
- [`GET /api/glossary/missing`](#get-apiglossarymissing)
- [`GET /api/glossary/<id>`](#get-apiglossaryid)
- [`GET /api/rules`](#get-apirules)
- [`GET /api/rules/<code>`](#get-apirulescode)
- [`GET /api/insights`](#get-apiinsights)
- [`GET /api/progress`](#get-apiprogress)
- [`POST /api/code-terms`](#post-apicode-terms)
- [`GET /api/commands`](#get-apicommands)
- [`POST /api/download`](#post-apidownload)
- [`POST /api/import-reference`](#post-apiimport-reference)
- [`POST /api/save-solution`](#post-apisave-solution)
- [`POST /api/v1/runs`](#post-apiv1runs)
- [`GET /api/v1/runs/<id>`](#get-apiv1runsid)
- [`POST /api/v1/runs/<id>/cancel`](#post-apiv1runsidcancel)
- [`GET /api/auth/status`](#get-apiauthstatus)
- [`POST /api/auth/start`](#post-apiauthstart)

---

## Общие правила для всех `/api/*`

**Только localhost.** Сервер слушает `127.0.0.1` — не рассчитан на доступ
из сети.

**Host/Origin guard (issue #242, F-03).** Каждый запрос к `/api/*` проходит
`_guard_request()`:
- Заголовок `Host` должен резолвиться в `127.0.0.1`/`localhost`, иначе
  **403** с `message_id: invalid_host` — защита от DNS-rebinding.
- Если есть `Origin` или `Referer` — их hostname должен быть из того же
  allowlist, иначе **403** с `message_id: invalid_origin`. Отсутствие обоих
  заголовков считается допустимым (curl, тесты, не-браузерные клиенты).

**Path-confinement (issue #261).** Пути из запросов (`path`/`folder` в
`/api/grade`, `/api/solutions`, `/api/source`, `/api/save-solution`,
`/api/v1/runs`) резолвятся относительно рабочей директории сервера
(`--root`, по умолчанию — cwd на момент `--serve`) и проверяются на выход
за её пределы (`../`, симлинк наружу, абсолютный путь снаружи) — иначе
**403** с `message_id: path_outside_workspace`. Отключается флагом
`--no-root-confinement` (явный откат пользователя). **Не применяется** к
`POST /api/download` (`root` там — куда скачивать задачу, отдельный
concern).

**Лимиты тела POST (issue #259).** `Content-Length` обязателен — без него
**400** (`content_length_required`). Тело больше `1 MiB` — **413**
(`body_too_large`, поле `limit`). Невалидный JSON — **400**
(`body_invalid_json`). JSON не объект (например список) — **400**
(`body_not_object`).

**Клампы числовых параметров (issue #259).** `repeats` (бенчмарк) кламп'ится
в `[1, 1000]` (дефолт 15); `number` (микро-бенчмарк) — в `[1, 1_000_000]`
(дефолт 1000). Одинаково для query-параметров (`/api/grade`) и JSON `params`
(`/api/v1/runs`).

**Локализация (issue #264, #363).** `?lang=ru|en` (по умолчанию `ru`) — влияет на
`message`/`message_id`/`message_params` в JSON-ответах при ошибках/пустых
результатах, а также на текстовые поля `summary`/`body` карточек глоссария
(`/api/glossary`, `/api/glossary/<id>`, `/api/code-terms` — issue #363). Карточки
хранятся двуязычно (`summary`/`body` — вложенный `{ru, en}`), но в ответе API
отдаются **строкой** выбранной локали с fallback на `ru` — структура ответа не
меняется.

**Формат ответа.** Все `/api/*`-ответы — `application/json; charset=utf-8`,
кроме отмеченных иначе.

---

## `GET /`

Отдаёт `index.html` (SPA) с подставленным `__DEFAULT_PATH__` = текущая
рабочая директория сервера (`server.workspace`).

```
curl http://127.0.0.1:8000/
```

## Статика (`/static/...`)

`/static/app.css`, `/static/app.js`, `/static/vendor/*.mjs` (CodeMirror 6,
issue #265), `/static/fonts/*.woff2` (issue #260) — маленький фиксированный
allowlist, не файловый сервер. Путь не из allowlist → **404** `text/plain`.

## `GET /api/grade` (DEPRECATED для bench/microbench)

Синхронный грейдинг — держит HTTP-запрос открытым на всё время прогона, без
прогресса и без отмены. Для `mode=tests` остаётся основным способом; для
`bench`/`microbench` — **используйте `POST /api/v1/runs`** (issue #262), этот
путь оставлен только для обратной совместимости.

| Параметр | Обязателен | Описание |
|---|---|---|
| `path` | да | Файл или папка с решением(-ями) |
| `mode` | нет (default `tests`) | `tests` \| `bench` \| `microbench` |
| `reference` | нет (только `bench`) | Путь/имя файла-эталона для режима «Сравнение» |
| `repeats` | нет (только `bench`) | Кол-во повторов, кламп `[1,1000]`, дефолт 15 |
| `number` | нет (только `microbench`) | Кол-во вызовов timeit, кламп `[1,1_000_000]`, дефолт 1000 |

Пустой `path` возвращает **200** с `{"kind": "error", ...}` в теле (не 4xx —
это осознанный контракт: клиент должен читать `kind`, не HTTP-статус, для
этого конкретного случая). Confinement-нарушение — 403 (см. выше).

```
curl "http://127.0.0.1:8000/api/grade?path=task_1.py&mode=tests"
curl "http://127.0.0.1:8000/api/grade?path=.&mode=bench&repeats=15"
```

## `GET /api/solutions`

Список файлов-решений в папке — пикер режима 1 «Один файл» (issue
#125-fix).

| Параметр | Обязателен |
|---|---|
| `path` | да |

```
curl "http://127.0.0.1:8000/api/solutions?path=."
```

## `GET /api/source`

Исходник файла-решения — показ кода перед запуском (issue #125-fix).

| Параметр | Обязателен |
|---|---|
| `path` | да |

```
curl "http://127.0.0.1:8000/api/source?path=task_1.py"
```

## `GET /api/glossary`

Поиск и фильтрация карточек глоссария (issue #125, грани — issue #329).
Грани комбинируются (логическое И); разделы **не** объединяются — «Списки
(list)» и «Кортежи (tuple)» фильтруются раздельно.

| Параметр | Обязателен | Значение |
|---|---|---|
| `q` | нет | подстрока по `id`/`title`/`aliases`/`keywords`/`tags` (пусто = все) |
| `section` | нет | точное имя раздела (напр. `Кортежи (tuple)`) |
| `kind` | нет | `function` / `exception` / `construct` / `term` |
| `status` | нет | `new` / `draft` / `ready` / `exported` |
| `sort` | нет | `az` (A–Я) / `section` (раздел→A–Я) / `version` (версионированные вперёд) |
| `lang` | нет | `ru`/`en` (issue #363) — локаль `summary`/`body` в ответе (fallback `ru`) |

```
curl "http://127.0.0.1:8000/api/glossary?q=ZeroDivisionError"
curl "http://127.0.0.1:8000/api/glossary?q=sorted&lang=en"
curl "http://127.0.0.1:8000/api/glossary?section=Кортежи%20(tuple)&sort=az"
curl "http://127.0.0.1:8000/api/glossary?kind=exception&sort=az"
```

## `GET /api/glossary/missing`

Очередь пополнения глоссария (J7 — неизвестные исключения без карточки).
Без параметров.

```
curl http://127.0.0.1:8000/api/glossary/missing
```

## `GET /api/glossary/<id>`

Одна карточка по id.

**200** карточка, либо **404** `{"kind": "error", message_id:
glossary_card_not_found}`.

```
curl http://127.0.0.1:8000/api/glossary/ZeroDivisionError
```

## `GET /api/rules`

Карточки правил PEP 8 (issue #348, эпик #342). Параметры: `q` — поиск по
id/title/tags (подстрока), `tag` — точное совпадение метки.

**200** — список карточек (`id`, `title`, `summary`, `body`, `pep_url`,
`severity`, `status`, `tags`, `example_bad`, `example_good`, `violated`).
`violated` (issue #403) — нарушал ли пользователь это правило лично (из истории
lint; `false` без истории/нарушений).

```
curl "http://127.0.0.1:8000/api/rules?q=E501"
curl "http://127.0.0.1:8000/api/rules?tag=imports"
```

## `GET /api/rules/<code>`

Одна карточка правила по коду. **200** карточка, либо **404** `{"kind":
"error", message_id: rule_card_not_found}`.

```
curl http://127.0.0.1:8000/api/rules/E501
```

## `GET /api/insights`

Карточки «Подучить» из истории прогонов (issue #348, эпик #342): частые ошибки
и их затухание. **200** — список `{key, category, status, hits,
runs_considered, glossary_id}`; `status` ∈ `active|fading|watch` (архивные
скрыты). Пустая/отсутствующая история → `[]`. Читает `.grader_history.db` из
рабочей папки сервера (opt-in `--history`).

```
curl http://127.0.0.1:8000/api/insights
```

## `GET /api/progress`

Агрегатный отчёт прогресса из истории (issue #538/#432) — тот же движок, что CLI
`--export-progress` (`progress_export.build_progress_report`). **200** — объект:

- `schema` — версия формата отчёта;
- `total_runs` — число прогонов в истории;
- `total_tasks`/`solved_tasks` — сводные счётчики задач;
- `tasks` — TTFG по задачам:
  `{task_key, attempts, solved, total_runs, seconds_to_first_ac}`
  (`seconds_to_first_ac` — `null`, если задача ещё не решена);
- `verdicts` — тали вердиктов кейсов (`{"AC": n, "WA": n, ...}`);
- `failure_kinds` — тали ключей падений (`{"timeout": n, ...}`).

Пустая/отсутствующая история → отчёт с нулевыми счётчиками (не ошибка, не 500).
Считается на лету из `.grader_history.db`, без хранимого состояния; раздел
«Прогресс» web-оболочки рендерит эти KPI (issue #538). Исходники решений в отчёт
не попадают.

```
curl http://127.0.0.1:8000/api/progress
```

## `POST /api/code-terms`

Мини-карточки глоссария для функций/конструкций, найденных в коде (issue
#321/#322/#367, панель «Функции в коде» песочницы и **режима 1** — после #366
в режиме 2/папке панель скрыта). Тело — либо `{"code": "..."}` (режим 1/
песочница, debounce при редактировании), либо `{"path": "..."}` (по выбранному
в пикере файлу решения — путь конфайнится в workspace и читается).

Сканирует код (`scan_code_concepts`) — наборы builtin'ов и методов теперь
**inventory-driven** из `stdlib_inventory` (issue #367, шире узкого
`CODE_TERM_BUILTINS`: `frozenset`/`super`/`hash`/`removeprefix`/…), плюс
разворот цепочки вызовов stdlib с корректным разбором `os.path.join` (≠ метод
`str.join`) и синтаксические конструкции (comprehensions, lambda, срез,
f-строка, распаковка, тернарный, walrus, декоратор, with, try). Сопоставляет с
карточками базы по
`id`/`aliases`/«хвосту id» (`split` → карта `str.split`; при конфликте типов
предпочитается `str`→`list`→`dict`→…). **200** `{"terms": [{"id", "title",
"summary", "kind", "has_card", "url", "confidence", "snippet"}]}` — **все**
распознанные концепции: покрытые (`has_card=true`) несут данные карточки,
непокрытые (`has_card=false`) — сам концепт (панель рисует их приглушённо);
методы — `confidence="low"` (тип получателя статически неизвестен). Порядок:
покрытые вперёд, затем по `title`. Синтаксически некорректный код / нет
знакомых функций → `{"terms": []}`.

Побочный эффект `{"path"}`-запроса (practice-driven канал): заметные функции
решения без карточки дозаписываются в очередь «Недостающее»
(`glossary_missing`, дедуп по `concept`).

```
curl -X POST http://127.0.0.1:8000/api/code-terms \
  -H "Content-Type: application/json" \
  -d '{"code": "xs = sorted([3, 1, 2])"}'
```

## `GET /api/commands`

Реестр команд Command Palette (Ctrl+K, issue #125), отфильтрованный по
тегам контекста.

| Параметр | Обязателен |
|---|---|
| `context` | нет — CSV тегов, например `grade,bench`; пусто = весь реестр |

```
curl "http://127.0.0.1:8000/api/commands?context=grade"
```

## `POST /api/download`

Скачать задачу+тесты со Stepik по URL шага (issue #186, раздел «Загрузчик
задач»). **Не** проходит через path-confinement (`root` — отдельный
concern).

Тело JSON: `{"url": "...", "root"?: "..."}`.

- `url` пустой → **400** `specify_url`.
- Успех → **200** `DownloadedTask` (`{"ok", "path", "files", "tests":
  {"count","source","format"}, "message"}`) — `ok=false` при ошибке
  сети/OAuth/битого URL; `ok=true` с пустыми `tests` и предупреждением в
  `message`, если тесты не нашлись (файлы задачи всё равно скачаны).

```
curl -X POST http://127.0.0.1:8000/api/download \
  -H "Content-Type: application/json" \
  -d '{"url": "https://stepik.org/lesson/.../step/1"}'
```

## `POST /api/import-reference`

Импортировать закреплённое решение Stepik (+ топовые по лайкам) из ветки
solutions шага в папку задачи как `task{N}_{100+}.py` — reference-competitor
для режимов 2–4 (issue #55). Читает `meta.json` из папки, код тянет через
`/api/comments?...&expand=submission`. `path` конфайнится в workspace.

Тело JSON: `{"path": "<папка задачи>", "top"?: <N=5>}`.

- `path` пустой → **400** `specify_folder`; вне workspace → **403**.
- Успех → **200** `{"ok": true, "files": ["task{N}_100.py", ...], "message"}`;
  `ok=false` с понятным `message` при ошибке (нет `secrets.json`/OAuth без
  браузера, нет ветки решений, решений нет, код не извлёкся) — HTTP всё равно
  **200**, как у `/api/download`.

```
curl -X POST http://127.0.0.1:8000/api/import-reference \
  -H "Content-Type: application/json" \
  -d '{"path": "StepikTasks/module2/task3", "top": 5}'
```

## `POST /api/save-solution`

Явно сохранить код решения на диск — кнопка «Сохранить» режима 1 (доделка
#125; отделена от грейда в issue #297). Грейд («Проверить») больше НЕ вызывает
этот эндпоинт — код исполняется из временного файла через `POST /api/v1/runs`
(см. ниже), запись на диск только по явному «Сохранить».

Тело JSON: `{"folder": "...", "path"?: "...", "code": "...",
"expected_mtime"?: <float>}`.

- `folder` пустой → **400** `specify_folder`.
- `code` не строка → **400** `specify_code`.
- `folder`/`path` вне workspace → **403** `path_outside_workspace`.
- `path` задан → перезаписывает существующий файл; не задан/пусто →
  создаётся новый файл в `folder` по маске (`task_N.py`/расширение
  существующей серии).
- `expected_mtime` (опционально, optimistic locking issue #297) — если задан
  и `path` указывает на существующий файл, чей фактический `mtime` расходится
  (допуск 1 мс) — запись НЕ выполняется: **200** `{"ok": false, "conflict":
  true, message_id: file_changed_on_disk}`. Для нового файла (`path` пуст) не
  применяется. Повторный вызов без `expected_mtime` перезаписывает.
- Успех → **200** `{"ok": true, "path": "...", "mtime": <float>}` (`mtime` —
  новый baseline для клиента); `OSError` при записи → **200** `{"ok": false,
  message_id: file_save_failed}` (не 5xx — graceful).

```
curl -X POST http://127.0.0.1:8000/api/save-solution \
  -H "Content-Type: application/json" \
  -d '{"folder": ".", "code": "print(input())"}'
```

## `POST /api/v1/runs`

Поставить tests/bench/microbench в очередь async job (issue #262/#297) —
асинхронная замена `GET /api/grade`. Возвращает `run_id` немедленно, не
дожидаясь завершения; прогресс/результат — через `GET /api/v1/runs/<id>`.

Тело JSON: `{"path": "...", "code"?: "...", "mode":
"tests"|"bench"|"microbench", "params"?: {"repeats"?, "reference"?,
"number"?}}`.

- `path` пустой → **400** `specify_path_file_or_folder`.
- `mode` не `tests`/`bench`/`microbench` → **400** `invalid_run_mode`.
- `path` вне workspace → **403** `path_outside_workspace`.
- Активных (нетерминальных) job'ов уже `CONFIG.max_active_runs` (дефолт 20,
  настройка сервера через `pyproject.toml`) → **429** `too_many_runs` (поле
  `limit`), issue #429. Back-pressure общий для всех kind (tests/bench/
  microbench/playground/trace/auth) — реестр `_JOBS`/очередь executor'а не
  растут без отказа. После завершения любых job'ов submit снова проходит.
- `code` (опционально) — исполняемое содержимое из временного файла рядом с
  `path`, БЕЗ записи в целевой файл (редактируемое окно режима 1, issue #297 —
  «Проверить» не пишет на диск, гонки save→grade между окнами нет); только
  для одного файла. `path` тут может быть как файлом, так и папкой (для
  несохранённого нового кода — папка, temp кладётся в неё, `tests/` резолвится
  там же).
- `mode="tests"` (issue #297) — грейд корректности (тот же результат, что у
  `GET /api/grade?mode=tests`), без числовых `params`.
- `mode="playground"` (issue #317) — раздел «Песочница»: тело
  `{"mode":"playground","code":"...","stdin"?:"..."}` **без** `path` и без
  тестов. Пустой/пробельный `code` → **400** `specify_code`. Результат job'ы
  (в `GET /api/v1/runs/<id>` → `result`) — `{"status":
  "OK"|"RE"|"TLE"|"CANCELLED", "stdout", "stderr", "exit_code", "duration_ms",
  "truncated"}`; никакой сверки с ожидаемым выводом.
- `mode="trace"` (issue #318) — пошаговый трейс исполнения: то же тело
  `{"code","stdin"?}` без `path`. `result` — JSON-трейс `{steps, stdout,
  truncated, error}` (кадры стека + heap объектов со ссылками по id, aliasing);
  формат — в [trace-format.md](trace-format.md).
- Успех → **202** `{"run_id": "...", "status": "queued"}`.

```
# режим 1 (корректность) с кодом в теле, без записи на диск:
curl -X POST http://127.0.0.1:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"path": "task.py", "code": "print(input())", "mode": "tests"}'

# режим 3 (bench):
curl -X POST http://127.0.0.1:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"path": ".", "mode": "bench", "params": {"repeats": 15}}'
```

## `GET /api/v1/runs/<id>`

Статус/прогресс/результат job'ы.

**200** `{"status": "queued"|"running"|"done"|"error"|"cancelled",
"progress": {"done","total"}, "result": ...}` — плюс `message`/`message_id`/
`message_params` при `status="error"` или `status="cancelled"`
(`message_id="run_cancelled"`). Отмена пользователем — отдельный терминальный
статус (issue #296), не `"error"`: семантически это не провал решения/
грейдера, клиенты не должны ретраить `"cancelled"` так же, как настоящую
ошибку.

- `id` пустой или содержит `/` → **404** `text/plain`.
- `id` не найден (или истёк TTL, 15 минут после завершения) → **404**
  `{"kind": "error", message_id: run_not_found}`.

```
curl http://127.0.0.1:8000/api/v1/runs/<run_id>
```

## `POST /api/v1/runs/<id>/cancel`

Best-effort отмена job'ы — выставляет сигнал и возвращает немедленно, не
дожидаясь фактической остановки дочернего процесса.

**200** `job.to_status_dict()` (тот же формат, что у `GET
/api/v1/runs/<id>`), если job существует — **включая уже завершённую**
(`done`/`error`/`cancelled`): повторный `cancel` идемпотентен, просто
возвращает статус без побочного эффекта. **404** `{"kind": "error",
message_id: run_not_found}` — только если job не найдена или истёк её TTL
(15 минут после завершения).

```
curl -X POST http://127.0.0.1:8000/api/v1/runs/<run_id>/cancel
```

## `POST /api/v1/hint`

AI-объяснение упавшего кейса как async job (issue #543, ADR-0003, эпик E3) —
opt-in BYOK через OpenAI-совместимый endpoint. Возвращает `run_id`; результат
(`{"hint": str|null, "configured": bool}`) — через `GET /api/v1/runs/<id>`.
Контекст (verdict/ввод-вывод/diff/ошибка + код решения) заземляет промпт общим
core-хелпером `build_failure_context` (issue #542).

Тело JSON: `{"verdict": "...", "stdin"?, "expected"?, "actual"?, "diff"?,
"error"?, "path"?|"code"?, "consent"?, "lang"?}`. `path` (в workspace) →
сервер читает код решения для заземления; иначе `code` из тела
(playground/инлайн).

- **Приватность (обязательное согласие):** подсказка отправляет ваш код и
  ввод-вывод AI-провайдеру. Без согласия — **403** `consent_required`, в сеть
  НИЧЕГО не уходит (job не ставится, провайдер не вызывается). `"consent": true`
  в теле фиксирует однократное согласие в `.grader_settings.json`
  (`ai_hint_consent`) рабочей директории — далее не требуется. Рекомендация:
  локальный ollama (данные не покидают машину); для несовершеннолетних — согласие
  представителя.
- `path` вне workspace → **403** `path_outside_workspace`.
- Активных job'ов уже `CONFIG.max_active_runs` → **429** `too_many_runs`.
- Провайдер не настроен (`ai_base_url`/`ai_model` пусты) → job завершается с
  `{"hint": null, "configured": false}` (graceful skip; грейдинг не затрагивается,
  в сеть ничего не уходит).
- Успех → **202** `{"run_id": "...", "status": "queued"}`.

```
# после согласия — объяснить WA-кейс режима 1:
curl -X POST http://127.0.0.1:8000/api/v1/hint \
  -H "Content-Type: application/json" \
  -d '{"path": "task.py", "verdict": "WA", "stdin": "4", "expected": "5", "actual": "6", "consent": true}'
```

---

## `GET /api/auth/status`

Статус OAuth-авторизации Stepik по `secrets.json` в рабочей директории сервера
(issue #402). Читает только локальный файл, сети не касается.

**200** `{"authorized": bool, "reason": "ok"|"no_token"|"no_secrets"}`:
`ok` — валидный токен; `no_token` — креды есть, но токена нет/истёк (нужен
браузерный flow); `no_secrets` — файла нет или креды неполные (нужна форма).
Битый/нечитаемый файл трактуется как `no_secrets` (best-effort, не 500).

```
curl http://127.0.0.1:8000/api/auth/status
```

## `POST /api/auth/start`

Запустить браузерный OAuth-flow первого запуска (issue #402). Тело —
`{"client_id": "...", "client_secret": "...", "redirect_uri"?: "..."}`
(`redirect_uri` по умолчанию `http://localhost:8080/callback`). Пишет креды в
`secrets.json` (0600) и стартует loopback-OAuth как async-job (`kind="auth"`);
опрос прогресса/итога — через `GET /api/v1/runs/<id>` (как у bench/microbench).

- Успех → **202** `{"run_id": "...", "status": "queued"}`; job по завершении —
  `{"status": "done", "result": {"authorized": true, "reason": "ok"}}`.
- Нет `client_id`/`client_secret` → **400** `{"kind": "error", message_id:
  specify_oauth_creds}`.
- Общие POST-гарды (`Content-Length`, Host/Origin/Fetch-Metadata) — как у всех
  POST `/api/*`.

`webbrowser.open` открывается на **машине сервера** — предназначено для
локального `--serve` (localhost, single-user); для удалённого сервера (#151,
не в scope) не применимо.

```
curl -X POST http://127.0.0.1:8000/api/auth/start \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"...","client_secret":"..."}'
```

---

Любой путь, не подошедший ни под один маршрут выше — **404** `text/plain`
`"not found"`.
