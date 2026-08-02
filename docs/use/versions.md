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

| | **v1.0.0** | **v1.1.0** | **v1.2.0** | **v1.3.0** | **v1.4.0** | **v1.5.0** | **v1.6.0** | **v1.7.0** | **v1.8.0** | **v1.9.0** | **v1.10.0** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Суть релиза** | Первый стабильный форк — «работает» | Зрелая архитектура, установка как пакет | Безопасность, кроссплатформа, дистрибуция, UX | Онбординг новичков + дистрибуция через PyPI | «Оболочки» — веб-интерфейс и интеграция с IDE | Рабочий поток — кэш, pytest-плагин, инкрементальный watch | Глоссарий против stdlib + прозрачность версии | WEB workspace + `--sandbox` + security-аудит | Гигиена по аудиту 2026-07 — консолидация двойников, багфиксы, детерминизм тестов | Наполнение оболочек — AI-подсказки, полная локализация web-UI, разделы обучения, границы web↔core (ADR-0010/0011), завершение глоссария | Обратная связь из продукта, глоссарий по официальному Python, периметр безопасности, подготовка к серверному пивоту |
| **Структура кода** | Плоский корень репозитория | src-layout: `src/stepik_grader/` + пакет `core/` | стабилизирована | → | + `web.py`, `ide.py` | + `pytest_plugin.py`, `core/cache.py` | + `glossary/stdlib_inventory.py`, `glossary/coverage.py` | + `core/sandbox/`, `web/runs.py`, `web/i18n.py`, `core/stats.py`; путь-API — `Path`, не `str` | + единый i18n-каталог `core/locales/*.json`, единый RE-резолвер `core/error_glossary.py` | + `core/ai_hints`/`ai_grounding`, `core/history`/`lint`/`insights`, `rules/`, фасад `web/grading`, общие `core/db.py` + `atomic_io.py` | + `core/feedback.py`, `web/feedback_adapter.py`; `docs/` разложена по четырём направлениям |
| **Запуск** | `python grader.py` | `stepik-grader` / `python -m stepik_grader.X` | `python -m stepik_grader` | + нативный файловый диалог (fallback без пути) | + `--serve` (Web UI), `--init-vscode` (VS Code), рецепт External Tool для PyCharm | + `pytest --grader-mode`; IDE-задачи через интерпретатор | + `python -m stepik_grader.glossary.coverage` | → | → | → | + окно-лаунчер `stepik-grader-gui` без командной строки |
| **CLI** | Только интерактивное меню | + argparse (`--mode/--file/--dir`) | + `--output json/csv/md`, `--watch`, `--lang`, `--verbose/--quiet` | → | + веб-интерфейс, задачи VS Code | + `--cache/--no-cache/--clear-cache`, инкрементальный `--watch` | → | + `--sandbox`, `--stats/--stats-summary` | → | + `--ai-hints`, `--history`, `--insights`/`--lint`, `--import-reference`, `--export-progress` | + пункт меню «Сообщить о проблеме / предложить идею» |
| **CI** | Ubuntu (pytest + ruff) | Ubuntu | Ubuntu + Windows + macOS, + mypy | → | → | → | + живые README-бейджи (coverage/version, авто-коммит) | + честное cross-OS combined покрытие (`coverage-combine`), два бейджа с разными подписями | → | + `check_ui_locale_guardrails` (i18n-паритет ru↔en), реальный `bwrap`-sandbox job | + пять docs-гейтов (направления, индексы, бюджеты README/CHANGELOG, запрет журнала работ), информационный `supply-chain` с `pip-audit` |
| **Безопасность** | Только таймаут выполнения | Только таймаут | + лимит памяти `RLIMIT_AS` (POSIX), явные импорты вместо wildcard | → | → | + `prlimit` после spawn (потокобезопасно) | → | + ОС-sandbox (`--sandbox`), security-аудит (OAuth/CSRF/DoS/секреты), path-confinement и Host/Origin guard в `--serve` | → | + AI-consent-gate (приватность кода/ввода перед отправкой провайдеру), back-pressure `POST /api/v1/runs` | + fuzz входного тракта, escape-PoC трёх sandbox-backend'ов, лимит вывода на всех путях исполнения, consent AI в CLI, атомарная запись `secrets.json`, чистка секретов из env дочернего процесса |
| **Дистрибуция** | `git clone` + `requirements.txt` | `pip install -e .` (единый источник — `pyproject.toml`) | GitHub Releases (sdist+wheel), `pipx` из git | + PyPI: `pipx install stepik-python-grader` (OIDC trusted publishing) | → | → | → | → | → | → | → |
| **Версионирование** | статичная строка | `importlib.metadata` (единый источник) | задокументированная схема + `scripts/version.py` | → | → | → | + `--version` отличает dev-сборку от релиза | → | + динамическая версия из git-тегов (`setuptools-scm`) | → | → |
| **Тестов / покрытие** | 260 / 59% | 523 / 95% | 591 / 96% | 599 / 95% | 622 / 95% | 660 / 95% | 784 / 95% | 1179 / 93% (cross-OS combined) | 1317 / 93% (cross-OS combined) | 1600+ / 93% (cross-OS combined) | живые бейджи README |

> **Почему в последней колонке нет цифр.** Числа тестов и покрытия
> фиксируются в этой таблице только для **закрытых** релизов — когда MINOR уже
> не последний и его значения перестали меняться. Для текущего релиза живой
> источник один: бейджи `Coverage (ubuntu)` / `Coverage (all OS)` в
> [README](../../README.md). Вписанная руками цифра здесь неизбежно устаревала к
> следующему PR и начинала противоречить README. При постановке тега
> `vX.(Y+1).0` подставь в колонку уходящего релиза его последние значения из
> бейджей.

> **MAJOR остаётся `1`** на всём протяжении: все изменения укладываются в рамки
> «локальный инструмент для Python-задач Stepik». Смена MAJOR (`2.0`)
> предполагается только при фундаментальном выходе за эти рамки — другие языки
> программирования или платформы. Подробнее — в разделе «Версионирование»
> [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
