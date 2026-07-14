# CLAUDE.md — Stepik-Python-Grader

> Агентский контракт: то, что Claude Code должен знать перед КАЖДЫМ действием.
> Только действующие инварианты, стиль и команды. История спринтов, roadmap и
> подробные примечания к issue вынесены в [`docs/history.md`](docs/history.md)
> (архив, issue #176). Не раздувать этот файл заново — большие технические
> разделы канонически живут в `docs/` (см. § Источники истины).

---

## 🚦 Критические запреты (читать первым)

```
❌ НЕ вносить изменения в ветку main напрямую — только через PR
❌ НЕ выполнять деструктивные git-операции (push --force, reset --hard,
   удаление веток) без явного запроса пользователя
❌ НЕ удалять/переименовывать публичные функции без PR
❌ НЕ ломать обратную совместимость __all__ в grader.py (тонкий фасад)
❌ НЕ использовать Optional[X]/List[X]/Dict[X,Y] — проект на Python 3.12+
❌ НЕ добавлять зависимости в pyproject.toml без явного указания
   (requirements.txt удалён — pyproject.toml единственный источник; не воссоздавать)
❌ НЕ коммитить secrets.json, stepik_config.json, StepikTasks/, .grader_cache/,
   .grader_stats.jsonl
❌ НЕ запускать executor.py с untrusted-кодом — нет OS-sandbox
❌ НЕ трогать .github/workflows/ без явной задачи
❌ НЕ править version в pyproject.toml вручную — версия динамическая, из git-тегов
   (setuptools-scm, issue #162). См. § Версионирование.
```

---

## 📍 Рабочая ветка

```bash
# Ветвиться от свежего main, PR — обратно в main:
git checkout main && git pull
git checkout -b <type>/<short-slug>     # напр. docs/versioning, fix/executor-timeout
```

Постоянной «рабочей ветки» нет: каждая задача — своя ветка от `main`, затем
PR. Тип ветки/коммита — по Conventional Commits (см. § Формат коммитов).

---

## 🗂️ Структура и архитектура

Канонические источники (здесь **не дублируются**, чтобы не расходиться):

- Дерево файлов — [`docs/project-structure.md`](docs/project-structure.md)
- Модули, слои, граф зависимостей (DAG), «что умеет каждый модуль» —
  [`docs/architecture.md`](docs/architecture.md)

Пакет живёт в `src/stepik_grader/` (src-layout). Точки входа —
`grader.py`/`cli.py`/`downloader.py`/`diagnostic_stepik.py` + `config.py`;
всё остальное внутреннее — в `src/stepik_grader/core/`. Запуск — только
`python -m stepik_grader.X` или `stepik-grader` после `pip install -e .`
(прямого `python grader.py` из корня нет).

---

## ⚙️ Команды

### Установка

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # runtime (requests/psutil/rich) + pytest/ruff/mypy
```

### Перед коммитом (зеркалит CI)

```bash
pytest tests/ -x -q --tb=short                          # тесты
ruff check .                                             # линтер
ruff format --check .                                    # форматтер (проверка)
mypy src/stepik_grader --ignore-missing-imports         # типы (issue #49 C-02)
pytest tests/ --cov=. --cov-report=term-missing -q      # покрытие (информационно)
```

### Запуск

```bash
python -m stepik_grader.grader              # интерактивное меню (режимы 0-4)
python -m stepik_grader.downloader          # скачать задачу по URL Stepik
python -m stepik_grader.diagnostic_stepik   # диагностика API и токена
stepik-grader                               # то же, если пакет установлен
```

---

## 🐍 Стиль кода (Python 3.12+)

```python
from __future__ import annotations   # ОБЯЗАТЕЛЬНО в начале каждого нового файла
```

- **Типизация — union-синтаксис:** `def f(x: int | None = None) -> list[str]`.
  Никаких `Optional`/`List`/`Dict`/`Union` из `typing`.
- **Dataclasses:** изменяемые defaults только через `field(default_factory=...)`.
- **Пути — только `pathlib`**, не `os.path`.
- **Путь-параметры/возвраты в публичных сигнатурах — `Path`, не `str`**
  (issue #73): функция/метод, принимающий или возвращающий путь к файлу или
  директории, типизируется `pathlib.Path`/`Path | None` — без обёртки в
  `str(...)` на входе/выходе и без защитного `pathlib.Path(...)` внутри тела
  (вызывающая сторона обязана передавать реальный `Path`). Не путь по смыслу
  (URL, идентификатор, хеш, голое имя файла без директории вроде
  `is_solution_file(file_name: str)`, содержимое кода/текста) — остаётся
  `str`. Правило действует на весь пакет, включая `web/`-слой; исключение —
  сетевая граница (HTTP query/JSON тела, `web/server.py`), где путь неизбежно
  приходит как `str` и конвертируется в `Path` один раз в точке входа
  (`argparse`, `_confined_path()`).
- **Subprocess-интерпретатор — `sys.executable`**, не `"python3"`/`"python"`.
- **Docstrings** для всех публичных функций (краткий формат).
- **`__all__`** — в каждом новом модуле.
- **Вывод — через `_console`** (rich) с graceful fallback на `print()`, не
  голый `print()` в логике модулей.
- **Никаких голых `except:`** — ловить `Exception as e` и логировать/re-raise.

Подробные примеры код-стайла и антипаттерны — в
[`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## 🏗️ Архитектурные инварианты

1. **DAG без циклов** — новые импорты не создают циклических зависимостей.
2. **Leaf-модули** — `storage.py`, `normalizers.py`, `glossary.py` не
   импортируют ничего из проекта. Не добавлять в них project-импорты.
3. **Graceful fallback** — `rich` опционален; весь вывод через `_console`.
4. **Sandbox — только opt-in** — по умолчанию `executor.py`/`LocalRunner`
   запускают код в subprocess **без** изоляции ФС/сети; OS-изоляция включается
   явным `--sandbox` (`core/sandbox/`, три backend'а, issue #266) и в web-слой
   пока не проброшена (issue #351). Дефолт «нет изоляции» документировать
   везде, где релевантно.
5. **Обратная совместимость** — все имена из `__all__` остаются доступными
   через `from stepik_grader.grader import X`.
6. **Истина глоссария** — полнота глоссария меряется относительно
   **официального Python/stdlib**, а не стороннего справочника. Внутренняя база
   Stepik-Python-Grader — источник истины контента; внешний
   [Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) — только цель
   экспорта/витрина, **никогда** не эталон полноты. Канон —
   [docs/glossary.md § Источники истины](docs/glossary.md#источники-истины-роли).

---

## 📐 Форматы тест-кейсов (кратко)

Три автодетектируемых формата: `1`—Legacy (`N`, `N.clue`), `2`—Named
(`input_N.txt`, `expected_N.txt`), `3`—python-generation (`input.txt` +
`output.txt` с `# TEST_N:`). Канонический справочник —
[`docs/configuration.md`](docs/configuration.md#формат-тест-кейсов).

---

## 🔑 Формат коммитов

Conventional Commits — обязательно:

```
fix(executor): use sys.executable instead of platform string
feat(config): add GraderConfig dataclass with pyproject.toml support
refactor(grader): extract reporter.py with rich output logic
test(config): add tests for GraderConfig defaults
docs(claude): trim CLAUDE.md to agent contract
chore(deps): bump psutil upper bound
```

---

## 🔢 Версионирование (кратко)

**Не SemVer.** Собственная схема (тег = MINOR+1, PATCH = число коммитов после
тега, все теги = `vX.Y.0`). После PR #183 версия **динамическая, из git-тегов**
(`setuptools-scm`): на теге → `X.Y.0`, вне тега → `X.Y.0.postN+g<hash>`.
Статической `version` в `pyproject.toml` нет — вручную не править.

Ручную сверку версий делать не нужно: за дрейф отвечает CI
(`scripts/check_version_consistency.py`, issue #165). Полная политика — в
[`CONTRIBUTING.md § Версионирование`](CONTRIBUTING.md#версионирование-issue-68).
UX-полировка вывода `--version` (dev vs release маркер) — задача #163, см.
[`docs/claude-handoff.md`](docs/claude-handoff.md).

`scripts/version.py`'s "логическая" `X.Y.Z` (README `Version`-бейдж) считает
PATCH через `git rev-list --invert-grep`, исключая автокоммиты CI
`chore(ci): update badges [skip ci]` — иначе счётчик рос бы вдвое быстрее
реальных изменений (issue #231). `setuptools-scm`-версия пакета (`X.Y.0.postN`)
это не затрагивает — у неё независимая логика без фильтрации по commit message.

---

## 📝 Обновление CHANGELOG.md / docs/history.md — когда

- **`CHANGELOG.md`** (английский) — запись под `## [Unreleased]` в **каждом**
  смерженном PR, без исключений для "внутренних"/рефакторинговых PR
  (используйте `### Refactored`/`### Changed`/`### Internal` — прецеденты уже
  есть в файле). При релизе `[Unreleased]` переименовывается в
  `[X.Y.0] - ДАТА`, наверх добавляется новый пустой `[Unreleased]`.
- **`docs/history.md`** (русский) — архивная запись на **каждый релиз**
  (новый git-тег `vX.Y.0`), не на каждый PR: сводка вошедшего в релиз, в
  стиле уже существующих записей (`**#NNN (дата):** ...`).
- **`CHECKPOINT.md`** — обновляется вместе с `docs/history.md`, на каждый
  релиз (это исторический snapshot, не отслеживает промежуточные PR).

Не откладывать `CHANGELOG.md` "до конца фичи/спринта" — если PR смержен,
запись нужна сразу этим же PR, а не пост-фактум пачкой.

---

## 📚 Источники истины (не дублировать)

| Тема | Канонический документ |
|---|---|
| Установка, OAuth, диагностика | [docs/installation.md](docs/installation.md) |
| Режимы, CLI-флаги, web/IDE, скачивание | [docs/grader-workflow.md](docs/grader-workflow.md) |
| Конфигурация, форматы тестов, безопасность | [docs/configuration.md](docs/configuration.md) |
| Архитектура (DAG, слои) | [docs/architecture.md](docs/architecture.md) |
| Контракт результата проверки (CLI/Web/API) | [docs/result-contract.md](docs/result-contract.md) |
| Дизайн server mode (Runner, API, sandbox) | [docs/server-mode.md](docs/server-mode.md) |
| Диагностика/логирование, редакция секретов | [docs/logging.md](docs/logging.md) |
| Архитектурные решения (ADR) | [docs/adr/README.md](docs/adr/README.md) |
| Дерево файлов | [docs/project-structure.md](docs/project-structure.md) |
| Версии, отличия от оригинала | [docs/versions.md](docs/versions.md) |
| Политика версионирования, код-стайл, workflow | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Кодекс поведения | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| WEB MVP: реализовано / замыслы / HTTP API | [docs/web-current.md](docs/web-current.md), [docs/web-design.md](docs/web-design.md), [docs/api.md](docs/api.md) |
| История спринтов/roadmap (архив) | [docs/history.md](docs/history.md) |
| Handoff для будущих реализаций Claude | [docs/claude-handoff.md](docs/claude-handoff.md) |
| Полный changelog (живой источник) | [CHANGELOG.md](CHANGELOG.md) |
| Состояние проекта (исторический snapshot) | [CHECKPOINT.md](CHECKPOINT.md) |

---

## 🎯 Открытая работа (указатели)

Реализационные задачи, готовые для будущего Claude — в
[`docs/claude-handoff.md`](docs/claude-handoff.md). Открытых пунктов этого
списка сейчас нет — актуальный список открытых issue см. `gh issue list`.

> **#199 (регистрация модулей glossary coverage в DAG/архитектуре) — закрыт.**
> `stdlib_inventory.py`/`coverage.py` описаны в
> [docs/architecture.md](docs/architecture.md) и
> [docs/project-structure.md](docs/project-structure.md).

> **#125/#186/#187/#129 (WEB workspace, Downloader-блок, микро-бенчмарк,
> тесты user journeys в web) — закрыты. Эпик #123 закрыт.** См.
> [docs/web-current.md](docs/web-current.md) и
> [docs/claude-handoff.md](docs/claude-handoff.md).

**Дизайн-указатели** (server mode, [docs/server-mode.md](docs/server-mode.md) +
ADR-0001): Runner-слой **#140** и контракт результата **#116**
([docs/result-contract.md](docs/result-contract.md)) — оба закрыты и уже
реализованы (`core/runner.py`, `core/result.py`), не переизобретать. API
удалённого исполнения **#156** и sandbox-требования **#157** закрыты как
дизайн — сам сервер/sandbox не реализованы, отдельных implementation-issue
пока нет. Диагностическое логирование — эпик **#146** реализован (#341):
opt-in `core/diag_log.py` с редакцией секретов, подключён в
`stepik_client`/`oauth_flow`/`downloader`; докс-часть — **#150**
([docs/logging.md](docs/logging.md)). Дочерние **#147**/**#148**/**#149**
закрыты.

> **#126 (`JsonGlossaryProvider`) и эпик #161/#163 (`--version` dev vs release) —
> закрыты.** Foundation локального глоссария и вся цепочка source-driven
> coverage (**#195–#198**: `origin`-поля, `stdlib_inventory.py`, `coverage.py`,
> CLI-точка входа) — в `src/stepik_grader/glossary/`, документация —
> [docs/glossary.md](docs/glossary.md). Не реализовывать заново.

Актуальные статусы — в GitHub Issues (`gh issue list`) и
[CHANGELOG.md](CHANGELOG.md); [CHECKPOINT.md](CHECKPOINT.md) — исторический snapshot.

---

## ✅ Чеклист перед PR

```
[ ] Ветка создана от свежего main (не коммитить в main напрямую)
[ ] pytest tests/ -x -q --tb=short   → все зелёные
[ ] ruff check .                      → 0 ошибок
[ ] ruff format --check .             → 0 ошибок
[ ] mypy src/stepik_grader --ignore-missing-imports  → 0 ошибок
[ ] Новые функции: type hints + docstring; новые модули: __all__
[ ] from __future__ import annotations в начале нового файла
[ ] Коммит в формате Conventional Commits
[ ] CHANGELOG.md: добавлена запись под ## [Unreleased] — в КАЖДОМ PR, без
    исключений для рефакторингов (см. § Обновление CHANGELOG.md/docs/history.md)
[ ] docs/history.md/CHECKPOINT.md — НЕ на каждый PR, обновляются вместе на
    релиз (см. ту же секцию)
[ ] Версия не правится вручную — CI (check_version_consistency.py) сам следит
    за дрейфом (issue #165); достаточно, чтобы CHECKPOINT/CHANGELOG совпадали
    с последним git-тегом
```

---

## 📎 Связанный проект

**Glossary-Python** (`https://github.com/ArtVsMark/Glossary-Python`) —
статический HTML-глоссарий Python-терминов. Грейдер ссылается на него при RE
через `core/glossary.py` (issue #72). НЕ трогать этот проект отсюда —
изменения только через отдельную задачу в самом Glossary-Python.

---

## 📊 Метрики (на момент v1.8.0)

| Метрика | Значение |
|---|---|
| Версия | 1.8.0 (stable) |
| Python | 3.12 / 3.13 (3.14 — экспериментальная, только ubuntu в CI) |
| Тестов | 1317 |
| Покрытие | 93% (cross-OS combined — см. заметку ниже) |
| Зависимостей runtime | 3 (requests, psutil, rich) |

> Строку `| Версия | X.Y.Z |` проверяет `scripts/check_version_consistency.py`
> (мягкое предупреждение при расхождении с последним git-тегом). Обновлять при
> релизе MINOR. Эволюция метрик по релизам — в [docs/history.md](docs/history.md).

> **Два числа покрытия (issue #283).** С `--sandbox` (issue #266) `core/sandbox/`
> содержит три ОС-специфичных backend'а — на любой одной машине/CI-job'е два из
> трёх всегда 0%. Поэтому `pytest`/локальный чек-лист и один job CI-матрицы
> видят только per-OS цифру (~86–90%, порог `fail_under = 85` в
> `pyproject.toml` — НЕ поднимать глобально, иначе любой контрибьютор на одной
> ОС будет ложно падать). README держит **два** бейджа: single-OS
> (`.github/badges/coverage.json`, как раньше) и cross-OS combined
> (`coverage-combined.json`, `coverage combine` по трём job'ам матрицы,
> отдельный job `coverage-combine` в `ci.yml`, порог 90). Таблица выше — это
> combined-цифра.
