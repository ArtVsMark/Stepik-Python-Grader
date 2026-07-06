# Конфигурация и справочник (reference)

> Канонический reference-документ (issue #171 / эпик #167, #97). Пользовательские
> сценарии (режимы, CLI-флаги, web/IDE, скачивание задачи) — в
> [grader-workflow.md](grader-workflow.md); установка и OAuth — в
> [installation.md](installation.md); карта документации — в
> [docs/README.md](README.md); инварианты ядра — в [`CLAUDE.md`](../CLAUDE.md).

Здесь собран весь **справочный** материал: параметры конфигурации, форматы
тест-кейсов (единственный канонический источник), ограничения и модель
безопасности локального запуска, а также диагностика конфигурационных ошибок.

## Оглавление

- [Где что настраивается](#где-что-настраивается)
- [`[tool.stepik-grader]` в `pyproject.toml`](#toolstepik-grader-в-pyprojecttoml)
- [`stepik_config.json` — корневая папка задач](#stepik_configjson--корневая-папка-задач)
- [Таймауты](#таймауты)
- [Замер памяти дочернего процесса](#замер-памяти-дочернего-процесса)
- [Лимит тест-кейсов для microbench](#лимит-тест-кейсов-для-microbench)
- [Формат тест-кейсов](#формат-тест-кейсов)
- [Ограничения и безопасность](#ограничения-и-безопасность)
- [Диагностика конфигурационных ошибок](#диагностика-конфигурационных-ошибок)

---

## Где что настраивается

| Что | Где | Формат |
|---|---|---|
| Параметры грейдинга (таймауты, память, пороги, кэш) | `pyproject.toml` → `[tool.stepik-grader]` | TOML, читается в `GraderConfig`/`CONFIG` |
| Корневая папка задач и путь к `secrets.json` | `stepik_config.json` (в текущей папке) | JSON, пишется `downloader.py` |
| OAuth-токены Stepik | `secrets.json` | JSON, пишется `storage.save_secrets()` (см. [installation.md](installation.md#работа-с-api-stepik-oauth)) |
| Кэш результатов проверки | `.grader_cache/results.json` (в CWD) | JSON, opt-in (`--cache`) |
| Поведение pytest-плагина | `pyproject.toml` → `[tool.pytest.ini_options]` `grader_mode` | TOML |

---

## `[tool.stepik-grader]` в `pyproject.toml`

Единая точка правды для параметров грейдинга — dataclass `GraderConfig`
(`frozen=True`, потокобезопасно) в `src/stepik_grader/config.py`. При импорте
пакета `load_config()` читает секцию `[tool.stepik-grader]` из `pyproject.toml`
и создаёт синглтон `CONFIG`. Если файла или секции нет — используются дефолты.
Незнакомые ключи молча игнорируются (в `GraderConfig` попадают только
объявленные поля).

```toml
[tool.stepik-grader]
timeout_seconds = 10.0
executor_timeout = 10
similar_threshold = 1.15
much_slower_threshold = 1.50
measure_child_memory = true
microbench_max_cases = 5
```

**Полный список параметров:**

| Ключ | Тип | Дефолт | Назначение |
|---|---|---|---|
| `timeout_seconds` | `float` | `10.0` | Таймаут subprocess одного тест-кейса (режимы 1–3), защита от зависания. |
| `executor_timeout` | `int` | `10` | Таймаут `core/executor.py` (`signal.alarm` на Unix). |
| `similar_threshold` | `float` | `1.15` | Порог вердикта `SIMILAR` в бенчмарке (относительно быстрейшего). |
| `much_slower_threshold` | `float` | `1.50` | Порог вердикта `MUCH SLOWER` в бенчмарке. |
| `measure_child_memory` | `bool` | `true` | `true` — мониторинг дочернего процесса через `psutil` (честнее, медленнее); `false` — RSS родителя (быстро, грубо). |
| `microbench_max_cases` | `int` | `5` | Максимум тест-кейсов при `timeit`-замерах (режим 4) для стабильного std-dev. |
| `encoding` | `str` | `"utf-8"` | Кодировка чтения файлов решений и тестов. |
| `max_memory_mb` | `int \| None` | `1024` | Best-effort лимит памяти дочернего процесса (POSIX-only, `RLIMIT_AS`); `None` — без лимита. См. [Ограничения и безопасность](#ограничения-и-безопасность). |
| `use_cache` | `bool` | `false` | Включить кэш результатов по умолчанию (эквивалент `--cache`, issue #56). Отдельный запуск форсируется `--no-cache`. |

> Значения из `pyproject.toml` перекрывают дефолты. `GraderConfig` — `frozen`:
> изменить его в рантайме нельзя (мутация → `FrozenInstanceError`). Полный
> список инвариантов ядра — в [`CLAUDE.md`](../CLAUDE.md).

---

## `stepik_config.json` — корневая папка задач

При первом запуске `downloader.py` предложит указать корневую папку для задач и
путь к `secrets.json`:

```
Укажи корневую папку для всех задач Stepik [StepikTasks]:
Укажи путь к secrets.json [secrets.json]:
```

Значения сохраняются в `stepik_config.json` (в `.gitignore` — не коммитится).
Структура директорий внутри корневой папки:

```
StepikTasks/
└── <курс>/<секция>/<урок>/<NN>/ или <NN-шаг>/
```

Подробнее о том, как `downloader.py` раскладывает файлы задачи и ищет
тест-кейсы, — в [grader-workflow.md § Шаг скачивания задачи](grader-workflow.md#шаг-скачивания-задачи).

---

## Таймауты

### Таймаут subprocess (режимы 1–3)

Константа `TIMEOUT_SECONDS` в `core/grader_core.py` (значение из
`CONFIG.timeout_seconds`, по умолчанию `10.0` с) защищает от зависания решения —
передаётся в `timeout=` у `proc.communicate()`:

```python
TIMEOUT_SECONDS: float = 10.0  # секунд
```

### Таймаут executor

В `core/executor.py` таймаут берётся из переменной окружения `EXECUTOR_TIMEOUT`
(по умолчанию `10` с, соответствует `CONFIG.executor_timeout`). На Unix —
`signal.alarm`; на Windows (где `SIGALRM` недоступен) защита обеспечивается
таймаутом subprocess уровня `core/grader_core.py`:

```python
TIMEOUT: int = int(os.environ.get("EXECUTOR_TIMEOUT", "10"))
```

### Microbench (режим 4)

Замер режима 4 обёрнут фиксированным `subprocess.run(timeout=60)` вокруг всего
цикла (5 повторов × N итераций). Локального per-call таймаута нет — см.
[Ограничения и безопасность](#ограничения-и-безопасность).

---

## Замер памяти дочернего процесса

```python
MEASURE_CHILD_MEMORY: bool = True  # False — быстрее, но грубее
```

- `True` (по умолчанию) — мониторинг дочернего процесса через `psutil` в
  отдельном потоке (честнее, но медленнее).
- `False` — RSS родительского процесса (быстро, приблизительно).

> Режим 4 (micro-bench) для stdin-блоков меряет пик **Python-heap через
> `tracemalloc`** (колонка `Py-heap`), а для function-блоков — RSS. `tracemalloc`
> не видит аллокации C-расширений (numpy и т.п.) — для чистого Python это
> приемлемо (issue #66).

---

## Лимит тест-кейсов для microbench

```python
MICROBENCH_MAX_CASES = 5
```

Ограничивает число тест-кейсов при `timeit`-замерах (режим 4) для стабильного
std-dev.

---

## Формат тест-кейсов

> **Единственный канонический источник по форматам тестов.** Остальные документы
> (README, grader-workflow.md, CONTRIBUTING.md) ссылаются сюда, а не дублируют.

Тест-кейсы лежат в папке `tests/` рядом с файлом(ами) решения:

```
module1/
└── task1/
    ├── task1_1.py        # основное решение
    ├── task1_2.py        # альтернативное решение 1
    └── tests/
        ├── 1             # входные данные теста №1 (stdin)
        ├── 1.clue        # ожидаемый вывод теста №1
        ├── 1.type        # тип теста: файл присутствует только для function-style задач,
        │                 # содержит строку "function"
        ├── 2
        ├── 2.clue
        └── ...
```

Файлы тестов читаются в кодировке UTF-8 (`CONFIG.encoding`).

### Типы тестов (`*.type`)

| Значение в файле | Когда создаётся | Поведение |
|---|---|---|
| *(файл отсутствует)* | stdin-задача | входные данные подаются через `stdin` |
| `function` | function-style задача | входные данные — объявление переменной (`x = 5`), передаётся через `exec` |

### Три формата (автодетект)

Грейдер автоматически распознаёт три формата тест-кейсов:

| Формат | Файлы | Источник |
|---|---|---|
| **1 — Legacy** | `1`, `1.clue`, `2`, `2.clue` в `tests/` | Stepik ZIP / `downloader.py` (создаётся автоматически при скачивании) |
| **2 — Именованные** | `input_1.txt` + `expected_1.txt`, `input_2.txt` + `expected_2.txt`, … | ручное добавление |
| **3 — python-generation** (приоритет) | `tests/input.txt` + `tests/output.txt` с маркерами `# TEST_N:` | репозитории python-generation |

Format 3 используется репозиториями
[python-generation/Professional](https://github.com/python-generation/Professional),
[python-generation/OOP](https://github.com/python-generation/OOP),
[python-generation/Samurai](https://github.com/python-generation/Samurai). Stepik
ZIP-архивы автоматически конвертируются в Format 3 при скачивании через
`downloader.py`; GitHub-ссылки в тексте задачи обрабатываются автоматически.

> При скачивании задачи через `downloader.py` файлы `tests/N`, `tests/N.clue` и
> при необходимости `tests/N.type` создаются **автоматически** из ZIP-архива или
> HTML-таблицы в тексте задачи. Если ни ZIP, ни таблицы нет — папку `tests/`
> нужно заполнить вручную. См. [grader-workflow.md § Шаг скачивания задачи](grader-workflow.md#шаг-скачивания-задачи).

### Вердикты тест-кейсов

| Вердикт | Значение |
|---------|----------|
| AC | Accepted — вывод совпал с ожидаемым |
| WA | Wrong Answer — вывод не совпал |
| TLE | Time Limit Exceeded — превышен таймаут |
| RE | Runtime Error — процесс завершился с ненулевым кодом |

---

## Ограничения и безопасность

**Threat model: решения запускаются БЕЗ полноценного sandbox на уровне ОС.**
Дочерний процесс имеет тот же доступ к файловой системе, сети и переменным
окружения, что и сам grader. Защита по времени выполнения есть всегда
(таймаут); на POSIX (Linux/macOS) есть ещё best-effort лимит памяти
(`GraderConfig.max_memory_mb`, по умолчанию 1024 МБ — `resource` через `RLIMIT_AS`
по pid после spawn); на Windows этого лимита нет (`resource` недоступен), решение
может использовать сколько угодно памяти. Ограничений диска или сети нет ни на
одной платформе. Запускай только доверенные решения (свои собственные или
скачанные из Stepik as-is) — grader не предназначен для проверки произвольного
untrusted-кода (`core/executor.py` явно задокументирован как «нет sandbox на
уровне ОС» в [`CLAUDE.md`](../CLAUDE.md)).

- **Режимы 1–3 (`grader_core.run_single_test`):** решение запускается напрямую
  через `subprocess.Popen` (для function-mode — во временном wrapper-скрипте,
  импортирующем функцию решения). Единственная защита — `timeout=` у
  `proc.communicate()` (`grader_core.TIMEOUT_SECONDS`, по умолчанию 10 с);
  `core/executor.py` (`run_solution`) реализует ту же модель с `signal.alarm`
  на Unix, но используется только в тестах, не в самом grader'е.
- **Режим 4 (`core/microbench_runner.py`):** решения запускаются через
  subprocess (`python -c`) с `timeit.repeat`, защищены фиксированным
  `subprocess.run(timeout=60)`. Исходник передаётся через временный файл;
  `stdin` сбрасывается перед каждой итерацией, а `stdout` решения
  перенаправляется в `os.devnull` на время замера, чтобы его вывод не
  смешивался с числами-таймингами.
- **Microbench: локальный per-call таймаут отсутствует** — решение, зависающее
  внутри одного вызова (не в бесконечном цикле верхнего уровня), упрётся в
  общий 60-секундный `subprocess.run(timeout=60)` вокруг всего замера
  (5 повторов × N итераций), а не в индивидуальный лимит на итерацию.
  Сообщение об ошибке при таймауте указывает `number=<N>` (сколько итераций
  было в замере), чтобы хотя бы приблизительно понять масштаб зависания
  (issue #47 R-01).

---

## Диагностика конфигурационных ошибок

| Симптом | Причина | Что делать |
|---|---|---|
| `⚠️ Тесты не найдены для: <name>` | нет папки `tests/` рядом с решением или неверный формат | создать `tests/` (см. [Формат тест-кейсов](#формат-тест-кейсов)) или скачать через `python -m stepik_grader.downloader` |
| Параметры из `pyproject.toml` не применились | опечатка в имени ключа (незнакомые ключи молча игнорируются) | сверить имена с таблицей [`[tool.stepik-grader]`](#toolstepik-grader-в-pyprojecttoml) |
| Лимит памяти не срабатывает | Windows (`resource` недоступен) | ограничение POSIX-only — см. [Ограничения и безопасность](#ограничения-и-безопасность) |
| Предупреждение об «осиротевших» файлах Формата 1/2 при Формате 3 | в `tests/` смешаны форматы; лишние файлы игнорируются | оставить один формат тест-кейсов на папку |
| Проблемы с токеном/авторизацией Stepik | `secrets.json`/OAuth | `python -m stepik_grader.diagnostic_stepik` — см. [installation.md](installation.md#работа-с-api-stepik-oauth) |
