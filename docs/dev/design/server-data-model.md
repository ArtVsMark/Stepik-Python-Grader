# Server-mode данные — доменная модель, PostgreSQL, accounts

> Дизайн-документ (issue #154, #155). **Не реализация**: описывает целевую
> доменную модель server mode, её отображение на PostgreSQL поверх нынешней
> локальной SQLite-истории и модель accounts/workspaces/courses с правами
> доступа — не добавляя кода, миграций или зависимостей (запреты
> [CLAUDE.md](../../../CLAUDE.md), Non-goals [server-mode.md](server-mode.md#non-goals)).
> Решение «единая доменная модель + PostgreSQL как серверное хранилище» —
> [ADR-0009](../adr/0009-server-data-model.md).
>
> Текущая локальная история (SQLite, `core/history.py`) — источник, поверх
> которого строится серверная схема. Sandbox-исполнение прогонов — соседний
> дизайн [server-sandbox-design.md](server-sandbox-design.md).

## Оглавление

- [Зачем и границы](#зачем-и-границы)
- [Единая доменная модель (одна модель, два хранилища)](#единая-доменная-модель-одна-модель-два-хранилища)
- [Accounts / workspaces / courses / tasks (issue #155)](#accounts--workspaces--courses--tasks-issue-155)
- [Права доступа (RBAC)](#права-доступа-rbac)
- [PostgreSQL-схема поверх SQLite (issue #154)](#postgresql-схема-поверх-sqlite-issue-154)
- [Стратегия миграции SQLite → PostgreSQL](#стратегия-миграции-sqlite--postgresql)
- [Открытые вопросы реализации (вне дизайна)](#открытые-вопросы-реализации-вне-дизайна)

---

## Зачем и границы

Локальная история прогонов (`core/history.py`, SQLite: `runs`/`case_results`/
`lint_violations`, [server-mode.md](server-mode.md)) — однопользовательская, в
файле `.grader_history.db`. Server mode ([ADR-0001](../adr/0001-server-mode.md))
исполняет прогоны **многих клиентов** и должен хранить их с изоляцией по
клиентам ([#157.6](server-mode.md#sandbox-и-сетевая-изоляция-issue-157)) в
разделяемой БД (PostgreSQL). Issue #154 просит согласовать SQLite и PG **без
дублирования доменной модели**; #155 — спроектировать сущности
пользователей/рабочих-пространств/курсов/задач и права.

**Границы:** это дизайн. Ни ORM, ни миграций, ни серверного кода — только форма
данных и стратегия. Локальная SQLite остаётся как есть (фаза 0). Биллинг,
федерация, аналитика вне рамок (фаза 4+).

---

## Единая доменная модель (одна модель, два хранилища)

Ключевой инвариант (как «ядро — библиотека» в ADR-0001): **доменная модель одна**,
хранилищ — два профиля. Прогон, кейс-результат и lint-нарушение — это доменные
сущности `core/`, а SQLite и PostgreSQL — их разные **бэкенды хранения**, не
разные модели.

```
core/ доменные типы прогона:
        CaseRecord, LintRecord   — уже есть (core/history.py)
        RunRecord                — вводится (сейчас поля прогона пишутся
                                   напрямую в record_run() без dataclass'а)
        │
        ├─ SQLite backend (core/history.py) — локально, single-user   [фаза 0, есть]
        └─ PostgreSQL backend               — сервер, multi-tenant     [фаза 3+, дизайн]
```

Практически это означает: серверная PG-схема — **надмножество** локальной
(те же поля прогона/кейса/линта + tenancy-колонки), а не параллельная модель.
Один набор доменных типов сериализуется в оба бэкенда; запись/чтение — за
абстракцией репозитория (как `Runner` абстрагирует исполнение). Это закрывает
критерий #154 «нет дублирования доменной модели».

> **Точность про `RunRecord`.** Сейчас в `core/history.py` есть dataclass'ы
> `CaseRecord` и `LintRecord`, но **нет** `RunRecord` — поля прогона (`mode`,
> `source`, `task_key`, `solution_hash`, `duration_s`) передаются в
> `record_run()` аргументами и пишутся в таблицу `runs` напрямую. Явный
> `RunRecord` **вводится** этим дизайном как третий доменный тип, чтобы оба
> бэкенда сериализовали прогон единообразно. Это дешёвый рефактор в рамках
> реализации, не смена поведения.

---

## Accounts / workspaces / courses / tasks (issue #155)

Иерархия сущностей (высокоуровнево — без физических типов, они в § PostgreSQL):

| Сущность | Смысл | Ключевые связи |
|---|---|---|
| **user** | Аккаунт человека (email/OAuth-идентити) | членство в workspaces |
| **workspace** | Изолированный контейнер данных (личный или командный) | владелец-user; содержит courses/runs |
| **membership** | Связка user↔workspace + роль | user, workspace, role |
| **course** | Набор задач (аналог курса Stepik или своего) | принадлежит workspace |
| **task** | Задача: метаданные + ссылка на test-set | принадлежит course; ссылается на test_set |
| **test_set** | Версионированный набор тест-кейсов (форматы 1–3) | референс из task и из прогона |
| **run** | Прогон решения (доменный `RunRecord`, вводится) | user, workspace, task, test_set |

**Инварианты модели:**

1. **Всё принадлежит workspace.** `run`/`course`/`task` несут `workspace_id` —
   граница изоляции клиентов ([#157.6](server-mode.md#sandbox-и-сетевая-изоляция-issue-157))
   на уровне данных: запрос всегда ограничен workspace'ами, где у user есть
   membership.
2. **`test_set` версионируется**, а `run` ссылается на конкретную версию — иначе
   перегенерация тестов задним числом делает старые вердикты невоспроизводимыми
   (та же проблема, что локально решает `solution_hash`/перескачивание #394).
3. **Личный режим = workspace из одного user'а.** Single-user (нынешний
   локальный опыт) — вырожденный случай: один user, один personal workspace,
   роль owner. Это гарантирует, что локальная и серверная модели — одна и та же,
   просто с N=1.

---

## Права доступа (RBAC)

Минимальный ролевой контроль на уровне membership (user↔workspace):

| Роль | Может |
|---|---|
| **owner** | всё в workspace + управление membership/удаление workspace |
| **maintainer** | CRUD courses/tasks/test_sets, видеть все прогоны workspace |
| **member** | запускать прогоны, видеть свои прогоны и курсы workspace |
| **viewer** | read-only: курсы/задачи, без запуска и без чужих прогонов |

Правила:

- Проверка доступа — **всегда** через membership: нет строки membership
  (user, workspace) → 404 (не 403), чтобы не раскрывать существование чужих
  workspace'ов.
- Прогон видит его автор (`run.user_id`) и роли maintainer+ того же workspace.
- Секреты/токены (OAuth) — **не** доменные данные workspace: живут отдельно,
  в исполнение sandbox не пробрасываются ([#157.4](server-mode.md#sandbox-и-сетевая-изоляция-issue-157)),
  редакция в логах — [logging.md](../logging.md).
- RBAC — на уровне API/сервиса, **не** в sandbox: изолированный код прогона не
  имеет доступа к БД вовсе (сеть off, § sandbox-design).

---

## PostgreSQL-схема поверх SQLite (issue #154)

Серверная схема — надмножество локальной. Слева — как есть в
`core/history.py` (`_SCHEMA_V1`), справа — серверный профиль.

**Прогон (`runs`).** Локально:

```sql
-- SQLite (core/history.py, есть)
runs(id INTEGER PK, ts_utc TEXT, mode INTEGER, source TEXT,
     task_key TEXT, solution_name TEXT, solution_hash TEXT, duration_s REAL)
```

Сервер добавляет tenancy и нативные типы, сохраняя те же смысловые поля:

```sql
-- PostgreSQL (дизайн)
runs(
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts_utc        TIMESTAMPTZ NOT NULL,          -- было TEXT
  mode          SMALLINT    NOT NULL,
  source        TEXT        NOT NULL,          -- 'cli'|'web'|'api'
  -- tenancy (server-only):
  workspace_id  BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id       BIGINT NOT NULL REFERENCES users(id),
  task_id       BIGINT      REFERENCES tasks(id),        -- заменяет свободный task_key
  test_set_id   BIGINT      REFERENCES test_sets(id),    -- версия тестов (инвариант 2)
  task_key      TEXT        NOT NULL,          -- сохраняем для совместимости/личного режима
  solution_name TEXT,
  solution_hash TEXT,
  duration_s    DOUBLE PRECISION
);
```

**Кейс-результаты и линт** — структурно те же, с `run_id`-FK и `ON DELETE
CASCADE` (как в SQLite); физические типы — нативные PG (`SMALLINT`/`DOUBLE
PRECISION`/`TEXT`):

```sql
case_results(run_id BIGINT REFERENCES runs(id) ON DELETE CASCADE,
             case_no SMALLINT, verdict TEXT, time_ms DOUBLE PRECISION,
             error_class TEXT, failure_kind TEXT,
             PRIMARY KEY (run_id, case_no));

lint_violations(run_id BIGINT REFERENCES runs(id) ON DELETE CASCADE,
                rule_code TEXT, line_no INTEGER, message TEXT);
```

**Соответствия SQLite → PostgreSQL:**

| SQLite | PostgreSQL | Зачем |
|---|---|---|
| `INTEGER PK` | `BIGINT GENERATED … IDENTITY` | масштаб id под многих клиентов |
| `ts_utc TEXT` (ISO-строка) | `TIMESTAMPTZ` | нативные операции по времени, TZ-корректность |
| `REAL` | `DOUBLE PRECISION` | точность таймингов |
| `user_version`-миграции | инструмент миграций (Alembic-класс) | версионирование серверной схемы |
| файл `.grader_history.db` | БД + `workspace_id` на строках | изоляция клиентов на уровне данных |

**Индексы** сохраняются и расширяются tenancy: `idx_runs_task` →
`(workspace_id, task_key, id)`; частые выборки «прогоны user в workspace» →
`(workspace_id, user_id, id)`.

Отсутствие дублирования (критерий #154): доменные поля прогона/кейса/линта — **те
же имена и смысл**; сервер лишь оборачивает их tenancy-контекстом и нативными
типами. Один доменный тип прогона (`RunRecord`, вводится) пишется обоими
бэкендами.

---

## Стратегия миграции SQLite → PostgreSQL

Не «переключение», а надстройка (каждый шаг обратно совместим, как фазы
[server-mode.md](server-mode.md#фазовая-миграция)):

1. **Абстракция репозитория** в `core/` (интерфейс чтения/записи истории) —
   локальный SQLite-бэкенд реализует её без смены поведения (дешёвый обратимый
   шаг, аналог `Runner`/`LocalRunner`). Локальный опыт не меняется.
2. **PG-бэкенд** реализует ту же абстракцию для сервера; схема — надмножество
   (§ выше). Личный режим сервера = один workspace (инвариант 3), поэтому та же
   доменная модель работает с N=1.
3. **Импорт локальной истории** (опц., на будущее): `.grader_history.db` → PG в
   personal workspace одноразовым скриптом — доменные поля совпадают, tenancy
   проставляется константой. Не обязателен для запуска сервера.
4. **Версионирование серверной схемы** — миграционный инструмент (Alembic-класс),
   а не `PRAGMA user_version`: server-схема эволюционирует независимо, локальная
   SQLite остаётся на своих `user_version`-миграциях.

Обратной миграции (PG → SQLite) не требуется: локальный однопользовательский
режим самодостаточен и остаётся на SQLite.

---

## Открытые вопросы реализации (вне дизайна)

Осознанно **не** решаются (этап реализации фазы 3+):

- Конкретный ORM/драйвер и миграционный инструмент (тяжёлые зависимости — только
  по явному решению, [CLAUDE.md](../../../CLAUDE.md)).
- Аутентификация (OAuth-провайдеры, сессии/токены) — смыкается с
  [oauth](../../use/installation.md), но серверные сессии — отдельный дизайн.
- Хранилище артефактов прогона (исходники/вывод) и их TTL — смыкается с
  [server-sandbox-design.md](server-sandbox-design.md).
- Шардинг/репликация/бэкапы PostgreSQL — эксплуатация, не доменный дизайн.
- Точные квоты per-client (частота/параллелизм) — класс ошибок `quota_exceeded`
  уже в [контракте API](server-mode.md#контракт-api-удалённого-исполнения-issue-156).

Всё вышеперечисленное — только после явного решения включать server mode
(фаза 3–4), вне текущей подготовки.
