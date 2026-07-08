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

## Snapshot: v1.6.0 (stable)

**Текущая версия: 1.6.0**

> Строка-маркер выше существует только для CI-проверки дрейфа
> (`scripts/check_version_consistency.py`, issue #165) — она сверяется с
> последним git-тегом. Каноническая история релизов — в [`CHANGELOG.md`](CHANGELOG.md)
> и [`docs/versions.md`](docs/versions.md); этот файл остаётся историческим
> snapshot, а не источником истины по версиям.

- Тестов: 784 · Покрытие: 95% · Python: 3.12 / 3.13 / 3.14
- CI: GitHub Actions (ruff + mypy + pytest), матрица ubuntu/windows/macos
  × 3.12/3.13 + ubuntu 3.14-experimental — зелёный
- Пакет — `src/stepik_grader/` (src-layout, issue #35). Запуск только через
  `python -m stepik_grader.X` или `stepik-grader` после `pip install -e .`
- Опубликован на PyPI: `pipx install stepik-python-grader`
- Точные метрики и их эволюция — [`docs/versions.md`](docs/versions.md);
  архитектура и модули — [`docs/architecture.md`](docs/architecture.md);
  дерево файлов — [`docs/project-structure.md`](docs/project-structure.md).

### Реализовано (см. каноны, здесь без дублей)

- Режимы 1–4 (проверка / сравнение / subprocess-бенчмарк / timeit-микробенч),
  non-interactive CLI, `--output json/csv/markdown`, `--watch`, i18n (ru/en) —
  [`docs/grader-workflow.md`](docs/grader-workflow.md).
- Три формата тест-кейсов и конфигурация `[tool.stepik-grader]` —
  [`docs/configuration.md`](docs/configuration.md).
- Web UI `--serve` (stdlib `http.server`, `web.py`) и IDE-интеграция
  `--init-vscode` (`ide.py`) — [`docs/grader-workflow.md`](docs/grader-workflow.md).
- Кэш результатов `.grader_cache/` (`core/cache.py`, issue #56) и
  pytest-плагин (`pytest_plugin.py`, issue #57) — вошли в v1.5.0.
- Runtime-зависимости: 3 (requests, psutil, rich) —
  [`docs/installation.md`](docs/installation.md).
- Foundation локального глоссария (issue **#126**, эпик #123): пакет
  `src/stepik_grader/glossary/` — `GlossaryCard`/`GlossaryMissingEntry`,
  `JsonGlossaryProvider`, `MissingConceptDetector`, очередь пополнения.
  Формат и API — [`docs/glossary.md`](docs/glossary.md).
- Glossary coverage относительно официального Python/stdlib (issues
  **#195–#198**, эпик #123): `origin`/`module`/`qualname` у
  `GlossaryMissingEntry` + валидация при загрузке; офлайн-инвентаризатор
  stdlib (`stdlib_inventory.py`); coverage-отчёт + missing JSON
  (`coverage.py`); CLI `python -m stepik_grader.glossary.coverage`.
- `--version` различает dev-сборку и релиз (issue **#163**, эпик #161 закрыт).
- Живые README-бейджи `Coverage`/`Version` (`scripts/generate_*_badge.py`,
  CI сам коммитит `.github/badges/*.json` после каждого прогона на push в main).
- Packaging hygiene: MIT `LICENSE` + `py.typed`.
- Полный diff — [`CHANGELOG.md § [1.6.0]`](CHANGELOG.md).

---

## Открытые фронты (указатели)

Актуальные статусы — только в GitHub Issues; ниже — навигация:

- **#125** — WEB workspace проверки решений (дизайн — [`docs/web-mvp.md`](docs/web-mvp.md)).
- **#186** — Downloader-блок в web · **#187** — микро-бенчмарк в web.
- **#129** — тесты web MVP (user journeys).
- **#191** — follow-up доводка глоссария (снижение false-positive детектора).
- **#199** — регистрация модулей glossary coverage в DAG/архитектуре.

Постановки задач для будущих сессий Claude — [`docs/claude-handoff.md`](docs/claude-handoff.md).
