# Архитектура модулей

> Вынесено из README (issue #105 / эпик #102). Обзор проекта — в
> [README](../README.md); детальные инварианты и текущие задачи — в
> [`CLAUDE.md`](../CLAUDE.md).

Граф зависимостей — DAG без циклов (все модули живут в `src/stepik_grader/`):

```
downloader.py          ──→  core/storage.py
downloader.py          ──→  core/stepik_client.py
downloader.py          ──→  core/parsers.py
core/stepik_client.py ──→  core/storage.py
grader.py              ──→  core/grader_core.py, core/reporter.py, cli.py  (тонкий фасад)
core/grader_core.py    ──→  core/executor.py, core/microbench_runner.py, core/normalizers.py
core/grader_core.py    ──→  core/test_loader.py, core/mode_detector.py, core/wrapper_builder.py
core/test_loader.py    ──→  core/mode_detector.py, core/parsers.py
core/mode_detector.py  ──→  core/storage.py
cli.py                 ──→  core/grader_core.py, core/reporter.py, core/microbench_runner.py
diagnostic_stepik.py ──→  core/stepik_client.py
diagnostic_stepik.py ──→  downloader.py       ← parse_stepik_step_url
downloader.py        ──→  core/oauth_flow.py
diagnostic_stepik.py ──→  core/oauth_flow.py
core/oauth_flow.py    ──→  core/stepik_client.py
core/oauth_flow.py    ──→  core/storage.py
```

downloader.py больше не импортирует grader.py: дублирующая копия
`_parse_testblock_file` в grader.py устранена (Issue #19) — оба модуля
читают `parse_testblock_file` из `core/parsers.py`.

Слои (снизу вверх):

```
┌───────────────────────────────────────────────────────────────┐
│  Domain / Application  (src/stepik_grader/ — точки входа)      │
│  downloader.py  │  grader.py (facade)  │  diagnostic_stepik   │
├───────────────────────────────────────────────────────────────┤
│  Application  (core/, грейдер разбит по SRP — Sprint 7, A-01) │
│  core/grader_core.py (исполнение)  │  core/reporter.py (вывод)│
│  core/test_loader.py │ core/mode_detector.py │ wrapper_builder │
│  cli.py (меню, публичная точка входа — stepik-grader)          │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure  (core/)                                       │
│  core/stepik_client.py  │  core/executor.py                    │
│  core/microbench_runner.py  │  core/oauth_flow.py              │
├───────────────────────────────────────────────────────────────┤
│  Infrastructure / Utilities  (core/, leaf, no deps)            │
│  core/storage.py  │  core/normalizers.py                       │
└───────────────────────────────────────────────────────────────┘
```

`core/storage.py` и `core/normalizers.py` — leaf-модули: не импортируют ничего из проекта, легко тестируются изолированно.
