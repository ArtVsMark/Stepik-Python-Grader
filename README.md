# Stepik Python Grader

[![CI](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtVsMark/Stepik-Python-Grader/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ArtVsMark/Stepik-Python-Grader)](https://github.com/ArtVsMark/Stepik-Python-Grader/releases)
![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)
![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue)

> **Status:** Stable

> Локальный грейдер для курсов «Поколение Python» на Stepik.
> Скачивает данные задачи с сайта и позволяет не только проверить решение локально, но и **сравнить несколько решений более честно**: сначала по корректности, потом по benchmark-метрикам.

[Первоисточник грейдера](https://github.com/PavloOps/python_generation_grader)

Курсы:
- [Поколение Python: Курс для начинающих](https://stepik.org/course/58852)
- [Поколение Python: Курс для продвинутых](https://stepik.org/course/68343)
- [Поколение Python: Курс для профессионалов](https://stepik.org/course/82541)
- [Поколение Python: ООП](https://stepik.org/course/98974)
- [Поколение Python: Курс для самураев](https://stepik.org/course/134318)

---

## Основные возможности

- ✅ Запуск решений против наборов тест-кейсов (`tests/N` + `tests/N.clue`)
- 📋 **Автоматическое извлечение тест-кейсов** из HTML-таблицы в тексте задачи Stepik
- 📦 **Автоскачивание тестов из ZIP-архива** по ссылке в тексте задачи
- 🔗 Обнаружение ссылок на GitHub-тесты с подсказкой скачать вручную
- 📊 Сравнение нескольких решений одной задачи в таблице
- 🚀 Subprocess-бенчмарк с замером времени и памяти (режим 3)
- ⚡ Timeit-микробенчмарк через subprocess (режим 4)
- 🎨 Цветной вывод через `rich` — зелёный OK/AC, красный WA/TLE/RE, жёлтый SLOWER
- 🔍 Diff при WA — сравнение ожидаемого и фактического вывода при провале теста
- ⚖️ Вердикты AC / WA / TLE / RE по каждому тест-кейсу
- 🌐 Локальный веб-интерфейс (`--serve`) и интеграция с VS Code / PyCharm
- 🧩 pytest-плагин (`pytest --grader-mode`), кэш результатов, `--watch`
- 🔍 Диагностика окружения и авторизация через Stepik API

Разбор по модулям и слоям — в [docs/architecture.md](docs/architecture.md).

---

## Быстрый старт

**Установить** (проще всего через [pipx](https://pipx.pypa.io)):

```bash
pipx install stepik-python-grader
```

**Запустить** интерактивное меню:

```bash
python -m stepik_grader       # надёжный способ (работает всегда)
stepik-grader                 # если команда в PATH
```

**Или проверить одно решение без интерактива:**

```bash
stepik-grader --mode 1 --file task.py
```

Полная установка (из исходников, venv, Windows-заметки, настройка OAuth) — в
[docs/installation.md](docs/installation.md). Пошаговый первый пример, режимы
1–4, CLI-флаги, скачивание задач и форматы тестов — в
[docs/grader-workflow.md](docs/grader-workflow.md).

---

## Документация

Полная база знаний — в [`docs/`](docs/README.md):

| Тема | Документ |
|---|---|
| Установка, OAuth, secrets.json, диагностика | [docs/installation.md](docs/installation.md) |
| Режимы работы, CLI-флаги, web/IDE, скачивание задачи | [docs/grader-workflow.md](docs/grader-workflow.md) |
| Дизайн WEB MVP (проверка решений + Downloader + Глоссарий-модуль, микро-бенчмарк, error/action cards) | [docs/web-mvp.md](docs/web-mvp.md) |
| Справочник: конфигурация, форматы тест-кейсов, ограничения и безопасность | [docs/configuration.md](docs/configuration.md) |
| Архитектура: модули, слои, граф зависимостей | [docs/architecture.md](docs/architecture.md) |
| Локальный глоссарий: формат, API, источник истины контента | [docs/glossary.md](docs/glossary.md) |
| Структура проекта (дерево файлов) | [docs/project-structure.md](docs/project-structure.md) |
| Версии и сравнение с оригиналом | [docs/versions.md](docs/versions.md) |
| Полный список изменений | [CHANGELOG.md](CHANGELOG.md) |
| Как внести вклад, код-стайл, версионирование | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Инварианты ядра и правила для агентов | [CLAUDE.md](CLAUDE.md) |

> Правило против дублей: каждая тема канонически живёт в одном файле, остальные
> ссылаются — см. [docs/README.md § Канонические источники](docs/README.md#канонические-источники-правило-против-дублей).

---

## Безопасность (кратко)

**Решения запускаются БЕЗ полноценного sandbox на уровне ОС.** Есть таймаут
выполнения (всегда) и best-effort лимит памяти на POSIX; изоляции ФС/сети нет.
Запускай только доверенные решения (свои или скачанные из Stepik as-is).
Подробная threat model — в
[docs/configuration.md § Ограничения и безопасность](docs/configuration.md#ограничения-и-безопасность).
Как сообщить об уязвимости — [SECURITY.md](SECURITY.md).

---

## Python версия

Python **3.12+** (3.14 — экспериментальная).

---

## Лицензия

[MIT](LICENSE) © Artem Markitanov (ArtVsMark).
