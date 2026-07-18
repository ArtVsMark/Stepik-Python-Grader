# CHECKPOINT — Stepik-Python-Grader

> **Роль файла: исторический snapshot, НЕ канонический источник.**
> Краткая «фотография» состояния проекта на момент правки. Не дублирует
> архитектуру, режимы, форматы тестов, конфиг и зависимости — они канонически
> живут в `docs/*` (см. ссылки ниже). За актуальным состоянием обращайся к
> живым источникам, а не к этому файлу:
>
> - Полный список изменений — [`CHANGELOG.md`](CHANGELOG.md).
> - Открытые задачи и статусы — GitHub Issues (`gh issue list`).
> - Карта документации и каноны — [`docs/README.md`](docs/README.md).
> - Эволюция метрик по релизам — [`docs/versions.md`](docs/versions.md).

---

## Snapshot: v1.8.0 (stable)

**Текущая версия: 1.8.0**

> Строка-маркер выше существует только для CI-проверки дрейфа
> (`scripts/check_version_consistency.py`, issue #165) — она сверяется с
> последним git-тегом. Каноническая история релизов — в [`CHANGELOG.md`](CHANGELOG.md)
> и [`docs/versions.md`](docs/versions.md); этот файл остаётся историческим
> snapshot, а не источником истины по версиям.

- Тестов: 1317 · Покрытие: 93% cross-OS combined — **снапшот на момент релиза
  v1.8.0; актуальные числа в живых бейджах README** (single-OS ubuntu ~87.5%,
  структурно ограничено `core/sandbox/`'s тремя ОС-специфичными backend'ами,
  см. [`docs/architecture.md`](docs/architecture.md)) · Python: 3.12 / 3.13 /
  3.14 (экспериментальная, только ubuntu в CI)
- CI: GitHub Actions (ruff + mypy + pytest), матрица ubuntu/windows/macos
  × 3.12/3.13 + ubuntu 3.14-experimental + отдельный `coverage-combine` job —
  зелёный
- Пакет — `src/stepik_grader/` (src-layout, issue #35). Запуск только через
  `python -m stepik_grader.X` или `stepik-grader` после `pip install -e .`
- Опубликован на PyPI: `pipx install stepik-python-grader`
- Точные метрики и их эволюция — [`docs/versions.md`](docs/versions.md);
  архитектура и модули — [`docs/architecture.md`](docs/architecture.md);
  дерево файлов — [`docs/project-structure.md`](docs/project-structure.md).

### Реализовано (см. каноны, здесь без дублей)

- WEB workspace (эпик **#123**, закрыт): split-pane UI, command palette,
  раздел «Глоссарий», Downloader-блок (**#186**), микро-бенчмарк в вебе
  (**#187**), сквозные user-journey тесты (**#129**) — что реализовано:
  [`docs/web-current.md`](docs/web-current.md); замыслы/отложенное:
  [`docs/web-design.md`](docs/web-design.md).
- `--sandbox` — опциональная ОС-уровневая изоляция исполнения (issue
  **#266**): bubblewrap/`sandbox-exec`/Job Objects. Гарантии по ОС —
  [`SECURITY.md`](SECURITY.md).
- Async job-модель для веб-бенчмарка (issue **#262**): `POST /api/v1/runs` +
  прогресс/отмена. Полный справочник HTTP API — [`docs/api.md`](docs/api.md).
- Диагностическое логирование сети/OAuth (эпик **#146**, #341): opt-in
  `core/diag_log.py` с редакцией секретов, подключён в
  `stepik_client`/`oauth_flow`/`downloader` — [`docs/logging.md`](docs/logging.md).
- Гигиена по аудиту 2026-07 (эпик **#343**, v1.8.0): багфиксы (#350–#352),
  дрейф доков (#353), консолидация «двойников» — единый i18n-каталог (#355) и
  единый RE-резолвер глоссария `core/error_glossary.py` (#356), мелкая гигиена
  код-стайла (#354), детерминированные web-тесты (#357).
- Security-аудит (эпики #146/#151/#97): закрыты утечка OAuth-токена,
  Login-CSRF, отсутствие лимитов на тело запроса, права `secrets.json`,
  path-confinement и Host/Origin guard в `--serve`.
- Path вместо `str` в публичных контрактах `core/`/`cli/`/`web/` (issue
  **#73**, breaking).
- Локальная статистика запусков `--stats`/`--stats-summary` (issue **#268**).
- Режимы 1–4, non-interactive CLI, `--output json/csv/markdown`, `--watch`,
  i18n (ru/en) — [`docs/grader-workflow.md`](docs/grader-workflow.md).
- Три формата тест-кейсов и конфигурация `[tool.stepik-grader]` —
  [`docs/configuration.md`](docs/configuration.md).
- Кэш результатов `.grader_cache/` (`core/cache.py`, issue #56) и
  pytest-плагин (`pytest_plugin.py`, issue #57).
- Runtime-зависимости: 3 (requests, psutil, rich) —
  [`docs/installation.md`](docs/installation.md).
- Локальный глоссарий против stdlib (issue #126, эпик #123) —
  [`docs/glossary.md`](docs/glossary.md).
- Живые README-бейджи `Coverage (ubuntu)`/`Coverage (all OS)`/`Version`
  (`scripts/generate_*_badge.py`, CI коммитит `.github/badges/*.json` после
  каждого прогона на push в main).
- Packaging hygiene: MIT `LICENSE` + `py.typed`.
- Полный diff — [`CHANGELOG.md § [1.8.0]`](CHANGELOG.md).

---

## Открытые фронты (указатели)

Актуальные статусы — только в GitHub Issues (`gh issue list`); ниже —
навигация по крупным веткам:

- **#151** (backlog) — серверный трек (SandboxRunner+контейнеры/PostgreSQL/
  accounts) — отложен до реального спроса; дизайн-задачи #153–#155 закрыты
  (ADR-0008/0009 + `server-*-design.md`, PR #484).
- **#97** — эпик анализа/развития: локальный CLI → WEB/Server IDE (родитель
  #151; #130/#146 — закрыты).
- **#59** (backlog) — долгосрочные идеи (Docker-sandbox, другие платформы,
  AI-подсказки, дашборд прогресса).

Постановки задач для будущих сессий Claude — [`docs/claude-handoff.md`](docs/claude-handoff.md).
