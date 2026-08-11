# Полный мультиролевой аудит Stepik-Python-Grader — 2026-08-10

**Версия:** v1.10.0 · **База:** `f67d6cc` (main) · **Метод:** 70 ролевых, зонных и прогонных срезов +
489 адверсариальных верификаций + 5 критиков полноты + разбор стороннего аудита ·
**Волн:** 24 запуска Workflow, ровно по 5 агентов, 120 агентов суммарно

> **Стратегический контекст.** Владелец намерен выжать максимум из локальной версии, затем перейти на
> серверную. Поэтому находки разделены по горизонтам: `bug` — дефект текущего поведения, `quick` — один PR,
> `mid` — несколько PR или дизайн, `long` — направление. Серверный пласт отведён в roadmap-issue **#59**.

---

## 0. Как читать документ

- **Каждая находка** имеет `ID`, привязку к `file:line` и прошла **адверсариальную верификацию** отдельным
  агентом-скептиком с установкой «опровергай по умолчанию».
- **Колонка «Итог»** — severity, назначенный **верификатором**, а не автором находки.
- **Колонка «✓»**: ✅ CONFIRMED (факт и вывод подтверждены), ◐ PARTIAL (факт верен, следствие или охват
  преувеличены). Отклонённые (✖ REFUTED) и схлопнутые (⧉ DUPLICATE) вынесены в § 6 и в работу не идут.
- **Состояние всех находок ниже — «открыта».** По мере закрытия сюда проставляется номер PR; когда закрыты
  или отклонены все, документ целиком переезжает в [`../archive/`](../archive/README.md) (правило
  [CLAUDE.md § Открытая работа](../../CLAUDE.md)).

---

## 1. Резюме

Проект инженерно зрелый: `pytest` зелёный (3071 тест, 65 пропущено), покрытие 90.21%, `ruff` и `mypy` чисты,
прошлый аудит (2026-07-30, 192 находки) разобран полностью — все 11 подэпиков **#771** закрыты. Поэтому
находки этого прогона сконцентрированы не в «грязном коде», а в **пяти системных местах**.

1. **Грейдер выносит неверный вердикт — и это снова нашёл тот, кто запускал продукт.** Все восемь
   подтверждённых `high` — про это. Формат 2 молча выбрасывает пару `input_02/expected_02` с ведущим нулём
   (`test_loader.py:204`), и если выброшенный кейс был единственным проверяющим, **неверное решение получает
   OK**. Формат 3 принимает stdin-данные вида `x = 5` за function-режим (`test_loader.py:187`) → **ложный RE
   на верном решении**. Потерянный `expected_N.txt` не ошибка, а тишина: отчёт показывает зелёное «OK N/N» на
   неполном наборе (`test_loader.py:205`). Пять прогонных срезов из семидесяти дали 8 `high`; шестьдесят пять
   читающих — ни одного нового. Вывод повторяется третий аудит подряд.

2. **Молчание вместо ошибки — сквозной шаблон.** Выброшенный тест-кейс, обрезанный по лимиту вывод,
   рассогласованные блоки формата 3, файлы тестов в cp1251, BOM в `input.txt` — всё это либо тишина, либо
   трейсбек, но не внятный отказ. Отдельная грань: **код возврата всегда 0** — четыре независимых среза
   назвали `cli/__init__.py:448`, где `FAIL`, `RE` и `NO TESTS` неотличимы от успеха для любого CI-скрипта.

3. **Пользователь не выбирает, как запустить, и не может передумать.** Лаунчер даёт три параметра — порт,
   папку, изоляцию, — молча включая историю прогонов. `--sandbox` **без `--mode` игнорируется молча** (три
   независимых среза, `cli/__init__.py:621`). Раздел «Настройки» в вебе **не содержит ни одного
   интерактивного элемента**, а согласие на AI-подсказку нельзя отозвать ниоткуда, кроме CLI. Причина
   системная: `CONFIG` связывается на импорте в двадцати модулях (`LNCH-3-03`), поэтому ни один параметр
   старта не перечитывается на ходу.

4. **Учебный контент местами учит неверному.** 209 из 1349 карточек глоссария содержат примеры, которые
   **не компилируются** — у многострочных блоков срезан отступ; карточка `any` заявляет вывод `False` там,
   где Python печатает `True`. Гейт `audit_glossary_cards.py` проверяет наличие полей, а не работоспособность
   примера, поэтому обе проблемы для CI невидимы.

5. **Сейф-нет не ловит то, ради чего заведён.** Три property-теста нормализации остаются зелёными, если
   заменить проверяемую функцию на тождественную. Формат 2 покрыт одной идеальной парой файлов, поэтому ни
   потерянный `expected_N`, ни ведущий ноль тестами не ловятся — их нашёл прогон.

**Отдельно о методе.** Критики полноты вынесли аудиту приговор: из 406 файлов репозитория в находках был
упомянут 141, а след реального прогона несли 44 находки из 424. По их указаниям дозапущены две волны — по
слепым зонам и по стратегии, — и они дали **4 новых `high`** и 68 среднесрочных целей вместо прежних двух.
Полнота аудита снова оказалась функцией не числа ролей, а **разнообразия способов проверки**.

---

## 2. Топ рисков

Группы, а не отдельные находки: приоритет ставится по вкладу в риск, а не по механическому severity (см. § 4).

| # | Риск | Находки | Кого бьёт |
|---|---|---|---|
| 1 | **Неверный вердикт грейдера**: верное решение получает WA/RE, неверное получает OK | `RUN-1-01` `RUN-2-01` `RUN-2-02` `QA-1-01` `STR-5-01` `DEV-2-01` `ADD-2-01` `RUN-1-02` `PY-1-01` | Студента — он правит корректный код по ложному диагнозу либо отправляет на Stepik заведомо неверное решение |
| 2 | **Молчание вместо ошибки**: выброшенный кейс, обрезанный вывод, cp1251, BOM, рассогласование блоков | `RUN-1-03` `RUN-2-03` `RUN-2-04` `RUN-2-05` `RUN-2-07` `PY-1-04` `PY-1-05` | Доверие к отчёту: «всё прошло» перестаёт означать «всё проверено» |
| 3 | **Код возврата всегда 0**: FAIL, RE и NO TESTS неотличимы от успеха | `DEV-1-01` `OPS-1-01` `RUN-1-04` `RUN-2-06` | Любую автоматизацию поверх грейдера: pre-commit, CI студента, скрипт преподавателя |
| 4 | **Изоляция не применяется молча**: `--sandbox` без `--mode` игнорируется, лаунчер не даёт выбора | `SEC-2-01` `DEV-1-02` `LNCH-2-01` `LNCH-1-02` | Того, кто явно попросил изоляцию и уверен, что получил её |
| 5 | **Учебный контент учит неверному**: 209 карточек с несобирающимися примерами, ошибочная семантика | `ADD-3-01` `ADD-3-02` `ADD-3-03` `ADD-3-04` `ADD-3-05` `ADD-3-07` | Студента напрямую — это единственная зона, где продукт не проверяет, а **учит** |
| 6 | **Согласие на AI не удержано**: web-путь обходит гейт, отозвать нельзя, полный код уходит провайдеру | `SEC-3-01` `LNCH-4-02` `SET-2-01` `MET-1-06` `SET-1-04` | Приватность кода студента и обещание, записанное в докстринге |
| 7 | **Настройки без управления**: раздел без контролов, ничего не меняется без перезапуска | `SET-1-02` `SET-4-01` `LNCH-3-03` `LNCH-4-03` `SET-3-03` | Пользователя без консоли: интерфейс показывает состояние и не даёт его изменить |
| 8 | **Тесты зелены независимо от поведения**: тождественная подмена не ломает property-тесты | `ADD-5-01` `ADD-5-02` `ADD-5-03` `ADD-5-04` `QA-2-01` `QA-2-02` | Всю остальную защиту: регресс вердикта пройдёт мимо CI |
| 9 | **Данные пользователя портятся или утекают**: отравление `secrets.json`, коммит-сообщение в публичный issue | `ADD-2-02` `ADD-4-01` `ADD-4-03` `ADD-4-04` `OPS-1-08` | Авторизацию и приватность: потеря токена, чужой текст в публичном обращении |
| 10 | **Релиз и цепочка поставок**: релиз может выйти без wheel, guard'ы зелены при пустом входе | `REL-1-01` `AUD-2-01` `AUD-2-02` `AUD-2-03` `REL-3-05` | Пользователей пакета и доверие ко всем остальным проверкам |

---

## 3. Статистика

| Показатель | Значение |
|---|---|
| Находок всего | **489** |
| Отклонено верификаторами | 6 REFUTED + 6 DUPLICATE = 12 |
| В работу | **477** — 8 high · 83 medium · 386 low (шкала верификаторов, см. § 4) |
| Подтверждено полностью (CONFIRMED) | 306 |
| Подтверждено частично (PARTIAL) | 171 |
| Дефектов текущего поведения (`bug`) | 32 |
| Быстрых целей (`quick`) | 373 |
| Среднесрочных (`mid`) | 68 |
| Долгосрочных (`long`) | 4 |
| Срезов | 70 ролевых, зонных и прогонных |
| Верификаций | 489 (100% находок, ни одной без вердикта) |
| Severity скорректирован верификатором | 238 находок |
| Волн Workflow | 24, ровно по 5 агентов; потеряно 2 агента из 120 (лимит сессии), оба добраны дельтой |

**Совпадения между срезами:** 35 находок разных срезов указывают на одну и ту же строку кода, ещё 74 группы
лежат в пределах 12 строк друг от друга. Это не дубли, а **независимые подтверждения**: `cli/__init__.py:621`
назвали три среза, `cli/__init__.py:448` — четыре.

---

## 4. Методологическая заметка: две шкалы severity

**Верификаторы скорректировали severity у 238 находок из 489 — почти половина, и почти всё вниз.**
Это не случайность, а следствие промпта: скептику предписано «опровергай по умолчанию», плюс контекст
«локальный инструмент, один доверенный пользователь». Шкала скептика систематически ниже шкалы автора находки.

Обе оценки добросовестны — они отвечают на разные вопросы: скептик оценивает *калибр дефекта в изоляции*,
критик — *вклад в риск*. Поэтому § 2 составлен по риск-группам, а не сортировкой по severity, ровно как
предписывает [`docs/agent/multiagent.md`](../agent/multiagent.md). `low` у подтверждённой находки **не
означает «неважно»**.

---

## 5. Находки по зонам


### Ядро проверки: вердикт, раннер, форматы тест-кейсов

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| DEV-2-01 | `src/stepik_grader/core/wrapper_builder.py:28` | Обёртка function-mode связывает аргумент по имени со своим же импортом (date/time) — ложный WA | high | ✅ |
| QA-1-01 | `src/stepik_grader/core/test_loader.py:205` | Нет теста на потерянный expected_N.txt: кейс молча выпадает, грейдер даёт ложно-зелёное «всё прошло» | high | ✅ |
| DEV-2-02 | `src/stepik_grader/core/tracer.py:218` | Трейс JSON растёт как O(шаги × состояние) и упирается в max_output_bytes → «no trace produced» | medium | ✅ |
| DEV-2-06 | `src/stepik_grader/core/microbench_runner.py:257` | Bench-скрипт микробенча пишется в общий /tmp — sys.path[0] дочернего процесса подменяем | medium | ✅ |
| PY-1-01 | `src/stepik_grader/core/grader_core.py:443` | Хвостовые нули float дают ложный AC: «12.3» принимается вместо «12.30» | medium | ✅ |
| PY-1-04 | `src/stepik_grader/core/test_loader.py:173` | Предупреждение о рассинхроне числа блоков не доходит до пользователя — ложно-зелёный прогон | medium | ◐ |
| PY-2-01 | `src/stepik_grader/core/sandbox/_linux.py:110` | CPU-квота песочницы не связана с spec.timeout: прогон длиннее 10 с CPU режется досрочно | medium | ✅ |
| PY-3-01 | `src/stepik_grader/core/cache.py:127` | Ключ кэша не учитывает условия исполнения: --sandbox/timeout_seconds не инвалидируют вердикт | medium | ✅ |
| PY-3-02 | `src/stepik_grader/core/cache.py:175` | cache.save() роняет грейдинг OSError вопреки инварианту «кэш никогда не роняет грейдер» | medium | ✅ |
| QA-1-04 | `src/stepik_grader/core/grader_core.py:437` | Обрезка вывода по max_output_bytes не проверена до вердикта: пользователь получает WA без причины | medium | ✅ |
| ARCH-1-01 | `src/stepik_grader/core/grader_core.py:283` | grader_core берёт max_memory_mb/max_output_bytes из снимка CONFIG, а timeout — из get_config() | low | ◐ |
| ARCH-1-02 | `src/stepik_grader/config.py:9` | Импорт core/grader_core.py читает pyproject.toml — ленивая загрузка конфига не работает | low | ◐ |
| ARCH-1-03 | `tests/test_import_dag.py:85` | DAG-guard слеп к ленивым импортам: 42 ребра вне графа, цикл cli ↔ cli.commands реален | low | ◐ |
| ARCH-1-04 | `docs/dev/architecture.md:57` | architecture.md помещает run_dir в core/sandbox/, реальный модуль — core/run_dir.py | low | ✅ |
| ARCH-1-05 | `src/stepik_grader/config.py:41` | config.py — god-object: 30 полей восьми подсистем в одном frozen dataclass, 21 импортёр | low | ◐ |
| ARCH-1-06 | `src/stepik_grader/core/result.py:154` | TestResult.to_dict() мёртв и лоссов: теряет exit_code — обязательное поле CaseResult | low | ◐ |
| ARCH-1-07 | `src/stepik_grader/core/grader_core.py:540` | run_tests — 9 параметров без объекта опций: каждая новая опция шьётся через пять слоёв | low | ◐ |
| DEV-2-03 | `src/stepik_grader/core/microbench_runner.py:297` | Микробенч: ребёнок упал без stderr → error='' → решение показано как успешное | low | ◐ |
| DEV-2-04 | `src/stepik_grader/core/microbench_runner.py:365` | apply_relative_ranking при нулевой медиане делает все решения SIMILAR | low | ◐ |
| DEV-2-05 | `src/stepik_grader/core/lint.py:147` | run_lint не изолирует ruff — набор замечаний зависит от окружающего pyproject.toml | low | ✅ |
| DEV-2-07 | `src/stepik_grader/core/runner.py:738` | RunOutcome при TLE отдаёт returncode=0 и фиктивный elapsed=spec.timeout | low | ◐ |
| PY-1-02 | `src/stepik_grader/core/normalizers.py:74` | Переполнение float: два разных огромных числа схлопываются в 'inf' → AC неверному решению | low | ✅ |
| PY-1-03 | `src/stepik_grader/core/parsers.py:64` | Номер в маркере # TEST_N: игнорируется — блоки input/output спариваются позиционно | low | ◐ |
| PY-1-05 | `src/stepik_grader/core/test_loader.py:215` | Форматы 1 и 2 в одном каталоге дают дубли кейсов с одинаковым index | low | ✅ |
| PY-1-06 | `src/stepik_grader/core/test_loader.py:268` | resolve_test_dir поднимается в parent.parent — решение можно проверить тестами чужой задачи | low | ◐ |
| PY-1-07 | `src/stepik_grader/core/test_loader.py:39` | ENCODING вморожен на импорте вопреки правилу «конфиг читается в момент вызова» | low | ◐ |
| PY-2-02 | `src/stepik_grader/core/sandbox/_posix_common.py:313` | sandbox_violation="cpu" недостижим на Linux: SIGXCPU получает внук, проверяется код возврата bwrap | low | ✅ |
| PY-2-03 | `src/stepik_grader/core/sandbox/_windows.py:385` | Windows: при CPU-нарушении Job Object не терминируется — внуки доживают до CloseHandle | low | ✅ |
| PY-2-04 | `src/stepik_grader/core/sandbox/_windows.py:270` | Windows: утечка handle Job Object при сбое AssignProcessToJobObject/NtResumeProcess | low | ✅ |
| PY-2-05 | `src/stepik_grader/core/sandbox/_posix_common.py:209` | POSIX: на штатном завершении дерево не добивается — на macOS внук переживает прогон | low | ✅ |
| PY-2-06 | `src/stepik_grader/core/sandbox/_linux.py:182` | max_memory_mb=None («без лимита» по контракту RunSpec) в песочнице становится kill на 1024 МБ | low | ✅ |
| PY-2-07 | `src/stepik_grader/core/sandbox/_windows.py:209` | Windows: CPU-поллер считает время только прямого потомка, память — всего дерева | low | ✅ |
| PY-3-03 | `src/stepik_grader/core/stats.py:57` | Журнал .grader_stats.jsonl привязан к cwd — тот же дефект, что чинили для истории в #818 | low | ✅ |
| PY-3-04 | `src/stepik_grader/core/mode_detector.py:189` | load_json_file: UnicodeDecodeError и ValueError проходят мимо except (JSONDecodeError, OSError) | low | ◐ |
| PY-3-05 | `src/stepik_grader/atomic_io.py:55` | Temp-файлы атомарной записи утекают при любом не-OSError прерывании (Ctrl+C) | low | ✅ |
| PY-3-06 | `src/stepik_grader/core/stats.py:188` | read_summary игнорирует поле схемы v — записи чужой версии молча смешиваются в сводку | low | ◐ |
| QA-1-02 | `src/stepik_grader/core/test_loader.py:214` | Смешение форматов 1 и 2 в одной папке даёт дубли index — ни теста, ни предупреждения | low | ✅ |
| QA-1-03 | `tests/test_runner.py:60` | Лимит памяти проверяется только против поддельного модуля resource — реального прогона нет | low | ◐ |
| QA-1-05 | `src/stepik_grader/core/result.py:154` | Round-trip тест TestResult зелёный по построению: to_dict() теряет exit_code, а фикстура его не содержит | low | ◐ |
| QA-1-06 | `tests/test_result.py:149` | Тесты передают str туда, где сигнатура требует Path — фиксируют терпимость, которой не должно быть | low | ✅ |
| QA-1-07 | `src/stepik_grader/core/runner.py:625` | Каждый прогон решения оставляет ResourceWarning о незакрытых пайпах; набор тестов это не ловит | low | ✅ |

