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
❌ НЕ коммитить secrets.json, stepik_config.json, StepikTasks/, .grader_cache/
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
4. **Нет sandbox** — `executor.py` запускает код в subprocess без изоляции
   ФС/сети. Документировать везде, где релевантно.
5. **Обратная совместимость** — все имена из `__all__` остаются доступными
   через `from stepik_grader.grader import X`.

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

---

## 📚 Источники истины (не дублировать)

| Тема | Канонический документ |
|---|---|
| Установка, OAuth, диагностика | [docs/installation.md](docs/installation.md) |
| Режимы, CLI-флаги, web/IDE, скачивание | [docs/grader-workflow.md](docs/grader-workflow.md) |
| Конфигурация, форматы тестов, безопасность | [docs/configuration.md](docs/configuration.md) |
| Архитектура (DAG, слои) | [docs/architecture.md](docs/architecture.md) |
| Дерево файлов | [docs/project-structure.md](docs/project-structure.md) |
| Версии, отличия от оригинала | [docs/versions.md](docs/versions.md) |
| Политика версионирования, код-стайл, workflow | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Дизайн WEB MVP | [docs/web-mvp.md](docs/web-mvp.md) |
| История спринтов/roadmap (архив) | [docs/history.md](docs/history.md) |
| Handoff для будущих реализаций Claude | [docs/claude-handoff.md](docs/claude-handoff.md) |
| Полный changelog (живой источник) | [CHANGELOG.md](CHANGELOG.md) |
| Состояние проекта (исторический snapshot) | [CHECKPOINT.md](CHECKPOINT.md) |

---

## 🎯 Открытая работа (указатели)

Реализационные задачи, готовые для будущего Claude — в
[`docs/claude-handoff.md`](docs/claude-handoff.md):

- **#125** — WEB workspace проверки решений (по [docs/web-mvp.md](docs/web-mvp.md)).
- **#186** — Downloader-блок в web · **#187** — микро-бенчмарк в web.
- **#129** — тесты web MVP (user journeys).
- **#161** (эпик) ⊃ **#163** — `--version` UX: различать dev и release.

> **#126 (`JsonGlossaryProvider`) — закрыт.** Foundation локального глоссария
> уже в репозитории (`src/stepik_grader/glossary/`, документация —
> [docs/glossary.md](docs/glossary.md)). Не реализовывать заново. Открытая
> доводка модуля — follow-up **#190/#191** (валидация `kind/status`, снижение
> false-positive детектора).

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
[ ] CHECKPOINT.md/CHANGELOG.md обновлены (если завершена фича/спринт)
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

## 📊 Метрики (на момент v1.5.0)

| Метрика | Значение |
|---|---|
| Версия | 1.5.0 (stable) |
| Python | 3.12 / 3.13 / 3.14 |
| Тестов | 660 |
| Покрытие | 95% |
| Зависимостей runtime | 3 (requests, psutil, rich) |

> Строку `| Версия | X.Y.Z |` проверяет `scripts/check_version_consistency.py`
> (мягкое предупреждение при расхождении с последним git-тегом). Обновлять при
> релизе MINOR. Эволюция метрик по релизам — в [docs/history.md](docs/history.md).
