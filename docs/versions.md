# Версии и эволюция проекта

> Вынесено из README (issue #106 / эпик #102). Актуальный статус — в
> [README](../README.md); полный список изменений — в
> [`CHANGELOG.md`](../CHANGELOG.md); схема версионирования — в
> [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Что изменилось по сравнению с оригиналом

Этот форк существенно расширяет [оригинальный проект PavloOps/python_generation_grader](https://github.com/PavloOps/python_generation_grader):

| Возможность | Оригинал | Этот форк |
|---|---|---|
| Проверка одного файла | ✅ | ✅ |
| Сравнение нескольких решений | ❌ | ✅ |
| Subprocess-benchmark | ❌ | ✅ режим 3 |
| Timeit-microbench | ❌ | ✅ режим 4 |
| Разделение корректности и benchmark | ❌ | ✅ |
| Профили нагрузки | ❌ | ✅ low/medium/high/custom |
| Оценка по median (не одиночный замер) | ❌ | ✅ |
| Вердикт SIMILAR / SLOWER / MUCH SLOWER | ❌ | ✅ |
| OAuth2 + скачивание данных задачи с API | ❌ | ✅ |
| Автоизвлечение тест-кейсов из HTML-таблицы | ❌ | ✅ Sprint 4 |
| Автоскачивание тестов из ZIP-архива | ❌ | ✅ Sprint 4 |
| Обнаружение ссылок на GitHub-тесты | ❌ | ✅ Sprint 4 |
| Поддержка function-style тестов (`*.type`) | ❌ | ✅ Sprint 4 |
| Схема файлов task{N}_1.py / task{N}_2.py | ❌ | ✅ Sprint 5 |
| Диагностика API | ❌ | ✅ |
| Поддержка function-only решений | ❌ | ✅ |
| Локальный веб-интерфейс (`--serve`) | ❌ | ✅ |
| Интеграция с IDE (VS Code `--init-vscode`, PyCharm — External Tool) | ❌ | ✅ |
| Выделенный HTTP/OAuth слой (`stepik_client.py`) | ❌ | ✅ Sprint 3 |
| Утилиты хранилища без project-зависимостей (`storage.py`) | ❌ | ✅ Sprint 3 |
| pyproject.toml (ruff, pytest, зависимости) | ❌ | ✅ |
| Pre-commit хуки (ruff check + ruff format) | ❌ | ✅ |
| Unit-тесты (660+ тестов) | ❌ | ✅ |
| OAuth2-фасад (`oauth_flow.py`) | ❌ | ✅ |
| GitHub Actions CI (pytest + ruff + mypy) | ❌ | ✅ |

## Эволюция версий

Таблица ниже — про **фундаментальные** сдвиги между релизами, а не про
отдельные фичи (полный список изменений — в [`CHANGELOG.md`](../CHANGELOG.md)).
Каждая версия — это качественный скачок в отдельной плоскости.

| | **v1.0.0** | **v1.1.0** | **v1.2.0** | **v1.3.0** | **v1.4.0** | **v1.5.0** | **v1.6.0** |
|---|---|---|---|---|---|---|---|
| **Суть релиза** | Первый стабильный форк — «работает» | Зрелая архитектура, установка как пакет | Безопасность, кроссплатформа, дистрибуция, UX | Онбординг новичков + дистрибуция через PyPI | «Оболочки» — веб-интерфейс и интеграция с IDE | Рабочий поток — кэш, pytest-плагин, инкрементальный watch | Глоссарий против stdlib + прозрачность версии |
| **Структура кода** | Плоский корень репозитория | src-layout: `src/stepik_grader/` + пакет `core/` | стабилизирована | → | + `web.py`, `ide.py` | + `pytest_plugin.py`, `core/cache.py` | + `glossary/stdlib_inventory.py`, `glossary/coverage.py` |
| **Запуск** | `python grader.py` | `stepik-grader` / `python -m stepik_grader.X` | `python -m stepik_grader` | + нативный файловый диалог (fallback без пути) | + `--serve` (Web UI), `--init-vscode` (VS Code), рецепт External Tool для PyCharm | + `pytest --grader-mode`; IDE-задачи через интерпретатор | + `python -m stepik_grader.glossary.coverage` |
| **CLI** | Только интерактивное меню | + argparse (`--mode/--file/--dir`) | + `--output json/csv/md`, `--watch`, `--lang`, `--verbose/--quiet` | → | + веб-интерфейс, задачи VS Code | + `--cache/--no-cache/--clear-cache`, инкрементальный `--watch` | → |
| **CI** | Ubuntu (pytest + ruff) | Ubuntu | Ubuntu + Windows + macOS, + mypy | → | → | → | + живые README-бейджи (coverage/version, авто-коммит) |
| **Безопасность** | Только таймаут выполнения | Только таймаут | + лимит памяти `RLIMIT_AS` (POSIX), явные импорты вместо wildcard | → | → | + `prlimit` после spawn (потокобезопасно) | → |
| **Дистрибуция** | `git clone` + `requirements.txt` | `pip install -e .` (единый источник — `pyproject.toml`) | GitHub Releases (sdist+wheel), `pipx` из git | + PyPI: `pipx install stepik-python-grader` (OIDC trusted publishing) | → | → | → |
| **Версионирование** | статичная строка | `importlib.metadata` (единый источник) | задокументированная схема + `scripts/version.py` | → | → | → | + `--version` отличает dev-сборку от релиза |
| **Тестов / покрытие** | 260 / 59% | 523 / 95% | 591 / 96% | 599 / 95% | 622 / 95% | 660 / 95% | 784 / 95% |

> **MAJOR остаётся `1`** на всём протяжении: все изменения укладываются в рамки
> «локальный инструмент для Python-задач Stepik». Смена MAJOR (`2.0`)
> предполагается только при фундаментальном выходе за эти рамки — другие языки
> программирования или платформы. Подробнее — в разделе «Версионирование»
> [`CONTRIBUTING.md`](../CONTRIBUTING.md).
