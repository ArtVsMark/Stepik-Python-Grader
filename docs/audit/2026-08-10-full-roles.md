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
- **Состояние находки** — «открыта», если её ID не значится в § 5-квater «Закрытые находки: реестр PR».
  Реестр ведётся одним списком, а не пометками в таблицах: одна находка попадает в несколько срезов, и
  «✅» у одной строки врал бы про остальные. Когда закрыты или отклонены все, документ целиком переезжает
  в [`../archive/`](../archive/README.md) (правило [CLAUDE.md § Открытая работа](../../CLAUDE.md)).

---

## 1. Резюме

Проект инженерно зрелый: `pytest` зелёный (3071 тест, 65 пропущено), покрытие 90.21%, `ruff` и `mypy` чисты,
прошлый аудит (2026-07-30, 192 находки) разобран полностью — все 11 подэпиков **#771** закрыты. Поэтому
находки этого прогона сконцентрированы не в «грязном коде», а в **пяти системных местах**.

1. **Грейдер выносит неверный вердикт, и виноват входной слой.** Из 16 подтверждённых `high` **девять** —
   в `test_loader.py` и `mode_detector.py`, ещё два в `wrapper_builder.py`. Формат 2 молча выбрасывает пару
   `input_02/expected_02` с ведущим нулём (`test_loader.py:204`), и если выброшенный кейс был единственным
   проверяющим, **неверное решение получает OK**. Формат 3 принимает stdin-данные вида `x = 5` за
   function-режим (`test_loader.py:187`) → **ложный RE на верном решении**. Потерянный `expected_N.txt` не
   ошибка, а тишина: отчёт показывает зелёное «OK N/N» на неполном наборе (`test_loader.py:205`).

   Механизм у всех девяти общий и назван стратегическим срезом: **три эвристических формата, ни один из
   которых не объявляет размер набора**. Поэтому пропажа файла неотличима от «набора из одного кейса», а
   `total` считается по загруженным кейсам, а не по заявленным. Это делает `load_test_cases` кандидатом на
   переписывание, а не на серию заплат: плотность находок там 92.5 на KLOC против 20.8 по всему `core`.

   Метод снова решил всё: прогон продукта дал `high` **впятеро плотнее**, чем чтение кода (8.3% против 1.6%).
   Вывод повторяется третий аудит подряд.

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

6. **История обучения теряется и врёт.** Два `high` в `history.py:683`: `--purge-history <ключ>` **необратимо
   стирает историю другой задачи**, потому что ключ — имя папки, и одноимённые папки разных курсов сливаются;
   тот же общий ключ красит только что проваленную задачу зелёным «✅ Решено» по чужому `AC`.

7. **Часть закрытого не закрыта.** Ревизия прошлого аудита нашла десять вернувшихся дефектов. `DIVE-02`
   числилась исправленной — прогон показал, что `Anna(20)` и `H2O(l)` по-прежнему уезжают на функциональный
   маршрут. `PY-03` закрыта в `mode_detector.py`, но не в `test_loader.py`; `SECD-02` — в CLI, но не в вебе;
   `DESC-02` — в ветке `WA`, но не в `[ERROR]`. Общий шаблон: **фикс применён к одному вызову из нескольких**.
   Отдельно — `release.yml:110`, где `checkout` идёт после `download-artifact` и стирает `dist/`: релиз
   уходит **без ассетов**.

**Отдельно о методе.** Аудит шёл четырьмя фазами с разными способами проверки, и каждая следующая критиковала
предыдущую. Критики полноты фазы 1 вынесли приговор «на 95% чтение кода» — по их указаниям дозапущены слепые
зоны и стратегия, давшие 4 новых `high`. Критики фазы 2 показали, что прогон покрыл **треть исполнимой
поверхности**: интерактивное меню не запускалось ни разу, браузер не открывался, из девяти комбинаций
CI-матрицы отработала одна. Ревизия фазы 4 объяснила, почему прошлый аудит с его 192 находками прошёл мимо
шести `high`: **загрузчик тест-кейсов не был зоной ни одного из тридцати срезов**, а читающие агенты проходили
в двух строках от дефектов, не заглядывая в тихие ветки.

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
| Находок всего | **932** |
| Отклонено верификаторами | 13 REFUTED + 16 DUPLICATE = 29 |
| В работу | **903** — 16 high · 165 medium · 722 low (шкала верификаторов, см. § 4) |
| Подтверждено полностью (CONFIRMED) | 585 |
| Подтверждено частично (PARTIAL) | 319 |
| Быстрых целей (`quick`) | 684 |
| Среднесрочных (`mid`) | 152 |
| Долгосрочных (`long`) | 7 |
| Дефектов текущего поведения (`bug`) | 60 |
| Срезов | 151 ролевых, зонных, прогонных, документных, ревизионных и браузерных |
| Верификаций | 933 (100% находок, ни одной без вердикта) |
| Severity скорректирован верификатором | 503 находки |
| Волн Workflow | 32, ровно по 5 агентов (доборы из 1-4); потеряно 3 агента, все добраны |

**По фазам** (метод у каждой свой, поэтому и урожай разный):

| Фаза | Метод | Находок в работе | Подтверждённых high |
|---|---|---|---|
| 1 | чтение кода, 65 срезов | 477 | 8 |
| 2 | **запуск продукта**, 9 волн | 211 | 7 |
| 3 | документы как система, 5 волн | 124 | 0 |
| 4 | ревизия аудита 2026-07-30 | 58 | 1 |
| 5 | **реальный браузер**, 5 срезов | 33 | 0 (4 подтверждённых `medium`) |

Плотность тяжёлых дефектов у прогона впятеро выше, чем у чтения: 8 high на 489 находок против 7 на 224.
Документационная фаза не дала ни одного `high` — и это не провал метода, а его честная граница:
расхождение между README и кодом не портит вердикт грейдера и не теряет данные пользователя.

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
| DEV-3-06 | `src/stepik_grader/core/stepik_client.py:546` | Обрыв сети при обновлении токена выдаётся пользователю за неверные OAuth-учётные данные | low | ✅ |
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
| QA-2-02 | `tests/e2e/test_not_silently_skipped.py:74` | Guard «набор не скипнулся целиком» считает СОБРАННЫЕ тесты, а не выполненные | low | ✅ |
| QA-2-03 | `tests/e2e/test_not_silently_skipped.py:43` | Guard запуска браузера сам скипается при сломанном окружении: фикстура срабатывает раньше проверки флага | low | ✅ |
| QA-2-04 | `tests/e2e/test_load_failures.py:91` | Тест «Повторить» не доказывает повторный запрос — фикс, только прячущий баннер, остаётся зелёным | low | ✅ |
| QA-2-05 | `tests/e2e/test_poller_resilience.py:55` | Ассерты по обеим локалям опираются на неверную посылку — язык UI детерминированно русский | low | ✅ |
| QA-2-06 | `tests/e2e/test_journeys.py:216` | Жёсткий Control+A в редакторе: e2e-набор чинён под Linux, у контрибьютора на macOS ломается молча по смыслу | low | ◐ |
| QA-2-07 | `tests/test_property.py:28` | Property-набор может исчезнуть молча: нет guard'а на hypothesis, в отличие от e2e и песочницы | low | ✅ |
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


---

## 5-бис. Находки фаз 2-4: прогон, документы, ревизия

Фаза 1 (§ 5 выше) читала код. Три следующие фазы проверяли иначе, и каждая нашла то, что предыдущему
методу недоступно по построению.

- **Фаза 2 — запуск продукта.** Агенты ставили пакет, гоняли все четыре режима, портили входные данные,
  запускали параллельные прогоны, работали без сети. Отсюда семь `high`.
- **Фаза 3 — документы как система.** Сверка пар: документ против кода и против другого документа.
  Ни одного `high` — и это честная граница метода, а не его провал.
- **Фаза 4 — ревизия аудита 2026-07-30.** Каждая находка прошлого аудита проверена по сегодняшнему коду:
  закрыта, вернулась или не трогалась. Здесь `bug` означает **вернувшийся** дефект.

Состояние всех находок ниже — «открыта», как и в § 5.

### Фаза 2 · История прогонов и журнал (запуск продукта)

Что ломается при реальном ведении истории обучения.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| JRN-4A-01 | `src/stepik_grader/core/history.py:683` | --purge-history <ключ> необратимо стирает историю ДРУГОЙ задачи: ключи разных курсов совпадают | high | ✅ |
| JRN-4B-02 | `src/stepik_grader/core/history.py:683` | Глобальная БД + task_key = имя папки: чужой AC красит только что проваленную задачу как «✅ Решено» | high | ✅ |
| JRN-1-01 | `src/stepik_grader/core/runner.py:616` | Колонка «Memory, MB» всегда 0.00: пик читается до join потока-замерщика на poll-пути | medium | ✅ |
| JRN-1-02 | `src/stepik_grader/core/test_loader.py:116` | BOM в expected_N.txt даёт WA, где Expected и Actual выглядят одинаково — тупик | medium | ✅ |
| JRN-2-01 | `src/stepik_grader/core/runner.py:759` | Memory, MB и peak_memory_mb всегда 0.00 в режимах 1/2/3: пик читается до join измеряющего потока | medium | ✅ |
| JRN-2-05 | `src/stepik_grader/core/history.py:647` | Углубление: один и тот же файл дал ТРИ строки «Прогресса» за три запуска из разных cwd | medium | ✅ |
| JRN-4A-02 | `src/stepik_grader/cli/commands.py:497` | «Прогресс» рисует одну задачу двумя строками, а соседнюю прячет, отдав ей чужой AC (углубление PROD-2-01) | medium | ✅ |
| JRN-4B-01 | `src/stepik_grader/cli/__init__.py:512` | --purge-history из любой папки стирает глобальную ~/.stepik-grader/history.db без подтверждения | medium | ✅ |
| JRN-5-04 | `src/stepik_grader/core/insights.py:316` | Углубление: карточка ZeroDivisionError с 21 попаданием гаснет от трёх прогонов другой задачи | medium | ✅ |
| JRN-1-03 | `src/stepik_grader/cli/commands.py:562` | Режим 1 принимает solution.py, режимы 2/3/4 его не видят — и сообщают строкой с сырым {path} | low | ◐ |
| JRN-1-04 | `src/stepik_grader/cli/options.py:35` | Раскладку tests/ узнаёшь только провалившись: --help молчит, подсказка зовёт в легаси-формат | low | ✅ |
| JRN-1-05 | `src/stepik_grader/cli/interactive.py:232` | «Подучить» в свежей установке показывает чужие карточки из глобальной БД, хотя пункт 7 говорит «ВЫКЛ» | low | ◐ |
| JRN-1-06 | `src/stepik_grader/core/reporter.py:257` | Шапка главной таблицы вердикта — по-английски в русском интерфейсе | low | ✅ |
| JRN-2-02 | `src/stepik_grader/core/microbench_runner.py:268` | Режим 4 молча выбрасывает корректное решение по жёсткому потолку 60 с, а режим 3 его ранжирует | low | ◐ |
| JRN-2-03 | `src/stepik_grader/cli/commands.py:510` | Четыре режима — четыре несовместимые JSON-схемы без пометки режима: --output json непарсим единообразно | low | ◐ |
| JRN-2-04 | `src/stepik_grader/core/history_recording.py:144` | --stats-summary говорит «нет данных» после десятка прогонов, а --insights тут же печатает чужие задачи | low | ◐ |
| JRN-2-06 | `src/stepik_grader/cli/commands.py:562` | Режим 1 грейдит файл с любым именем, режимы 2/3/4 молча не видят его без шаблона task*.py | low | ◐ |
| JRN-3A-01 | `src/stepik_grader/downloader.py:426` | Битый/пустой stepik_config.json — тупик: мастер недоступен, сообщение без пути и без выхода | low | ✅ |
| JRN-3A-02 | `src/stepik_grader/downloader_config.py:229` | Провальный прогон downloader молча переписывает конфиг: относительные пути → абсолютные | low | ✅ |
| JRN-3A-03 | `src/stepik_grader/diagnostic_stepik.py:296` | diagnostic_stepik завершается кодом 0 при любом провале — триаж-инструмент непригоден как проверка | low | ✅ |
| JRN-3A-04 | `src/stepik_grader/core/stepik_client.py:446` | Занятый локальный порт 8080 выдаётся за неверные OAuth-учётные данные (углубление) | low | ✅ |
| JRN-3A-05 | `src/stepik_grader/diagnostic_stepik.py:274` | Все три приглашения diagnostic_stepik — по-английски, вперемешку с русскими ответами | low | ✅ |
| JRN-3B-01 | `src/stepik_grader/core/stepik_client.py:490` | Веб-мастер OAuth: 120 с тишины, а ссылка для входа уходит только в stdout сервера | low | ◐ |
| JRN-3B-02 | `src/stepik_grader/web/auth_adapter.py:114` | Провалившийся веб-OAuth оставляет непроверенные креды и подменяет статус мастера | low | ◐ |
| JRN-3B-03 | `src/stepik_grader/web/downloader_adapter.py:203` | /api/download не проверяет URL: опечатка и чужой домен выдаются за проблему авторизации | low | ✅ |
| JRN-3B-04 | `src/stepik_grader/web/viewmodels.py:739` | NO TESTS — сырой английский литерал контракта, единственный отклик веба на задачу без тестов | low | ◐ |
| JRN-3B-05 | `src/stepik_grader/web/static/index.html:530` | Офлайн-путь работает, но веб о нём не знает: единственный вход «завести задачу» — Загрузчик Stepik | low | ◐ |
| JRN-4A-03 | `src/stepik_grader/core/history_recording.py:133` | Подъём за .grader_history.db отключён вне $HOME: база в корне курса не находится с другого диска | low | ◐ |
| JRN-4A-04 | `src/stepik_grader/core/history_recording.py:172` | Единая база копит задачи всех каталогов машины, а «Прогресс» не показывает, откуда задача | low | ◐ |
| JRN-4A-05 | `src/stepik_grader/core/stats.py:58` | В одном каталоге два ответа на «сколько прогонов»: 9 у --export-progress и 2 у --stats-summary | low | ◐ |
| JRN-4B-03 | `src/stepik_grader/core/user_settings.py:82` | Web читает .grader_settings.json из --root, а CLI — из cwd: согласие/онбординг расходятся в одной сессии | low | ✅ |
| JRN-4B-04 | `src/stepik_grader/web/server.py:177` | Онбординг «первый запуск» всплывает в каждой новой папке, а Прогресс рядом показывает 28 прогонов | low | ◐ |
| JRN-4B-06 | `src/stepik_grader/core/lint.py:166` | Web-проверка оставляет .ruff_cache в каталоге запуска — вне --root и вне охвата --clear-cache | low | ✅ |
| JRN-5-01 | `src/stepik_grader/cli/__init__.py:517` | --purge-history с опечаткой в ключе задачи рапортует успех при нуле удалённых прогонов | low | ✅ |
| JRN-5-02 | `src/stepik_grader/cli/__init__.py:534` | Выгруженный grader-progress.md переживает полную очистку истории с устаревшими агрегатами | low | ✅ |
| JRN-5-03 | `src/stepik_grader/core/reporter.py:355` | --stats-summary мешает в одной колонке прогоны и кейсы: Total runs 36 и Verdict AC 36 при 12 зелёных | low | ✅ |
| JRN-5-05 | `src/stepik_grader/core/progress_export.py:97` | Углубление: за порогом retention отчёт противоречит себе: 201 прогон в шапке, 205 попыток строкой ниже | low | ✅ |

### Фаза 2 · Матрица режимов и рейтинг решений

Прогон всех четырёх режимов проверки на реальных наборах кейсов.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| MTX-1-02 | `src/stepik_grader/core/test_loader.py:219` | Углубление: tests/N без N.clue — в режиме 3 неверное решение встаёт первым в рейтинге | high | ✅ |
| MTX-3-01 | `src/stepik_grader/core/microbench_runner.py:197` | Режим 4 переворачивает рейтинг: tracemalloc делает более быстрое решение MUCH_SLOWER 2175.5% | high | ✅ |
| MTX-5-03 | `src/stepik_grader/core/test_loader.py:219` | Пропажа N.clue усекает набор молча: заведомо неверное решение получает зелёное OK 1/1 | high | ✅ |
| MTX-1-01 | `src/stepik_grader/core/grader_core.py:893` | Режим 4 складывает timeit-микросекунды и subprocess-миллисекунды в одну статистику: Mean в 1650× больше Median | medium | ✅ |
| MTX-10-01 | `src/stepik_grader/core/runner.py:718` | Веб-бенчмарк объявляет SIMILAR решения, которые CLI признаёт MUCH_SLOWER: 0.1-с квантование | medium | ✅ |
| MTX-10-04 | `src/stepik_grader/cli/commands.py:465` | --output json печатает русский текст вместо JSON на ошибках; веб на тот же вход даёт валидный JSON | medium | ✅ |
| MTX-2-01 | `src/stepik_grader/core/grader_core.py:838` | Режим 4 бенчмаркит 5 кейсов из 7 и молчит: медленное решение получает SIMILAR | medium | ✅ |
| MTX-2-02 | `src/stepik_grader/core/grader_core.py:186` | Relative режима 3 считается по медиане: решение в 5.8 раза хуже по среднему — SIMILAR | medium | ✅ |
| MTX-3-04 | `src/stepik_grader/core/test_loader.py:163` | Предупреждения автодетекта формата 3 — английский UserWarning с внутренним путём, мимо _console | medium | ✅ |
| MTX-4-01 | `src/stepik_grader/core/test_loader.py:148` | Формат 3 вытесняет кейсы формата 2 в той же tests/: мутант получает «OK 1/1» и rc=0 | medium | ◐ |
| MTX-4-04 | `src/stepik_grader/cli/commands.py:624` | Ноль загруженных кейсов: неверное решение получает JSON без единого провала | medium | ✅ |
| MTX-5-01 | `src/stepik_grader/core/parsers.py:35` | Одни и те же ожидания дают разный вердикт в формате 1 и формате 3 — верное решение то AC, то WA | medium | ✅ |
| MTX-5-02 | `src/stepik_grader/core/test_loader.py:288` | Посторонний 1.type переводит в function-режим ВСЕ кейсы: верное stdin-решение получает 0/2 RE | medium | ◐ |
| MTX-5-04 | `src/stepik_grader/core/test_loader.py:174` | Предупреждение о рассинхроне блоков — сырой UserWarning по-английски, а в JSON его нет вовсе | medium | ✅ |
| MTX-5-05 | `src/stepik_grader/core/parsers.py:64` | Маркер в другом регистре съедается как данные: ожидание кейса 1 разрастается, кейс 2 исчезает | medium | ✅ |
| MTX-6-01 | `src/stepik_grader/core/runner.py:759` | Паритет памяти нарушен: одно решение — 0.00 МБ без --sandbox и 870.56 МБ под --sandbox | medium | ✅ |
| MTX-7-01 | `src/stepik_grader/core/grader_core.py:285` | Пустой input_N.txt подаёт решению фантомный перевод строки — верное решение получает WA | medium | ✅ |
| MTX-9-02 | `src/stepik_grader/core/grader_core.py:419` | Пометка об обрезке уходит в stderr, а stderr читается только при returncode != 0 | medium | ✅ |
| MTX-9-03 | `src/stepik_grader/core/reporter.py:549` | Строка вывода в 1 МБ вешает режим 1 по умолчанию; --quiet на том же кейсе — 0.4 с | medium | ✅ |
| MTX-1-03 | `src/stepik_grader/core/grader_core.py:869` | --number в режиме 4 меняет смысл от типа кейса; на function-задаче timeit не участвует вовсе | low | ◐ |
| MTX-1-04 | `src/stepik_grader/cli/commands.py:722` | Режим 3 печатает пустую таблицу, когда отбракованы все решения; режим 4 — не печатает | low | ✅ |
| MTX-10-03 | `src/stepik_grader/web/api_routes.py:690` | POST /api/v1/runs молча игнорирует repeats на верхнем уровне тела: 30 прогонов вместо 3 | low | ◐ |
| MTX-10-05 | `docs/dev/result-contract.md:9` | На одном RE-файле веб отдаёт glossary/suggestions/severity/timeout_s, а --output json — ничего из этого | low | ◐ |
| MTX-2-03 | `src/stepik_grader/core/test_loader.py:48` | task_a.py подходит под обещанный в ошибке шаблон task*.py, но отвергается режимами 2/3/4 | low | ✅ |
| MTX-2-04 | `src/stepik_grader/web/api_routes.py:216` | GET /api/grade не валидирует mode и молча отдаёт режим tests вместо запрошенного | low | ✅ |
| MTX-3-05 | `src/stepik_grader/core/locales/ru.json:223` | Режимы 3 и 4 при отбраковке не называют номер провалившегося кейса, режимы 1/2 называют | low | ✅ |
| MTX-4-03 | `src/stepik_grader/core/wrapper_builder.py:159` | function-режим: мутация «return str вместо int» проходит как AC | low | ◐ |
| MTX-6-02 | `src/stepik_grader/core/sandbox/_linux.py:165` | Под --sandbox traceback показывает эфемерный /tmp/stepik-sandbox-*/solution.py вместо файла студента | low | ✅ |
| MTX-6-03 | `src/stepik_grader/core/sandbox/_posix_common.py:179` | measure_child_memory=false игнорируется под --sandbox: память всё равно измеряется | low | ✅ |
| MTX-6-04 | `src/stepik_grader/core/runner.py:318` | Измеритель памяти дефолтного раннера добавляет ~30% к измеряемому времени и всё равно отдаёт 0.00 | low | ◐ |
| MTX-6-05 | `src/stepik_grader/core/sandbox/_linux.py:172` | Время под --sandbox расходится в обе стороны (2.5x медленнее / 2.2x быстрее) при фиксированном пороге TLE | low | ◐ |
| MTX-7-02 | `src/stepik_grader/core/normalizers.py:27` | Одиночный \r в stdout решения считается переводом строки: «a\rb» принимается как две строки (ложный AC) | low | ◐ |
| MTX-7-03 | `src/stepik_grader/core/grader_core.py:610` | Поле stdin в результате не равно реально поданным байтам: теряется завершающий перевод строки | low | ✅ |
| MTX-7-04 | `src/stepik_grader/core/test_loader.py:116` | Пустой expected_N.txt даёт зелёное «OK 1/1» решению, которое ничего не печатает | low | ◐ |
| MTX-8-01 | `src/stepik_grader/core/normalizers.py:76` | Округление до 9 знаков абсолютное: вывод «0.0» принимается вместо «0.0000000001» → AC неверному решению | low | ◐ |
| MTX-8-02 | `src/stepik_grader/core/normalizers.py:76` | Большие конечные float схлопываются в один repr: 123456789012345678.0 и ...679.0 дают AC | low | ✅ |
| MTX-8-03 | `src/stepik_grader/core/normalizers.py:76` | Знак нуля асимметричен: 0.0 против +4e-10 — AC, против -4e-10 — WA; «-0.00» из f-строки тоже WA | low | ◐ |
| MTX-8-04 | `src/stepik_grader/core/normalizers.py:57` | Нормализуется форма записи, а не значение: «1e-10» против «0.0000000001» даёт WA — это одно число | low | ◐ |
| MTX-8-05 | `src/stepik_grader/core/reporter.py:590` | AC по числовому допуску ничем не помечен: «✓ Test 4: AC» и пустой diff при 12.3 против 12.30 | low | ✅ |
| MTX-9-01 | `src/stepik_grader/core/runner.py:414` | Обрезка max_output_bytes режет строку посередине: в diff строка-призрак, которой решение не печатало | low | ✅ |
| MTX-9-04 | `src/stepik_grader/cli/commands.py:510` | Машинный вывод не обрезан ничем: 28.5 МБ JSON на один тест-кейс | low | ✅ |

### Фаза 2 · Порча входных данных (fuzzing)

Битые, урезанные и подменённые файлы тестов: что продукт делает вместо отказа.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| FZZ-1-01 | `src/stepik_grader/core/grader_core.py:844` | Режим 4 падает трейсбеком на корректном решении с объявленной PEP-263 кодировкой cp1251 | medium | ✅ |
| FZZ-1-02 | `src/stepik_grader/core/test_loader.py:116` | Один файл ожиданий не в UTF-8 убивает весь режим 2 на 0%, а с --output json отдаёт CI ноль байт | medium | ✅ |
| FZZ-1-04 | `src/stepik_grader/core/test_loader.py:116` | Незащищено ровно то чтение, ради которого делали #792: соседний .type переживает любой байт, expected — нет | medium | ✅ |
| FZZ-3-01 | `src/stepik_grader/core/test_loader.py:268` | Конфайнмент --root обходится штатным /api/grade: в ответ утекает файл ВЫШЕ рабочей директории | medium | ✅ |
| FZZ-3-02 | `src/stepik_grader/core/test_loader.py:255` | Решение-симлинк грейдится тестами каталога-цели: верное решение получает WA при верных тестах рядом | medium | ✅ |
| FZZ-5-05 | `src/stepik_grader/core/history.py:709` | --purge-history падает трейсбеком на том файле, ради которого продукт советует «удалите его» | medium | ✅ |
| FZZ-1-03 | `src/stepik_grader/core/error_glossary.py:91` | RE из-за кодировки исходника получает подсказку про синтаксическую опечатку — диагностика уводит в сторону | low | ✅ |
| FZZ-2-01 | `src/stepik_grader/core/reporter.py:611` | Вывод решения печатается в tty сырым: ANSI из решения стирает отчёт и рисует поддельный «AC» | low | ✅ |
| FZZ-2-02 | `src/stepik_grader/core/grader_core.py:418` | stdout сравнивается после lossy-декода utf-8: два разных вывода получают одинаковый AC | low | ✅ |
| FZZ-2-03 | `src/stepik_grader/core/grader_core.py:418` | Решение с выводом не в UTF-8 даёт WA с «██████» и без намёка на кодировку | low | ✅ |
| FZZ-2-04 | `src/stepik_grader/core/reporter.py:561` | _clip_value режет по символам и рвёт ANSI пополам — уведомление об обрезке съедается | low | ✅ |
| FZZ-2-05 | `src/stepik_grader/core/reporter.py:607` | Превью Expected/Actual клеит строки через « \| »: вывод «a \| b» неотличим от двух строк | low | ✅ |
| FZZ-3-03 | `src/stepik_grader/core/test_loader.py:75` | Режим 2 молча не видит решения в симлинк-подкаталоге: строки нет в таблице, предупреждения нет, rc=0 | low | ✅ |
| FZZ-3-04 | `src/stepik_grader/core/reporter.py:258` | Длинное имя файла схлопывает колонку вердикта в ноль — в текстовом выводе результата нет вовсе | low | ✅ |
| FZZ-4-01 | `src/stepik_grader/web/http_guards.py:295` | Не-UTF8 байт в теле POST рвёт соединение без ответа на всех body-эндпоинтах API | low | ✅ |
| FZZ-4-02 | `src/stepik_grader/web/http_guards.py:283` | Расхождение Content-Length с телом вешает обработчик на 30 с, под лимитом — без ответа | low | ◐ |
| FZZ-4-03 | `src/stepik_grader/web/api_routes.py:457` | Пустое тело POST /api/downloader/config создаёт stepik_config.json и объявляет загрузчик настроенным | low | ✅ |
| FZZ-4-04 | `src/stepik_grader/web/api_routes.py:755` | POST /api/v1/settings отвечает ok:true на запись, которая не состоялась | low | ✅ |
| FZZ-4-05 | `src/stepik_grader/web/api_routes.py:559` | POST /api/import-reference: отказ кодом 200, число в path становится именем папки | low | ◐ |
| FZZ-5-01 | `src/stepik_grader/db.py:117` | БД истории нулевой длины — единственная порча без предупреждения: база тихо пересоздаётся с нуля | low | ◐ |
| FZZ-5-02 | `src/stepik_grader/cli/__init__.py:531` | --export-progress на нечитаемой БД: файла нет, exit 0, сообщение про «инсайты» вместо ошибки | low | ✅ |
| FZZ-5-03 | `src/stepik_grader/core/stats.py:188` | --stats-summary печатает самопротиворечивую сводку: 1 прогон, но 101 вердикт и 1000 с | low | ✅ |
| FZZ-5-04 | `src/stepik_grader/cli/options.py:353` | Тумблер истории мёртв даже в своей папке: _resolve_record_history не читает .grader_settings.json | low | ◐ |
| FZZ-5-06 | `src/stepik_grader/core/locales/ru.json:124` | «Статистика выключена» — сообщение, когда журнал есть, но все записи битые | low | ✅ |

