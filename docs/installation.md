# Установка и настройка Stepik

> Вынесено из README (issue #168 / эпик #102, PR-13). Обзор проекта — в
> [README](../README.md); карта документации — в [docs/README.md](README.md);
> работа с грейдером после установки — в
> [grader-workflow.md](grader-workflow.md).

## Оглавление

- [Требования](#требования)
- [Способ A — через pipx (рекомендуется)](#способ-a--через-pipx-рекомендуется)
- [Способ B — из исходников (для разработки)](#способ-b--из-исходников-для-разработки)
- [Зависимости](#зависимости)
- [Работа с API Stepik (OAuth)](#работа-с-api-stepik-oauth)
- [Диагностика](#диагностика)

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

> Пакет публикуется на [PyPI](https://pypi.org/project/stepik-python-grader/)
> (issue #70). Если нужна ещё не выпущенная версия прямо из репозитория —
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
python -m stepik_grader --version   # напр. 1.5.0
```

> Проект использует src-layout (`src/stepik_grader/`, Issue #35) — модули
> запускаются только как пакет (`python -m stepik_grader`) или командой
> `stepik-grader` (если её каталог в PATH). Прямого `python grader.py` из корня
> репозитория нет.

---

## Зависимости

| Пакет | Назначение | Используется в |
|-------|------------|----------------|
| `requests>=2.34.2` | HTTP-запросы к Stepik API, OAuth2, скачивание ZIP | `core/stepik_client.py`, `downloader.py` |
| `psutil>=5.9` | Замер памяти и мониторинг процессов | `core/grader_core.py`, `core/executor.py` |
| `rich>=13.0` | Цветные таблицы, прогресс-бар, WA diff в терминале | `core/reporter.py` |

Dev-зависимости (`pip install -e ".[dev]"`):

| Пакет | Назначение |
|-------|------------|
| `pytest>=8.2` | Тестирование |
| `pytest-cov>=5.0` | Покрытие тестами (`--cov`) |
| `ruff>=0.4` | Линтер и форматтер |
| `mypy>=1.10` | Проверка типов |

---

## Работа с API Stepik (OAuth)

Настройка нужна, только если хочешь **автоматически скачивать данные задач** с
Stepik. Сам грейдинг локальных решений (см.
[grader-workflow.md](grader-workflow.md)) работает без OAuth.

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
