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

- [ ] Ветка от свежего `main`, PR — в `main` (не коммит в `main` напрямую)
- [ ] `pytest tests/ -x -q` — зелёные
- [ ] `ruff check .` и `ruff format --check .` — чисто
- [ ] `mypy src/stepik_grader scripts` — чисто
- [ ] Новые функции: type hints + docstring; новые модули: `__all__` и
      `from __future__ import annotations`
- [ ] Версия не правится вручную (динамическая, `setuptools-scm`)
- [ ] CHANGELOG.md: запись под `## [Unreleased]` добавлена — в КАЖДОМ PR (#373), без исключений для рефакторингов
- [ ] CHECKPOINT.md / docs/history.md — НЕ на каждый PR, только при релизе
- [ ] Нет секретов в диффе (`secrets.json`, токены, `stepik_config.json`)

## Как проверял

<!-- Команды/шаги проверки. Для UI/web — что открывал и что видел. -->
