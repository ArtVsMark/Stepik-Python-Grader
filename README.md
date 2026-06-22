# Stepik Python Grader

Локальный грейдер для курсов «Поколение Python» на Stepik.  
Скачивает тесты к задаче с сайта и позволяет не только проверить решение локально, но и **сравнить несколько решений более честно**: сначала по корректности, потом по benchmark-метрикам.

[Первоисточник грейдера](https://github.com/PavloOps/python_generation_grader)

Курсы:
- [Поколение Python: Профи](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)

---

## Что умеет

| Скрипт | Что делает |
|---|---|
| `at_first.py` | Создаёт папку задачи и скачивает тесты через API Stepik |
| `test.py` | Проверяет решения локально, сравнивает несколько решений, запускает benchmark |
| `executor.py` | Хелпер для запуска function-only решений |
| `diagnoctik-stepik.py` | Диагностика: проверяет структуру ответа API и наличие ZIP |

---

## Быстрый старт

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

---

## Шаг 2 — Скачать тесты к задаче

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
    ├── task.py
    ├── task_1.py
    ├── task_2.py
    ├── README.md
    └── tests/
        ├── 1
        ├── 1.clue
        └── ...
```

---

## Шаг 3 — Проверка и сравнение решений

```bash
python test.py
```

Теперь у `test.py` **три режима**.

```text
Choose mode:
1 - test single file
2 - compare all solutions in top-level folder
3 - benchmark passed solutions
```

### Режим 1 — проверить один файл

Подходит, когда хочешь быстро прогнать одно решение.

```text
Enter py-file's path from the content root: P2.2/step-4-название-задачи/task.py
```

Пример вывода:

```text
P2.2/step-4-название-задачи/task.py: 10/10 tests, total=0.4231s, avg=0.0423s, peak_memory=48.03 MB, status=OK
```

### Режим 2 — сравнить все решения в папке

Этот режим нужен для **проверки корректности** нескольких файлов сразу. Он показывает:
- сколько тестов прошло каждое решение,
- общее время,
- среднее время на тест,
- пиковую память,
- статус (`OK`, `FAILED`, `NO TESTS`).

```text
Enter top-level folder from the content root: P2.2
```

Пример вывода:

```text
📂 P2.2/step-4-название-задачи
------------------------------------------------------------------
File                       Passed    Total time    Avg time     Peak memory      Status   Fail test
------------------------------------------------------------------
P2.2/.../task.py            10/10        0.4231      0.0423           48.03          OK          -
P2.2/.../task_1.py          10/10        0.3870      0.0387           48.04          OK          -
P2.2/.../task_2.py           8/10        0.3521      0.0352           48.02      FAILED          9
```

> Ширина колонки `File` подстраивается автоматически под длину самого длинного пути в группе.  
> Этот режим **не является полноценным benchmark**. Он нужен в первую очередь для проверки правильности решений.

### Режим 3 — benchmark только для прошедших решений

Это режим для **более объективного сравнения производительности**.

Сначала `test.py` отбирает только те решения, которые прошли все тесты. Затем запускает повторные прогоны и считает статистику.

После выбора режима появится профиль нагрузки:

```text
Benchmark load:
1 - low (5 repeats)
2 - medium (15 repeats)
3 - high (50 repeats)
4 - custom
```

Если выбрать `custom`, можно ввести число повторов от `5` до `100`.

### Что показывает benchmark

| Поле | Значение |
|---|---|
| `Runs` | сколько всего запусков было выполнено |
| `Min` | лучший замер |
| `Median` | медианное время, главный ориентир |
| `Mean` | среднее время |
| `Max` | худший замер |
| `Stdev` | стандартное отклонение, показывает разброс замеров |
| `Memory` | пиковая память |
| `Relative` | относительное время к лучшему решению |
| `Verdict` | итоговая оценка (`SIMILAR`, `SLOWER`, `MUCH SLOWER`) |

Пример вывода:

```text
🚀 Benchmark: P2.2/step-4-название-задачи
-------------------------------------------------------------------------------------------
File                       Runs       Min    Median      Mean       Max     Stdev    Memory  Relative    Verdict
-------------------------------------------------------------------------------------------
P2.2/.../task.py             70   0.03120   0.03410   0.03440   0.03790   0.00120     48.03    100.0%    SIMILAR
P2.2/.../task_1.py           70   0.03110   0.03400   0.03420   0.03810   0.00130     48.04    100.0%    SIMILAR
P2.2/.../task_2.py           70   0.03150   0.03680   0.03710   0.04140   0.00160     48.02    108.2%     SLOWER
```

> Ширина колонки `File` подстраивается автоматически под длину самого длинного пути в группе.

### Что такое `Stdev`

`Stdev` — это стандартное отклонение. Простыми словами:
- маленькое значение → замеры стабильные;
- большое значение → результаты скачут, и benchmark шумный.

---

## Диагностика

Если `at_first.py` не нашёл ZIP автоматически:

```bash
python diagnoctik-stepik.py
```

Скрипт сохранит:
- `lesson_debug.json`,
- `step_debug.json`,
- `diagnostic_result.json`

в папку `stepik_diagnostics/`.

---

## Структура репозитория

```text
.
├── at_first.py
├── test.py
├── executor.py
├── diagnoctik-stepik.py
├── requirements.txt
├── secrets.json.example
├── stepik_config.json.example
├── .gitignore
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

## Что изменилось в `test.py`

По сравнению со старым вариантом:
- сравнение корректности и benchmark разделены,
- benchmark работает только для полностью прошедших решений,
- добавлены профили нагрузки `low / medium / high / custom`,
- результаты оцениваются по `median`, а не по случайному одиночному замеру,
- добавлены `mean`, `max`, `stdev`, `relative_percent` и `verdict`.

Это делает сравнение решений заметно более честным и полезным для обучения.

### Changelog

| Дата | Изменение |
|---|---|
| 2026-06-22 | Динамическая ширина колонки `File` в таблицах режимов 2 и 3 — шапка больше не съезжает при длинных именах папок и файлов (`_file_col_width()`) |

---

## Python версия

Python **3.10+**
