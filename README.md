# Stepik Python Grader

Локальный грейдер для курсов «Поколение Python» на Stepik.  
Скачивает тесты к задаче прямо с сайта и проверяет твоё решение локально.

Курсы:
- [Поколение Python: Профи](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)

---

## Что умеет

| Скрипт | Что делает |
|---|---|
| `at_first.py` | Создаёт папку задачи, скачивает тесты через API Stepik |
| `test.py` | Запускает тесты локально, сравнивает вывод с эталоном |
| `executor.py` | Хелпер для запуска function-only решений |
| `diagnoctik-stepik.py` | Диагностика: проверяет структуру ответа API и наличие ZIP |

---


## Быстрый старт

### Шаг 0 — Настройка (один раз)

**1. Создай OAuth-приложение на Stepik**

1. Зайди на https://stepik.org/oauth2/applications/
2. Нажми **«+ New Application»**
3. Заполни поля:

| Поле | Значение |
|---|---|
| Name | любое, например `my-grader` |
| Client type | `Confidential` |
| Authorization grant type | `Authorization code` |
| Redirect uris | `http://localhost:8080/callback` |

4. Нажми **Save** — ты увидишь `Client ID` и `Client Secret`

**2. Создай `secrets.json`**

Скопируй шаблон и заполни своими значениями:

```bash
cp secrets.json.example secrets.json
```

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

> ⚠️ `secrets.json` уже добавлен в `.gitignore` — он **не попадёт** в репозиторий.  
> Поля `access_token`, `refresh_token`, `expires_at` оставь пустыми — скрипт заполнит их сам.

---

### Шаг 1 — Скачать тесты к задаче

```bash
python at_first.py
```

При первом запуске скрипт создаст `stepik_config.json`, затем откроется браузер для подтверждения — **только один раз**. При последующих запусках используется сохранённый `refresh_token`.

Введи URL шага:
```
Enter Stepik step URL: https://stepik.org/lesson/569749/step/4?unit=564263
```

Скрипт создаст структуру:

```
P2.2/
└── step-4-название-задачи/
    ├── task.py        ← сюда пишешь решение
    ├── task_1.py
    ├── task_2.py
    ├── README.md      ← условие задачи
    └── tests/
        ├── 1
        ├── 1.clue
        └── ...
```

---

### Шаг 2 — Проверить решение

```bash
python test.py
```

```
Choose mode:
1 - test single file
2 - compare all solutions in top-level folder
```

Пример вывода (режим 2):

```
📂 step-4-название-задачи
──────────────────────────────────────────────────────────────
File              Passed    Total time    Avg time    Status
──────────────────────────────────────────────────────────────
task.py           10/10       0.4231        0.0423      OK
task_1.py         10/10       0.3870        0.0387      OK
task_2.py          8/10       0.3521        0.0352    FAILED
```

---

## Диагностика

Если `at_first.py` не находит ZIP автоматически:

```bash
python diagnoctik-stepik.py
```

Сохранит `lesson_debug.json`, `step_debug.json` и `diagnostic_result.json` в `stepik_diagnostics/`.

---

## Структура репозитория

```
.
├── at_first.py
├── test.py
├── executor.py
├── diagnoctik-stepik.py
├── requirements.txt
├── secrets.json.example       ← скопируй в secrets.json и заполни
├── stepik_config.json.example ← создаётся автоматически при первом запуске
├── .gitignore
└── README.md
```

> Папки с задачами, `secrets.json`, `stepik_config.json`, debug-файлы — в `.gitignore`.

---

## Python версия

Python **3.10+**
