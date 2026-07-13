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
- [`GET /api/commands`](#get-apicommands)
- [`POST /api/download`](#post-apidownload)
- [`POST /api/save-solution`](#post-apisave-solution)
- [`POST /api/v1/runs`](#post-apiv1runs)
- [`GET /api/v1/runs/<id>`](#get-apiv1runsid)
- [`POST /api/v1/runs/<id>/cancel`](#post-apiv1runsidcancel)

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

**Локализация (issue #264).** `?lang=ru|en` (по умолчанию `ru`) — влияет на
`message`/`message_id`/`message_params` в JSON-ответах при ошибках/пустых
результатах. Не влияет на структуру данных.

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
прогресса и без отмены. Для `mode=tests`/`mode=file` остаётся основным
способом; для `bench`/`microbench` — **используйте `POST /api/v1/runs`**
(issue #262), этот путь оставлен только для обратной совместимости.

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

```
curl "http://127.0.0.1:8000/api/glossary?q=ZeroDivisionError"
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
/api/v1/runs/<id>`), либо **404** `{"kind": "error", message_id:
run_not_found}`, если job не найдена или уже завершена
(`done`/`error`/`cancelled`) — повторный `cancel` уже отменённой job'ы тоже
не ошибка, просто `to_status_dict()` без побочного эффекта.

```
curl -X POST http://127.0.0.1:8000/api/v1/runs/<run_id>/cancel
```

---

Любой путь, не подошедший ни под один маршрут выше — **404** `text/plain`
`"not found"`.
