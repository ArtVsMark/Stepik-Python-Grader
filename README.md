# Stepik Python Grader

> Локальный грейдер для курсов «Поколение Python» на Stepik.  
> Скачивает тесты к задаче с сайта и позволяет не только проверить решение локально, но и **сравнить несколько решений более честно**: сначала по корректности, потом по benchmark-метрикам.

[Первоисточник грейдера](https://github.com/PavloOps/python_generation_grader)

Курсы:
- [Поколение Python: Профи](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)

---

## Содержание

- [Что умеет](#что-умеет)
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

| Скрипт | Что делает |
|---|---|
| `at_first.py` | OAuth2-авторизация на Stepik, создаёт папку задачи и скачивает тесты через API |
| `test.py` | Проверяет решения локально, сравнивает несколько решений, запускает subprocess-benchmark и timeit-microbench |
| `executor.py` | Запускатель решений: `compile + exec` с таймаутом и изолированным namespace (function-only решения) |
| `microbench_runner.py` | Timeit-микробенчмарк через `exec` + `contextlib` (без запуска нового процесса) |
| `diagnostik_stepik.py` | Диагностика: проверяет структуру ответа API и наличие ZIP |

Основные возможности:

- ✅ Запуск решений против наборов тест-кейсов (`tests/N` + `tests/N.clue`)
- 📊 Сравнение нескольких решений одной задачи в таблице
- 🚀 Subprocess-бенчмарк с замером времени и памяти
- ⚡ Timeit-микробенчмарк через `exec` + `contextlib` (без запуска нового процесса)
- 🔍 Диагностика окружения и авторизация через Stepik API

---

## Структура проекта

```
Stepik-Python-Grader/
├── test.py                    # Главный модуль: 4 режима работы
├── microbench_runner.py       # Timeit-микробенчмарк через exec
├── executor.py                # Запускатель решений: compile + exec с таймаутом и изолированным namespace
├── at_first.py                # Авторизация Stepik OAuth2 + получение токена
├── diagnostik_stepik.py       # Диагностика окружения и API
├── pyproject.toml             # Конфигурация проекта (ruff, mypy)
├── requirements.txt           # Зависимости
├── secrets.json.example       # Шаблон файла с токеном
├── stepik_config.json.example # Шаблон конфига Stepik
└── README.md
```

Обычно локально дополнительно появляются:

```text
P2.2/
stepik_config.json
secrets.json
errors.txt
stepik_diagnostics/
```

Эти файлы и папки лучше держать в `.gitignore`.

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

---

## Быстрый старт

```bash
python test.py
```

При запуске вы увидите:

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

Заполни файл своими значениями:

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
| `expires_at` | время истечения `access_token`, заполняется автоматически |

> `secrets.json` должен оставаться локальным файлом и не должен попадать в Git.  
> Поля `access_token`, `refresh_token`, `expires_at` при первом запуске оставь пустыми — скрипт заполнит их сам.

### Шаг 2 — Скачать тесты к задаче

```bash
python at_first.py
```

При первом запуске:
- создастся `stepik_config.json`,
- откроется браузер для подтверждения доступа,
- после успешной авторизации токены будут сохранены в `secrets.json`.

Введи URL шага, например:

```text
Enter Stepik step URL: https://stepik.org/lesson/569749/step/4?unit=564263
```

Скрипт создаст структуру вида:

```text
P2.2/
└── step-4-название-задачи/
    ├── task_1.py
    ├── task_2.py
    ├── README.md
    └── tests/
        ├── 1
        ├── 1.clue
        └── ...
```

---

## Режимы работы

### Режим 1 — Проверка одного файла

Подходит, когда хочешь быстро прогнать одно решение.

```
Enter path to solution file (relative or absolute): module1/task1/task1.py

module1/task1/task1.py: 5/5 tests, total=0.1234s, avg=0.0247s, peak_memory=25.30 MB, status=OK
```

### Режим 2 — Сравнение всех решений

Этот режим нужен для **проверки корректности** нескольких файлов сразу. Он показывает:
- сколько тестов прошло каждое решение,
- общее время,
- среднее время на тест,
- пиковую память,
- статус (`OK`, `FAILED`, `NO TESTS`).

Проходит по всей папке, находит все файлы `task*.py` и верифицирует каждый. Результаты выводятся таблицей, сгруппированной по задачам.

```
Enter top-level folder from the content root: module1

📂 module1/task1
--------------------------------------------------------------------
File                    Passed    Total time      Avg time   Peak memory      Status  Fail test
--------------------------------------------------------------------
module1/task1/task1.py       5/5        0.1234        0.0247        25.30          OK          -
module1/task1/task1_2.py     5/5        0.1456        0.0291        24.80          OK          -
```

> Этот режим **не является полноценным benchmark**. Он нужен в первую очередь для проверки правильности решений.

### Режим 3 — Subprocess-бенчмарк

Запускает N повторений для каждого **прошедшего все тесты** решения через отдельный процесс. Выводит min/median/mean/max/std-dev и сравнивает скорость решений относительно быстрейшего.

**Нагрузка (repeats):**

| # | Режим | Повторений |
|---|-------|------------|
| 1 | low | 5 |
| 2 | medium | 15 |
| 3 | high | 50 |
| 4 | custom | 5–100 |

### Что показывает benchmark

| Поле | Значение |
|---|---|
| `Runs` | сколько всего запусков было выполнено |
| `Min` | лучший замер |
| `Median` | медианное время, главный ориентир |
| `Mean` | среднее время |
| `Max` | худший замер |
| `Std dev` | стандартное отклонение, показывает разброс замеров |
| `Memory` | пиковая память |
| `Relative` | относительное время к лучшему решению |
| `Verdict` | итоговая оценка (`SIMILAR`, `SLOWER`, `MUCH SLOWER`) |

> **Что такое `Std dev`:** маленькое значение → замеры стабильные; большое значение → результаты скачут и benchmark шумный.

```
🚀 Benchmark: module1/task1
---------------------------------------------------------------------
File                    Runs         Min      Median        Mean         Max     Std dev      Memory    Relative     Verdict
---------------------------------------------------------------------
module1/task1/task1.py    25     0.02341     0.02489     0.02501     0.02789     0.00112       25.30      100.0%     SIMILAR
module1/task1/task1_2.py  25     0.02567     0.02712     0.02734     0.03012     0.00134       24.80      108.9%      SLOWER
```

### Режим 4 — Micro-bench (timeit)

Замеряет время выполнения через `timeit.timeit` внутри одного процесса — без накладных расходов на запуск интерпретатора. Поддерживает любые решения (script-style с `input()` и function-only).

**Количество вызовов (calls per run):**

| # | Режим | Вызовов |
|---|-------|---------|
| 1 | fast | 500 |
| 2 | normal | 1 000 |
| 3 | thorough | 5 000 |
| 4 | deep | 50 000 |
| 5 | hard | 100 000 |
| 6 | custom | 100–500 000 |

> **Примечание:** режим `hard` подходит для коротких детерминированных функций.  
> Режим `custom` позволяет задать произвольное число от 100 до 500 000.

```
Enter top-level folder from the content root: module1

Microbench repeats (calls per run):
1 - fast     (500)
2 - normal   (1 000)
3 - thorough (5 000)
4 - deep     (50 000)
5 - hard     (100 000)
6 - custom   (100 to 500 000)
Choose (1/2/3/4/5/6): 2

⚡ Micro-bench (timeit): module1/task1
---------------------------------------------------------------------------
File                    Repeats      Min, us  Median, us    Mean, us     Max, us Std dev, us    Relative     Verdict
---------------------------------------------------------------------------
module1/task1/task1.py     1000        12.34       13.01       13.12       15.67        0.82      100.0%     SIMILAR
module1/task1/task1_2.py   1000        14.21       15.34       15.45       18.90        1.12      117.9%  MUCH SLOWER
```

---

## Формат тест-кейсов

Тест-кейсы хранятся рядом с решением в папке `tests/`:

```
module1/
└── task1/
    ├── task1.py          # решение (или несколько: task1_2.py, task1_v2.py ...)
    └── tests/
        ├── 1             # входные данные теста №1
        ├── 1.clue        # ожидаемый вывод теста №1
        ├── 2
        ├── 2.clue
        └── ...
```

**Файл входных данных** (`tests/N`) — текстовый файл, строки которого будут поданы на `stdin`.

**Файл эталона** (`tests/N.clue`) — текстовый файл с ожидаемым выводом решения.

Кодировка определяется автоматически через `chardet`.

---

## Конфигурация

### Таймаут subprocess

В `test.py` константа `SUBPROCESS_TIMEOUT` (по умолчанию `10.0` секунд) защищает от зависания при бесконечных циклах в решениях студентов:

```python
SUBPROCESS_TIMEOUT = 10.0  # секунд
```

### Таймаут executor

В `executor.py` таймаут передаётся через переменную окружения `EXECUTOR_TIMEOUT` (по умолчанию `10` секунд). На Unix используется `signal.alarm`; на Windows таймаут обеспечивается на уровне subprocess через `SUBPROCESS_TIMEOUT`:

```python
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))
```

### Замер памяти дочернего процесса

```python
MEASURE_CHILD_MEMORY = False  # True — честнее, но медленнее
```

- `False` (по умолчанию) — измеряется RSS родительского процесса (быстро, приблизительно)
- `True` — мониторинг дочернего процесса через `psutil` в отдельном потоке (точнее)

### Лимит тест-кейсов для microbench

```python
MICROBENCH_MAX_CASES = 5  # максимум тест-кейсов в microbench
```

Ограничивает количество тест-кейсов при `timeit`-замерах для стабильного std-dev без перегрузки при большом числе повторений.

---

## Зависимости

| Пакет | Назначение |
|-------|------------|
| `psutil` | Замер памяти и мониторинг процессов |
| `chardet` | Авто-определение кодировки файлов |
| `requests` | HTTP-запросы к Stepik API (`at_first.py`) |

Установка:

```bash
pip install -r requirements.txt
```

---

## Диагностика

Если `at_first.py` не нашёл ZIP автоматически:

```bash
python diagnostik_stepik.py
```

Скрипт сохранит:
- `lesson_debug.json`,
- `step_debug.json`,
- `diagnostic_result.json`

в папку `stepik_diagnostics/`.

Файл `diagnostik_stepik.py` также позволяет:
- Проверить доступность Stepik API
- Убедиться в корректности токена авторизации
- Получить информацию о курсе, уроке или задаче по ID

Файл `at_first.py` реализует OAuth2-авторизацию через Stepik и сохраняет токен в `secrets.json`.

> Скопируйте `secrets.json.example` → `secrets.json` и заполните `client_id` / `client_secret` из настроек приложения на Stepik.

---

## Ограничения и безопасность

- **`executor.py` (режимы 1–3):** решения запускаются через отдельный subprocess. Код компилируется через `compile(source, "<solution>", "exec")` и выполняется в изолированном namespace `{"__builtins__": __builtins__}`. На Unix защита от зависания — `signal.alarm(TIMEOUT)`; на Windows — `SUBPROCESS_TIMEOUT` на уровне `subprocess.run`.
- **`microbench_runner.py` (режим 4):** решения запускаются через `exec(compiled, {})` внутри одного процесса. `stdin`/`stdout` перенаправляются через `contextlib.redirect_stdin` / `contextlib.redirect_stdout` — потокобезопасно в отличие от прямой подмены `sys.stdin`/`sys.stdout`.
- **Таймаут subprocess:** `SUBPROCESS_TIMEOUT = 10.0s` защищает от бесконечных циклов в режимах 1–3.
- **Microbench без таймаута:** в режиме 4 (`timeit`) таймаут не применяется — бесконечный цикл в решении подвесит grader. Используйте только с проверенными решениями.
- **Нет sandbox:** grader не изолирует файловую систему или сеть. Запускайте только доверенные решения.

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
| OAuth2 + скачивание тестов с API | ❌ | ✅ |
| Диагностика API | ❌ | ✅ |
| Поддержка function-only решений | ❌ | ✅ через `executor.py` |
| pyproject.toml (ruff, mypy) | ❌ | ✅ |

Ключевые улучшения в `test.py`:
- сравнение корректности и benchmark разделены;
- benchmark работает только для полностью прошедших решений;
- добавлены профили нагрузки `low / medium / high / custom`;
- результаты оцениваются по `median`, а не по случайному одиночному замеру;
- добавлены `mean`, `max`, `std_dev`, `relative_percent` и `verdict`.

---

## Python версия

Python **3.10+**
