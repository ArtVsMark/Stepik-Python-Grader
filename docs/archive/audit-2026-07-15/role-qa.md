## 🧪 Тестировщик

### Методика

Реальный прогон `.venv/bin/pytest tests/ -q` (Linux, Python 3.13): **1420 passed, 22 skipped, 9 warnings, 82 с**, покрытие single-OS **87.55%** (порог 85), cross-OS badge 92.9%. Прочитаны все 76 тест-файлов (~17.7 тыс. строк) + `tests/e2e/` (15 тестов), `ci.yml`, `web/server.py`, `web/runs.py`, `web/playground.py`, `core/runner.py`, `core/sandbox/*`. Помимо чтения — **адверсарные репро-скрипты** против LocalRunner и `--serve` (scratchpad: `repro_stdin_deadlock.py`, `repro_orphan.py`, `repro_cp1251.py`); три бага подтверждены исполнением. Находки прошлого аудита (§ 4 audit-2026-07.md) перепроверены: sleep-поллинг **исправлен** (`tests/_wait.py`, issue #357 — паттерн `wait_until` внедрён в test_web/test_runs/test_web_playground), «e2e вне CI» **уже неверно** — job `e2e` в `ci.yml:249` гоняет Playwright на каждом push/PR (ubuntu + Chromium).

### Сильные стороны

- Дисциплина набора: 1442 теста (в CLAUDE.md заявлено 1317 — набор растёт быстрее доков), матрица 3 ОС × 3.12/3.13, per-OS/combined двойной coverage-гейт, guardrail-скрипты сами покрыты тестами (`test_check_docs_guardrails.py`, `test_check_locale_guardrails.py`, `test_i18n_guardrails.py`).
- Сеть в юнитах полностью замокана (OAuth — фейковый `HTTPServer` без реальных портов, `test_stepik_client_retry.py` — эфемерный порт 0); tmp-гигиена на `tmp_path` повсеместно; фиксированных портов в suite нет.
- Конкурентность хранилищ покрыта честными многопоточными тестами: `test_history.py:125` (WAL, N писателей), `test_stats.py:126`, `test_glossary_module.py:316` — P0 прошлого аудита (локи файловых записей) закрыт и протестирован.
- Интеграционные тесты на реальных данных трёх репозиториев (`test_integration_repos.py`) и cross-runner паритет LocalRunner↔SandboxRunner (`test_sandbox_runner.py:428-443`).
- e2e: 14 user journeys + XSS-регрессия payload'ом в stdout (`tests/e2e/test_xss_regression.py`) — в CI, с кэшем Chromium.

### Находки

**F1. [HIGH, баг подтверждён] Deadlock на записи stdin обходит и timeout, и cancel** — `core/runner.py:330-339` (`_run_with_polling`): `proc.stdin.write(spec.stdin)` выполняется синхронно ДО цикла опроса. Если решение не читает stdin (например, `while True`), а вход > pipe-буфера (~64 KiB Linux), запись блокируется навсегда: репро показал, что при `timeout=2s` поток жив спустя 8 с, и `cancel_event.set()` не помогает. Путь реален из web: `POST /api/v1/runs` (mode=tests с большим input-файлом, playground со stdin до 1 MiB). Воркер `ThreadPoolExecutor` (дефолт `job_workers=2`, `config.py:54`) утекает навсегда — две такие job'ы убивают всю async-подсистему до рестарта сервера, job висит «running», отмена не работает. Синхронный CLI-путь не затронут (`communicate()` дренирует сам). Нужен тест: большой stdin + нечитающее решение + cancel/timeout.

**F2. [HIGH] Функциональные тесты linux-sandbox не выполняются нигде, включая CI** — 22 skip локального прогона = все нативные sandbox-сценарии. Причина skip'а linux-класса: «bubblewrap (bwrap) not installed» (`test_sandbox_runner.py:200`); `ci.yml` bwrap **не ставит**, и в манифесте образа ubuntu-24.04 GitHub Actions его нет (проверено по apt-списку runner-images). Итог: `_linux.py` — **0% даже в combined coverage** (coverage.xml: `_linux.py line-rate="0"`, `_posix_common.py` 0% локально), блокировка записи/сети, fork-bomb, memory/output violation на Linux проверены только вручную автором. Combined-гейт 90 это не ловит (92.9 > 90). Молчаливый skip — анти-паттерн: никакой guard не требует, чтобы на нативной ОС тесты реально выполнились.

**F3. [MEDIUM, баг подтверждён] Не-UTF8 файл роняет соединение вместо error-карточки** — `web/viewmodels.py:312` (`read_source`) ловит только `OSError`; `UnicodeDecodeError` (ValueError) пролетает до `socketserver.handle_error` → traceback в stderr сервера, клиент получает `RemoteDisconnected` без HTTP-ответа. Репро: файл в cp1251 + `GET /api/source` — подтверждено. Тот же паттерн: `server.py:356-358` (`/api/code-terms`, `except OSError`), `viewmodels.py:614-616` (чтение reference в bench). Для аудитории проекта (Windows, кириллица, чужие редакторы) cp1251-файл — обыденность; тестов на не-UTF8 вход в web-слое нет (единственный encoding-тест — cp1251-stdout CLI, `test_cli.py:488`).

**F4. [MEDIUM, баг подтверждён] TLE-kill оставляет внуков-сирот в LocalRunner** — `proc.kill()` (`runner.py:263, 356`) убивает только прямого ребёнка: репро с решением, спавнящим subprocess и засыпающим до TLE, — внук пережил kill и дописал файл-маркер. Решение студента с `multiprocessing`/`subprocess` + TLE оставляет пожирающие CPU процессы после каждого прогона (batch-режим 2 умножает утечку). Sandbox-бэкенды это сдерживают (`test_fork_bomb_contained`), но sandbox opt-in и в web не проброшен (issue #351). Нет ни `start_new_session=True` + `killpg`, ни теста на «процессное дерево убито».

**F5. [MEDIUM] Нет защиты от зависания suite** — `pytest-timeout` отсутствует в dev-extras (`pyproject.toml:26-31`), при этом suite полон реальных subprocess'ов и потоков (`test_runs`, `test_web`, `test_sandbox_runner`). Регрессия класса F1 превращает CI-job не в красный тест, а в 6-часовой hang до таймаута GitHub Actions (×9 job'ов матрицы). `wait_until` (10 с дедлайн) защищает только опросные ассерты, не сами вызовы.

**F6. [LOW] Реестр job'ов без верхней границы** — `web/runs.py:97-127`: TTL-sweep удаляет только терминальные job'ы; лимита на количество активных/queued нет — цикл `POST /api/v1/runs` наращивает `_JOBS` и очередь пула неограниченно (каждая job держит result до 15 мин). Смягчено localhost-only + Host/Origin-guard (#242), но забагованный фронтенд-цикл достаточен. Тестов флуда/лимита нет (`grep 429|limit` по `test_runs.py` — пусто).

**F7. [LOW] app.js (2468 строк) — только 15 e2e-кейсов, один браузер** — клиентская логика (поллинг runs, отмена, localStorage-история, рендер 41 innerHTML-сайта — сам тест признаёт «only guaranteed safe by code review», `test_xss_regression.py:4-6`) без юнит-тестов; XSS-регрессия покрывает один payload-путь из ~41. Прогресс с прошлого аудита есть (e2e в CI), но пропорция кода к тестам худшая в проекте.

**F8. [LOW] Углы без тестов**: KeyboardInterrupt-пути (`web/server.py:719-736` — весь `run_server` не покрыт, `cli/__init__.py:376`); safety-net воркера (`web/runs.py:288-295` — гарантия «job не застрянет running» сама не протестирована); property-based тестов (hypothesis) для `normalizers.py`/парсеров форматов 1-3/float-сравнений по-прежнему нет — перенос из аудита v1.7.0, актуально.

### Рекомендации (конкретные тест-кейсы)

1. К F1: тест `LocalRunner` — `stdin=b"x"*1_000_000`, решение `while True: sleep`, `timeout=2`, ассерт «вернулся ≤ 5 с с timed_out=True»; парный тест на cancel. Фикс — писать stdin в отдельном потоке (как делает `communicate()`).
2. К F2: `apt-get install -y bubblewrap` в ubuntu-job `ci.yml` + guard-строка `pytest tests/test_sandbox_runner.py -q -rs` с проверкой «0 skipped среди linux-класса» (или маркер `--strict-native-sandbox`).
3. К F3: таблица тестов web-слоя «файл в cp1251 / latin-1 / битый UTF-8» для `/api/source`, `/api/code-terms`, bench-reference — ожидание: JSON error-card, не разрыв соединения; фикс — ловить `(OSError, UnicodeDecodeError)` или читать `errors="replace"`.
4. К F4: тест «решение спавнит внука, TLE; после run() дерево пусто» (psutil children); фикс — `start_new_session=True` + `os.killpg`.
5. К F5: `pytest-timeout` в dev-extras, `timeout = 120` per-test в `pyproject.toml` (метод thread).
6. К F6/F8: лимит `_MAX_ACTIVE_JOBS` (429 + message_id), тест флуда; тест safety-net — monkeypatch `grade_path` на raise, ассерт status="error"; smoke `run_server` c KeyboardInterrupt через мок `serve_forever`.
7. Не флейково, но шумно: 9 UserWarning от `runner.py:208/212` в каждом прогоне — добавить `filterwarnings` в pytest-конфиг, чтобы новые предупреждения были заметны.

**Оценка направления: 7/10** — редкая по дисциплине пирамида тестов для side-project (моки сети, конкурентные тесты хранилищ, guardrail'ы, e2e в CI, вылеченный flaky-поллинг), но два подтверждённых runtime-бага, найденных за час адверсарного тестирования, и молчаливо не выполняющийся нигде набор linux-sandbox-тестов показывают, что негативные/нагрузочные сценарии системно недотестированы.
