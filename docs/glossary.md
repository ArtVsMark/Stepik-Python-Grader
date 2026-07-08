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

Компактная карта встроенных исключений (`core/glossary.py`, ~28 записей) **не
заменяется** — локальный модуль её расширяет richer-карточками; связь через
`GlossaryCard.id` = `GlossaryEntry.anchor`.

## Источники истины (роли)

Чтобы не путать «где хранится контент» и «относительно чего меряется полнота»,
роли разведены явно:

| Роль | Кто | Смысл |
|---|---|---|
| **Истина контента** | **Stepik-Python-Grader** | Полная внутренняя база знаний/глоссарий (карточки, тексты, связи) живёт здесь. Это единственный редактируемый источник. |
| **Истина полноты (coverage)** | **официальный Python / stdlib** | Покрытие и «чего не хватает» меряется относительно официального Python и стандартной библиотеки, а не относительно любого стороннего справочника. |
| **Экспорт / витрина** | [Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) | Downstream one-page HTML для быстрого просмотра. Получает контент через односторонний экспорт и **никогда** не является эталоном полноты. |

**Два источника пробелов** (оба питают очередь `GlossaryMissingEntry`):

1. **Practice-driven** — из реальной практики: решения/ошибки (RE/WA),
   которые `MissingConceptDetector` встречает без карточки (журнал J7).
