<!--
Conventional Commits в заголовке PR: fix(...)/feat(...)/docs(...)/refactor(...)/...
Подробности — CONTRIBUTING.md.
-->

## Что и зачем

<!-- Коротко: что меняется и почему. -->

Closes #

## Тип изменения

- [ ] fix — исправление бага
- [ ] feat — новая функциональность
- [ ] docs — документация
- [ ] refactor / test / chore — прочее

## Чеклист

- [ ] `python scripts/preflight.py` — всё чисто (ветка свежая, весь набор,
      линтеры, типы; см. `docs/agent/preflight.md`)
- [ ] Ветка от свежего `main` и обновлена из `origin/main` ПЕРЕД прогоном
      гейтов: «зелено на моей ветке» ≠ «зелено после мержа»
- [ ] `pytest tests/ -x -q` — зелёные
- [ ] `ruff check .` и `ruff format --check .` — чисто
- [ ] `mypy src/stepik_grader scripts` — чисто
- [ ] Новые функции: type hints + docstring; новые модули: `__all__` и
      `from __future__ import annotations`
- [ ] Версия не правится вручную (динамическая, `setuptools-scm`)
- [ ] Запись о изменении добавлена файлом `changelog.d/<slug>.<секция>.md` — в КАЖДОМ PR, без исключений для рефакторингов (формат — `changelog.d/README.md`)
- [ ] HISTORY.md — НЕ на каждый PR, только при релизе
- [ ] Нет секретов в диффе (`secrets.json`, токены, `stepik_config.json`)

## Как проверял

<!-- Команды/шаги проверки. Для UI/web — что открывал и что видел. -->
