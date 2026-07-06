# Локальный глоссарий — формат JSON и API (issue #126)

> Foundation полноценного локального knowledge-модуля глоссария WEB MVP
> (эпик #123). Продуктовый дизайн и роль относительно внешнего проекта —
> каноничен в [web-mvp.md § Глоссарий как локальный knowledge-модуль](web-mvp.md#глоссарий-как-локальный-knowledge-модуль);
> здесь — **формат хранения** карточек/очереди и публичный Python-API пакета
> `stepik_grader.glossary`.

Модуль хранит карточки терминов/исключений/функций **локально** (истина — в
проекте, не на удалённом сайте) и умеет находить пробелы в покрытии. Внешний
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) остаётся целью
**одностороннего** экспорта готовых карточек и в этом PR не затрагивается.

Компактная карта встроенных исключений (`core/glossary.py`, ~30 записей) **не
заменяется** — локальный модуль её расширяет richer-карточками; связь через
`GlossaryCard.id` = `GlossaryEntry.anchor`.

## Формат карточки (`GlossaryCard`)

База карточек — JSON-файл со списком объектов **или** объект с ключом `cards`.
Директория с несколькими `*.json` тоже поддерживается (файлы читаются по
имени). Обязательны только `id` и `title`.

| Поле | Тип | Обяз. | Описание |
|---|---|:--:|---|
| `id` | string | ✓ | Уникальный идентификатор (= `GlossaryEntry.anchor`, напр. `recursionerror`) |
| `title` | string | ✓ | Заголовок карточки |
| `kind` | `exception\|function\|construct\|term` | | Тип (по умолчанию `term`) |
| `summary` | string | | Однострочное пояснение (RU); синоним — `hint` из `core/glossary.py` |
| `body` | string | | Расширенное описание (Markdown) |
| `status` | `new\|draft\|ready\|exported` | | Жизненный цикл (по умолчанию `draft`) |
| `url` | string | | Ссылка во внешний Glossary-Python (цель экспорта) |
| `section` | string | | Раздел глоссария (напр. «Исключения») |
| `aliases` | string[] | | Синонимы для поиска |
| `keywords` | string[] | | Ключевые слова для поиска |
| `tags` | string[] | | Теги для группировки/фильтра |
| `examples` | string[] | | Примеры использования |
| `related` | string[] | | `id` связанных карточек |
| `related_errors` | string[] | | Связанные коды/имена ошибок |

Поиск (`search`) идёт по `id`, `title`, `aliases`, `keywords`, `tags`
(подстрока, без учёта регистра). `hint` принимается как алиас `summary` для
совместимости с контрактом `core/glossary.py`.

Пример — [`examples/glossary.sample.json`](examples/glossary.sample.json).

## Очередь пополнения (`GlossaryMissingEntry`)

Обнаруженные пробелы складываются в отдельный JSON-файл (список объектов) —
это «TODO-лист глоссария», растущий из практики проверки решений (журнал J7).

| Поле | Тип | Обяз. | Описание |
|---|---|:--:|---|
| `concept` | string | ✓ | Недостающая функция/конструкция/исключение (напр. `functools.reduce`) |
| `kind` | `function\|exception\|construct` | | Тип пробела |
| `status` | `new\|draft` | | Жизненный цикл до появления карточки |
| `reason` | string | | Почему помечено |
| `snippet` | string | | Фрагмент кода/ошибки, где встретилось |
| `seen_in` | string[] | | Источники (файлы решений) |
| `suggested_tags` | string[] | | Предлагаемые теги |
| `verdict` | `string\|null` | | Вердикт, если пробел найден из ошибки (RE/WA) |
| `first_seen` | string | | ISO-дата первого обнаружения |

`append_missing_entries()` дедуплицирует по `concept`, объединяя `seen_in`.

## Python-API

```python
from stepik_grader.glossary import (
    JsonGlossaryProvider, MissingConceptDetector, append_missing_entries,
)

# Загрузка локальной базы (файл или директория)
provider = JsonGlossaryProvider.load("docs/examples/glossary.sample.json")
provider.get("recursionerror")          # GlossaryCard | None
provider.search("рекурсия")             # list[GlossaryCard]
provider.list_by_status("ready")        # list[GlossaryCard]
provider.list_by_tag("function")        # list[GlossaryCard]

# Детектор пробелов (без исполнения кода — только AST)
detector = MissingConceptDetector()
missing = detector.detect_from_code(code, known=provider.known_terms(), source="sol.py")
append_missing_entries(".grader_glossary_missing.json", missing)
```

Ошибки чтения (нет файла / битый JSON / нет обязательного поля) поднимаются как
`GlossaryError` (подкласс `ValueError`) с понятным сообщением — грейдер не
падает, вызывающий код решает, показать ошибку или продолжить с пустой базой
(тот же принцип graceful degradation, что у кэша #56).

## Границы (что НЕ входит)

- **WEB UI и endpoint'ы** (`/api/glossary*`) — реализация в #125/#129.
- **Экспортёр во внешний Glossary-Python** — отдельная задача #126-follow-up.
- **SQLite-хранилище** (#130+) — сейчас JSON-first; API провайдера
  (`GlossaryProvider`-протокол) абстрагирует источник для будущей замены.
