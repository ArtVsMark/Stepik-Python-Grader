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
`lint_violations` — на этой истории раздел «Подучить» считает частоту падений
и затухание карточек. Опции: `--history`/`record_history` (см.
[configuration.md](configuration.md)). Таксономия падений и алгоритм затухания
(`failure_kind`, статусы карточек) — issue #347, будут описаны здесь же по мере
реализации.
