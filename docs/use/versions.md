# Версии и эволюция проекта

> Актуальный статус — в
> [README](../../README.md); полный список изменений — в
> [`CHANGELOG.md`](../../CHANGELOG.md); схема версионирования — в
> [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Что изменилось по сравнению с оригиналом

Этот форк существенно расширяет [оригинальный проект PavloOps/python_generation_grader](https://github.com/PavloOps/python_generation_grader):

| Возможность | Оригинал | Этот форк |
|---|---|---|
| Проверка одного файла на корректность | ✅ | ✅ |
| Сравнение и бенчмарк решений — subprocess-бенчмарк (режим 3), timeit-микробенч (режим 4), оценка по median, вердикты SIMILAR/SLOWER/MUCH_SLOWER, профили нагрузки | ❌ | ✅ |
| Интеграция со Stepik — OAuth2, автоскачивание задачи и тест-кейсов (HTML-таблица/ZIP/GitHub-ссылки), function-style тесты, диагностика API | ❌ | ✅ |
| Локальный веб-интерфейс (`--serve`) и интеграция с IDE (VS Code, PyCharm) | ❌ | ✅ |
| AI-объяснение падений (`--ai-hints`, opt-in, свой ключ; заземление на карточки глоссария, без согласия ничего не уходит в сеть) | ❌ | ✅ |
| Локальный глоссарий Python — карточки функций/исключений/конструкций, детектор недостающих терминов, deep-link из error cards, двуязычные описания | ❌ | ✅ |
| Правила PEP 8 + раздел «Подучить» — частые ошибки из истории прогонов с затуханием карточек (`--insights`/`--lint`) | ❌ | ✅ |
| Опциональная OS-песочница исполнения (`--sandbox`: bwrap / Job Objects / sandbox-exec) с ФС-изоляцией и — на Linux/macOS — сетевой (гарантии различаются по ОС: на Windows сетевой изоляции нет, см. [SECURITY.md](../../SECURITY.md)) | ❌ | ✅ |
| Двуязычный интерфейс RU/EN — CLI-сообщения, web-оболочка, глоссарий (`--lang`, `?lang=en`) | ❌ | ✅ |
| Локальная история прогонов (SQLite) + статистика — раздел «Подучить», всё офлайн | ❌ | ✅ |
| Инженерная база — src-layout пакет, `pyproject.toml`, pre-commit (ruff), CI (pytest + ruff + mypy) на 3 ОС, автотесты на каждый PR | ❌ | ✅ |

Подробности по каждому пункту — в [`CHANGELOG.md`](../../CHANGELOG.md) и [`archive/history.md`](../archive/history.md).

## Эволюция версий

Таблица ниже — про **фундаментальные** сдвиги между релизами, а не про
отдельные фичи (полный список изменений — в [`CHANGELOG.md`](../../CHANGELOG.md)).
Каждая версия — это качественный скачок в отдельной плоскости.

Версии идут **строками**, плоскости — колонками: новый релиз дописывается снизу
и ширина таблицы не растёт. Обратная раскладка (версия = колонка) уезжала вправо
на каждом MINOR, и v1.0.0 с текущим релизом было уже не увидеть одновременно.
`↑` — плоскость не менялась с предыдущей версии.

| Версия | Суть релиза | Структура кода | Запуск | CLI | CI | Безопасность | Дистрибуция | Версионирование | Тестов / покрытие |
|---|---|---|---|---|---|---|---|---|---|
| **v1.0.0** | Первый стабильный форк — «работает» | Плоский корень репозитория | `python grader.py` | Только интерактивное меню | Ubuntu (pytest + ruff) | Только таймаут выполнения | `git clone` + `requirements.txt` | статичная строка | 260 / 59% |
| **v1.1.0** | Зрелая архитектура, установка как пакет | src-layout: `src/stepik_grader/` + пакет `core/` | `stepik-grader` / `python -m stepik_grader.X` | + argparse (`--mode/--file/--dir`) | Ubuntu | ↑ | `pip install -e .` (единый источник — `pyproject.toml`) | `importlib.metadata` (единый источник) | 523 / 95% |
| **v1.2.0** | Безопасность, кроссплатформа, дистрибуция, UX | стабилизирована | `python -m stepik_grader` | + `--output json/csv/md`, `--watch`, `--lang`, `--verbose/--quiet` | Ubuntu + Windows + macOS, + mypy | + лимит памяти `RLIMIT_AS` (POSIX), явные импорты вместо wildcard | GitHub Releases (sdist+wheel), `pipx` из git | задокументированная схема + `scripts/version.py` | 591 / 96% |
| **v1.3.0** | Онбординг новичков + дистрибуция через PyPI | ↑ | + нативный файловый диалог (fallback без пути) | ↑ | ↑ | ↑ | + PyPI: `pipx install stepik-python-grader` (OIDC trusted publishing) | ↑ | 599 / 95% |
| **v1.4.0** | «Оболочки» — веб-интерфейс и интеграция с IDE | + `web.py`, `ide.py` | + `--serve` (Web UI), `--init-vscode` (VS Code), рецепт External Tool для PyCharm | + веб-интерфейс, задачи VS Code | ↑ | ↑ | ↑ | ↑ | 622 / 95% |
| **v1.5.0** | Рабочий поток — кэш, pytest-плагин, инкрементальный watch | + `pytest_plugin.py`, `core/cache.py` | + `pytest --grader-mode`; IDE-задачи через интерпретатор | + `--cache/--no-cache/--clear-cache`, инкрементальный `--watch` | ↑ | + `prlimit` после spawn (потокобезопасно) | ↑ | ↑ | 660 / 95% |
| **v1.6.0** | Глоссарий против stdlib + прозрачность версии | + `glossary/stdlib_inventory.py`, `glossary/coverage.py` | + `python -m stepik_grader.glossary.coverage` | ↑ | + живые README-бейджи (coverage/version, авто-коммит) | ↑ | ↑ | + `--version` отличает dev-сборку от релиза | 784 / 95% |
| **v1.7.0** | WEB workspace + `--sandbox` + security-аудит | + `core/sandbox/`, `web/runs.py`, `web/i18n.py`, `core/stats.py`; путь-API — `Path`, не `str` | ↑ | + `--sandbox`, `--stats/--stats-summary` | + честное cross-OS combined покрытие (`coverage-combine`), два бейджа с разными подписями | + ОС-sandbox (`--sandbox`), security-аудит (OAuth/CSRF/DoS/секреты), path-confinement и Host/Origin guard в `--serve` | ↑ | ↑ | 1179 / 93% (cross-OS combined) |
| **v1.8.0** | Гигиена по аудиту 2026-07 — консолидация двойников, багфиксы, детерминизм тестов | + единый i18n-каталог `core/locales/*.json`, единый RE-резолвер `core/error_glossary.py` | ↑ | ↑ | ↑ | ↑ | ↑ | + динамическая версия из git-тегов (`setuptools-scm`) | 1317 / 93% (cross-OS combined) |
| **v1.9.0** | Наполнение оболочек — AI-подсказки, полная локализация web-UI, разделы обучения, границы web↔core (ADR-0010/0011), завершение глоссария | + `core/ai_hints`/`ai_grounding`, `core/history`/`lint`/`insights`, `rules/`, фасад `web/grading`, общие `core/db.py` + `atomic_io.py` | ↑ | + `--ai-hints`, `--history`, `--insights`/`--lint`, `--import-reference`, `--export-progress` | + `check_ui_locale_guardrails` (i18n-паритет ru↔en), реальный `bwrap`-sandbox job | + AI-consent-gate (приватность кода/ввода перед отправкой провайдеру), back-pressure `POST /api/v1/runs` | ↑ | ↑ | 1600+ / 93% (cross-OS combined) |
| **v1.10.0** | Обратная связь из продукта, глоссарий по официальному Python, периметр безопасности, подготовка к серверному пивоту | + `core/feedback.py`, `web/feedback_adapter.py`; `docs/` разложена по четырём направлениям | + окно-лаунчер `stepik-grader-gui` без командной строки | + пункт меню «Сообщить о проблеме / предложить идею» | + пять docs-гейтов (направления, индексы, бюджеты README/CHANGELOG, запрет журнала работ), информационный `supply-chain` с `pip-audit` | + fuzz входного тракта, escape-PoC трёх sandbox-backend'ов, лимит вывода на всех путях исполнения, consent AI в CLI, атомарная запись `secrets.json`, чистка секретов из env дочернего процесса | ↑ | ↑ | живые бейджи README |

> **Почему в последней строке нет цифр.** Числа тестов и покрытия
> фиксируются в этой таблице только для **закрытых** релизов — когда MINOR уже
> не последний и его значения перестали меняться. Для текущего релиза живой
> источник один: бейджи `Coverage (ubuntu)` / `Coverage (all OS)` в
> [README](../../README.md). Вписанная руками цифра здесь неизбежно устаревала к
> следующему PR и начинала противоречить README. При постановке тега
> `vX.(Y+1).0` подставь в строку уходящего релиза его последние значения из
> бейджей, а новый релиз добавь строкой ниже.

> **MAJOR остаётся `1`** на всём протяжении: все изменения укладываются в рамки
> «локальный инструмент для Python-задач Stepik». Смена MAJOR (`2.0`)
> предполагается только при фундаментальном выходе за эти рамки — другие языки
> программирования или платформы. Подробнее — в разделе «Версионирование»
> [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
