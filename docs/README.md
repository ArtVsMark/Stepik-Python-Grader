# База знаний Stepik Python Grader

> Карта документации проекта (issue #172, #178 / эпик #102, PR-13). Обзор и
> быстрый старт — в корневом [README](../README.md).

## Куда идти

| Хочу… | Документ |
|---|---|
| Установить (pipx / из исходников), настроить OAuth, диагностика | [installation.md](installation.md) |
| Запустить грейдер, режимы 1–4, CLI-флаги, web/IDE, скачать задачу | [grader-workflow.md](grader-workflow.md) |
| Начать работать в вебе: `stepik-grader --serve` или GUI-лаунчер `stepik-grader-gui` (issue #661), адрес `http://127.0.0.1:8000`, разделы «Проверка решений» / «Песочница» / «Глоссарий» | [grader-workflow.md § Веб-интерфейс](grader-workflow.md#веб-интерфейс---serve) |
| WEB MVP: три блока (Проверка решений, Downloader, Глоссарий-модуль), микро-бенчмарк, error/action cards — что реализовано | [web-current.md](web-current.md) |
| WEB MVP: замыслы, отложенное, отклонённое | [web-design.md](web-design.md) |
| Справочник HTTP API `--serve`: эндпоинты, лимиты, коды ответов, curl-примеры | [api.md](api.md) |
| Справочник: конфигурация (`[tool.stepik-grader]`), форматы тест-кейсов, ограничения и безопасность | [configuration.md](configuration.md) |
| Цепочка поставок: инвентарь runtime-зависимостей и вендоренных веб-ассетов, аудит `pip-audit`, лицензии | [supply-chain.md](supply-chain.md) |
| Локальный глоссарий: формат JSON карточек/очереди, Python-API (`stepik_grader.glossary`) | [glossary.md](glossary.md) |
| Понять архитектуру: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) |
| Контракт результата проверки (поля, вердикты) для CLI/Web/API | [result-contract.md](result-contract.md) |
| Формат JSON-трейса пошагового исполнения (песочница, `core/tracer.py`) | [trace-format.md](trace-format.md) |
| Дизайн server mode: Runner-слой, API удалённого исполнения, sandbox | [server-mode.md](server-mode.md) |
| Дизайн server-mode sandbox-backend (контейнеры, cgroups v2, netns) | [server-sandbox-design.md](server-sandbox-design.md) |
| Дизайн server-mode данных (PostgreSQL поверх SQLite, accounts/workspaces, RBAC) | [server-data-model.md](server-data-model.md) |
| Диагностический режим и лог-файл (редакция секретов, opt-in) | [logging.md](logging.md) |
| Архитектурные решения (ADR) | [adr/README.md](adr/README.md) |
| Посмотреть дерево файлов проекта | [project-structure.md](project-structure.md) |
| Сравнить версии и отличия от оригинала | [versions.md](versions.md) |
| Полный список изменений | [../CHANGELOG.md](../CHANGELOG.md) |
| Внести вклад: код-стайл, форматы тестов, версионирование | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Инварианты ядра и правила для агентов | [../CLAUDE.md](../CLAUDE.md) |
| Режим ответов Claude: полный шаблон 13 ролей + матрица подключения | [roles.md](roles.md) |
| Очередь работ для Claude Code после крупного аудита (сейчас пусто) | [claude-handoff.md](claude-handoff.md) |
| Политика безопасности, ответственное раскрытие уязвимостей | [../SECURITY.md](../SECURITY.md) |
| Правила PEP 8 и учебные инсайты: разделы «Правила»/«Подучить», формат `RuleCard`, `core/lint.py` (эпик #342) | [rules-insights.md](rules-insights.md) |
| **Всё историческое — в одном месте:** история спринтов и релизов, архив CHANGELOG, разовые аудиты, завершённые планы, прошлые постановки для Claude | [archive/README.md](archive/README.md) |

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
| Цепочка поставок: runtime-зависимости, вендоренные веб-ассеты, `pip-audit`, лицензии | [supply-chain.md](supply-chain.md) | SECURITY.md (там — короткий указатель), vendor/VERSIONS.md (там — рецепт сборки CodeMirror, не инвентарь) |
| Формат JSON локального глоссария (карточки/очередь) и API `stepik_grader.glossary` | [glossary.md](glossary.md) | web-current.md (там — продуктовый контекст, не формат хранения) |
| Архитектура: модули, слои, граф зависимостей, «что умеет» | [architecture.md](architecture.md) | README, CLAUDE.md (там — инварианты, не дублирующее описание) |
| Контракт результата проверки (поля case/solution/run, вердикты, стабильность) | [result-contract.md](result-contract.md) | web-current.md (там — ViewModel-надстройки), configuration.md (там — таблица вердиктов) |
| Дизайн server mode (Runner/SandboxRunner, API удалённого исполнения, sandbox-требования) | [server-mode.md](server-mode.md) | SECURITY.md (там — короткая политика), ADR-0001 (там — решение, не спецификация), api.md (там — текущие эндпоинты, не дизайн) |
| Дизайн server-mode sandbox-backend: контейнеры, cgroups v2/netns/seccomp, отображение требований #157 на примитивы (issue #153) | [server-sandbox-design.md](server-sandbox-design.md) | server-mode.md (там — требования #157, не «как»), ADR-0008 (там — решение о классе backend, не спецификация) |
| Дизайн server-mode данных: доменная модель, PostgreSQL поверх SQLite-истории, accounts/workspaces/courses, RBAC (issue #154/#155) | [server-data-model.md](server-data-model.md) | history.py (там — локальная SQLite-схема), ADR-0009 (там — решение, не спецификация), server-mode.md (там — фазовая карта) |
| Диагностический режим, лог-файл, редакция секретов | [logging.md](logging.md) | SECURITY.md, configuration.md |
| Архитектурные решения (контекст/решение/альтернативы/последствия) | [adr/README.md](adr/README.md) | docs/* (дизайн-доки описывают «как», ADR — «почему») |
| Дерево файлов проекта | [project-structure.md](project-structure.md) | README |
| Сравнение версий, отличия от оригинала | [versions.md](versions.md) | README |
| История релизов (детальный changelog) | [../CHANGELOG.md](../CHANGELOG.md) | versions.md (там — только качественные скачки) |
| Политика версионирования (схема тег=MINOR+1, release vs dev) | [../CONTRIBUTING.md](../CONTRIBUTING.md) § Версионирование | README, CLAUDE.md, versions.md |
| Инварианты ядра, правила для агентов | [../CLAUDE.md](../CLAUDE.md) | docs/* |
| Полный шаблон 13 ролей для ответов Claude (профили, правила, матрица) | [roles.md](roles.md) | CLAUDE.md (там — только компактный триггер-блок § Режим ответов) |
| Очередь работ после аудита (порядок, блокеры); пустая, когда пласт разобран | [claude-handoff.md](claude-handoff.md) | ../CHANGELOG.md — там «что сделано»; GitHub Issues — там открытые задачи |
| Всё историческое: история спринтов/релизов, архив CHANGELOG, разовые аудиты, завершённые планы | [archive/README.md](archive/README.md) | активные `docs/*` — в них не остаётся ни истории, ни статусов закрытых issue |

> **Версия проекта — без ручного source of truth в доках.** Актуальный номер
> берётся из git-тега / `importlib.metadata` (бейдж релиза в README тянет
> `github/v/release`), а схема нумерации канонически описана в
> [CONTRIBUTING.md](../CONTRIBUTING.md). Не вписывай `version-X.Y.Z` вручную в
> README как единственный источник истины.
