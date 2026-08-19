# HTTP API — справочник (`--serve`)

> Канонический справочник по HTTP API локального веб-интерфейса
> (`python -m stepik_grader.grader --serve`).
> Описывает то, что **реализовано сейчас** в `src/stepik_grader/web/server.py`
> — эндпоинты, параметры, лимиты, коды ответов, примеры curl. Не
> дизайн-документ: замыслы/нереализованные эндпоинты (например
> `POST /api/glossary/export`) — в
> [web-design.md](design/web-design.md#экспортсинхронизация-глоссария-во-внешний-проект).
> Дизайн будущего сетевого multi-tenant server mode (отдельная тема) — в
> [server-mode.md](design/server-mode.md).
>
> Реализация UI, использующего этот API — [web-contracts.md](web-contracts.md).
> Контракт данных ответов (`ResultViewModel`/`ErrorCard`/...) —
> [result-contract.md](result-contract.md) и
> [web-contracts.md § Контракты данных](web-contracts.md#контракты-данных).

## Оглавление

- [Общие правила для всех `/api/*`](#общие-правила-для-всех-api)
- [`GET /`](#get-)
- [Статика (`/static/...`)](#статика-static)
- [`GET /api/grade`](#get-apigrade-deprecated-для-benchmicrobench)
- [`GET /api/solutions`](#get-apisolutions)
- [`GET /api/source`](#get-apisource)
- [`GET /api/task/statement`](#get-apitaskstatement)
- [`GET /api/task/image`](#get-apitaskimage)
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
- [`POST /api/feedback`](#post-apifeedback)
- [`POST /api/import-reference`](#post-apiimport-reference)
- [`POST /api/save-solution`](#post-apisave-solution)
- [`POST /api/v1/runs`](#post-apiv1runs)
- [`GET /api/v1/runs/<id>`](#get-apiv1runsid)
- [`POST /api/v1/runs/<id>/cancel`](#post-apiv1runsidcancel)
- [`POST /api/v1/hint`](#post-apiv1hint)
- [`POST /api/v1/settings`](#post-apiv1settings)
- [`POST /api/stepik/submit`](#post-apistepiksubmit)
- [`GET /api/auth/status`](#get-apiauthstatus)
- [`GET /api/downloader/config`](#get-apidownloaderconfig)
- [`POST /api/downloader/config`](#post-apidownloaderconfig)
- [`POST /api/auth/start`](#post-apiauthstart)

---

## Общие правила для всех `/api/*`

**Только localhost.** Сервер слушает `127.0.0.1` — не рассчитан на доступ
из сети.

**Host/Origin guard.** Каждый запрос к `/api/*` проходит
`_guard_request()`:
- Заголовок `Host` должен резолвиться в `127.0.0.1`/`localhost` **и нести порт,
  на котором сервер слушает**, иначе **403** с `message_id: invalid_host` —
  защита от DNS-rebinding.
- Если есть `Origin` или `Referer` — их hostname и порт должны совпадать с
  адресом сервера, иначе **403** с `message_id: invalid_origin`. Отсутствие
  обоих заголовков считается допустимым (curl, тесты, не-браузерные клиенты).
- `Sec-Fetch-Site: cross-site` и `same-site` отклоняются. Порт входит в
  проверку именно из-за `same-site`: для браузера все `localhost:*` — один
  site, поэтому без него страница на соседнем локальном порту (чужой
  dev-сервер, Jupyter) проходила гард и могла исполнить произвольный код через
  `POST /api/v1/runs`.

**`GET /`** тоже проходит проверку `Host` (но не Origin — страницу открывают
прямым переходом): она несёт абсолютный путь рабочей директории и флаг наличия
OS-изоляции. Статика (`/static/*`) остаётся без гарда — данных о машине
пользователя в ней нет.

**Path-confinement.** Пути из запросов (`path`/`folder` в
`/api/grade`, `/api/solutions`, `/api/source`, `/api/save-solution`,
`/api/v1/runs`) резолвятся относительно рабочей директории сервера
(`--root`, по умолчанию — cwd на момент `--serve`) и проверяются на выход
за её пределы (`../`, симлинк наружу, абсолютный путь снаружи) — иначе
**403** с `message_id: path_outside_workspace`. Отключается флагом
`--no-root-confinement` (явный откат пользователя). **Не применяется** к
`POST /api/download` (`root` там — куда скачивать задачу, отдельный
concern).

**Лимиты тела POST.** `Content-Length` обязателен — без него
**400** (`content_length_required`). Тело больше `1 MiB` — **413**
(`body_too_large`, поле `limit`). Невалидный JSON — **400**
(`body_invalid_json`). JSON не объект (например список) — **400**
(`body_not_object`).

**Клампы числовых параметров.** `repeats` (бенчмарк) кламп'ится
в `[1, 1000]` (дефолт 15); `number` (микро-бенчмарк) — в `[1, 1_000_000]`
(дефолт 1000). Одинаково для query-параметров (`/api/grade`) и JSON `params`
(`/api/v1/runs`).

**Локализация.** `?lang=ru|en` (по умолчанию `ru`) — влияет на
`message`/`message_id`/`message_params` в JSON-ответах при ошибках/пустых
результатах, а также на текстовые поля `summary`/`body` карточек глоссария
(`/api/glossary`, `/api/glossary/<id>`, `/api/code-terms`). Карточки
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

Фиксированный allowlist, а не файловый сервер: путь не из него → **404**
`text/plain`. Всё содержимое читается один раз при импорте модуля.

| Путь | Что отдаётся |
|---|---|
| `/static/app.css` | стили оболочки |
| `/static/<имя>.js` | каждый файл `static/*.js` — `app.js` (entry) и извлечённые ES-модули (`core`, `grade`, `content`, `downloader`, `sandbox`, `trace-player`, `feedback`); список собирается `glob`-сканом при импорте, новый модуль подхватывается без правки кода |
| `/static/locales/ui.json` | каталог UI-строк `ru`/`en` для `applyUiLocale()` |
| `/static/vendor/*.mjs` | вендоренный бандл CodeMirror 6 |
| `/static/fonts/*.woff2` | Inter и JetBrains Mono (subset) |

## `GET /api/grade` (DEPRECATED для bench/microbench)

Синхронный грейдинг — держит HTTP-запрос открытым на всё время прогона, без
прогресса и без отмены. Для `mode=tests` остаётся основным способом; для
`bench`/`microbench` — **используйте `POST /api/v1/runs`**, этот
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

Строка режима `bench` несёт метрики дважды: `min`/`median`/`mean`/`max`/`stdev`
— строками с автовыбором единиц («144.812 ms», для таблицы) и `min_s`/
`median_s`/`max_s` — числами в секундах (диаграмме сравнения нужны
сырые значения, парсить строку обратно было бы восстановлением потерянного).
Режим `microbench` отдаёт числа с самого начала — `*_us`.

```
curl "http://127.0.0.1:8000/api/grade?path=task_1.py&mode=tests"
curl "http://127.0.0.1:8000/api/grade?path=.&mode=bench&repeats=15"
```

## `GET /api/solutions`

Список файлов-решений в папке — пикер режима 1 «Один файл».

| Параметр | Обязателен |
|---|---|
| `path` | да |

```
curl "http://127.0.0.1:8000/api/solutions?path=."
```

## `GET /api/source`

Исходник файла-решения — показ кода перед запуском.

| Параметр | Обязателен |
|---|---|
| `path` | да |

```
curl "http://127.0.0.1:8000/api/source?path=task_1.py"
```

## `GET /api/task/statement`

Условие скачанной задачи для показа в браузере: шапка из `meta.json`, очищенное
тело, список вложений. В сеть не ходит — отдаёт то, что уже на диске.

| Параметр | Обязателен |
|---|---|
| `path` | да — каталог **задачи**, не файл |

Тело условия проходит серверную очистку по whitelist тегов. Чистка выполняется
при показе, а не при скачивании: правила будут меняться, и менять их не должно
означать «перекачай сорок задач». Сырой источник остаётся в `task.html`.

`img src` подставляется только на приехавшие вложения-картинки и указывает на
`GET /api/task/image`; внешние источники не подставляются — их всё равно
блокирует CSP, а ходить в сеть при показе скачанной задачи незачем.

Ответ: `kind: "statement"` с полями `header` / `html` / `attachments`, либо
`kind: "empty"` с `reason` (`no_statement` — условия нет, `not_a_task_dir` —
такого каталога нет). Пустая строка вместо явного «условия нет» неотличима на
клиенте от «условие загрузилось и оказалось пустым».

```
curl "http://127.0.0.1:8000/api/task/statement?path=StepikTasks/course/section/lesson/04"
```

## `GET /api/task/image`

Байты картинки-вложения, на которую ссылается условие.

| Параметр | Обязателен |
|---|---|
| `path` | да — каталог задачи |
| `name` | да — имя файла из списка вложений `meta.json` |

Ручка нужна потому, что в браузере `src="pic.png"` разрешается относительно
корня сервера, а не каталога задачи.

`name` **сверяется со списком вложений** и не используется как путь; расширение
обязано быть картиночным (`png`/`jpg`/`jpeg`/`gif`/`webp`/`bmp`). SVG не
отдаётся: он умеет носить скрипт, а ответ идёт со своего origin — это обошло бы
всю очистку HTML. Не прошедшее проверку — `404`, а не `403`: существование
чужого файла не подтверждается.

```
curl "http://127.0.0.1:8000/api/task/image?path=StepikTasks/course/section/lesson/04&name=pic.png"
```

## `GET /api/glossary`

Поиск и фильтрация карточек глоссария.
Грани комбинируются (логическое И); разделы **не** объединяются — «Списки
(list)» и «Кортежи (tuple)» фильтруются раздельно.

| Параметр | Обязателен | Значение |
|---|---|---|
| `q` | нет | подстрока по `id`/`title`/`aliases`/`keywords`/`tags`, `summary`/`body` (RU+EN) и `syntax`/`examples`; пусто = все |
| `section` | нет | точное имя раздела (напр. `Кортежи (tuple)`) |
| `kind` | нет | `function` / `exception` / `construct` / `term` |
| `status` | нет | `new` / `draft` / `ready` / `exported` |
| `group` | нет | семейство разделов: `modules` (все разделы «Модуль X») / `types` (встроенные типы) / `syntax` (конструкции языка) / `builtins` (встроенные функции и исключения) / `io` (ввод-вывод и файлы) / `algorithms` / `other` (разделы без явного семейства); неизвестное значение игнорируется |
| `sort` | нет | `relevance` (качество совпадения с `q`, при пустом `q` = `az`) / `az` (A–Я) / `section` (раздел→A–Я) / `version` (версионированные вперёд) |
| `lang` | нет | `ru`/`en` — локаль `summary`/`body` в ответе (fallback `ru`) |

Каждая карточка ответа несёт два вычисляемых сервером поля:
`group` — семейство её раздела (UI строит по нему и кнопки семейств, и список
разделов внутри раскрытого, не повторяя правило у себя) и `section_label` —
подпись раздела на языке `?lang=` (`Модуль math` → `Module math`), тогда как
само `section` остаётся серверным **значением фильтра** и не переводится.
Семейства покрывают **все** разделы базы (неклассифицированный раздел падает в
`other`), потому что в UI они заменили собой селект «Раздел». Те же два поля
отдаёт и [`GET /api/glossary/<id>`](#get-apiglossaryid).

```
curl "http://127.0.0.1:8000/api/glossary?q=ZeroDivisionError"
curl "http://127.0.0.1:8000/api/glossary?q=sorted&lang=en"
curl "http://127.0.0.1:8000/api/glossary?section=Кортежи%20(tuple)&sort=az"
curl "http://127.0.0.1:8000/api/glossary?kind=exception&sort=az"
curl "http://127.0.0.1:8000/api/glossary?group=modules&sort=az"
curl "http://127.0.0.1:8000/api/glossary?q=split&sort=relevance"
```

## `GET /api/glossary/missing`

Очередь пополнения глоссария (неизвестные исключения без карточки).
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

Карточки правил PEP 8. Параметры: `q` — поиск по
id/title/tags (подстрока), `tag` — точное совпадение метки.

**200** — список карточек (`id`, `title`, `summary`, `body`, `pep_url`,
`severity`, `status`, `tags`, `example_bad`, `example_good`, `violated`).
`violated` — нарушал ли пользователь это правило лично (из истории
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

Карточки «Подучить» из истории прогонов: частые ошибки
и их затухание. **200** — список `{key, category, status, hits,
runs_considered, glossary_id}`; `status` ∈ `active|fading|watch` (архивные
скрыты). Пустая/отсутствующая история → `[]`. Читает `.grader_history.db` из
рабочей папки сервера (opt-in `--history`).

```
curl http://127.0.0.1:8000/api/insights
```

## `GET /api/progress`

Агрегатный отчёт прогресса из истории — тот же движок, что CLI
`--export-progress` (`progress_export.build_progress_report`). **200** — объект:

- `schema` — версия формата отчёта;
- `total_runs` — число прогонов в истории;
- `total_tasks`/`solved_tasks` — сводные счётчики задач;
- `tasks` — TTFG по задачам:
  `{task_key, attempts, solved, total_runs, seconds_to_first_ac}`
  (`seconds_to_first_ac` — `null`, если задача ещё не решена; считаются только
  прогоны проверки, режимы 1/2). `task_key` — идентификатор шага
  (`step:<id>` из `meta.json` папки задачи), устойчивый к переименованию и
  переезду папки; для задач, скачанных не downloader'ом, — путь папки
  относительно workspace, а если задача и есть workspace, её имя, чтобы ключ
  не зависел от каталога запуска. Человеку показывается `display_name` —
  имя папки, обновляемое на каждом прогоне;
- `verdicts` — тали вердиктов кейсов (`{"AC": n, "WA": n, ...}`);
- `failure_kinds` — тали ключей падений (`{"timeout": n, ...}`).

Пустая/отсутствующая история → отчёт с нулевыми счётчиками (не ошибка, не 500).
Тали вердиктов считаются на лету из `.grader_history.db`, а `tasks` — из
агрегата `task_progress` той же базы (он ведётся при записи прогонов, поэтому
удаление старых записей не занижает «попытки до первого зачёта»); раздел
«Прогресс» web-оболочки рендерит эти KPI. Исходники решений в отчёт
не попадают.

```
curl http://127.0.0.1:8000/api/progress
```

## `POST /api/code-terms`

Мини-карточки глоссария для функций/конструкций, найденных в коде (панель
«Функции в коде» песочницы и **режима 1** — в режиме 2/папке панель
скрыта). Тело — либо `{"code": "..."}` (режим 1/
песочница, debounce при редактировании), либо `{"path": "..."}` (по выбранному
в пикере файлу решения — путь конфайнится в workspace и читается).

Сканирует код (`scan_code_concepts`) — наборы builtin'ов и методов теперь
**inventory-driven** из `stdlib_inventory` (шире узкого
`CODE_TERM_BUILTINS`: `frozenset`/`super`/`hash`/`removeprefix`/…), плюс
разворот цепочки вызовов stdlib с корректным разбором `os.path.join` (≠ метод
`str.join`) и синтаксические конструкции (comprehensions, lambda, срез,
f-строка, распаковка, тернарный, walrus, декоратор, with, try).

Охват — «любое совпадение с глоссарием, кроме имён
переменных»:

- **исключения, названные в коде** — `raise ValueError(...)`, `except (KeyError,
  TypeError):`, конструктор `RuntimeError("...")` (`kind: "exception"`); раньше
  не распознавались вовсе, хотя карточек исключений в базе больше сотни;
- **атрибуты импортированных модулей без вызова** — `math.pi`, `sys.argv`,
  `string.ascii_lowercase` (`kind: "attribute"`); записывается только внешнее
  звено цепочки, поэтому `os.path.join` не порождает лишний термин `os.path`;
- **методы stdlib-классов** — `Path.exists()`, `Path.read_text()`: имена таких
  методов инвентарь не знает, поэтому берутся из самой базы (id вида
  `Класс.метод`, где первый сегмент — не имя stdlib-модуля, иначе `math.sqrt`
  превратился бы в «метод `sqrt`»);
- **голые ссылки на имена с карточкой** (`kind: "name"`) — не только вызовы, но
  и `isinstance(x, int)`, аннотация `c: Counter`, база класса `class F(Enum)`;
  набор распознаваемых имён — bare-id всех карточек (`str`/`int`/`Counter`/
  `defaultdict`/`NamedTuple`/…), поэтому «любое имя с карточкой», а не курируемый
  список. Имя-функция вызова не задваивается ссылкой; имя, затенённое
  присваиванием (`int = 5`), считается переменной и не выводится;
- **ключевые конструкции** — `for`/`while`/`if`/`break`/`continue`/`return`/
  `def`/`class`/`assert` (в дополнение к прежним comprehensions/lambda/with/try),
  если на ключевое слово есть карточка.

Сопоставляет с карточками базы по
`id`/`aliases`/«хвосту id» (`split` → карта `str.split`; при конфликте типов
предпочитается `str`→`list`→`dict`→…). **200** `{"terms": [{"id", "title",
"summary", "kind", "has_card", "confidence", "snippet"}]}` — **все**
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

## `POST /api/glossary/hit`

Отметка перехода в карточку глоссария **из ошибки** прогона (issue #1220) —
deep-link из панели разбора, а не открытие раздела «Глоссарий» руками. Тело:
`{"card_id", "failure_kind"?, "error_class"?}`; `failure_kind` — тот же ключ
ошибки, что кладётся в историю (`insights.failure_kind`, приезжает в карточке
кейса), `error_class` — имя исключения при RE.

**200** `{"kind": "glossary_hit", "recorded": bool}`. `recorded=false` — не
отказ, а честный ответ «история выключена»: тумблер один на весь журнал
(`--history`/`--serve --no-history`, ADR-0002), и отдельного согласия эта
запись не заводит. Пустой `card_id` → **400** `glossary_no_card_id`.

Почему POST, а не параметр к `GET /api/glossary/<id>`: у записи есть побочный
эффект, а GET браузер волен повторить или предзагрузить — тогда метрика
«пришёл из ошибки» считала бы кэш и префетчи.

```
curl -X POST http://127.0.0.1:8000/api/glossary/hit \
  -H "Content-Type: application/json" \
  -d '{"card_id": "indexerror", "error_class": "IndexError"}'
```

## `GET /api/commands`

Реестр команд действий (`copy_input`/`copy_output`/…), отфильтрованный по
тегам контекста. Питает inline-кнопки действий в панели разбора
(`renderCommandButtons` в `static/grade.js`) — единственную поверхность:
command palette и нижних сценарных кнопок в приложении нет. Сам эндпоинт и
реестр `COMMANDS` живы.

| Параметр | Обязателен |
|---|---|
| `context` | нет — CSV тегов, например `grade,bench`; пусто = весь реестр |

```
curl "http://127.0.0.1:8000/api/commands?context=grade"
```

## `POST /api/download`

Скачать задачу+тесты со Stepik по URL шага (раздел «Загрузчик
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

## `POST /api/feedback`

Собрать черновик обращения (баг / идея / задача проверяется неправильно).
Возвращает **ссылку на заполненную форму issue** на GitHub
(`.github/ISSUE_TEMPLATE/*.yml`, prefilled query-параметры по `id` полей) и
предпросмотр того, что в неё уйдёт.

Сервер ничего не создаёт и не отправляет: у грейдера нет GitHub-токена, Submit
жмёт сам пользователь в браузере. POST, а не GET, чтобы текст обращения не
оседал в access-логе `http.server` как query-string.

Тело JSON: `{"kind": "bug"|"idea"|"task-problem", "summary"?: "...",
"step_url"?: "...", "logs"?: "..."}`.

- `kind` не распознан → **400** `feedback_unknown_kind`.
- `step_url` учитывается только при `kind=task-problem`, `logs` — только при
  `kind=bug` (в остальных формах таких полей нет).
- Успех → **200** `{"kind", "url", "fields": [{"id","value"}, ...],
  "truncated": [...], "dropped": [...], "discussions_url"}`.
  `fields` — **список** пар (порядок для предпросмотра значим), значения уже
  прошли редакцию секретов и сворачивание домашнего пути в `~`.
  `truncated`/`dropped` — поля, усечённые/выброшенные из-за лимита длины URL
  (`core/feedback.py:MAX_URL_LENGTH`); UI обязан сказать об этом вслух.

Поле `commit` (`git log --oneline -1`, формы `bug`/`task-problem`) заполняется
сервером, когда рабочая директория — git-клон: хеш и subject сразу привязывают
отчёт к точке истории. Нет git (установка через pip), git не установлен или вызов
завис → поля в ответе просто нет, черновик собирается как обычно.

Приватность: окружение собирается на машине пользователя (версия, ОС, Python,
активность `--sandbox`, локаль) **без** имени машины; код решения не
отправляется никогда — его при желании прикладывает сам пользователь уже в
форме. Канон — docstring `core/feedback.py`.

```
curl -X POST http://127.0.0.1:8000/api/feedback \
  -H "Content-Type: application/json" \
  -d '{"kind": "bug", "summary": "падает на пустом вводе"}'
```

## `POST /api/import-reference`

Импортировать закреплённое решение Stepik (+ топовые по лайкам) из ветки
solutions шага в папку задачи как `task{N}_{100+}.py` — reference-competitor
для режимов 2–4. Читает `meta.json` из папки, код тянет через
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

Явно сохранить код решения на диск — кнопка «Сохранить» режима 1, отдельная от
грейда. Грейд («Проверить») НЕ вызывает
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
- `expected_mtime` (опционально, optimistic locking) — если задан
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

Поставить tests/bench/microbench в очередь async job —
асинхронная замена `GET /api/grade`. Возвращает `run_id` немедленно, не
дожидаясь завершения; прогресс/результат — через `GET /api/v1/runs/<id>`.

Тело JSON: `{"path": "...", "code"?: "...", "mode":
"tests"|"bench"|"microbench", "params"?: {"repeats"?, "reference"?,
"number"?}, "limits"?: {"timeout_s"?, "memory_mb"?}}`.

- `path` пустой → **400** `specify_path_file_or_folder`.
- `mode` не `tests`/`bench`/`microbench` → **400** `invalid_run_mode`.
- `path` вне workspace → **403** `path_outside_workspace`; `path` не `.py` →
  **403** `source_not_a_solution` (ручка увозит содержимое файла провайдеру,
  поэтому читает только решения).
- Активных (нетерминальных) job'ов уже `CONFIG.max_active_runs` (дефолт 20,
  настройка сервера через `pyproject.toml`) → **429** `too_many_runs` (поле
  `limit`). Back-pressure общий для всех kind (tests/bench/
  microbench/playground/trace/auth) — реестр `_JOBS`/очередь executor'а не
  растут без отказа. После завершения любых job'ов submit снова проходит.
- `code` (опционально) — исполняемое содержимое из временного файла рядом с
  `path`, БЕЗ записи в целевой файл (редактируемое окно режима 1 —
  «Проверить» не пишет на диск, гонки save→grade между окнами нет); только
  для одного файла. `path` тут может быть как файлом, так и папкой (для
  несохранённого нового кода — папка, temp кладётся в неё, `tests/` резолвится
  там же).
- `mode="tests"` — грейд корректности (тот же результат, что у
  `GET /api/grade?mode=tests`), без числовых `params`.
- `limits` (опционально) — per-run override дефолтов сервера:
  `timeout_s` (зажим в 1..60 с) и `memory_mb` (16..1024 МБ). Мусор/отсутствие →
  дефолт из `CONFIG` (`timeout_seconds`/`max_memory_mb`). Верх диапазона не выше
  серверного максимума. Действует для `mode="tests"`/`"bench"`; `microbench`
  держит серверные дефолты.
- `mode="playground"` — раздел «Песочница»: тело
  `{"mode":"playground","code":"...","stdin"?:"..."}` **без** `path` и без
  тестов. Пустой/пробельный `code` → **400** `specify_code`. Результат job'ы
  (в `GET /api/v1/runs/<id>` → `result`) — `{"status":
  "OK"|"RE"|"TLE"|"CANCELLED", "stdout", "stderr", "exit_code", "duration_ms",
  "truncated"}`; никакой сверки с ожидаемым выводом.
- `mode="trace"` — пошаговый трейс исполнения: то же тело
  `{"code","stdin"?}` без `path`. `result` — JSON-трейс `{steps, stdout,
  truncated, stdout_truncated, error}` (кадры стека + heap объектов со ссылками
  по id, aliasing); формат — в [trace-format.md](trace-format.md).
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
статус, не `"error"`: семантически это не провал решения/
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

AI-объяснение упавшего кейса как async job (ADR-0003) —
opt-in BYOK через OpenAI-совместимый endpoint. Возвращает `run_id`; результат
(`{"hint": str|null, "configured": bool, "reason": str|null}`) — через
`GET /api/v1/runs/<id>`.
Контекст (verdict/ввод-вывод/diff/ошибка + код решения) заземляет промпт общим
core-хелпером `build_failure_context`.

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
  `{"hint": null, "configured": false, "reason": null}` (graceful skip; грейдинг
  не затрагивается, в сеть ничего не уходит).
- Провайдер отказал → `{"hint": null, "configured": true, "reason": "..."}`, где
  `reason` — `unauthorized` (401), `forbidden` (403), `rate_limited` (429),
  `bad_request` (400), `server_error` (5xx), `network` (сеть/таймаут) или
  `empty` (пустой ответ). Без этого поля отказ провайдера неотличим от
  выключенного канала: в обоих случаях приходит `hint: null`.
- Успех → **202** `{"run_id": "...", "status": "queued"}`.

```
# после согласия — объяснить WA-кейс режима 1:
curl -X POST http://127.0.0.1:8000/api/v1/hint \
  -H "Content-Type: application/json" \
  -d '{"path": "task.py", "verdict": "WA", "stdin": "4", "expected": "5", "actual": "6", "consent": true}'
```

---

## `POST /api/v1/settings`

Write-through UI-настроек в `.grader_settings.json` рабочей директории.
Пока единственное поле — `onboarding_seen` (показан ли стартовый
экран-онбординг). Клиент шлёт `true` при закрытии онбординга с отмеченной галкой
«не показывать» и `false`, если галку сняли (вернуть авто-показ при следующем
запуске). `ai_hint_consent` сюда НЕ пишется — у него отдельный consent-путь
(`POST /api/v1/hint`).

Тело JSON: `{"onboarding_seen"?: bool}`. Пишутся только явно переданные
bool-поля (прочие не трогаются — `save_settings` сохраняет лишь не-`None`).
Начальное состояние флага сервер инжектит в `data-onboarding-seen` страницы (как
`data-sandbox`), отдельного GET нет.

- Успех → **200** `{"ok": true}`.

```
curl -X POST http://127.0.0.1:8000/api/v1/settings \
  -H "Content-Type: application/json" \
  -d '{"onboarding_seen": true}'
```

---

## `POST /api/stepik/submit`

Отправить решение (режим 1) на Stepik как async job — attempt →
submission → poll вердикта на стороне сервера. Возвращает `run_id`; вердикт
(`{"status": "correct"|"wrong"|"evaluation", "hint": str, "score": str,
"submission_id": int}`) — через `GET /api/v1/runs/<id>`.

Тело JSON: `{"code": "...", "path"?: "...", "step_id"?: int}`. `step_id` берётся
из тела или из `meta.json` папки задачи по `path` (`read_step_id`); язык
определяется автоматически из `code_templates` шага.

- Пустой `code` → **400** `stepik_no_code`.
- Не Stepik-задача (нет `step_id` в теле и в `meta.json`) → **400**
  `stepik_no_step_id`.
- `path` вне workspace → **403** `path_outside_workspace`.
- Нет валидного OAuth-токена (`secrets.json`) → job завершается **error**
  `stepik_auth_required` (в сеть ничего не уходит).
- Активных job'ов уже `CONFIG.max_active_runs` → **429** `too_many_runs`.
- Успех → **202** `{"run_id": "...", "status": "queued"}`.

Отправка — **необратимое действие** на платформе, поэтому UI требует явного
подтверждения пользователя перед вызовом этого эндпоинта.

```
curl -X POST http://127.0.0.1:8000/api/stepik/submit \
  -H "Content-Type: application/json" \
  -d '{"path": "StepikTasks/lesson-1-step-1", "code": "print(1)"}'
```

---

## `GET /api/auth/status`

Статус OAuth-авторизации Stepik по `secrets.json`. Путь к файлу —
из `stepik_config.json` рабочей директории, тот же, что использует скачивание.
Читает только локальный файл, сети не касается.

**200** `{"authorized": bool, "reason": "ok"|"no_token"|"no_secrets",
"secrets_path": "secrets.json"}`: `ok` — валидный токен; `no_token` — креды
есть, но токена нет/истёк (нужен браузерный flow); `no_secrets` — файла нет или
креды неполные (нужна форма). `secrets_path` — путь, по которому смотрели
(относительный, если внутри рабочей директории). Битый/нечитаемый файл
трактуется как `no_secrets` (best-effort, не 500).

```
curl http://127.0.0.1:8000/api/auth/status
```

## `GET /api/downloader/config`

Конфиг загрузчика из `stepik_config.json` рабочей директории —
единый источник и для скачивания, и для статуса авторизации.

**200** `{"root_dir": "StepikTasks", "secrets_path": "secrets.json",
"root_dir_default": "StepikTasks", "secrets_exists": bool, "configured": bool}`.
Пути — относительные, если лежат внутри рабочей директории. `configured` —
существует ли сам `stepik_config.json` (то есть отличает первый запуск от
повторного); отсутствие файла не ошибка, отдаются дефолты.

```
curl http://127.0.0.1:8000/api/downloader/config
```

## `POST /api/downloader/config`

Сохранить конфиг загрузчика. Тело —
`{"root_dir"?: "...", "secrets_path"?: "..."}`; пишутся только переданные поля,
остальные сохраняют текущее значение (правка корневой папки из web не стирает
`secrets_path`, выставленный из CLI).

- Успех → **200** `{"ok": true, <поля GET /api/downloader/config>, "auth":
  {"authorized": bool, "reason": "..."}}` — статус считается уже по новому пути,
  поэтому UI сразу говорит «файл рабочий» или «токена нет».
- Путь вне рабочей директории → **403** `path_outside_workspace` (как `root` у
  `POST /api/download`).

```
curl -X POST http://127.0.0.1:8000/api/downloader/config \
  -H 'Content-Type: application/json' \
  -d '{"root_dir":"StepikTasks"}'
```

## `POST /api/auth/start`

Запустить браузерный OAuth-flow первого запуска. Тело —
`{"client_id"?: "...", "client_secret"?: "...", "redirect_uri"?: "..."}`
(`redirect_uri` по умолчанию `http://localhost:8080/callback`). Пишет креды в
`secrets.json` (0600, путь — из `stepik_config.json`) и стартует loopback-OAuth
как async-job (`kind="auth"`); опрос прогресса/итога — через
`GET /api/v1/runs/<id>` (как у bench/microbench).

- Успех → **202** `{"run_id": "...", "status": "queued"}`; job по завершении —
  `{"status": "done", "result": {"authorized": true, "reason": "ok"}}`.
- Тело может быть пустым, если креды уже лежат в `secrets.json`:
  истёкший токен обновляется одной кнопкой, без повторного ввода
  `client_id`/`client_secret`.
- Нет `client_id`/`client_secret` ни в теле, ни в файле → **400**
  `{"kind": "error", message_id: specify_oauth_creds}`.
- Общие POST-гарды (`Content-Length`, Host/Origin/Fetch-Metadata) — как у всех
  POST `/api/*`.

`webbrowser.open` открывается на **машине сервера** — предназначено для
локального `--serve` (localhost, single-user); для удалённого сервера (не в
scope) не применимо.

```
curl -X POST http://127.0.0.1:8000/api/auth/start \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"...","client_secret":"..."}'
```

---

Любой путь, не подошедший ни под один маршрут выше — **404** `text/plain`
`"not found"`.
