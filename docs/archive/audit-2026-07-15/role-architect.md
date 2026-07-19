## 🏛️ Архитектор

**Методика.** Прочитаны CLAUDE.md, docs/README.md, docs/architecture.md, docs/server-mode.md, SECURITY.md и § 1 прошлого аудита (docs/audit-2026-07.md, snapshot v1.7.0). Реальный граф импортов построен AST-скриптом (python3.13) по всем 74 файлам `src/stepik_grader/` и сверен с задекларированным DAG; поиск циклов — DFS. Прочитаны ключевые модули: `core/grader_core.py`, `core/runner.py`, `core/executor.py`, `core/error_glossary.py`, `core/microbench_runner.py`, `config.py`, `cli/__init__.py`, `web/{server,runs,playground,viewmodels,__init__}.py`, `grader.py`. Каждая находка прошлого аудита (R1–R7) перепроверена по коду v1.8.0+.

### Сильные стороны

- **DAG подтверждён кодом.** На уровне модулей циклов нет; leaf-модули (`storage`, `normalizers`, `core/glossary`, `i18n`, `diag_log`, `mtime_cache`, `tracer`, `cli/context`, `cli/rendering`) действительно без project-импортов; островки `glossary/` и `rules/` не тянут `core/`-бизнес-логику (у `rules/json_provider` — только утилитарный `core/mtime_cache`).
- **R1 прошлого аудита закрыт честно:** `--sandbox` + `--serve` теперь `parser.error` вместо тихого игнора (`cli/__init__.py:461-468`, issue #351).
- **R3 закрыт:** записи под `threading.Lock` — `core/stats.py:49` (`_WRITE_LOCK`), `glossary/json_provider.py:227` (`_MISSING_QUEUE_LOCK`).
- **Двойник i18n консолидирован (issue #355):** захардкоженный `_MESSAGES` слит в JSON-локали `core/locales/`; CLI `_t()` (`cli/__init__.py:147-164`) и `web/i18n.py` рендерят один каталог.
- **Двойник глоссариев консолидирован на уровне lookup (issue #356):** `core/error_glossary.py` — единый RE-резолвер для CLI (`reporter`) и web (`viewmodels`), `card_url()` заменил три несовместимые URL-стратегии; компактная карта осталась осознанным офлайн-fallback'ом.
- **Runner Protocol — качественный шов:** вердикты считаются над раннером (`grader_core.run_single_test`), `SandboxRunner` реализует тот же протокол, инъекция через одну точку `set_runner()`; дисциплина локов в async-job-модели (`web/runs.py`, `Job.lock` vs `_JOBS_LOCK`) документирована и корректна.
- Web-слой держит форму «тонкий роутинг → viewmodels/адаптеры», body-limit/host-allowlist/конфайнмент путей — server-мышление уже в MVP.

### Находки

**A1 (high). Пути исполнения в обход Runner-шва: `--sandbox --mode 4` исполняет код без песочницы.** `run_microbench_mode` для stdin-блоков зовёт `run_microbench()` (`core/grader_core.py:656-658`), а тот запускает код решения напрямую `subprocess.Popen([sys.executable, "-c", bench_script])` (`core/microbench_runner.py:210-214`) — мимо `_RUNNER`. Help-текст обещает песочницу для «--mode 1/2/3/4» (`cli/options.py:202-210`); SECURITY.md в списке «именованных пробелов» этот случай не называет. Итог: под явным флагом безопасности часть кода решения исполняется неизолированно — тот же класс проблемы, что закрытый R1 (тихое ослабление явно запрошенной гарантии).

**A2 (medium). Runner — не единственная точка исполнения.** Помимо A1: `web/playground.py:62` инстанцирует `LocalRunner()` хардкодом (инъекция `set_runner()` до него не долетит), `core/tracer.py` спавнит свой subprocess. Для server mode (#151) это значит: «подменить исполнение» — не одна точка, а четыре. Абстракция есть, но не является choke point'ом, на который рассчитывает docs/server-mode.md.

**A3 (medium). Двойник executor.py vs runner.py НЕ консолидирован.** `core/executor.py` (183 строки) — параллельная система запуска со своей политикой таймаута (SIGALRM + env `EXECUTOR_TIMEOUT`), грейдером не вызывается (комментарий в `grader_core.py:62-63`), жива только ради `tests/test_executor.py` и реэкспорта `RunResult` (`grader_core.py:65-70`). architecture.md продолжает описывать её как действующий Infrastructure-модуль (строки 56, 91, 185). Мёртвый вес с расходящейся семантикой таймаутов.

**A4 (medium). Дрейф docs/architecture.md + нет автоматического DAG-guard'а.** Четыре упоминания уже удалённого `_MESSAGES` (строки 16, 21, 67, 97 — «JSON-локали поверх статического _MESSAGES», что после #355 неверно); в графе нет рёбер `core/insights → core/{glossary,history,normalizers}` (`insights.py:25`), `cli/interactive → rules/history/insights/reporter` (lazy, `interactive.py:246-248`), `web/viewmodels → web/i18n`. Guardrail-скрипты проверяют доки/локали/версию, но не граф импортов — дрейф будет повторяться после каждого рефакторинга.

**A5 (medium). Процессные синглтоны — барьер для server mode (#151/#155).** `grader_core._RUNNER` (`:193`, без лока) — один на процесс: per-request/per-tenant раннер невозможен без смены API; `get_config()` кэширует конфиг, найденный от CWD (`config.py:148-161`) — multi-workspace сервер (#155) не сможет иметь per-workspace конфиг; алиасы `TIMEOUT_SECONDS`/... заморожены при импорте (`grader_core.py:125-130`) и запечены в дефолты сигнатур (`run_single_test`, `:243-244`). Это R2 прошлого аудита — по-прежнему актуален, теперь уже как явный блокер дизайна сервера.

**A6 (medium). `RunSpec` не готов к транспорту.** `path: pathlib.Path` + `cancel_event: threading.Event` (`core/runner.py:62-67`) — несериализуемо и привязано к локальной ФС; генерация wrapper-темпфайла живёт НАД швом (`grader_core.py:294-303`). Контейнерный `SandboxRunner` (#153) и API #156 потребуют spec с содержимым кода/артефактами и cancellation-токеном — сейчас внутренний шов не совпадает со спроектированным внешним контрактом.

**A7 (low). Цикл пакет↔подмодуль в `web/` и фасады на приватных именах.** `web/__init__.py:12-28` импортирует `web.server`, а `server.py:28` — `from stepik_grader.web import runs`: работает только благодаря fallback'у импорт-системы на частично инициализированном пакете. Плюс `web/__init__` и `grader.py:18-56` реэкспортируют десятки приватных имён (`_APP_JS`, `_Handler`, `_case_view`, ~30 в grader.py) «для тестов» — скрытая поверхность совместимости (R5 сохраняется).

**A8 (low). Микро-двойник сразу после дедупликации:** `core/error_glossary.py:62-96` вручную реализует mtime-кеш (`_INDEX_CACHE`) вместо `core/mtime_cache.MtimeCache`, извлечённого в #345 именно против таких копий. Туда же — растущая if/elif-маршрутизация `web/server.py` (`do_GET:171`, `do_POST:328`): каждый новый эндпоинт правит хендлер; таблица маршрутов назрела (server-side аналог R6; сам `app.js` вырос 2358 → 2468 строк).

### Рекомендации (порядок важен)

1. **Закрыть A1 немедленно** (микрорелиз): либо провести `run_microbench` через `_RUNNER`, либо `parser.error` на `--sandbox --mode 4` + строка в SECURITY.md — по прецеденту #351.
2. Списать `executor.py` (A3): перевести `tests/test_executor.py` на `LocalRunner`, удалить реэкспорт, вычистить architecture.md.
3. Добавить AST-guard графа импортов в CI (расширить семейство `scripts/check_*_guardrails.py`) и синхронизировать architecture.md (A4) — дёшево, навсегда снимает класс дрейфа.
4. Перед стартом реализации #151: спроектировать `RunSpec v2` (payload вместо path, токен отмены) и единую фабрику раннера, покрывающую playground/microbench (A2/A5/A6) — иначе SandboxRunner-контейнеры (#153) упрутся в шов, который придётся ломать под нагрузкой.
5. Держать «правило mtime_cache»: новые кеши — только через `core/mtime_cache` (A8).

**Оценка: 8/10.** Дисциплина DAG, консолидация трёх из четырёх «двойников» v1.7.0 и честное закрытие R1/R3 — уровень зрелой библиотеки. Балл снимают: обход Runner-шва с реальной дырой в гарантии `--sandbox` (A1) и процессные синглтоны, которые проектная документация server mode пока не признаёт блокером.
