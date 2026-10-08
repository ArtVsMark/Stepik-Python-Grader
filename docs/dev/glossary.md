# Глоссарий в грейдере — выгрузка Glossary-Python, формат и API

> Продуктовая роль раздела — каноничен
> [use/web-interface.md § Глоссарий](../use/web-interface.md#глоссарий-как-локальный-knowledge-модуль);
> здесь — откуда приезжают карточки, их формат, импорт и публичный Python-API
> пакета `stepik_grader.glossary`.

Содержание глоссария ведётся в
[Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) (решение
владельца). Грейдер получает его **выгрузкой**
закреплённого выпуска и показывает студенту: раздел «Глоссарий», подсказки к
ошибкам, панель «Функции в коде», детектор пробелов в решениях.

Компактная карта встроенных исключений (`core/glossary.py`, ~28 записей) **не
заменяется** — она запасной вариант на случай, когда каталога данных нет; связь
через `GlossaryCard.id` = `GlossaryEntry.anchor`.

## Источники истины (роли)

| Роль | Кто | Смысл |
|---|---|---|
| **Истина контента** | [Glossary-Python](https://github.com/ArtVsMark/Glossary-Python) | Карточки ведутся там (`data/cards/<группа>.json`), там же их проверяют: структура, двуязычие, исполнение примеров по блокам, адреса `docs_url`, полнота. Единственный редактируемый источник. |
| **Истина полноты** | **официальный Python / stdlib** | «Чего не хватает» меряется относительно самого Python, а не стороннего справочника. Работа над полнотой — в Glossary-Python (`make completeness`, `whatsnew.json`). |
| **Копия для студента** | **Stepik-Python-Grader** | `src/stepik_grader/glossary/data/*.json` — раскладка выгрузки по группам. Руками не правится: правка потеряется при следующем импорте. |

Почему так: каждая правка карточки здесь прогоняла полный
CI всего грейдера — долго и дорого, — а проверки, нужные глоссарию, живут там.
Две живые копии разъезжаются, а спор о том, чья версия верна, решать некому.

**Что грейдер по-прежнему делает сам** — то, чего глоссарий не знает, потому
что это свойства грейдера:

1. **Пробелы из практики.** `MissingConceptDetector` встречает в решениях и
   ошибках студента имена без карточки и складывает их в очередь
   `GlossaryMissingEntry` (§ Очередь пополнения ниже).
2. **Пробелы относительно stdlib** — `glossary/stdlib_inventory.py` +
   `glossary/coverage.py`, раздел «Недостающее» (§ Инвентарь и § Coverage-отчёт).
3. **Достижимость детектором.** Имя, которое детектор вынимает из кода
   (`os.getcwd`, `exists`), обязано совпасть с search-термом карточки — иначе
   описанное имя считается непокрытым. Держит `tests/test_glossary_detector_reach.py`
   на комплектной базе, вызывая сам детектор, а не его копию.

Найденный здесь пробел становится задачей в Glossary-Python, а не правкой
`glossary/data/`.

## Импорт выгрузки

```
Glossary-Python, выпуск vX.Y.0 ──► delivery.json + delivery.schema.json (приложены к выпуску)
                                        │
            python scripts/import_glossary.py  — четыре проверки, затем раскладка
                                        │
     src/stepik_grader/glossary/data/<группа>.json · _moved.json · _source.json
```

**Закрепление, а не «последнее».** Читается выпуск, записанный в
`glossary/data/_source.json` (`release`, `form`, `cards`, `digest`), а не
подвижный `badges/delivery.json`: смена формы карточки на стороне глоссария
становится здесь явным изменением, а не поломкой, которую замечают по пустому
разделу.

```bash
python scripts/import_glossary.py                    # закреплённый выпуск из сети
python scripts/import_glossary.py --release v1.3.0   # перезакрепить на другой выпуск
python scripts/import_glossary.py --check            # дерево == импорт закреплённого
python scripts/import_glossary.py --drift            # закреплён ли последний выпуск
python scripts/import_glossary.py --file d.json --schema s.json --release v1.2.0
```

**Сторож дрейфа.** Издатель выпускает сам, и отставание закрепления видно
только сравнением. `--drift` спрашивает последний выпуск Glossary-Python и
отвечает одним из исходов: закреплён последний (0); вышел новый, и импорт его
принимает — находка с командой перехода (1); вышел новый, а импорт его
отвергает — находка с причинами (1), то есть доработать импорт надо до
перехода, а не во время; источник недоступен (2) — «не узнали», а не «нового
нет». Зовёт его ночной обход (`scripts/nightly_checks.py`), находка уходит в
его задачу.

**Четыре проверки до записи** — каждая со своим отказом словами, и при отказе
не пишется ничего:

| Проверка | Отказ |
|---|---|
| мажор `form` == `ACCEPTED_FORM` | «контракт глоссария сменился: v6 → v7.0, обновите импорт» |
| выгрузка соответствует приложенной `delivery.schema.json` | путь и нарушение; **незнакомое ключевое слово схемы — тоже отказ** |
| `moved`: новый id есть, старого нет | какое перенаправление битое |
| каждая карточка проходит `from_dict` → `to_dict` побайтно той же | какие поля потерялись бы |

Последняя проверка — главная. На форме 6.0 прежний загрузчик не падал, а молча
превращал `{"ru": …}` в строку `"{'ru': …}"` и массивы блоков примеров — в
строковые представления списков: такая поломка видна только сравнением.

Проверщик схемы — подмножество JSON Schema 2020-12 ровно в объёме схемы
выгрузки (`jsonschema` не зависимость проекта). Отказ на незнакомом ключевом
слове — намеренно: схема, проверенная наполовину, выглядит проверенной.

Минор формы выше принятого законен — это новое необязательное поле, о котором
импорт вправе не знать. Тогда `to_dict` его не вернёт, и импорт откажет по
четвёртой проверке: поле надо завести в `GlossaryCard`. Контракт и журнал формы —
[Glossary-Python `docs/contracts.md` § `delivery.json`](https://github.com/ArtVsMark/Glossary-Python/blob/main/docs/contracts.md).

**Перенаправления.** Карточки, слитые с дублем, перечислены в `_moved.json`
(«старый id → новый»). `JsonGlossaryProvider.get(old)` и `/api/glossary/<old>`
отдают новую карточку — старая ссылка `#/glossary/<id>` из закладки или истории
продолжает работать. Файлы с `_` в начале имени провайдер карточками не читает.

## Формат карточки (`GlossaryCard`)

Форма — та, что у карточки выгрузки (сейчас **6.1**); `from_dict`/`to_dict`
переводят её без потерь. Старые плоские поля (`title` строкой, `version`,
`examples` списком строк) по-прежнему читаются — для чужих баз и фикстур.

| Поле | Тип | Описание |
|---|---|---|
| `id` | string | Уникальный идентификатор (= `GlossaryEntry.anchor`, напр. `recursionerror`) |
| `title` | `{ru, en}` | Заголовок; в модели — `title` / `title_en` |
| `kind` | `exception\|function\|construct\|term` | Тип |
| `summary`, `body` | `{ru, en}` | Пояснение и описание (**простой текст** — UI выводит через `esc()`) |
| `syntax` | string | Сигнатура (`sorted(iterable, *, key=None)`) |
| `status` | `ready` | В выгрузке только готовые |
| `docs_url` | string | Официальная документация `docs.python.org` — единственная внешняя ссылка |
| `added` | string | С какой версии есть; `"<3.0"` — раньше Python 3 (до формы 5.0 — поле `version`) |
| `deprecated`, `removed` | string | С какой версии устарело / в какой удалено; пусто — нет |
| `platforms` | string[] | `["AllOS"]` или подмножество `Linux`, `macOS`, `Windows` |
| `section` | string | Раздел (RU — значение фильтра; EN-подпись — `taxonomy.SECTION_LABELS_EN`) |
| `subcat` | `{ru, en}` | Подкатегория; в модели — `subcat` / `subcat_en` |
| `aliases`, `keywords`, `tags` | string[] | Поиск и группировка |
| `examples` | string[][] | Блоки примеров; блок — массив строк, читается и исполняется отдельно. В модели и API — блок одной строкой |
| `related`, `related_errors` | string[] | id связанных карточек; имена ошибок |

В web-API (`to_api_dict(lang)`) двуязычные поля сплющиваются по `?lang=`, а
`removed_pending` говорит, что удаление запланировано в версии новее
работающего интерпретатора: значок «будет удалено в 3.16» жёлтый, а
свершившееся «удалено в 3.13» — красный.

Поля `url` (ссылка в витрину Glossary-Python) у карточки **нет**: адрес
карточки — её `id` как якорь раздела «Глоссарий» (`#/glossary/<id>`).

**Разделы.** Семейство раздела и его подписи ведёт Glossary-Python: выгрузка
формы 6.1 несёт `navigation` — порядок семейств, подписи семейств и для
каждого раздела его семейство и подписи на обоих языках. Импорт кладёт её в
`glossary/data/_navigation.json`, `taxonomy.SECTION_GROUPS` и
`SECTION_LABELS_EN` только читают этот файл. Новый раздел в выгрузке больше
не требует правки грейдера.

Два отступления от навигации — намеренные:

- подпись раздела модуля строится правилом «Модуль X» → «Module X», а не
  берётся короткой (`abc`) из навигации: подпись идёт и в мету карточки, где
  голое имя модуля читается хуже;
- раздел, которого навигация не знает (сторонняя база через настройку
  `glossary_store` или комплект без `_navigation.json`), не теряется:
  «Модуль X» уходит в «Модули», прочее — в «Прочее».

Расхождение со своей выгрузкой ловят тесты: каждый раздел комплектных карточек
назван в навигации, а подписи семейств в `ui.json` совпадают с подписями
издателя на обоих языках.

## Очередь пополнения (`GlossaryMissingEntry`)

Обнаруженные пробелы складываются в отдельную базу — это «TODO-лист глоссария»,
растущий из практики проверки решений (журнал J7). Хранилище —
SQLite/WAL (`.grader_glossary_missing.db`) поверх общего коннектора `db.py`;
раньше был JSON-файл со списком объектов, и его формат по-прежнему описывает
таблицу полей ниже: она задаёт и схему записи, и структуру legacy-файла.

| Поле | Тип | Обяз. | Описание |
|---|---|:--:|---|
| `concept` | string | ✓ | Недостающая функция/конструкция/исключение (напр. `functools.reduce`) |
| `kind` | `function\|exception\|construct\|class` | | Тип пробела (`class` — только source-driven записи: классы stdlib без карточки, напр. `pathlib.Path`) |
| `status` | `new\|draft` | | Жизненный цикл до появления карточки |
| `reason` | string | | Почему помечено |
| `snippet` | string | | Фрагмент кода/ошибки, где встретилось |
| `seen_in` | string[] | | Источники (файлы решений) |
| `suggested_tags` | string[] | | Предлагаемые теги |
| `verdict` | `string\|null` | | Вердикт, если пробел найден из ошибки (RE/WA) |
| `first_seen` | string | | ISO-дата первого обнаружения |
| `origin` | `solution\|error\|stdlib_scan` | | Источник обнаружения: practice-driven (`solution`/`error`, ставит `MissingConceptDetector`) или source-driven (`stdlib_scan`, ставит `coverage.missing_entries_from_inventory`). По умолчанию `solution` |
| `module` | string | | stdlib-модуль происхождения (заполняется source-driven сканом) |
| `qualname` | string | | Полное квалифицированное имя (заполняется source-driven сканом) |

Старые записи очереди без `origin`/`module`/`qualname` читаются с дефолтами
(`origin="solution"`, пустые строки) — обратная совместимость сохранена.
`from_dict` валидирует `kind`/`status`/`origin` по допустимым значениям (как
`GlossaryCard`), иначе поднимает `GlossaryError` с именем поля.

`append_missing_entries()` дедуплицирует по `concept`, объединяя `seen_in`;
`origin` первой записи не перезаписывается, но пустые `module`/`qualname`
дополняются из новой записи (обогащение practice-пробела source-driven данными).

**Legacy `.json` читается и мигрирует сам.** Переход на SQLite не
требует ручных действий: на первой же записи `_ensure_queue_db()` разбирает два
случая. Если по указанному пути лежит JSON — он читается best-effort, файл
удаляется (`sqlite3` не откроет не-SQLite файл) и пересоздаётся как база с теми
же записями. Если файла нет, но рядом лежит `<stem>.json` — так бывает после
смены дефолта с `.json` на `.db` — соседняя очередь импортируется один раз.
Битый или нечитаемый legacy-файл не роняет запись: старое содержимое теряется,
но очередь создаётся и пробелы копятся дальше. Чтение (`load_missing_queue()`)
понимает оба формата, поэтому старый файл пригоден и до первой записи — но там
битые данные уже поднимают `GlossaryError`, а не проглатываются молча.

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
append_missing_entries(".grader_glossary_missing.db", missing)
```

Ошибки чтения (нет файла / битый JSON / нет обязательного поля) поднимаются как
`GlossaryError` (подкласс `ValueError`) с понятным сообщением — грейдер не
падает, вызывающий код решает, показать ошибку или продолжить с пустой базой
(тот же принцип graceful degradation, что у кэша).

Веб-слой (`src/stepik_grader/web/glossary_adapter.py`) конфигурирует
путь к store и очереди через `GraderConfig` (`config.py`):
`glossary_store` (`str | None`, по умолчанию `None` — тогда `/api/glossary*`
отдаёт fallback-контент из компактного `core/glossary.py`) и
`glossary_missing_queue` (по умолчанию `.grader_glossary_missing.db` — SQLite/WAL,
относительно корня проекта, в `.gitignore` — тот же паттерн,
что выше в этом примере). Оба переопределяются через `[tool.stepik-grader]` в `pyproject.toml`.

## Инвентарь официального Python/stdlib (`stdlib_inventory`)

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
  `set`, `tuple`, `bytes`, `int`, `float` и т.д.) — публичные
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

## Coverage-отчёт и missing JSON (`coverage`)

Сопоставляет инвентарь (`stdlib_inventory`) с известными терминами локальной
базы (`JsonGlossaryProvider.known_terms()`) и строит:

- **`CoverageReport`** — покрытие по категориям (`CATEGORIES`): `builtins`
  (функции/классы `builtins`, кроме исключений), `methods` (методы встроенных
  типов, `kind="method"`), `exceptions` (все `kind="exception"`
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
append_missing_entries(".grader_glossary_missing.db", missing)  # идемпотентно
```

Повторный запуск идемпотентен: `append_missing_entries()` дедуплицирует по
`concept`, поэтому повторный скан не плодит дубли в очереди (см. § Очередь
пополнения выше).

### CLI-точка входа

```bash
python -m stepik_grader.glossary.coverage \
    --cards docs/examples/glossary.sample.json \
    --missing-out .grader_glossary_missing.db \
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
достаточна и без интеграции в i18n-меню режимов 0-5 (отдельная задача, если
понадобится).

## Границы (что НЕ входит)

- **WEB UI и endpoint'ы** (`/api/glossary*`) — не входят в формат хранения (эта тема); реализованы в web-слое (`web/glossary_adapter.py`), справочник эндпоинтов — [api.md](api.md).
- **Экспортёр во внешний Glossary-Python** — отдельная задача.
- **SQLite-хранилище** — сейчас JSON-first; API провайдера
  (`GlossaryProvider`-протокол) абстрагирует источник для будущей замены.