2. **Source-driven** — из будущего сканирования покрытия официального
   Python/stdlib (инвентаризация того, что ещё не описано). Это плановый
   источник (см. follow-up #195–#198 в [claude-handoff.md](claude-handoff.md));
   сетевого скана не предполагается.

Продуктовая роль относительно WEB MVP — каноничен
[web-mvp.md § Глоссарий](web-mvp.md#глоссарий-как-локальный-knowledge-модуль).

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
| `kind` | `function\|exception\|construct\|class` | | Тип пробела (`class` — только source-driven записи, issue #197: классы stdlib без карточки, напр. `pathlib.Path`) |
| `status` | `new\|draft` | | Жизненный цикл до появления карточки |
| `reason` | string | | Почему помечено |
| `snippet` | string | | Фрагмент кода/ошибки, где встретилось |
| `seen_in` | string[] | | Источники (файлы решений) |
| `suggested_tags` | string[] | | Предлагаемые теги |
| `verdict` | `string\|null` | | Вердикт, если пробел найден из ошибки (RE/WA) |
| `first_seen` | string | | ISO-дата первого обнаружения |
| `origin` | `solution\|error\|stdlib_scan` | | Источник обнаружения: practice-driven (`solution`/`error`, ставит `MissingConceptDetector`) или source-driven (`stdlib_scan`, ставит `coverage.missing_entries_from_inventory` — issue #197). По умолчанию `solution` |
| `module` | string | | stdlib-модуль происхождения (заполняется source-driven сканом) |
| `qualname` | string | | Полное квалифицированное имя (заполняется source-driven сканом) |

Старые записи очереди без `origin`/`module`/`qualname` читаются с дефолтами
(`origin="solution"`, пустые строки) — обратная совместимость сохранена.
`from_dict` валидирует `kind`/`status`/`origin` по допустимым значениям (как
`GlossaryCard`), иначе поднимает `GlossaryError` с именем поля.

`append_missing_entries()` дедуплицирует по `concept`, объединяя `seen_in`;
`origin` первой записи не перезаписывается, но пустые `module`/`qualname`
дополняются из новой записи (обогащение practice-пробела source-driven данными).

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

## Инвентарь официального Python/stdlib (`stdlib_inventory`, issue #196)

Source-driven сторона покрытия (см. § Источники истины выше): офлайн-снимок
того, что предлагает сам Python, — без сети и без разбора внешних сайтов.
`build_stdlib_inventory()` собирает через интроспекцию running-интерпретатора:

- **builtins** — публичные функции/классы модуля `builtins`;
- **исключения** — рекурсивный обход иерархии `BaseException` (а не плоский
  список `builtins`, чтобы захватывать и исключения курируемых модулей, напр.
  `json.JSONDecodeError`, если модуль был просканирован);
- **курируемые stdlib-модули** (`NOTABLE_STDLIB_MODULES` — `functools`,
  `itertools`, `collections`, `math`, `re`, `pathlib`, `json` и т.п.) —
  публичные члены (`__all__`, если есть, иначе `dir()` без `_`-префикса).

```python
from stepik_grader.glossary import build_stdlib_inventory

items = build_stdlib_inventory()  # list[StdlibItem], отсортирован по qualname
item = items[0]
item.qualname       # "abc.ABC" | "ValueError" | "functools.reduce" | ...
item.module         # "abc" | "builtins" | "functools" | ...
item.kind           # "function" | "class" | "exception"
item.python_version # "3.14" — из sys.version_info текущего интерпретатора
```

Инвентарь детерминирован (сортировка по `qualname`, без дублей) и не зависит
от внешнего состояния — только от версии Python в текущем окружении.
Модуль — leaf (`stdlib_inventory.py` не тянет `core/*` и не импортируется из
него).

## Coverage-отчёт и missing JSON (`coverage`, issue #197)

Сопоставляет инвентарь (`stdlib_inventory`) с известными терминами локальной
базы (`JsonGlossaryProvider.known_terms()`) и строит:

- **`CoverageReport`** — покрытие по трём категориям (`CATEGORIES`):
  `builtins` (функции/классы `builtins`, кроме исключений), `exceptions`
  (все `kind="exception"` независимо от модуля), `stdlib` (всё остальное —
  члены курируемых модулей). Для каждой категории — `total`/`covered`/
  `missing` (кортеж qualname без карточки) и `ratio` (доля покрытия);
- **список `GlossaryMissingEntry(origin="stdlib_scan")`** — по одной записи
  на каждую непокрытую сущность инвентаря, с `module`/`qualname`, кортеж
  `kind` мапится из `InventoryKind` (`function`/`class`/`exception`).

Сущность считается известной, если её `qualname` (или "хвост" после точки,
напр. `reduce` для `functools.reduce`) есть среди `known` — та же эвристика
подавления, что у `MissingConceptDetector`.

```python
from stepik_grader.glossary import (
    JsonGlossaryProvider, build_stdlib_inventory,
    build_coverage_report, missing_entries_from_inventory,
    append_missing_entries,
)

provider = JsonGlossaryProvider.load("docs/examples/glossary.sample.json")
inventory = build_stdlib_inventory()
known = provider.known_terms()

report = build_coverage_report(inventory, known=known)
report.categories["exceptions"].ratio       # 0.0..1.0
report.categories["stdlib"].missing         # tuple[str, ...] непокрытых qualname

missing = missing_entries_from_inventory(inventory, known=known)
append_missing_entries(".grader_glossary_missing.json", missing)  # идемпотентно
```

Повторный запуск идемпотентен: `append_missing_entries()` дедуплицирует по
`concept`, поэтому повторный скан не плодит дубли в очереди (см. § Очередь
пополнения выше).

### CLI-точка входа (issue #198)

```bash
python -m stepik_grader.glossary.coverage \
    --cards docs/examples/glossary.sample.json \
    --missing-out .grader_glossary_missing.json \
    --modules functools,itertools   # опционально — подмножество модулей
```

Печатает краткую сводку покрытия по категориям (`covered/total`, `%`,
`missing`) через локальный rich-опциональный принтер (свой, не
`core/reporter._console` — модуль остаётся leaf'ом и не тянет `core/*`); при
`--missing-out` дозаписывает пробелы в очередь тем же идемпотентным
`append_missing_entries()`. Без `--cards` покрытие считается относительно
пустой базы (все сущности — «недостающие»). Ошибка чтения базы карточек
(битый JSON/нет файла) завершает запуск понятным сообщением через
`argparse`-`parser.error` (код возврата 2), не трейсбеком.

Пункт интерактивного меню (`grader.py`) не добавлен — модульная точка входа
покрывает acceptance criteria #198 без интеграции в i18n-меню режимов 0-4
(отдельная задача, если понадобится).

## Границы (что НЕ входит)

- **WEB UI и endpoint'ы** (`/api/glossary*`) — реализация в #125/#129.
- **Экспортёр во внешний Glossary-Python** — отдельная задача #126-follow-up.
- **SQLite-хранилище** (#130+) — сейчас JSON-first; API провайдера
  (`GlossaryProvider`-протокол) абстрагирует источник для будущей замены.