### Фаза 2 · Параллельность и целостность состояния

Одновременные прогоны, миграции схемы, конкурентная запись.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| CNC-1-01 | `src/stepik_grader/cli/commands.py:423` | Один и тот же верный код даёт 4 разных вердикта при параллельных прогонах, и TLE залипает в кэше | medium | ◐ |
| CNC-1-03 | `src/stepik_grader/cli/commands.py:491` | Попадание в кэш пишется в .grader_stats.jsonl как полноценный прогон с чужим total_time | medium | ✅ |
| CNC-4-01 | `src/stepik_grader/db.py:176` | apply_schema коммитит промежуточную версию 1 — соседние процессы повторяют backfill и раздувают агрегат | medium | ✅ |
| CNC-4-02 | `src/stepik_grader/core/history.py:361` | Читающие команды (--insights, /api/progress) делают write-миграцию: 8 чтений раздули агрегат вчетверо | medium | ✅ |
| CNC-5-01 | `src/stepik_grader/cli/interactive.py:579` | Открытое меню воскрешает отозванное AI-согласие: web после этого шлёт код без спроса (HTTP 202) | medium | ✅ |
| CNC-5-05 | `src/stepik_grader/core/cache.py:112` | --sandbox с --cache не заходит в песочницу: вердикт и метрики из несанбоксного прогона | medium | ✅ |
| CNC-1-02 | `src/stepik_grader/core/cache.py:172` | Кэш --mode 1 теряет записи при параллельных прогонах: в results.json выживают 3 из 10 | low | ✅ |
| CNC-1-04 | `src/stepik_grader/core/runner.py:743` | AC у кейса, превысившего бюджет: 2.04 с при timeout_seconds=2.0, рядом TLE ровно на 2.00 | low | ◐ |
| CNC-2-01 | `src/stepik_grader/web/runs.py:325` | Отмена прогона в статусе queued не действует, пока не освободится воркер: ~20 с висит «queued» | low | ✅ |
| CNC-2-02 | `src/stepik_grader/web/runs.py:486` | Отменённый прогон выбрасывает уже посчитанные кейсы: result=null при progress 2/8 | low | ✅ |
| CNC-2-03 | `src/stepik_grader/web/api_routes.py:141` | Прогон переживает потерю клиента: перечислить или отменить его нечем, история пишется всё равно | low | ◐ |
| CNC-2-04 | `src/stepik_grader/web/runs.py:391` | Гибель сервера на лету оставляет tmpXXXXXX.py в папке задачи пользователя — подметать некому | low | ✅ |
| CNC-2-05 | `src/stepik_grader/web/api_routes.py:963` | POST /api/v1/runs/{id}/cancel не валидирует run_id: пустой и со слэшами id уезжает в текст пользователю | low | ✅ |
| CNC-3-01 | `src/stepik_grader/web/runs.py:230` | Бюджет воркеров и back-pressure — на процесс: соседний сервер молча раздувает замеры времени | low | ◐ |
| CNC-3-02 | `src/stepik_grader/cli/__init__.py:606` | Занятый порт: сырой traceback и exit 1 вместо сообщения — одинаково в ru и en (углубление) | low | ✅ |
| CNC-3-03 | `src/stepik_grader/web/viewmodels.py:804` | Прогон каталога пишет в историю ОДНУ запись без имени решения — вердикты всех файлов слиты | low | ◐ |
| CNC-3-04 | `src/stepik_grader/core/runner.py:543` | kill -9 сервера оставляет живой процесс решения и его tmp*.py в ОБЩЕМ каталоге --root | low | ✅ |
| CNC-4-03 | `src/stepik_grader/core/history.py:585` | Пути чтения истории вызывают _connect (миграцию) без _WRITE_LOCK, в отличие от add_run | low | ◐ |
| CNC-4-04 | `src/stepik_grader/core/history.py:315` | Агрегат task_progress не сверяется с runs: runs_total > count(runs) не ловится и не чинится | low | ◐ |
| CNC-5-02 | `src/stepik_grader/core/cache.py:94` | Параллельные --cache прогоны затирают друг друга: из 5 записей в results.json выживает 1 | low | ✅ |
| CNC-5-03 | `src/stepik_grader/core/cache.py:177` | --clear-cache во время идущего прогона рапортует «удалено 0» и не очищает: кэш возвращается | low | ✅ |
| CNC-5-04 | `src/stepik_grader/cli/interactive.py:579` | Сессия меню откатывает подтверждённую вебом (HTTP 200) запись: окно потери равно всей сессии | low | ✅ |

### Фаза 2 · Долгий прогон и накопление

Поведение на длинной дистанции: кэш, рост базы, деградация.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| LNG-1-01 | `src/stepik_grader/core/progress_export.py:97` | Экспорт прогресса занижает число прогонов: считает по усечённой retention таблице runs, а не по runs_total | medium | ✅ |
| LNG-1-04 | `src/stepik_grader/cli/commands.py:497` | Одна задача — два task_key в одной БД: дубли task_progress, двойной TTFG, удвоенный лимит retention | medium | ✅ |
| LNG-5-01 | `src/stepik_grader/core/history.py:709` | Полная очистка переносит хранилище: сразу после «История удалена» --insights печатает 12 чужих задач | medium | ◐ |
| LNG-5-04 | `src/stepik_grader/core/stats.py:57` | --purge-history из подпапки подтверждает «записей статистики — 0», оставляя 116 живых записей | medium | ✅ |
| LNG-1-02 | `src/stepik_grader/core/stats.py:61` | --stats-summary после ротации журнала молча теряет половину «всего прогонов» и общего времени | low | ✅ |
| LNG-1-03 | `src/stepik_grader/core/progress_export.py:94` | Серия и бейджи считаются по обрезанному окну runs: 201 вместо 360 подряд зачтённых прогонов | low | ✅ |
| LNG-2-01 | `src/stepik_grader/core/stats.py:76` | Журнал статистики на 1 МиБ молча теряет половину истории во время обычной проверки: 10 124 → 5 063 | low | ◐ |
| LNG-2-02 | `src/stepik_grader/core/progress_export.py:97` | Отчёт прогресса навсегда замирает на «Прогонов: 10000» при 40 000 в базе: размер выборки выдан за факт | low | ✅ |
| LNG-2-03 | `src/stepik_grader/web/api_routes.py:297` | Веб-«Прогресс» замедляется в 5-9 раз по мере накопления: 38 мс → 350 мс, 21 КБ ответа, без кэша | low | ◐ |
| LNG-2-04 | `src/stepik_grader/cli/__init__.py:566` | --insights вываливает таблицу всех задач: 211 строк на 200 задач, «Подучить» уезжает за экран | low | ✅ |
| LNG-3-01 | `src/stepik_grader/core/runner.py:720` | Штатный выход раннера не добивает дерево: каждый прогон копит осиротевший процесс решения | low | ◐ |
| LNG-3-02 | `src/stepik_grader/core/runner.py:727` | Внук, держащий stdout, копит в сервере по 2 потока и 2 pipe-дескриптора на кейс | low | ◐ |
| LNG-3-03 | `src/stepik_grader/web/server.py:271` | SIGTERM серверу минует finally с shutdown_jobs(): процесс решения переживает сервер без таймаута | low | ✅ |
| LNG-4-02 | `src/stepik_grader/core/progress_export.py:97` | «Прогонов: 109 · задач: 1»: шапка считает бенчмарк-прогоны, таблица задач — нет | low | ✅ |
| LNG-4-03 | `src/stepik_grader/core/insights.py:87` | «Типы падений» недосчитывает 6 ERR: 135 против 141 падений внутри одного файла | low | ◐ |
| LNG-4-04 | `src/stepik_grader/core/progress_export.py:78` | Тали вердиктов смешивают бенчмарк-вердикты SIMILAR/ERR с AC/WA/RE | low | ✅ |
| LNG-4-05 | `src/stepik_grader/core/insights.py:229` | Два отчёта спорят о времени: «до AC — 11 с» при «всего 7.594 с» на все 108 прогонов | low | ◐ |
| LNG-5-02 | `src/stepik_grader/core/cache.py:165` | Кэш молча упирается в 512 записей: на 600 решениях каждый прогон вечно перепрогоняет 88 | low | ◐ |
| LNG-5-03 | `src/stepik_grader/core/cache.py:179` | --clear-cache удаляет 393 КБ кэша и рапортует «удалено записей — 0» при несовместимой версии файла | low | ✅ |

### Фаза 2 · Песочница под нагрузкой

`--sandbox` на реальных задачах, а не в теории.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| SBX-1-01 | `src/stepik_grader/core/wrapper_builder.py:248` | Кейсы типа function падают под --sandbox поголовно: 3/3 OK локально → 0/3 FAIL | high | ✅ |
| SBX-1-02 | `src/stepik_grader/core/sandbox/_linux.py:165` | Соседний модуль решения не попадает в песочницу: 5/5 OK → 0/5 FAIL под --sandbox | medium | ✅ |
| SBX-1-03 | `src/stepik_grader/core/runner.py:546` | Вердикт LocalRunner зависит от каталога запуска: один файл даёт и 0/5 FAIL, и 5/5 OK | medium | ✅ |
| SBX-2-03 | `src/stepik_grader/core/runner.py:616` | Углубление: peak_memory_mb зависит от max_output_bytes — 209.13 МБ против 0.0 на одном решении | medium | ✅ |
| SBX-3-01 | `src/stepik_grader/core/sandbox/_posix_common.py:313` | Зависшее решение под --sandbox получает RE вместо TLE: CPU-квота 10 с гасит его раньше wall-clock | medium | ✅ |
| SBX-4-01 | `src/stepik_grader/core/sandbox/_linux.py:202` | Сломанный bwrap не отвергается: верное решение получает FAIL/RE со stderr самой песочницы | medium | ◐ |
| SBX-4-03 | `src/stepik_grader/cli/__init__.py:621` | --sandbox без --mode: в меню OK, под --mode 1 FAIL; запись вне задачи реально проходит | medium | ✅ |
| SBX-5-01 | `src/stepik_grader/core/cache.py:112` | Кэш отдаёт вердикт, порождённый изоляцией, обычному прогону: FAIL 0/2 и чужие 15.10 МБ | medium | ✅ |
| SBX-5-02 | `src/stepik_grader/cli/__init__.py:669` | --watch --mode 2 включает кэш сам — под --sandbox изоляция отключается без единого cache-флага | medium | ✅ |
| SBX-1-04 | `src/stepik_grader/cli/commands.py:562` | «Файлы решений не найдены в: {path}» — плейсхолдер печатается буквально | low | ✅ |
| SBX-2-01 | `src/stepik_grader/core/sandbox/_posix_common.py:134` | Стоимость изоляции — детерминированная добавка ≈25 мс на кейс, и она списывается на решение | low | ✅ |
| SBX-2-02 | `src/stepik_grader/core/grader_core.py:802` | Verdict режима 3 меняется от --sandbox: та же пара решений — MUCH_SLOWER 192.8% против SLOWER 149.1% | low | ◐ |
| SBX-2-04 | `src/stepik_grader/core/sandbox/_posix_common.py:84` | Память под --sandbox: постоянный «пол» ~12–13 МБ и разброс 12% на одном решении | low | ◐ |
| SBX-3-02 | `src/stepik_grader/core/grader_core.py:385` | SANDBOX_VIOLATION — третья ветка, стирающая вывод: напечатано 10 МБ, в отчёте output: [] | low | ✅ |
| SBX-3-03 | `src/stepik_grader/core/grader_core.py:405` | TLE-ветка не передаёт memory= вовсе: тот же цикл под --sandbox даёт 13.09 МБ, без него — 0.00 | low | ✅ |
| SBX-3-04 | `src/stepik_grader/core/runner.py:737` | Обрезка на 10 МБ не защищает отчёт: один кейс — 21 МБ JSON, пометка об обрезке изготовлена и выброшена | low | ✅ |
| SBX-4-02 | `src/stepik_grader/core/sandbox/_posix_common.py:326` | Backend, не исполнивший код, даёт тихий WA «Actual: (empty)» | low | ◐ |
| SBX-4-04 | `src/stepik_grader/core/grader_core.py:421` | Отказ ФС в песочнице выдаётся как RE, а подсказка глоссария учит неверной причине | low | ◐ |
| SBX-5-03 | `src/stepik_grader/core/history.py:234` | Схема истории не хранит признак изоляции: два режима ложатся в одну серию задачи | low | ✅ |
| SBX-5-04 | `src/stepik_grader/cli/commands.py:509` | JSON-вывод и .grader_stats.jsonl не помечают изоляцию — CI не докажет режим прогона | low | ✅ |
| SBX-5-05 | `src/stepik_grader/core/tracer.py:392` | Отказ трейса под --sandbox — русский литерал мимо локалей, ?lang=en игнорируется | low | ✅ |

### Фаза 2 · Установка и офлайн

