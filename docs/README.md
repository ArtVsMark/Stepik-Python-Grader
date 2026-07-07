# База знаний Stepik Python Grader

> Карта документации проекта (issue #172, #178 / эпик #102, PR-13). Обзор и
> быстрый старт — в корневом [README](../README.md).

## Куда идти

| Хочу… | Документ |
|---|---|
| Установить (pipx / из исходников), настроить OAuth, диагностика | [installation.md](installation.md) |
| Запустить грейдер, режимы 1–4, CLI-флаги, web/IDE, скачать задачу | [grader-workflow.md](grader-workflow.md) |
| Дизайн WEB MVP: три блока (Проверка решений, Downloader, Глоссарий-модуль), микро-бенчмарк, error/action cards | [web-mvp.md](web-mvp.md) |
| Справочник: конфигурация (`[tool.stepik-grader]`), форматы тест-кейсов, ограничения и безопасность | [configuration.md](configuration.md) |
| Локальный глоссарий: формат JSON карточек/очереди, Python-API (`stepik_grader.glossary`) | [glossary.md](glossary.md) |
| Понять архитектуру: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) |
| Посмотреть дерево файлов проекта | [project-structure.md](project-structure.md) |
| Сравнить версии и отличия от оригинала | [versions.md](versions.md) |
| Полный список изменений | [../CHANGELOG.md](../CHANGELOG.md) |
| Внести вклад: код-стайл, форматы тестов, версионирование | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Инварианты ядра и правила для агентов | [../CLAUDE.md](../CLAUDE.md) |
| Постановки будущих задач для Claude Code (#125/#186/#187/#129, версии #161/#163; #126 — foundation готов, доводка #190/#191) | [claude-handoff.md](claude-handoff.md) |
| История спринтов и roadmap (архив) | [history.md](history.md) |

## Канонические источники (правило против дублей)

Каждая тема живёт ровно в одном каноническом файле. Остальные документы
**ссылаются** на него, а не копируют содержимое. При обновлении темы правь
только её канонический файл (issue #178).

| Тема | Канонический источник | Не дублировать в |
|---|---|---|
| Обзор проекта, бейджи, основные возможности | [README](../README.md) | docs/* |
| Установка, OAuth, secrets.json, диагностика | [installation.md](installation.md) | README (только короткий quick start) |
| Режимы работы, CLI-флаги, web/IDE, скачивание задачи | [grader-workflow.md](grader-workflow.md) | README, CONTRIBUTING |
| Дизайн WEB MVP (два раздела / три блока: проверка + Downloader + Глоссарий-модуль, микро-бенчмарк, error/action cards, будущая архитектура web UI) | [web-mvp.md](web-mvp.md) | grader-workflow.md (там — текущий `--serve`, не дизайн) |
| Конфигурация (`[tool.stepik-grader]`), форматы тест-кейсов, ограничения и безопасность | [configuration.md](configuration.md) | README, CONTRIBUTING, grader-workflow.md |
| Формат JSON локального глоссария (карточки/очередь) и API `stepik_grader.glossary` | [glossary.md](glossary.md) | web-mvp.md (там — продуктовый дизайн, не формат хранения) |
| Архитектура: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) | README, CLAUDE.md (там — инварианты, не дублирующее описание) |
| Дерево файлов проекта | [project-structure.md](project-structure.md) | README |
| Сравнение версий, отличия от оригинала | [versions.md](versions.md) | README |
| История релизов (детальный changelog) | [../CHANGELOG.md](../CHANGELOG.md) | versions.md (там — только качественные скачки) |
| Политика версионирования (схема тег=MINOR+1, release vs dev) | [../CONTRIBUTING.md](../CONTRIBUTING.md) § Версионирование | README, CLAUDE.md, versions.md, history.md |
| Инварианты ядра, правила для агентов | [../CLAUDE.md](../CLAUDE.md) | docs/* |
| История спринтов/roadmap, подробные примечания к issue (архив) | [history.md](history.md) | CLAUDE.md (там — только действующие инварианты) |
| Постановки будущих реализаций для Claude (scope/non-goals) | [claude-handoff.md](claude-handoff.md) | CLAUDE.md (там — короткие указатели); канон продукта — web-mvp.md |

> **Версия проекта — без ручного source of truth в доках.** Актуальный номер
> берётся из git-тега / `importlib.metadata` (бейдж релиза в README тянет
> `github/v/release`), а схема нумерации канонически описана в
> [CONTRIBUTING.md](../CONTRIBUTING.md). Не вписывай `version-X.Y.Z` вручную в
> README как единственный источник истины.
