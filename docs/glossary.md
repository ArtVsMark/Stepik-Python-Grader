# Локальный глоссарий — формат JSON и API (issue #126)

> Foundation полноценного локального knowledge-модуля глоссария WEB MVP
> (эпик #123). Продуктовый дизайн и роль относительно внешнего проекта —
> каноничен в [web-current.md § Глоссарий как локальный knowledge-модуль](web-current.md#глоссарий-как-локальный-knowledge-модуль);
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
2. **Source-driven** — из сканирования покрытия официального Python/stdlib
   (инвентаризация того, что ещё не описано). Реализован (#195–#198):
   `glossary/stdlib_inventory.py` + `glossary/coverage.py` (§ Инвентарь и
   § Coverage-отчёт ниже), CLI `python -m stepik_grader.glossary.coverage`;
   офлайн, сетевого скана нет.

Продуктовая роль относительно WEB MVP — каноничен
[web-current.md § Глоссарий](web-current.md#глоссарий-как-локальный-knowledge-модуль).

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
| `syntax` | string | | Сигнатура/шаблон использования (напр. `sorted(iterable, *, key=None)`) |
| `status` | `new\|draft\|ready\|exported` | | Жизненный цикл (по умолчанию `draft`) |
| `url` | string | | Ссылка во внешний Glossary-Python (цель экспорта) |
| `docs_url` | string | | Ссылка на официальную документацию `docs.python.org`; синоним — `docs` (схема Glossary-Python) |
| `version` | string | | Мин. версия Python, если релевантно (напр. `3.10`); `null` нормализуется в `""` |
| `section` | string | | Раздел глоссария (напр. «Исключения») |
| `subcat` | string | | Подкатегория внутри `section` (для фильтров раздела «Глоссарий») |
| `aliases` | string[] | | Синонимы для поиска |
| `keywords` | string[] | | Ключевые слова для поиска |
| `tags` | string[] | | Теги для группировки/фильтра |
| `examples` | string[] | | Примеры использования |
| `related` | string[] | | `id` связанных карточек |
| `related_errors` | string[] | | Связанные коды/имена ошибок |

Поиск (`search`) идёт по `id`, `title`, `aliases`, `keywords`, `tags`
(подстрока, без учёта регистра). `hint` принимается как алиас `summary`, а
`docs` — как алиас `docs_url` для совместимости с контрактами
`core/glossary.py` и внешнего Glossary-Python.

Пример — [`examples/glossary.sample.json`](examples/glossary.sample.json).

## Ревизия и инварианты карточек (issue #684)

Комплектную базу сверяет `scripts/audit_glossary_cards.py` (отчёт-чеклист) и
одноимённый тест `tests/test_glossary_card_audit.py` (CI-инварианты) по трём осям
аудита #684:

- **Обязательные поля `ready`-карточки** — `summary` (RU), `syntax`, `docs_url`,
  `section`, `subcat`, `tags` (≥1), `examples` (≥1). Прочие поля
  (`body`/`aliases`/`keywords`/`version`/`related`/`url`) осознанно опциональны.
- **Matcher-safety мультифункциональных карточек** — если `title` перечисляет
  несколько вызовов через ` / ` (напр. `os.getcwd() / os.chdir()`), карточке
  нужны `keywords` с чистыми именами каждого вызова. Иначе детектор «Функции в
  коде» (`detector._is_known` матчит concept `os.getcwd` или хвост `getcwd`, а не
  склеенный `title`) и coverage ложно считают вложенное имя непокрытым. Гибрид
  #684: часть бандлов разбита на 1-концепт-карточки, остальным добавлены `keywords`.
- **EN-ratchet** — число карточек без `summary_en` не растёт выше
  зафиксированного порога `MAX_CARDS_WITHOUT_EN`. Волна #684 перевела все 525
  карточек старого импорта из Glossary-Python, поэтому порог сведён к **0**:
  теперь это жёсткий гейт — карточка с RU-`summary` обязана нести и EN, иначе
  `localized()` молча откатит её на русский при `?lang=en` (issue #363).

Запуск отчёта: `python scripts/audit_glossary_cards.py --report`.

## Комплектная база (bundled, issue #326)

В пакете лежит готовая база — `src/stepik_grader/glossary/data/*.json` (число
`ready`-карточек считает `scripts/generate_glossary_badge.py` /
`python -m stepik_grader.glossary.coverage` — не хардкод, issue #398/#535; эпик
наполнения #363 завершён: черновиков-автодрафтов нет, фильтр #436 теперь скрывает
лишь приватные не-`ready` имена; по файлу на цветовую группу `cg`), частично импортированная из внешнего
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python). Она попадает
в wheel через `package-data` и служит **zero-config источником по умолчанию**:
web-адаптер отдаёт её, когда `CONFIG.glossary_store` не задан, а на компактный
`core/glossary.py` (~28 исключений) деградирует лишь при её отсутствии.

Импорт одноразовый (реинициализация — тем же скриптом, идемпотентно; сеть не
нужна, путь к HTML — аргумент):

```bash
python scripts/import_glossary_python.py \
    --html /path/to/Glossary-Python/python_glossary.html \
    --out src/stepik_grader/glossary/data
```

Маппинг схем (`name→title`, `group→section`, `docs→docs_url`, `version` null→`""`,
и т.д.) и kind-эвристика — в `scripts/import_glossary_python.py`. `id` исключений
приводится к нижнему регистру (конвенция анкоров `core/glossary.py` — сохраняет
связь ошибка→карточка). После импорта источник истины — локальная база; внешний
проект отсюда **не** редактируется (CLAUDE.md § Связанный проект), поток контента
односторонний grader → витрина.

### Черновики из официальной документации (issue #328)

То, чего нет в импортированной базе, добирается **офлайн-генерацией** черновиков
из самой stdlib (докстринги/сигнатуры — это и есть официальная документация):

```bash
python scripts/generate_draft_cards.py \
    --base src/stepik_grader/glossary/data \
    --out src/stepik_grader/glossary/data/drafts.json
```

Для каждой сущности инвентаря без карточки создаётся `GlossaryCard(status="draft")`:
`syntax` из `inspect.signature` (или первой строки docstring), `body` — первый
абзац `inspect.getdoc` (EN, под редактуру), `docs_url` по шаблону
`docs.python.org`, `section` зеркалит разделы импортированной базы (черновики
попадают под те же чипы-фильтры). `id` = полный qualname — метод-черновик
`str.split` тем самым закрывает свой coverage-пробел (#327). `summary` пуст —
его (RU-однострочник) вписывает редактор при промоции `draft` → `ready`.
Генерация **идемпотентна** и не перезаписывает существующие (в т.ч.
отредактированные) карточки. В web черновики **скрыты из выдачи по умолчанию**
(issue #436): дефолтный вид показывает только `ready`, черновики доступны
явным выбором «Черновики»/«Все статусы» в селекте статуса; приватно-именованные
автодрафты (`os._exit`, `_pickle.X` — одиночное `_` в сегменте, но не дандеры)
скрыты из выдачи ученика всегда. Детектор недостающих терминов и очередь
пополнения по-прежнему работают по ПОЛНОЙ базе.

**Итог эпика #363 (кампания завершена).** Все 832 автодрафта разобраны волнами
В1–В6 (строки/числа/list/dict/set/tuple → исключения → учебные модули →
операторы/типизация → `os`/`os.path` целиком → `bytes`/`bytearray`): stdlib
доведён до `ready`, покрытие офлайн-инвентаря ~100%, `data/drafts.json` пуст.
Оставшиеся не-stdlib имена (сторонний `rich`, приватные C-ускорители
`_pickle`/`_lzma`, приватные подмодули `pathlib._abc`, собственный
`stepik_grader`) по инварианту истины глоссария в инвентарь **не берутся**:
`_is_official_stdlib_exception` в `stdlib_inventory.py` фильтрует обход
`BaseException` по `sys.stdlib_module_names` + приватным модулям/классам
(эвристика по `__all__` отвергнута — она выбросила бы публичные
`shutil.ReadError`/`RegistryError`). Так покрытие и генерация черновиков считаются
только относительно официального Python/stdlib (см. § Источники истины).

### Полуавтоматический конвейер черновиков (issue #438)

Следующий шаг доводки `draft` → `ready` — `scripts/glossary_draft_pipeline.py`:
собирает карточку по шаблону волны **B1** (RU+EN summary + примеры), **проверяет
примеры прогоном** (практика #371: «# → результат» сверяется с фактическим
выводом) и печатает diff против базы для ручного ревью. Ничего не мержится
автоматически; ready-база не трогается.

**Без реальной модели.** Генерация текста/примеров — за сменным провайдером
`DraftProvider`; дефолт `OfflineDraftProvider` работает офлайн (EN-summary из
docstring, каркас B1). Реальный BYOK-LLM (облако/ollama на `requests`, эпик AI
#434/#435) реализует тот же протокол и подключается **opt-in** — не входит в
dev-инструмент и не добавляет runtime-зависимостей (чистый stdlib). Ценность, не
зависящая от модели, — движок валидации примеров и diff-ревью, работает сейчас.

```bash
# аудит примеров существующих карточек (прогон + сверка «# → …»):
python scripts/glossary_draft_pipeline.py check --base src/stepik_grader/glossary/data
# предложить B1-черновик qualname (контент из файла имитирует выход модели),
# проверить примеры, показать review-diff; запись — только валидных, в отдельный
# draft-файл (никогда в ready-базу, никогда автомержем):
python scripts/glossary_draft_pipeline.py propose --qualname str.rjust \
    --content-file draft.json --write review-drafts.json
```

Классы вердикта примера: `ok` (все `# → …` сошлись), `mismatch` (разошлось),
`error` (runtime-ошибка прогона), `unverifiable` (нечего/нельзя сверить —
нет маркеров, или блок хранится построчно без отступов, или число ожиданий ≠
числу строк вывода). Битые примеры блокируют запись черновика.

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

Веб-слой (issue #125, `src/stepik_grader/web/glossary_adapter.py`) конфигурирует
путь к store и очереди через `GraderConfig` (`config.py`):
`glossary_store` (`str | None`, по умолчанию `None` — тогда `/api/glossary*`
отдаёт fallback-контент из компактного `core/glossary.py`) и
`glossary_missing_queue` (по умолчанию `.grader_glossary_missing.json`,
относительно корня проекта, в `.gitignore` — тот же паттерн, что выше в этом
примере). Оба переопределяются через `[tool.stepik-grader]` в `pyproject.toml`.

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
  публичные члены (`__all__`, если есть, иначе `dir()` без `_`-префикса);
- **методы встроенных типов** (`NOTABLE_BUILTIN_TYPES` — `str`, `list`, `dict`,
  `set`, `tuple`, `bytes`, `int`, `float` и т.д., issue #327) — публичные
  вызываемые методы (`str.split`, `dict.get`, `list.append`; data-дескрипторы
  вроде `int.numerator` отбрасываются). Это самый частый у новичков пласт,
  которого builtins-сканер не видит — он собирает только сами классы.

```python
from stepik_grader.glossary import build_stdlib_inventory

items = build_stdlib_inventory()  # list[StdlibItem], отсортирован по qualname
item = items[0]
item.qualname       # "abc.ABC" | "ValueError" | "functools.reduce" | "str.split" | ...
item.module         # "abc" | "builtins" | "functools" | ...
item.kind           # "function" | "class" | "exception" | "method"
item.python_version # "3.14" — из sys.version_info текущего интерпретатора
```

Инвентарь детерминирован (сортировка по `qualname`, без дублей) и не зависит
от внешнего состояния — только от версии Python в текущем окружении.
Модуль — leaf (`stdlib_inventory.py` не тянет `core/*` и не импортируется из
него).

## Coverage-отчёт и missing JSON (`coverage`, issue #197)

Сопоставляет инвентарь (`stdlib_inventory`) с известными терминами локальной
базы (`JsonGlossaryProvider.known_terms()`) и строит:

- **`CoverageReport`** — покрытие по категориям (`CATEGORIES`): `builtins`
  (функции/классы `builtins`, кроме исключений), `methods` (методы встроенных
  типов, `kind="method"` — issue #327), `exceptions` (все `kind="exception"`
  независимо от модуля), `stdlib` (всё остальное — члены курируемых модулей).
  Для каждой категории — `total`/`covered`/`missing` (кортеж qualname без
  карточки) и `ratio` (доля покрытия). Метод считается покрытым только при
  совпадении **полного** qualname (`str.split`), без «хвостовой» эвристики —
  иначе одна карточка `split` ложно закрыла бы методы всех типов;
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
покрывает acceptance criteria #198 без интеграции в i18n-меню режимов 0-5
(отдельная задача, если понадобится).

## Границы (что НЕ входит)

- **WEB UI и endpoint'ы** (`/api/glossary*`) — не входят в формат хранения (эта тема); реализованы в web-слое (`web/glossary_adapter.py`, #125/#129), справочник эндпоинтов — [api.md](api.md).
- **Экспортёр во внешний Glossary-Python** — отдельная задача #126-follow-up.
- **SQLite-хранилище** (#130+) — сейчас JSON-first; API провайдера
  (`GlossaryProvider`-протокол) абстрагирует источник для будущей замены.
