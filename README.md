# Stepik Python Grader

[![CI](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ArtVsMark/Stepik-Python-Grader)](https://github.com/ArtVsMark/Stepik-Python-Grader/releases)
[![PyPI](https://img.shields.io/pypi/v/stepik-python-grader)](https://pypi.org/project/stepik-python-grader/)
[![Version](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/.github/badges/version.json&cacheSeconds=300)](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CHANGELOG.md)
[![Coverage (ubuntu)](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/.github/badges/coverage.json&cacheSeconds=300)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
[![Coverage (all OS combined)](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/.github/badges/coverage-combined.json&cacheSeconds=300)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
<!-- Бейджей покрытия два не случайно: что именно меряет каждый — CONTRIBUTING.md § Покрытие. -->
[![Glossary](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/.github/badges/glossary.json&cacheSeconds=300)](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/dev/glossary.md)
[![Good first issues](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/.github/badges/good-first-issues.json&cacheSeconds=300)](https://github.com/ArtVsMark/Stepik-Python-Grader/labels/good%20first%20issue)
![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%28exp%29-blue)

> **Status:** Stable &nbsp;·&nbsp; 🇬🇧 [English quick start & generic mode](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/README.en.md)

> Локальный грейдер для курсов «Поколение Python» на Stepik.
> Скачивает данные задачи с сайта и позволяет не только проверить решение локально, но и **сравнить несколько решений более честно**: сначала по корректности, потом по benchmark-метрикам.
>
> Сверх обычного прогона тестов: **офлайн-глоссарий Python** с переходом прямо
> из ошибки, **пошаговый трассировщик** с memory-graph, **микробенчмарк**
> `timeit`, **OS-песочница** для Linux/macOS/Windows и **AI-объяснение падений**
> (opt-in, свой ключ).

```bash
pipx install stepik-python-grader && stepik-grader
```

![Веб-интерфейс --serve: грейдинг папки решений против тест-кейсов с вердиктом OK и таблицей результатов](https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/docs/assets/hero-serve.gif)

> Форк / продолжение проекта: [Первоисточник грейдера](https://github.com/PavloOps/python_generation_grader)
>
> 💬 **Нашли баг или есть идея?** Пункт `9` в меню грейдера и кнопка 💬 в
> веб-интерфейсе открывают форму [issue](https://github.com/ArtVsMark/Stepik-Python-Grader/issues/new/choose)
> уже заполненной (версия, ОС, Python подставятся сами). Вопрос, а не баг — в
> [Discussions](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions).

Курсы:
- [Поколение Python: Курс для начинающих](https://stepik.org/course/58852)
- [Поколение Python: Курс для продвинутых](https://stepik.org/course/68343)
- [Поколение Python: Курс для профессионалов](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)
- [Поколение Python: Курс для самураев](https://stepik.org/course/134318)

---

## Зачем это, если Stepik уже проверяет решения?

Встроенный чекер Stepik даёт «зачёт / не зачёт» — и только после сабмита. Вот
чем грейдер отличается от двух реальных альтернатив:

| | Чекер Stepik | `pytest` вручную | Этот грейдер |
|---|:---:|:---:|:---:|
| Проверка без сабмита и лимита попыток | ❌ | ✅ | ✅ |
| Тест-кейсы задачи скачиваются сами | ✅ | ❌ | ✅ |
| Сравнение своих решений по времени и памяти | ❌ | ❌ | ✅ (режимы 3/4) |
| Diff при неверном выводе | ❌ | ~ | ✅ |
| Разбор ошибки: глоссарий, трейс, AI-объяснение | ❌ | ❌ | ✅ |
| «Подучить» — свои частые ошибки из истории | ❌ | ❌ | ✅ |
| Код остаётся на машине | ❌ | ✅ | ✅ |

Детальное сравнение с проектом-первоисточником — в [docs/use/versions.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/versions.md#что-изменилось-по-сравнению-с-оригиналом).

---

## Основные возможности

- ✅ Запуск решений против наборов тест-кейсов (`tests/N` + `tests/N.clue`)
- 📋 **Автоматическое извлечение тест-кейсов** — из HTML-таблицы задачи, из
  ZIP-архива по ссылке, плюс подсказка при тестах на GitHub
- 📊 Сравнение нескольких решений одной задачи в таблице
- 🚀 Subprocess-бенчмарк с замером времени и памяти (режим 3)
- ⚡ Timeit-микробенчмарк через subprocess (режим 4)
- ⚖️ Вердикты AC / WA / TLE / RE по каждому кейсу: цветной вывод через `rich` и
  diff «ожидалось / получено» при WA
- 🌐 Локальный веб-интерфейс (`--serve`, `http://127.0.0.1:8000`) и интеграция с VS Code / PyCharm
- 🖥 GUI-лаунчер веб-интерфейса без командной строки (`stepik-grader-gui`) —
  на Windows ярлык без консольного окна
- 🧩 pytest-плагин (`pytest --grader-mode`), кэш результатов и `--watch`
  (extra `[watch]`); Playwright e2e-смоук фронтенда с регрессией на XSS
  (extra `[e2e]`, см. [CONTRIBUTING.md § E2E-тесты](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CONTRIBUTING.md#e2e-тесты-playwright-опционально))
- 📚 Локальный глоссарий (объём — в бейдже Glossary выше): функции, исключения и
  конструкции, детектор недостающих терминов, deep-link из error cards
- 🎓 Правила PEP 8 и раздел «Подучить» — частые ошибки из истории прогонов с
  затуханием (`--insights` / `--lint`)
- 📈 Локальная статистика прогонов (`--stats`) и SQLite-история (`--history`) — без сети
- 🤖 AI-объяснение падений WA/RE (`--ai-hints`) — opt-in, на своём ключе (BYOK:
  локальная ollama или облако), с заземлением на карточки глоссария; без
  настройки и явного согласия ничего в сеть не уходит
- 🔒 Опциональная OS-песочница исполнения решений (`--sandbox`)
- 🔍 Диагностика окружения и авторизация через Stepik API

> **Только в вебе — CLI-аналога нет.** «Песочница» (запуск кода со своим stdin,
> не путать с OS-изоляцией `--sandbox`), пошаговый трейс с memory-graph,
> редактор решения с сохранением, «Отправить в Stepik», а также интерактивные
> «Глоссарий», «Правила (PEP)», «Подучить» и «Прогресс» — в терминале от них
> есть только сводки `--insights`/`--lint` и экспорт `--export-progress`. Обзор
> разделов — [docs/use/web-interface.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/web-interface.md).

Разбор по модулям и слоям — в [docs/dev/architecture.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/dev/architecture.md).

### Как это выглядит (`--serve`)

| Проверка папки решений (режим 2) | Офлайн-глоссарий Python |
|---|---|
| ![Таблица результатов веб-интерфейса: task.py — 5 из 5 тест-кейсов пройдено, вердикт OK, время и память](https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/docs/assets/serve-results.png) | ![Раздел «Глоссарий»: список карточек и открытая карточка оператора % с синтаксисом и примерами кода](https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/docs/assets/serve-glossary.png) |

---

## Быстрый старт

**Установить** — `pipx install stepik-python-grader` (см. первый экран) или
[другие способы](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/installation.md). **Запустить** интерактивное меню:

```bash
python -m stepik_grader       # надёжный способ (работает всегда)
stepik-grader                 # если команда в PATH
```

**Или веб-интерфейс** (только localhost) — те же режимы 1–4 в браузере плюс
разделы, которых в CLI нет (см. § Основные возможности):

```bash
stepik-grader --serve         # http://127.0.0.1:8000 (другой порт — --port)
```

**Совсем без командной строки** — окно-лаунчер веб-интерфейса:
выбор варианта запуска («Простой сервер» / «Сервер с изоляцией `--sandbox`»),
порта (с проверкой «занят») и рабочей папки, кнопки «Запустить»/«Остановить» и
авто-открытие браузера:

```bash
stepik-grader-gui                  # на Windows — ярлык без консольного окна
python -m stepik_grader.launcher   # то же окно из терминала
```

**Или проверить одно решение без интерактива:**

```bash
stepik-grader --mode 1 --file task.py
```

Полная установка (из исходников, venv, Windows-заметки, настройка OAuth) — в
[docs/use/installation.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/installation.md). Пошаговый первый пример, режимы
1–4, CLI-флаги, скачивание задач и форматы тестов — в
[docs/use/grader-workflow.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/grader-workflow.md).

---

## Документация

База знаний — в [`docs/`](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/README.md), разложена по четырём направлениям:

| Направление | Для кого | Что внутри |
|---|---|---|
| [**docs/use/**](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/README.md) | пользователь | установка и OAuth, режимы 1–4 и CLI-флаги, веб-интерфейс, конфигурация, форматы тест-кейсов, отличия от первоисточника |
| [**docs/dev/**](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/dev/README.md) | контрибьютор | архитектура и дерево модулей, HTTP API, контракты данных, 11 ADR, дизайн незапущенного server mode |
| [**docs/agent/**](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/agent/README.md) | Claude Code | шаблон ролей, очередь работ после крупного аудита |
| [**docs/archive/**](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/archive/README.md) | по необходимости | история разработки, архив CHANGELOG, разовые аудиты |

Рядом с кодом: [CHANGELOG.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CHANGELOG.md) — что изменилось в релизах,
[CONTRIBUTING.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CONTRIBUTING.md) — как внести вклад,
[CLAUDE.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CLAUDE.md) — инварианты ядра для агентов.

> Два правила этой документации: **одна тема — один файл** (остальные
> ссылаются, а не копируют) и **в активном документе нет журнала работ** (что
> сделано — в CHANGELOG, что предстоит — в Issues). Подробнее —
> [docs/README.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/README.md).

---

## Безопасность (кратко)

**По умолчанию решения запускаются БЕЗ полноценного sandbox на уровне ОС.** Есть
таймаут выполнения (всегда) и best-effort лимит памяти на POSIX; изоляции ФС/сети
по умолчанию нет. Опциональная OS-изоляция включается флагом `--sandbox`
(`core/sandbox/`, три backend'а) — и в CLI (режимы 1–4), и в web
(`--serve --sandbox`; пошаговый трейс под ней недоступен). Без
`--sandbox` запускай только доверенные решения (свои или скачанные из Stepik
as-is).
Подробная threat model — в
[docs/use/configuration.md § Ограничения и безопасность](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/configuration.md#ограничения-и-безопасность).
Как сообщить об уязвимости — [SECURITY.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/SECURITY.md).

---

## Прозрачность и доверие

- ✅ **Автотесты на каждый PR** (pytest), CI-матрица на 3 ОС × Python 3.12/3.13
  (+3.14 экспериментально) — живые бейджи покрытия single-OS и cross-OS в шапке.
- 🧠 **Строгий mypy** (`disallow_untyped_defs`, `warn_return_any`, …) + `ruff`
  (lint + format) в pre-commit и CI — типы и стиль проверяются на каждый PR.
- 🔐 **Приватный репорт уязвимостей** (GitHub Private Vulnerability Reporting) +
  документированная threat model — [SECURITY.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/SECURITY.md).
- 📦 **Публикация на PyPI через OIDC trusted publishing** — без хранимого токена
  в секретах; релизный dist собирается один раз в CI.
- 📜 **MIT**, открытая история изменений — [CHANGELOG.md](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CHANGELOG.md).

---

## Первый вклад за 15 минут

Новичок? Возьмите issue с меткой
[`good first issue`](https://github.com/ArtVsMark/Stepik-Python-Grader/labels/good%20first%20issue)
— это задачи с понятным объёмом и ссылками на канон. Пошаговый онбординг (форк →
ветка от `main` → локальные гейты `pytest`/`ruff`/`mypy` → PR по Conventional
Commits) — в [CONTRIBUTING.md § Первый вклад за 15 минут](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/CONTRIBUTING.md#первый-вклад-за-15-минут).
Вопросы, идеи и «покажу своё» — в
[Discussions](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions).

---

## Python версия

Python **3.12+** (3.14 — экспериментальная).

---

## Лицензия

[MIT](https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/LICENSE) © Artem Markitanov (ArtVsMark).
