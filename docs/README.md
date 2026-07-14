# База знаний Stepik Python Grader

> Карта документации проекта (issue #172, #178 / эпик #102, PR-13). Обзор и
> быстрый старт — в корневом [README](../README.md).

## Куда идти

| Хочу… | Документ |
|---|---|
| Установить (pipx / из исходников), настроить OAuth, диагностика | [installation.md](installation.md) |
| Запустить грейдер, режимы 1–4, CLI-флаги, web/IDE, скачать задачу | [grader-workflow.md](grader-workflow.md) |
| WEB MVP: три блока (Проверка решений, Downloader, Глоссарий-модуль), микро-бенчмарк, error/action cards — что реализовано | [web-current.md](web-current.md) |
| WEB MVP: замыслы, отложенное, отклонённое | [web-design.md](web-design.md) |
| Справочник HTTP API `--serve`: эндпоинты, лимиты, коды ответов, curl-примеры | [api.md](api.md) |
| Справочник: конфигурация (`[tool.stepik-grader]`), форматы тест-кейсов, ограничения и безопасность | [configuration.md](configuration.md) |
| Локальный глоссарий: формат JSON карточек/очереди, Python-API (`stepik_grader.glossary`) | [glossary.md](glossary.md) |
| Понять архитектуру: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) |
| Контракт результата проверки (поля, вердикты) для CLI/Web/API | [result-contract.md](result-contract.md) |
| Формат JSON-трейса пошагового исполнения (песочница, `core/tracer.py`) | [trace-format.md](trace-format.md) |
| Дизайн server mode: Runner-слой, API удалённого исполнения, sandbox | [server-mode.md](server-mode.md) |
| Диагностический режим и лог-файл (редакция секретов, opt-in) | [logging.md](logging.md) |
| Архитектурные решения (ADR) | [adr/README.md](adr/README.md) |
| Посмотреть дерево файлов проекта | [project-structure.md](project-structure.md) |
| Сравнить версии и отличия от оригинала | [versions.md](versions.md) |
| Полный список изменений | [../CHANGELOG.md](../CHANGELOG.md) |
| Архив CHANGELOG: ротированные релизы 1.1.0–1.5.0 (issue #373) + до-тегового периода (до #162/#183), построчный английский лог | [changelog-archive.md](changelog-archive.md) |
| Внести вклад: код-стайл, форматы тестов, версионирование | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Инварианты ядра и правила для агентов | [../CLAUDE.md](../CLAUDE.md) |
| Политика безопасности, ответственное раскрытие уязвимостей | [../SECURITY.md](../SECURITY.md) |
| Архив постановок для Claude Code (все закрыты: #125/#186/#187/#129, #161/#163, #126/#190/#191) | [claude-handoff.md](claude-handoff.md) |
| История спринтов и roadmap (архив) | [history.md](history.md) |
| Разовый глубокий аудит 2026-07 (8 ролей) + дизайн разделов «Правила/PEP» и «Подучить» | [audit-2026-07.md](audit-2026-07.md) |
| План 2026-07: наполнение 832 draft-карточек глоссария (эпик #363) + UX-оптимизация web-«Проверки решений» (эпик #362, пункты владельца а–к) | [web-glossary-optimization-2026-07.md](web-glossary-optimization-2026-07.md) |
| Правила PEP 8 и учебные инсайты: разделы «Правила»/«Подучить», формат `RuleCard`, `core/lint.py` (эпик #342) | [rules-insights.md](rules-insights.md) |

## Канонические источники (правило против дублей)

Каждая тема живёт ровно в одном каноническом файле. Остальные документы
**ссылаются** на него, а не копируют содержимое. При обновлении темы правь
только её канонический файл (issue #178).

| Тема | Канонический источник | Не дублировать в |
|---|---|---|
| Обзор проекта, бейджи, основные возможности | [README](../README.md) | docs/* |
| Установка, OAuth, secrets.json, диагностика | [installation.md](installation.md) | README (только короткий quick start) |
| Режимы работы, CLI-флаги, web/IDE, скачивание задачи | [grader-workflow.md](grader-workflow.md) | README, CONTRIBUTING |
| WEB MVP — что реализовано (два раздела / три блока: проверка + Downloader + Глоссарий-модуль, микро-бенчмарк, error/action cards) | [web-current.md](web-current.md) | grader-workflow.md (там — текущий `--serve`, не подробности UI) |
| WEB MVP — замыслы, отложенное, отклонённое (будущая архитектура web UI) | [web-design.md](web-design.md) | web-current.md (там — только реализованное) |
| Справочник HTTP API (эндпоинты/параметры/лимиты/коды/curl для `--serve`) | [api.md](api.md) | server-mode.md (там — дизайн будущего сетевого API, не справочник по текущим эндпоинтам) |
| Конфигурация (`[tool.stepik-grader]`), форматы тест-кейсов, ограничения и безопасность | [configuration.md](configuration.md) | README, CONTRIBUTING, grader-workflow.md |
| Формат JSON локального глоссария (карточки/очередь) и API `stepik_grader.glossary` | [glossary.md](glossary.md) | web-current.md (там — продуктовый контекст, не формат хранения) |
| Архитектура: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) | README, CLAUDE.md (там — инварианты, не дублирующее описание) |
| Контракт результата проверки (поля case/solution/run, вердикты, стабильность) | [result-contract.md](result-contract.md) | web-current.md (там — ViewModel-надстройки), configuration.md (там — таблица вердиктов) |
| Дизайн server mode (Runner/SandboxRunner, API удалённого исполнения, sandbox-требования) | [server-mode.md](server-mode.md) | SECURITY.md (там — короткая политика), ADR-0001 (там — решение, не спецификация), api.md (там — текущие эндпоинты, не дизайн) |
| Диагностический режим, лог-файл, редакция секретов | [logging.md](logging.md) | SECURITY.md, configuration.md |
| Архитектурные решения (контекст/решение/альтернативы/последствия) | [adr/README.md](adr/README.md) | docs/* (дизайн-доки описывают «как», ADR — «почему») |
| Дерево файлов проекта | [project-structure.md](project-structure.md) | README |
| Сравнение версий, отличия от оригинала | [versions.md](versions.md) | README |
| История релизов (детальный changelog) | [../CHANGELOG.md](../CHANGELOG.md) | versions.md (там — только качественные скачки) |
| Архив CHANGELOG: ротированные релизы (1.1.0–1.5.0, issue #373) + до-тегового периода (до #162/#183) | [changelog-archive.md](changelog-archive.md) | ../CHANGELOG.md (там — только живая часть: актуальный Unreleased + три последних MINOR) |
| Политика версионирования (схема тег=MINOR+1, release vs dev) | [../CONTRIBUTING.md](../CONTRIBUTING.md) § Версионирование | README, CLAUDE.md, versions.md, history.md |
| Инварианты ядра, правила для агентов | [../CLAUDE.md](../CLAUDE.md) | docs/* |
| История спринтов/roadmap, подробные примечания к issue (архив) | [history.md](history.md) | CLAUDE.md (там — только действующие инварианты) |
| Архив постановок для Claude (scope/non-goals; все закрыты) | [claude-handoff.md](claude-handoff.md) | CLAUDE.md (там — короткие указатели); канон продукта — web-current.md/web-design.md |

> **Версия проекта — без ручного source of truth в доках.** Актуальный номер
> берётся из git-тега / `importlib.metadata` (бейдж релиза в README тянет
> `github/v/release`), а схема нумерации канонически описана в
> [CONTRIBUTING.md](../CONTRIBUTING.md). Не вписывай `version-X.Y.Z` вручную в
> README как единственный источник истины.