### Живой прогон продукта (запуск, а не чтение)

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| RUN-1-01 | `src/stepik_grader/core/test_loader.py:204` | Формат 2: пара с ведущим нулём (input_02/expected_02) молча выбрасывается — решение получает OK | high | ✅ |
| RUN-2-01 | `src/stepik_grader/core/test_loader.py:187` | Формат 3: stdin-данные вида «x = 5» или «sin(30)» уезжают в function-режим → ложный RE на верном решении | high | ✅ |
| RUN-2-02 | `src/stepik_grader/core/mode_detector.py:211` | Формат 3 function-mode: вспомогательная функция объявлена раньше целевой → ложный RE «name ... is not defined» | high | ✅ |
| RUN-1-02 | `src/stepik_grader/core/grader_core.py:466` | Вывод свыше max_output_bytes режется молча: верное решение получает WA без упоминания обрезки | medium | ✅ |
| RUN-1-03 | `src/stepik_grader/core/test_loader.py:218` | Формат 1: файл N без N.clue молча исчезает — OK на усечённом наборе тестов | medium | ✅ |
| RUN-1-04 | `src/stepik_grader/cli/__init__.py:448` | Код возврата всегда 0: FAIL и NO TESTS неотличимы от OK для CI | medium | ✅ |
| RUN-2-03 | `src/stepik_grader/core/test_loader.py:149` | Файлы тестов в cp1251 роняют прогон трейсбеком UnicodeDecodeError; в режиме 2 гибнет вся пачка | medium | ✅ |
| RUN-2-04 | `src/stepik_grader/core/parsers.py:64` | BOM в input.txt/output.txt формата 3 → ноль тест-кейсов, статус NO TESTS и rc=0 (молчаливый отказ) | medium | ✅ |
| RUN-2-05 | `src/stepik_grader/core/test_loader.py:173` | Рассогласование блоков формата 3: лишние кейсы отброшены, вердикт OK, в JSON следа нет | medium | ✅ |
| RUN-2-06 | `src/stepik_grader/cli/__init__.py:448` | Код возврата всегда 0 — FAIL, RE и NO TESTS неотличимы от успеха для CI-скрипта | medium | ✅ |
| RUN-2-07 | `src/stepik_grader/core/test_loader.py:198` | Форматы 1 и 2 в одной папке молча объединяются: два кейса с индексом 1 и разными ожиданиями | medium | ✅ |
| RUN-2-08 | `src/stepik_grader/core/test_loader.py:268` | Решение в подпапке без своих тестов молча грейдится тестами родительской папки → чужой WA | medium | ✅ |
| RUN-3-01 | `src/stepik_grader/web/viewmodels.py:436` | POST /api/save-solution перезаписывает файлы тест-кейсов внутри root → верное решение получает RE | medium | ✅ |
| RUN-3-03 | `src/stepik_grader/web/runs.py:372` | Две OAuth-джобы занимают весь пул воркеров: все проверки висят в queued, cancel не помогает | medium | ✅ |
| RUN-4-01 | `src/stepik_grader/core/runner.py:672` | Верное решение получает WA с пустым Actual, если оставило дочерний процесс, держащий stdout | medium | ✅ |
| RUN-4-02 | `src/stepik_grader/core/reporter.py:615` | Одна длинная строка вывода вешает CLI на часы: строки diff идут в rich без обрезки по длине | medium | ✅ |
| RUN-4-05 | `src/stepik_grader/cli/__init__.py:435` | Код возврата всегда 0 — CI не отличит провал тестов и опечатку в пути от успеха | medium | ✅ |
| RUN-1-05 | `src/stepik_grader/core/test_loader.py:200` | Форматы 1 и 2 в одной tests/ грузятся оба: дубли индексов, два «Test 1», нет предупреждения | low | ✅ |
| RUN-1-06 | `src/stepik_grader/core/wrapper_builder.py:143` | function-стиль (N.type): вызов на верхнем уровне даёт RE с трейсбеком внутрь wrapper'а | low | ◐ |
| RUN-3-02 | `src/stepik_grader/web/viewmodels.py:423` | Гонка в save-solution: 5 ответов ok:true, 3 файла на диске — код двух сохранений потерян | low | ✅ |
| RUN-3-04 | `src/stepik_grader/web/http_guards.py:63` | NUL-байт в path роняет обработчик: ValueError и разрыв соединения без HTTP-ответа | low | ✅ |
| RUN-3-05 | `src/stepik_grader/web/api_routes.py:972` | POST /api/v1/runs/{id}/cancel отдаёт статус ДО отмены — клиент не отличит отмену от отказа | low | ✅ |
| RUN-3-06 | `src/stepik_grader/web/runs.py:391` | Проверка кода из редактора показывает имя tmpXXXXXX.py вместо реального файла решения | low | ✅ |
| RUN-3-07 | `src/stepik_grader/web/api_routes.py:217` | GET-эндпоинты отвечают 200 на ошибку «путь не указан», а POST в том же случае — 400 | low | ✅ |
| RUN-4-03 | `src/stepik_grader/diagnostic_stepik.py:274` | diagnostic_stepik --help падает трейсбеком EOFError вместо справки | low | ✅ |
| RUN-4-04 | `src/stepik_grader/cli/commands.py:562` | 'Файлы решений не найдены в: {path}' — плейсхолдер печатается буквально | low | ✅ |
| RUN-4-06 | `src/stepik_grader/downloader.py:400` | downloader --help вместо справки запускает мастер конфига и падает на EOF с кодом 0 | low | ✅ |
| RUN-4-07 | `src/stepik_grader/core/reporter.py:359` | Сводка --stats-summary печатается по-английски при дефолтной локали ru | low | ◐ |
| RUN-5-01 | `src/stepik_grader/glossary/stdlib_inventory.py:199` | Знаменатель coverage глоссария плавает: одна команда — разные числа | low | ✅ |
| RUN-5-02 | `src/stepik_grader/glossary/coverage.py:283` | coverage-CLI без --cards молча печатает 0/995 и rc=0, хотя база лежит в пакете | low | ◐ |
| RUN-5-04 | `src/stepik_grader/downloader.py:451` | downloader роняет трейсбек EOFError в цикле ввода URL | low | ✅ |
| RUN-5-05 | `src/stepik_grader/downloader.py:466` | downloader выходит с кодом 0 после провала конфига/авторизации | low | ✅ |
| RUN-5-06 | `src/stepik_grader/core/locales/ru.json:217` | Сообщение про --watch советует чужое имя пакета stepik-grader[watch] | low | ✅ |
| RUN-5-07 | `src/stepik_grader/glossary/stdlib_inventory.py:157` | Опечатка в --modules молча игнорируется: покрытие выглядит 100%, rc=0 | low | ✅ |
| RUN-5-08 | `src/stepik_grader/glossary/__init__.py:19` | Документированная команда coverage печатает RuntimeWarning от runpy | low | ✅ |

### Веб: сервер, API, граница слоёв, адаптеры

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| ADD-1-01 | `src/stepik_grader/web/reference_adapter.py:64` | reference_adapter: обновление токена вне try — сетевая ошибка ломает контракт «никогда не бросает» | medium | ✅ |
| ADD-1-03 | `src/stepik_grader/web/glossary_adapter.py:350` | glossary_adapter: очередь «Недостающее» резолвится от cwd процесса, а не от workspace | medium | ✅ |
| ARCH-2-01 | `src/stepik_grader/web/viewmodels.py:796` | Режим 1 через async-job пишет в историю и в таблицу имя временного файла | medium | ◐ |
| PERF-1-01 | `src/stepik_grader/core/cache.py:114` | Кэш результатов игнорирует параметры прогона — устаревший вердикт при смене timeout_seconds/--sandbox | medium | ✅ |
| PERF-1-02 | `src/stepik_grader/core/microbench_runner.py:197` | Микробенч меряет тайминги с включённым tracemalloc — ранжирование смещено против alloc-heavy решений | medium | ✅ |
| SEC-1-01 | `src/stepik_grader/web/api_routes.py:865` | POST /api/v1/hint читает ЛЮБОЙ файл в workspace и отправляет его AI-провайдеру наружу | medium | ✅ |
| SEC-1-03 | `src/stepik_grader/web/api_routes.py:611` | POST /api/save-solution перезаписывает любой файл внутри workspace, включая secrets.json | medium | ✅ |
| ADD-1-02 | `src/stepik_grader/web/downloader_adapter.py:202` | downloader_adapter: mkdir и постобработка вне try — OSError утекает из адаптера | low | ✅ |
| ADD-1-04 | `src/stepik_grader/web/reference_adapter.py:31` | Путь пересекает границу как str: Path → str(confined) → Path (нарушение инварианта) | low | ✅ |
| ADD-1-05 | `src/stepik_grader/web/settings_adapter.py:48` | settings_adapter: провал записи настроек проглатывается, а API отвечает новым значением | low | ◐ |
| ADD-1-06 | `src/stepik_grader/web/settings_adapter.py:46` | set_flag: getattr/setattr по строковому имени без сверки с полями UserSettings | low | ◐ |
| ADD-1-07 | `src/stepik_grader/web/rules_adapter.py:21` | Резолвер пути к БД истории скопирован в два адаптера вместо общего хелпера | low | ◐ |
| ARCH-2-02 | `src/stepik_grader/web/api_routes.py:207` | Синхронный GET /api/grade обходит реестр прогонов: ни back-pressure, ни отмены, ни TTL | low | ✅ |
| ARCH-2-03 | `src/stepik_grader/web/grading.py:43` | ADR-0010 выполнен формально: grading.py — ре-экспорт, оркестрация осталась в viewmodels | low | ◐ |
| ARCH-2-04 | `tests/test_import_dag.py:298` | Boundary-guard grade-ядра — денилист из 7 имён, а не allowlist, как обещает ADR-0010 §4 | low | ◐ |
| ARCH-2-05 | `scripts/check_web_imports.py:51` | check_web_imports.py охраняет только ребро → core.js, остальной граф ES-модулей не проверяется | low | ✅ |
| ARCH-2-06 | `src/stepik_grader/web/api_routes.py:114` | api_routes.py разросся в god-mixin: ~33 хендлера всех доменов в одном классе | low | ◐ |
| ARCH-2-07 | `src/stepik_grader/web/viewmodels.py:447` | Флаг истории и реестр прогонов — процесс-глобалы при состоянии-на-сервере | low | ✅ |
| PERF-1-03 | `src/stepik_grader/core/mtime_cache.py:26` | mtime_signature по max(mtime) не замечает удаления файла и добавления файла со старым mtime | low | ✅ |
| PERF-1-04 | `scripts/perf_baseline.py:250` | perf_baseline не умеет сравнивать со снимком — регресс ловить нечем | low | ◐ |
| PERF-1-05 | `scripts/perf_baseline.py:205` | perf_baseline: срыв дедлайна в параллельном сценарии даёт молча неверную цифру и висящие job'ы | low | ✅ |
| PERF-1-06 | `src/stepik_grader/web/viewmodels.py:772` | Ответ grade_path несёт stdin/actual для каждого кейса, включая прошедшие — payload и DOM растут линейно | low | ✅ |
| PERF-1-07 | `scripts/perf_baseline.py:179` | perf_baseline оставляет временные директории с тест-кейсами после каждого прогона | low | ✅ |
| SEC-1-02 | `src/stepik_grader/web/api_routes.py:865` | UnicodeDecodeError в чтении файла для hint не подавлен — запрос обрывается без ответа | low | ✅ |
| SEC-1-04 | `src/stepik_grader/web/server.py:208` | run_server принимает произвольный host, а Host-заголовок подделывается любым не-браузером | low | ◐ |
| SEC-1-05 | `src/stepik_grader/web/api_routes.py:846` | Согласие на отправку кода AI выдаётся тем же запросом, что и передаёт данные | low | ◐ |
| SEC-1-06 | `tests/e2e/test_xss_regression.py:29` | XSS-регрессия закрывает единственный сток (stdout), эхо-поля path/name не покрыты | low | ✅ |
| SEC-1-07 | `tests/e2e/test_xss_regression.py:1` | Тест периметра написан по-английски вопреки инварианту «артефакты по-русски» | low | ◐ |

