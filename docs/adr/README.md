# Architecture Decision Records (ADR)

> Журнал архитектурных решений проекта. ADR фиксирует **одно значимое
> решение**: контекст, само решение, рассмотренные альтернативы и последствия.
> Формат — облегчённый [Nygard-style](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Зачем ADR

Большие архитектурные развилки (server mode, смена механизма исполнения,
API-поверхность) нельзя закапывать в issue/PR — они теряются. ADR — короткая,
датированная запись «почему мы так решили», на которую ссылаются дизайн-доки и
код. Продуктовые/технические детали живут в `docs/*` (напр.
[server-mode.md](../server-mode.md)); ADR фиксирует **решение**, а не
спецификацию.

## Соглашения

- Файл: `NNNN-краткий-слаг.md` (`0001-server-mode.md`).
- Статусы: `Proposed` → `Accepted` / `Rejected` → `Superseded by ADR-XXXX`.
- Один ADR = одно решение. Изменение решения — **новый** ADR, который помечает
  старый `Superseded`, а не правка старого задним числом.
- Секции: Status · Context · Decision · Alternatives · Consequences (+
  Migration, если решение фазовое).

## Индекс

| ADR | Решение | Статус |
|---|---|---|
| [0001](0001-server-mode.md) | Направление на server mode через Runner-абстракцию (без немедленной реализации) | Accepted |
| [0002](0002-history-opt-in.md) | Запись истории прогонов в CLI остаётся opt-in (тумблер в меню + nudge, web — default-on) | Accepted |
| [0003](0003-ai-integration.md) | AI-интеграция — BYOK OpenAI-compatible на `requests` (облако + ollama одним кодом, opt-in, без новых зависимостей) | Accepted |
| [0004](0004-src-layout.md) | Раскладка пакета — src-layout (`src/stepik_grader/`), чистая миграция без root-shim'ов | Accepted |
| [0005](0005-dynamic-versioning.md) | Версия пакета — динамическая из git-тегов (`setuptools-scm`), статической `version` нет | Accepted |
| [0006](0006-runner-abstraction.md) | Абстракция исполнения — протокол `Runner` (`RunSpec`/`RunOutcome`, `LocalRunner`, verdict наверху) | Accepted |
| [0007](0007-sandbox-backends.md) | OS-песочница — opt-in `--sandbox`, три нативных backend'а по ОС, fail-loud без тихого fallback | Accepted |
