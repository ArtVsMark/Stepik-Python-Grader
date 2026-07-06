# База знаний Stepik Python Grader

> Карта документации проекта (issue #172, #178 / эпик #102, PR-13). Обзор и
> быстрый старт — в корневом [README](../README.md).

## Куда идти

| Хочу… | Документ |
|---|---|
| Установить (pipx / из исходников), настроить OAuth, диагностика | [installation.md](installation.md) |
| Запустить грейдер, режимы 1–4, CLI-флаги, web/IDE, скачать задачу, форматы тестов, конфигурация | [grader-workflow.md](grader-workflow.md) |
| Понять архитектуру: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) |
| Посмотреть дерево файлов проекта | [project-structure.md](project-structure.md) |
| Сравнить версии и отличия от оригинала | [versions.md](versions.md) |
| Полный список изменений | [../CHANGELOG.md](../CHANGELOG.md) |
| Внести вклад: код-стайл, форматы тестов, версионирование | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Инварианты ядра и правила для агентов | [../CLAUDE.md](../CLAUDE.md) |

## Канонические источники (правило против дублей)

Каждая тема живёт ровно в одном каноническом файле. Остальные документы
**ссылаются** на него, а не копируют содержимое. При обновлении темы правь
только её канонический файл (issue #178).

| Тема | Канонический источник | Не дублировать в |
|---|---|---|
| Обзор проекта, бейджи, основные возможности | [README](../README.md) | docs/* |
| Установка, OAuth, secrets.json, диагностика | [installation.md](installation.md) | README (только короткий quick start) |
| Режимы работы, CLI-флаги, скачивание задачи, форматы тестов, конфигурация | [grader-workflow.md](grader-workflow.md) | README, CONTRIBUTING |
| Архитектура: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) | README, CLAUDE.md (там — инварианты, не дублирующее описание) |
| Дерево файлов проекта | [project-structure.md](project-structure.md) | README |
| Сравнение версий, отличия от оригинала | [versions.md](versions.md) | README |
| История релизов (детальный changelog) | [../CHANGELOG.md](../CHANGELOG.md) | versions.md (там — только качественные скачки) |
| Политика версионирования (схема тег=MINOR+1 и т.п.) | [../CONTRIBUTING.md](../CONTRIBUTING.md) § Версионирование | README, CLAUDE.md, versions.md |
| Инварианты ядра, правила для агентов | [../CLAUDE.md](../CLAUDE.md) | docs/* |

> **Версия проекта — без ручного source of truth в доках.** Актуальный номер
> берётся из git-тега / `importlib.metadata` (бейдж релиза в README тянет
> `github/v/release`), а схема нумерации канонически описана в
> [CONTRIBUTING.md](../CONTRIBUTING.md). Не вписывай `version-X.Y.Z` вручную в
> README как единственный источник истины.
