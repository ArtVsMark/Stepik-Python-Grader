# Версии и эволюция проекта

> Вынесено из README (issue #106 / эпик #102). Актуальный статус — в
> [README](../README.md); полный список изменений — в
> [`CHANGELOG.md`](../CHANGELOG.md); схема версионирования — в
> [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Что изменилось по сравнению с оригиналом

Этот форк существенно расширяет [оригинальный проект PavloOps/python_generation_grader](https://github.com/PavloOps/python_generation_grader):

| Возможность | Оригинал | Этот форк |
|---|---|---|
| Проверка одного файла на корректность | ✅ | ✅ |
| Сравнение и бенчмарк решений — subprocess-бенчмарк (режим 3), timeit-микробенч (режим 4), оценка по median, вердикты SIMILAR/SLOWER/MUCH_SLOWER, профили нагрузки | ❌ | ✅ |
| Интеграция со Stepik — OAuth2, автоскачивание задачи и тест-кейсов (HTML-таблица/ZIP/GitHub-ссылки), function-style тесты, диагностика API | ❌ | ✅ |
| Локальный веб-интерфейс (`--serve`) и интеграция с IDE (VS Code, PyCharm) | ❌ | ✅ |
| Инженерная база — src-layout пакет, `pyproject.toml`, pre-commit (ruff), CI (pytest + ruff + mypy), 1000+ тестов | ❌ | ✅ |

Подробности по каждому пункту — в [`CHANGELOG.md`](../CHANGELOG.md) и [`docs/history.md`](history.md).

## Эволюция версий

Таблица ниже — про **фундаментальные** сдвиги между релизами, а не про
отдельные фичи (полный список изменений — в [`CHANGELOG.md`](../CHANGELOG.md)).
Каждая версия — это качественный скачок в отдельной плоскости.

| | **v1.0.0** | **v1.1.0** | **v1.2.0** | **v1.3.0** | **v1.4.0** | **v1.5.0** | **v1.6.0** | **v1.7.0** |
|---|---|---|---|---|---|---|---|---|
| **Суть релиза** | Первый стабильный форк — «работает» | Зрелая архитектура, установка как пакет | Безопасность, кроссплатформа, дистрибуция, UX | Онбординг новичков + дистрибуция через PyPI | «Оболочки» — веб-интерфейс и интеграция с IDE | Рабочий поток — кэш, pytest-плагин, инкрементальный watch | Глоссарий против stdlib + прозрачность версии | WEB workspace (эпик #123) + `--sandbox` + security-аудит |
| **Структура кода** | Плоский корень репозитория | src-layout: `src/stepik_grader/` + пакет `core/` | стабилизирована | → | + `web.py`, `ide.py` | + `pytest_plugin.py`, `core/cache.py` | + `glossary/stdlib_inventory.py`, `glossary/coverage.py` | + `core/sandbox/`, `web/runs.py`, `web/i18n.py`, `core/stats.py`; путь-API — `Path`, не `str` |
| **Запуск** | `python grader.py` | `stepik-grader` / `python -m stepik_grader.X` | `python -m stepik_grader` | + нативный файловый диалог (fallback без пути) | + `--serve` (Web UI), `--init-vscode` (VS Code), рецепт External Tool для PyCharm | + `pytest --grader-mode`; IDE-задачи через интерпретатор | + `python -m stepik_grader.glossary.coverage` | → |
| **CLI** | Только интерактивное меню | + argparse (`--mode/--file/--dir`) | + `--output json/csv/md`, `--watch`, `--lang`, `--verbose/--quiet` | → | + веб-интерфейс, задачи VS Code | + `--cache/--no-cache/--clear-cache`, инкрементальный `--watch` | → | + `--sandbox`, `--stats/--stats-summary` |
| **CI** | Ubuntu (pytest + ruff) | Ubuntu | Ubuntu + Windows + macOS, + mypy | → | → | → | + живые README-бейджи (coverage/version, авто-коммит) | + честное cross-OS combined покрытие (`coverage-combine`), два бейджа с разными подписями |
| **Безопасность** | Только таймаут выполнения | Только таймаут | + лимит памяти `RLIMIT_AS` (POSIX), явные импорты вместо wildcard | → | → | + `prlimit` после spawn (потокобезопасно) | → | + ОС-sandbox (`--sandbox`), security-аудит (OAuth/CSRF/DoS/секреты), path-confinement и Host/Origin guard в `--serve` |
| **Дистрибуция** | `git clone` + `requirements.txt` | `pip install -e .` (единый источник — `pyproject.toml`) | GitHub Releases (sdist+wheel), `pipx` из git | + PyPI: `pipx install stepik-python-grader` (OIDC trusted publishing) | → | → | → | → |
| **Версионирование** | статичная строка | `importlib.metadata` (единый источник) | задокументированная схема + `scripts/version.py` | → | → | → | + `--version` отличает dev-сборку от релиза | → |
| **Тестов / покрытие** | 260 / 59% | 523 / 95% | 591 / 96% | 599 / 95% | 622 / 95% | 660 / 95% | 784 / 95% | 1179 / 93% (cross-OS combined) |

> **MAJOR остаётся `1`** на всём протяжении: все изменения укладываются в рамки
> «локальный инструмент для Python-задач Stepik». Смена MAJOR (`2.0`)
> предполагается только при фундаментальном выходе за эти рамки — другие языки
> программирования или платформы. Подробнее — в разделе «Версионирование»
> [`CONTRIBUTING.md`](../CONTRIBUTING.md).