Чистая установка, отсутствие сети, чужие файлы в родительских каталогах.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| INS-1-01 | `src/stepik_grader/config.py:337` | Битый pyproject.toml в любом каталоге над cwd рушит трейсбеком каждую команду, включая --version | medium | ✅ |
| INS-3-01 | `src/stepik_grader/__main__.py:9` | `python -m stepik_grader` из каталога задачи падает ImportError на файле-однофамильце stdlib | medium | ✅ |
| INS-4-01 | `src/stepik_grader/config.py:337` | Битый pyproject.toml выше по дереву или в STEPIK_GRADER_CONFIG роняет грейдер до первого теста | medium | ✅ |
| INS-4-03 | `src/stepik_grader/core/history.py:504` | Недоступный для записи путь БД истории — полная тишина и тройной повтор: прогон вдвое дольше | medium | ✅ |
| INS-1-02 | `src/stepik_grader/web/glossary_adapter.py:178` | /api/glossary отдаёт 2.2 МБ полных карточек без сжатия — заново на каждый ввод в поиске | low | ✅ |
| INS-1-04 | `src/stepik_grader/core/reporter.py:524` | --lint на чистом файле не печатает ничего: «замечаний нет» неотличимо от «не проверялось» | low | ✅ |
| INS-2-01 | `src/stepik_grader/core/reporter.py:462` | Без rich таблицы --insights и «Прогресс» теряют заголовок и подписи столбцов | low | ✅ |
| INS-2-02 | `src/stepik_grader/cli/__init__.py:419` | Углубление: --watch без watchfiles отменяет саму проверку и выходит с кодом 0 | low | ◐ |
| INS-2-03 | `src/stepik_grader/core/runner.py:40` | Импорт psutil на уровне модуля runner.py роняет весь грейдер сырым трейсбеком | low | ◐ |
| INS-3-03 | `src/stepik_grader/downloader.py:465` | `python -m stepik_grader.downloader` возвращает 0 после фатального отказа конфига | low | ✅ |
| INS-3-04 | `src/stepik_grader/ide.py:1` | `python -m stepik_grader.ide` — тихий no-op с кодом 0 | low | ◐ |
| INS-4-02 | `src/stepik_grader/config.py:274` | pyproject.toml из чужой родительской папки молча переворачивает вердикт: AC 2/2 → FAIL 0/2 (TLE) | low | ◐ |
| INS-4-04 | `src/stepik_grader/cli/__init__.py:546` | Флаги, пишущие в cwd, падают голым OSError на read-only каталоге | low | ✅ |
| INS-5-01 | `src/stepik_grader/downloader.py:424` | `downloader --help` вместо справки запускает мастер и создаёт stepik_config.json в текущем каталоге | low | ✅ |
| INS-5-02 | `src/stepik_grader/core/locales/ru.json:148` | Справка и ошибки зовут в файлы репозитория (docs/*.md, SECURITY.md, README), которых нет после pip install | low | ✅ |
| INS-5-03 | `src/stepik_grader/cli/options.py:61` | `--lang en --help` печатает целиком русскую справку | low | ✅ |
| INS-5-04 | `src/stepik_grader/downloader_config.py:185` | Загрузчик нельзя запустить неинтерактивно даже с валидным конфигом: на EOF не применяется дефолт [y/N] | low | ◐ |
| INS-5-05 | `src/stepik_grader/launcher.py:793` | stepik-grader-gui — вторая установленная команда: не упомянута в справке и не знает --help | low | ✅ |
| INS-5-06 | `src/stepik_grader/cli/options.py:35` | Справка не отвечает на первый вопрос новичка «откуда взять задачу» | low | ✅ |

### Фаза 2 · Стратегия по данным аудита

Выводы не из кода, а из самого накопителя находок: системная причина, порядок работ, экономика.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| STR2-1-04 | `src/stepik_grader/core/test_loader.py:204` | Три эвристических формата тестов без объявленного размера набора: 10 из 26 high — входной слой | high | ◐ |
| STR2-1-01 | `src/stepik_grader/core/result.py:22` | Вердикт не имеет состояния «проверка не состоялась» — сбой среды выходит уверенным AC/WA | medium | ◐ |
| STR2-1-03 | `src/stepik_grader/config.py:282` | Состояние, определяющее вердикт, берётся из окружения, а не из объявления: 4 канала, 9 high | medium | ◐ |
| STR2-1-06 | `src/stepik_grader/core/sandbox/_linux.py:165` | --sandbox — второй исполнитель без контракта паритета: 8 из 26 high, 6 ломают верное решение | medium | ◐ |
| STR2-2-01 | `src/stepik_grader/config.py:273` | Ступень 1: детерминизм окружения прогона — иначе ни один фикс ниже не доказуем | medium | ✅ |
| STR2-2-03 | `src/stepik_grader/core/cache.py:110` | Ступень 3: ключ кэша с условиями исполнения — один файл разблокирует проверяемость sandbox | medium | ✅ |
| STR2-2-04 | `src/stepik_grader/core/history.py:672` | Ступень 4: единый ключ задачи — предпосылка для всего, что считает прогресс | medium | ◐ |
| STR2-2-05 | `src/stepik_grader/core/runner.py:737` | Ступень 5: обрезка и декод вывода — поле результата, а не пометка в stderr | medium | ✅ |
| STR2-2-07 | `src/stepik_grader/web/api_routes.py:845` | Параллельная дорожка, а не седьмой пункт: AI-согласие и конфайнмент web | medium | ◐ |
| STR2-4-01 | `src/stepik_grader/core/test_loader.py:118` | load_test_cases — кандидат на переписывание, а не на починку: 92.5 находок/KLOC против 20.8 по core | medium | ◐ |
| STR2-4-02 | `scripts/corpus_mutations.py:189` | Прогонный корпус мутирует только решение — класс дефектов набора тестов ему недостижим | medium | ✅ |
| STR2-4-04 | `src/stepik_grader/core/history.py:647` | task_key = имя папки при глобальной БД: 27 находок в 17 файлах — чинить надо ключ, а не потребителей | medium | ✅ |
| STR2-1-02 | `src/stepik_grader/core/result.py:55` | Нет паспорта прогона: результат не говорит, что именно исполнялось — 20 fix'ов чинят это поштучно | low | ◐ |
| STR2-1-05 | `CONTRIBUTING.md:36` | Пофайловое ревью слепо к этому классу: 20 из 26 high — на стыке модулей, 18 нашлись только прогоном | low | ◐ |
| STR2-3-01 | `docs/agent/multiagent.md:216` | Критерий отбраковки: фильтровать зоной и видом до записи, а не разбирать 485 low поштучно | low | ✅ |
| STR2-3-03 | `src/stepik_grader/core/stats.py:58` | Журнал .grader_stats.jsonl: 12 находок, все low — решать судьбу фичи целиком | low | ◐ |
| STR2-3-04 | `src/stepik_grader/launcher.py:13` | Паритет GUI-лаунчера с CLI — не долг, а заявка на второй продукт: 11 находок, все low | low | ◐ |
| STR2-3-05 | `docs/use/configuration.md:375` | --sandbox: сузить обещание в доке вместо упрочнения ОС-бэкендов — 36 находок и ни одной high | low | ◐ |
| STR2-3-06 | `CLAUDE.md:338` | Правило «CHANGELOG-запись в КАЖДОМ PR» делает мелкую починку дороже самой мелочи | low | ◐ |
| STR2-4-03 | `tests/e2e/test_journeys.py:1` | 143 репро фазы 2 живут только в отчёте аудита: после фикса ничто не удержит их от возврата | low | ◐ |
| STR2-4-05 | `tests/test_facade_contract.py:1` | Паритет CLI↔web↔sandbox↔cache: 25 находок, но ни одного теста на равенство результата | low | ◐ |
| STR2-4-06 | `src/stepik_grader/core/result.py:73` | Метрики прогона объявлены полями, но не контрактом: 20 находок, 0.00 как валидное значение | low | ✅ |
| STR2-5-01 | `CONTRIBUTING.md:345` | Профиль плоский: план «PR на файл» стоит ~149 PR и ~2000 job-прогонов CI | low | ◐ |
| STR2-5-02 | `docs/agent/claude-handoff.md:1` | Спринт поимённо: 19 файлов держат все 26 high и 51% веса severity | low | ◐ |
| STR2-5-03 | `src/stepik_grader/core/test_loader.py:1` | test_loader.py: переписать 292 строки дешевле, чем чинить 27 находок поштучно | low | ✅ |
| STR2-5-04 | `scripts/check_docs_guardrails.py:1` | Четверть корпуса (173 находки) не про рантайм — поштучный ремонт здесь чистая потеря | low | ◐ |
| STR2-5-05 | `docs/audit/README.md:40` | Хвост из 384 low не окупает процесс: нужна политика массового отклонения с фиксацией | low | ◐ |
| STR2-5-06 | `docs/agent/multiagent.md:146` | 35% вердиктов — PARTIAL: треть находок не готова к постановке задачи | low | ✅ |

### Фаза 3 · Карта связей документов

Битые ссылки и якоря, документы-сироты, упомянутые но несуществующие цели.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| MAP-1-01 | `docs/README.md:13` | CODE_OF_CONDUCT.md — сирота: ни README, ни README.en, ни docs/README на него не ссылаются | low | ◐ |
| MAP-1-02 | `SECURITY.md:3` | SECURITY.md называет себя «коротким и ссылочным», будучи самым большим корневым документом без оглавления | low | ✅ |
| MAP-1-03 | `CONTRIBUTING.md:340` | CONTRIBUTING обещает паттерн имён файлов решений в configuration.md, где его нет | low | ✅ |
| MAP-1-04 | `README.en.md:37` | EN-витрина уводит во все ru-only документы без предупреждения о языке | low | ✅ |
| MAP-1-05 | `docs/README.md:13` | docs/README.md обещает «два файла рядом с кодом», а перечисляет четыре | low | ◐ |
| MAP-1-06 | `SECURITY.md:1` | SECURITY.md и CODE_OF_CONDUCT.md — навигационные тупики без обратных ссылок | low | ✅ |
| MAP-2-01 | `docs/use/versions.md:5` | versions.md шлёт за деталями в CHANGELOG.md, где после ротации только три последних MINOR | low | ✅ |
| MAP-2-02 | `docs/use/versions.md:41` | Таблица эволюции в versions.md хардкодит числа тестов/покрытия вопреки правилу | low | ◐ |
| MAP-2-03 | `docs/use/versions.md:6` | versions.md ведёт за схемой версионирования в CONTRIBUTING.md вместо канона dev/versioning.md | low | ✅ |
| MAP-2-04 | `docs/use/configuration.md:171` | configuration.md отправляет пользователя за каноном в CLAUDE.md, а тот ссылается обратно | low | ◐ |
| MAP-2-05 | `docs/use/web-interface.md:202` | Пользовательский web-interface.md содержит dev-внутренности вопреки разделу с web-contracts.md | low | ✅ |
| MAP-2-06 | `docs/README.md:8` | Развилка docs/README.md описывает use/ без темы versions.md | low | ✅ |
| MAP-3-01 | `docs/dev/README.md:37` | Индекс docs/dev обещает e2e-покрытие journeys J0–J7, но J5 не защищён ни одним тестом | low | ◐ |
| MAP-3-02 | `CLAUDE.md:386` | Канон версионирования в CLAUDE.md ведёт в CONTRIBUTING.md, который сам делегирует дальше | low | ◐ |
| MAP-3-03 | `CLAUDE.md:382` | Таблица источников истины в CLAUDE.md не содержит glossary.md, trace-format.md, rules-insights.md | low | ◐ |
| MAP-3-04 | `docs/dev/project-structure.md:39` | web/settings_adapter.py выпал из канонического дерева project-structure.md | low | ✅ |
| MAP-3-05 | `scripts/check_docs_guardrails.py:361` | Гард полноты индексов docs/ не видит подкаталоги без README и не-md файлы | low | ◐ |
| MAP-4-01 | `docs/dev/adr/0009-server-data-model.md:3` | ADR-0009 числится Proposed, хотя фаза 0 его доменной модели уже в core/history.py | low | ◐ |
| MAP-4-03 | `docs/dev/adr/0011-local-persistence.md:6` | ADR-0011 указывает несуществующий путь core/db.py вопреки собственному решению | low | ✅ |
| MAP-4-04 | `docs/dev/adr/0001-server-mode.md:6` | ADR-0001 отсылает к эпику #151 как к живому backlog'у, хотя направление держит #59 | low | ◐ |
| MAP-4-05 | `docs/dev/adr/0001-server-mode.md:9` | Связи вокруг ADR-0001 односторонние: 0006/0007/0010 ссылаются, он о них не знает | low | ◐ |
| MAP-4-06 | `docs/dev/design/README.md:3` | design/README: «здесь описано то, чего в коде нет» противоречит своей же таблице | low | ◐ |
| MAP-4-07 | `docs/dev/design/README.md:21` | «Читай как согласованный контракт» распространяется и на отклонённое в web-design.md | low | ◐ |
| MAP-5-01 | `docs/agent/claude-handoff.md:9` | Подписи ссылок с ../ ведут в несуществующие пути (читатель копирует подпись, а не цель) | low | ✅ |
| MAP-5-02 | `scripts/check_docs_guardrails.py:134` | Гейт подписей ссылок слеп ко всем подписям, начинающимся с '..' — основной форме внутри docs/ | low | ◐ |
| MAP-5-03 | `docs/archive/README.md:19` | Индекс архива обещает ротированные релизы 1.1.0–1.6.0, а файл содержит 1.7.0 | low | ✅ |
| MAP-5-04 | `docs/agent/claude-handoff.md:15` | Очередь работ описывает состояние до аудита 2026-08-10 и не упоминает 477 живых находок | low | ◐ |
| MAP-5-05 | `scripts/check_docs_guardrails.py:417` | CI-подсказка гейта CHANGELOG указывает на несуществующий путь docs/changelog-archive.md | low | ✅ |
| MAP-5-06 | `docs/agent/README.md:30` | Ссылки с подписью «§ раздел» ведут в начало CLAUDE.md без якоря | low | ✅ |
| MAP-5-07 | `docs/audit/README.md:10` | Индекс аудитов дублирует счётчики находок, которые начнут врать с первым закрытым PR | low | ✅ |

### Фаза 3 · Противоречия по существу

Пары утверждений, которые не могут быть верны одновременно: документ против кода и против документа.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| PAIR-1-02 | `src/stepik_grader/config.py:234` | Док обещает предупреждение в stderr, а ai_api_key_env отбраковывается молча в выключенный лог | medium | ◐ |
| PAIR-4-02 | `docs/use/configuration.md:378` | Лимит памяти заявлен «на POSIX (Linux/macOS)», а prlimit — Linux-only: на macOS лимита нет | medium | ✅ |
| PAIR-1-01 | `docs/use/grader-workflow.md:390` | Док: история в .grader_history.db рядом с задачей; код по умолчанию пишет в ~/.stepik-grader/history.db | low | ◐ |
| PAIR-1-03 | `docs/use/configuration.md:272` | Таблица типов тестов обещает «нет .type → stdin», но AST/meta.json переопределяют тип всех кейсов | low | ✅ |
| PAIR-1-04 | `src/stepik_grader/core/test_loader.py:153` | Формат 3 без маркеров # TEST_N: тихо проваливается в форматы 1/2 — диагностика уводит не туда | low | ✅ |
| PAIR-1-05 | `docs/use/configuration.md:35` | --clear-cache удаляет ещё и .stepik_cache/ (ответы API) — в docs/use этот кэш не описан нигде | low | ✅ |
| PAIR-1-06 | `src/stepik_grader/cli/__init__.py:590` | --lang en обещан для «меню и сообщений», но вывод --import-reference захардкожен по-русски мимо _t() | low | ✅ |
| PAIR-2-01 | `docs/dev/project-structure.md:110` | Дерево project-structure.md: призрачный sandbox/_run_dir.py и три неописанных модуля | low | ✅ |
| PAIR-2-02 | `docs/dev/architecture.md:57` | Инвентарь модулей в docs/dev не покрыт guard'ом, хотя граф и api.md покрыты | low | ✅ |
| PAIR-2-03 | `docs/dev/result-contract.md:133` | result-contract.md не называет фактические ключи SolutionResult | low | ✅ |
| PAIR-2-04 | `src/stepik_grader/core/result.py:7` | Докстринг core/result.py противоречит коду и result-contract.md по типу возврата | low | ◐ |
| PAIR-2-05 | `src/stepik_grader/web/api_routes.py:117` | Докстринг реестра маршрутов занижает число эндпоинтов: 13 GET + 9 POST вместо 14 и 12 | low | ✅ |
| PAIR-2-06 | `docs/dev/architecture.md:126` | Граф зависимостей перечисляет внутрислойные рёбра выборочно, guard их не ловит | low | ◐ |
| PAIR-2-07 | `docs/dev/result-contract.md:138` | Поле cancelled у BenchResult не описано в контракте результата | low | ✅ |
| PAIR-3-01 | `docs/dev/adr/0006-runner-abstraction.md:42` | ADR-0006 и architecture.md ведут точку инъекции Runner в grader_core, реестр живёт в core/runner.py | low | ◐ |
| PAIR-3-02 | `docs/dev/adr/0011-local-persistence.md:85` | ADR-0011 п.5 требует «тихо деградировать», а тот же ADR и код (#794) — называть повреждение вслух | low | ✅ |
| PAIR-3-03 | `docs/dev/architecture.md:71` | architecture.md: microbench описан как прямой subprocess, ребра microbench_runner → runner в DAG нет | low | ✅ |
| PAIR-3-04 | `docs/dev/adr/0002-history-opt-in.md:49` | ADR-0002 фиксирует nudge только после падений, в коде есть второй триггер — серия успехов | low | ✅ |
| PAIR-3-05 | `docs/dev/adr/README.md:27` | Индекс ADR схлопывает статусы: «принято, но не построено» неотличимо от «реализовано» | low | ◐ |
| PAIR-3-06 | `docs/dev/architecture.md:16` | architecture.md и CLAUDE.md называют меню «пункты 0-8», в коде есть пункт 9 | low | ✅ |
| PAIR-3-07 | `docs/dev/adr/0007-sandbox-backends.md:29` | ADR-0007 не фиксирует изъятие трейса из-под sandbox — единственный разрыв контракта Runner | low | ◐ |
| PAIR-3-08 | `CLAUDE.md:244` | CLAUDE.md инвариант 2 перечисляет потребителей atomic_io без core/stats.py | low | ✅ |
| PAIR-4-01 | `README.en.md:169` | README.en.md не предупреждает, что по умолчанию изоляции нет | low | ✅ |
| PAIR-4-03 | `README.en.md:160` | README.en.md обещает у --sandbox «FS isolation» без оговорок из таблицы SECURITY.md | low | ◐ |
| PAIR-4-04 | `SECURITY.md:4` | SECURITY.md шлёт за threat model в configuration.md, где нет веб-периметра | low | ◐ |
| PAIR-4-05 | `docs/dev/adr/0007-sandbox-backends.md:41` | ADR-0007 приписывает Windows-бэкенду ядерный лимит CPU, SECURITY.md — только backstop | low | ✅ |

### Фаза 3 · Канон и дубли

Кто на самом деле источник истины по теме и где копия разошлась с оригиналом.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| CANON-2-01 | `src/stepik_grader/core/sandbox/_linux.py:72` | Песочница монтирует весь venv: сторонние пакеты работают вопреки трём документам | medium | ✅ |
| CANON-3-01 | `src/stepik_grader/core/test_loader.py:182` | Формат 3: блоки спариваются позиционно, номера маркеров # TEST_N: игнорируются | medium | ✅ |
| CANON-3-03 | `src/stepik_grader/core/test_loader.py:205` | Неполная пара файлов в форматах 1/2 отбрасывается молча — прогон остаётся «зелёным» | medium | ✅ |
| CANON-1-01 | `scripts/version.py:105` | version.py берёт любой git-тег без --match v[0-9]*: нерелизный тег роняет вычисление версии | low | ✅ |
| CANON-1-02 | `scripts/version.py:112` | version.py тихо выдаёт 0.0.N без тегов, и CI коммитит это в README-бейдж | low | ◐ |
| CANON-1-03 | `scripts/check_version_consistency.py:206` | check_version_consistency при отсутствии тегов печатает "OK ... docs match the baseline", ничего не сверив | low | ◐ |
| CANON-1-04 | `docs/use/versions.md:6` | Указатели на канон версионирования ведут в CONTRIBUTING.md, а канон — docs/dev/versioning.md | low | ◐ |
| CANON-1-05 | `docs/dev/versioning.md:116` | Канон не упоминает, что гейт версий проверяет ещё и docs/use/versions.md | low | ✅ |
| CANON-1-06 | `CLAUDE.md:329` | CLAUDE.md пересказывает подсчёт PATCH и теряет --first-parent — половину определения | low | ✅ |
| CANON-2-02 | `src/stepik_grader/core/sandbox/_linux.py:111` | Под --sandbox документированное max_memory_mb=None («без лимита») молча становится 1024 МБ | low | ◐ |
| CANON-2-03 | `CLAUDE.md:376` | Канон по безопасности не определён: CLAUDE.md вообще не упоминает SECURITY.md | low | ✅ |
| CANON-2-04 | `docs/use/configuration.md:375` | Безусловное «изоляции ФС/сети нет» противоречит разделам про --sandbox в тех же файлах | low | ◐ |
| CANON-2-05 | `docs/use/configuration.md:99` | max_memory_mb описан как POSIX-only, хотя на Windows под --sandbox задаёт лимит Job Object | low | ✅ |
| CANON-2-06 | `src/stepik_grader/core/sandbox/__init__.py:53` | Отказ трейса под --sandbox обоснован утверждением, неверным для macOS/Windows | low | ◐ |
| CANON-3-02 | `docs/use/configuration.md:283` | Канон описывает вывод downloader как формат 1, а ZIP-ветка пишет формат 3 | low | ✅ |
| CANON-3-04 | `src/stepik_grader/core/test_loader.py:198` | Смешение форматов 1 и 2 в одной папке склеивает кейсы с дублирующимися номерами | low | ✅ |
| CANON-3-05 | `src/stepik_grader/core/mode_detector.py:234` | *.type документирован как признак отдельного теста, применяется ко всей папке | low | ◐ |
| CANON-3-06 | `docs/dev/corpus.md:24` | corpus.md обещает регрессионную защиту мутациями, а корпус пуст и стенд не в CI | low | ◐ |
| CANON-3-07 | `src/stepik_grader/core/test_loader.py:163` | Предупреждения загрузчика тестов — на английском и мимо _console | low | ✅ |
| CANON-3-08 | `src/stepik_grader/core/test_loader.py:250` | resolve_test_dir: четыре стратегии поиска в коде против трёх в docstring и одной в каноне | low | ✅ |
| CANON-4-01 | `src/stepik_grader/glossary/coverage.py:88` | Coverage считает покрытым чужой API по «хвосту» имени: enum.property, operator.abs, io.open | low | ◐ |
| CANON-4-02 | `src/stepik_grader/glossary/coverage.py:165` | В очередь пополнения как «официальный Python» попадает удалённый API (re.template) | low | ✅ |
| CANON-4-03 | `CLAUDE.md:506` | CLAUDE.md сам хардкодит знаменатель покрытия «0/909» — фактически 995 | low | ✅ |
| CANON-4-04 | `docs/dev/glossary.md:178` | Документированный способ «сверить число карточек локально» не считает карточки | low | ◐ |
| CANON-4-05 | `tests/test_glossary.py:200` | Ratchet на витрину ловит только домен artvsmark.github.io, docs_url ничем не ограничен | low | ✅ |
| CANON-4-06 | `docs/use/web-interface.md:247` | web-interface.md вписывает «~1349 ready-карточек» руками вопреки запрету хардкода | low | ✅ |
| CANON-4-07 | `src/stepik_grader/core/glossary.py:11` | Размер компактной карты исключений разошёлся: код говорит ~30, доки ~28, фактически 28 | low | ◐ |
| CANON-5-01 | `docs/dev/web-contracts.md:258` | web-contracts.md объявляет отменённое правило web→core: прямые вызовы core вместо фасада web/grading | low | ✅ |
| CANON-5-02 | `docs/dev/design/web-design.md:149` | Статус-трекер web-design.md числит command palette реализованной, хотя она удалена | low | ✅ |
| CANON-5-03 | `docs/use/web-interface.md:415` | Доки называют CSP строгим и запрещающим инлайн-стили, хотя style-src разрешает 'unsafe-inline' | low | ✅ |
| CANON-5-04 | `docs/dev/api.md:5` | api.md и web-design.md адресуют реализацию эндпоинтов к web/server.py, где их нет | low | ✅ |
| CANON-5-05 | `docs/dev/api.md:110` | Контракт ответа GET / описан не в api.md: три из четырёх инжектируемых флагов вне раздела | low | ✅ |
| CANON-5-06 | `src/stepik_grader/web/server.py:229` | Докстринга run_server описывает установку runner'а через приватный grader_core._RUNNER | low | ✅ |

### Фаза 3 · Путь читателя

Пять читателей проходят проект насквозь: новичок, контрибьютор, агент, безопасник, англоязычный.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| READER-1-01 | `src/stepik_grader/cli/__init__.py:448` | CLI всегда возвращает exit 0 — раздел «для CI/скриптов» обещает то, чего нет | medium | ✅ |
| READER-2-05 | `CONTRIBUTING.md:198` | Блок § Установка — копипаст-ловушка: Windows-активация раскомментирована, macOS/Linux — в комментарии | medium | ✅ |
| READER-1-02 | `README.md:139` | README даёт первую команду проверки без упоминания обязательной папки tests/ | low | ◐ |
| READER-1-03 | `README.md:143` | Первый экран зовёт в пункт 8, но нигде не сказано про своё OAuth-приложение Stepik | low | ◐ |
| READER-1-04 | `src/stepik_grader/core/locales/ru.json:148` | Ошибка OAuth ведёт на docs/use/installation.md, которого у pipx-пользователя нет | low | ✅ |
| READER-1-05 | `src/stepik_grader/core/reporter.py:167` | Документ обещает в колонке «Fail test» прочерк, грейдер печатает None | low | ✅ |
| READER-1-06 | `docs/use/grader-workflow.md:596` | «Шаг скачивания задачи» описывает первый запуск без шага ввода Client id/secret | low | ◐ |
| READER-2-01 | `CONTRIBUTING.md:230` | pytest на подмножестве тестов падает из-за глобального fail_under=85 — онрамп рвётся на первом цикле правки | low | ✅ |
| READER-2-02 | `CONTRIBUTING.md:35` | Список «локальных гейтов, зеркалящих CI» неполон: 4 команды против 6 guard-скриптов в ci.yml | low | ◐ |
| READER-2-03 | `CONTRIBUTING.md:332` | Требование «язык артефактов — русский» есть только в CLAUDE.md — контрибьютор узнаёт о нём на ревью | low | ✅ |
| READER-2-04 | `CONTRIBUTING.md:29` | В онрампе нет шага получения кода: git clone есть только в docs/use/installation.md, ссылки на него нет | low | ✅ |
| READER-2-06 | `CONTRIBUTING.md:83` | Правило размещения файлов описывает пакет cli/ по устаревшему составу — новый CLI-модуль уйдёт не туда | low | ◐ |
| READER-2-07 | `CLAUDE.md:162` | CLAUDE.md указывает несуществующую точку входа cli.py (фактически пакет cli/) | low | ✅ |
| READER-2-08 | `CONTRIBUTING.md:279` | Дублированный фрагмент фразы в описании e2e-тестов | low | ✅ |
| READER-3-01 | `CLAUDE.md:162` | CLAUDE.md называет точкой входа несуществующий cli.py | low | ✅ |
| READER-3-02 | `CLAUDE.md:179` | «Перед коммитом (зеркалит CI)» не зеркалит CI: нет шести блокирующих гардрейлов | low | ✅ |
| READER-3-03 | `CLAUDE.md:399` | «Первый непустой источник и есть план» делает docs/audit/ и очередь недостижимыми | low | ◐ |
| READER-3-04 | `CONTRIBUTING.md:99` | CONTRIBUTING «правило одной строки» противоречит инварианту top-level leaf'ов (ADR-0011) | low | ✅ |
| READER-3-05 | `CLAUDE.md:195` | CLAUDE.md не знает про stepik-grader-gui и pytest-плагин — две точки входа вне контракта | low | ◐ |
| READER-3-06 | `CLAUDE.md:390` | В таблице «Источники истины» нет строки для docs/agent/local-sweep.md | low | ◐ |
| READER-3-07 | `CLAUDE.md:506` | Абзац, запрещающий хардкод чисел, сам содержит устаревшее число: 909 вместо 995 | low | ✅ |
| READER-4-01 | `SECURITY.md:64` | SECURITY.md заявляет «ничего не уходит в сеть» — в проекте четыре канала исходящего трафика | low | ◐ |
| READER-4-02 | `SECURITY.md:64` | SECURITY.md указывает историю прогонов «в рабочей папке», дефолт — ~/.stepik-grader/history.db | low | ✅ |
| READER-4-03 | `SECURITY.md:64` | Инвентарь локальных данных неполон: consent, кэш, очередь глоссария, диагностика | low | ◐ |
| READER-4-04 | `SECURITY.md:34` | Не назван главный локальный риск: решение читает secrets.json с OAuth-токеном Stepik | low | ◐ |
| READER-4-05 | `docs/use/configuration.md:376` | Threat model разошлась с кодом: env дочернего процесса скрабится по denylist | low | ✅ |
| READER-4-06 | `SECURITY.md:120` | Свежесть supply-chain опирается на джоб с continue-on-error — CVE не даёт ни красного, ни алерта | low | ◐ |
| READER-4-07 | `docs/dev/logging.md:86` | Правила редакции лога не покрывают AI-канал: ai_hints нет в списке модулей | low | ✅ |
| READER-5-01 | `README.en.md:160` | В README.en.md нет секции безопасности: EN-читатель не узнаёт, что изоляции нет по умолчанию | low | ◐ |
| READER-5-02 | `README.en.md:112` | Секция про веб в README.en.md не ведёт никуда: docs/use/web-interface.md недостижим из EN | low | ✅ |
| READER-5-03 | `README.en.md:192` | «Full reference» из README.en.md молча уводит в русскоязычные docs/ без предупреждения | low | ✅ |
| READER-5-04 | `SECURITY.md:23` | SECURITY.md: английская врезка покрывает только канал репорта, скоуп и non-goals — лишь по-русски | low | ✅ |
| READER-5-05 | `README.en.md:66` | README.en.md даёт только шим stepik-grader — без запасного `python -m stepik_grader` | low | ◐ |
| READER-5-06 | `README.en.md:3` | EN-витрина без бейджей PyPI/Release/Python, без статуса проекта и требования Python 3.12+ | low | ✅ |
| READER-5-07 | `src/stepik_grader/web/static/locales/ui.json:493` | 14 английских строк UI набраны русскими ёлочками «…» — калька с RU в витринной локали | low | ✅ |

### Фаза 4 · Ревизия аудита 2026-07-30: что вернулось

Находки прошлого аудита против сегодняшнего кода. `bug` здесь — вернувшийся дефект.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| REV-1-01 | `src/stepik_grader/core/test_loader.py:187` | DIVE-02 вернулась: stdin-данные со скобками («Anna(20)», «H2O(l)») уезжают на function-маршрут | high | ✅ |
| REV-1-02 | `src/stepik_grader/core/test_loader.py:116` | PY-03 закрыта не везде: test_loader читает .clue/.type/input.txt без errors=replace | medium | ✅ |
| REV-1-03 | `src/stepik_grader/core/history.py:354` | Миграция истории 1→2 — check-then-act без BEGIN IMMEDIATE: конкурентный backfill удваивает task_progress | medium | ✅ |
| REV-1-04 | `src/stepik_grader/core/history.py:153` | MIGR-03 закрыта частично: «повреждена» ловится 4 подстроками, прочие сбои снова = «истории нет» | medium | ✅ |
| REV-3-01 | `.github/workflows/release.yml:110` | github-release: checkout идёт ПОСЛЕ download-artifact и стирает dist/ — релиз без ассетов | medium | ✅ |
| REV-3-02 | `src/stepik_grader/web/api_routes.py:845` | Web-путь AI-подсказки не проверяет получателя согласия (SECD-02 закрыт только в CLI) | medium | ✅ |
| REV-3-06 | `src/stepik_grader/core/ai_hints.py:280` | AI-канал следует редиректам без ревалидации: правило https/loopback обходится 307-ым | medium | ✅ |
| REV-4-04 | `src/stepik_grader/web/static/content.js:303` | DESW-02 не закрыта для двух списков глоссария: сбой сети рендерится как «пропусков нет» | medium | ✅ |
| REV-6-04 | `docs/archive/audit-2026-07-30-full-roles.md:24` | Аудит 2026-07-30 переехал в archive с ~65 находками §5 без состояния | medium | ✅ |
| REV-1-05 | `src/stepik_grader/core/test_loader.py:214` | Форматы 1 и 2 в одном каталоге дублируют кейсы: ни предупреждения, ни дедупликации по index | low | ✅ |
| REV-1-06 | `src/stepik_grader/core/grader_core.py:322` | Пользовательские тексты ошибок грейдера по-английски — нарушение инварианта «артефакты по-русски» | low | ✅ |
| REV-2-01 | `scripts/check_version_consistency.py:189` | Гейт дрейфа версий молча самоотключается при любой ошибке git (SKIP + exit 0) | low | ✅ |
| REV-2-02 | `scripts/check_docs_guardrails.py:570` | UI-strings guard зелёный при нулевом входе: переезд options.py или locales/ обнуляет проверку | low | ✅ |
| REV-2-03 | `scripts/check_docs_guardrails.py:345` | check_showcase_metrics молча пропускает исчезнувший README — та же дыра нулевого входа | low | ✅ |
| REV-2-04 | `.github/workflows/ci.yml:283` | Бейдж «coverage (ubuntu)» считается по CI-конфигу с доп. omit — систематически выше локального pytest | low | ✅ |
| REV-2-05 | `.github/workflows/ci.yml:204` | Cross-OS гейт покрытия 90% выключается падением flaky sandbox-linux, job остаётся зелёным | low | ◐ |
| REV-2-06 | `scripts/generate_ci_coveragerc.py:76` | generate_ci_coveragerc копирует ровно 4 ключа — новый параметр coverage не доедет до CI | low | ✅ |
| REV-3-03 | `src/stepik_grader/core/stepik_client.py:285` | Лимит внешней загрузки проверяется после чтения тела в память — без Content-Length OOM остался | low | ✅ |
| REV-3-04 | `src/stepik_grader/core/stepik_client.py:350` | refresh_access_token ходит мимо retry-сессии: единичный 503 роняет скачивание и отправку | low | ✅ |
| REV-3-05 | `.github/workflows/release.yml:16` | Job'ы verify и build в release.yml без permissions — токен по умолчанию при прогоне pytest | low | ✅ |
| REV-4-01 | `src/stepik_grader/core/reporter.py:593` | DESC-02 закрыта наполовину: строка [ERROR] в verbose печатается без обрезки (до 10 МБ) | low | ✅ |
| REV-4-02 | `src/stepik_grader/core/reporter.py:564` | Зашитые русские хвосты обрезки в print_case_verbose ломают --lang en | low | ✅ |
| REV-4-03 | `src/stepik_grader/web/static/core.js:642` | FER-04 не перенесён на модалку AI-согласия: focus-trap на оверлее, Escape отваливается | low | ✅ |
| REV-4-05 | `src/stepik_grader/web/static/core.js:500` | Загрузка каталога локали ui.json не проверяет HTTP-статус и падает молча | low | ◐ |
| REV-5-01 | `docs/archive/audit-2026-07-30-full-roles.md:593` | Реестр отклонённых находок обрезан посреди слова — отклонение невозможно перепроверить | low | ◐ |
| REV-5-02 | `tests/test_glossary_draft_pipeline.py:700` | Ratchet глоссария сверяет только id: новое расхождение у 150 разрешённых карточек невидимо | low | ✅ |
| REV-5-03 | `CONTRIBUTING.md:94` | CONTRIBUTING отправляет любой внутренний модуль в core/, ломая leaf-инвариант ADR-0011 | low | ✅ |
| REV-5-04 | `tests/test_import_dag.py:682` | Guard architecture.md проверяет только рёбра графа — таблица модулей дрейфует безнаказанно | low | ✅ |
| REV-6-01 | `src/stepik_grader/core/microbench_runner.py:257` | SECC-01 закрыта не на всех путях: bench-скрипт микробенча остаётся в общем /tmp и он же — sys.path[0] | low | ◐ |
| REV-6-02 | `src/stepik_grader/core/sandbox/_linux.py:117` | PY-13 закрыта наполовину: RLIMIT_FSIZE песочницы по-прежнему из CONFIG, а не из RunSpec | low | ◐ |
| REV-6-03 | `docs/archive/audit-2026-07-30-full-roles.md:196` | MIGR-01 фактически закрыта кодом (SchemaTooNewError), но архивный аудит числит её открытой | low | ✅ |
| REV-6-05 | `pyproject.toml:302` | Комментарий о покрытии scripts/ заморожен на #790: «15 файлов» и 88% против 25 скриптов | low | ✅ |

### Фаза 4 · Непроверенные оси связей

Ссылки из кода в документы, документы против трекера и против `.github`.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| LINK-1-01 | `src/stepik_grader/core/history.py:5` | Ссылка `docs/audit-2026-07.md` битая в 4 местах кода — файл переехал в docs/archive/ | low | ✅ |
| LINK-1-02 | `src/stepik_grader/web/static/trace-player.js:207` | В web-статике ссылки на `docs/trace-format.md` без направления `dev/` — цели и якоря нет | low | ✅ |
| LINK-1-03 | `scripts/check_docs_guardrails.py:417` | CI-guard ротации CHANGELOG печатает несуществующий путь docs/changelog-archive.md | low | ✅ |
| LINK-1-04 | `scripts/check_docs_guardrails.py:180` | Guard ссылок покрывает только *.md — ссылки из кода в документы не проверяет никто | low | ◐ |
| LINK-1-05 | `src/stepik_grader/core/locales/ru.json:148` | Сообщения об ошибках шлют в docs/, которых нет при pipx/pip-установке | low | ◐ |
| LINK-1-06 | `src/stepik_grader/web/static/grade.js:17` | Ссылка на контракт вердиктов в grade.js ведёт на docs/result-contract.md вместо docs/dev/ | low | ✅ |
| LINK-2-01 | `docs/dev/adr/0001-server-mode.md:6` | ADR-0001 и ещё три ADR отправляют читателя за server mode в закрытый эпик #151 | low | ◐ |
| LINK-2-02 | `docs/audit/2026-08-10-full-roles.md:1` | Документ живого аудита не связан с эпиком #915 — из находки нет пути в трекер | low | ◐ |
| LINK-2-03 | `docs/audit/2026-08-10-full-roles.md:20` | У 477 находок аудита нет состояния и номера PR — правило docs/audit/README.md не соблюдено | low | ◐ |
| LINK-2-04 | `CLAUDE.md:344` | Правило CHANGELOG в CLAUDE.md требует «(#PR)», а весь файл ссылается на issue | low | ✅ |
| LINK-2-05 | `CLAUDE.md:392` | Таблица «Источники истины» ведёт на самый старый аудит, живой и предыдущий не упомянуты | low | ◐ |
| LINK-2-06 | `docs/dev/adr/0003-ai-integration.md:124` | ADR-0003 отсылает за деталями флага в закрытые issue #435/#438 вместо документации | low | ◐ |
| LINK-2-07 | `docs/dev/adr/0011-local-persistence.md:3` | ADR-0011 называет issue #551/#552 «merged» — это номера задач, а не PR | low | ✅ |
| LINK-3-01 | `docs/dev/versioning.md:113` | versioning.md зовёт ротацию CHANGELOG гейтом релиза, но release.yml не запускает check_docs_guardrails | low | ◐ |
| LINK-3-02 | `.github/workflows/release.yml:141` | «Забытая запись роняет релиз»: падает только GitHub Release, PyPI публикуется | low | ◐ |
| LINK-3-03 | `.github/ISSUE_TEMPLATE/bug_report.yml:53` | Ни один шаблон issue не ставит area/* и difficulty/*, хотя политика объявляет их обязательными | low | ◐ |
| LINK-3-04 | `docs/dev/supply-chain.md:141` | supply-chain.md: у AI-workflow заявлено «только чтение», фактически объявлен id-token: write | low | ✅ |
| LINK-3-05 | `docs/dev/supply-chain.md:120` | Инвентарь actions: upload/download-artifact приписан только ci.yml, а он несёт dist в release.yml | low | ✅ |
| LINK-3-06 | `.github/dependabot.yml:27` | Метки area/ci и downloader используются в .github, но отсутствуют в каноническом словаре | low | ◐ |
| LINK-3-07 | `docs/dev/project-structure.md:138` | project-structure.md описывает .github одной строкой ci.yml | low | ✅ |

### Фаза 4 · Почему прошлый аудит промахнулся

Разбор метода: какие зоны и способы проверки отсутствовали, из-за чего `high` остались невидимыми.

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| REV-7-04 | `src/stepik_grader/core/microbench_runner.py:198` | Прогонные срезы планируются от CLI-меню, поэтому режим 4 (микробенч) не запускался ни разу | medium | ◐ |
| REV-7-01 | `docs/archive/audit-2026-07-30-full-roles.md:139` | Загрузчик тест-кейсов не был зоной ни одного из 30 срезов 2026-07-30 — оттуда пришли high следующего аудита | low | ◐ |
| REV-7-02 | `src/stepik_grader/core/test_loader.py:205` | Читающие срезы не искали тихие ветки: находки прошли в двух строках от дефектов и не заметили их | low | ✅ |
| REV-7-03 | `src/stepik_grader/core/history.py:709` | Код, дописанный по находкам прошлого аудита, следующим аудитом не проверяется как отдельная зона | low | ✅ |
| REV-7-05 | `docs/dev/corpus.md:54` | Корпус мутирует только решение — класс «испорчена постановка задачи» ему недостижим по построению | low | ✅ |
| REV-7-06 | `docs/audit/2026-08-10-full-roles.md:93` | Итоговое число подтверждённых high расходится между двумя каноничными документами одного аудита: 8 против 16 | low | ✅ |

---


---

## 5-тер. Фаза 5: браузерный прогон веб-интерфейса

Пятая фаза появилась не по плану, а по замечанию владельца: веб — основная поверхность продукта,
а браузер за весь аудит не открывался ни разу. Проверка шла запросами к API и чтением JS, то есть
класс дефектов «в коде верно, в браузере ломается» не проверял никто.

Причина промаха — та же, что фаза 4 нашла у предыдущего аудита (REV-7-04): **прогонные срезы
планировались от точек входа CLI**, и браузер в этот список не попал. Урок был записан в документ
и тут же повторён.

**Метод.** Пять агентов, по одной роли на срез, реальный Chromium через Playwright: клики,
ввод, переключение вкладок, перехват `console`, `pageerror` и ответов 4xx/5xx, скриншоты.
Каждому — свой порт, свой рабочий каталог и готовая фикстура (два тест-кейса, три решения:
верное, неверное, медленное).

**Результат — 34 находки, из них 18 medium и выше по авторской оценке.** После верификации:
27 CONFIRMED, 6 PARTIAL, 1 REFUTED; четыре подтверждённых `medium`. Плотность значимого выше,
чем у документационной фазы, при вчетверо меньшем объёме.

Что нашлось только глазами и принципиально недостижимо чтением:

- **многострочный пример в карточке глоссария распадается на строки-плитки** без отступов —
  вложенность теряется, учебный контент показывает неверное (`content.js:401`);
- **режим «Папка», включённый по умолчанию, не засчитывает решённую задачу**, если рядом в папке
  лежит падающий файл — черновики рядом с решением ломают «Прогресс» (`history.py:478`);
- **плашка истории перекрывает таблицу результатов и глотает клики** по тест-кейсам на 1280×720;
- **мусорный URL в Загрузчике диагностируется как проблема с `secrets.json`** — пользователь идёт
  чинить токен вместо адреса;
- **Markdown в правилах PEP печатается буквально**, со звёздочками и обратными кавычками;
- **EN-локаль не переводит ни заголовки карточек глоссария, ни названия 36 правил**, хотя счётчик
  «36 rules» переведён;
- на ширине 390 px **переключатель RU/EN схлопывается в полоску 6 px**;
- контраст ссылки «→ правило» на тёмной теме — **2.01:1** при норме 4.5:1.

Состояние находок ниже — «открыта», как и в остальных разделах.

### Вкладка «Проверка решений»: четыре режима через интерфейс

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| BRW-1-01 | `src/stepik_grader/web/runs.py:404` | Режим 1: в отчёте и озвучке стоит имя временного файла вместо выбранного решения | medium | ✅ |
| BRW-1-02 | `src/stepik_grader/web/static/grade.js:528` | Пустой путь: «Запустить» молча ничего не делает и оставляет на экране прошлый результат | low | ✅ |
| BRW-1-03 | `src/stepik_grader/web/static/app.css:920` | Плашка #history-notice перекрывает таблицу результатов и глотает клики по тест-кейсам | low | ✅ |
| BRW-1-04 | `src/stepik_grader/core/microbench_runner.py:268` | Микробенчмарк даёт ERR по 60-секундному таймауту на верном решении с задержкой | low | ◐ |
| BRW-1-05 | `src/stepik_grader/web/static/index.html:6` | Каждая загрузка страницы даёт 404 на /favicon.ico и ошибку в консоли браузера | low | ✅ |
| BRW-1-06 | `src/stepik_grader/web/static/grade.js:976` | KPI-карточка «OK» красит 0% зелёной положительной дельтой при полном провале | low | ✅ |
| BRW-1-07 | `src/stepik_grader/web/static/grade.js:977` | Статус NO TESTS засчитывается как FAIL в KPI, бейджах и aria-озвучке | low | ✅ |
| BRW-1-08 | `src/stepik_grader/web/static/grade.js:1000` | Строка результата раскрывается только кликом по ячейке с именем файла | low | ◐ |

### Вкладки «Загрузчик задач» и «Песочница»

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| BRW-2-01 | `src/stepik_grader/web/static/downloader.js:337` | Загрузчик: клик «Скачать» с пустым URL — молчание, готовое серверное сообщение недостижимо | low | ✅ |
| BRW-2-02 | `src/stepik_grader/web/downloader_adapter.py:171` | Загрузчик: мусорный URL и URL чужого сайта диагностируются как проблема с secrets.json | low | ◐ |
| BRW-2-03 | `src/stepik_grader/web/static/trace-player.js:12` | Песочница: трейс бесконечного цикла даёт «Ошибка выполнения» и сырое английское «exceeded 10.0s» | low | ✅ |
| BRW-2-04 | `src/stepik_grader/web/static/sandbox.js:8` | Песочница: F5 стирает набранный код и stdin, хотя раздел восстанавливается | low | ✅ |
| BRW-2-05 | `src/stepik_grader/web/static/index.html:7` | Каждая загрузка веб-интерфейса даёт 404 на /favicon.ico и ошибку в консоли | low | ✅ |
| BRW-2-06 | `src/stepik_grader/web/static/downloader.js:92` | Загрузчик: «Сохранить» пустого значения в строке «Куда скачивать» — тихий no-op | low | ✅ |
| BRW-2-07 | `src/stepik_grader/web/playground.py:35` | Песочница: длинный вывод режется посреди строки, метка обрезки теряется под портянкой | low | ◐ |

### Вкладки «Глоссарий» и «Правила (PEP)»

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| BRW-3-01 | `src/stepik_grader/web/static/content.js:401` | Многострочный пример в карточке глоссария распадается на строки-плитки без отступов | medium | ✅ |
| BRW-3-02 | `src/stepik_grader/web/static/content.js:576` | Разметка Markdown в теле правила PEP выводится буквально (**…**, обратные кавычки) | low | ✅ |
| BRW-3-03 | `src/stepik_grader/glossary/models.py:310` | В EN-локали заголовки карточек глоссария остаются русскими, релевантность поиска ломается | low | ✅ |
| BRW-3-04 | `src/stepik_grader/web/static/content.js:366` | Битая ссылка на карточку глоссария молча показывает «Карточка не выбрана» | low | ✅ |
| BRW-3-06 | `src/stepik_grader/web/static/content.js:399` | У примеров кода в карточках глоссария и правил нет кнопки копирования | low | ✅ |
| BRW-3-07 | `src/stepik_grader/web/static/index.html:7` | Каждая загрузка страницы даёт 404 /favicon.ico и ошибку в консоли | low | ✅ |

### Вкладки «Подучить» и «Прогресс»

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| BRW-4-01 | `src/stepik_grader/web/viewmodels.py:805` | Режим «Папка» (по умолчанию): задача не решена, если хоть один файл в папке падает | medium | ✅ |
| BRW-4-03 | `src/stepik_grader/core/history.py:647` | Свежая рабочая папка показывает чужую историю: ключ задачи — голое имя папки | medium | ✅ |
| BRW-4-02 | `src/stepik_grader/core/insights.py:237` | Колонка «Попыток» в «Прогрессе» замирает после первого зачёта и не растёт | low | ◐ |
| BRW-4-04 | `src/stepik_grader/web/static/content.js:645` | Карточка правила в «Подучить» — голый код без названия и совета | low | ✅ |
| BRW-4-05 | `src/stepik_grader/core/insights.py:276` | Бейдж «Первая AC» выдан при «Решено задач 0/1» — противоречие на одном экране | low | ✅ |
| BRW-4-06 | `src/stepik_grader/core/insights.py:141` | «Подучить» не показывает, в какой задаче ошибка, и не ведёт к упавшему кейсу | low | ✅ |

### Сквозное по восьми вкладкам: тема, язык, доступность

| ID | file:line | Находка | Итог | ✓ |
|---|---|---|---|---|
| BRW-5-01 | `src/stepik_grader/web/static/app.css:624` | Ссылка «→ правило» в «Подучить» рисуется браузерным синим — на тёмной теме контраст 2.01:1 | low | ✅ |
| BRW-5-02 | `src/stepik_grader/web/static/core.js:367` | Переключение RU→EN не переводит подсказку в редакторах кода до перезагрузки | low | ✅ |
| BRW-5-03 | `src/stepik_grader/web/static/content.js:532` | Раздел «Правила (PEP)» под EN: счётчик переведён, названия всех 36 правил остаются русскими | low | ✅ |
| BRW-5-04 | `src/stepik_grader/web/static/app.css:260` | На ширине 390px переключатель RU/EN схлопывается в полоску 6px и визуально исчезает | low | ✅ |
| BRW-5-05 | `src/stepik_grader/web/static/index.html:10` | Skip-link «Перейти к содержимому» затирает hash-адрес раздела на #main-content | low | ✅ |
| BRW-5-06 | `src/stepik_grader/web/static/app.css:481` | Фокус на текстовых полях показан только тенью 18% прозрачности, outline снят | low | ◐ |

---

## 5-квater. Закрытые находки: реестр PR

Реестр вместо пометок в самих таблицах: находка упоминается в нескольких срезах, и «✅» рядом с одной
строкой оставляла бы остальные вводить в заблуждение. Здесь — единственное место, где смотрят состояние.

**Сверка 26.08.2026.** Реестр отстал от `main` на 152 записи: PR закрывали находки, не дописывая сюда
строку, и документ числил закрытое открытым — ровно тот отказ, из-за которого правило «чек-лист вместо
перечисления скопом» появилось в [CONTRIBUTING.md](../../CONTRIBUTING.md). Сверка машинная: тела всех
смерженных pull request против ID находок этого документа, с разбором формулировки — «закрывает» против
«остаётся». Находки, которые PR назвал оставшимися, сюда не попали: `SEC-2-03`, `CANON-3-05`, `MTX-5-01`,
`READER-4-04`, `ED-2-04`, `PROD-1-04`, `LINK-3-01`, `LINK-3-04`, `REV-2-05`, `DES-1-04`, `REL-1-03`,
`READER-1-04` (закрыта половина про URL).

| ID | Находка (кратко) | PR |
|---|---|---|
| INS-4-01 | Битый/чужой pyproject.toml выше по дереву меняет прогон и роняет грейдер | #1009 |
| INS-4-02 | pyproject.toml из чужой родительской папки переворачивает вердикт (TLE) | #1009 |
| INS-1-01 | Битый pyproject.toml над cwd рушит трейсбеком каждую команду | #1009 |
| LNCH-3-04 | Конфиг якорится на cwd, веб-настройки — на --root: два корня настроек | #1009 |
| SBX-5-01 | Кэш отдаёт обычному прогону вердикт, порождённый изоляцией | #1011 |
| CNC-5-05 | --sandbox вместе с --cache не заходит в песочницу вовсе | #1011 |
| PY-3-01 | Ключ кэша не учитывает условия исполнения | #1011 |
| PERF-1-01 | Устаревший вердикт при смене timeout_seconds/--sandbox | #1011 |
| PY-3-02 | cache.save() роняет грейдинг OSError вопреки инварианту | #1011 |
| MTX-3-01 | Режим 4 переворачивает рейтинг: tracemalloc штрафует аллокации | #1028 |
| PERF-1-02 | То же смещение замера, систематическое, а не случайное | #1028 |
| REL-1-02 | Ни в одном workflow нет concurrency-группы | #1038 |
| STR-2-01 | CI-веер: OS-независимые гейты гоняются девять раз | #1038 |
| OPS-1-02 | save_json пишет ответы API без редакции секретов | #1051 |
| RUN-4-03 | diagnostic_stepik --help падает EOFError вместо справки | #1051 |
| RUN-5-03 | То же при закрытом stdin | #1051 |
| JRN-3A-03 | diagnostic_stepik завершается кодом 0 при любом провале | #1051 |
| JRN-3A-05 | Приглашения диагностики по-английски вперемешку с русским | #1051 |
| OPS-1-03 | При сбое диагностики не сообщается путь к логу | #1051 |
| DEV-3-03 | diagnostic_stepik печатает authorize URL без state | #1022 |
| INS-5-01 | downloader --help запускает мастер и создаёт stepik_config.json | #1058 |
| RUN-4-06 | То же: мастер вместо справки, падение на EOF с кодом 0 | #1058 |
| TW-3-04 | Документированная команда downloader не разбирает флаги | #1058 |
| INS-3-03 | downloader возвращает 0 после фатального отказа конфига | #1058 |
| RUN-5-05 | downloader выходит с кодом 0 после провала конфига/авторизации | #1058 |
| RUN-5-04 | downloader роняет трейсбек EOFError в цикле ввода URL | #1058 |
| JRN-3A-01 | Битый stepik_config.json — тупик: сообщение без пути и без выхода | #1058 |
| DEV-1-03 | Ctrl+C в интерактивном меню роняет процесс трейсбеком | #1061 |
| PROD-1-03 | То же: KeyboardInterrupt вместо штатного выхода | #1061 |
| DEV-1-05 | Диалог tkinter открывается в неинтерактивном запуске | #1061 |
| INS-5-06 | Справка не отвечает на вопрос «откуда взять задачу» | #1061 |
| JRN-1-04 | Раскладку tests/ узнаёшь только провалившись | #1061 |
| VIS-1-06 | «тали вердиктов» — непереведённая калька в справке и документации | #1061 |
| PROD-1-01 | Ложный «стрик успеха» на несостоявшихся прогонах | #936 |
| DEV-1-01 | main() всегда завершается кодом 0 | #936 |
| READER-1-01 | CLI всегда возвращает exit 0 вопреки разделу «для CI/скриптов» | #936 |
| RUN-1-04 | Код возврата всегда 0: FAIL и NO TESTS неотличимы от OK | #936 |
| RUN-2-06 | Код возврата всегда 0 — FAIL, RE и NO TESTS неотличимы от успеха | #936 |
| RUN-4-05 | Код возврата всегда 0 — CI не отличит провал от опечатки в пути | #936 |
| DEV-1-02 | --sandbox без --mode молча игнорируется | #1064 |
| LNCH-2-01 | То же: меню запускает решения без изоляции | #1064 |
| SBX-4-03 | --sandbox без --mode: в меню OK, под --mode 1 FAIL | #1064 |
| SEC-2-01 | --sandbox без --mode: меню грейдит через LocalRunner | #1064 |
| LNCH-2-03 | --serve игнорирует record_history из pyproject и тумблер | #1064 |
| LNCH-3-01 | --serve игнорирует персистентный опт-аут истории | #1064 |
| LNCH-5-01 | Сохранённый выбор «история ВЫКЛ» игнорируется при --serve | #1064 |
| SET-2-03 | --serve не читает персистентный тумблер истории | #1064 |
| LNCH-3-02 | Один флаг record_history — три несогласованные лестницы | #1064 |
| FZZ-5-04 | _resolve_record_history не читает .grader_settings.json | #1064 |
| SEC-3-02 | revoke_ai_consent отзывает согласие только в текущей папке | #1009 |
| MTX-10-04 | --output json печатает русский текст вместо JSON на ошибках | #1067 |
| RUN-4-04 | «Файлы решений не найдены в: {path}» — плейсхолдер печатается буквально | #1067 |
| SBX-1-04 | То же в прогоне под --sandbox | #1067 |
| DES-2-03 | _rows_to_markdown не экранирует «\|» и переводы строк | #1067 |
| DEV-3-05 | Лимит внешней загрузки проверяется после чтения тела в память | #1069 |
| REV-3-03 | То же: размер проверяется, когда ответ уже целиком в памяти | #1069 |
| DEV-3-04 | wait_for_auth_code обслуживает ровно один HTTP-запрос | #943 |
| DEV-2-01 | Обёртка function-mode связывает аргумент по имени со своим же импортом (date/time) — ложный WA | #1025 |
| RUN-2-01 | Формат 3: stdin-данные вида «x = 5» или «sin(30)» уезжают в function-режим → ложный RE на вер… | #1025 |
| RUN-2-02 | Формат 3 function-mode: вспомогательная функция объявлена раньше целевой → ложный RE «name ..… | #1025 |
| QA-1-04 | Обрезка вывода по max_output_bytes не проверена до вердикта: пользователь получает WA без при… | #1033 |
| RUN-1-02 | Вывод свыше max_output_bytes режется молча: верное решение получает WA без упоминания обрезки | #1033 |
| DEV-2-06 | Bench-скрипт микробенча пишется в общий /tmp — sys.path[0] дочернего процесса подменяем | #1044 |
| FZZ-5-05 | --purge-history падает трейсбеком на том файле, ради которого продукт советует «удалите его» | #1046 |
| JRN-4A-01 | --purge-history <ключ> необратимо стирает историю ДРУГОЙ задачи: ключи разных курсов совпадают | #1046 |
| PROD-2-02 | purge_history падает трейсбеком на пустом/битом файле БД вместо best-effort | #1046 |
| ADD-2-02 | Ответ 200 без access_token отравляет secrets.json: протухшему токену ставят expires_at=now+3600 | #1048 |
| FZZ-2-03 | Решение с выводом не в UTF-8 даёт WA с «██████» и без намёка на кодировку | #1050 |
| DEV-1-07 | Ранний выход «файл/папка не найдены» возвращает False и засчитывается как успешный прогон | #1067 |
| DEV-3-06 | Обрыв сети при обновлении токена выдаётся пользователю за неверные OAuth-учётные данные | #1070 |
| INS-3-04 | `python -m stepik_grader.ide` — тихий no-op с кодом 0 | #1071 |
| STR-1-07 | ide.py: 147 строк и флаг ради статичного JSON без проверки на дрейф флагов | #1071 |
| STR-3-05 | Каталог задачи может произвести только загрузчик со Stepik: нет офлайн-способа завести задачу | #1071 |
| VIS-2-03 | Точка расширения асимметрична: set_runner в фасаде, Runner/RunSpec/RunOutcome — только в core/ | #1071 |
| REV-3-04 | refresh_access_token ходит мимо retry-сессии: единичный 503 роняет скачивание и отправку | #1073 |
| STR-3-03 | Смена формы ответа Stepik маскируется под «шаг не найден» вместо честного «формат изменился» | #1073 |
| STR-3-06 | API_HOST зашит константой и не переопределяем; diagnostic импортирует значение, а не модуль | #1073 |
| CNC-5-01 | Открытое меню воскрешает отозванное AI-согласие: web после этого шлёт код без спроса (HTTP 202) | #1074 |
| CNC-5-04 | Сессия меню откатывает подтверждённую вебом (HTTP 200) запись: окно потери равно всей сессии | #1074 |
| SET-2-02 | save_settings переписывает файл целиком — флаги, записанные другим каналом, теряются | #1074 |
| COM-1-07 | Прогресс не складывается по группе: преподаватель не видит, где застревает курс | #1077 |
| VIS-1-03 | История — тупик для интеграций: --insights молча игнорирует --output, --export-progress без json | #1077 |
| DES-2-08 | Неверный номер профиля в режимах 3/4 молча подменяется на профиль 2 | #1078 |
| JRN-1-05 | «Подучить» в свежей установке показывает чужие карточки из глобальной БД, хотя пункт 7 говори… | #1078 |
| CNC-1-01 | Один и тот же верный код даёт 4 разных вердикта при параллельных прогонах, и TLE залипает в кэше | #1079 |
| CNC-1-03 | Попадание в кэш пишется в .grader_stats.jsonl как полноценный прогон с чужим total_time | #1079 |
| JRN-4A-02 | «Прогресс» рисует одну задачу двумя строками, а соседнюю прячет, отдав ей чужой AC (углублени… | #1081 |
| LNG-1-04 | Одна задача — два task_key в одной БД: дубли task_progress, двойной TTFG, удвоенный лимит ret… | #1081 |
| MTX-4-04 | Ноль загруженных кейсов: неверное решение получает JSON без единого провала | #1082 |
| SBX-1-02 | Соседний модуль решения не попадает в песочницу: 5/5 OK → 0/5 FAIL под --sandbox | #1084 |
| PY-2-01 | CPU-квота песочницы не связана с spec.timeout: прогон длиннее 10 с CPU режется досрочно | #1085 |
| SBX-3-01 | Зависшее решение под --sandbox получает RE вместо TLE: CPU-квота 10 с гасит его раньше wall-c… | #1085 |
| CANON-2-01 | Песочница монтирует весь venv: сторонние пакеты работают вопреки трём документам | #1086 |
| SEC-2-02 | Linux-песочница ro-биндит весь venv: site-packages доступны решению вопреки SECURITY.md | #1086 |
| SBX-4-01 | Сломанный bwrap не отвергается: верное решение получает FAIL/RE со stderr самой песочницы | #1088 |
| CANON-2-02 | Под --sandbox документированное max_memory_mb=None («без лимита») молча становится 1024 МБ | #1089 |
| PROD-2-05 | Окно «Подучить» берётся по последним N прогонам всех задач — чужие успехи архивируют неисправ… | #1090 |
| INS-5-03 | `--lang en --help` печатает целиком русскую справку | #1094 |
| JRN-2-03 | Четыре режима — четыре несовместимые JSON-схемы без пометки режима: --output json непарсим ед… | #1096 |
| SBX-5-04 | JSON-вывод и .grader_stats.jsonl не помечают изоляцию — CI не докажет режим прогона | #1096 |
| JRN-1-03 | Режим 1 принимает solution.py, режимы 2/3/4 его не видят — и сообщают строкой с сырым {path} | #1100 |
| JRN-2-06 | Режим 1 грейдит файл с любым именем, режимы 2/3/4 молча не видят его без шаблона task*.py | #1100 |
| JRN-3A-04 | Занятый локальный порт 8080 выдаётся за неверные OAuth-учётные данные (углубление) | #1101 |
| TRE-1-01 | Детектор reasoning-моделей не знает gpt-5* и deepseek-reasoner — payload отвергается, подсказ… | #1126 |
| TRE-1-03 | Настроенный, но отказывающий AI-провайдер (401/429/таймаут) не даёт пользователю ни одного слова | #1126 |
| REV-1-02 | PY-03 закрыта не везде: test_loader читает .clue/.type/input.txt без errors=replace | #1128 |
| REV-3-02 | Web-путь AI-подсказки не проверяет получателя согласия (SECD-02 закрыт только в CLI) | #1128 |
| ED-2-06 | Английская локаль набрана русскими «ёлочками»; термин Learn закавычен двумя способами в одном… | #1129 |
| ED-2-07 | Орфографическая ошибка в русской подсказке по TLE: «Превышён» вместо «Превышен» | #1129 |
| INS-5-02 | Справка и ошибки зовут в файлы репозитория (docs/*.md, SECURITY.md, README), которых нет посл… | #1129 |
| LINK-1-05 | Сообщения об ошибках шлют в docs/, которых нет при pipx/pip-установке | #1129 |
| SET-3-03 | Таймаут и лимит памяти нельзя задать из CLI — только правкой pyproject.toml, которого у pipx нет | #1137 |
| LNCH-1-02 | Выбор «с изоляцией» молча отключает пошаговый трейс — последствие не показано в точке выбора | #1140 |
| LNCH-1-07 | Веб-онбординг обещает галку sandbox в лаунчере, которой там нет | #1140 |
| LNCH-2-05 | --lang не доходит до веб-UI: страница всегда стартует на ru | #1140 |
| LNCH-5-07 | Три двери и ни одного экрана выбора: способ запуска нигде не предъявлен пользователю | #1140 |
| ED-2-03 | CI-guard полноты каталога не видит CLI и downloader: опечатка в ключе печатает сам ключ, гейт… | #1142 |
| STR-2-02 | --cov=scripts + fail_under=85 в addopts втягивают разовую тулзу мейнтейнера в гейт качества п… | #1144 |
| INS-5-05 | stepik-grader-gui — вторая установленная команда: не упомянута в справке и не знает --help | #1145 |
| LNCH-1-04 | Язык окна нельзя выбрать, а LANG=C даёт английское окно вместо заявленного русского fallback | #1145 |
| PROD-1-05 | Лаунчер локализован, а его ошибки и дочерний сервер — нет: англоязычный пользователь видит ру… | #1145 |
| LNCH-1-05 | «Порт занят — выберите другой»: тупик без данных, хотя лаунчер умеет проверять порты | #1146 |
| LNCH-5-05 | Занятый порт в лаунчере — тупик вместо действия, хотя чаще всего там уже наш сервер | #1146 |
| ARCH-3-03 | Миграция схемы 1→2 не идемпотентна при параллельных CLI и web: агрегат task_progress удваивается | #1147 |
| OPS-1-08 | Перескачивание задачи безвозвратно стирает вручную дописанные тест-кейсы без бэкапа | #1148 |
| LNCH-1-03 | Пользователь без tkinter/дисплея не видит ничего: совет уходит в stdout GUI-процесса без консоли | #1150 |
| LNCH-1-06 | «Найдено задач: 0» — дедэнд без следующего шага | #1150 |
| LNCH-5-06 | Headless-ветка лаунчера советует набрать флаг вместо того, чтобы запустить | #1150 |
| PKG-1-05 | gui-scripts: сообщение об отказе лаунчера уходит в несуществующую консоль | #1150 |
| LNCH-1-08 | Выбор не запоминается между запусками, включая режим изоляции | #1152 |
| LNCH-5-02 | Лаунчер забывает выбор способа запуска: порт, папка и изоляция сбрасываются каждый раз | #1152 |
| ED-2-08 | Плюрализация core-локалей подделана скобками run(s)/entry(ies) при готовом механизме форм в вебе | #1153 |
| MTX-3-05 | Режимы 3 и 4 при отбраковке не называют номер провалившегося кейса, режимы 1/2 называют | #1153 |
| RUN-5-06 | Сообщение про --watch советует чужое имя пакета stepik-grader[watch] | #1153 |
| FZZ-5-06 | «Статистика выключена» — сообщение, когда журнал есть, но все записи битые | #1155 |
| LNCH-3-05 | UserSettings: поля перечислены руками в трёх местах — новое поле молча не сохраняется | #1159 |
| LNCH-3-06 | set_flag — read-modify-write всего файла настроек без блокировки: потеря обновления | #1159 |
| LNCH-2-02 | --serve падает трейсбеком при занятом или некорректном порте | #1162 |
| LNCH-2-04 | --serve молча съедает --mode/--file/--stats/--lint/--ai-hints/--output/--cache/--watch | #1162 |
| SET-1-01 | Галка «не показывать онбординг» не синхронизируется с сервером — настройка молча переворачива… | #1163 |
| RUN-4-02 | Одна длинная строка вывода вешает CLI на часы: строки diff идут в rich без обрезки по длине | #1182 |
| VIS-2-01 | pytest-плагин молча собирает 0 items для решения без tests/ — зелёный CI при пропавших тестах | #1183 |
| MET-1-01 | При RE и TLE студент не видит вход кейса: ранний return до печати Input/Expected/Actual | #1184 |
| MET-1-07 | resolve_error_hint не фильтрует карточки по kind — под «объяснение ошибки» подставится карточ… | #1184 |
| ARCH-3-04 | Настройки лежат per-cwd, а база истории — глобальная: тумблер записи истории молча не применя… | #1186 |
| REV-5-01 | Реестр отклонённых находок обрезан посреди слова — отклонение невозможно перепроверить | #1187 |
| CANON-2-03 | Канон по безопасности не определён: CLAUDE.md вообще не упоминает SECURITY.md | #1193 |
| READER-4-01 | SECURITY.md заявляет «ничего не уходит в сеть» — в проекте четыре канала исходящего трафика | #1193 |
| READER-4-02 | SECURITY.md указывает историю прогонов «в рабочей папке», дефолт — ~/.stepik-grader/history.db | #1193 |
| ADD-4-03 | redact не ловит kwarg/repr-форму token='...' — токен уезжает в URL публичного issue | #1194 |
| OPS-1-04 | configure_diagnostics падает OSError на недоступном каталоге — включение лога роняет прогон | #1194 |
| OPS-1-05 | grader.log без ротации и потолка размера: под web+debug растёт неограниченно | #1194 |
| OPS-1-06 | В логе нет заголовка прогона (версия, ОС, Python, аргументы) | #1194 |
| SEC-3-06 | register_secret игнорирует секреты короче 8 символов — короткий ключ не маскируется | #1194 |
| MTX-8-01 | Округление до 9 знаков абсолютное: вывод «0.0» принимается вместо «0.0000000001» → AC неверно… | #1195 |
| MTX-8-02 | Большие конечные float схлопываются в один repr: 123456789012345678.0 и ...679.0 дают AC | #1195 |
| MTX-8-03 | Знак нуля асимметричен: 0.0 против +4e-10 — AC, против -4e-10 — WA; «-0.00» из f-строки тоже WA | #1195 |
| PY-1-02 | Переполнение float: два разных огромных числа схлопываются в 'inf' → AC неверному решению | #1195 |
| MTX-5-05 | Маркер в другом регистре съедается как данные: ожидание кейса 1 разрастается, кейс 2 исчезает | #1196 |
| PY-1-03 | Номер в маркере # TEST_N: игнорируется — блоки input/output спариваются позиционно | #1196 |
| PY-3-04 | load_json_file: UnicodeDecodeError и ValueError проходят мимо except (JSONDecodeError, OSError) | #1197 |
| ARCH-1-06 | TestResult.to_dict() мёртв и лоссов: теряет exit_code — обязательное поле CaseResult | #1198 |
| PAIR-2-04 | Докстринг core/result.py противоречит коду и result-contract.md по типу возврата | #1198 |
| QA-1-05 | Round-trip тест TestResult зелёный по построению: to_dict() теряет exit_code, а фикстура его… | #1198 |
| MTX-4-03 | function-режим: мутация «return str вместо int» проходит как AC | #1199 |
| RUN-1-06 | function-стиль (N.type): вызов на верхнем уровне даёт RE с трейсбеком внутрь wrapper'а | #1199 |
| ADD-4-01 | collect_commit() тянет subject коммита ЛЮБОГО репозитория в CWD — в публичный issue | #1200 |
| ADD-4-04 | FIELD_BUDGET_CHARS считается в символах, а лимит URL — в байтах: русский текст режется вдвое… | #1200 |
| OPS-1-07 | Осиротевшие stepik-sandbox-* каталоги никто не подметает, отказ уборки идёт мимо лога | #1201 |
| ARCH-3-01 | rules/ импортирует core/ — инвариант, на котором построен ADR-0011, нарушен и не проверяется… | #1202 |
| ARCH-3-05 | Два «единых» атомарных JSON-писателя с разной семантикой прав файла | #1203 |
| PY-3-05 | Temp-файлы атомарной записи утекают при любом не-OSError прерывании (Ctrl+C) | #1203 |
| PERF-1-03 | mtime_signature по max(mtime) не замечает удаления файла и добавления файла со старым mtime | #1204 |
| AUD-1-04 | Докстринг пакета core/ написан по-английски вопреки § Язык артефактов | #1205 |
| INS-3-01 | `python -m stepik_grader` из каталога задачи падает ImportError на файле-однофамильце stdlib | #1205 |
| PKG-1-06 | Пакет не экспортирует __version__ и __all__ | #1205 |
| REL-2-08 | pre-commit проверяет только ruff: четыре stdlib-guardrail'а и пины версий ловятся лишь в CI | #1209 |
| AUD-2-02 | Гард контраста не парсит палитру темы «авто» — именно её видит пользователь по умолчанию | #1239 |
| AUD-2-03 | Оба web-гарда обходят только верхний уровень static/ — переезд модуля в подкаталог их ослепляет | #1239 |
| AUD-2-04 | check_web_imports стережёт только ребро на core.js — девять остальных импорт-рёбер не проверя… | #1239 |
| RUN-4-01 | Верное решение получает WA с пустым Actual, если оставило дочерний процесс, держащий stdout | #1247 |
| ADD-5-01 | Три property-теста normalize_floats остаются зелёными, если функцию заменить на тождественную | #1252 |
| REL-3-05 | Джоб supply-chain не отличает «уязвимостей нет» от «аудит не отработал» | #1252 |
| QA-2-01 | Инвентарь пропусков слеп к модульному pytestmark — скип целого файла невидим для гейта | #1266 |
| QA-2-02 | Guard «набор не скипнулся целиком» считает СОБРАННЫЕ тесты, а не выполненные | #1266 |
| QA-2-03 | Guard запуска браузера сам скипается при сломанном окружении: фикстура срабатывает раньше про… | #1266 |
| QA-2-04 | Тест «Повторить» не доказывает повторный запрос — фикс, только прячущий баннер, остаётся зелёным | #1266 |
| QA-3-01 | Корпус пуст: ни одной зафиксированной задачи в репозитории — стенд не ловит ничего | #1274 |
| QA-3-02 | Префиксные мутации ломают решения с `from __future__ import` — 9 из 12 дают ложный «дефект ядра» | #1274 |
| QA-3-03 | Задача без загруженных кейсов даёт вакуумный AC на baseline вместо ошибки | #1274 |
| ARCH-2-01 | Режим 1 через async-job пишет в историю и в таблицу имя временного файла | #1300 |
| FE-2-01 | «Отправить в Stepik» не переоценивается после прогона режима 1 — необратимый сабмит по устаре… | #1305 |
| PY-1-06 | resolve_test_dir поднимается в parent.parent — решение можно проверить тестами чужой задачи | #1310 |
| RUN-2-05 | Рассогласование блоков формата 3: лишние кейсы отброшены, вердикт OK, в JSON следа нет | #1310 |
| RUN-2-08 | Решение в подпапке без своих тестов молча грейдится тестами родительской папки → чужой WA | #1310 |
| DES-1-01 | На экране ≤768px страница не прокручивается: контент ниже сгиба недостижим | #1315 |
| DES-1-02 | prefers-reduced-motion не гасит бесконечные анимации — скелетон и прогресс начинают мерцать | #1315 |
| LNG-5-01 | Полная очистка переносит хранилище: сразу после «История удалена» --insights печатает 12 чужи… | #1318 |
| SEC-2-06 | Скраб секретов из env — denylist по подстрокам: SSH_AUTH_SOCK проходит в решение | #1319 |
| LNG-3-01 | Штатный выход раннера не добивает дерево: каждый прогон копит осиротевший процесс решения | #1324 |
| LNG-3-02 | Внук, держащий stdout, копит в сервере по 2 потока и 2 pipe-дескриптора на кейс | #1324 |
| INS-2-03 | Импорт psutil на уровне модуля runner.py роняет весь грейдер сырым трейсбеком | #1328 |
| MTX-9-01 | Обрезка max_output_bytes режет строку посередине: в diff строка-призрак, которой решение не п… | #1330 |
| RUN-3-03 | Две OAuth-джобы занимают весь пул воркеров: все проверки висят в queued, cancel не помогает | #1334 |
| STR-5-03 | WA на невидимом различии: Expected/Actual и diff печатаются на экране одинаково | #1335 |
| JRN-1-01 | Колонка «Memory, MB» всегда 0.00: пик читается до join потока-замерщика на poll-пути | #1336 |
| JRN-2-01 | Memory, MB и peak_memory_mb всегда 0.00 в режимах 1/2/3: пик читается до join измеряющего потока | #1336 |
| MTX-6-04 | Измеритель памяти дефолтного раннера добавляет ~30% к измеряемому времени и всё равно отдаёт… | #1337 |
| MTX-4-01 | Формат 3 вытесняет кейсы формата 2 в той же tests/: мутант получает «OK 1/1» и rc=0 | #1339 |
| LNCH-1-01 | Лаунчер молча включает запись истории прогонов и не даёт её выключить | #1140 |
| DES-1-04 | check_contrast.py проверяет наличие токена в парах, а не реальную пару текст/фон | #1276 |
| ADD-1-01 | reference_adapter: обновление токена вне try — сетевая ошибка ломает контракт «никогда не бросает» | #1374 |
| AUD-1-01 | Контракт-тест фасада не замораживает приватные реэкспорты, которые сам объявляет замороженными | #1439 |
| QA-2-05 | Ассерты по обеим локалям опираются на неверную посылку — язык UI детерминированно русский | #1435 |
| QA-2-07 | Property-набор может исчезнуть молча: нет guard'а на hypothesis, в отличие от e2e и песочницы | #1435 |

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
| ADD-5-02 | Публичный API mode_detector не покрыт тестами | REFUTED | Проверено при разборе #921 (PR #1252): `is_function_only_solution` покрыт 23 обращениями в `tests/test_analyzer.py` через фасад. Верно лишь то, что покрытие лежит не в `test_mode_detector.py`. |

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

### Дефекты, найденные прогоном, документами и ревизией

Здесь `bug` из фазы 4 означает **вернувшийся** дефект: прошлый аудит его находил, находка числилась
закрытой, но код говорит иное.

| ID | Как чинить | file:line | Итог |
|---|---|---|---|
| JRN-4A-01 | Хранить в runs канонический идентификатор задачи (хеш абсолютного пути) и фильтровать --purge-history по нему; до этого — печатать список затрагиваемы | `src/stepik_grader/core/history.py:683` | high |
| SBX-1-01 | Собирать обёртку относительно файла, реально попадающего в песочницу: писать исходник решения в run_dir отдельным модулем и в spec_from_file_location  | `src/stepik_grader/core/wrapper_builder.py:248` | high |
| CANON-2-01 | Либо биндить только base_prefix+stdlib (venv-префикс исключить), либо привести к реальности SECURITY.md:284/:304-306, докстринг core/sandbox/__init__. | `src/stepik_grader/core/sandbox/_linux.py:72` | medium |
| CANON-3-01 | Парсить номер маркера и спаривать по нему; расхождение множеств номеров — явная ошибка загрузки, а не тихое zip(). В configuration.md § Формат тест-ке | `src/stepik_grader/core/test_loader.py:182` | medium |
| CNC-1-01 | cache.put (commands.py:423) не должен фиксировать вердикт, зависящий от загрузки машины: не кэшировать TLE вовсе либо инвалидировать запись, если elap | `src/stepik_grader/cli/commands.py:423` | medium |
| CNC-4-01 | Сделать 0→SCHEMA_VERSION одной транзакцией: открывать миграцию через BEGIN IMMEDIATE и коммитить только итоговую версию, убрав промежуточный commit()  | `src/stepik_grader/db.py:176` | medium |
| CNC-4-02 | Backfill выполнять под межпроцессной блокировкой (BEGIN IMMEDIATE до чтения user_version) и сделать идемпотентным: заполнять task_progress одним INSER | `src/stepik_grader/core/history.py:361` | medium |
| CNC-5-01 | В save_settings перечитывать файл перед записью и мержить только реально изменённые поля; ai_hint_consent считать монотонно отзываемым — воскресить ег | `src/stepik_grader/cli/interactive.py:579` | medium |
| FZZ-1-01 | Читать исходник в run_microbench_mode так же, как mode_detector.py:207/244 — read_bytes().decode(ENCODING, errors="replace"); лучше уважать PEP-263 че | `src/stepik_grader/core/grader_core.py:844` | medium |
| FZZ-1-02 | Обернуть загрузку кейсов в режиме 2 по каждой группе отдельно: UnicodeDecodeError → пометить задачу статусом ERROR с текстом «файл тестов не в UTF-8:  | `src/stepik_grader/core/test_loader.py:116` | medium |
| FZZ-3-02 | Искать тесты относительно НЕразвёрнутого пути (solution_path.parent), а resolve() применять только там, где нужен реальный файл для запуска; если test | `src/stepik_grader/core/test_loader.py:255` | medium |
| FZZ-5-05 | purge_history обернуть в except sqlite3.DatabaseError\|OSError и просто удалять файл БД (+ -wal/-shm) — цель команды и есть «истории не стало». В db.c | `src/stepik_grader/core/history.py:709` | medium |
| INS-1-01 | Обернуть tomllib.load в config.py:337 в try/except TOMLDecodeError: предупреждение через _console с путём и строкой ошибки и возврат GraderConfig() по | `src/stepik_grader/config.py:337` | medium |
| INS-3-01 | В __main__.py до `from stepik_grader.cli import main` вычистить cwd из sys.path. Тот же приём уже применён к дочернему процессу (wrapper_builder.py:12 | `src/stepik_grader/__main__.py:9` | medium |
| JRN-1-01 | Перенести stop_event.set() + mem_thread.join(0.5) ВНУТРЬ _run_with_polling перед сборкой RunOutcome, либо писать result[0]=peak на каждой итерации сем | `src/stepik_grader/core/runner.py:616` | medium |
| JRN-2-01 | Перенести stop_event.set() и mem_thread.join() внутрь _run_with_polling до сборки RunOutcome — как уже сделано для TLE-ветки communicate() по issue #7 | `src/stepik_grader/core/runner.py:759` | medium |
| JRN-4B-01 | Перед удалением показать реальный путь БД и объём (N прогонов / M задач) и требовать подтверждения (или --yes); в --help и локали заменить «.grader_hi | `src/stepik_grader/cli/__init__.py:512` | medium |
| MTX-10-01 | Отвязать замер от гранулярности отмены: ждать процесс блокирующим wait в отдельном потоке, а cancel_event опрашивать по его завершении, либо снизить т | `src/stepik_grader/core/runner.py:718` | medium |
| MTX-4-01 | Не «побеждать молча»: либо грузить оба набора с раздельной нумерацией, либо останавливать прогон явной ошибкой (как parser.error для --sandbox). Миним | `src/stepik_grader/core/test_loader.py:148` | medium |
| MTX-9-03 | Клипать КАЖДУЮ строку diff по _VERBOSE_MAX_VALUE_CHARS перед _cprint (как уже для Input/Expected/Actual), а в _cprint уводить строки длиннее ~4 КБ на  | `src/stepik_grader/core/reporter.py:549` | medium |
| READER-1-01 | Вернуть из main() код: 0 — все AC, 1 — есть WA/TLE/RE, 2 — тесты/файл не найдены; `sys.exit(main())` в точке входа. Задокументировать таблицу кодов в  | `src/stepik_grader/cli/__init__.py:448` | medium |
| SBX-1-02 | Копировать в run_dir весь каталог решения (или ro-bind его), а не один файл — как минимум .py-соседей. Пока не сделано — детектировать импорт локально | `src/stepik_grader/core/sandbox/_linux.py:165` | medium |
| SBX-3-01 | Сделать sandbox_max_cpu_seconds строго больше timeout_seconds (backstop, а не конкурент). Плюс чинить :313: bwrap маскирует сигнал в положительный код | `src/stepik_grader/core/sandbox/_posix_common.py:313` | medium |
| SBX-4-01 | После which делать смоук-прогон backend'а (bwrap --version + тривиальный exec) и при ненулевом коде поднимать SandboxUnavailableError → CLI отдаст par | `src/stepik_grader/core/sandbox/_linux.py:202` | medium |
| FZZ-4-01 | В _read_json_body ловить (json.JSONDecodeError, UnicodeDecodeError) либо декодировать явно в том же try и отдавать 400 body_invalid_json. Тест: POST с | `src/stepik_grader/web/http_guards.py:295` | low |
| FZZ-5-01 | В db.connect различать «файла нет» и «файл есть, size==0, user_version==0»: во втором случае до apply_schema предупредить тем же каналом, что и malfor | `src/stepik_grader/db.py:117` | low |
| MTX-9-01 | В _OutputBudget.take() при частичном взятии дорезать до последнего b'\n' (chunk[:room].rpartition(b'\n')), чтобы обрезанный хвост не превращался в псе | `src/stepik_grader/core/runner.py:414` | low |
| SBX-3-02 | В _map_outcome_to_result передавать накопленный вывод в ветку sandbox_violation (и в TLE/RE): _fail_result должен принимать output=..., а не хардкодит | `src/stepik_grader/core/grader_core.py:385` | low |

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

### Быстрые цели фаз 2-4

284 дополнительные быстрые цели из прогона, сверки документов и ревизии. Полный контекст — в § 5-бис.

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| JRN-4B-02 | Ключ задачи сделать уникальным: хеш абсолютного пути папки (или step_id из meta.json) как первичный ключ, имя папки — только отображаемый label; при к | `src/stepik_grader/core/history.py:683` | high |
| MTX-1-02 | При обходе tests/ собирать непарные N и N.clue и печатать предупреждение с их именами. В JSON рядом с total добавить число отброшенных файлов, чтобы C | `src/stepik_grader/core/test_loader.py:219` | high |
| MTX-3-01 | Разделить проходы: timeit.repeat без tracemalloc для таймингов и отдельный короткий прогон под tracemalloc только ради MEM:. Сейчас строки 193-205 _bu | `src/stepik_grader/core/microbench_runner.py:197` | high |
| MTX-5-03 | В ветке пропущенного N.clue выдавать то же предупреждение, что формат 3 (test_loader.py:173-180), и добавить в результат число отброшенных кейсов, что | `src/stepik_grader/core/test_loader.py:219` | high |
| REV-1-01 | Требовать, чтобы Call/Assign были на уровне statement И ссылались на имя из решения (func_name/публичные имена модуля), либо сверять с _detect_run_mod | `src/stepik_grader/core/test_loader.py:187` | high |
| CANON-3-03 | Предупреждать о файлах без пары (симметрично формату 3) и показывать число загруженных кейсов; описать поведение неполного набора в configuration.md. | `src/stepik_grader/core/test_loader.py:205` | medium |
| CNC-1-03 | Пробросить флаг cache-hit из _grade_with_cache в запись статистики: не писать строку на попадании либо помечать её `cached: true`, чтобы --insights не | `src/stepik_grader/cli/commands.py:491` | medium |
| FZZ-1-04 | Свести чтения локальных текстовых данных к одному хелперу read_text_lenient(path) = read_bytes().decode(ENCODING, errors="replace") на кодеке utf-8-si | `src/stepik_grader/core/test_loader.py:116` | medium |
| INS-4-01 | Обернуть tomllib.load (config.py:337) в try/except (TOMLDecodeError, OSError): предупредить тем же UserWarning-каналом, что и неизвестные ключи (confi | `src/stepik_grader/config.py:337` | medium |
| INS-4-03 | В _db_problem_kind (history.py:151) добавить вид 'unwritable' для OSError и сообщений 'unable to open database file'/'readonly database': сдаваться ср | `src/stepik_grader/core/history.py:504` | medium |
| JRN-1-02 | Читать тест-файлы с encoding='utf-8-sig' в load_text_lines и parsers; при расхождении только по невидимым символам печатать repr() в diff. | `src/stepik_grader/core/test_loader.py:116` | medium |
| JRN-2-05 | Считать ключ от инварианта задачи (нормализованный абсолютный путь папки или его хеш + читаемое имя), а не от каталога запуска; при миграции склеить с | `src/stepik_grader/core/history.py:647` | medium |
| LNG-1-01 | Считать total_runs как SUM(task_progress.runs_total) — агрегат уже ведётся и retention не подвержен; len(runs) оставить для подписи «из них сохранено  | `src/stepik_grader/core/progress_export.py:97` | medium |
| LNG-5-01 | После purge_history (cli/__init__.py:512) печатать удалённый путь и путь будущих записей, либо не терять привязку (оставлять пустую БД). Иначе повторн | `src/stepik_grader/core/history.py:709` | medium |
| LNG-5-04 | Резолвить журнал тем же путём, что и БД (рядом с default_history_db_path()), а до миграции — печатать фактический путь и не выдавать «0» за подтвержде | `src/stepik_grader/core/stats.py:57` | medium |
| MTX-10-04 | Ранние выходы «файл не найден» / «тесты не найдены» пропускать через тот же форматтер, что и результат: при --output json печатать {"kind":"error","me | `src/stepik_grader/cli/commands.py:465` | medium |
| MTX-2-01 | Печатать в шапке режима 4 и отдавать в JSON явное «замерены кейсы 1-5 из 7 (microbench_max_cases)»; выбирать подмножество не первыми N, а самыми тяжёл | `src/stepik_grader/core/grader_core.py:838` | medium |
| MTX-3-04 | Заменить warnings.warn (строки 163 и 174) на вывод через _console с ключами локалей ru/en и продублировать факт в структуре результата (поле notes), ч | `src/stepik_grader/core/test_loader.py:163` | medium |
| MTX-4-04 | Добавить в JSON/CSV явный статус прогона (ok\|fail\|no_tests), как в text-таблице, и не выдавать нулевой отчёт за успех: при total==0 — статус no_test | `src/stepik_grader/cli/commands.py:624` | medium |
| MTX-5-01 | При WA, где расхождение ровно в краевых пустых строках, печатать и класть в JSON признак «ожидание усечено границей блока, задайте задачу форматом 1/2 | `src/stepik_grader/core/parsers.py:35` | medium |
| MTX-5-02 | Ограничить override кейсами, у которых режим определён именно для них, а частичный .type по набору трактовать как ошибку конфигурации тестов, а не выд | `src/stepik_grader/core/test_loader.py:288` | medium |
| MTX-5-04 | Печатать через _console и локаль ru как предупреждение отчёта, а не warnings.warn из недр grader_core, и продублировать отдельным полем результата, чт | `src/stepik_grader/core/test_loader.py:174` | medium |
| MTX-5-05 | Матчить маркер без учёта регистра (re.IGNORECASE в parsers.py:62) либо, если регистр принципиален, распознавать похожую на маркер строку внутри блока  | `src/stepik_grader/core/parsers.py:64` | medium |
| MTX-6-01 | Читать peak_mb_result[0] ПОСЛЕ stop_event.set() + mem_thread.join() на ВСЕХ путях выхода LocalRunner.run, а не только в TLE-ветке (там уже сделано по  | `src/stepik_grader/core/runner.py:759` | medium |
| MTX-7-01 | На строке 285 не добавлять завершающий '\n' для пустого набора: `stdin_data = "\n".join(case.input_lines) + "\n" if case.input_lines else ""`. Закрыть | `src/stepik_grader/core/grader_core.py:285` | medium |
| MTX-9-02 | Прокинуть флаг обрезки из RunOutcome в case-result отдельным полем и печатать при WA строкой 'вывод обрезан на лимите N байт' вместо голого WA; то же  | `src/stepik_grader/core/grader_core.py:419` | medium |
| PAIR-1-02 | Либо перенести проверку пространства имён в config.py/_RULES (сработает штатный UserWarning при загрузке), либо в configuration.md:117 написать, что о | `src/stepik_grader/config.py:234` | medium |
| PAIR-4-02 | Заменить «POSIX (Linux/macOS)» на «Linux-only» в configuration.md:378-381 и в строке таблицы max_memory_mb (99), в SECURITY.md:42-43, README.md:174; в | `docs/use/configuration.md:378` | medium |
| READER-2-05 | Развести на два блока (Windows / macOS-Linux) либо оставить активной POSIX-строку, а Windows вынести отдельно с явной пометкой — как уже сделано в шаг | `CONTRIBUTING.md:198` | medium |
| REV-1-02 | Один хелпер чтения тестовых файлов (read_bytes().decode(ENCODING, errors='replace')) и перевести на него load_text_lines, чтение input.txt/output.txt  | `src/stepik_grader/core/test_loader.py:116` | medium |
| REV-1-03 | Обернуть миграцию в BEGIN IMMEDIATE и перечитать user_version под write-lock; либо делать backfill только при пустой task_progress (SELECT COUNT(*)=0) | `src/stepik_grader/core/history.py:354` | medium |
| REV-1-04 | Инвертировать критерий: transient — только 'database is locked'/'database is busy', всё остальное считать неустранимым и печатать однократное предупре | `src/stepik_grader/core/history.py:153` | medium |
| REV-3-01 | Перенести actions/checkout первым шагом job'а (до download-artifact) и выставить fail_on_unmatched_files: true у softprops/action-gh-release, чтобы пу | `.github/workflows/release.yml:110` | medium |
| REV-3-02 | В _handle_create_hint считать endpoint через consent_endpoint(config.ai_base_url), сверять с settings.ai_hint_consent_endpoint и при расхождении отвеч | `src/stepik_grader/web/api_routes.py:845` | medium |
| REV-3-06 | Слать с allow_redirects=False; при 3xx либо отказываться (лог + None, канал graceful), либо ревалидировать Location через base_url_is_allowed с лимито | `src/stepik_grader/core/ai_hints.py:280` | medium |
| REV-4-04 | Завести state.glossary.missingFailed по образцу state.rules.failed и в renderGlossaryMissing() вызывать clearLoadError/renderLoadError(el, loadGlossar | `src/stepik_grader/web/static/content.js:303` | medium |
| REV-6-04 | Проставить состояние каждой строке (PR · отклонена с причиной · вынесена в issue N), а оставшиеся открытыми вернуть в docs/audit/ живым файлом. | `docs/archive/audit-2026-07-30-full-roles.md:24` | medium |
| SBX-1-03 | Задать LocalRunner детерминированный cwd — каталог файла решения (`cwd=spec.path.parent` в subprocess.Popen), как уже делает песочница через --chdir.  | `src/stepik_grader/core/runner.py:546` | medium |
| SBX-2-03 | В _run_with_polling ставить stop_event и join'ить mem_thread ДО сборки RunOutcome (либо писать result[0] на каждой итерации сэмплера) + тест, гоняющий | `src/stepik_grader/core/runner.py:616` | medium |
| SBX-4-03 | Поднять блок `if args.sandbox: set_runner(SandboxRunner())` выше ветки `args.mode is None`, чтобы меню и запуск web из пункта 6 (interactive.py:554) н | `src/stepik_grader/cli/__init__.py:621` | medium |
| SBX-5-01 | Сохранять рядом с solution_sha/tests_sha отпечаток условий (sandbox-backend или его отсутствие, timeout_seconds, max_memory_mb) и считать промахом люб | `src/stepik_grader/core/cache.py:112` | medium |
| SBX-5-02 | До появления отпечатка условий в ключе кэша — при одновременных --sandbox и --watch либо не включать кэш автоматически, либо инвалидировать его целико | `src/stepik_grader/cli/__init__.py:669` | medium |
| CANON-1-01 | Добавить `--match v[0-9]*` в describe у version.py и разбирать тег регуляркой `^v(\d+)\.(\d+)\.(\d+)$` с явным уходом в fallback при несовпадении; `ls | `scripts/version.py:105` | low |
| CANON-1-02 | В generate_version_badge.py (или в project_version) считать версию с MAJOR==0 признаком отказа: печатать ошибку и выходить ≠0, не переписывая существу | `scripts/version.py:112` | low |
| CANON-1-03 | Разделить сообщения: при baseline is None печатать «OK (partial): only pyproject checked, baseline skipped». Плюс флаг --require-baseline, включённый  | `scripts/check_version_consistency.py:206` | low |
| CANON-1-04 | Перенаправить versions.md:6, versions.md:66 и докстринги обоих скриптов напрямую на docs/dev/versioning.md, оставив CONTRIBUTING только как онбординг- | `docs/use/versions.md:6` | low |
| CANON-1-05 | Дописать в § CI-защита от дрейфа третий пункт про docs/use/versions.md и явно указать, что CLAUDE.md/versions.md дают WARNING (не роняют сборку), а па | `docs/dev/versioning.md:116` | low |
| CANON-1-06 | Свести CLAUDE.md § Версионирование к одной фразе + ссылка на docs/dev/versioning.md: убрать пересказ формулы либо дописать `--first-parent` рядом с `- | `CLAUDE.md:329` | low |
| CANON-2-02 | В трёх backend'ах различать «не задано» и явный None: при None в обоих источниках не ставить RLIMIT_AS/JobMemoryLimit и не включать psutil-порог; либо | `src/stepik_grader/core/sandbox/_linux.py:111` | low |
| CANON-2-03 | Добавить в таблицу CLAUDE.md строку «Песочница, гарантии по ОС, политика уязвимостей → SECURITY.md», а в строке 376 сузить configuration.md до «конфиг | `CLAUDE.md:376` | low |
| CANON-2-04 | В обоих местах явно ограничить область: «по умолчанию (без --sandbox)» + ссылка на SECURITY.md § --sandbox; в SECURITY.md:42 уточнить, что вне скоупа  | `docs/use/configuration.md:375` | low |
| CANON-2-05 | В :99 разделить два пути: без --sandbox — POSIX-only (RLIMIT_AS); под --sandbox — общий лимит всех трёх backend'ов, на Windows через JOB_OBJECT_LIMIT_ | `docs/use/configuration.md:99` | low |
| CANON-3-02 | Привести таблицу форматов и абзац про downloader к коду: ZIP и GitHub → формат 3 (input.txt/output.txt, .type не создаётся), HTML-таблица → формат 1.  | `docs/use/configuration.md:283` | low |
| CANON-3-04 | Прекращать разбор после первого формата, давшего кейсы (3 → 2 → 1), либо предупреждать о коллизии номеров; в configuration.md сформулировать приоритет | `src/stepik_grader/core/test_loader.py:198` | low |
| CANON-3-05 | Либо ограничить override кейсами без собственного .type, либо явно описать в configuration.md, что режим запуска определяется на уровне папки/файла ре | `src/stepik_grader/core/mode_detector.py:234` | low |
| CANON-3-07 | Перевести оба текста на русский и дублировать вывод через _console (warnings.warn оставить для тестов), чтобы сообщение доходило и в CLI, и в web. | `src/stepik_grader/core/test_loader.py:163` | low |
| CANON-3-08 | Синхронизировать docstring с кодом, описать порядок поиска в configuration.md и печатать в отчёте фактический путь каталога тестов. | `src/stepik_grader/core/test_loader.py:250` | low |
| CANON-4-02 | Отбраковывать в stdlib_inventory имена, удалённые/deprecated в поддерживаемых версиях, либо помечать такие записи отдельным reason, чтобы они не читал | `src/stepik_grader/glossary/coverage.py:165` | low |
| CANON-4-03 | Убрать число: «без --cards база не подхватится и отчёт покажет 0 покрытых из полного инвентаря»; отметить зависимость знаменателя от версии интерпрета | `CLAUDE.md:506` | low |
| CANON-4-04 | Развести в обоих документах: карточки считает только generate_glossary_badge.py (list_by_status('ready')), coverage — покрытие stdlib. Убрать «/ pytho | `docs/dev/glossary.md:178` | low |
| CANON-4-05 | Расширить ratchet: запрет подстроки «Glossary-Python» в данных/статике/локалях и жёсткая проверка, что непустой docs_url начинается с https://docs.pyt | `tests/test_glossary.py:200` | low |
| CANON-4-06 | Заменить число ссылкой на бейдж Glossary — как уже сделано в README.md:81 («объём — в бейдже Glossary выше»). | `docs/use/web-interface.md:247` | low |
| CANON-4-07 | Убрать число из докстринга («компактный курированный набор частых встроенных исключений») либо привести к 28 во всех трёх местах. | `src/stepik_grader/core/glossary.py:11` | low |
| CANON-5-01 | В § Архитектура web UI добавить инвариант ADR-0010 (вход в грейдинг — только web/grading, импорты из core — только публичная поверхность), внести grad | `docs/dev/web-contracts.md:258` | low |
| CANON-5-02 | В таблице-трекере поставить «❌ удалена (команды остались кнопками/action cards)», строку про внешние плагины и fuzzy-поиск снять либо перенацелить на  | `docs/dev/design/web-design.md:149` | low |
| CANON-5-03 | В web-contracts.md § Безопасность привести CSP дословно с пометкой, что style-src 'unsafe-inline' оставлен ради вендоренного CodeMirror, добавить X-Fr | `docs/use/web-interface.md:415` | low |
| CANON-5-04 | В шапке api.md указать src/stepik_grader/web/api_routes.py (server.py — транспорт и статика); в web-design.md критерий нереализованности сформулироват | `docs/dev/api.md:5` | low |
| CANON-5-05 | В api.md § GET / перечислить все четыре подстановки с итоговыми data-атрибутами и источниками значений (run_server(sandbox=…, record_history=…), .grad | `docs/dev/api.md:110` | low |
| CANON-5-06 | Переписать абзац: «ставит SandboxRunner активным Runner'ом фасада (web.grading.set_runner — публичная поверхность core, ADR-0010)»; заодно grep'ом про | `src/stepik_grader/web/server.py:229` | low |
| CNC-1-02 | В save() перечитать файл под межпроцессным локом и слить entries (свои поверх чужих) перед os.replace. Потеря записи вердикт не портит, но молча обнул | `src/stepik_grader/core/cache.py:172` | low |
| CNC-1-04 | Не клампить elapsed при TLE (отдавать измеренное) и ставить вердикт по одному измерению: elapsed > timeout → TLE, независимо от того, успел ли communi | `src/stepik_grader/core/runner.py:743` | low |
| CNC-2-01 | В cancel_job при status == "queued" сразу переводить job в терминальный "cancelled" под job.lock и звать job.future.cancel(); _run_job на входе провер | `src/stepik_grader/web/runs.py:325` | low |
| CNC-2-02 | В ветке cancel_event публиковать частичный result (кейсы, успевшие получить вердикт) с явным признаком неполноты, чтобы UI рисовал «отменено, 2 из 8 п | `src/stepik_grader/web/runs.py:486` | low |
| CNC-2-05 | В _handle_cancel_run перед runs.get_job() применить ту же проверку, что в _get_run_status: пустой run_id или наличие "/" → plain 404 без подстановки в | `src/stepik_grader/web/api_routes.py:963` | low |
| CNC-3-02 | Обернуть конструирование _GraderServer в try/except OSError в run_server и отдать локализованное сообщение с номером порта и подсказкой --port, заверш | `src/stepik_grader/cli/__init__.py:606` | low |
| CNC-4-03 | Завести путь открытия «только чтение» без migrate (нет схемы — отдать пусто) либо накрыть _connect общим _WRITE_LOCK во всех четырёх местах, а межпроц | `src/stepik_grader/core/history.py:585` | low |
| CNC-5-03 | После unlink() убеждаться, что файл не появился снова (или брать .grader_cache/.lock на время clear и save); счётчик в cache_cleared считать по реальн | `src/stepik_grader/core/cache.py:177` | low |
| CNC-5-04 | Меню не должно нести снапшот всей сессии: перед save_settings перечитывать файл и переносить в него только record_history. Общий помощник update_setti | `src/stepik_grader/cli/interactive.py:579` | low |
| FZZ-1-03 | В resolve_error_hint перед выдачей карточки прогонять текст ошибки по узким сигнатурам (`Non-UTF-8 code`, `no encoding declared`) и подставлять адресн | `src/stepik_grader/core/error_glossary.py:91` | low |
| FZZ-2-01 | В print_case_verbose экранировать C0/C1 у expected/actual/diff перед _cprint (ESC, BEL, NUL, VT — в видимые \x1b/\x07/\x00/\x0b). Веб-слой такую санац | `src/stepik_grader/core/reporter.py:611` | low |
| FZZ-2-03 | При U+FFFD в декодированном stdout добавлять рядом с Actual строку «вывод решения не в UTF-8»; упомянуть случай в configuration.md § Как сравнивается  | `src/stepik_grader/core/grader_core.py:418` | low |
| FZZ-2-04 | Санировать управляющие символы ДО обрезки (см. находку про сырой ANSI) — тогда резать нечего; либо резать по границам escape-последовательностей и доп | `src/stepik_grader/core/reporter.py:561` | low |
| FZZ-2-05 | Печатать многострочные expected/actual списком с номерами строк (или ↵-маркером), при одной строке — без разделителя. Как минимум экранировать литерал | `src/stepik_grader/core/reporter.py:607` | low |
| FZZ-3-03 | Либо followlinks=True с защитой от петель по (st_dev, st_ino), либо явная строка отчёта «пропущен симлинк-каталог <path>»: молчаливое исчезновение реш | `src/stepik_grader/core/test_loader.py:75` | low |
| FZZ-3-04 | Дать колонке File ограничение ширины (max_width ≈ 48) с overflow="ellipsis" вместо голого no_wrap=True, либо задать min_width остальным колонкам — тог | `src/stepik_grader/core/reporter.py:258` | low |
| FZZ-4-02 | Читать тело порциями с дедлайном меньше 30 с и на недобор байт отвечать 400 body_truncated вместо молчаливого падения по таймауту; осушение перед 413  | `src/stepik_grader/web/http_guards.py:283` | low |
| FZZ-4-03 | Если ни root_dir, ни secrets_path не переданы — не звать write_config, а вернуть текущий конфиг без записи (или 400 specify_config_field). Запись на д | `src/stepik_grader/web/api_routes.py:457` | low |
| FZZ-4-04 | Отвечать 400 body_field_type при неверном типе onboarding_seen и включать в ответ список применённых полей ({"ok":true,"applied":[...]}), чтобы клиент | `src/stepik_grader/web/api_routes.py:755` | low |
| FZZ-4-05 | Проверять isinstance(body.get('path'), str) и существование confined_dir до вызова import_reference; отказ отдавать 4xx, а не 200 с ok:false; из messa | `src/stepik_grader/web/api_routes.py:559` | low |
| FZZ-5-02 | В ветке args.export_progress проверять результат build_progress_report: при недоступной истории печатать сообщение про экспорт и выходить с ненулевым  | `src/stepik_grader/cli/__init__.py:531` | low |
| FZZ-5-03 | Валидировать запись целиком до накопления: _parse_entry(entry) -> Entry\|None, агрегаты обновлять только для непустого результата. Заодно закрыть isin | `src/stepik_grader/core/stats.py:188` | low |
| FZZ-5-04 | Добавить третью ступень: args.history → load_settings().record_history → CONFIG.record_history. Ту же лестницу применить в _resolve_record_stats и в в | `src/stepik_grader/cli/options.py:353` | low |
| FZZ-5-06 | read_summary возвращает счётчик skipped рядом с total_runs; reporter при total_runs==0 и skipped>0 печатает отдельный ключ локали с числом непрочитанн | `src/stepik_grader/core/locales/ru.json:124` | low |
| INS-1-04 | Печатать блок стиля всегда, когда задан --lint: при нуле замечаний — одна строка 'Стиль: замечаний нет (ruff X.Y.Z)'. Ключ добавить рядом с lint_block | `src/stepik_grader/core/reporter.py:524` | low |
| INS-2-01 | В fallback-ветках print_insights_summary (reporter.py:460) и print_progress_summary (reporter.py:510) печатать lbl["title"] и строку подписей столбцов | `src/stepik_grader/core/reporter.py:462` | low |
| INS-2-02 | В except ImportError (cli/__init__.py:419-422) сначала выполнить rerun() один раз, а сообщение о недоступном extra печатать в stderr; либо завершать п | `src/stepik_grader/cli/__init__.py:419` | low |
| INS-3-03 | В `if __name__ == "__main__"` (downloader.py:465-466) — `raise SystemExit(0 if main() else 1)`: пустой список скачанного даёт ненулевой код. Вызов из  | `src/stepik_grader/downloader.py:465` | low |
| INS-3-04 | Либо main-guard с подсказкой «используйте stepik-grader --init-vscode» и ненулевым кодом, либо уточнить в CLAUDE.md/docs точный список запускаемых -m  | `src/stepik_grader/ide.py:1` | low |
| INS-4-04 | Обернуть обе записи (cli/__init__.py:546, ide.py:73) в try/except OSError с локализованным «не удалось записать <путь>: каталог недоступен для записи» | `src/stepik_grader/cli/__init__.py:546` | low |
| INS-5-01 | Дать downloader.main() argparse с description/epilog и обрабатывать --help/--version до любого чтения и записи конфига; неизвестный флаг — parser.erro | `src/stepik_grader/downloader.py:424` | low |
| INS-5-02 | Заменить относительные пути на абсолютные URL проекта в ru/en-локалях и help-строках options.py; добавить тест, запрещающий «docs/», «README», «.md» б | `src/stepik_grader/core/locales/ru.json:148` | low |
| INS-5-04 | Обернуть чтение в try/except EOFError и на EOF возвращать дефолт (change="n", текущий конфиг) — как делает главное меню; либо перевести на общий _conf | `src/stepik_grader/downloader_config.py:185` | low |
| INS-5-05 | Добавить строку про stepik-grader-gui в _EPILOG (options.py:35) и в launcher.main() ранний разбор sys.argv: --help печатает назначение и RC=0, --versi | `src/stepik_grader/launcher.py:793` | low |
| INS-5-06 | Дописать в _EPILOG три строки первого шага: «Нет задачи? python -m stepik_grader.downloader — скачать по URL шага», «Веб-интерфейс: stepik-grader --se | `src/stepik_grader/cli/options.py:35` | low |
| JRN-1-03 | Либо снять шаблон имени в find_all_solution_files (все *.py кроме tests/), либо в mode 1 предупреждать о неподходящем имени. Отдельно — передать path= | `src/stepik_grader/cli/commands.py:562` | low |
| JRN-1-04 | Добавить в _EPILOG скелет задачи (task.py + tests/input_1.txt + tests/expected_1.txt) и переписать ru.json:115 на именованный формат, легаси — второй  | `src/stepik_grader/cli/options.py:35` | low |
| JRN-1-06 | Завести ключи локали для заголовков в ru.json/en.json и подставлять их в обеих ветках печати таблицы. | `src/stepik_grader/core/reporter.py:257` | low |
| JRN-2-02 | Связать бюджет с --number или подбирать number пилотным прогоном (timeit.autorange); выпавшее решение показывать строкой таблицы с вердиктом TIMEOUT,  | `src/stepik_grader/core/microbench_runner.py:268` | low |
| JRN-2-06 | В режимах 2/3/4 брать все *.py за вычетом служебных (test_*, conftest, __init__), а task*.py оставить лишь приоритетом сортировки; на «ничего не нашли | `src/stepik_grader/cli/commands.py:562` | low |
| JRN-3A-01 | В downloader.main (стр. 281-287) различать «конфиг не читается» и прочие сбои: на JSONDecodeError/UnicodeDecodeError/ValueError печатать локализованны | `src/stepik_grader/downloader.py:426` | low |
| JRN-3A-02 | Разделить нормализацию и запись: абсолютные пути — только в возвращаемом значении, на диске остаётся то, что ввёл пользователь. Либо звать save_json_f | `src/stepik_grader/downloader_config.py:229` | low |
| JRN-3A-03 | Сделать main() -> int, возвращать 1 из except на стр. 293-297, а __main__ (313-314) обернуть в raise SystemExit(main()). Тот же приём — для downloader | `src/stepik_grader/diagnostic_stepik.py:296` | low |
| JRN-3A-04 | Обернуть HTTPServer на стр. 446 в try/except OSError и бросать отдельный тип с текстом «локальный порт {port} занят»; в downloader.py:440-445 давать с | `src/stepik_grader/core/stepik_client.py:446` | low |
| JRN-3A-05 | Завести ключи diag_prompt_url / diag_prompt_secrets / diag_prompt_outdir в ru.json и en.json, добавить локальный _t (как в downloader_config.py:52-57) | `src/stepik_grader/diagnostic_stepik.py:274` | low |
| JRN-3B-01 | Возвращать auth_url в progress/result auth-job и показывать его в UI кликабельным (веб-режим сам себе браузер). Таймаут ожидания кода отдавать не как  | `src/stepik_grader/core/stepik_client.py:490` | low |
| JRN-3B-02 | Писать client_id/client_secret только после успешного обмена кода на токен (или откатывать запись при ошибке flow), чтобы reason=no_token означал «кре | `src/stepik_grader/web/auth_adapter.py:114` | low |
| JRN-3B-03 | Разбирать URL (домен stepik.org + /lesson/N/step/M) до проверки авторизации и отдавать отдельный message_id о неверной ссылке — иначе человек уходит н | `src/stepik_grader/web/downloader_adapter.py:203` | low |
| JRN-3B-04 | Добавить в строку результата message_id (напр. no_tests_for_solution) с локалью и текстом «положите tests/input_1.txt и tests/expected_1.txt рядом с р | `src/stepik_grader/web/viewmodels.py:739` | low |
| JRN-4A-03 | Ограничивать подъём не «внутренностью home», а найденным корнем рабочего пространства (.git/pyproject.toml/StepikTasks) с лимитом глубины — тогда прав | `src/stepik_grader/core/history_recording.py:133` | low |
| JRN-4A-05 | Свести статистику к тому же резолву пути, что и история (default_history_db_path), и в обеих сводках печатать строку-источник с фактическим путём — то | `src/stepik_grader/core/stats.py:58` | low |
| JRN-4B-03 | Единая функция разрешения пути настроек (workspace-параметр, по умолчанию cwd) и передача выбранного workspace в CLI-ветки commands.py:220/256, либо х | `src/stepik_grader/core/user_settings.py:82` | low |
| JRN-4B-06 | Запускать ruff с cwd=workspace и явным --cache-dir внутри .grader_cache (или --no-cache), чтобы служебные каталоги не появлялись в папке запуска; вклю | `src/stepik_grader/core/lint.py:166` | low |
| JRN-5-01 | При task_key и runs_removed == 0 печатать отдельное сообщение локали («задача {task} в истории не найдена; известные ключи: …» из task_progress) и воз | `src/stepik_grader/cli/__init__.py:517` | low |
| JRN-5-02 | В ветке полной очистки удалять рядом лежащие grader-progress.md/.html (либо называть их в сообщении как неудалённые), а --export-progress на пустой ис | `src/stepik_grader/cli/__init__.py:534` | low |
| JRN-5-03 | Разделить секции «Прогонов / из них полностью зелёных» и «Кейсов по вердиктам», подписав единицу в самой метке; число зелёных прогонов считать в stats | `src/stepik_grader/core/reporter.py:355` | low |
| LINK-1-01 | Заменить на `docs/archive/audit-2026-07.md` во всех 4 точках (history.py:5, history.py:227, insights.py:4, rules/models.py:7), в форме `](../../../doc | `src/stepik_grader/core/history.py:5` | low |
| LINK-1-02 | Заменить на `docs/dev/trace-format.md` в трёх точках; в trace-player.js:207 уточнить подпись до «§ Ссылки (`<ref>`) и heap». | `src/stepik_grader/web/static/trace-player.js:207` | low |
| LINK-1-03 | Поправить на `docs/archive/changelog-archive.md` в тексте ошибки, комментарии строки 105 и docstring; добавить самопроверку — пути из текстов ошибок д | `scripts/check_docs_guardrails.py:417` | low |
| LINK-1-05 | В пользовательских строках давать абсолютный URL — база уже объявлена в `pyproject.toml:89` полем Documentation; репо-относительные пути в сообщениях об ошибках не работают при установке через pip и pipx | `src/stepik_grader/core/locales/ru.json:148` | low |
| LINK-1-06 | Заменить на `docs/dev/result-contract.md § Вердикты (семантика)` в grade.js:17. | `src/stepik_grader/web/static/grade.js:17` | low |
| LINK-2-01 | В ADR-0001/0008/0009/0010 заменить «открытый v2.0-backlog (эпик #151)» на «#151 закрыт как дизайн; билд держит roadmap #59» со ссылкой на #59 — как уж | `docs/dev/adr/0001-server-mode.md:6` | low |
| LINK-2-02 | Добавить в шапку документа и в docs/audit/README.md строку «Эпик разбора: #915 (8 подэпиков)», а в заголовки риск-групп § 2 — номер соответствующего п | `docs/audit/2026-08-10-full-roles.md:1` | low |
| LINK-2-03 | Ввести колонку «Состояние» (открыта / PR #NNN / отклонена + причина) и заполнять её при мерже; ✅/◐ оставить только вердиктом верификатора, пояснив раз | `docs/audit/2026-08-10-full-roles.md:20` | low |
| LINK-2-04 | Исправить CLAUDE.md:344 на `(#issue)` — практикой закреплён именно issue (он объясняет «зачем», PR доступен из него). Либо разрешить оба вида, но потр | `CLAUDE.md:344` | low |
| LINK-2-05 | Заменить строку на указатель на папки: «Аудиты в работе → docs/audit/README.md; отработанные → docs/archive/README.md» без хардкода даты — иначе строк | `CLAUDE.md:392` | low |
| LINK-2-06 | Заменить отсылку на прямые ссылки: docs/use/grader-workflow.md (флаг --ai-hints) и docs/use/configuration.md (параметры ai_*), оставив #435/#438 тольк | `docs/dev/adr/0003-ai-integration.md:124` | low |
| LINK-2-07 | Переписать строку статуса: «Accepted, реализовано: PR #591 (atomic_write_json) и PR #592 (core/db.py + миграция missing-queue)»; номера issue #551/#55 | `docs/dev/adr/0011-local-persistence.md:3` | low |
| LINK-3-01 | Добавить шаг `python scripts/check_docs_guardrails.py` в job verify (release.yml, рядом с check_version_consistency) либо переписать versioning.md:113 | `docs/dev/versioning.md:113` | low |
| LINK-3-02 | Сделать pypi-publish: needs [build, verify, github-release] либо перенести извлечение заметок в job verify (до сборки), либо честно переписать version | `.github/workflows/release.yml:141` | low |
| LINK-3-03 | Либо дописать area/* в labels шаблонов, где область предсказуема, либо завести labeler-workflow (по dropdown `area` в bug_report.yml:57), либо явно за | `.github/ISSUE_TEMPLATE/bug_report.yml:53` | low |
| LINK-3-04 | Дописать id-token: write в supply-chain.md:141 и в строку таблицы 124 с пояснением, зачем он claude-code-action и почему не даёт публикацию на PyPI (t | `docs/dev/supply-chain.md:141` | low |
| LINK-3-05 | В строке 120 указать `ci.yml`, `release.yml` и добавить в колонку «Зачем» передачу dist из build в github-release/pypi-publish. | `docs/dev/supply-chain.md:120` | low |
| LINK-3-06 | Свести словарь: добавить area/ci (и downloader, если он реально есть) в CONTRIBUTING.md:51-58 и таблицу CLAUDE.md либо заменить их на существующие мет | `.github/dependabot.yml:27` | low |
| LINK-3-07 | Развернуть узел .github/ в project-structure.md:138 до подкаталогов (workflows/{ci,release,claude,claude-code-review}.yml, dependabot.yml, ISSUE_TEMPL | `docs/dev/project-structure.md:138` | low |
| LNG-1-02 | При ротации сворачивать отбрасываемую половину в служебную строку-агрегат (runs/time/verdicts + since_ts) и учитывать её в read_summary; в сводке печа | `src/stepik_grader/core/stats.py:61` | low |
| LNG-1-03 | Вести серию инкрементально при записи прогона (поле в task_progress или строка метаданных), а не пересчитывать по runs; при вытеснении показывать «≥ N | `src/stepik_grader/core/progress_export.py:94` | low |
| LNG-2-01 | Сохранять при ротации свёрнутые счётчики вытесненной половины (служебная первая строка или соседний total-файл) и складывать их в read_summary. Миниму | `src/stepik_grader/core/stats.py:76` | low |
| LNG-2-02 | Считать total_runs отдельным COUNT(*) FROM runs (или SUM(runs_total) по task_progress), а тали вердиктов — GROUP BY по case_results, вместо материализ | `src/stepik_grader/core/progress_export.py:97` | low |
| LNG-2-04 | Печатать «Подучить» первым (это цель команды), а «Прогресс» ужать до нерешённых + N последних со строкой «ещё K решено» и отдельным флагом на полный с | `src/stepik_grader/cli/__init__.py:566` | low |
| LNG-3-01 | На штатном выходе тоже подчищать потомков: после proc.wait() пройти psutil.Process(pid).children(recursive=True) и терминировать оставшихся — та же ло | `src/stepik_grader/core/runner.py:720` | low |
| LNG-3-02 | После reader.join(timeout=...) закрывать proc.stdout/stderr/stdin в finally и не оставлять брошенный поток владельцем pipe: читать через selectors/неб | `src/stepik_grader/core/runner.py:727` | low |
| LNG-3-03 | Поставить signal.signal(SIGTERM, ...), вызывающий server.shutdown() и runs.shutdown_jobs() (или поднимающий KeyboardInterrupt), чтобы SIGTERM шёл тем  | `src/stepik_grader/web/server.py:271` | low |
| LNG-4-03 | Добавить в failure_kind() ветку ERR — вернуть 'runtime-error:<Класс>' через glossary.lookup_from_error, как для RE, либо хотя бы 'runtime-error'. Закр | `src/stepik_grader/core/insights.py:87` | low |
| LNG-5-02 | Печатать факт вытеснения («кэш заполнен: 88 вытеснено, лимит 512»), вынести лимит в [tool.stepik-grader] и в --help; либо держать записи покаталожно,  | `src/stepik_grader/core/cache.py:165` | low |
| LNG-5-03 | Считать удалённое по факту (число файлов/байт), нечитаемый кэш отмечать отдельной строкой. Заодно удалять сам каталог: после очистки остаётся пустой . | `src/stepik_grader/core/cache.py:179` | low |
| MAP-1-01 | Добавить CODE_OF_CONDUCT.md в перечень корневых файлов docs/README.md (рядом с SECURITY) и строкой в README.md § «Первый вклад за 15 минут». | `docs/README.md:13` | low |
| MAP-1-02 | Либо вынести крупные разделы (`--sandbox` MVP, веб-оболочка, CI-агент) в docs/dev/ со ссылками, либо снять слово «короткий» и добавить оглавление из # | `SECURITY.md:3` | low |
| MAP-1-03 | Описать правило именования решений подразделом docs/use/configuration.md (все четыре формы + что не подхватывается) либо перенаправить ссылку. | `CONTRIBUTING.md:340` | low |
| MAP-1-04 | Одна строка-предупреждение в README.en.md § More: «docs/* is in Russian; this page and the `?lang=en` UI are the English surface». | `README.en.md:37` | low |
| MAP-1-05 | Заменить на «Плюс файлы в корне репозитория» и оформить единым списком без числительного. | `docs/README.md:13` | low |
| MAP-1-06 | Добавить в конец обоих документов однострочный футер-возврат со ссылками на `README.md` и на развилку `docs/README.md`. | `SECURITY.md:1` | low |
| MAP-2-01 | В versions.md заменить одиночную ссылку парой: CHANGELOG.md (три последних MINOR) + docs/archive/changelog-archive.md (всё раньше), с явным указанием  | `docs/use/versions.md:5` | low |
| MAP-2-02 | Либо убрать колонку (эволюция метрик живёт в docs/archive/history.md по CLAUDE.md), либо расширить check_showcase_metrics на docs/use/versions.md. | `docs/use/versions.md:41` | low |
| MAP-2-03 | В шапке versions.md заменить ссылку на CONTRIBUTING.md ссылкой на docs/dev/versioning.md — тогда утверждение versioning.md:124 станет верным. | `docs/use/versions.md:6` | low |
| MAP-2-04 | Заменить ссылки на CLAUDE.md ссылками на docs/dev/architecture.md (инварианты) и SECURITY.md § Предупреждение о локальном исполнении; CLAUDE.md остави | `docs/use/configuration.md:171` | low |
| MAP-2-05 | Перенести абзацы «Реализация»/«Non-goal» в docs/dev/web-contracts.md, оставив в use/ одну ссылку «как устроено внутри». | `docs/use/web-interface.md:202` | low |
| MAP-2-06 | Дописать в строку use/ «отличия от первоисточника и эволюция релизов», синхронизировав формулировку с README.md:155. | `docs/README.md:8` | low |
| MAP-3-01 | Либо добавить e2e-тест на J5 (Copy input + Run again с тем же RunSpec), либо снять с README.md:37 обещание сплошного покрытия и пометить J5 в web-cont | `docs/dev/README.md:37` | low |
| MAP-3-02 | Заменить цель CLAUDE.md:386 на docs/dev/versioning.md (код-стайл/workflow оставить за CONTRIBUTING.md отдельной строкой) и убрать «политику версиониро | `CLAUDE.md:386` | low |
| MAP-3-03 | Добавить строки на docs/dev/glossary.md, trace-format.md, rules-insights.md либо явно записать, что полный индекс направления — docs/dev/README.md, а  | `CLAUDE.md:382` | low |
| MAP-3-04 | Добавить строку settings_adapter.py в блок web/ project-structure.md; заодно рассмотреть тест-гард «модули src/ ⊆ дерево project-structure.md» по обра | `docs/dev/project-structure.md:39` | low |
| MAP-3-05 | Расширить check_docs_index_completeness: подкаталог без своего README.md проверять против README.md ближайшего родителя и учитывать файлы-фикстуры (*. | `scripts/check_docs_guardrails.py:361` | low |
| MAP-4-01 | Перевести ADR-0009 в «Accepted (фаза 0 реализована: HistoryRepository/SqliteHistoryRepository в core/history.py; фазы 1–3 — дизайн)», синхронно поправ | `docs/dev/adr/0009-server-data-model.md:3` | low |
| MAP-4-03 | Заменить в строке 6 `core/db.py` на `db.py` (top-level) и добавить прямую ссылку на ../../../src/stepik_grader/db.py в поле «Связанный код». | `docs/dev/adr/0011-local-persistence.md:6` | low |
| MAP-4-04 | В статусе ADR-0001 заменить «эпик #151» на «roadmap-issue #59 (эпик #151 закрыт)», ту же правку сделать в ADR-0009:5 и ADR-0010:7, чтобы точка входа б | `docs/dev/adr/0001-server-mode.md:6` | low |
| MAP-4-05 | Добавить в шапку ADR-0001 поле «Развит в:» со ссылками на ADR-0006/0007/0008/0009/0010 и зафиксировать в docs/dev/adr/README.md § Соглашения требовани | `docs/dev/adr/0001-server-mode.md:9` | low |
| MAP-4-06 | Смягчить строку 3 до «согласованное будущее; отдельные слои уже реализованы и помечены в тексте» и добавить в таблицу колонку статуса (дизайн / частич | `docs/dev/design/README.md:3` | low |
| MAP-4-07 | Вынести web-design.md из таблицы «согласованное будущее» в отдельный абзац «Замыслы: отложенное и отклонённое — не контракт на реализацию» либо помети | `docs/dev/design/README.md:21` | low |
| MAP-5-01 | Привести подписи к реальному пути: `../../CHANGELOG.md`, `../../CLAUDE.md`, `../../CONTRIBUTING.md` в docs/agent/claude-handoff.md:9/65/66 и `../agent | `docs/agent/claude-handoff.md:9` | low |
| MAP-5-02 | Убрать (?!\.\.) и резолвить подпись относительно каталога документа (md.parent / caption) с fallback на прежнее сравнение хвоста пути; добавить регрес | `scripts/check_docs_guardrails.py:134` | low |
| MAP-5-03 | Обновить строку индекса на «релизы 1.1.0–1.7.0» и при следующей ротации править её тем же PR (правило ротации — CLAUDE.md § Обновление CHANGELOG.md). | `docs/archive/README.md:19` | low |
| MAP-5-04 | Обновить блок «Сейчас пусто» новой датой и одной строкой-указателем: очередь пуста, потому что находки 2026-08-10 независимы; живой аудит — docs/audit | `docs/agent/claude-handoff.md:15` | low |
| MAP-5-05 | Заменить на docs/archive/changelog-archive.md; заодно поправить устаревший пример в docstring check_docs_index_completeness (строки 366-369): у docs/a | `scripts/check_docs_guardrails.py:417` | low |
| MAP-5-06 | Дописать якоря к этим четырём ссылкам, сверив слаг функцией github_slug() из scripts/check_docs_guardrails.py (она же используется гейтом якорей). | `docs/agent/README.md:30` | low |
| MAP-5-07 | Оставить в индексе описание аудита и топ рисков без счётчиков (или строку «актуальные числа — в шапке файла аудита»), сами числа держать только в доку | `docs/audit/README.md:10` | low |
| MTX-1-04 | Перед print_benchmark_results(ranked, ...) проверять непустой ranked — как уже делает ветка режима 4 (commands.py:839). Заодно выровнять префикс '✗' у | `src/stepik_grader/cli/commands.py:722` | low |
| MTX-10-03 | Отвергать неизвестные ключи верхнего уровня 400 (или принимать repeats/number и там, зеркаля /api/grade) и возвращать фактические params в теле статус | `src/stepik_grader/web/api_routes.py:690` | low |
| MTX-2-03 | Свести текст локали с регексом: убрать обещание glob «task*.py» и сказать, что после task допустимы только цифры и _цифры; либо расширить регекс до ре | `src/stepik_grader/core/test_loader.py:48` | low |
| MTX-2-04 | Валидировать mode в _get_grade тем же кортежем, что и в _handle_create_run, и на неизвестном значении возвращать ошибку с message_id, а не тихую подме | `src/stepik_grader/web/api_routes.py:216` | low |
| MTX-3-05 | Добавить в шаблон bench_skipped_not_ac (ru.json и en.json) параметр {case} и передавать индекс первого провала из cli/commands.py:109 и web/viewmodels | `src/stepik_grader/core/locales/ru.json:223` | low |
| MTX-6-02 | В LinuxSandboxRunner.run после получения RunOutcome заменять в stdout/stderr абсолютный префикс run_dir на исходный путь решения из spec — тогда текст | `src/stepik_grader/core/sandbox/_linux.py:165` | low |
| MTX-6-03 | Пробросить spec.measure_memory в run_argv_with_limits: поток оставить (он контролирует max_memory_mb), но при measure_memory=False возвращать peak_mem | `src/stepik_grader/core/sandbox/_posix_common.py:179` | low |
| MTX-7-03 | Собирать stdin-строку один раз (в _RunPlan) и класть в результат ровно её, а не пересобирать в строке 610; ключ не выставлять для function-кейсов, как | `src/stepik_grader/core/grader_core.py:610` | low |
| MTX-7-04 | В load_test_cases предупреждать (тем же каналом, что и рассинхрон блоков), когда expected-файл пуст, а input непустой; в JSON отдавать признак пустого | `src/stepik_grader/core/test_loader.py:116` | low |
| MTX-8-01 | Сравнивать значением, а не строкой после round: math.isclose(a, b, rel_tol=1e-9, abs_tol=0) либо Decimal-сравнение с числом знаков из ожидания. Абсолю | `src/stepik_grader/core/normalizers.py:76` | low |
| MTX-8-02 | Не гонять через float() токены, где значащих цифр больше 17: сравнивать такие как Decimal(a) == Decimal(b) либо дословно. Закрывает заодно и схлопыван | `src/stepik_grader/core/normalizers.py:76` | low |
| MTX-8-03 | После round гасить знак нуля: r = round(...); if r == 0: r = 0.0 — верное решение с «-0.00»/«-0.0» перестанет получать WA, допуск станет симметричным. | `src/stepik_grader/core/normalizers.py:76` | low |
| MTX-8-04 | Добавить альтернативу для мантиссы без дробной части: r"(?<![\d.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)(?!\.\d)" — голое целое без экспоненты по-прежнему не | `src/stepik_grader/core/normalizers.py:57` | low |
| MTX-8-05 | Пробрасывать признак в результат кейса (normalized: true, выставляется в grader_core.py:442-443) и показывать в reporter: «✓ Test N: AC (совпало после | `src/stepik_grader/core/reporter.py:590` | low |
| PAIR-1-01 | В grader-workflow.md:390/474 и configuration.md:105 заменить `.grader_history.db` на «база истории (по умолчанию ~/.stepik-grader/history.db, см. hist | `docs/use/grader-workflow.md:390` | low |
| PAIR-1-03 | Дописать в § «Типы тестов» третий источник (детекция режима по AST/meta.json на уровне файла решения, приоритет над отсутствующим .type) и явно сказат | `docs/use/configuration.md:272` | low |
| PAIR-1-04 | Добавить warnings.warn при существующих input.txt/output.txt без распознанных блоков и строку в таблицу диагностики configuration.md:419-425 («input.t | `src/stepik_grader/core/test_loader.py:153` | low |
| PAIR-1-05 | Добавить строку про .stepik_cache/ (кэш ответов Stepik API) в таблицу configuration.md:30-36 и одно предложение в § --cache / --clear-cache в grader-w | `docs/use/configuration.md:35` | low |
| PAIR-1-06 | Завести ключи import_reference_ok/import_reference_failed в core/locales/{ru,en}.json и печатать через _t() — тогда регресс закроет check_locale_guard | `src/stepik_grader/cli/__init__.py:590` | low |
| PAIR-2-01 | Заменить строку 110 на `core/run_dir.py` в блоке core/ (не в sandbox/), добавить строки web/settings_adapter.py и cli/prompts.py. | `docs/dev/project-structure.md:110` | low |
| PAIR-2-02 | Добавить в tests/test_import_dag.py тест «каждый *.py пакета упомянут в architecture.md и project-structure.md и наоборот» — по образцу test_api_md_ma | `docs/dev/architecture.md:57` | low |
| PAIR-2-03 | Дать solution-уровню такую же таблицу «Поле \| Тип \| Всегда \| Описание», как у case-уровня, с реальными именами ключей SolutionResult. | `docs/dev/result-contract.md:133` | low |
| PAIR-2-04 | Переписать абзац: run_single_test/run_tests аннотированы CaseResult/SolutionResult, runtime-представление остаётся dict. | `src/stepik_grader/core/result.py:7` | low |
| PAIR-2-05 | Убрать конкретные числа из докстринга и сослаться на таблицы + docs/dev/api.md как единственный счётчик. | `src/stepik_grader/web/api_routes.py:117` | low |
| PAIR-2-06 | Дописать web/runs.py в строку 126 и core/result.py в строку 110 либо пометить в шапке графа, что внутрислойные рёбра неполны. | `docs/dev/architecture.md:126` | low |
| PAIR-2-07 | Добавить cancelled в перечень bench-полей с пометкой NotRequired и семантикой «прерван через cancel_event, не ошибка решения» — по аналогии с CANCELLE | `docs/dev/result-contract.md:138` | low |
| PAIR-3-01 | В ADR-0006 п.5 и в строке core/runner.py в architecture.md зафиксировать: реестр (_RUNNER/set_runner/run_spec/active_runner) — в core/runner.py, grade | `docs/dev/adr/0006-runner-abstraction.md:42` | low |
| PAIR-3-02 | Переписать п.5 Решения в редакции #794: не ронять грейдинг, но однократно за процесс называть повреждение/откат схемы с путём. | `docs/dev/adr/0011-local-persistence.md:85` | low |
| PAIR-3-03 | Обновить строку core/microbench_runner.py (исполнение через активный Runner через run_spec) и добавить в граф ребро `core/microbench_runner.py ──→ cor | `docs/dev/architecture.md:71` | low |
| PAIR-3-04 | Дописать в ADR-0002 п.4 второй триггер (позитивный nudge по серии успехов, #645) с ограничением «не чаще раза за сессию» и явным «настройку не меняет» | `docs/dev/adr/0002-history-opt-in.md:49` | low |
| PAIR-3-05 | Добавить в таблицу колонки «Дата» и «Реализовано» (да / частично / нет) из шапок ADR; в § Соглашения разрешить суффикс состояния реализации к статусу. | `docs/dev/adr/README.md:27` | low |
| PAIR-3-06 | Заменить «пункты 0-8» на «0-9» в architecture.md:16 и в CLAUDE.md § Команды (либо снять точный диапазон, оставив «зациклено до 0»). | `docs/dev/architecture.md:16` | low |
| PAIR-3-07 | Добавить в ADR-0007 пункт-исключение: пошаговый трейс требует импорта пакета проекта в дочернем процессе, под --sandbox недоступен и отдаёт явную ошиб | `docs/dev/adr/0007-sandbox-backends.md:29` | low |
| PAIR-3-08 | Дописать core/stats.py в список потребителей atomic_io в CLAUDE.md п.2 и упомянуть atomic_write_text в строке atomic_io.py в architecture.md:74. | `CLAUDE.md:244` | low |
| PAIR-4-01 | Добавить в README.en.md раздел Safety — зеркало README.md:171-182. Заодно расширить check_docs_guardrails.py проверкой паритета обязательных разделов  | `README.en.md:169` | low |
| PAIR-4-03 | Переписать строку 160: «guarantees differ per OS — no network isolation on Windows, no read isolation on macOS, only cwd-relative write containment on | `README.en.md:160` | low |
| PAIR-4-04 | Добавить в configuration.md § Ограничения и безопасность подраздел про веб-периметр (127.0.0.1, Host/Origin guard, --root, риск --no-root-confinement) | `SECURITY.md:4` | low |
| PAIR-4-05 | Убрать из пункта 3 ADR-0007 пер-ОС перечисление гарантий (оставить ссылку на таблицу SECURITY.md) либо привести к канону: «память/процессы — ядром, CP | `docs/dev/adr/0007-sandbox-backends.md:41` | low |
| READER-1-02 | Добавить перед командой строку-предусловие: «рядом нужна папка tests/ — вход в файле `1`, ожидаемый вывод в `1.clue`» + ссылка на grader-workflow.md#п | `README.md:139` | low |
| READER-1-03 | В README § Быстрый старт добавить: «Скачивание задач (пункт 8) требует одноразовой регистрации своего OAuth-приложения — см. installation.md; локальна | `README.md:143` | low |
| READER-1-04 | Заменить относительный путь на полный URL на GitHub (как во всех ссылках README). Развести две ветки: файла нет → «создайте secrets.json, запустив заг | `src/stepik_grader/core/locales/ru.json:148` | low |
| READER-1-05 | В reporter.py заменить `str(first_fail)` на `"-" if first_fail is None else str(first_fail)` в обеих ветках. Заодно убрать из образца конкретное значе | `src/stepik_grader/core/reporter.py:167` | low |
| READER-1-06 | Добавить пункт между первым и вторым: «если secrets.json ещё нет, мастер спросит Client id / Client secret из OAuth-приложения (installation.md § Шаг  | `docs/use/grader-workflow.md:596` | low |
| READER-2-01 | В § Запуск тестов развести два режима: `pytest tests/test_x.py --no-cov` для итераций и `pytest tests/ -x -q` перед PR. Одной строкой объяснить, что п | `CONTRIBUTING.md:230` | low |
| READER-2-02 | В § Локальные гейты добавить блок guardrails с полным списком команд и пометкой, какой релевантен при правке docs/локалей/web. В .github/PULL_REQUEST_ | `CONTRIBUTING.md:35` | low |
| READER-2-03 | Внести § Язык артефактов в сам CONTRIBUTING (не ссылкой на CLAUDE.md, который контрибьютор читать не обязан): что по-русски (докстринги, комментарии,  | `CONTRIBUTING.md:332` | low |
| READER-2-04 | В шаг 2 добавить копипаст-строки: `gh repo fork ArtVsMark/Stepik-Python-Grader --clone` (или git clone своего форка + `git remote add upstream`), зате | `CONTRIBUTING.md:29` | low |
| READER-2-06 | Заменить перечисление содержимого cli/ на критерий («всё, что обслуживает интерактивное меню и разбор аргументов — в cli/; библиотечное ядро — в core/ | `CONTRIBUTING.md:83` | low |
| READER-2-07 | Заменить `cli.py` на `cli/` в CLAUDE.md:162 — синхронно с CONTRIBUTING.md:83 и docs/dev/project-structure.md:16, где пакет назван правильно. | `CLAUDE.md:162` | low |
| READER-2-08 | Оставить одно вхождение: «… плюс регрессионный тест на XSS в `app.js` (экранирование в `esc()`). Живут в `tests/e2e/` …». | `CONTRIBUTING.md:279` | low |
| READER-3-01 | В CLAUDE.md:162 заменить cli.py на cli/ (пакет, фасад в __init__.py) и уточнить, что CLI запускается скриптом stepik-grader, а не python -m stepik_gra | `CLAUDE.md:162` | low |
| READER-3-02 | Добавить в § Команды и в § Чеклист перед PR прогон шести guardrail-скриптов (лучше одной командой-агрегатором). Либо снять формулировку «зеркалит CI», | `CLAUDE.md:179` | low |
| READER-3-03 | Переформулировать: источники не альтернативны, а складываются. gh issue list — источник статусов; docs/audit/ и claude-handoff.md читаются дополнитель | `CLAUDE.md:399` | low |
| READER-3-04 | Добавить в таблицу CONTRIBUTING.md строку-исключение для stdlib-leaf'ов верхнего уровня (atomic_io.py, db.py) со ссылкой на ADR-0011 и строку для ide. | `CONTRIBUTING.md:99` | low |
| READER-3-05 | Добавить в § Команды строки stepik-grader-gui и pytest --grader-mode, а в список точек входа на строке 162 — launcher.py и pytest_plugin.py. | `CLAUDE.md:195` | low |
| READER-3-06 | Добавить в таблицу строку «Локальный сквозной прогон по базе задач курса → docs/agent/local-sweep.md», а также строки для multiagent.md и docs/audit/R | `CLAUDE.md:390` | low |
| READER-3-07 | Убрать число из скобки на строке 506 — оставить «отчёт покажет нулевое покрытие», чтобы абзац не противоречил собственному правилу со строки 499. | `CLAUDE.md:506` | low |
| READER-4-01 | Заменить абзац таблицей исходящих каналов (куда / что уходит / чем гейтится), а фразу «ничего не уходит в сеть» оставить только про .grader_history.db | `SECURITY.md:64` | low |
| READER-4-02 | В таблице «Ваши данные» указать реальный дефолтный путь ~/.stepik-grader/history.db и правило авторезолва, сослаться на history_db_path в configuratio | `SECURITY.md:64` | low |
| READER-4-03 | Дополнить таблицу этими артефактами (что внутри, чем удаляется) и явно сказать, что --purge-history их не трогает; отдельно — где хранится и как отзыв | `SECURITY.md:64` | low |
| READER-4-04 | В § Предупреждение о локальном исполнении назвать вектор явно (токен Stepik читаем решением; под --sandbox закрыт только на Linux) и дать обход: держа | `SECURITY.md:34` | low |
| READER-4-05 | Описать в configuration.md § Ограничения и безопасность фактическое поведение LocalRunner (denylist-скраб и что именно он НЕ ловит), отметив, что под  | `docs/use/configuration.md:376` | low |
| READER-4-06 | Либо сделать джоб блокирующим на high, либо снять формулировку «держит свежим»: сказать, что CI-сигнал информационный и не уведомляет, а дата — момент | `SECURITY.md:120` | low |
| READER-4-07 | Добавить core/ai_hints.py в § Реализация по модулям с явным утверждением, что логируется (эндпоинт/модель/ошибка) и что промпт с кодом решения в лог н | `docs/dev/logging.md:86` | low |
| READER-5-01 | Добавить в README.en.md секцию «Security (short)» — зеркало README.md:171-182: дефолт без OS-изоляции, есть только таймаут и best-effort лимит памяти  | `README.en.md:160` | low |
| READER-5-02 | Добавить в конец секции ссылку на docs/use/web-interface.md с честной пометкой «(Russian)», как уже сделано в README.md:97. | `README.en.md:112` | low |
| READER-5-03 | Абзац в README.en.md: docs/ ведётся по-русски, EN-поверхность — README.en.md + `?lang=en` + врезки в SECURITY.md/CONTRIBUTING.md. Плюс врезка «In Engl | `README.en.md:192` | low |
| READER-5-04 | Дополнить врезку строк 23-25 двумя пунктами: что в скоупе (web XSS, path traversal, утечка secrets.json/токенов, zip-slip в downloader) и что out-of-s | `SECURITY.md:23` | low |
| READER-5-05 | В README.en.md после строки 68 добавить `python -m stepik_grader` (и `python -m stepik_grader.launcher`) как PATH-независимый вариант — зеркало README | `README.en.md:66` | low |
| READER-5-06 | Добавить в шапку README.en.md бейджи PyPI, Release и Python (те же URL, что в README.md:4-5, 12), строку «Status: Stable» и явное «Requires Python 3.1 | `README.en.md:3` | low |
| READER-5-07 | Заменить « » на английские кавычки в 14 перечисленных строках блока en в ui.json; блок ru не трогать. Закрепить тестом локалей: в en-значениях нет сим | `src/stepik_grader/web/static/locales/ui.json:493` | low |
| REV-1-05 | Собирать кейсы в dict по index (первый формат выигрывает) и предупреждать warnings.warn о смешении форматов 1/2 — тем же приёмом, что уже применён для | `src/stepik_grader/core/test_loader.py:214` | low |
| REV-1-06 | Перевести эти три сообщения и оба warnings.warn на русский (образец — подсказки history.py:141-150), оставив английскими только идентификаторы. | `src/stepik_grader/core/grader_core.py:322` | low |
| REV-2-01 | Ввести STEPIK_REQUIRE_VERSION_BASELINE=1 в шаге ci.yml:121 (паттерн уже применён: STEPIK_REQUIRE_SANDBOX_TESTS ci.yml:151, STEPIK_REQUIRE_E2E_TESTS ci | `scripts/check_version_consistency.py:189` | low |
| REV-2-02 | Сделать нулевой вход ошибкой по образцу check_web_imports.py:104: если collect_ui_strings() пуст или options.py не найден — класть в errors ('проверят | `scripts/check_docs_guardrails.py:570` | low |
| REV-2-03 | Сравнивать число найденных файлов с len(_SHOWCASE_FILES) и класть расхождение в errors: витрина — фиксированный список, её файл не может «просто отсут | `scripts/check_docs_guardrails.py:345` | low |
| REV-2-04 | Либо считать per-OS бейдж по базовому конфигу (отдельный `coverage xml` без .coveragerc.ci после прогона), либо переименовать метку в 'coverage (ubunt | `.github/workflows/ci.yml:283` | low |
| REV-2-05 | Отсутствие данных = отказ гейта, а не его отмена. Минимум: при degraded ронять coverage-combine на push в main, либо применять --fail-under к имеющимс | `.github/workflows/ci.yml:204` | low |
| REV-2-06 | Копировать секции целиком (обход всех ключей run/report), а не белый список из четырёх имён; плюс тест, сверяющий множество ключей .coveragerc.ci с кл | `scripts/generate_ci_coveragerc.py:76` | low |
| REV-3-03 | Скачивать со stream=True и читать iter_content с накопительным счётчиком, обрывая соединение и поднимая ExternalUrlRejected при превышении _MAX_EXTERN | `src/stepik_grader/core/stepik_client.py:285` | low |
| REV-3-04 | Вынести создание сессии с Retry-адаптером (без Authorization) в отдельный хелпер и слать через него и refresh_access_token, и обмен authorization_code | `src/stepik_grader/core/stepik_client.py:350` | low |
| REV-3-05 | Добавить в release.yml top-level permissions: contents: read, оставив точечные повышения у github-release и pypi-publish. | `.github/workflows/release.yml:16` | low |
| REV-4-01 | Пропустить result.error через _clip_value() или отдельный многострочный лимит по образцу _clip_diff_lines (трейсбек многострочный) — в том же месте, г | `src/stepik_grader/core/reporter.py:593` | low |
| REV-4-02 | Добавить print_case_verbose параметр `labels` с дефолтами _VERBOSE_LABELS ({"more_chars", "more_diff"}) по образцу _labels() и передавать их из CLI вм | `src/stepik_grader/core/reporter.py:564` | low |
| REV-4-03 | Повесить onKeydown на document со снятием в done() и добавить закрытие отказом по клику на подложку — тот же паттерн, что уже принят в #906 для онборд | `src/stepik_grader/web/static/core.js:642` | low |
| REV-4-05 | Добавить `if (!resp.ok) throw new Error(resp.status)` и на сбое показывать один видимый баннер «интерфейс без переводов / Повторить»; сам маркерный fa | `src/stepik_grader/web/static/core.js:500` | low |
| REV-5-01 | В applier'е волн не резать maxLength жёстким слайсом: обоснование отклонения писать целиком. Минимум — обрез по границе слова с явным «…» и обязательн | `docs/archive/audit-2026-07-30-full-roles.md:593` | low |
| REV-5-02 | Хранить в allowlist пару `id: подстрока detail` и сверять расхождение целиком. Отдельно — сервисный прогон, печатающий записи, не сработавшие ни на од | `tests/test_glossary_draft_pipeline.py:700` | low |
| REV-5-03 | Добавить в таблицу строку «общий stdlib-leaf (atomic_io.py, db.py) — top-level по ADR-0011: glossary/rules не должны тянуть core/» и оговорку к «прави | `CONTRIBUTING.md:94` | low |
| REV-5-04 | Добавить инвентарный guard: каждый *.py в src/stepik_grader (минус __init__.py и allowlist приватных) обязан иметь строку в таблице модулей; обратно — | `tests/test_import_dag.py:682` | low |
| REV-6-01 | Собирать bench-скрипт в tempfile.mkdtemp(prefix='stepik-bench-') с 0700 и сносить каталог в finally (как runner.py:527 и tracer.py:417), либо передава | `src/stepik_grader/core/microbench_runner.py:257` | low |
| REV-6-02 | Передавать spec.max_output_bytes or CONFIG.sandbox_max_output_bytes в build_bootstrap_argv на обоих POSIX-backend'ах — либо явно задокументировать, чт | `src/stepik_grader/core/sandbox/_linux.py:117` | low |
| REV-6-03 | Проставить в таблицу MIGR номер закрывшего PR (серия #794) и то же для остальных фактически закрытых строк §5 — иначе архив как источник состояния бес | `docs/archive/audit-2026-07-30-full-roles.md:196` | low |
| REV-6-05 | Убрать из комментария число файлов и снимок 88% (живой источник — бейджи), оставив только обоснование «scripts измеряются наравне с пакетом, отдельног | `pyproject.toml:302` | low |
| SBX-1-04 | Передавать path во всех трёх местах: `ctx.t("no_solutions_found", path=directory)` в commands.py:562, :656, :766. Плюс тест локалей, что ни одна отрен | `src/stepik_grader/cli/commands.py:562` | low |
| SBX-3-03 | Добавить memory=outcome.peak_memory_mb в вызов _fail_result в ветке TLE (grader_core.py:405-411) и в ветке RE — по образцу sandbox_violation на :389.  | `src/stepik_grader/core/grader_core.py:405` | low |
| SBX-4-02 | Прокидывать в изолированную команду маркер живости (sentinel из _posix_bootstrap.py) и при returncode==0 без маркера возвращать launch_error «песочниц | `src/stepik_grader/core/sandbox/_posix_common.py:326` | low |
| SBX-5-04 | Добавить в JSON-результат поле уровня прогона ("isolation": "none"\|"<backend>") и то же поле в строку статистики; отразить в docs/dev/result-contract | `src/stepik_grader/cli/commands.py:509` | low |
| SBX-5-05 | Вынести текст в ключ локали (ru/en) и возвращать по lang запроса. Дополнительно гасить кнопку «Шаг за шагом», когда сервер поднят с --sandbox: бейдж и | `src/stepik_grader/core/tracer.py:392` | low |

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

### Среднесрочные цели фаз 2-4

78 целей, где нужен дизайн или несколько PR.

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| STR2-1-04 | Скачивание задачи пишет манифест набора (формат, список кейсов, их число); загрузчик сверяет загруженное с объявленным и при расхождении отдаёт UNVERI | `src/stepik_grader/core/test_loader.py:204` | high |
| CNC-5-05 | Добавить в ключ кэша отпечаток условий исполнения (имя runner'а, backend песочницы, timeout_seconds) или разносить записи по под-ключу окружения; как  | `src/stepik_grader/core/cache.py:112` | medium |
| FZZ-3-01 | Конфайнить не только аргумент, но и производные пути: прогнать итоговый каталог из resolve_test_dir через _resolve_within_root(workspace, ...) и отбра | `src/stepik_grader/core/test_loader.py:268` | medium |
| JRN-4A-02 | task_key_for вызывать не с base=Path.cwd() (commands.py:497,603,693,859), а с корнем рабочего пространства (маркер каталога) либо писать канонический  | `src/stepik_grader/cli/commands.py:497` | medium |
| JRN-5-04 | Считать clean_streak по прогонам той же задачи (или считать чистыми только прогоны, где ключ мог проявиться), а не по глобальному окну последних N про | `src/stepik_grader/core/insights.py:316` | medium |
| LNG-1-04 | Нормализовать ключ у точки записи: считать task_key от корня рабочего пространства (или от каталога самой БД), а не от Path.cwd(); при чтении схлопыва | `src/stepik_grader/cli/commands.py:497` | medium |
| MTX-1-01 | Не смешивать пути замера: считать _micro_stats отдельно по timeit- и subprocess-кейсам, в результат добавить поле метода замера. На смешанном наборе — | `src/stepik_grader/core/grader_core.py:893` | medium |
| MTX-2-02 | Добавить второй критерий по хвосту (Max или p90 к эталону) и отдельный вердикт «медленно на части кейсов», когда медианы близки, а Max/Std dev расходя | `src/stepik_grader/core/grader_core.py:186` | medium |
| REV-7-04 | Перед волной строить матрицу исполнимой поверхности (CLI-режимы 1–4, web-разделы, downloader, три sandbox-backend'а) и требовать хотя бы один прогонны | `src/stepik_grader/core/microbench_runner.py:198` | medium |
| STR2-1-01 | Ввести терминальный статус UNVERIFIABLE с полем reason и отдавать его вместо WA/RE во всех ветках «среда не смогла проверить». CLI/web/JSON рендерят е | `src/stepik_grader/core/result.py:22` | medium |
| STR2-1-06 | Определить контракт паритета: активация --sandbox прогоняет через выбранный backend встроенный мини-набор эталонных решений с известными вердиктами; р | `src/stepik_grader/core/sandbox/_linux.py:165` | medium |
| STR2-2-01 | Ограничить поиск pyproject корнем workspace, требовать секцию [tool.stepik-grader], падать понятной ошибкой, убрать cwd из sys.path. Первый пункт очер | `src/stepik_grader/config.py:273` | medium |
| STR2-2-03 | Добавить в ключ отпечаток условий (runner/backend, timeout, лимиты) и версию схемы. Строго ДО sandbox-работ и параллельных прогонов: иначе их фиксы пр | `src/stepik_grader/core/cache.py:110` | medium |
| STR2-2-04 | Устойчивый идентификатор (курс+шаг из метаданных, не путь) + миграция истории и confirm на purge. Строго перед правками insights/progress_export/stats | `src/stepik_grader/core/history.py:672` | medium |
| STR2-2-05 | Расширить контракт результата флагами truncated/decode_errors/tolerance_applied и заполнять их в runner; reporter/web/normalizers читают контракт. Обр | `src/stepik_grader/core/runner.py:737` | medium |
| STR2-2-07 | Вести отдельной веткой параллельно ступеням 1-6: согласие привязать к конкретному ai_base_url и endpoint, конфайнмент проверять в одной точке для grad | `src/stepik_grader/web/api_routes.py:845` | medium |
| STR2-4-02 | Второй каталог — мутации набора: удалить expected_N, ведущий ноль в имени, BOM/cp1251, посторонний .type, смесь форматов 2+3, пустой input; ожидание — | `scripts/corpus_mutations.py:189` | medium |
| CANON-2-06 | Сделать supports_project_imports свойством backend'а, а не константой фасада, либо заменить обоснование на честное «единый консервативный отказ ради о | `src/stepik_grader/core/sandbox/__init__.py:53` | low |
| CANON-3-06 | Завести 2-3 собственные задачи в corpus/tasks (включая одну в формате 3 — corpus/README.md:29-31 фиксирует только формат 2), добавить job в ci.yml, а  | `docs/dev/corpus.md:24` | low |
| CANON-4-01 | Ограничить хвостовую эвристику: засчитывать хвост только если карточка принадлежит тому же модулю (по docs_url/section/tags) либо сущность из builtins | `src/stepik_grader/glossary/coverage.py:88` | low |
| CNC-2-03 | Добавить GET /api/v1/runs со списком нетерминальных прогонов (id, kind, path, progress) и дать UI подхватывать/отменять их после перезагрузки; либо га | `src/stepik_grader/web/api_routes.py:141` | low |
| CNC-2-04 | Подметать осиротевшие tmp*.py при старте сервера (скан по маске и mtime), как уже сделано для stepik-sandbox-* каталогов; маску зафиксировать, чтобы у | `src/stepik_grader/web/runs.py:391` | low |
| CNC-3-01 | Ввести общий на хост лимит одновременных исполнений (advisory-лок/семафор в --root или ~/.stepik-grader), а в результат bench/microbench добавить приз | `src/stepik_grader/web/runs.py:230` | low |
| CNC-3-03 | В ветке каталога писать по записи на решение (как ветка kind=="file" строкой выше), передавая solution_name/solution_hash каждого файла; иначе «Подучи | `src/stepik_grader/web/viewmodels.py:804` | low |
| CNC-3-04 | Не класть временный файл исполнения в пользовательский --root (общий для двух серверов), а в приватный каталог прогона; сирот подстраховать сторожем ( | `src/stepik_grader/core/runner.py:543` | low |
| CNC-4-04 | Сверять при чтении task_progress: runs_total, превышающий возможное число прогонов, помечать агрегат недостоверным. Плюс дать команду пересчёта агрега | `src/stepik_grader/core/history.py:315` | low |
| CNC-5-02 | Перечитывать results.json в save() и мержить свои entries в свежий снапшот (read-merge-write под тем же mkstemp+replace), либо хранить запись на решен | `src/stepik_grader/core/cache.py:94` | low |
| FZZ-2-02 | Сравнивать по байтам либо декодировать stdout через errors="surrogateescape" (обратимо), чтобы разные байты оставались разными. Как минимум: при U+FFF | `src/stepik_grader/core/grader_core.py:418` | low |
| INS-1-02 | В списочном ответе оставить id/title/kind/summary/section/status (тело — из /api/glossary/<id>), включить gzip для JSON и ETag/Cache-Control на неизме | `src/stepik_grader/web/glossary_adapter.py:178` | low |
| INS-2-03 | Сделать импорт psutil ленивым (внутри функции-замерщика) и обернуть в try/except ImportError по образцу _RICH: при недоступности печатать «Memory, MB  | `src/stepik_grader/core/runner.py:40` | low |
| INS-4-02 | Останавливать подъём на границе (домашний каталог либо первый каталог с .git) и печатать в шапке прогона путь применённого pyproject.toml с полями, от | `src/stepik_grader/config.py:274` | low |
| INS-5-03 | Разобрать --lang первым проходом (parse_known_args), вызвать set_lang до build_parser и брать description/epilog/help из локали через _t() по ключам c | `src/stepik_grader/cli/options.py:61` | low |
| JRN-1-05 | Показывать в шапке «Подучить» источник и окно (путь БД, число и дата прогонов), добавить колонку задачи и пометку, что данные накоплены раньше/другим  | `src/stepik_grader/cli/interactive.py:232` | low |
| JRN-2-03 | Ввести общий конверт {"schema":1,"mode":"grade\|batch\|benchmark\|microbench","results":{...}} и зафиксировать его в docs/dev/result-contract.md; влож | `src/stepik_grader/cli/commands.py:510` | low |
| JRN-2-04 | Либо включать оба журнала одним тумблером и одинаково сообщать о пустоте, либо в --insights показывать источник и период («из ~/.stepik-grader/history | `src/stepik_grader/core/history_recording.py:144` | low |
| JRN-3B-05 | Дать в веб-онбординге и в пустом состоянии «Проверить» вторую дорожку «Своя задача без Stepik»: кнопку создания каталога с шаблоном tests/. И убрать и | `src/stepik_grader/web/static/index.html:530` | low |
| JRN-4A-04 | Показывать в «Прогрессе»/экспорте происхождение задачи (хвост пути/курс) и дать фильтр по рабочей папке; ключи вида «имя временного каталога» — призна | `src/stepik_grader/core/history_recording.py:172` | low |
| JRN-4B-04 | Перенести onboarding_seen и ai_hint_consent в пользовательский профиль рядом с user_history_db_path(), оставив per-workspace только папочное; либо гас | `src/stepik_grader/web/server.py:177` | low |
| JRN-5-05 | Шапку считать по сумме task_progress.runs_total, а секции вердиктов/падений подписывать «по последним N сохранённым прогонам», чтобы усечение было вид | `src/stepik_grader/core/progress_export.py:97` | low |
| LINK-1-04 | Расширить link-check на src/stepik_grader/**/*.{py,js} и scripts/*.py: вытаскивать `docs/…\.md` и `](…\.md#якорь)`, резолвить от корня (или от файла д | `scripts/check_docs_guardrails.py:180` | low |
| LNG-2-03 | Считать сводку агрегирующими запросами вместо чтения 10 000 прогонов на каждый GET; список задач — страницами (?limit/?offset). Плюс кэш в процессе с  | `src/stepik_grader/web/api_routes.py:297` | low |
| LNG-4-02 | Считать шапку и таблицу из одного источника: либо total_runs = сумма p.total_runs по task_progress, либо заводить строку task_progress и для бенчмарк- | `src/stepik_grader/core/progress_export.py:97` | low |
| LNG-4-04 | Разделить тали на correctness (AC/WA/RE/TLE) и benchmark (SIMILAR/SLOWER/MUCH_SLOWER/ERR) — у history.runs есть mode, отфильтровать по нему. Показыват | `src/stepik_grader/core/progress_export.py:78` | low |
| LNG-4-05 | Подписать метрику так, чтобы её нельзя было сложить с временем грейдинга: «Прошло от первой попытки» вместо «Время до AC», и «Время грейдинга» в --sta | `src/stepik_grader/core/insights.py:229` | low |
| MTX-1-03 | Развести флаги: --number оставить за timeit-путём, для function-кейсов ввести отдельное число повторов (или переиспользовать --repeats). В заголовке и | `src/stepik_grader/core/grader_core.py:869` | low |
| MTX-10-05 | Свести кейс к одному набору полей: либо добавить glossary/glossary_ids/suggestions/severity/timeout_s в CLI-JSON, либо явно записать в result-contract | `docs/dev/result-contract.md:9` | low |
| MTX-4-03 | Либо задокументировать в configuration.md, что function-режим сравнивает напечатанное значение и типы не различает, либо печатать repr(_result) для не | `src/stepik_grader/core/wrapper_builder.py:159` | low |
| MTX-6-04 | Кэшировать список потомков между итерациями (перечитывать раз в ~0.5 с, а не каждые 20 мс) и увеличить период опроса; в первую очередь починить возвра | `src/stepik_grader/core/runner.py:318` | low |
| MTX-6-05 | Помечать режим изоляции в записи истории и в отчёте (--output json/markdown), предупреждать при сравнении прогонов разных режимов в --mode 3/4 и зафик | `src/stepik_grader/core/sandbox/_linux.py:172` | low |
| MTX-7-02 | Разделить два правила: файлы тестов читать как сейчас, а stdout решения резать только по '\n' и '\r\n', оставляя одиночный \r данными. В docs/use/conf | `src/stepik_grader/core/normalizers.py:27` | low |
| MTX-9-04 | Ввести лимит сериализации (json_max_value_lines в GraderConfig) и класть в JSON усечённые output/expected/diff с явными полями truncated/dropped_lines | `src/stepik_grader/cli/commands.py:510` | low |
| REV-7-01 | В чек-лист multiagent.md добавить обязательный слой «срез на поверхность продукта»: матрица «формат тест-кейсов 1/2/3 × режим CLI 1–4 × web-раздел», у | `docs/archive/audit-2026-07-30-full-roles.md:139` | low |
| REV-7-02 | В промпт читающего среза внести обязательный пункт: перечислить каждый continue/except/return None/zip(strict=False), гасящий вход, и назвать, что при | `src/stepik_grader/core/test_loader.py:205` | low |
| REV-7-03 | Ввести обязательный срез «регрессия по закрытым находкам»: вход — список PR, закрывших прошлый аудит, зона — их диффы, вопрос «не внесло ли лечение но | `src/stepik_grader/core/history.py:709` | low |
| REV-7-05 | Расширить каталог мутациями фикстуры (unpaired_expected, zero_padded_index, block_count_mismatch, cp1251_fixture, bom_input) с ожиданием «внятный отка | `docs/dev/corpus.md:54` | low |
| REV-7-06 | Печатать сводку severity генератором из накопителя вердиктов (одно число — один источник), ручные пересказы в multiagent.md заменить ссылкой на §3 отч | `docs/audit/2026-08-10-full-roles.md:93` | low |
| SBX-2-01 | Снимать start после реального старта решения (или вычитать измеренный оверхед spawn'а), либо помечать в выводе, что время под --sandbox включает фикси | `src/stepik_grader/core/sandbox/_posix_common.py:134` | low |
| SBX-2-02 | Считать relative по времени без стоимости изоляции (см. предыдущую находку) либо помечать таблицу флагом изоляции и не сравнивать прогоны с разным реж | `src/stepik_grader/core/grader_core.py:802` | low |
| SBX-2-04 | Вычитать базовый RSS интерпретатора (замер пустого скрипта) либо брать пик из cgroup memory.peak вместо poll'а; в отчёте помечать точность и не сравни | `src/stepik_grader/core/sandbox/_posix_common.py:84` | low |
| SBX-3-04 | Пометку об обрезке класть в поле результата, а не в отбрасываемый stderr, и показывать на WA. Отдельно капнуть отчёт: ограничить число строк output/di | `src/stepik_grader/core/runner.py:737` | low |
| SBX-4-04 | При активном SandboxRunner добавлять в текст ошибки контекст «исполнение изолировано: запись/сеть вне рабочей папки запрещены» и гасить glossary-подск | `src/stepik_grader/core/grader_core.py:421` | low |
| SBX-5-03 | Миграция схемы v2: колонка `isolation TEXT` в runs (none/bwrap/seatbelt/job-object), заполняется из активного Runner. Иначе «Подучить», «Прогресс» и т | `src/stepik_grader/core/history.py:234` | low |
| STR2-1-02 | Ввести RunManifest (объявлено/загружено кейсов, формат, путь конфига и его отличия от дефолта, runner/backend, источник вердикта, ключ задачи) как пол | `src/stepik_grader/core/result.py:55` | low |
| STR2-1-05 | Добавить в гейты перед PR acceptance-прогон для изменений на пути вердикта (loader/runner/sandbox/cache/config/history): реальная задача, четыре конфи | `CONTRIBUTING.md:36` | low |
| STR2-3-01 | В § Дополнительно для аудитов добавить пред-фильтр в промпт среза: не заводить находку, если она (а) idea без дефекта поведения, (б) целиком внутри .m | `docs/agent/multiagent.md:216` | low |
| STR2-3-03 | Одно решение вместо двенадцати правок: либо добавить stats в список кандидатов на вырезание рядом со STR-1, либо объявить CLI-only диагностикой мейнте | `src/stepik_grader/core/stats.py:58` | low |
| STR2-3-04 | Зафиксировать в докстринге лаунчера и docs/use/grader-workflow.md границу: лаунчер — три кнопки запуска сервера, остальное живёт во флагах CLI и в раз | `src/stepik_grader/launcher.py:13` | low |
| STR2-3-05 | Записать в threat model: --sandbox — защита от собственной ошибки (fork-bomb, бесконечный цикл, запись мимо папки), а не рубеж против враждебного кода | `docs/use/configuration.md:375` | low |
| STR2-3-06 | Добавить в § Обновление CHANGELOG.md жанр PR «гигиена»: пачка однотипных мелочей одной веткой и одной строкой вида «- гигиена доков/CI: N правок (#PR) | `CLAUDE.md:338` | low |
| STR2-4-03 | Завести tests/e2e/test_verdict_regressions.py: каждая закрытая находка фазы 2 — параметризованный кейс «вход → ожидаемый вердикт/ошибка» с id находки  | `tests/e2e/test_journeys.py:1` | low |
| STR2-4-05 | Матрица «один вход — четыре пути» (CLI, CLI+cache, web, --sandbox): вердикт и состав кейсов обязаны совпадать после нормализации, метрики — расходитьс | `tests/test_facade_contract.py:1` | low |
| STR2-4-06 | Ввести тип измерения (значение + единица + источник + флаг «не измерено») и запретить 0.0 как замену отсутствию: невалидный замер не попадает в рейтин | `src/stepik_grader/core/result.py:73` | low |
| STR2-5-01 | Ввести в CONTRIBUTING.md единицу работы «sweep PR»: одна ветка закрывает пачку однотипных низких находок по зоне (docs, cli-вывод, локали) с одной стр | `CONTRIBUTING.md:345` | low |
| STR2-5-02 | Записать в claude-handoff.md очередь спринта: 12 файлов топа по весу плюс 7 с одиночными high (wrapper_builder, __main__, mode_detector, task_page_par | `docs/agent/claude-handoff.md:1` | low |
| STR2-5-03 | Не разбирать 27 находок по одной, а переписать загрузчик тест-кейсов целиком одним PR с новым набором тестов на три формата: диффы всё равно пересекаю | `src/stepik_grader/core/test_loader.py:1` | low |
| STR2-5-04 | Закрывать нерантайм одной-двумя sweep-ветками по направлениям docs/, а повторяемость дрейфа гасить проверкой в check_docs_guardrails.py (сверка упомян | `scripts/check_docs_guardrails.py:1` | low |
| STR2-5-05 | Дописать в docs/audit/README.md режим массового решения: после спринта хвост low разбирается пачками по файлу либо отклоняется списком с общей причино | `docs/audit/README.md:40` | low |
| STR2-5-06 | Добавить в разбор после волны (multiagent.md) обязательный шаг: PARTIAL не переходит в issue как есть — либо переписывается в CONFIRMED-формулировку п | `docs/agent/multiagent.md:146` | low |

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

### Долгосрочные идеи фаз 2-4

| ID | Что сделать | file:line | Итог |
|---|---|---|---|
| STR2-1-03 | Зафиксировать инвариант: всё, влияющее на вердикт, приходит явным входом (флаг, файл задачи, манифест), окружение — только подсказка, попадающая в пас | `src/stepik_grader/config.py:282` | medium |
| STR2-4-01 | Разнести на три шага: детектор формата (формат + список претензий), валидатор набора (непарный, недекодируемый, ведущий ноль, смесь форматов — ошибка) | `src/stepik_grader/core/test_loader.py:118` | medium |
| STR2-4-04 | Ввести доменный идентификатор задачи (course/lesson/step из URL Stepik либо хеш условия+тестов) как первичный ключ БД, имя папки — только отображаемый | `src/stepik_grader/core/history.py:647` | medium |

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

## 14-бис. Приговоры критиков фаз 2 и 3

Правило «критик проверяет метод своей фазы» дало три независимых разбора. Каждый опирается на числа
накопителя, а не на впечатление, и каждый признан справедливым после сверки хостом.

### Критики прогонной фазы (Ф2-9)

- **Новизна узкая.** 209 находок фазы 2 из 224 указывают на файлы, уже помеченные фазой 1; 100 лежат в
  пределах ±20 строк от конкретной находки чтения. Принципиально новых файлов — 15.
- **Прогон покрыл треть исполнимой поверхности.** Каналов оказалось два: `--mode` и `curl`. Интерактивное
  меню `python -m stepik_grader.grader` не вызывалось **ни разу**, браузер не открывался ни разу — на 4875
  строк JS пришлось 2 находки. Не запускались playground и трейс; из ~24 эндпоинтов не вызваны семь.
- **Одна точка окружения.** Linux, CPython 3.12.3, rich и psutil на месте, одна база истории на все волны.
  Из девяти комбинаций CI-матрицы отработала одна; 647 строк macOS- и Windows-бэкендов песочницы не
  исполнялись. Отсюда ноль находок про отсутствие `rich`, про права доступа, про установку из wheel.
- **Фаза 2 не проверяла фазу 1.** Ни одна находка чтения не снята и не понижена по итогам запуска; все
  опровержения вынесены чтением ещё до прогона. Формат накопителя вдобавок не отличает «прогнали и чисто»
  от «не запускали»: в нём есть только дефекты.

### Критики документационной фазы (Ф3-5)

- **Покрытие дырявое.** Якорь `file:line` есть у 33 документов из 80; архив почти не тронут (1 из 24), пять
  ADR из одиннадцати без единой находки.
- **Ось «код → документы» не проверялась вовсе** — и оказалась урожайной: 17 битых ссылок из 389, включая
  `docs/audit-2026-07.md` в четырёх местах кода. Её закрыл срез LINK-1 фазы 4.
- **Три оси остались за бортом** и тоже ушли в фазу 4: документ против issue (393 ссылки вида `#NNN`),
  документ против шаблонов `.github`, документ против `release.yml`.
- **Тяжесть предсказуемо ниже.** 1 заявленный `high` на 127 находок против 16 подтверждённых на 738 в
  фазах 1-2; после верификации не осталось ни одного. Прогноз критика «усохнет около 45» сбылся: понижена
  половина находок фазы.

### Что это меняет в методе

Ни один из трёх приговоров не отменяет находок — все они про **непроверенное**, а не про ложное. Практический
вывод один: полнота аудита определяется не числом ролей и не числом находок, а **разнообразием способов
проверки**. Три фазы дали три разных класса дефектов, и пересечение между ними меньше, чем ожидалось.

Незакрытым остаётся то, что назвали критики и на что не хватило окна: интерактивное меню, браузер, вторая ОС,
Python 3.13/3.14, установка из wheel, отсутствие `rich`. Это и есть содержание следующего аудита.

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
| DEV-3-05 | Лимит размера внешней загрузки проверяется после того, как тело уже в памяти | #1069 |
| DEV-3-06 | Обрыв сети при обновлении токена выдаётся за неверные OAuth-учётные данные | #1070 |
| REV-3-04 | refresh_access_token ходит мимо retry-сессии: единичный 503 роняет прогон | #1073 |
| STR-3-03 | Смена формы ответа Stepik маскируется под «шаг не найден» | #1073 |
| STR-3-06 | API_HOST зашит константой; diagnostic импортирует значение, а не модуль | #1073 |
| INS-3-04 | `python -m stepik_grader.ide` — тихий no-op с кодом 0 | #1071 |
| STR-1-07 | ide.py: статичный tasks.json без проверки на дрейф флагов CLI | #1071 |
| VIS-2-03 | Точка расширения асимметрична: set_runner в фасаде, Runner/RunSpec — в core/ | #1071 |
| STR-3-05 | Нет офлайн-способа завести задачу: каталог умеет делать только загрузчик | #1071 |
| SET-2-02 | save_settings переписывает файл целиком — флаги другого канала теряются | #1074 |
| CNC-5-01 | Открытое меню воскрешает отозванное AI-согласие | #1074 |
| CNC-5-04 | Сессия меню откатывает подтверждённую вебом запись | #1074 |
| MTX-4-04 | Ноль загруженных кейсов: неверное решение получает JSON без провалов | #1082 |
| CNC-1-01 | Кэшированный TLE залипает: верный код «не проходит» без запуска | #1079 |
| CNC-1-03 | Попадание в кэш пишется в статистику как прогон с чужим total_time | #1079 |
| JRN-4A-02 | «Прогресс» рисует одну задачу двумя строками (ключ от cwd) | #1081 |
| LNG-1-04 | Одна задача — два task_key: дубли, двойной TTFG, удвоенный retention | #1081 |
| DES-2-08 | Неверный номер профиля в режимах 3/4 молча подменяется на профиль 2 | #1078 |
| JRN-1-05 | «Подучить» в свежей установке показывает чужие карточки из глобальной БД | #1078 |
| VIS-1-03 | История — тупик для интеграций: --insights игнорирует --output | #1077 |
| COM-1-07 | Прогресс не складывается по группе: нет машинного экспорта | #1077 |
| SET-3-03 | Таймаут и лимит памяти нельзя задать из CLI — только правкой pyproject.toml | #1092 |
| SBX-5-04 | JSON и .grader_stats.jsonl не помечают изоляцию — режим прогона недоказуем | #1096 |
| JRN-2-03 | Четыре режима — четыре JSON-схемы без пометки режима | #1096 |
| INS-5-03 | `--lang en --help` печатает целиком русскую справку | #1094 |
| JRN-2-06 | Режимы 2/3/4 молча не видят решение без шаблона task*.py | #1100 |
| JRN-1-03 | Режим 1 принимает любое имя, режимы 2/3/4 — нет | #1100 |

---

## 17. Что дальше

Все четыре фазы отработаны, результаты сведены в этот файл — правило «один аудит, один файл» соблюдено.

1. **Порядок работ задан не severity, а графом заражения** (стратегический срез Ф2-8). Первым чинится
   детерминизм окружения: пока чужой `pyproject.toml` из родительского каталога переворачивает вердикт
   (`AC 2/2 → FAIL 0/2`), репро остальных находок недоказуемо. Вторым — строгая загрузка набора тест-кейсов,
   где сидят девять `high` из шестнадцати. Третьим — ключ кэша, который заражает ещё 19 находок.
2. **Не всё, что найдено, идёт в работу.** Из 870 находок рабочий пул — около 307: 138 вообще не про продукт,
   313 несут вердикт `PARTIAL`, часть помечена скептиками как «по замыслу». Остальное закрывается **статусом,
   а не PR** — иначе трекер захлебнётся задачами, которые никто не собирается делать.
3. **Эпик и подэпики** — по риск-группам § 2 и по зонам фаз 2-4; issue заводятся на `bug`, `high` и `medium`.
   Находки, не попавшие в issue поимённо, живут в эпике хвоста таблицами по направлениям.
4. **Очередь работ с рёбрами** — в [`../agent/claude-handoff.md`](../agent/claude-handoff.md): что за чем и
   что сломается при обратном порядке.
5. **Следующий аудит** обязан закрыть то, что назвали критики и на что не хватило окна: интерактивное меню,
   браузер, вторая ОС, Python 3.13/3.14, установка из wheel, поведение без `rich`. И отдельной зоной — код,
   дописанный по находкам **этого** аудита: ревизия показала, что именно он остаётся непроверенным.
