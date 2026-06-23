# Stepik Python Grader

> Локальный грейдер для курсов «Поколение Python» на Stepik.  
> Скачивает данные задачи с сайта и позволяет не только проверить решение локально, но и **сравнить несколько решений более честно**: сначала по корректности, потом по benchmark-метрикам.

[Первоисточник грейдера](https://github.com/PavloOps/python_generation_grader)

Курсы:
- [Поколение Python: Профи](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)

---

## Содержание

- [Что умеет](#что-умеет)
- [Архитектура модулей](#архитектура-модулей)
- [Структура проекта](#структура-проекта)
- [Установка](#установка)
- [Быстрый старт](#быстрый-старт)
- [Работа с API Stepik](#работа-с-api-stepik)
- [Режимы работы](#режимы-работы)
- [Формат тест-кейсов](#формат-тест-кейсов)
- [Конфигурация](#конфигурация)
- [Зависимости](#зависимости)
- [Диагностика](#диагностика)
- [Ограничения и безопасность](#ограничения-и-безопасность)
- [Что изменилось по сравнению с оригиналом](#что-изменилось-по-сравнению-с-оригиналом)
- [Python версия](#python-версия)

---

## Что умеет

| Скрипт | Архитектурный слой | Что делает |
|---|---|---|
| `storage.py` | Infrastructure / Utilities | Чтение и запись JSON-файлов; нет зависимостей от других модулей проекта |
| `stepik_client.py` | Infrastructure / HTTP | OAuth2-авторизация, `requests.Session`, GET-запросы к Stepik REST API, скачивание сабмишнов |
| `at_first.py` | Domain / Application | Управление конфигом и secrets, разбор URL шага, построение директорий задач, сохранение файлов задачи, оркестрация вызовов API |
| `test.py` | Application | Проверяет решения локально, сравнивает несколько решений, запускает subprocess-benchmark и timeit-microbench |
| `executor.py` | Application | Запускатель решений: `compile + exec` с таймаутом и изолированным namespace |
| `microbench_runner.py` | Application | Timeit-микробенчмарк через `exec` + `contextlib` (без запуска нового процесса) |
| `diagnostik_stepik.py` | Infrastructure | Диагностика: проверяет структуру ответа API и корректность токена авторизации |

Основные возможности:

- ✅ Запуск решений против наборов тест-кейсов (`tests/N` + `tests/N.clue`)
- 📊 Сравнение нескольких решений одной задачи в таблице
- 🚀 Subprocess-бенчмарк с замером времени и памяти
- ⚡ Timeit-микробенчмарк через `exec` + `contextlib` (без запуска нового процесса)
- 🔍 Диагностика окружения и авторизация через Stepik API

---

## Архитектура модулей

Граф зависимостей — DAG без циклов:

```
at_first.py          ──→  storage.py
at_first.py          ──→  stepik_client.py
stepik_client.py     ──→  storage.py
test.py              ──→  executor.py
test.py              ──→  microbench_runner.py
diagnostik_stepik.py ──→  stepik_client.py
```

Слои (снизу вверх):

```
┌─────────────────────────────────────────────────┐
│  Domain / Application                           │
│  at_first.py  │  test.py  │  diagnostik_stepik  │
├─────────────────────────────────────────────────┤
│  Infrastructure                                 │
│  stepik_client.py  │  executor.py               │
│  microbench_runner.py                           │
├─────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (leaf, no deps)    │
│  storage.py                                     │
└─────────────────────────────────────────────────┘
```

`storage.py` — leaf-модуль: не импортирует ничего из проекта, легко тестируется изолированно.

---

## Структура проекта

```
Stepik-Python-Grader/
├── test.py                    # Главный модуль: 4 режима работы
├── executor.py                # Запускатель решений: compile + exec с таймаутом
├── microbench_runner.py       # Timeit-микробенчмарк через exec
├── at_first.py                # Domain: конфиг, slugify, построение папок, оркестрация API
├── stepik_client.py           # Infrastructure: OAuth2, requests.Session, Stepik API
├── storage.py                 # Utilities: load/save JSON (нет project-зависимостей)
├── diagnostik_stepik.py       # Диагностика API и токена
├── tests/
│   ├── test_executor.py
│   ├── test_microbench.py
│   └── test_slugify.py
├── pyproject.toml             # Конфигурация проекта (ruff, pytest, зависимости)
├── requirements.txt           # Runtime-зависимости
├── secrets.json.example       # Шаблон файла с OAuth-токеном
├── stepik_config.json.example # Шаблон конфига Stepik
└── README.md
```

Локально обычно появляются:

```text
P2.2/
stepik_config.json
secrets.json
errors.txt
stepik_diagnostics/
```

Эти файлы и папки держи в `.gitignore`.

---

## Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/ArtVsMark/Stepik-Python-Grader.git
cd Stepik-Python-Grader
```

### 2. Создать виртуальное окружение

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

Для разработки (линтер, тесты):

```bash
pip install -e ".[dev]"
```

---

## Быстрый старт

```bash
python test.py
```

При запуске появится меню:

```
Choose mode:
1 - test single file
2 - compare all solutions in top-level folder
3 - benchmark passed solutions
4 - microbench (timeit, any solution type)
Memory mode: parent process (fast, rough)
Subprocess timeout: 10.0s per test
Enter mode (1/2/3/4):
```

---

## Работа с API Stepik

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
> При первом запуске оставь `access_token`, `refresh_token`, `expires_at` пустыми — скрипт заполнит их сам через `storage.save_secrets()`.

### Шаг 2 — Скачать данные задачи

```bash
python at_first.py
```

При первом запуске:
- создастся `stepik_config.json` с выбранным курсом,
- откроется браузер для подтверждения доступа,
- после успешной авторизации токены сохранятся в `secrets.json`.

Введи URL шага, например:

```text
Enter Stepik step URL: https://stepik.org/lesson/569749/step/4?unit=564263
```

Скрипт создаст структуру:

```text
P2.2/
└── step-4-название-задачи/
    ├── template.py         # шаблон решения из задачи
    ├── solution.py         # первый сабмишн (если доступен)
    ├── meta.json           # метаданные шага (id, lesson, position)
    ├── task.md             # текст задачи в Markdown
    └── tests/
        ├── 1
        ├── 1.clue
        └── ...
```

OAuth-поток полностью реализован в `stepik_client.py` (`create_user_session`, `authorize_via_browser`, `refresh_access_token`); `at_first.py` только оркестрирует вызовы.

---

## Режимы работы

### Режим 1 — Проверка одного файла

Быстро прогнать одно решение:

```
Enter path to solution file (relative or absolute): module1/task1/task1.py

module1/task1/task1.py: 5/5 tests, total=0.1234s, avg=0.0247s, peak_memory=25.30 MB, status=OK
```

### Режим 2 — Сравнение всех решений

Проходит по всей папке, находит все `task*.py` и верифицирует каждый. Результаты — таблица, сгруппированная по задачам.

```
📂 module1/task1
--------------------------------------------------------------------
File                     Passed   Total time   Avg time  Peak memory  Status  Fail test
--------------------------------------------------------------------
module1/task1/task1.py      5/5       0.1234     0.0247        25.30      OK          -
module1/task1/task1_2.py    5/5       0.1456     0.0291        24.80      OK          -
```

> Режим 2 — проверка **корректности**, не полноценный benchmark.

### Режим 3 — Subprocess-бенчмарк

Запускает N повторений для каждого **прошедшего все тесты** решения через отдельный процесс. Выводит min / median / mean / max / std-dev и сравнивает решения относительно быстрейшего.

**Профили нагрузки (repeats):**

| # | Режим | Повторений |
|---|-------|------------|
| 1 | low | 5 |
| 2 | medium | 15 |
| 3 | high | 50 |
| 4 | custom | 5–100 |

**Что показывает benchmark:**

| Поле | Значение |
|---|---|
| `Runs` | всего запусков |
| `Min` | лучший замер |
| `Median` | медианное время — главный ориентир |
| `Mean` | среднее время |
| `Max` | худший замер |
| `Std dev` | разброс замеров (мало → стабильно) |
| `Memory` | пиковая память |
| `Relative` | относительное время к лучшему решению |
| `Verdict` | `SIMILAR`, `SLOWER`, `MUCH SLOWER` |

```
🚀 Benchmark: module1/task1
---------------------------------------------------------------------
File                     Runs     Min  Median    Mean     Max  Std dev  Memory  Relative   Verdict
---------------------------------------------------------------------
module1/task1/task1.py     25  0.0234  0.0249  0.0250  0.0279   0.0011   25.30    100.0%   SIMILAR
module1/task1/task1_2.py   25  0.0257  0.0271  0.0273  0.0301   0.0013   24.80    108.9%    SLOWER
```

### Режим 4 — Micro-bench (timeit)

Замеряет время через `timeit.timeit` внутри одного процесса — без накладных расходов на запуск интерпретатора. Поддерживает script-style (с `input()`) и function-only решения.

**Количество вызовов (calls per run):**

| # | Режим | Вызовов |
|---|-------|---------|
| 1 | fast | 500 |
| 2 | normal | 1 000 |
| 3 | thorough | 5 000 |
| 4 | deep | 50 000 |
| 5 | hard | 100 000 |
| 6 | custom | 100–500 000 |

> Режим `hard` — только для коротких детерминированных функций.

```
⚡ Micro-bench (timeit): module1/task1
---------------------------------------------------------------------------
File                     Repeats  Min, us  Median, us  Mean, us  Max, us  Std dev, us  Relative     Verdict
---------------------------------------------------------------------------
module1/task1/task1.py      1000    12.34       13.01     13.12    15.67         0.82    100.0%      SIMILAR
module1/task1/task1_2.py    1000    14.21       15.34     15.45    18.90         1.12    117.9%  MUCH SLOWER
```

---

## Формат тест-кейсов

```
module1/
└── task1/
    ├── task1.py          # решение
    └── tests/
        ├── 1             # входные данные теста №1 (stdin)
        ├── 1.clue        # ожидаемый вывод теста №1
        ├── 2
        ├── 2.clue
        └── ...
```

Кодировка определяется автоматически через `chardet`.

---

## Конфигурация

### Таймаут subprocess

В `test.py` константа `SUBPROCESS_TIMEOUT` (по умолчанию `10.0` с) защищает от зависания:

```python
SUBPROCESS_TIMEOUT = 10.0  # секунд
```

### Таймаут executor

В `executor.py` таймаут передаётся через переменную окружения `EXECUTOR_TIMEOUT` (по умолчанию `10` с). На Unix — `signal.alarm`; на Windows — `SUBPROCESS_TIMEOUT`:

```python
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))
```

### Замер памяти дочернего процесса

```python
MEASURE_CHILD_MEMORY = False  # True — честнее, но медленнее
```

- `False` — RSS родительского процесса (быстро, приблизительно)
- `True` — мониторинг дочернего процесса через `psutil` в отдельном потоке

### Лимит тест-кейсов для microbench

```python
MICROBENCH_MAX_CASES = 5
```

Ограничивает число тест-кейсов при `timeit`-замерах для стабильного std-dev.

---

## Зависимости

| Пакет | Назначение | Используется в |
|-------|------------|----------------|
| `requests>=2.34` | HTTP-запросы к Stepik API, OAuth2 | `stepik_client.py` |
| `psutil>=5.9` | Замер памяти и мониторинг процессов | `test.py`, `executor.py` |
| `chardet>=5.0` | Авто-определение кодировки файлов | `executor.py` |

Dev-зависимости (`pip install -e ".[dev]"`):

| Пакет | Назначение |
|-------|------------|
| `pytest>=8.2` | Тестирование |
| `ruff>=0.4` | Линтер и форматтер |

---

## Диагностика

Если `at_first.py` не нашёл данных шага автоматически:

```bash
python diagnostik_stepik.py
```

Скрипт сохранит в папку `stepik_diagnostics/`:
- `lesson_debug.json`
- `step_debug.json`
- `diagnostic_result.json`

`diagnostik_stepik.py` также позволяет:
- проверить доступность Stepik API;
- убедиться в корректности токена авторизации;
- получить информацию о курсе, уроке или задаче по ID.

---

## Ограничения и безопасность

- **Режимы 1–3 (`executor.py`):** решения запускаются через отдельный subprocess. Код компилируется через `compile(source, "<solution>", "exec")` и выполняется в изолированном namespace `{"__builtins__": __builtins__}`. На Unix — `signal.alarm(TIMEOUT)`; на Windows — `SUBPROCESS_TIMEOUT`.
- **Режим 4 (`microbench_runner.py`):** решения запускаются через `exec(compiled, {})` внутри одного процесса. `stdin`/`stdout` перенаправляются через `contextlib.redirect_stdin` / `contextlib.redirect_stdout`.
- **Microbench без таймаута:** бесконечный цикл в решении подвесит grader. Используй только с проверенными решениями.
- **Нет sandbox:** grader не изолирует файловую систему или сеть. Запускай только доверенные решения.

---

## Что изменилось по сравнению с оригиналом

Этот форк существенно расширяет [оригинальный проект PavloOps/python_generation_grader](https://github.com/PavloOps/python_generation_grader):

| Возможность | Оригинал | Этот форк |
|---|---|---|
| Проверка одного файла | ✅ | ✅ |
| Сравнение нескольких решений | ❌ | ✅ |
| Subprocess-benchmark | ❌ | ✅ режим 3 |
| Timeit-microbench | ❌ | ✅ режим 4 |
| Разделение корректности и benchmark | ❌ | ✅ |
| Профили нагрузки | ❌ | ✅ low/medium/high/custom |
| Оценка по median (не одиночный замер) | ❌ | ✅ |
| Вердикт SIMILAR / SLOWER / MUCH SLOWER | ❌ | ✅ |
| OAuth2 + скачивание данных задачи с API | ❌ | ✅ |
| Диагностика API | ❌ | ✅ |
| Поддержка function-only решений | ❌ | ✅ |
| Выделенный HTTP/OAuth слой (`stepik_client.py`) | ❌ | ✅ Sprint 3 |
| Утилиты хранилища без project-зависимостей (`storage.py`) | ❌ | ✅ Sprint 3 |
| pyproject.toml (ruff, pytest, зависимости) | ❌ | ✅ |
| Pre-commit хуки (ruff check + ruff format) | ❌ | ✅ |
| Unit-тесты (19 тестов) | ❌ | ✅ |

---

## Python версия

Python **3.11+**
