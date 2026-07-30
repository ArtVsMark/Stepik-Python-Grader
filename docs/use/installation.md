# Установка и настройка Stepik

> Обзор проекта — в
> [README](../../README.md); карта документации — в [../README.md](../README.md);
> работа с грейдером после установки — в
> [grader-workflow.md](grader-workflow.md).

## Оглавление

- [Требования](#требования)
- [Способ A — через pipx (рекомендуется)](#способ-a--через-pipx-рекомендуется)
- [Способ B — из исходников (для разработки)](#способ-b--из-исходников-для-разработки)
- [Проверка установки — веб-интерфейс](#проверка-установки--веб-интерфейс)
- [Зависимости](#зависимости)
- [Работа с API Stepik (OAuth)](#работа-с-api-stepik-oauth)
- [Диагностика](#диагностика)
- [Диагностика окружения разработки (pytest, Windows)](#диагностика-окружения-разработки-pytest-windows)

---

## Требования

- **Python 3.12 или 3.13.** Версия 3.14 — экспериментальная (может ломаться),
  ставь её только осознанно. Проверить свою версию: `python --version`.
- **Git** — только для установки из исходников.

> **Коротко для новичка:** если просто хочешь пользоваться — ставь через
> **`pipx`** (Способ A): он сам всё изолирует и добавит команду в PATH, никаких
> `venv` и `activate`. Способ B (из исходников) нужен только если будешь менять
> код.

---

## Способ A — через pipx (рекомендуется)

[pipx](https://pipx.pypa.io) ставит CLI-инструмент в отдельное окружение и сам
прописывает команду в PATH — не нужно ни `venv`, ни `activate`.

```bash
python -m pip install --user pipx
python -m pipx ensurepath      # один раз добавляет pipx в PATH — ПЕРЕЗАПУСТИ терминал после этого
pipx install stepik-python-grader
```

Проверь, что всё встало:

```bash
stepik-grader --version        # должно напечатать текущую версию
```

> Пакет публикуется на [PyPI](https://pypi.org/project/stepik-python-grader/).
> Если нужна ещё не выпущенная версия прямо из репозитория —
> `pipx install git+https://github.com/ArtVsMark/Stepik-Python-Grader.git`.
> Обычный `pip install stepik-python-grader` тоже работает, но `pipx` удобнее
> для CLI-инструмента (изоляция + PATH).

---

## Способ B — из исходников (для разработки)

**Шаг 1. Клонировать репозиторий:**

```bash
git clone https://github.com/ArtVsMark/Stepik-Python-Grader.git
cd Stepik-Python-Grader
```

**Шаг 2. Создать виртуальное окружение:**

```bash
python -m venv .venv
```

**Шаг 3. Активировать окружение:**

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

> ⚠️ **Windows: «выполнение сценариев отключено в этой системе»
> (PSSecurityException)?** PowerShell по умолчанию блокирует активацию venv.
> Два выхода:
>
> 1. **Разрешить скрипты для своего пользователя (один раз):**
>    ```powershell
>    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
>    ```
>    затем снова `.venv\Scripts\Activate.ps1`.
> 2. **Или не активировать вообще** — звать интерпретатор из venv напрямую:
>    ```powershell
>    .venv\Scripts\python.exe -m pip install -e .
>    .venv\Scripts\python.exe -m stepik_grader
>    ```
>
> ❗ **Не пропускай активацию, если ставишь просто `pip install -e .`** — иначе
> пакет уедет в *глобальный* Python, а не в venv, и команда `stepik-grader`
> может «не найтись» (её каталог не в PATH). В любом случае надёжный запуск —
> `python -m stepik_grader` (работает всегда, см.
> [grader-workflow.md](grader-workflow.md)).
>
> ⚠️ **`stepik-grader --serve`/`--sandbox`/любая команда падает с
> `ModuleNotFoundError: No module named 'stepik_grader'`, хотя команда
> находится (не «command not found»)?** Значит `stepik-grader` резолвится в
> *чужой* (глобальный) Python, а не в активный `.venv` — обычно из-за старого
> editable-install, сделанного когда-то до перехода проекта на src-layout
> или просто не в venv. Проверь, откуда берётся команда:
> ```powershell
> Get-Command stepik-grader   # Windows: путь должен указывать на .venv\Scripts\
> ```
> ```bash
> which stepik-grader          # macOS/Linux: путь должен указывать на .venv/bin/
> ```
> Если путь НЕ внутри `.venv` этого репозитория — активируй venv (Шаг 3 выше)
> и повтори команду; PATH внутри активированного venv ставит его `Scripts`/
> `bin` первым, так что нужный `stepik-grader` найдётся раньше чужого.
> Чтобы такой «протухший» глобальный install не путал в будущем, его стоит
> убрать явно (замени `<version>`/`<hash>` на то, что покажет `pip show -f
> stepik-python-grader` из глобального Python):
> ```bash
> pip uninstall stepik-python-grader   # если ругается "No files were found to
>                                      # uninstall" — метаданные битые, удали
>                                      # вручную *.dist-info/, __editable__*.pth,
>                                      # __editable___*_finder.py из site-packages
>                                      # (путь покажет `python -c "import site;
>                                      # print(site.getsitepackages())"`) и
>                                      # соответствующий stepik-grader(.exe) из
>                                      # каталога Scripts/bin рядом.
> ```

**Шаг 4. Установить зависимости:**

```bash
pip install -e .             # рантайм: requests, psutil, rich
```

Для разработки (тесты, линтер, типизация):

```bash
pip install -e ".[dev]"      # + pytest, pytest-cov, ruff, mypy
```

**Шаг 5. Проверить установку:**

```bash
python -m stepik_grader --version   # напр. 1.9.0
```

> Проект использует src-layout (`src/stepik_grader/`) — модули
> запускаются только как пакет (`python -m stepik_grader`) или командой
> `stepik-grader` (если её каталог в PATH). Прямого `python grader.py` из корня
> репозитория нет.

---

## Проверка установки — веб-интерфейс

Самый наглядный способ убедиться, что установка рабочая (годится и для способа
A, и для способа B) — поднять локальный веб-интерфейс:

```bash
stepik-grader --serve          # или: python -m stepik_grader --serve
```

Открой в браузере <http://127.0.0.1:8000> — порт по умолчанию 8000, другой
задаётся флагом `--port` (напр. `stepik-grader --serve --port 9000`). При
первом заходе показывается стартовый экран-приветствие, за ним —
оболочка с разделами «Проверка решений», «Загрузчик задач», «Глоссарий»,
«Правила (PEP)», «Подучить», «Песочница». Если интерфейс отрисовался —
установка рабочая. Остановить сервер — `Ctrl+C` в терминале.

Сервер слушает только `127.0.0.1` и наружу не торчит; без `--sandbox` изоляции
исполнения нет — запускай только свои решения. Разделы, флаги и threat model —
[grader-workflow.md](grader-workflow.md#веб-интерфейс---serve).

> **Запуск без командной строки.** Кроме `python -m stepik_grader`
> и `stepik-grader`, после установки доступен GUI-лаунчер `stepik-grader-gui`
> (на Windows — ярлык без консольного окна; или `python -m stepik_grader.launcher`):
> окно с выбором режима сервера, порта и рабочей папки. Детали —
> [grader-workflow.md](grader-workflow.md#веб-интерфейс---serve).

---

## Зависимости

| Пакет | Назначение | Используется в |
|-------|------------|----------------|
| `requests>=2.34.2` | HTTP-запросы к Stepik API, OAuth2, скачивание ZIP | `core/stepik_client.py`, `downloader.py` |
| `psutil>=5.9` | Замер памяти и мониторинг процессов | `core/grader_core.py`, `core/runner.py` |
| `rich>=13.0` | Цветные таблицы, прогресс-бар, WA diff в терминале | `core/reporter.py` |

Dev-зависимости (`pip install -e ".[dev]"`):

| Пакет | Назначение |
|-------|------------|
| `pytest>=8.2` | Тестирование |
| `pytest-cov>=5.0` | Покрытие тестами (`--cov`) |
| `pytest-timeout>=2.3` | Тайм-аут зависших тестов (`timeout` в `[tool.pytest]`) |
| `ruff>=0.15.19` | Линтер и форматтер |
| `mypy>=1.10` | Проверка типов |
| `hypothesis>=6.0` | Property-based тесты (парсер тест-блоков, нормализация float) |

Отдельные opt-in extra (ставятся явно, не входят в `[dev]`): `[watch]` —
`watchfiles` для watch-режима прогона; `[lint]` — `ruff` как **runtime**-движок
блока «Стиль»; `[e2e]` — `playwright` для смок-тестов web-UI
(см. [CONTRIBUTING.md § E2E](../../CONTRIBUTING.md)). Полный инвентарь
версий/лицензий рантайма и вендоренных веб-ассетов, плюс `pip-audit` в CI —
[../dev/supply-chain.md](../dev/supply-chain.md).

---

## Работа с API Stepik (OAuth)

Настройка нужна, если хочешь **автоматически скачивать данные задач** с Stepik
или **отправлять решение на Stepik** прямо из веб-интерфейса (кнопка
«Отправить в Stepik» в режиме 1, см.
[grader-workflow.md](grader-workflow.md#веб-интерфейс---serve)). Сам грейдинг
локальных решений (см. [grader-workflow.md](grader-workflow.md)) работает без
OAuth.

> **Проще всего — без ручной настройки.** В интерактивном `downloader` при
> отсутствии `secrets.json` запускается пошаговый мастер, а в
> веб-интерфейсе (`--serve`) раздел «Загрузчик задач» даёт форму авторизации
> прямо в браузере. Оба варианта заменяют ручное создание
> `secrets.json` — шаги ниже нужны, только если предпочитаешь настроить всё
> вручную.

### Шаг 0 — Настройка OAuth на Stepik

**1. Создай OAuth-приложение на Stepik**

1. Зайди на <https://stepik.org/oauth2/applications/>
2. Нажми **+ New Application**
3. Заполни поля:

| Поле | Значение |
|---|---|
| Name | любое, например `my-grader` |
| Client type | `Confidential` |
| Authorization grant type | `Authorization code` |
| Redirect uris | `http://localhost:8080/callback` |

4. Нажми **Save** — Stepik покажет `Client ID` и `Client Secret`.

### Шаг 1 — Создай `secrets.json`

Скопируй шаблон:

```bash
cp secrets.json.example secrets.json
```

Заполни своими значениями:

```json
{
  "client_id": "<Client ID из настроек приложения Stepik>",
  "client_secret": "<Client Secret из настроек приложения Stepik>",
  "redirect_uri": "http://localhost:8080/callback",
  "access_token": "",
  "refresh_token": "",
  "expires_at": 0
}
```

### Что означают поля в `secrets.json`

| Поле | Что это |
|---|---|
| `client_id` | ID OAuth-приложения в Stepik |
| `client_secret` | секрет OAuth-приложения |
| `redirect_uri` | адрес для возврата после авторизации |
| `access_token` | текущий токен доступа, заполняется автоматически |
| `refresh_token` | токен обновления, заполняется автоматически |
| `expires_at` | время истечения `access_token` (Unix-timestamp), заполняется автоматически |

> `secrets.json` — локальный файл, не должен попадать в Git.
> При первом запуске оставь `access_token`, `refresh_token`, `expires_at`
> пустыми — скрипт заполнит их сам через `storage.save_secrets()`.

Дальше — [скачивание данных задачи](grader-workflow.md#шаг-скачивания-задачи).

### Устойчивость к сетевым сбоям

Запросы к Stepik API идут через `requests.Session`, которая автоматически
повторяет временные сбои — падать при мимолётной перегрузке API не нужно,
достаточно подождать и переподключиться.

- **Какие статусы повторяются.** `429` (Too Many Requests) и временные `5xx` —
  `500`/`502`/`503`/`504`. Прочие `4xx` (напр. `404` — задача не найдена) **не**
  повторяются: это не временная проблема, а значит запрос не может «просто
  сработать со второй попытки».
- **Backoff.** Экспоненциальный, база — 1 секунда, удваивается с каждой
  попыткой (1с → 2с → 4с...). Если сервер прислал заголовок `Retry-After` —
  используется он, а не расчётная задержка. По умолчанию 3 повтора (всего до
  4 попыток на запрос).
- **Что делать при постоянной ошибке.** Если после всех повторов запрос всё
  ещё падает — это не флап, а реальная проблема (Stepik API недоступен,
  истёкший токен, сетевая изоляция). Скрипт покажет исключение
  `requests.exceptions.RetryError` (транспортный уровень) или
  `requests.RequestException` (сетевой уровень, напр. таймаут) — смотри текст
  ошибки; для диагностики токена/доступности API — см. раздел
  [Диагностика](#диагностика) ниже.
- **Реализация.** `core/stepik_client.make_session()` монтирует
  `requests.adapters.HTTPAdapter` с `urllib3.util.Retry` на `http://`/`https://` —
  повтор действует на уровне транспорта для любого запроса через сессию, не
  только там, где явно вызывается внутренний `_get_with_retry()` (тот остаётся
  дополнительным уровнем повтора при сетевых исключениях вроде обрыва
  соединения). Зависимость `urllib3` уже идёт с `requests`, новых пакетов не
  добавлено.

---

## Диагностика

Если `downloader.py` не нашёл данных шага автоматически:

```bash
python -m stepik_grader.diagnostic_stepik
```

Скрипт сохранит в папку `stepik_diagnostics/`:
- `lesson_debug.json`
- `step_debug.json`
- `diagnostic_result.json`

`diagnostic_stepik.py` также позволяет:
- проверить доступность Stepik API;
- убедиться в корректности токена авторизации;
- получить информацию о курсе, уроке или задаче по ID.

---

## Диагностика окружения разработки (pytest, Windows)

Три известные проблемы, все воспроизводятся на чистом
`main` — они вызваны не кодом проекта, а состоянием локального окружения:

**`test_packaging.py::test_license_is_mit_in_metadata` падает
(`License-Expression` — `None`, ожидался `"MIT"`) или
`tests/test_pytest_plugin.py` падает с `unrecognized arguments:
--grader-mode`.** Оба симптома — один и тот же корень: **протухшие
метаданные editable-установки** (`pip install -e ".[dev]"` был сделан до
изменений в `pyproject.toml`, затрагивающих `license`/`entry-points`,
и `.dist-info/` не обновился). `entry-points.txt` пакета всё ещё содержит
старую (или отсутствующую) регистрацию `pytest11 = stepik_grader.pytest_plugin`
— отсюда и падение `test_pytest_plugin.py` (плагин не резолвится в дочернем
`pytest.main()`, который поднимает `pytester`), и падение
license-метадаты. Чинится переустановкой:

```bash
pip install -e ".[dev]" --force-reinstall --no-deps
```

Проверить причину/фикс:

```bash
python -m pytest tests/test_packaging.py tests/test_pytest_plugin.py -q
```

**`PermissionError: [WinError 5] Отказано в доступе` на
`%TEMP%\pytest-of-<user>`.** Каталог создавался предыдущим прогоном pytest
в другом контексте/правах (напр. другой пользователь Windows, повышенные
права) и остался недоступен для записи текущему пользователю. Это не
связано с проектом — `tmp_path`-фикстура просто не может создать
поддиректорию. Диагностика:

```powershell
Get-Acl "$env:TEMP\pytest-of-$env:USERNAME"
```

Если `Owner`/`Access` не совпадают с текущим пользователем — либо исправь
права (`icacls` от администратора), либо обойди явным `--basetemp` вне
этого каталога:

```bash
pytest tests/ --basetemp=C:\temp\pytest-basetemp
```

Проверять оба симптома *в этом порядке* — если первопричина (протухшая
установка) устранена переустановкой, `test_pytest_plugin.py` обычно
начинает проходить и с дефолтным `--basetemp` тоже, без обходного пути.
