# ADR-0011 — Локальная персистентность: общий `core/db.py`, выборочная миграция на SQLite

- **Статус:** Proposed
- **Дата:** 2026-07-19
- **Связанные issue:** #529 (эпик E5), #548 (этот ADR); реализация — #530 (эпик E6):
  #551 (общий `atomic_io.atomic_write_json` + атомарная запись missing-queue/settings),
  #552 (`core/db.py` + миграция missing-queue на SQLite/WAL); контекст — #344 (history),
  #408 (атомарный JSON)
- **Связанный дизайн:** [docs/architecture.md](../architecture.md),
  [docs/configuration.md](../configuration.md), [docs/logging.md](../logging.md)
- **Опирается на:** [ADR-0002](0002-history-opt-in.md) (история opt-in),
  [ADR-0009](0009-server-data-model.md) (доменная модель server mode)

## Контекст

Локальная персистентность размазана по семи механизмам с разной надёжностью:

| Механизм | Хранилище | Формат | Durability / гонки |
|---|---|---|---|
| `core/history.py` | `.grader_history.db` | SQLite/WAL, schema v1 | атомарно; межпроцессная гонка закрыта (WAL) |
| `core/storage.py` | произвольный путь | JSON | атомарно (`mkstemp`+`os.replace`+`fsync`) |
| `core/stats.py` | `.grader_stats.jsonl` | JSONL append-only | устойчив к обрыву построчно; process-only Lock |
| `core/cache.py` | `.grader_cache/results.json` | JSON | opt-in, регенерируемо |
| `glossary/json_provider.py` | missing-queue JSON | JSON | атомарно с #551 (`atomic_write_json`, `fsync`); межпроцессная гонка остаётся (process-only Lock) → SQLite в #552 |
| `core/user_settings.py` | `.grader_settings.json` | JSON | атомарно с #551 (`atomic_write_json`, `mkstemp` без `fsync`; прежний фиксированный `.tmp` делили писатели) |
| config/secrets | `stepik_config.json`/`secrets.json` | JSON | secrets `0600`, атомарно |

> **Обновление (#551, первый шаг E6).** Атомарность записи missing-queue и
> settings уже закрыта — общий `atomic_write_json` заменил голый `open("w")`
> (missing-queue) и фиксированный `.tmp` (settings). Осталась ровно
> **межпроцессная гонка** CLI+web для missing-queue — её снимает миграция на
> SQLite/WAL (#552). То есть durability и гонка развязаны на два шага: сперва
> атомарный JSON-писатель (#551), затем SQLite для кросс-процессной безопасности
> (#552).

SQLite-подключение + PRAGMA-шаблон (`_connect`, `journal_mode=WAL`,
`busy_timeout`, `foreign_keys=ON`, `user_version`-миграция) сегодня заперты внутри
`core/history.py:139-168` — переиспользовать негде. Межпроцессная гонка CLI+web
закрыта только для history; missing-queue пишется неатомарно голым `open("w")` и
гонится между CLI и web. Отдельного `core/db.py` нет.

Это решения уровня ADR: границы (что БД, что файл), durability (кто может потерять
запись), дорогой откат (миграция формата). Контекст фиксируется ДО кода —
реализация вынесена в эпик E6 (#530).

## Решение

1. **Ввести общий `core/db.py`** — вынести `_connect` + PRAGMA (WAL,
   `busy_timeout`, `foreign_keys=ON`) + `user_version`-миграцию из `history.py` в
   переиспользуемый leaf-модуль. `history.py` начинает потреблять его; наблюдаемое
   поведение не меняется.
2. **Мигрировать на SQLite только missing-queue глоссария.** У неё разом две
   проблемы — неатомарная запись (`open("w")`) и межпроцессная гонка CLI+web;
   SQLite/WAL закрывает обе. Durability-выигрыш оправдывает миграцию формата.
3. **Оставить на JSON/JSONL сознательно:**
   - `stats.jsonl` — append-only JSONL, устойчивость к обрыву на уровне строк — это
     ФИЧА, а не долг; SQLite её бы ухудшила. **НЕ мигрируем.**
   - `cache` — low-priority (opt-in, регенерируемо); миграции не стоит.
   - `user_settings`/config/secrets — простые редко-пишущиеся JSON; атомарности
     достаточно.
4. **НЕ единый физический `.grader.db` для всего.** history, cache и будущий
   missing-queue-db — разные файлы с разным жизненным циклом (история копится; кэш
   сбрасывается; очередь пополняется). Общий `core/db.py` — это общий **код**
   подключения, а не общий **файл**.
5. **Graceful degradation — инвариант.** Любой персистентный слой при битом
   хранилище / `sqlite3.Error` / `OSError` тихо деградирует (пропуск записи, пустое
   чтение), не роняя грейдинг — как уже делает history.

## Альтернативы

- **A. Единый `.grader.db` для всей персистентности.** Минус: смешивает разные
  жизненные циклы (история / кэш / очередь), усложняет очистку и сброс, повышает
  contention на одном файле. Отклонено.
- **B. Статус-кво** (всё на JSON, `_connect` заперт в history). Минус: missing-queue
  теряет записи и гонится CLI+web; PRAGMA-шаблон не переиспользуется. Отклонено.
- **C. Мигрировать ВСЁ на SQLite (включая stats).** Минус: `stats.jsonl` теряет
  устойчивость-к-обрыву append-only; миграция ради единообразия, а не durability.
  Отклонено.
- **D. `core/db.py` (общий код) + выборочная миграция missing-queue; stats/cache
  остаются файлами (выбрано).** Мигрируем ровно то, у чего реальная
  durability-проблема; остальное — по назначению формата.

## Миграция (фазы)

Реализация — эпик **E6** (#530): `core/db.py` (`_connect`+PRAGMA WAL+`busy_timeout`+
`user_version` по шаблону `history.py`) и миграция missing-queue на SQLite/WAL
(durability + межпроцессная гонка закрыты разом; битая БД → тихий пропуск).
`stats.jsonl` не трогается; единого `.grader.db` не вводится. Этот ADR фиксирует
только РЕШЕНИЕ — код, схема и тесты живут в E6.

## Последствия

**Положительные:**

- PRAGMA/подключение переиспользуемы; missing-queue получает durability +
  межпроцессную безопасность «даром» от общего `core/db.py`.
- Явное правило «что БД, что файл» — будущие «давайте всё в одну `.grader.db`»
  отсылаются к этому ADR.

**Отрицательные / издержки:**

- Ещё один core-leaf (`core/db.py`); митигируется тем, что это вынос
  существующего кода, а не новый механизм.
- Миграция формата missing-queue (JSON→SQLite) требует одноразового переноса и
  совместимости чтения — деталь E6.

**Нейтральные:**

- Доменная модель server mode (PostgreSQL-надмножество SQLite,
  [ADR-0009](0009-server-data-model.md)) не затрагивается — этот ADR про
  ЛОКАЛЬНУЮ персистентность.
