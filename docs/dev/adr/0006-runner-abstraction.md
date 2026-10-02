# ADR-0006 — Абстракция исполнения: протокол `Runner` (`RunSpec`/`RunOutcome`)

- **Статус:** Accepted (ретро-запись уже реализованного решения — Runner-слой
  внедрён в issue #136–#140; ADR фиксирует его задним числом, D3 аудита)
- **Дата:** 2026-07-16
- **Связанные issue:** #140 (Runner-слой), #136/#137/#138 (`Runner`/`LocalRunner`
  рефактор без изменения поведения), #116 (контракт результата), #266
  (`SandboxRunner` как второй backend), #406 (юнит-тесты verdict на `RunOutcome`)
- **Связанный код/доки:** [core/runner.py](../../../src/stepik_grader/core/runner.py),
  [core/grader_core.py](../../../src/stepik_grader/core/grader_core.py)
  (`run_single_test`/`set_runner`), [dev/design/server-mode.md § Runner-слой](../design/server-mode.md),
  [../result-contract.md](../result-contract.md); развивает [ADR-0001](0001-server-mode.md)

## Контекст

[ADR-0001](0001-server-mode.md) зафиксировал направление на server mode
**через абстракцию `Runner`**, но не саму абстракцию. На тот момент фактическое
исполнение решения было размазано по `core/grader_core.py`, `core/executor.py` и
`core/microbench_runner.py` — не было единой точки, где можно сменить механизм
запуска (sandbox, будущий remote), не задев логику грейдинга.

Нужна минимальная, обратимая абстракция, которая: (1) не меняет текущее
поведение, (2) даёт один шов для инъекции другого backend'а, (3) держит
вычисление вердикта отдельно от механизма запуска.

## Решение

1. **Ввести `Runner`** — `Protocol` (`runtime_checkable`) с единственным методом
   `run(RunSpec) -> RunOutcome`.
2. **`RunSpec`** (frozen dataclass) описывает **ЧТО** запустить (путь к файлу,
   stdin, лимиты) и **не зависит** от механизма изоляции — одинаков для
   `LocalRunner` и `SandboxRunner`. **`RunOutcome`** несёт **сырой** итог запуска
   (stdout/stderr/returncode/wall time/peak memory/`timed_out`).
3. **Runner не выносит verdict/diff.** Классификация AC/WA/TLE/RE и сравнение
   вывода остаются выше по стеку (`grader_core.py`) — инвариант 3
   ([server-mode.md § Runner-слой](../design/server-mode.md)). Runner — только «запустить
   и вернуть сырой результат».
4. **`LocalRunner`** — рефактор текущего subprocess-пути **без изменения
   поведения** (#138): `subprocess.Popen` с принудительным UTF-8 в дочернем
   окружении, best-effort `RLIMIT_AS` (POSIX-only), фоновый psutil-мониторинг
   пикового RSS.
5. **Инъекция — `grader_core.set_runner()`**, дефолт — `LocalRunner`.
   `grader_core` не знает и не должен знать, какой backend активен.

## Альтернативы

- **A. Прямые `subprocess`-вызовы в `grader_core` (status quo).** Минус: нет
  точки инъекции — sandbox/remote нельзя добавить без хирургии в грейдинге;
  каждую ветку verdict нельзя протестировать без реального subprocess. Отклонено.
- **B. Наследование (`BaseRunner` + подклассы).** Минус: навязывает иерархию и
  жёсткую связность; структурного `Protocol` достаточно, он не требует
  наследоваться от общего базового класса. Отклонено в пользу `Protocol`.
- **C. `Runner`-Protocol + `LocalRunner`, verdict наверху (выбрано).** Дешёвый
  обратимый шаг, полезный и без server mode.

## Последствия

**Положительные:**

- Единая точка смены механизма исполнения; `SandboxRunner` (#266,
  [ADR-0007](0007-sandbox-backends.md)) и будущий remote подключаются
  `set_runner()`-ом **без правок** `grader_core`.
- `run_single_test` разложен так, что каждая ветка verdict (AC/WA/TLE/RE/
  CANCELLED/SANDBOX_VIOLATION) юнит-тестируется на `RunOutcome` без subprocess
  (#406).
- Тестируемость исполнения выросла уже сейчас, до всякого server mode.

**Отрицательные / издержки:**

- Ещё одна абстракция в `core/` — небольшой оверхед; митигируется тем, что
  `LocalRunner` ведёт себя ровно как прежний путь.

**Нейтральные:**

- `RunSpec`/`RunOutcome` — внутренний контракт исполнения; форма
  пользовательского результата остаётся за [result-contract.md](../result-contract.md).