### Фронтенд и дизайн

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| DES-1-01 | `src/stepik_grader/web/static/app.css:686` | На экране ≤768px страница не прокручивается: контент ниже сгиба недостижим | medium | ✅ |
| DES-1-02 | `src/stepik_grader/web/static/app.css:215` | prefers-reduced-motion не гасит бесконечные анимации — скелетон и прогресс начинают мерцать | medium | ✅ |
| FE-2-01 | `src/stepik_grader/web/static/grade.js:866` | «Отправить в Stepik» не переоценивается после прогона режима 1 — необратимый сабмит по устаревшему вердикту | medium | ✅ |
| FE-2-02 | `src/stepik_grader/web/static/grade.js:1141` | renderInlineDiff строит неограниченный DOM по выводу решения — раздел виснет, выход только перезагрузкой | medium | ✅ |
| FE-3-01 | `src/stepik_grader/web/static/content.js:365` | Deep-link на несуществующую карточку глоссария — тихий отказ, на экране остаётся ЧУЖАЯ карточка | medium | ✅ |
| DES-1-03 | `src/stepik_grader/web/static/app.css:624` | Ссылки в .errcard: primary на warning/error-highlight — 3.03:1 в тёмной теме, ниже WCAG 4.5 | low | ✅ |
| DES-1-04 | `scripts/check_contrast.py:201` | check_contrast.py проверяет наличие токена в парах, а не реальную пару текст/фон | low | ◐ |
| DES-1-05 | `src/stepik_grader/web/static/app.css:165` | Тёмная тема продублирована в двух блоках и уже разошлась; гейт смотрит только на один | low | ◐ |
| DES-1-06 | `src/stepik_grader/web/static/app.css:759` | Литеральные hex-фоллбэки в var() мимо системы токенов и разошлись со значениями токенов | low | ✅ |
| DES-1-07 | `src/stepik_grader/web/static/app.css:475` | outline:none у .form-input гасит глобальный :focus-visible; кольцо фокуса — на oklch(from …) | low | ◐ |
| DES-2-01 | `src/stepik_grader/core/reporter.py:592` | При RE/TLE verbose-вывод не показывает вход кейса — ранний return до блока Input | low | ✅ |
| DES-2-02 | `src/stepik_grader/core/reporter.py:593` | Traceback в [ERROR] печатается целиком — обрезка _clip_value применена только к Input/Expected/Actual | low | ✅ |
| DES-2-03 | `src/stepik_grader/cli/rendering.py:44` | _rows_to_markdown не экранирует '\|' и переводы строк — таблица --output markdown разваливается на RE | low | ✅ |
| DES-2-04 | `src/stepik_grader/core/reporter.py:167` | У зачтённого решения в колонке «Fail test» печатается «None» | low | ✅ |
| DES-2-05 | `src/stepik_grader/core/reporter.py:167` | Статус «NO TESTS» шире колонки (:>6) и сдвигает хвост plain-таблицы | low | ✅ |
| DES-2-06 | `src/stepik_grader/core/reporter.py:80` | Разделитель сжимается под ширину терминала, а сама plain-таблица — нет | low | ✅ |
| DES-2-07 | `src/stepik_grader/core/reporter.py:57` | Ширина rich-Console фиксируется на импорте — ресайз окна в цикле меню не подхватывается | low | ✅ |
| DES-2-08 | `src/stepik_grader/cli/interactive.py:99` | Неверный номер профиля в режимах 3/4 молча подменяется на профиль 2 | low | ◐ |
| FE-1-01 | `src/stepik_grader/web/static/core.js:669` | Опрос AI-подсказки не смотрит на HTTP-статус ответа: 75 запросов вхолостую вместо ошибки | low | ✅ |
| FE-1-02 | `src/stepik_grader/web/static/core.js:681` | AI-подсказка: нет отмены и дедупликации, результат уходит в откреплённый от DOM узел | low | ✅ |
| FE-1-03 | `src/stepik_grader/web/static/core.js:642` | Focus-trap модалки AI-согласия мёртв после клика по подложке (рецидив #804 FER-04) | low | ✅ |
| FE-1-04 | `src/stepik_grader/web/static/app.js:21` | applyTheme() затирается асинхронной applyUiLocale: aria-label кнопки темы врёт о режиме | low | ✅ |
| FE-1-05 | `src/stepik_grader/web/static/core.js:500` | Загрузка ui.json без таймаута и проверки статуса блокирует старт всего приложения | low | ◐ |
| FE-1-06 | `src/stepik_grader/web/static/core.js:376` | fetchCodeTerms без отмены: ответ на старый код перетирает панель «Функции в коде» | low | ✅ |
| FE-1-07 | `src/stepik_grader/web/static/app.js:89` | Роутер не сбрасывает выбранную карточку: «Назад» оставляет открытым то, чего нет в URL | low | ✅ |
| FE-1-08 | `src/stepik_grader/web/static/app.js:292` | Глобальные хоткеи раздела «Проверка» срабатывают под открытой модалкой | low | ✅ |
| FE-2-03 | `src/stepik_grader/web/static/grade.js:921` | Кнопка «Отмена» глушит ответ сервера и остаётся навсегда disabled при неудаче | low | ◐ |
| FE-2-04 | `src/stepik_grader/web/static/grade.js:873` | state.lastResult не сбрасывается при провале/отмене — сводка и «Разбор» показывают числа прошлого прогона | low | ◐ |
| FE-2-05 | `src/stepik_grader/web/static/grade.js:1219` | Панель «Разбор» не чистит ANSI-escape — исправление #726 применено лишь к ошибкам бенча | low | ✅ |
| FE-2-06 | `src/stepik_grader/web/static/trace-player.js:187` | Плеер трейса не подсвечивает мутацию на месте — сравниваются только ссылки, не heap | low | ✅ |
| FE-2-07 | `src/stepik_grader/web/static/trace-player.js:97` | Перетаскивание слайдера трейса пересобирает поддерево и форсит layout на каждый input | low | ◐ |
| FE-2-08 | `src/stepik_grader/web/static/grade.js:986` | Таблица результатов управляется только мышью: раскрытие решения и выбор кейса недоступны с клавиатуры | low | ✅ |
| FE-3-02 | `src/stepik_grader/web/static/content.js:334` | Списки карточек глоссария и правил не доступны с клавиатуры: <li> с click-обработчиком без role/tabindex | low | ✅ |
| FE-3-03 | `src/stepik_grader/web/static/downloader.js:394` | Возврат в «Загрузчик» стирает индикатор идущего OAuth, опрос не отменяется — можно запустить второй OAuth-job | low | ◐ |
| FE-3-04 | `src/stepik_grader/web/static/downloader.js:68` | Сбой GET /api/downloader/config оставляет строку «Куда скачивать» пустой без единого сообщения | low | ✅ |
| FE-3-05 | `src/stepik_grader/web/static/index.html:614` | Вендоренный бандл CodeMirror — единая точка отказа всего UI: при его пропаже страница молча мертва | low | ◐ |
| FE-3-06 | `src/stepik_grader/web/static/index.html:427` | Разметка редактора песочницы: <label for> указывает на <div>, обёртка tabindex=0 остаётся лишним фокус-стопом | low | ✅ |
| FE-3-07 | `src/stepik_grader/web/static/index.html:436` | Дублированный атрибут data-i18n-placeholder на поле stdin песочницы | low | ✅ |
| FE-3-08 | `src/stepik_grader/web/static/sandbox.js:138` | Кнопка «Отменить» в песочнице — тихий no-op, пока не пришёл ответ на POST /api/v1/runs | low | ✅ |

### Запуск и настройки (запрос владельца)

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| LNCH-2-01 | `src/stepik_grader/cli/__init__.py:621` | --sandbox молча игнорируется без --mode: меню запускает решения без изоляции | medium | ✅ |
| LNCH-2-03 | `src/stepik_grader/cli/__init__.py:615` | --serve игнорирует record_history из pyproject и персистентный тумблер истории | medium | ◐ |
| LNCH-3-01 | `src/stepik_grader/cli/__init__.py:615` | --serve игнорирует персистентный опт-аут истории из .grader_settings.json | medium | ◐ |
| LNCH-3-02 | `src/stepik_grader/cli/options.py:353` | Один флаг record_history — три несогласованные лестницы приоритета | medium | ✅ |
| LNCH-3-04 | `src/stepik_grader/config.py:281` | Конфиг якорится на cwd, а веб-настройки — на --root: два разных корня настроек | medium | ◐ |
| LNCH-4-02 | `src/stepik_grader/web/api_routes.py:845` | Web-путь AI-подсказки не проверяет endpoint согласия (#812), а клиент шлёт consent:true всегда | medium | ✅ |
| LNCH-5-01 | `src/stepik_grader/cli/__init__.py:615` | Сохранённый выбор «история ВЫКЛ» игнорируется при запуске веба флагом --serve и лаунчером | medium | ✅ |
| SET-1-04 | `src/stepik_grader/web/static/core.js:587` | Согласие на AI-подсказку нельзя отозвать из интерфейса | medium | ✅ |
| SET-2-01 | `src/stepik_grader/web/api_routes.py:845` | Web-путь AI-подсказки игнорирует ai_hint_consent_endpoint — согласие снова глобальное (обход #812) | medium | ✅ |
| SET-2-03 | `src/stepik_grader/cli/__init__.py:615` | `--serve` не читает персистентный тумблер истории: явное «выкл» игнорируется | medium | ✅ |
| LNCH-1-01 | `src/stepik_grader/launcher.py:261` | Лаунчер молча включает запись истории прогонов и не даёт её выключить | low | ◐ |
| LNCH-1-02 | `src/stepik_grader/core/locales/ru.json:170` | Выбор «с изоляцией» молча отключает пошаговый трейс — последствие не показано в точке выбора | low | ✅ |
| LNCH-1-03 | `src/stepik_grader/launcher.py:803` | Пользователь без tkinter/дисплея не видит ничего: совет уходит в stdout GUI-процесса без консоли | low | ✅ |
| LNCH-1-04 | `src/stepik_grader/launcher.py:141` | Язык окна нельзя выбрать, а LANG=C даёт английское окно вместо заявленного русского fallback | low | ✅ |
| LNCH-1-05 | `src/stepik_grader/launcher.py:679` | «Порт занят — выберите другой»: тупик без данных, хотя лаунчер умеет проверять порты | low | ✅ |
| LNCH-1-06 | `src/stepik_grader/launcher.py:647` | «Найдено задач: 0» — дедэнд без следующего шага | low | ✅ |
| LNCH-1-07 | `src/stepik_grader/web/static/locales/ui.json:358` | Веб-онбординг обещает галку sandbox в лаунчере, которой там нет | low | ✅ |
| LNCH-1-08 | `src/stepik_grader/launcher.py:557` | Выбор не запоминается между запусками, включая режим изоляции | low | ◐ |
| LNCH-2-02 | `src/stepik_grader/cli/__init__.py:607` | --serve падает трейсбеком при занятом или некорректном порте | low | ✅ |
| LNCH-2-04 | `src/stepik_grader/cli/__init__.py:597` | --serve молча съедает --mode/--file/--stats/--lint/--ai-hints/--output/--cache/--watch | low | ✅ |
| LNCH-2-05 | `src/stepik_grader/web/server.py:182` | --lang не доходит до веб-UI: страница всегда стартует на ru | low | ◐ |
| LNCH-2-07 | `src/stepik_grader/cli/__init__.py:140` | --version печатает англоязычный маркер мимо каталога локалей | low | ✅ |
| LNCH-3-03 | `src/stepik_grader/core/test_loader.py:39` | Двадцать модулей связывают CONFIG на импорте — главный блокер рантайм-перечитывания | low | ◐ |
| LNCH-3-05 | `src/stepik_grader/core/user_settings.py:129` | UserSettings: поля перечислены руками в трёх местах — новое поле молча не сохраняется | low | ◐ |
| LNCH-3-06 | `src/stepik_grader/web/settings_adapter.py:45` | set_flag — read-modify-write всего файла настроек без блокировки: потеря обновления | low | ◐ |
| LNCH-3-07 | `src/stepik_grader/web/viewmodels.py:447` | Флаг истории веба дублируется в модульном глобале и на объекте сервера | low | ◐ |
| LNCH-4-03 | `src/stepik_grader/web/api_routes.py:756` | Отключить историю из интерфейса нельзя: API принимает только onboarding_seen | low | ✅ |
| LNCH-4-04 | `src/stepik_grader/web/server.py:186` | Статус истории и бейдж изоляции запекаются в HTML при загрузке и врут после перезапуска сервера | low | ◐ |
| LNCH-4-05 | `src/stepik_grader/web/static/index.html:476` | Раздел «Настройки» без единого контрола: только read-only абзац со ссылкой на CLI-флаг | low | ✅ |
| LNCH-4-06 | `src/stepik_grader/web/static/core.js:516` | При недоступном ui.json «Настройки» показывают сырой маркер ключа вместо текста | low | ◐ |
| LNCH-5-02 | `src/stepik_grader/launcher.py:556` | Лаунчер забывает выбор способа запуска: порт, папка и изоляция сбрасываются каждый раз | low | ◐ |
| LNCH-5-03 | `src/stepik_grader/core/user_settings.py:70` | Профили запуска вместо россыпи флагов: UserSettings хранит 4 плоских булева, а выбор запуска — 20+ флагов | low | ◐ |
| LNCH-5-04 | `src/stepik_grader/web/api_routes.py:756` | Тумблер истории в вебе невозможен без перезапуска, хотя адаптер настроек к этому готов | low | ◐ |
| LNCH-5-05 | `src/stepik_grader/launcher.py:678` | Занятый порт в лаунчере — тупик вместо действия, хотя чаще всего там уже наш сервер | low | ◐ |
| LNCH-5-06 | `src/stepik_grader/launcher.py:803` | Headless-ветка лаунчера советует набрать флаг вместо того, чтобы запустить | low | ◐ |
| LNCH-5-07 | `src/stepik_grader/cli/options.py:40` | Три двери и ни одного экрана выбора: способ запуска нигде не предъявлен пользователю | low | ◐ |
| LNCH-5-08 | `docs/use/grader-workflow.md:181` | Слово «Песочница» означает две разные вещи — прямо в точке выбора способа запуска | low | ◐ |
| SET-1-01 | `src/stepik_grader/web/static/index.html:534` | Галка «не показывать онбординг» не синхронизируется с сервером — настройка молча переворачивается | low | ✅ |
| SET-1-02 | `src/stepik_grader/web/static/index.html:462` | Раздел «Настройки» не содержит ни одного интерактивного элемента | low | ✅ |
| SET-1-03 | `src/stepik_grader/web/static/index.html:467` | Заголовок панели «Интерфейс» не соответствует единственному содержимому — истории прогонов | low | ✅ |
| SET-1-05 | `src/stepik_grader/web/static/locales/ui.json:173` | Текст статуса при --no-history не даёт пути обратно — раздел становится тупиком | low | ✅ |
| SET-1-06 | `src/stepik_grader/web/static/app.js:111` | Состояние изоляции (sandbox) показано только бейджем в topbar, в «Настройках» его нет | low | ◐ |
| SET-2-02 | `src/stepik_grader/cli/interactive.py:577` | save_settings переписывает файл целиком — флаги, записанные другим каналом, теряются | low | ✅ |
| SET-2-04 | `src/stepik_grader/web/api_routes.py:847` | Веб умеет давать AI-согласие, но не умеет его отзывать — отзыв только из CLI | low | ◐ |
| SET-2-05 | `src/stepik_grader/web/api_routes.py:759` | POST /api/v1/settings всегда отвечает ok:true, включая случай несостоявшейся записи | low | ✅ |
| SET-2-06 | `src/stepik_grader/core/user_settings.py:100` | Битый .grader_settings.json молча обнуляет все настройки, следующая запись закрепляет потерю | low | ◐ |
| SET-2-07 | `src/stepik_grader/web/settings_adapter.py:46` | set_flag работает через getattr/setattr по строке — переименование поля не поймает ни mypy, ни ruff | low | ✅ |
| SET-3-02 | `docs/use/configuration.md:134` | Док обещает «файл не создаётся без --history», но --serve создаёт .grader_history.db по умолчанию | low | ✅ |
| SET-3-03 | `src/stepik_grader/cli/options.py:65` | Таймаут и лимит памяти нельзя задать из CLI — только правкой pyproject.toml, которого у pipx нет | low | ◐ |
| SET-3-04 | `src/stepik_grader/web/api_routes.py:756` | Веб показывает статус истории, но менять его нечем: запись настроек — только onboarding_seen | low | ✅ |
| SET-3-05 | `src/stepik_grader/web/viewmodels.py:447` | Веб-прогоны никогда не попадают в статистику: record_stats в web/ не используется вообще | low | ✅ |
| SET-3-06 | `src/stepik_grader/web/api_routes.py:62` | Потолки per-run лимитов в вебе — литералы, дублирующие дефолты CONFIG, и клампятся молча | low | ◐ |
| SET-3-07 | `src/stepik_grader/web/static/app.js:12` | Язык интерфейса нельзя закрепить ни на одной поверхности — только флаг и query-параметр | low | ◐ |
| SET-3-08 | `src/stepik_grader/config.py:45` | Нет единого контракта «настройка»: три хранилища, ни одного реестра «поле → поверхность» | low | ◐ |
| SET-4-01 | `src/stepik_grader/web/static/index.html:476` | Раздел «Настройки» в web — без единого контрола: только read-only статус истории | low | ✅ |
| SET-4-02 | `src/stepik_grader/core/insights.py:84` | Строгость сравнения вывода: решение принимается каждый прогон, а в UI его нет | low | ◐ |
| SET-4-03 | `src/stepik_grader/config.py:123` | Глубина и объём AI-подсказок доступны только через pyproject.toml | low | ◐ |
| SET-4-04 | `src/stepik_grader/core/ai_hints.py:145` | ai_system_prompt стирает запрет «не выдавай готовый код» — вредная настройка | low | ◐ |
| SET-4-05 | `src/stepik_grader/core/insights.py:47` | Пороги затухания «Подучить» надо объяснять, а не давать настраивать | low | ◐ |
| SET-4-06 | `docs/use/grader-workflow.md:513` | Критерий «что заслуживает раздела Настройки» не зафиксирован в доке | low | ◐ |
| SET-5-02 | `src/stepik_grader/web/static/core.js:585` | Согласие на AI-подсказку невозможно отозвать: нет ни одной точки отзыва в UI | low | ✅ |
| SET-5-03 | `src/stepik_grader/web/static/app.js:144` | Онбординг настраивается только изнутри модалки — в «Настройках» нет ни тумблера, ни повторного запуска | low | ◐ |
| SET-5-04 | `src/stepik_grader/web/static/index.html:9` | Нет честного блока «задаётся при запуске»: sandbox и рабочая директория известны фронту, но не показаны | low | ◐ |
| SET-5-05 | `src/stepik_grader/web/static/grade.js:1483` | Нет сброса локальных данных: 8+ ключей localStorage и история копятся без точки очистки | low | ✅ |
| SET-5-06 | `src/stepik_grader/web/static/index.html:467` | Заголовок панели «Интерфейс» врёт, а пустое состояние оформлено абзацем вместо готового .empty-state | low | ✅ |

### Сеть, загрузка задач, авторизация

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| ADD-2-01 | `src/stepik_grader/core/task_page_parser.py:70` | Парсер таблицы кейсов теряет <br>/<p>: expected склеивается в одну строку → неверный вердикт | high | ✅ |
| DEV-3-01 | `src/stepik_grader/core/test_source_fetcher.py:237` | GitHub-вариант А пишет input.txt/output.txt мимо _reset_tests_dir — старый Format 1 побеждает | high | ✅ |
| ADD-2-02 | `src/stepik_grader/core/oauth_flow.py:165` | Ответ 200 без access_token отравляет secrets.json: протухшему токену ставят expires_at=now+3600 | medium | ◐ |
| ADD-2-05 | `src/stepik_grader/core/stepik_reference.py:225` | Повторный импорт reference дублирует те же решения и затирает привязку в meta.json | medium | ✅ |
| ADD-2-06 | `src/stepik_grader/core/task_page_parser.py:128` | extract_tests_from_html молча даёт ноль кейсов на таблице из 2 колонок и на незакрытом <th> | medium | ◐ |
| DEV-3-02 | `src/stepik_grader/core/test_source_fetcher.py:239` | Скачивание файлов GitHub без перехвата исключений и со strict-utf8 чтением — полузаписанный набор тестов | medium | ✅ |
| DEV-3-04 | `src/stepik_grader/core/stepik_client.py:449` | wait_for_auth_code обслуживает ровно один HTTP-запрос: любой посторонний запрос убивает OAuth | medium | ✅ |
| ADD-2-04 | `src/stepik_grader/core/stepik_reference.py:192` | Неожиданная структура discussion-threads → KeyError/AttributeError вместо понятной ошибки | low | ✅ |
| DEV-3-03 | `src/stepik_grader/diagnostic_stepik.py:84` | diagnostic_stepik печатает authorize URL без state — по нему авторизация гарантированно отклоняется | low | ◐ |
| DEV-3-05 | `src/stepik_grader/core/stepik_client.py:284` | Лимит размера внешней загрузки проверяется после того, как тело уже целиком в памяти | low | ◐ |
| DEV-3-06 | `src/stepik_grader/core/stepik_client.py:546` | Обрыв сети при обновлении токена выдаётся пользователю за неверные OAuth-учётные данные | low | ◐ |
| DEV-3-07 | `src/stepik_grader/core/test_source_fetcher.py:93` | test_source_fetcher печатает голым print() и без локали — под --lang en вывод наполовину русский | low | ✅ |

### Безопасность, песочница, приватность

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| STR-5-01 | `src/stepik_grader/core/test_loader.py:205` | Тест-кейс без пары молча выбрасывается — решение получает зелёное «OK N/N» на неполном наборе | high | ✅ |
| ADD-4-01 | `src/stepik_grader/core/feedback.py:332` | collect_commit() тянет subject коммита ЛЮБОГО репозитория в CWD — в публичный issue | medium | ✅ |
| ADD-4-03 | `src/stepik_grader/core/diag_log.py:79` | redact не ловит kwarg/repr-форму token='...' — токен уезжает в URL публичного issue | medium | ✅ |
| SEC-2-01 | `src/stepik_grader/cli/__init__.py:621` | `--sandbox` без `--mode` молча игнорируется: меню грейдит через LocalRunner | medium | ✅ |
| SEC-2-02 | `src/stepik_grader/core/sandbox/_linux.py:70` | Linux-песочница ro-биндит весь venv: site-packages доступны решению вопреки SECURITY.md | medium | ✅ |
| SEC-2-03 | `src/stepik_grader/core/sandbox/_windows.py:385` | Windows: обрыв по CPU-квоте не убивает Job Object — внуки переживают нарушение | medium | ✅ |
| SEC-3-01 | `src/stepik_grader/web/api_routes.py:845` | Web-гейт согласия на AI не привязан к получателю: код уходит на подменённый ai_base_url молча | medium | ◐ |
| ADD-4-02 | `src/stepik_grader/web/static/index.html:581` | Предпросмотр «что уйдёт» в web свёрнут по умолчанию — контракт «сначала покажи fields» не выполнен | low | ◐ |
| ADD-4-04 | `src/stepik_grader/core/feedback.py:396` | FIELD_BUDGET_CHARS считается в символах, а лимит URL — в байтах: русский текст режется вдвое сверх бюджета | low | ◐ |
| ADD-4-05 | `src/stepik_grader/web/static/feedback.js:122` | Провал черновика гасит и ссылку на Discussions — запасной канал мёртв именно тогда, когда нужен | low | ✅ |
| ADD-4-06 | `src/stepik_grader/web/feedback_adapter.py:84` | Web-канал не передаёт logs: параметр адаптера, метка и место в _SACRIFICE_ORDER мертвы | low | ✅ |
| ADD-4-07 | `src/stepik_grader/web/feedback_adapter.py:74` | git log запускается на каждый пересбор черновика — подпроцесс на каждую паузу в наборе | low | ✅ |
| SEC-2-04 | `docs/use/configuration.md:377` | Доки обещают лимит памяти на macOS, которого нет: prlimit — Linux-only | low | ✅ |
| SEC-2-05 | `SECURITY.md:54` | CLI не предупреждает о дефолте «без OS-изоляции» в момент запуска | low | ◐ |
| SEC-2-06 | `src/stepik_grader/core/runner.py:441` | Скраб секретов из env — denylist по подстрокам: SSH_AUTH_SOCK проходит в решение | low | ◐ |
| SEC-2-07 | `src/stepik_grader/core/sandbox/_windows.py:391` | Windows-backend: `proc.wait()` без таймаута после аварийного обрыва | low | ◐ |
| SEC-3-02 | `src/stepik_grader/cli/commands.py:256` | revoke_ai_consent отзывает согласие только в текущей папке — из другой директории отзыв фиктивен | low | ◐ |
| SEC-3-03 | `src/stepik_grader/web/runs.py:535` | Web не проверяет base_url_is_allowed: подсказка тихо не работает без видимой причины | low | ✅ |
| SEC-3-04 | `src/stepik_grader/core/stats.py:57` | Журнал прогонов пишется в cwd, а очистка удаляет только текущий — данные остаются в чужих папках | low | ✅ |
| SEC-3-05 | `.github/workflows/claude.yml:23` | CI-агент: гейт по автору комментария, но в промпт едет тело issue от постороннего | low | ◐ |
| SEC-3-06 | `src/stepik_grader/core/diag_log.py:95` | register_secret игнорирует секреты короче 8 символов — короткий ключ не маскируется | low | ◐ |
| STR-5-02 | `src/stepik_grader/core/grader_core.py:232` | TLE и RE стирают частичный вывод решения: отчёт утверждает output=[], хотя раннер его знает | low | ◐ |
| STR-5-03 | `src/stepik_grader/core/reporter.py:610` | WA на невидимом различии: Expected/Actual и diff печатаются на экране одинаково | low | ◐ |
| STR-5-04 | `src/stepik_grader/core/reporter.py:147` | Отчёт печатает «OK» как вердикт о решении, хотя знает лишь «прошли N локальных кейсов» | low | ✅ |
| STR-5-05 | `src/stepik_grader/core/normalizers.py:7` | Нет способа выразить «порядок строк/пробелы не важны» — готовые нормализаторы висят неподключёнными | low | ◐ |

### Локальные данные, история, глоссарий

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| ADD-3-01 | `src/stepik_grader/glossary/data/module.json:7595` | 209 из 1349 карточек: примеры не компилируются — у многострочных блоков срезан отступ | medium | ✅ |
| ARCH-3-03 | `src/stepik_grader/core/history.py:354` | Миграция схемы 1→2 не идемпотентна при параллельных CLI и web: агрегат task_progress удваивается | medium | ✅ |
| ARCH-3-04 | `src/stepik_grader/core/user_settings.py:76` | Настройки лежат per-cwd, а база истории — глобальная: тумблер записи истории молча не применяется | medium | ✅ |
| DATA-1-03 | `src/stepik_grader/glossary/coverage.py:252` | coverage без --cards считает базу пустой и засоряет очередь ~987 ложными пробелами | medium | ✅ |
| PROD-2-01 | `src/stepik_grader/core/history.py:683` | task_key зависит от каталога запуска: одна задача даёт два ключа, разные задачи — один | medium | ✅ |
| PROD-2-02 | `src/stepik_grader/core/history.py:710` | purge_history падает трейсбеком на пустом/битом файле БД вместо best-effort | medium | ✅ |
| PROD-2-05 | `src/stepik_grader/core/insights.py:317` | Окно «Подучить» берётся по последним N прогонам всех задач — чужие успехи архивируют неисправленную ошибку | medium | ✅ |
| ADD-3-02 | `src/stepik_grader/glossary/data/builtin.json:119` | Карточка any учит неверной семантике: заявлен вывод False там, где Python печатает True | low | ✅ |
| ADD-3-03 | `src/stepik_grader/glossary/data/builtin.json:1016` | Карточка id заявляет id([]) == id([]) → False, CPython печатает True | low | ✅ |
| ADD-3-04 | `src/stepik_grader/glossary/data/exc.json:4077` | Карточка signal.ItimerError заявляет __name__ == ItimerError, реально itimer_error | low | ✅ |
| ADD-3-05 | `src/stepik_grader/glossary/data/module.json:5100` | Карточки math скрывают погрешность float: заявлено 30.0, печатается 29.999999999999996 | low | ✅ |
| ADD-3-06 | `src/stepik_grader/glossary/data/iter.json:488` | 9 карточек iter.json (в т.ч. 4 функции itertools) ведут docs_url на общий term-generator | low | ✅ |
| ADD-3-07 | `scripts/audit_glossary_cards.py:62` | Гейт audit_glossary_cards.py проверяет только наличие полей — битые примеры и пустой body зелёные | low | ✅ |
| ARCH-3-01 | `src/stepik_grader/rules/json_provider.py:19` | rules/ импортирует core/ — инвариант, на котором построен ADR-0011, нарушен и не проверяется тестом | low | ✅ |
| ARCH-3-02 | `src/stepik_grader/core/history.py:709` | purge_history падает трейсбеком на пустой/немигрированной БД — единственный путь истории без best-effort | low | ◐ |
| ARCH-3-05 | `src/stepik_grader/core/storage.py:44` | Два «единых» атомарных JSON-писателя с разной семантикой прав файла | low | ✅ |
| ARCH-3-06 | `docs/dev/adr/0011-local-persistence.md:20` | ADR-0011 описывает историю по устаревшему пути .grader_history.db и схеме v1 | low | ◐ |
| DATA-1-01 | `src/stepik_grader/glossary/stdlib_inventory.py:63` | Coverage меряет полноту по курируемым 23 модулям, но печатает это как «stdlib 100%» | low | ◐ |
| DATA-1-02 | `src/stepik_grader/glossary/coverage.py:88` | Хвостовая эвристика _is_known засчитывает покрытыми 21 сущность stdlib по чужим карточкам | low | ◐ |
| DATA-1-04 | `src/stepik_grader/glossary/json_provider.py:183` | known_terms() не фильтрует статус: черновики с пустым summary сразу считаются покрытием | low | ✅ |
| DATA-1-05 | `scripts/audit_glossary_cards.py:99` | Ревизия карточек проверяет docs_url только на непустоту — битые анкоры проходят гейт | low | ✅ |
| DATA-1-06 | `scripts/generate_draft_cards.py:111` | Генератор черновиков строит неверный анкор docs.python.org для встроенных типов | low | ◐ |
| DATA-1-07 | `src/stepik_grader/glossary/coverage.py:35` | Публичная format_report_summary отсутствует в __all__ модуля coverage | low | ✅ |
| MET-2-01 | `src/stepik_grader/core/insights.py:317` | Карточки «Подучить» гаснут от прогонов ДРУГОЙ задачи и от бенчмарков | low | ◐ |
| MET-2-02 | `src/stepik_grader/core/insights.py:132` | classify_status архивирует карточку, которая ни разу не была активной | low | ✅ |
| MET-2-03 | `src/stepik_grader/glossary/taxonomy.py:170` | Сортировка карточек по версии Python лексикографическая: 3.10 раньше 3.2 и 3.9 | low | ✅ |
| MET-2-04 | `src/stepik_grader/core/insights.py:100` | WA с расхождением только в регистре считается ошибкой форматирования | low | ◐ |
| MET-2-05 | `src/stepik_grader/glossary/detector.py:307` | Эвристика методов игнорирует defined_names: метод своего класса → карточка set.add | low | ✅ |
| MET-2-06 | `src/stepik_grader/core/insights.py:342` | severity правила собирается, но до раздела «Подучить» не доходит | low | ◐ |
| MET-2-07 | `src/stepik_grader/core/insights.py:284` | «Правила, которые ты нарушал» помнят только 10 последних прогонов | low | ✅ |
| MET-2-08 | `docs/dev/glossary.md:58` | Канон глоссария не описывает taxonomy.py: семейства разделов, EN-подписи, сортировки | low | ✅ |
| PROD-2-03 | `src/stepik_grader/core/stats.py:58` | Журнал .grader_stats.jsonl остался per-cwd — сводка неполна, --purge-history чистит лишь один каталог | low | ◐ |
| PROD-2-04 | `src/stepik_grader/core/progress_export.py:71` | Отчёт прогресса смешивает усечённые retention'ом прогоны с монотонным агрегатом задач | low | ✅ |
| PROD-2-06 | `src/stepik_grader/core/insights.py:259` | Повторный прогон неизменённого решения считается попыткой и наращивает серию; solution_hash не используется | low | ◐ |
| PROD-2-07 | `src/stepik_grader/core/progress_export.py:94` | В отчёте нет ни одного временного среза — «становлюсь ли я лучше» по нему не ответить | low | ◐ |
| PROD-2-08 | `docs/dev/rules-insights.md:100` | rules-insights.md описывает историю до #818/#819: папочная БД, нет task_progress и влияния retention | low | ✅ |

### CLI и пользовательские сценарии

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| COM-1-01 | `CODE_OF_CONDUCT.md:43` | Канал жалоб по Кодексу поведения не существует, а англоязычный блок ведёт в публичный тред | medium | ✅ |
| COM-1-05 | `docs/use/installation.md:40` | Первая команда установки падает на Ubuntu/Debian/Fedora (PEP 668 externally-managed-environment) | medium | ✅ |
| DEV-1-01 | `src/stepik_grader/cli/__init__.py:448` | main() всегда завершается кодом 0 — падение тестов неотличимо от успеха в CI | medium | ◐ |
| DEV-1-02 | `src/stepik_grader/cli/__init__.py:621` | --sandbox без --mode/--serve молча игнорируется: меню запускается без изоляции | medium | ✅ |
| MET-1-01 | `src/stepik_grader/core/reporter.py:591` | При RE и TLE студент не видит вход кейса: ранний return до печати Input/Expected/Actual | medium | ✅ |
| MET-1-07 | `src/stepik_grader/core/error_glossary.py:103` | resolve_error_hint не фильтрует карточки по kind — под «объяснение ошибки» подставится карточка термина | medium | ✅ |
| OPS-1-02 | `src/stepik_grader/diagnostic_stepik.py:165` | save_json() пишет ответы API в stepik_diagnostics/*.json без редакции — нарушение п.4 logging.md | medium | ✅ |
| OPS-1-08 | `src/stepik_grader/core/tests_writer.py:64` | Перескачивание задачи безвозвратно стирает вручную дописанные тест-кейсы без бэкапа | medium | ✅ |
| PROD-1-02 | `src/stepik_grader/core/user_settings.py:74` | Тумблер истории (пункт 7) забывается при запуске из другой папки задачи | medium | ✅ |
| COM-1-02 | `docs/use/installation.md:226` | Обещание «OAuth без ручной настройки» неверно: Шаг 0 (создание OAuth-приложения) обязателен всегда | low | ◐ |
| COM-1-03 | `src/stepik_grader/core/insights.py:246` | Серия считается по прогонам без дедупликации задач — бейджи фармятся повторным «Проверить» | low | ✅ |
| COM-1-04 | `src/stepik_grader/core/insights.py:265` | Потолок геймификации — 10 задач: на курсе из сотен шагов мотивация кончается за неделю | low | ✅ |
| COM-1-06 | `README.md:200` | Самый низкий порог входа — карточка глоссария — не виден ни в README, ни в онрампе | low | ✅ |
| COM-1-07 | `src/stepik_grader/cli/options.py:205` | Прогресс не складывается по группе: преподаватель не видит, где застревает курс | low | ◐ |
| DEV-1-03 | `src/stepik_grader/cli/interactive.py:634` | Ctrl+C в интерактивном меню роняет процесс трейсбеком | low | ✅ |
| DEV-1-04 | `src/stepik_grader/cli/__init__.py:617` | --serve с занятым портом падает трейсбеком, хотя локализованное сообщение уже есть | low | ✅ |
| DEV-1-05 | `src/stepik_grader/cli/interactive.py:220` | Диалог tkinter открывается в неинтерактивном запуске: --mode 1 без --file вешает скрипт | low | ✅ |
| DEV-1-06 | `src/stepik_grader/cli/__init__.py:425` | --watch с --output json/csv засоряет машинный поток текстом и escape-последовательностями | low | ✅ |
| DEV-1-07 | `src/stepik_grader/cli/commands.py:466` | Ранний выход «файл/папка не найдены» возвращает False и засчитывается как успешный прогон | low | ✅ |
| DEV-1-08 | `src/stepik_grader/cli/__init__.py:232` | Реэкспорт-фасад ради тестов (#903) — 19 патчимых имён, обёртки уже разошлись с реализацией | low | ◐ |
| MET-1-02 | `src/stepik_grader/core/grader_core.py:408` | TLE не несёт никакой дидактики и показывается по-английски | low | ✅ |
| MET-1-03 | `src/stepik_grader/core/error_glossary.py:91` | Карточка ошибки всегда по-русски: resolve_error_hint не принимает lang, хотя summary_en есть | low | ✅ |
| MET-1-04 | `src/stepik_grader/core/ai_hints.py:228` | _clip режет голову трейсбека — из промпта пропадает строка с классом и текстом исключения | low | ◐ |
| MET-1-05 | `src/stepik_grader/core/insights.py:81` | Ключ «Подучить» теряет класс исключения для 98 из 126 карточек: таксономия смотрит в компактную карту | low | ✅ |
| MET-1-06 | `src/stepik_grader/core/ai_hints.py:234` | Полный код решения уходит провайдеру вопреки инварианту в докстринге, выключателя нет | low | ◐ |
| MET-1-08 | `src/stepik_grader/core/ai_hints.py:313` | Граница «помог против решил за него» держится только на тексте промпта: ответ модели не проверяется | low | ◐ |
| OPS-1-01 | `src/stepik_grader/cli/__init__.py:448` | Неожиданное исключение в CLI → голый traceback, лог выключен, приложить к баг-репорту нечего | low | ✅ |
| OPS-1-03 | `src/stepik_grader/diagnostic_stepik.py:296` | При сбое диагностики пользователю не сообщается ни путь к логу, ни его существование | low | ✅ |
| OPS-1-04 | `src/stepik_grader/core/diag_log.py:173` | configure_diagnostics падает OSError на недоступном каталоге — включение лога роняет прогон | low | ✅ |
| OPS-1-05 | `src/stepik_grader/core/diag_log.py:174` | grader.log без ротации и потолка размера: под web+debug растёт неограниченно | low | ✅ |
| OPS-1-06 | `src/stepik_grader/core/diag_log.py:177` | В логе нет заголовка прогона (версия, ОС, Python, аргументы) | low | ✅ |
| OPS-1-07 | `src/stepik_grader/core/run_dir.py:50` | Осиротевшие stepik-sandbox-* каталоги никто не подметает, отказ уборки идёт мимо лога | low | ◐ |
| PROD-1-01 | `src/stepik_grader/cli/interactive.py:494` | Ложный «стрик успеха»: несостоявшиеся прогоны (нет файла/нет tests/) считаются успешными | low | ✅ |
| PROD-1-03 | `src/stepik_grader/cli/interactive.py:634` | Ctrl+C в интерактивном меню завершает процесс трейсбеком KeyboardInterrupt | low | ✅ |
| PROD-1-04 | `src/stepik_grader/core/locales/ru.json:115` | Сообщение «Тесты не найдены» отправляет пользователя меню за пределы меню | low | ✅ |
| PROD-1-05 | `src/stepik_grader/launcher.py:445` | Лаунчер локализован, а его ошибки и дочерний сервер — нет: англоязычный пользователь видит русский текст | low | ✅ |
| PROD-1-06 | `src/stepik_grader/web/static/index.html:571` | Дублирующийся атрибут data-i18n-placeholder: поле обратной связи берёт legacy-ключ | low | ✅ |
| PROD-1-07 | `src/stepik_grader/core/history.py:272` | Нет метрики «дошёл ли пользователь до первой зелёной проверки» | low | ◐ |

### CI, релизы, упаковка, цепочка поставок

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| AUD-2-02 | `scripts/check_contrast.py:38` | Гард контраста не парсит палитру темы «авто» — именно её видит пользователь по умолчанию | medium | ◐ |
| PKG-1-01 | `pyproject.toml:157` | Сборка из дерева без .git падает: у setuptools-scm нет fallback_version | medium | ✅ |
| REL-1-01 | `.github/workflows/release.yml:110` | github-release: checkout после download-artifact стирает dist/ — релиз выходит без wheel и sdist | medium | ✅ |
| REL-2-04 | `pyproject.toml:319` | Точечный прогон pytest всегда выходит с кодом 1: fail_under в конфиге при always-on --cov | medium | ✅ |
| REL-3-06 | `.github/workflows/release.yml:81` | Релиз публикует артефакт, который никто не открывал: ни twine check, ни проверки состава wheel | medium | ✅ |
| AUD-2-01 | `scripts/check_ruff_pin.py:62` | check_ruff_pin не проверяет `language: system` — второй пин версии ruff вернётся молча | low | ✅ |
| AUD-2-03 | `scripts/check_ui_locale_guardrails.py:76` | Оба web-гарда обходят только верхний уровень static/ — переезд модуля в подкаталог их ослепляет | low | ✅ |
| AUD-2-04 | `scripts/check_web_imports.py:51` | check_web_imports стережёт только ребро на core.js — девять остальных импорт-рёбер не проверяются | low | ✅ |
| AUD-2-05 | `scripts/check_ruff_pin.py:89` | installed_ruff_version() глотает любую ошибку запуска — проверка №3 отключается без следа | low | ◐ |
| AUD-2-06 | `scripts/check_version_consistency.py:145` | Дрейф версии в CLAUDE.md/versions.md — только WARNING, CI зелёный вопреки обещанию канона | low | ◐ |
| AUD-2-07 | `scripts/check_docs_guardrails.py:328` | Регулярка showcase-метрик ловит только «число + слово» вплотную — русская формулировка проходит | low | ✅ |
| PKG-1-02 | `tests/test_packaging.py:35` | Ни один тест не проверяет содержимое собранного колеса — package-data не защищена | low | ✅ |
| PKG-1-03 | `pyproject.toml:178` | Нерекурсивные глобы package-data: новая подпапка ассетов молча выпадает из колеса | low | ✅ |
| PKG-1-04 | `pyproject.toml:157` | sdist 3.4 МБ: едут docs/, tests/, .github/ и CHANGELOG — MANIFEST.in отсутствует | low | ✅ |
| PKG-1-05 | `src/stepik_grader/launcher.py:803` | gui-scripts: сообщение об отказе лаунчера уходит в несуществующую консоль | low | ✅ |
| PKG-1-06 | `src/stepik_grader/__init__.py:8` | Пакет не экспортирует __version__ и __all__ | low | ◐ |
| REL-1-02 | `.github/workflows/ci.yml:3` | Ни в одном workflow нет concurrency-группы: дублирующиеся прогоны матрицы и гонка badge-push | low | ✅ |
| REL-1-03 | `scripts/combine_coverage.py:196` | Cross-OS гейт покрытия 90% сам себя отключает при любой нехватке артефакта — возвращает 0 | low | ◐ |
| REL-1-04 | `.github/workflows/ci.yml:41` | Ни у одного job нет timeout-minutes: зависший прогон жжёт до 6 часов раннера | low | ◐ |
| REL-1-05 | `.github/workflows/ci.yml:84` | Нет кеша pip: каждый из ~14 job-инстансов ставит зависимости с нуля | low | ✅ |
| REL-1-06 | `.github/workflows/ci.yml:450` | pip-audit не блокирует и не запускается по расписанию: CVE всплывает только со следующим PR | low | ✅ |
| REL-1-07 | `.github/workflows/release.yml:24` | Гейт релиза проверяет предка в main, но не то, что CI на этом коммите был зелёным | low | ✅ |
| REL-2-01 | `scripts/version.py:105` | scripts/version.py берёт baseline любым тегом: git describe без --match, распаковка в 3 части | low | ✅ |
| REL-2-02 | `scripts/check_version_consistency.py:105` | Version-гейт валит MAJOR-релиз и делает это до шага тестов | low | ✅ |
| REL-2-03 | `pyproject.toml:178` | package-data перечисляет подпапки web/static вручную и не покрыт ни одним тестом | low | ◐ |
| REL-2-05 | `pyproject.toml:72` | Нижняя граница requests>=2.34.2 равна самому свежему релизу и ничем не обоснована | low | ✅ |
| REL-2-06 | `pyproject.toml:102` | Dev-зависимости без верхних границ: мажоры mypy и pytest уже сменились, огорожен только ruff | low | ✅ |
| REL-2-07 | `.github/dependabot.yml:11` | Dependabot не следит за Python-зависимостями — верхние границы протухают молча | low | ◐ |
| REL-2-08 | `.pre-commit-config.yaml:15` | pre-commit проверяет только ruff: четыре stdlib-guardrail'а и пины версий ловятся лишь в CI | low | ✅ |
| REL-3-01 | `src/stepik_grader/web/static/vendor/VERSIONS.md:77` | Рецепт пересборки CodeMirror не воспроизводим: esbuild@latest и 7 из 13 пакетов без пина | low | ✅ |
| REL-3-02 | `docs/dev/supply-chain.md:91` | Шрифты (107 КБ бинарных woff2 в wheel) без контрольных сумм — якорь ревью есть только у CodeMirror | low | ◐ |
| REL-3-03 | `scripts/check_ruff_pin.py:61` | check_ruff_pin.py не проверяет `language: system`, хотя докстринг заявляет это первой проверкой | low | ✅ |
| REL-3-04 | `.github/workflows/ci.yml:282` | Бейдж «coverage (ubuntu)» считается по CI-конфигу с выкинутыми sandbox-бэкендами и завышен | low | ✅ |
| REL-3-05 | `.github/workflows/ci.yml:451` | Джоб supply-chain не отличает «уязвимостей нет» от «аудит не отработал» | low | ◐ |
| REL-3-07 | `scripts/generate_ci_coveragerc.py:78` | generate_ci_coveragerc.py переносит только 4 ключа — новая настройка coverage тихо не доедет до CI | low | ◐ |

### Тесты, корпус, качество сейф-нета

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| ADD-5-03 | `src/stepik_grader/core/test_loader.py:205` | Формат 2 покрыт одной идеальной парой: потерянный expected_N и ведущий ноль молча выбрасывают кейсы | medium | ✅ |
| ADD-5-01 | `tests/test_property.py:87` | Три property-теста normalize_floats остаются зелёными, если функцию заменить на тождественную | low | ◐ |
| ADD-5-02 | `tests/test_mode_detector.py:16` | tests/test_mode_detector.py проверяет только приватный _is_safe_constant, публичный API модуля не тронут | low | ◐ |
| ADD-5-04 | `tests/test_output_comparison.py:125` | Таблица сравнения вывода не содержит строк про обрезку по max_output_bytes и хвостовые нули float | low | ◐ |
| ADD-5-05 | `tests/test_output_comparison.py:15` | Докстринг test_output_comparison.py обещает xfail(strict=True) на известные дефекты — маркеров в файле нет | low | ✅ |
| ADD-5-06 | `tests/test_result.py:1` | Докстринги test_result.py и части test_grader_core.py — по-английски вопреки инварианту | low | ✅ |
| QA-2-01 | `scripts/skip_inventory.py:155` | Инвентарь пропусков слеп к модульному pytestmark — скип целого файла невидим для гейта | low | ✅ |
| QA-2-02 | `tests/e2e/test_not_silently_skipped.py:74` | Guard «набор не скипнулся целиком» считает СОБРАННЫЕ тесты, а не выполненные | low | ◐ |
| QA-2-03 | `tests/e2e/test_not_silently_skipped.py:43` | Guard запуска браузера сам скипается при сломанном окружении: фикстура срабатывает раньше проверки флага | low | ◐ |
| QA-2-04 | `tests/e2e/test_load_failures.py:91` | Тест «Повторить» не доказывает повторный запрос — фикс, только прячущий баннер, остаётся зелёным | low | ✅ |
| QA-2-05 | `tests/e2e/test_poller_resilience.py:55` | Ассерты по обеим локалям опираются на неверную посылку — язык UI детерминированно русский | low | ✅ |
| QA-2-06 | `tests/e2e/test_journeys.py:216` | Жёсткий Control+A в редакторе: e2e-набор чинён под Linux, у контрибьютора на macOS ломается молча по смыслу | low | ◐ |
| QA-2-07 | `tests/test_property.py:28` | Property-набор может исчезнуть молча: нет guard'а на hypothesis, в отличие от e2e и песочницы | low | ◐ |
| QA-3-01 | `corpus/README.md:24` | Корпус пуст: ни одной зафиксированной задачи в репозитории — стенд не ловит ничего | low | ✅ |
| QA-3-02 | `scripts/corpus_mutations.py:106` | Префиксные мутации ломают решения с `from __future__ import` — 9 из 12 дают ложный «дефект ядра» | low | ✅ |
| QA-3-03 | `scripts/corpus_run.py:202` | Задача без загруженных кейсов даёт вакуумный AC на baseline вместо ошибки | low | ✅ |
| QA-3-04 | `scripts/corpus_run.py:228` | check_task мутирует сломанный эталон — WA-мутации совпадают случайно и отчёт зеленеет | low | ✅ |
| QA-3-05 | `scripts/corpus_mutations.py:187` | Каталог мутаций не покрывает четыре проверенных поведения ядра, включая эхо приглашения input() | low | ◐ |
| QA-3-06 | `scripts/corpus_fetch.py:228` | --limit в corpus_fetch применяется после полного обхода курса — сотни лишних GET | low | ✅ |
| QA-3-07 | `scripts/corpus_run.py:194` | Стенд никогда не гоняет вердикты под --sandbox: путь SandboxRunner корпусом не проверяется | low | ◐ |

### Документация, локали, редактура

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| AUD-3-01 | `docs/archive/audit-2026-07-30-full-roles.md:21` | Документ аудита 2026-07-30 не несёт ни одной пометки закрытия при десятках закрытых находок | medium | ✅ |
| ED-2-03 | `scripts/check_locale_guardrails.py:54` | CI-guard полноты каталога не видит CLI и downloader: опечатка в ключе печатает сам ключ, гейт зелёный | medium | ✅ |
| AUD-1-01 | `tests/test_facade_contract.py:52` | Контракт-тест фасада не замораживает приватные реэкспорты, которые сам объявляет замороженными | low | ✅ |
| AUD-1-02 | `src/stepik_grader/cli/__init__.py:687` | Текст ошибки --watch выводится по-английски мимо механизма локали _t() | low | ✅ |
| AUD-1-03 | `src/stepik_grader/cli/interactive.py:134` | Весь пакет cli/ печатает голым print() — инвариант «вывод через _console» не соблюдён | low | ◐ |
| AUD-1-04 | `src/stepik_grader/core/__init__.py:1` | Докстринг пакета core/ написан по-английски вопреки § Язык артефактов | low | ✅ |
| AUD-1-05 | `src/stepik_grader/core/runner.py:124` | Сообщение исключения RunSpec — по-английски, долетает до пользователя через CLI и web-API | low | ◐ |
| AUD-1-06 | `CLAUDE.md:162` | CLAUDE.md и докстринг grader.py ссылаются на несуществующий модуль cli.py | low | ✅ |
| AUD-1-07 | `src/stepik_grader/core/microbench_runner.py:86` | Путь в публичной сигнатуре strip_harness_frames типизирован str вместо Path | low | ◐ |
| AUD-3-02 | `docs/agent/claude-handoff.md:17` | docs/audit/README.md и claude-handoff.md дают взаимоисключающий ответ на «что делать дальше» | low | ◐ |
| AUD-3-03 | `docs/archive/audit-2026-07-30-full-roles.md:807` | §10 «Незакрытые пробелы» устарел: три из четырёх пробелов уже имеют закрывающие артефакты | low | ✅ |
| AUD-3-04 | `docs/archive/audit-2026-07-30-full-roles.md:613` | Цель DOCU-N1 (гейт достоверности docs/use/) не построена и нигде не отражена как открытая | low | ◐ |
| AUD-3-05 | `CHANGELOG.md:13` | CHANGELOG [Unreleased] снова простыня: 106 записей по ~590 символов при правиле «одна строка» | low | ◐ |
| ED-1-01 | `README.en.md:160` | EN-витрина нигде не предупреждает, что по умолчанию песочницы нет | low | ◐ |
| ED-1-02 | `README.md:220` | README.md ровно на пределе бюджета 220 строк — запас нулевой | low | ◐ |
| ED-1-03 | `README.md:156` | Хардкод «11 ADR» в README вопреки правилу «числа не хардкодятся» | low | ✅ |
| ED-1-04 | `CONTRIBUTING.md:279` | Дублированный фрагмент фразы в гайде контрибьютора (§ E2E-тесты) | low | ✅ |
| ED-1-05 | `CONTRIBUTING.md:115` | CONTRIBUTING описывает README, которого нет: «4 режима и основные CLI-флаги» | low | ◐ |
| ED-1-06 | `CONTRIBUTING.md:227` | Онбординг и § Запуск тестов дают разные команды локальных гейтов | low | ◐ |
| ED-1-07 | `README.en.md:3` | EN-витрина потеряла бейджи PyPI, Release и good first issues | low | ✅ |
| ED-2-01 | `src/stepik_grader/core/error_glossary.py:109` | RE-подсказка всегда по-русски: resolve_error_hint не знает про ?lang= при двуязычных карточках | low | ✅ |
| ED-2-02 | `src/stepik_grader/web/static/sandbox.js:53` | Фронтенд почти нигде не передаёт ?lang= — в английском интерфейсе ошибки сервера приходят по-русски | low | ✅ |
| ED-2-04 | `src/stepik_grader/core/locales/ru.json:8` | Веб-сообщение о ненайденных тестах — тупик, тогда как CLI на ту же ситуацию даёт три способа починки | low | ✅ |
| ED-2-05 | `src/stepik_grader/web/i18n.py:71` | render_message молча показывает сырой шаблон с {path}: несовпадение параметров не ловят ни лог, ни CI | low | ◐ |
| ED-2-06 | `src/stepik_grader/core/locales/en.json:34` | Английская локаль набрана русскими «ёлочками»; термин Learn закавычен двумя способами в одном файле | low | ✅ |
| ED-2-07 | `src/stepik_grader/core/locales/ru.json:11` | Орфографическая ошибка в русской подсказке по TLE: «Превышён» вместо «Превышен» | low | ✅ |
| ED-2-08 | `src/stepik_grader/core/locales/en.json:228` | Плюрализация core-локалей подделана скобками run(s)/entry(ies) при готовом механизме форм в вебе | low | ✅ |
| TW-1-01 | `src/stepik_grader/web/viewmodels.py:310` | ErrorCard.timeout_s отдаёт серверный дефолт, а не действовавший per-run лимит | low | ✅ |
| TW-1-02 | `docs/dev/result-contract.md:146` | Run result: mode="microbench" не объявлен в перечислении контракта | low | ✅ |
| TW-1-03 | `docs/dev/result-contract.md:135` | Solution result описан несуществующими именами полей (time/memory) | low | ◐ |
| TW-1-04 | `docs/dev/result-contract.md:163` | Контракт описывает поле `lint` в результате, которого нет ни в одном ответе | low | ◐ |
| TW-1-05 | `docs/dev/web-contracts.md:326` | CaseView в web-contracts описан устаревшей формой: нет половины полей и двух вердиктов | low | ◐ |
| TW-1-06 | `docs/dev/web-contracts.md:139` | `actions` объявлен как CommandAction[], а API отдаёт список строк-id | low | ✅ |
| TW-1-07 | `docs/dev/api.md:558` | 404 run_not_found возможен до истечения TTL — вытеснение по потолку реестра не описано | low | ✅ |
| TW-1-08 | `docs/dev/architecture.md:57` | core/run_dir.py отсутствует в таблице модулей и в графе зависимостей | low | ✅ |
| TW-2-01 | `src/stepik_grader/core/runner.py:201` | ADR-0006: Runner описан как протокол с единственным run, а в коде требует ещё атрибут | low | ✅ |
| TW-2-02 | `docs/dev/adr/0003-ai-integration.md:9` | ADR-0003 помечает код как «ещё не AI» и не знает про реализованный consent-gate | low | ✅ |
| TW-2-03 | `docs/dev/adr/0011-local-persistence.md:99` | ADR-0011: выбранная альтернатива D называет core/db.py вопреки своему же решению | low | ✅ |
| TW-2-04 | `src/stepik_grader/web/viewmodels.py:22` | ADR-0010: последствие «viewmodels худеет до JSON-маппинга» не наступило | low | ✅ |
| TW-2-05 | `src/stepik_grader/cli/interactive.py:311` | ADR-0002 фиксирует один nudge, в коде их два (серия успехов не описана) | low | ✅ |
| TW-2-06 | `docs/dev/adr/0006-runner-abstraction.md:42` | ADR-0006 ведёт за точкой инъекции в grader_core — реестр раннера живёт в core/runner.py | low | ◐ |
| TW-2-07 | `docs/dev/adr/README.md:21` | Конвенция «не править ADR задним числом» нарушена самим ADR-0011 | low | ◐ |
| TW-3-01 | `src/stepik_grader/web/api_routes.py:509` | grader-workflow.md утверждает, что /api/download не конфайнится — код конфайнит (issue #401) | low | ✅ |
| TW-3-02 | `src/stepik_grader/cli/options.py:159` | Флаг --purge-history не описан ни в одном документе docs/use/ | low | ✅ |
| TW-3-03 | `docs/use/installation.md:173` | installation.md: список разделов веб-интерфейса отстал от сайдбара (нет «Прогресс» и «Настройки») | low | ✅ |
| TW-3-04 | `src/stepik_grader/downloader.py:402` | Документированная команда downloader не разбирает флаги: --help запускает мастер конфига | low | ✅ |
| TW-3-05 | `src/stepik_grader/cli/__init__.py:687` | Ошибка «--watch is only supported for --mode 1/2» печатается по-английски мимо локали | low | ◐ |
| TW-3-06 | `docs/use/installation.md:151` | installation.md: пример вывода --version застрял на 1.9.0 и не показывает dev-форму | low | ✅ |

### Стратегия: развитие, цена владения, платформа

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| STR-2-02 | `pyproject.toml:245` | --cov=scripts + fail_under=85 в addopts втягивают разовую тулзу мейнтейнера в гейт качества продукта | medium | ✅ |
| TRE-1-01 | `src/stepik_grader/core/ai_hints.py:150` | Детектор reasoning-моделей не знает gpt-5* и deepseek-reasoner — payload отвергается, подсказок нет | medium | ✅ |
| TRE-1-03 | `src/stepik_grader/core/ai_hints.py:289` | Настроенный, но отказывающий AI-провайдер (401/429/таймаут) не даёт пользователю ни одного слова | medium | ✅ |
| VIS-2-01 | `src/stepik_grader/pytest_plugin.py:132` | pytest-плагин молча собирает 0 items для решения без tests/ — зелёный CI при пропавших тестах | medium | ✅ |
| ADV-1-01 | `CHANGELOG.md:69` | Тело GitHub-релиза — стена инженерной прозы: цитировать и репостить нечего | low | ✅ |
| ADV-1-03 | `README.md:16` | Русский первый экран не говорит, что грейдер работает без Stepik — это только в README.en.md | low | ✅ |
| ADV-1-04 | `README.md:10` | Бейдж Glossary — главный дифференциатор — ведёт во внутренний dev-документ | low | ✅ |
| ADV-1-05 | `pyproject.toml:19` | PyPI-keywords не содержат ни одного запроса, которым проект реально ищут | low | ◐ |
| ADV-1-06 | `docs/assets:1` | Нет заготовок анонса: в репозитории только 4 скриншота под README, ни одного постового формата | low | ◐ |
| GRW-1-01 | `.github/badges/good-first-issues.json:4` | Пул good first issue — 3 задачи при собственном пороге здоровья 5, и 2 из 3 в одной области | low | ✅ |
| GRW-1-02 | `.github/workflows/ci.yml:291` | Бейдж пула обновляется только при push в main и глушит сбой — README может врать неделями | low | ✅ |
| GRW-1-03 | `.github/ISSUE_TEMPLATE/feature_task.md:1` | Легаси-шаблон feature_task.md дублирует idea.yml и отпугивает языком постановки | low | ◐ |
| GRW-1-04 | `.github/PULL_REQUEST_TEMPLATE.md:24` | Чеклист PR не применим к вкладу, который CONTRIBUTING рекламирует как первый | low | ◐ |
| GRW-1-05 | `README.md:5` | About-панель репозитория: homepage пуст, описание только по-русски | low | ✅ |
| GRW-1-06 | `.github/ISSUE_TEMPLATE/bug_report.yml:87` | Обязательное поле «Окружение» блокирует баг-репорт от того, кто грейдер не ставил | low | ✅ |
| STR-1-01 | `corpus/README.md:1` | Корпус: ~2860 строк стенда при нуле данных в git и нуле упоминаний в CI — кандидат №1 на вырезание | low | ✅ |
| STR-1-02 | `src/stepik_grader/core/tracer.py:383` | Пошаговый трейс: ~1580 строк ради web-only витрины, недоступной под --sandbox | low | ✅ |
| STR-1-03 | `src/stepik_grader/core/grader_core.py:812` | Два режима бенчмарка (3 и 4) на один вопрос — с несопоставимыми метриками памяти | low | ◐ |
| STR-1-04 | `src/stepik_grader/core/stepik_reference.py:7` | --import-reference: скрейпер недокументированного API Stepik, пишущий чужой код в папку решений | low | ◐ |
| STR-1-05 | `src/stepik_grader/cli/options.py:65` | 31 флаг в корневом --help, из них 9 — служебные «сделать и выйти» | low | ✅ |
| STR-1-06 | `src/stepik_grader/cli/options.py:323` | --watch: opt-зависимость и отдельная ветка в резолвере кэша ради петли, которую даёт IDE | low | ◐ |
| STR-1-07 | `src/stepik_grader/ide.py:49` | ide.py: 147 строк и флаг ради статичного JSON без проверки на дрейф флагов | low | ✅ |
| STR-2-01 | `.github/workflows/ci.yml:105` | CI-веер: OS-независимые гейты гоняются 9 раз, ни в одном workflow нет concurrency-группы | low | ✅ |
| STR-2-03 | `.github/workflows/ci.yml:13` | Каждый новый гард стоит 4 артефакта: скрипт + шаг ci.yml + тест-файл + абзац в доках | low | ◐ |
| STR-2-04 | `docs/dev/project-structure.md:110` | project-structure.md — ручной «канонический перечень файлов», уже разошёлся с деревом | low | ✅ |
| STR-2-05 | `CHANGELOG.md:1` | CHANGELOG.md — самый горячий файл репозитория и обязательная точка конфликта каждого PR | low | ◐ |
| STR-3-01 | `src/stepik_grader/core/stepik_client.py:661` | Файловый кэш API не спасает при недоступности Stepik: нет отдачи просроченной копии | low | ✅ |
| STR-3-02 | `tests/test_stepik_client.py:315` | Нет канарейки дрейфа Stepik API: все тесты мокают, зелёный CI ничего не гарантирует | low | ✅ |
| STR-3-03 | `src/stepik_grader/core/stepik_client.py:702` | Смена формы ответа Stepik маскируется под «шаг не найден» вместо честного «формат изменился» | low | ✅ |
| STR-3-04 | `README.md:14` | Русский README не сообщает, что грейдер работает без Stepik — «generic mode» живёт только в EN-версии | low | ✅ |
| STR-3-05 | `src/stepik_grader/downloader.py:336` | Каталог задачи может произвести только загрузчик со Stepik: нет офлайн-способа завести задачу | low | ◐ |
| STR-3-06 | `src/stepik_grader/core/stepik_client.py:85` | API_HOST зашит константой и не переопределяем; diagnostic импортирует значение, а не модуль | low | ✅ |
| STR-3-07 | `docs/use/versions.md:64` | Имя продукта/пакета/дистрибутива вшито в платформу — при пивоте это ломающее переименование | low | ◐ |
| STR-4-01 | `src/stepik_grader/core/stats.py:47` | Веб-прогоны не попадают в журнал статистики, хотя код утверждает обратное | low | ✅ |
| STR-4-02 | `src/stepik_grader/core/stats.py:124` | Журнал не отвечает на вопрос, ради которого заведён: нет таксономии причин падения | low | ◐ |
| STR-4-03 | `src/stepik_grader/core/stats.py:57` | Журнал статистики фрагментируется по папкам — для истории это уже починили, для stats нет | low | ✅ |
| STR-4-04 | `src/stepik_grader/core/stats.py:126` | Метка времени пишется в каждую запись, но сводка не имеет временной оси | low | ✅ |
| STR-4-05 | `src/stepik_grader/web/static/index.html:69` | У локальной статистики нет входа для пользователя, не читавшего документацию | low | ◐ |
| STR-4-06 | `src/stepik_grader/config.py:80` | Дефолт истории разъехался: web ON, CLI OFF — «Подучить» для CLI-пользователя молча пуст | low | ◐ |
| TRE-1-02 | `src/stepik_grader/core/ai_hints.py:274` | Для reasoning-модели бюджет 400 токенов уходит в рассуждение — content пустой, подсказка всегда пропускается | low | ◐ |
| TRE-1-04 | `.github/workflows/ci.yml:43` | Python 3.14 стабилен ~10 месяцев, но остаётся continue-on-error и отсутствует в classifiers | low | ✅ |
| TRE-1-05 | `src/stepik_grader/web/static/vendor/VERSIONS.md:77` | Рецепт пересборки CodeMirror пинит 6 пакетов из 13 — сборка невоспроизводима, sha256 не сойдётся | low | ◐ |
| TRE-1-06 | `.github/dependabot.yml:11` | Вендоренный CodeMirror (360 КБ в каждом wheel) не покрыт ни dependabot, ни pip-audit | low | ◐ |
| VIS-1-01 | `src/stepik_grader/core/insights.py:57` | Грейдер знает, что WA — только форматирование, но не говорит этого пользователю | low | ✅ |
| VIS-1-02 | `src/stepik_grader/glossary/lookup.py:20` | Офлайн-разбор «код → карточки глоссария» доступен только вебу и AI-промпту, CLI-поверхности нет | low | ◐ |
| VIS-1-03 | `src/stepik_grader/cli/options.py:115` | История — тупик для интеграций: --insights молча игнорирует --output, --export-progress без json | low | ◐ |
| VIS-1-04 | `src/stepik_grader/cli/options.py:58` | Онбординг завязан на скачивание из Stepik — нет команды завести задачу локально | low | ◐ |
| VIS-1-05 | `src/stepik_grader/cli/__init__.py:556` | Карточка «Подучить» терминальна: механика затухания есть, следующего шага для ученика нет | low | ◐ |
| VIS-1-06 | `src/stepik_grader/cli/options.py:211` | «тали вердиктов» в тексте --export-progress — непереведённая калька в справке и документации | low | ✅ |
| VIS-2-02 | `src/stepik_grader/pytest_plugin.py:56` | Плагин pytest не умеет включить sandbox: поверхность в чужой CI жёстко на LocalRunner | low | ◐ |
| VIS-2-03 | `src/stepik_grader/grader.py:94` | Точка расширения асимметрична: set_runner в фасаде, Runner/RunSpec/RunOutcome — только в core/ | low | ✅ |
| VIS-2-04 | `pyproject.toml:126` | Единственная группа entry points — pytest11: сторонний Runner или источник задач не подключить | low | ◐ |
| VIS-2-05 | `src/stepik_grader/cli/__init__.py:576` | --init-vscode не умеет перезаписывать: tasks.json никогда не обновляется после апгрейда | low | ✅ |
| VIS-2-06 | `src/stepik_grader/core/runner.py:783` | Runner — процесс-глобальный синглтон; ADR-0006 не задаёт контракт для сторонних реализаций | low | ✅ |
| VIS-2-07 | `src/stepik_grader/core/stepik_client.py:85` | Привязка к Stepik размазана по 7 модулям; платформо-нейтральный шов уже проходит по test_loader | low | ✅ |

---

## 6. Отклонённые и схлопнутые находки

Фиксируются по правилу [`docs/audit/README.md`](README.md): молча удалённая находка вернётся следующим аудитом.

| ID | Находка | Вердикт | Причина |
|---|---|---|---|
| PY-3-07 | MtimeCache: подпись снимается до load() — правка во время загрузки кешируется ка | REFUTED | Вывод обратный: сохраняется СТАРАЯ подпись при НОВЫХ данных, поэтому следующий вызов видит несовпадение и перезагружает. Лишний reload, а не устаревшее значение; репро не |
| TRE-1-07 | MCP-канал оставлен "открытым" в ADR-0003, но ни issue, ни ADR так и не заведены | REFUTED | Не пробел, а зафиксированный анти-приоритет: docs/archive/audit-2026-07-18.md:350 перечисляет MCP-канал в списке 'что НЕ делать сейчас'. Это фича из roadmap, а не дефект  |
| ADV-1-02 | Забытое переименование [Unreleased] обрушивает job релиза уже после публикации т | REFUTED | Сценарий «PyPI выложен, релиза нет» недостижим: забытое переименование [Unreleased] валит verify до build, оба потребителя гейтятся им. Гейт уже есть, переносить нечего. |
| RUN-5-03 | diagnostic_stepik падает трейсбеком EOFError при закрытом stdin | DUPLICATE | Дубликат RUN-4-03 (тот же файл, та же строка, тот же фикс). Отдельная запись не нужна. |
| LNCH-2-06 | Лаунчер умеет только --port/--root/--sandbox: GUI-пользователь заперт в дефолтах | DUPLICATE | Дубликат LNCH-1-01: тот же файл, та же функция, та же первопричина. |
| LNCH-4-01 | Web-сервер игнорирует сохранённый opt-out истории (.grader_settings.json / CONFI | DUPLICATE | Дубликат LNCH-3-01: те же строки, то же следствие и то же исправление; новой информации нет. |
| SET-3-01 | record_history разрешается тремя независимыми цепочками; явный выбор пользовател | DUPLICATE | Дубликат SET-2-03 (--serve игнорирует персистентный тумблер). Формулировка шире (добавляет ветку --mode 2, тоже не видящую user-state), но дефект и правка те же — единый  |
| SET-5-01 | История прогонов: раздел показывает тупик «перезапустите сервер», хотя write-thr | DUPLICATE | Дубликат SET-4-01: тот же файл, тот же элемент, то же предложение (тумблер + расширить whitelist). |
| SET-5-07 | Смена языка не перерисовывает раздел настроек: единственная строка остаётся на п | REFUTED | Вывод неверен: при смене языка на открытых «Настройках» #history-status перерисовывается через секционный хук — это закомментировано как решение #546. Предложенный фикс у |
| ADD-2-03 | try_create_session_without_browser ловит только HTTPError — ConnectionError рвёт | DUPLICATE | Тот же дефект и тот же repro, что ADD-1-01 — автор смотрит на другой конец той же цепочки. Дубликат ADD-1-01; факт про HTTPError-only верен и там учтён. |
| STR-2-06 | Разобранный аудит 2026-07-30 лежит в docs/audit/ и заявляет все находки открытым | REFUTED | Ключевое утверждение «ни одного номера PR» ложно, аудит не отработан — правило переезда в archive не нарушено. Реально устарела одна фраза в шапке, а не 192 находки. |
| STR-5-06 | Непроверенное решение (нет тест-кейсов) попадает в рейтинг скорости без пометки  | REFUTED | Репро недостижим: непроверенное решение не получает ни процентов, ни FASTER/SIMILAR, ни места в рейтинге — оно выпадает строкой с ошибкой. Остаётся лишь мелкая несогласов |

---

## 7. а) Ошибки и баги

Находки с горизонтом `bug` — дефекты текущего поведения. Полный контекст каждой — в § 5.

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| ADD-2-01 | В _TableParser на <br> и на закрытие p/div/li дописывать '\n' в _current_cell, на выходе схлопывать повторные переводы строк и strip'ать. Тесты на <br | `src/stepik_grader/core/task_page_parser.py:70` | high |
| QA-1-01 | Добавить в TestLoadTestCases кейс «input без пары» и «N без N.clue» с pytest.warns; в core/test_loader.py:205/:218 перед continue выдавать warnings.wa | `src/stepik_grader/core/test_loader.py:205` | high |
| RUN-1-01 | Искать пару по той же буквальной группе цифр: f"expected_{m.group(1)}.txt" с откатом на нормализованный индекс; плюс warnings.warn, когда input_N.txt  | `src/stepik_grader/core/test_loader.py:204` | high |
| RUN-2-01 | Классифицировать блок тем же критерием, что и маршрут исполнения (_block_invokes_solution: блок печатает/вызывает решение), а не «похоже на Python»; п | `src/stepik_grader/core/test_loader.py:187` | high |
| RUN-2-02 | Считать блок драйвером формата 3, если он вызывает любое имя из множества функций решения (собрать все имена через AST), а не только первое; тогда исп | `src/stepik_grader/core/mode_detector.py:211` | high |
| ADD-2-02 | В refresh_access_token валидировать ответ: непустой access_token и числовой expires_in, иначе ValueError. В oauth_flow не обновлять secrets и вернуть  | `src/stepik_grader/core/oauth_flow.py:165` | medium |
| ARCH-2-01 | Пробросить исходный путь в grade_path отдельным параметром (`display_path`) и брать его для `_rel()` и записи истории; temp оставить только исполняемы | `src/stepik_grader/web/viewmodels.py:796` | medium |
| FE-2-01 | Звать updateStepikSubmitButton(state.lastResult) в _finishGradeUI() и сбрасывать кнопку в disabled в grade() рядом со сбросом selectedRow; дополнитель | `src/stepik_grader/web/static/grade.js:866` | medium |
| FE-2-02 | Рендерить первые N строк (напр. 500) с плашкой «N из M» и кнопкой «показать всё», плюс кламп длины одной строки перед visibleWhitespace; лучше — усека | `src/stepik_grader/web/static/grade.js:1141` | medium |
| LNCH-2-01 | Перенести блок установки SandboxRunner (и проверку SandboxUnavailableError) ВЫШЕ ветки `args.mode is None`, прокинуть флаг в CliContext, чтобы пункты  | `src/stepik_grader/cli/__init__.py:621` | medium |
| LNCH-2-03 | В ветке --serve резолвить историю как `args.history if args.history is not None else (user_settings.record_history / CONFIG.record_history)`, переиспо | `src/stepik_grader/cli/__init__.py:615` | medium |
| LNCH-4-02 | Перенести проверку из cli/commands.py в _handle_create_hint: сверять ai_hint_consent_endpoint с текущим scheme://host[:port], расхождение → 403 consen | `src/stepik_grader/web/api_routes.py:845` | medium |
| REL-1-01 | Поставить `actions/checkout` ПЕРВЫМ шагом github-release, до download-artifact (либо дать checkout отдельный `path:`). Дополнительно `fail_on_unmatche | `.github/workflows/release.yml:110` | medium |
| RUN-1-02 | Пробросить факт обрезки в результат: вернуть stderr или флаг truncated в словарь AC/WA (стр. 455-467) и показывать в reporter/JSON, чтобы WA от лимита | `src/stepik_grader/core/grader_core.py:466` | medium |
| RUN-1-03 | Перед continue на стр. 218-219 (и симметрично 205-206) выдавать warnings.warn с именем непарного файла — тем же приёмом, что уже применён к формату 3. | `src/stepik_grader/core/test_loader.py:218` | medium |
| RUN-2-03 | Читать файлы тестов байтами с decode(errors='replace') либо ловить UnicodeDecodeError и возвращать «файл тестов не в UTF-8: <путь>»; в режиме 2 помеча | `src/stepik_grader/core/test_loader.py:149` | medium |
| RUN-4-01 | В _drain читать pipe.read1(65536) вместо read(65536), чтобы уже доступные байты попадали в sink сразу; после выхода процесса дочитывать детерминирован | `src/stepik_grader/core/runner.py:672` | medium |
| RUN-4-02 | Применять _clip_value к каждой строке diff в print_case_verbose (:614-621) до _cprint и ограничить общий объём diff в символах, а не только 20 строкам | `src/stepik_grader/core/reporter.py:615` | medium |
| SEC-2-01 | Перенести блок `if args.sandbox: set_runner(...)` выше проверки `args.mode is None`, чтобы меню наследовало изоляцию и падало тем же parser.error. Зао | `src/stepik_grader/cli/__init__.py:621` | medium |
| SEC-2-02 | Биндить каталог интерпретатора + stdlib/platstdlib + /usr, явно исключив `*/site-packages`; либо честно переписать строку SECURITY.md и `supports_proj | `src/stepik_grader/core/sandbox/_linux.py:70` | medium |
| SEC-2-03 | Добавить `cpu_exceeded.is_set()` в условие на строке 385, чтобы Job уничтожался на всех аварийных исходах единообразно. | `src/stepik_grader/core/sandbox/_windows.py:385` | medium |
| SET-2-01 | Вынести в settings_adapter has_ai_consent(workspace, endpoint)/grant_ai_consent(workspace, endpoint); в _handle_create_hint считать consent_endpoint(C | `src/stepik_grader/web/api_routes.py:845` | medium |
| SET-2-03 | Свести чтение user-state в одну точку: _resolve_record_history → args → load_settings(default_settings_path()).record_history → CONFIG; в --serve испо | `src/stepik_grader/cli/__init__.py:615` | medium |
| ADD-2-04 | В pick_solutions_thread пропускать не-dict элементы; в import_references_from_task_dir брать proxy через .get и при отсутствии бросать ValueError('Отв | `src/stepik_grader/core/stepik_reference.py:192` | low |
| LNCH-2-02 | Обернуть web.run_server в `except OSError as exc: parser.error(_t('server_start_failed', error=exc))` — симметрично interactive.py:568-572; добавить п | `src/stepik_grader/cli/__init__.py:607` | low |
| LNCH-2-04 | Ввести таблицу совместимости флагов и parser.error на конфликт (--serve с --mode/--file/--dir/--watch/--output); --stats для --serve либо прокинуть в  | `src/stepik_grader/cli/__init__.py:597` | low |
| QA-1-02 | Тест на смешанную папку + warning при пересечении индексов между форматами 1 и 2 (либо явный приоритет 2 над 1, как формат 3 над остальными). | `src/stepik_grader/core/test_loader.py:214` | low |
| RUN-1-05 | При одновременном наличии N/N.clue и input_N.txt выдавать warnings.warn о смешении форматов и выбирать один приоритетный, как сделано для формата 3. | `src/stepik_grader/core/test_loader.py:200` | low |
| RUN-1-06 | Ловить NameError имени из блока теста и переигрывать кейс через exec-обёртку (_build_call_wrapper); минимум — внятное сообщение вместо трейсбека и явн | `src/stepik_grader/core/wrapper_builder.py:143` | low |
| SET-1-01 | В initOnboarding() перед показом выставлять `dontShow.checked = document.body.dataset.onboardingSeen === "true"`; убрать статический `checked` из inde | `src/stepik_grader/web/static/index.html:534` | low |
| SET-2-02 | В save_settings перечитывать файл и мержить: писать не-None поля поверх дискового состояния, либо добавить save_fields(path, **changed) и звать её из  | `src/stepik_grader/cli/interactive.py:577` | low |
| TW-1-01 | Пробросить фактический timeout прогона в grade_path→_case_view (параметром вместо CONFIG) и отдавать его; либо, если поле означает серверный дефолт, п | `src/stepik_grader/web/viewmodels.py:310` | low |

---

## 8. б) Быстро достижимые цели

Всего быстрых целей — 373. Полный перечень в § 5; ниже распределение по зонам и топ-45.

| Зона | Быстрых целей |
|---|---|
| ядро | 100 |
| документация | 47 |
| фронтенд | 47 |
| прочее | 37 |
| веб-сервер | 36 |
| CLI | 29 |
| guard-скрипты | 25 |
| тесты | 18 |
| глоссарий | 16 |
| CI | 12 |
| песочница | 6 |

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| DEV-2-01 | Матчить по именам, реально присвоенным тест-блоком: `if all(_n in _assigned for _n in _param_names)` (_assigned уже считает _assigned_names), а _local | `src/stepik_grader/core/wrapper_builder.py:28` | high |
| DEV-3-01 | В варианте А писать через общий путь: скачать оба файла в память, затем `_reset_tests_dir(tests_dir)` (или прогнать через `write_testblock_tests` посл | `src/stepik_grader/core/test_source_fetcher.py:237` | high |
| ADD-1-01 | Обернуть вызов тем же блоком, что в downloader_adapter.py:185-190 (RequestException → сетевое сообщение, OSError → «не удалось сохранить токен») и вын | `src/stepik_grader/web/reference_adapter.py:64` | medium |
| ADD-1-03 | Передавать `queue_path=self.server.workspace / CONFIG.glossary_missing_queue` в api_routes:264/486 либо ввести резолвер `default_missing_queue_path()` | `src/stepik_grader/web/glossary_adapter.py:350` | medium |
| ADD-2-05 | Перед записью сверять _normalize_code с уже существующими task{N}_{100+}.py и пропускать совпадения; refs_meta мержить с прежним meta['stepik_referenc | `src/stepik_grader/core/stepik_reference.py:225` | medium |
| ADD-2-06 | Если строк с 3+ ячейками нет, а строки из 2 есть — трактовать их как (вход, выход). Сбрасывать _in_th также на <tr>/<td>/</tr>, чтобы незакрытый загол | `src/stepik_grader/core/task_page_parser.py:128` | medium |
| ADD-3-01 | Восстановить отступы в examples у 209 карточек скриптом-нормализатором (после строки на ':' добивать 4 пробела до дедента), затем закрепить compile()  | `src/stepik_grader/glossary/data/module.json:7595` | medium |
| ADD-4-01 | Перед сбором сверять origin рабочей копии с REPO_URL; при несовпадении отдавать только хеш (git rev-parse --short) без subject либо None. | `src/stepik_grader/core/feedback.py:332` | medium |
| ADD-4-03 | В diag_log добавить паттерн (?:key)\s*=\s*(['\"])(?:(?!\1).)*\1 — присваивание/kwarg с кавычками; тест на repr-форму без двоеточия. | `src/stepik_grader/core/diag_log.py:79` | medium |
| ADD-5-03 | Добавить кейсы формата 2: (а) input_N без expected_N — ожидать предупреждение, а не тихий пропуск; (б) input_03+expected_03 — кейс должен загрузиться; | `src/stepik_grader/core/test_loader.py:205` | medium |
| ARCH-3-03 | Взять write-lock до проверки версии: в _migrate выполнить BEGIN IMMEDIATE, перечитать user_version уже под ним и только тогда мигрировать/бэкфиллить,  | `src/stepik_grader/core/history.py:354` | medium |
| AUD-2-02 | Добавить `:root:not([data-theme])` третьим селектором в `_THEME_SELECTORS` (тема `dark-auto`) и отдельной проверкой требовать равенства множества имён | `scripts/check_contrast.py:38` | medium |
| AUD-3-01 | Пройти §5/§7 и проставить состояние (номер PR / отклонена с причиной), сверяясь с CHANGELOG [Unreleased]; затем либо оставить в docs/audit/ только отк | `docs/archive/audit-2026-07-30-full-roles.md:21` | medium |
| COM-1-01 | Завести один реальный приватный канал (почта мейнтейнера или приватная форма GitHub) и указать его и в русском разделе, и в блоке «In English»; убрать | `CODE_OF_CONDUCT.md:43` | medium |
| COM-1-05 | Дать в Способе A ветку для Linux: `sudo apt install pipx` / `sudo dnf install pipx` (или `pip install --user --break-system-packages pipx`) с одной ст | `docs/use/installation.md:40` | medium |
| DATA-1-03 | Сделать default=BUNDLED_GLOSSARY_DIR для --cards, а сравнение с пустой базой вынести в отдельный флаг (--no-cards). Заодно снять предупреждение-костыл | `src/stepik_grader/glossary/coverage.py:252` | medium |
| DES-1-01 | В @media (max-width:768px) снять overflow:hidden с .main/.main-view и убрать height:auto/overflow:auto у .app-shell, отдав прокрутку документу: body{o | `src/stepik_grader/web/static/app.css:686` | medium |
| DES-1-02 | В блок prefers-reduced-motion добавить `animation-iteration-count:1!important; animation-delay:0ms!important;` и `html{scroll-behavior:auto}`. Провери | `src/stepik_grader/web/static/app.css:215` | medium |
| DEV-1-01 | Прокинуть had_failures наружу: main() возвращает int (0/1), __main__.py и grader.py — `raise SystemExit(main())`; console script уже делает sys.exit(m | `src/stepik_grader/cli/__init__.py:448` | medium |
| DEV-1-02 | Перенести обработку --sandbox выше проверки `args.mode is None` (тогда меню тоже под SandboxRunner), либо явно `parser.error(...)`, если --sandbox пер | `src/stepik_grader/cli/__init__.py:621` | medium |
| DEV-2-06 | Повторить схему runner.py/tracer.py: tempfile.mkdtemp(prefix='stepik-bench-') → записать bench.py внутрь → передать этот путь в RunSpec → shutil.rmtre | `src/stepik_grader/core/microbench_runner.py:257` | medium |
| DEV-3-02 | Обернуть обе сетевые ветки в тот же `except (requests.RequestException, ExternalUrlRejected)` и вернуть 0; писать на диск только после успешного получ | `src/stepik_grader/core/test_source_fetcher.py:239` | medium |
| DEV-3-04 | Крутить `handle_request` в цикле до дедлайна, выходя только когда получен code/error по нужному path и с верным state; таймаут считать по монотонным ч | `src/stepik_grader/core/stepik_client.py:449` | medium |
| ED-2-03 | Добавить в _CATALOG_CALL_NAMES имена "t" и "_t" и починить всплывшие расхождения; закрепить тестом в tests/test_check_locale_guardrails.py. | `scripts/check_locale_guardrails.py:54` | medium |
| FE-3-01 | В .then/.catch ветке при отсутствии карточки скрывать #glossary-detail-content, показывать #glossary-empty с текстом «карточка не найдена» (ключ локал | `src/stepik_grader/web/static/content.js:365` | medium |
| LNCH-3-01 | Резолвить record_history для --serve той же лестницей, что и меню: явный --history/--no-history → UserSettings → CONFIG. Вынести лестницу в одну функц | `src/stepik_grader/cli/__init__.py:615` | medium |
| LNCH-3-02 | Сделать UserSettings полноправным слоем между CLI-аргументами и GraderConfig для всех входов, а не только меню; зафиксировать лестницу в docs/dev/arch | `src/stepik_grader/cli/options.py:353` | medium |
| LNCH-5-01 | Один резолвер выбора: приоритет «явный флаг → user-state → CONFIG» в cli/options.py рядом с _resolve_record_history (options.py:353), звать из обеих т | `src/stepik_grader/cli/__init__.py:615` | medium |
| MET-1-01 | Печатать вход кейса (а для TLE — и expected/actual) ДО ветки error, а не после: перенести строку `Input:` выше `if result.error:` и убрать ранний retu | `src/stepik_grader/core/reporter.py:591` | medium |
| MET-1-07 | В `resolve_error_hint` отбирать только карточки с `card.kind == "exception"` (и, если поле пустое, требовать суффикс error/exception в id) — иначе воз | `src/stepik_grader/core/error_glossary.py:103` | medium |
| OPS-1-02 | В save_json прогонять сериализованный JSON через diag_log.redact перед write_text (payload → json.dumps → redact → запись). Добавить тест: словарь с a | `src/stepik_grader/diagnostic_stepik.py:165` | medium |
| OPS-1-08 | Перед очисткой переносить прежнее содержимое в tests.bak/ либо удалять только файлы известных генерируемых шаблонов (N, N.clue, N.type, input.txt, out | `src/stepik_grader/core/tests_writer.py:64` | medium |
| PERF-1-01 | Добавить в запись отпечаток окружения (timeout, runner/sandbox-backend, sys.version_info) и сравнивать его в get() наравне с хешами; при расхождении — | `src/stepik_grader/core/cache.py:114` | medium |
| PERF-1-02 | Разделить прогоны: сначала timeit.repeat без tracemalloc (тайминги), затем отдельный короткий прогон stmt под tracemalloc ради строки MEM:. Парсер std | `src/stepik_grader/core/microbench_runner.py:197` | medium |
| PKG-1-01 | Добавить в [tool.setuptools_scm] `fallback_version = "0.0.0"` (или документировать SETUPTOOLS_SCM_PRETEND_VERSION) и тест, собирающий пакет из git-арх | `pyproject.toml:157` | medium |
| PROD-2-01 | Считать ключ от стабильного якоря, а не от cwd: база истории знает свой корень — брать base = каталог найденной .grader_history.db (или, для user-базы | `src/stepik_grader/core/history.py:683` | medium |
| PROD-2-02 | Обернуть обе ветки purge_history в try/except (sqlite3.Error, OSError): при сбое подсчёта отдавать removed=0 и всё равно удалять файл + -wal/-shm; для | `src/stepik_grader/core/history.py:710` | medium |
| PROD-2-05 | Строить карточки по окну прогонов ТОЙ ЖЕ задачи (read_recent_runs(task_key=...)) и агрегировать карточки по задачам, либо явно документировать окно ка | `src/stepik_grader/core/insights.py:317` | medium |
| PY-1-01 | Нормализацию по float применять только когда число десятичных знаков у факта не МЕНЬШЕ, чем у ожидания (иначе форматная задача теряет требование), либ | `src/stepik_grader/core/grader_core.py:443` | medium |
| PY-2-01 | Считать лимит как max(ceil(spec.timeout), ceil(CONFIG.sandbox_max_cpu_seconds)) — конфиг остаётся полом, но CPU-квота никогда не жёстче wall-clock тай | `src/stepik_grader/core/sandbox/_linux.py:110` | medium |
| PY-3-01 | Добавить в запись кэша поле exec_fingerprint (runner-класс, timeout_seconds, max_output_bytes, max_memory_mb, encoding, версия пакета) и сверять его в | `src/stepik_grader/core/cache.py:127` | medium |
| PY-3-02 | Обернуть тело save() в try/except OSError с тихим пропуском (как _load и stats.record_run), опционально одноразовое предупреждение через _console; выз | `src/stepik_grader/core/cache.py:175` | medium |
| QA-1-04 | Кейс в test_output_comparison.py с маленьким max_output_bytes: утверждать, что вердикт отражает обрезку (отдельный признак/непустой error), а не голый | `src/stepik_grader/core/grader_core.py:437` | medium |
| REL-2-04 | Убрать fail_under из [tool.coverage.report] и задавать порог явным `--cov-fail-under=85` в CI-шаге и в combine-job'е; либо документировать `--no-cov`  | `pyproject.toml:319` | medium |
| REL-3-06 | В job build после `python -m build` добавить `twine check dist/*` и шаг, распаковывающий wheel и проверяющий ключевые package-data (vendor/*.mjs с тем | `.github/workflows/release.yml:81` | medium |

---

## 9. в) Среднесрочные цели

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| STR-5-01 | Вернуть из загрузчика список пропущенных пар (имя файла + причина), добавить в SolutionResult поле `skipped`, печатать его в отчёте и запрещать статус | `src/stepik_grader/core/test_loader.py:205` | high |
| ARCH-3-04 | Привязать настройки к тому же скоупу, что и БД: ~/.stepik-grader/settings.json с fallback на существующий .grader_settings.json в cwd (как find_existi | `src/stepik_grader/core/user_settings.py:76` | medium |
| DEV-2-02 | Ограничивать трейс по ОБЪЁМУ, а не только по числу шагов: копить длину сериализованного шага и выставлять truncated при исчерпании бюджета (~0.5×max_o | `src/stepik_grader/core/tracer.py:218` | medium |
| LNCH-3-04 | Ввести единое понятие «корень настроек» (workspace) и резолвить от него pyproject, .grader_settings.json и базу истории. Нужен дизайн: порядок cwd vs  | `src/stepik_grader/config.py:281` | medium |
| PROD-1-02 | Резолвить .grader_settings.json тем же порядком, что и историю: env → CONFIG → существующий файл вверх по дереву → ~/.stepik-grader/settings.json; cwd | `src/stepik_grader/core/user_settings.py:74` | medium |
| PY-1-04 | Добавить в SolutionResult поле с диагностикой загрузки (список проблем тест-каталога) и показывать его в CLI/web рядом со счётчиком; warnings.warn ост | `src/stepik_grader/core/test_loader.py:173` | medium |
| RUN-3-03 | Выполнять блокирующие auth/network-джобы вне пула проверок (отдельный executor/поток), не пускать вторую auth-джобу (409 или переиспользование текущей | `src/stepik_grader/web/runs.py:372` | medium |
| STR-2-02 | Убрать --cov из addopts (включать флагами в CI-командах), чтобы точечный локальный прогон не падал по гейту. scripts/ вынести в отдельный coverage-кон | `pyproject.toml:245` | medium |
| ADD-3-07 | Добавить в _check_invariants: (1) compile('\n'.join(card.examples)) без ошибки; (2) сверку stdout для строк print(...) # → X в subprocess с allow-list | `scripts/audit_glossary_cards.py:62` | low |
| ADD-4-04 | Считать бюджет по длине percent-encoded представления (len(quote(value))), а в truncated возвращать пару (поле, сколько символов осталось из скольких) | `src/stepik_grader/core/feedback.py:396` | low |
| ADV-1-06 | Завести docs/assets/press/ с тремя форматами: (1) пост-релиз из блока «Главное» CHANGELOG; (2) клип ≤60 с — сид уже есть, hero-serve.gif; (3) карточка | `docs/assets:1` | low |
| ARCH-1-03 | Построить второй граф по ленивым рёбрам и прогнать по нему тот же детектор циклов с явным allowlist задокументированных разрывов; в architecture.md ут | `tests/test_import_dag.py:85` | low |
| ARCH-1-05 | Разложить на вложенные секции (ai/sandbox/web/history) с плоскими алиасами для совместимости и ввести явный контекст-оверрайд (dataclasses.replace как | `src/stepik_grader/config.py:41` | low |
| ARCH-1-07 | Ввести frozen-dataclass GradeOptions (timeout/max_memory_mb/callbacks/cancel_event) одним параметром, оставив текущие kwargs deprecated-обёрткой — тог | `src/stepik_grader/core/grader_core.py:540` | low |
| ARCH-2-03 | Перенести оркестрацию (grade_path/grade_benchmark/grade_microbench без JSON-маппинга) в grading.py как единственную grade-поверхность; viewmodels оста | `src/stepik_grader/web/grading.py:43` | low |
| ARCH-2-06 | Разбить по доменам на несколько миксинов (runs/content/downloader/settings) с общей таблицей маршрутов, собираемой из них; `_Handler` наследует набор. | `src/stepik_grader/web/api_routes.py:114` | low |
| AUD-1-03 | Ввести в cli/ единую точку вывода (общий `_cprint`/`_console` из reporter или адаптер в CliContext) и заменить голые print. Либо явно зафиксировать ис | `src/stepik_grader/cli/interactive.py:134` | low |
| AUD-3-04 | Либо реализовать гейт по готовому образцу tests/test_web_api_contract.py:64 (там так же сверяются docs/dev/api.md и маршруты), либо явно отклонить DOC | `docs/archive/audit-2026-07-30-full-roles.md:613` | low |
| COM-1-07 | Добавить машиночитаемый экспорт (--export-progress json со схемой task_key/attempts/failure_kind) и офлайн-команду сведения нескольких таких файлов в  | `src/stepik_grader/cli/options.py:205` | low |
| DATA-1-01 | В format_report_summary печатать число просканированных модулей и пометку «выборка N из M модулей stdlib»; добавить второй показатель — долю самих мод | `src/stepik_grader/glossary/stdlib_inventory.py:63` | low |
| DES-1-04 | Выводить фактическую пару: для каждого селектора с `color:` найти ближайший объявленный background (свой или у селектора-префикса) и мерить его; _PAIR | `scripts/check_contrast.py:201` | low |
| DEV-1-08 | Либо убрать параметр-теряющую обёртку и оставить фасаду только реэкспорт имени, либо прокинуть record_history. Стратегически (issue #903): перевести т | `src/stepik_grader/cli/__init__.py:232` | low |
| FE-3-05 | Обернуть импорт редактора в динамический import() внутри makeEditor с деградацией на <textarea>, либо добавить в index.html inline-обработчик window.e | `src/stepik_grader/web/static/index.html:614` | low |
| LNCH-3-03 | Дешёво: заменить импорт CONFIG на get_config() в точке использования во всех модулях (один PR) и запретить module-level снимки линт-правилом. Дорого:  | `src/stepik_grader/core/test_loader.py:39` | low |
| LNCH-5-03 | Добавить launch_profiles: dict[str, dict] + last_profile в UserSettings и флаг --profile NAME в options.py. load_settings (user_settings.py:108-117) у | `src/stepik_grader/core/user_settings.py:70` | low |
| LNCH-5-04 | Расширить whitelist в api_routes.py:756 на record_history и читать флаг per-request через read_settings (кэш по mtime вместо глобали viewmodels.py:447 | `src/stepik_grader/web/api_routes.py:756` | low |
| LNCH-5-07 | Первый запуск (по аналогии с onboarding_seen, user_settings.py:73) показывает один экран: терминал / браузер / браузер с изоляцией; выбор в user-state | `src/stepik_grader/cli/options.py:40` | low |
| MET-1-08 | Добавить постфильтр ответа: если в тексте кодовый блок длиннее N строк (или совпадает по структуре с ctx.code), сворачивать его в одну строку-намёк ли | `src/stepik_grader/core/ai_hints.py:313` | low |
| PERF-1-06 | Для прошедших кейсов не отдавать stdin/actual (тянуть по клику) либо усекать до N байт; на фронте — делегирование кликов на #out и ленивый casesHtml п | `src/stepik_grader/web/viewmodels.py:772` | low |
| PROD-1-07 | Локально (без сети) отмечать в .grader_settings.json first_run_at / first_grade_at / first_pass_at и показывать «время до первого зачёта» в разделе «П | `src/stepik_grader/core/history.py:272` | low |
| PROD-2-06 | Учитывать прогон в attempts/серии только если solution_hash отличается от предыдущего для этого task_key (данные уже в БД); либо считать серию по уник | `src/stepik_grader/core/insights.py:259` | low |
| PROD-2-07 | Добавить в отчёт агрегат по неделям (GROUP BY strftime('%Y-%W', ts_utc)): доля падающих прогонов и медиана duration_s по неделям + спарклайн в HTML. К | `src/stepik_grader/core/progress_export.py:94` | low |
| PY-2-05 | Вызывать _kill_process_tree(proc) безусловно после выхода из цикла (на Linux это no-op) и закрывать proc.stdout/stderr перед join, чтобы читатели гара | `src/stepik_grader/core/sandbox/_posix_common.py:209` | low |
| PY-2-07 | Суммировать cpu_times по дереву (по аналогии с sample_tree_rss) и/или выставлять PerJobUserTimeLimit с флагом JOB_OBJECT_LIMIT_JOB_TIME — квота Job'а  | `src/stepik_grader/core/sandbox/_windows.py:209` | low |
| PY-3-03 | Ввести stats_path-резолвер по образцу history_recording.default_history_db_path(): env-override → CONFIG-поле → существующий .grader_stats.jsonl рядом | `src/stepik_grader/core/stats.py:57` | low |
| QA-3-07 | Добавить `--sandbox` в corpus_run.py (SandboxRunner активным до прогона, как в web --serve --sandbox) и сверять вердикты с несандбоксным прогоном; нед | `scripts/corpus_run.py:194` | low |
| REL-1-07 | Либо добавить в verify те же guardrail-скрипты (чистый stdlib, секунды), либо перед публикацией проверять заключение CI-прогона для GITHUB_SHA (`gh ru | `.github/workflows/release.yml:24` | low |
| REL-2-07 | Добавить второй блок updates с `package-ecosystem: "pip"`, `directory: "/"`, месячным расписанием, `versioning-strategy: widen` и группировкой в один  | `.github/dependabot.yml:11` | low |
| SEC-1-05 | Развести: согласие — только через явный эндпоинт настроек; `/api/v1/hint` только читает флаг и отвечает 403 `consent_required`, пока он не выставлен. | `src/stepik_grader/web/api_routes.py:846` | low |
| SEC-3-04 | Резолвить журнал статистики и каталог диагностики так же, как history_db_path (#818): файл рядом, иначе ~/.stepik-grader/. purge_stats — обходить проф | `src/stepik_grader/core/stats.py:57` | low |
| SET-3-08 | Дать полям GraderConfig metadata (surfaces={'cli': '--timeout', 'web': True/False}, doc=...), из неё генерировать таблицу configuration.md и справку;  | `src/stepik_grader/config.py:45` | low |
| SET-5-05 | Группа «Локальные данные»: «Очистить недавние пути» и «Сбросить настройки интерфейса» (localStorage, quick), плюс размер .grader_history.db и «Очистит | `src/stepik_grader/web/static/grade.js:1483` | low |
| STR-1-01 | Удалить scripts/corpus_*.py, tests/test_corpus_*.py, corpus/, docs/dev/corpus.md (~2860 строк). Теряется ручной стенд сверки вердиктов и каталог мутац | `corpus/README.md:1` | low |
| STR-1-02 | Удалить core/tracer.py, tests/test_tracer.py, web/static/trace-player.js, trace-job в web/runs.py:651, trace-ветку sandbox.js, docs/dev/trace-format.m | `src/stepik_grader/core/tracer.py:383` | low |
| STR-1-03 | Свернуть режим 4 в 3: удалить core/microbench_runner.py, tests/test_microbench_grader.py, флаг --number (options.py:86), microbench-ветки web. Теряетс | `src/stepik_grader/core/grader_core.py:812` | low |
| STR-1-04 | Удалить core/stepik_reference.py, web/reference_adapter.py, tests/test_stepik_reference.py, --import-reference/--import-top (~500 строк). Теряется сра | `src/stepik_grader/core/stepik_reference.py:7` | low |
| STR-1-05 | Свернуть эти 9 флагов в подкоманду `stepik-grader admin <...>`, оставив в корне только флаги проверки. Теряются короткие имена — нужен один релиз с de | `src/stepik_grader/cli/options.py:65` | low |
| STR-1-06 | Удалить --watch, extra [watch], _watch_and_rerun/_dispatch_with_watch, параметр incremental и test_watch_incremental.py. Теряется авто-перезапуск — за | `src/stepik_grader/cli/options.py:323` | low |
| STR-1-07 | Удалить ide.py, tests/test_ide.py, --init-vscode и ключи локали; готовый tasks.json вставить блоком в docs/use/grader-workflow.md:277 (PyCharm там уже | `src/stepik_grader/ide.py:49` | low |
| STR-2-01 | Перенести ruff/mypy/check_ruff_pin/check_version_consistency в быстрый stdlib-job (docs-guardrails → `static`), матрице оставить pytest. Добавить conc | `.github/workflows/ci.yml:105` | low |
| STR-2-04 | Добавить в scripts/check_docs_guardrails.py (уже ходит по докам) двустороннюю сверку: каждый .py из src/stepik_grader упомянут в дереве, каждое упомян | `docs/dev/project-structure.md:110` | low |
| STR-2-05 | Фрагменты вместо общего файла: одна запись = changelog.d/<PR>.<тип>.md, склейка при релизе (towncrier-подход) — конфликты исчезают, а наличие записи с | `CHANGELOG.md:1` | low |
| STR-3-02 | Записать фикстуры реальных ответов (санитизированные) + отдельный weekly-job `continue-on-error` в ci.yml, который бьёт по живому Stepik по публичному | `tests/test_stepik_client.py:315` | low |
| STR-3-05 | Задокументировать схему task_dir/meta.json в docs/use/configuration.md как открытый контракт (Stepik-поля — опциональные) и добавить `stepik-grader -- | `src/stepik_grader/downloader.py:336` | low |
| STR-3-06 | Читать хост через `_api_host()` с приоритетом env `STEPIK_GRADER_API_HOST` → константа-дефолт; в diagnostic_stepik импортировать модуль (`from ...core | `src/stepik_grader/core/stepik_client.py:85` | low |
| STR-4-01 | Либо вызвать stats.record_run из web/runs.py по завершении прогона (лок уже под это заведён), либо убрать ложное утверждение из комментария и явно зад | `src/stepik_grader/core/stats.py:47` | low |
| STR-4-02 | Добавить в запись тальи failure_kind (уже вычисляются рядом при записи истории) и строку разбивки в print_stats_summary; либо снять из докстринга обещ | `src/stepik_grader/core/stats.py:124` | low |
| STR-4-03 | Перенести на ту же схему, что history_db_path: поле stats_path в GraderConfig, авторезолв вверх по дереву, дефолт ~/.stepik-grader/stats.jsonl с сохра | `src/stepik_grader/core/stats.py:57` | low |
| STR-4-05 | Показать сводку статистики в веб-разделе «Прогресс» и добавить в меню тумблер по образцу пункта 7 (история), с той же формулировкой «только локально,  | `src/stepik_grader/web/static/index.html:69` | low |
| STR-5-02 | Прокинуть outcome.stdout (декодированный, обрезанный) в _fail_result для TLE/RE как `output` и явно помечать его частичным (`partial: true` или суффик | `src/stepik_grader/core/grader_core.py:232` | low |
| STR-5-04 | Добавить в SECURITY.md секцию «Что означает AC/OK (и чего не означает)» по образцу секции про SANDBOX_VIOLATION: AC = совпадение с ЛОКАЛЬНЫМИ кейсами  | `src/stepik_grader/core/reporter.py:147` | low |
| STR-5-05 | Ввести per-задачу настройку допуска сравнения (meta.json/конфиг): compare = exact\|sorted\|whitespace_insensitive, подключить уже написанные sort_line | `src/stepik_grader/core/normalizers.py:7` | low |
| TRE-1-06 | Завести минимальный package.json/lock рядом с рецептом (build-time only) и подключить к dependabot npm, либо добавить в CI еженедельный osv-scanner по | `.github/dependabot.yml:11` | low |
| TW-2-04 | Либо переформулировать последствие честно (фасад покрывает только grade/bench/microbench, остальные core-импорты web — осознанно прямые), либо завести | `src/stepik_grader/web/viewmodels.py:22` | low |
| VIS-1-02 | Флаг вида --explain FILE в cli/options.py: scan_code_concepts + lookup.match_card → печать title/summary найденных карточек. Тот же путь даёт «объясне | `src/stepik_grader/glossary/lookup.py:20` | low |
| VIS-1-04 | --init-task DIR в cli/options.py: создаёт папку задачи в формате 2 (input_N.txt/expected_N.txt), заготовку решения и meta.json-заглушку; в README — ра | `src/stepik_grader/cli/options.py:58` | low |
| VIS-2-04 | Завести группу `stepik_grader.runners` (позже `stepik_grader.platforms`), грузить её через importlib.metadata.entry_points при старте CLI/web, описать | `pyproject.toml:126` | low |
| VIS-2-06 | Дать run_single_test/run_spec необязательный параметр runner (глобал — дефолт) и дописать в ADR-0006 требования к реализациям: потокобезопасность run( | `src/stepik_grader/core/runner.py:783` | low |

---

## 10. г) Долгосрочные цели и идеи

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| STR-2-03 | Свести гарды к одному входу (scripts/guards.py с подкомандами + реестр проверок) и одному параметризованному тест-файлу. В ci.yml остаётся один шаг вм | `.github/workflows/ci.yml:13` | low |
| STR-3-07 | Не абстрагировать платформы заранее (отклонялось). Дешёвая заготовка: ADR, фиксирующий план пивота — нейтральный alias-console-script рядом с текущим, | `docs/use/versions.md:64` | low |
| VIS-1-05 | Связать три готовых куска: ключ карточки → карточка глоссария (lookup) → мини-дрилл с эталонными тест-кейсами; статус active/fading задаёт очередь пов | `src/stepik_grader/cli/__init__.py:556` | low |
| VIS-2-07 | Ввести протокол Platform (auth → fetch step → materialize task dir) над уже нейтральным test_loader; Stepik — первая реализация, downloader и web-адап | `src/stepik_grader/core/stepik_client.py:85` | low |

**Сверх таблицы — четыре стратегических вывода, которых в аудите не было до волны критиков:**

1. **Аудит не умел вычитать.** На 167 предложений «добавить» приходилось 26 «убрать», и все 26 — микроправки.
   Срез `STR-1` назвал кандидатов на вывод из продукта: прогонный корпус (~2860 строк стенда при нуле данных
   в git и нуле упоминаний в CI), пошаговый трейс (~1580 строк ради витрины, недоступной под `--sandbox`),
   два режима бенчмарка на один вопрос, `--import-reference` как скрейпер недокументированного API.
2. **Цена владения не считалась ни разу.** 31 флаг в корневом `--help`, из них 9 — служебные «сделать и
   выйти»; каждый новый guard стоит четырёх артефактов (скрипт, шаг CI, тест, абзац документации);
   `CHANGELOG.md` — самый горячий файл репозитория и обязательная точка конфликта каждого PR.
3. **Зависимость от Stepik — риск, а не только код-шов.** Нет канарейки дрейфа API: все тесты сетевого слоя
   на моках, поэтому зелёный CI ничего не говорит о работоспособности с живым Stepik. Каталог задачи может
   создать только загрузчик — офлайн-способа завести задачу нет, хотя «generic mode» существует и о нём не
   сказано в русском README.
4. **Этика вердикта.** Отчёт печатает «OK» как суждение о решении, хотя знает лишь «прошли N локальных
   кейсов». При подтверждённых восьми дефектах ложного вердикта это утверждение сильнее, чем позволяют данные.

---

## 11. Запуск: почему пользователь не выбирает, как стартовать

Раздел заведён по прямому запросу владельца. Пять срезов (`LNCH-1`…`LNCH-5`) отвечают на вопрос целиком.

**Что есть сейчас.** Три двери — консольная команда, `--serve`, GUI-лаунчер — и **ни одного экрана выбора**
(`LNCH-5-07`). Лаунчер (`launcher.py:249`) собирает команду ровно из трёх значений: порт, рабочая папка,
галка изоляции. Всё остальное — история, язык, AI, таймауты, лимиты — решается за пользователя молча.

**Что молчит.** История прогонов включается без спроса (`LNCH-1-01`); выбор «с изоляцией» **отключает
пошаговый трейс**, и в точке выбора об этом ни слова (`LNCH-1-02`); `--serve` **съедает**
`--mode/--file/--stats/--lint/--ai-hints/--output/--cache/--watch` без предупреждения (`LNCH-2-04`) и
**игнорирует** сохранённый опт-аут истории (`LNCH-3-01`, `SET-2-03`); веб-онбординг обещает галку sandbox,
которой в лаунчере нет (`LNCH-1-07`).

**Почему «перезапустите сервер».** Не потому, что так задумано: **`CONFIG` связывается на импорте в двадцати
модулях** (`LNCH-3-03`) — это и есть блокер перечитывания на ходу. Плюс конфиг якорится на текущем каталоге,
а веб-настройки — на `--root`: два разных корня настроек (`LNCH-3-04`), и три независимые цепочки приоритета
для одного флага `record_history` (`LNCH-3-02`, `SET-3-01`).

**Что делать — по возрастанию цены.**

| Шаг | Что сделать | Цена |
|---|---|---|
| 1 | Запомнить выбор лаунчера между запусками (`LNCH-5-02`) и показать в нём, что именно сейчас включится | quick |
| 2 | Честный блок «задаётся при запуске» вместо тупика «перезапустите сервер» (`SET-5-04`) | quick |
| 3 | Отказ вместо тишины: `--serve` с несовместимым флагом сообщает, что флаг проигнорирован (`LNCH-2-04`) | quick |
| 4 | Тумблер истории без перезапуска — адаптер настроек к этому готов, не хватает эндпоинта (`LNCH-5-04`) | mid |
| 5 | Профили запуска вместо россыпи флагов: сохранённый набор «как я обычно запускаю» (`LNCH-5-03`) | mid |
| 6 | Перечитывание конфигурации в рантайме — снять связывание `CONFIG` на импорте (`LNCH-3-03`) | mid |

---

## 12. Настройки: нужен ли раздел и чем его наполнить

Раздел заведён по прямому запросу владельца: «они пустые, есть ли смысл».

**Факт.** Секция `view-settings` (`index.html:462`) содержит **ноль интерактивных элементов** (`SET-1-02`,
`SET-4-01`): панель «Интерфейс» и абзац о состоянии истории со ссылкой на CLI-флаг. На весь раздел приходится
пять ключей локали. Заголовок панели не описывает даже это единственное содержимое (`SET-1-03`, `SET-5-06`).

**Смысл есть — но наполнять его надо тем, что пользователь уже вынужден решать:**

| Что | Почему это настройка | Находки |
|---|---|---|
| **Отзыв согласия на AI** | Дать согласие из веба можно, отозвать — только из CLI, и то лишь в текущей папке | `SET-1-04` `SET-2-04` `SET-5-02` `SEC-3-02` |
| **Тумблер истории** | Единственное, что раздел показывает, — и единственное, что нельзя изменить | `SET-2-03` `SET-3-04` `LNCH-4-03` |
| **Строгость сравнения вывода** | Решение принимается каждый прогон, а управления им нет нигде | `SET-4-02` `STR-5-05` |
| **Таймаут и лимит памяти** | Задаются только правкой `pyproject.toml`, **которого у пользователя pipx попросту нет** | `SET-3-03` |
| **Язык интерфейса** | Не закрепляется ни на одной поверхности — только флаг и query-параметр | `SET-3-07` `LNCH-2-05` |
| **Сброс локальных данных** | 8+ ключей `localStorage` и история копятся без единой точки очистки | `SET-5-05` |

**Что убрать из настроек.** `ai_system_prompt` позволяет **стереть запрет «не выдавай готовый код»**
(`SET-4-04`) — настройка, прямо вредящая методике. Пороги затухания карточек «Подучить» нужно объяснять, а не
давать настраивать (`SET-4-05`).

**Попутный дефект той же зоны:** веб-прогоны **вообще не попадают в журнал статистики** — `record_stats` в
`web/` не используется ни разу (`SET-3-05`, `STR-4-01`), хотя документация утверждает обратное.

---

## 13. Разбор стороннего аудита

Владелец передал аудит того же проекта, сделанный другой системой: 40 находок, 4 среза, чтение кода и
CI-истории. Все 40 проверены по коду десятью агентами-скептиками двумя волнами.

| Итог | Значение |
|---|---|
| Вердиктов | 42 (40 находок + 2 пункта их списка «быстрых целей») |
| CONFIRMED | 24 |
| PARTIAL (факт верен, следствие преувеличено) | 18 |
| REFUTED | **0** |

**Сторонний аудит фактически точен — но систематически завышает последствия.** Типичные поправки: окружение
наследуется **не целиком**, секретные имена вырезаются (`SEC-03`); «заявляют owner-only» опровергнуто —
оба докстринга прямо фиксируют ограничение Windows как известное (`SEC-07`); два экземпляра `run_server()`
в одном процессе в продукте недостижимы (`WEB-03`); `aria-pressed` уже сквозной паттерн, включая `grade.js`.

**Его уникальный вклад — одна находка, которой не было у нас:** `CORE-01`, задвоение при параллельной
миграции схемы истории (`core/history.py`), подтверждено как `high` и оказалось **хуже описанного** —
достижимо **внутри одного процесса** через read-путь веб-сервера на `ThreadingHTTPServer`, а не только между
двумя процессами. Эта находка включена в работу наравне с нашими.

**Чего у него нет.** Ни одного прогона продукта — отсюда ноль находок класса «неверный вердикт», то есть
ровно того, что составило все восемь наших `high`. Полный текст стороннего аудита сохранён вне репозитория.

---

## 14. Приговор критиков полноты

Пять критиков проверяли не код, а **сам аудит**. Их выводы приведены полностью, потому что они и определили
две дополнительные волны.

1. **Метод.** След реального прогона несли 44 находки из 424; песочница **ни разу не запускалась**
   (bubblewrap в контейнере нет, 65 тестов пропущено), веб **ни разу не открывался в браузере**, качество
   тестов не измерялось — ни ветвевого покрытия, ни мутаций. Скептики усилили тот же перекос: перечитывали
   код, а не воспроизводили.
2. **Охват.** Из 406 файлов репозитория в находках упомянут 141; **252 файла не открыл никто**. Слепые зоны
   совпали с границами системы: контент глоссария, сетевой слой Stepik, адаптерный слой веба, 129 из 138
   файлов `tests/`, фича «Обратная связь» целиком.
3. **Роли.** Каждому срезу заранее выдали 5-6 файлов, и вопрос роли превратился в «прочитай их и выдай
   находки с `file:line`». Роли, чей вопрос не привязан к файлу, получили по одному срезу; обязательные
   вопросы четырёх ролей не были заданы вовсе.
4. **Доказательность.** Итог верификации не был применён — 157 полуопровергнутых находок лежали в одном пуле
   с подтверждёнными; дедупликация схлопнула 5 записей при 30 коллизиях `file+line`.
5. **Стратегия.** 345 быстрых целей против 2 долгосрочных: протокол дефектов, а не стратегия.

**Что сделано по их указаниям — прямо в этом аудите:**

- дозапущена волна слепых зон (`ADD-1`…`ADD-5`) → **4 новых `high`**, включая 209 несобирающихся карточек
  глоссария и отравление `secrets.json`;
- дозапущена стратегическая волна (`STR-1`…`STR-5`) → 68 среднесрочных целей вместо прежних двух;
- вердикты **применены** к находкам: у каждой теперь статус и итоговая severity, отбраковано 12;
- коллизии `file:line` сведены и указаны как независимые подтверждения (§ 3);
- три главные находки перепроверены **вручную владельцем аудита**: воспроизведены `NO TESTS` при
  `input_02.txt`, `OK` при выброшенном проверяющем кейсе и `RE: function_name not found` на верном
  stdin-решении формата 3.

**Что осталось непроверенным и вынесено в фазы 2 и 3** — § 16.

---

## 15. Серверный пласт → #59

Отдельных серверных находок этот аудит не дал: все 70 срезов работали по локальной версии, как и задано
стратегическим контекстом. В roadmap **#59** дописываются следствия стратегических срезов, которые **не
решаются локально**:

- **канарейка дрейфа Stepik API** (`STR-3-02`) — на локальной машине её негде держать: нужен регулярный
  прогон против живого API, то есть расписание на сервере;
- **офлайн-способ завести задачу без загрузчика** (`STR-3-05`) в серверном варианте превращается в каталог
  задач, а не в локальный мастер;
- **переопределяемый `API_HOST`** (`STR-3-06`) — предпосылка и для тестового стенда, и для серверного
  развёртывания;
- **сбор данных об использовании** (`STR-4-05`) — локально возможен только как opt-in журнал; всё, что
  требует агрегации по пользователям, — серверное по определению;
- ранее сведённые в #59 блоки (контейнерная песочница, очередь, мультитенантность) остаются в силе.

---

## 16. Незакрытые пробелы

Честно фиксируются, чтобы непроверенное не растворилось (правило § 10 прошлого аудита).

| Пробел | Почему не закрыт | Чем закрывается |
|---|---|---|
| Песочница ни разу не запускалась | В контейнере нет bubblewrap; 65 тестов пропущено по этой причине | Фаза 2, волна «Паритет песочницы» |
| Веб не открывался в браузере: JS ни разу не исполнялся | Все находки `FE-*`, `SET-*`, `LNCH-4` получены чтением | Фаза 2, headless Chromium (Playwright в окружении есть) |
| Качество тестов измерено выборочно | Ветвевое покрытие и мутационное тестирование не прогонялись | Фаза 2, волна «Матрица вердиктов» |
| Загрузчик и OAuth против живого Stepik | Сети к Stepik в окружении аудита нет | Нужен тестовый токен либо записанные ответы API как фикстуры |
| Документы как система: связи, дубли, противоречия | Проверялись поодиночке, а противоречия видны только между файлами | Фаза 3 целиком |
| Прогонные агенты сорят в рабочем каталоге | Замечено при уборке: 13 файлов в корне репозитория после волны прогона | Отдельная находка: то же случится у пользователя, запускающего проверку из папки проекта |

---

## 17. Что дальше

1. **Эпик и подэпики** заведены по риск-группам § 2; issue — на находки `bug`, `high` и `medium`.
2. **Фаза 2** — сверхобъёмный аудит на работающем коде: сквозные путешествия, матрица вердиктов, фаззинг,
   конкурентность, долгая сессия, паритет песочницы, установка, стратегия, критики новизны.
3. **Фаза 3** — документация как система: карта связей, противоречия по существу, канон и дубли, путь
   читателя, критики полноты.
4. Результаты фаз 2 и 3 дописываются **в этот же документ** отдельными разделами — правило «один аудит, один
   файл» не нарушается.
