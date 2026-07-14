# Правила PEP 8 и учебные инсайты

Канонический справочник по разделам **«Правила/PEP»** и **«Подучить»** (эпик
#342, дизайн — [audit-2026-07.md § 9](audit-2026-07.md)). Здесь — форматы
данных и контракты; продуктовый замысел и wireframes — в аудите.

Строится по образцу глоссария (модель + JSON-провайдер + bundled-данные), но
**без** обобщения в единый «RefCard»-слой — у правил своя специфика (severity,
PEP-ссылка, bad/good примеры), преждевременная абстракция дороже (решение § 11
аудита, правило трёх).

---

## Пакет `rules/` — карточки правил (issue #345)

Локальная база карточек правил стиля, на которые ссылаются найденные в коде
нарушения. Каждая карточка учит **духу** PEP 8 — объясняет «почему», а не
только «что нельзя».

- `rules/models.py` — модель `RuleCard` (Domain, leaf).
- `rules/json_provider.py` — `JsonRulesProvider` (`from_file`/`from_directory`/
  `load`), `RulesError`, `bundled_rules()` (кеш по mtime через
  `core/mtime_cache`).
- `rules/data/pep8_ru.json` — комплектная база (≥30 частых кодов
  pycodestyle/pyflakes: E1xx–E7xx, W2xx/W3xx, W605, F401/F811/F821/F841),
  попадает в wheel через package-data.

### Формат карточки `RuleCard`

| Поле | Тип | Обяз. | Описание |
|---|---|:--:|---|
| `id` | `str` | ✓ | Код правила: `E501`, `F401` |
| `title` | `str` | ✓ | Краткое название (RU) |
| `summary` | `str` | | Однострочник: что нарушено (RU) |
| `body` | `str` | | Markdown: «почему» + «как исправить» |
| `pep_url` | `str` | | Ссылка на раздел peps.python.org |
| `docs_url` | `str` | | Доп. ссылка (ruff/pycodestyle) |
| `severity` | `"convention"\|"warning"\|"error"` | | Тяжесть (на вердикт НЕ влияет) |
| `status` | `"draft"\|"ready"` | | Жизненный цикл карточки |
| `tags` | `list[str]` | | Метки для поиска/фильтров |
| `example_bad` | `str` | | Фрагмент-нарушение |
| `example_good` | `str` | | Исправленный фрагмент |

`from_dict`/`to_dict` симметричны, с валидацией обязательных полей и
`severity`/`status` (как `GlossaryCard`). JSON-корень — список карточек или
объект `{"rules": [...]}`.

---

## `core/lint.py` — PEP-проверка через ruff (issue #346)

Тонкая обёртка над `ruff check --output-format json --select E,W,F`: возвращает
`list[Violation]` (`rule_code`, `line_no`, `message`, `column`). Свой
AST/tokenize-чекер не пишем — не дублируем pycodestyle (§ 11 аудита).

**Границы (дизайн § 5/§ 9.4):**

- **Opt-in extra** `[lint]` (`pip install stepik-python-grader[lint]`) — ruff
  НЕ runtime-зависимость. Без него `run_lint` бросает `LintUnavailable`, а
  UI/CLI скрывают блок «Стиль» с подсказкой про установку.
- **Не влияет на вердикт** проверки — информационный канал.
- **Best-effort**: ruff упал / вернул мусор / аварийный код возврата → пустой
  список (принцип `cache`/`stats`); `code=null` (синтаксические ошибки) —
  пропускается (нет карточки правила, RE и так виден в проверке).

`ruff_available()` — дешёвая проба для решения, показывать ли блок.

Опциональное поле `lint` в контракте результата — см.
[result-contract.md](result-contract.md).

---

## История прогонов (issue #344, фундамент)

`core/history.py` (SQLite `.grader_history.db`) хранит `runs`/`case_results`/
`lint_violations` — фундамент, на котором считаются карточки «Подучить». Опции:
`--history`/`record_history` (см. [configuration.md](configuration.md)).

---

## Таксономия падений и затухание (`core/insights.py`, issue #347)

**Ключ карточки** «Подучить» — `failure_kind` (падение) или `rule_code` (lint).
`failure_kind` — чистая классификация исхода кейса (заполняется при записи в
историю):

| kind | Когда |
|---|---|
| `timeout` | TLE |
| `runtime-error:<Класс>` | RE (имя исключения из stderr тем же `lookup_from_error`; `runtime-error` без класса) |
| `output-format` | WA, где вывод совпал с точностью до пробелов/пустых строк/регистра |
| `wrong-answer` | прочие WA |
| `slow` | SLOWER/MUCH_SLOWER режимов 3/4 |

**Статус карточки** — чистая функция `classify_status(flags)` от истории её
ключа в последних `N` прогонах (`True` = встречался), **по номерам прогонов, а
не по календарю** (тестируемо без часов/freezegun):

| status | Правило |
|---|---|
| `active` | ≥`T` попаданий в окне и последний прогон «грязный» |
| `fading` | был активен, 1..`K`-1 последних прогонов чистые |
| `archived` | ≥`K` чистых прогонов подряд → исчезает из «Подучить», остаётся в «архиве побед» |
| `watch` | появлялся, но ниже порога активности (первое появление / «мигающая» ошибка) |

Пороги `N`/`T`/`K` (default 10/2/3) настраиваются в `[tool.stepik-grader]`
(`insights_window_n`/`insights_active_threshold_t`/`insights_clean_streak_k`).
`learning_cards(db_path)` агрегирует карточки из истории на лету — **без
хранимого состояния**; `archived` по умолчанию скрыты (`include_archived=True`
для «архива побед»), а `runtime-error:<Класс>` несёт `glossary_id` — ссылку на
карточку исключения в глоссарии.
