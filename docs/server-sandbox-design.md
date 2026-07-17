# Server-mode SandboxRunner — дизайн контейнерного backend

> Дизайн-документ (issue #153). **Не реализация**: описывает целевой
> контейнерный backend `SandboxRunner` для server mode, механику жёстких лимитов
> и сетевой изоляции — не добавляя кода, зависимостей или демонов (запреты
> [CLAUDE.md](../CLAUDE.md), Non-goals [server-mode.md](server-mode.md#non-goals)).
> Решение о *классе* backend зафиксировано в
> [ADR-0008](adr/0008-server-sandbox-backend.md); этот документ — техническая
> спецификация, на которую ADR ссылается.
>
> Обязательные требования безопасности (что backend ДОЛЖЕН обеспечить) — в
> [server-mode.md § #157](server-mode.md#sandbox-и-сетевая-изоляция-issue-157).
> Здесь — **как** они закрываются на Linux-примитивах. Локальный `--sandbox`
> MVP (issue #266) — в [SECURITY.md](../SECURITY.md#--sandbox--sandboxrunner-mvp-issue-266).

## Оглавление

- [Зачем и границы](#зачем-и-границы)
- [Выбор класса backend](#выбор-класса-backend)
- [CPU / RAM / time limits (cgroups v2)](#cpu--ram--time-limits-cgroups-v2)
- [Network-off (сетевой namespace)](#network-off-сетевой-namespace)
- [ФС-изоляция и эфемерность](#фс-изоляция-и-эфемерность)
- [Seccomp-профиль](#seccomp-профиль)
- [Место в Runner-абстракции](#место-в-runner-абстракции)
- [Отношение к локальному `--sandbox` MVP](#отношение-к-локальному---sandbox-mvp-issue-266)
- [Rootless vs privileged и fallback](#rootless-vs-privileged-и-fallback)
- [Открытые вопросы реализации (вне дизайна)](#открытые-вопросы-реализации-вне-дизайна)

---

## Зачем и границы

Server mode исполняет **недоверенный** код многих клиентов на общем хосте — это
прямая RCE-поверхность без настоящей изоляции ([ADR-0001](adr/0001-server-mode.md)).
[server-mode.md § #157](server-mode.md#sandbox-и-сетевая-изоляция-issue-157)
задал требования (сеть off, эфемерные tmp, жёсткие квоты, изоляция клиентов); он
намеренно не выбирал механизм. Этот документ выбирает **класс** механизма и
раскладывает каждое требование на конкретный Linux-примитив, чтобы будущая
реализация (фаза 2-server) стартовала со спецификации, а не с чистого листа.

**Границы:** это дизайн. Ни `SandboxRunner`-класса для сервера, ни зависимостей,
ни выбора конкретного OCI-рантайма/оркестратора здесь нет — только требования к
ним и отображение на примитивы ядра. Локальная threat model (фаза 0, доверенный
код без sandbox) не меняется.

---

## Выбор класса backend

Решение (полное обоснование и альтернативы — [ADR-0008](adr/0008-server-sandbox-backend.md)):
**контейнер уровня ОС на Linux-хосте** — namespaces + cgroups v2 + seccomp,
rootless по умолчанию, за тонким адаптером к OCI-совместимому рантайму. Не
привязан к Docker; допускает усиление (gVisor/микро-VM) как параметр деплоя.

| Вариант | Изоляция | Старт | Оверхед прогона | Вес эксплуатации | Роль |
|---|---|---|---|---|---|
| OS-контейнер (namespaces+cgroups+seccomp) | сильная (общий kernel) | ~10–30 мс | низкий | средний | **дефолт** |
| gVisor (`runsc`) | сильнее (user-space syscalls) | ~50–100 мс | заметный на syscall-heavy | средний | усиление (opt-in деплоя) |
| Микро-VM (Firecracker) | сильнейшая (своё ядро) | ~100–200 мс | низкий внутри | высокий (KVM, rootfs) | усиление для публичной мультитенантности |
| bubblewrap per-process (локальный MVP) | средняя, без per-tenant cgroup/очереди | ~5 мс | низкий | низкий | **только локальный `--sandbox`**, не сервер |

**Почему не микро-VM по умолчанию:** оверхед старта и эксплуатации (KVM,
образы) непропорционален для типичного прогона грейдера в секунды; оставлено как
рекомендованное усиление фазы 4 (публичный сервер). **Почему не gVisor по
умолчанию:** оверхед на syscall-heavy код искажает bench/microbench-вердикты —
конфликт с назначением грейдера мерить время честно.

---

## CPU / RAM / time limits (cgroups v2)

Требование [#157.3](server-mode.md#sandbox-и-сетевая-изоляция-issue-157) —
**жёсткие** лимиты (не best-effort POSIX `resource`, который обходится и не даёт
per-tenant учёта). Каждый прогон — в своей cgroup v2:

| Лимит | Контроллер cgroup v2 | Превышение → |
|---|---|---|
| Память (RSS+page cache) | `memory.max` (+ `memory.swap.max=0`) | OOM-kill процесса → вердикт памяти (`MLE`/`sandbox_violation`) |
| CPU-время (доля) | `cpu.max` (quota/period) | троттлинг; wall-таймаут остаётся верхней границей |
| Число процессов/потоков | `pids.max` | форк-бомба упирается в лимит → `sandbox_violation` |
| Wall-время | внешний дедлайн супервизора (как сейчас `subprocess timeout`) | `TLE`, kill всей cgroup |
| Размер stdout/stderr | счётчик супервизора при чтении пайпов | обрезка + `sandbox_violation` |

Ключевое отличие от локального MVP: лимит памяти/CPU **kernel-enforced на
cgroup**, а не `setrlimit`. Wall-время и размер вывода остаются заботой
супервизора (родительского процесса вне sandbox), как в текущем `LocalRunner`.
CPU-время из `cpu.stat` даёт честный per-run учёт для квот клиента (#157.6).

---

## Network-off (сетевой namespace)

Требование [#157.1](server-mode.md#sandbox-и-сетевая-изоляция-issue-157) — у
кода нет ни исходящего доступа, ни слушающих сокетов. Механизм: прогон в **новом
network namespace** (`unshare(CLONE_NEWNET)`) — свежий netns содержит только
loopback-интерфейс `lo` в состоянии DOWN и ни одного `veth`/uplink наружу. Без
маршрутизируемого интерфейса любой `connect()`/`bind()` на внешний адрес
завершается ошибкой на уровне ядра, без firewall-правил, которые можно исказить.

Скачивание задач/тестов делает **сервер до** старта решения и кладёт их в
эфемерный tmp прогона (ниже) — само решение сеть не трогает. Это тот же
инвариант, что уже реализован локально (`--unshare-net` в `core/sandbox/_linux.py`),
поднятый до server-mode уровня.

---

## ФС-изоляция и эфемерность

Требования [#157.2 и #157.5](server-mode.md#sandbox-и-сетевая-изоляция-issue-157)
— запись только в приватный per-run tmp, удаляемый после прогона; между
прогонами состояние не сохраняется.

- **Mount namespace** (`CLONE_NEWNS`) + минимальный root: read-only bind
  интерпретатора и stdlib (тот же приём, что `_python_tree_binds()` в локальном
  MVP, issue #420 — `/usr` bind под loader), поверх — **писчий tmpfs** только
  под рабочий каталог прогона.
- Нет доступа к ФС хоста, другим прогонам, `secrets.json`, OAuth-токенам
  (#157.4). Секреты сервера не пробрасываются в окружение sandbox (env
  очищается; редакция в логах — [logging.md](logging.md)).
- Каталог прогона — уникальный, эфемерный (tmpfs размонтируется вместе с mount
  ns; на диске ничего не остаётся). Каждый прогон стартует из чистого образа
  root — состояние не переносится (#157.5).

---

## Seccomp-профиль

Дополнительно к namespaces — seccomp-BPF фильтр, отсекающий заведомо ненужный
грейдингу класс syscalls (`ptrace`, `mount`, `keyctl`, модульные/`bpf`,
`clock_settime`, raw-сокеты и т.п.). Профиль — allowlist в духе профиля Docker
default, суженный до нужд Python-исполнения. Это defense-in-depth поверх
контейнера (#157 «полная защита от эскалации ядра — ответственность механизма»);
конкретный список syscalls — этап реализации, не этот документ.

---

## Место в Runner-абстракции

Server-mode sandbox — ещё одна реализация `Runner` ([server-mode.md § Runner-слой](server-mode.md#runner-слой-issue-140-реализация--136137138)),
не переписывание грейдинга:

```
grader_core.run_single_test(...) → Runner.run(RunSpec) → RunOutcome
                                     └ ServerSandboxRunner (контейнер + cgroup + netns)
```

- **`RunSpec`** уже несёт `timeout`/`max_memory_mb` — server-backend отображает
  их на `cpu.max`/`memory.max`/wall-дедлайн. Дополнительные server-лимиты
  (`pids.max`, размер вывода, размер артефактов) — расширение `RunSpec`
  **аддитивно**, без слома локального пути.
- **`RunOutcome`** остаётся тем же — все девять полей (`stdout`/`stderr`/
  `returncode`/`elapsed`/`peak_memory_mb`/`timed_out`/`launch_error`/`cancelled`/
  `sandbox_violation`). Server-backend **не** вводит новое поле: нарушение квоты
  отражается тем же `sandbox_violation`, которое уже добавлено локальным
  `--sandbox` MVP (issue #266) и уже мапится выше по стеку (`grader_core.py`) в
  аддитивный вердикт `SANDBOX_VIOLATION` — по правилу 3
  [result-contract.md](result-contract.md) (вердикт добавлен, не ломая
  существующие AC/WA/TLE/RE/CANCELLED). Это согласуется с классом ошибок API
  [server-mode.md § Классы ошибок](server-mode.md#контракт-api-удалённого-исполнения-issue-156)
  (`sandbox_violation` — вердикт в теле результата, не HTTP-ошибка).
- Инвариант ADR-0001 сохранён: ни `grader_core`, ни адаптеры (CLI/Web/API) не
  знают, какой Runner активен — выбор инжектируется (`set_runner`).

---

## Отношение к локальному `--sandbox` MVP (issue #266)

Локальный `core/sandbox/` (bubblewrap/`sandbox-exec`/Job Objects за `--sandbox`)
— **не** server-backend, но задаёт переиспользуемые приёмы:

| Приём локального MVP | В server-backend |
|---|---|
| `--unshare-net` (netns без внешних интерфейсов) | тот же принцип, поднят до per-run контейнера |
| usrmerge/`/usr` read-only bind под ELF-loader (#420) | тот же минимальный read-only root |
| per-run tmp + уборка | tmpfs в mount ns, эфемерность |
| POSIX `setrlimit` (best-effort) | **заменяется** cgroup v2 (kernel-enforced) |
| один процесс, без очереди/multi-tenancy | **добавляется** cgroup-иерархия per-client, очередь, per-tenant учёт |

Вывод: server-backend — это локальный подход + cgroups v2 + мультитенантная
оркестрация. Локальный MVP остаётся как есть (opt-in на доверенной машине).

---

## Rootless vs privileged и fallback

**Дефолт — rootless** (unprivileged user namespaces): контейнер без root на
хосте, меньше поверхность эскалации. Где хост запрещает unprivileged userns
(тот же класс ограничения, что вскрыт в CI, [issue #420](../CHANGELOG.md)) —
задокументированный **fallback на privileged-контейнер** под контролируемым
демоном/супервизором с теми же cgroup/netns/seccomp. Выбор профиля — параметр
деплоя, не кода.

---

## Открытые вопросы реализации (вне дизайна)

Осознанно **не** решаются этим документом (этап реализации фазы 2-server):

- Конкретный OCI-рантайм (`runc`/`crun`/`youki`) и способ его установки.
- Оркестрация: очередь прогонов, пул воркеров, автоскейл, backpressure
  (частично прототипировано локально — `web/runs.py`, issue #262/#429).
- Точный seccomp-allowlist и его тестирование.
- Хранилище артефактов прогона и их TTL (смыкается с
  [server-data-model.md](server-data-model.md)).
- Метрики/квоты per-client (частота, параллелизм) — класс ошибок `quota_exceeded`
  уже в контракте API.

Всё вышеперечисленное реализуется только после явного решения включать server
mode (фаза 3–4) и не входит в текущую подготовку.
