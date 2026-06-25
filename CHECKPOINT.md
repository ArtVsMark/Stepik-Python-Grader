# ✅ Контрольная точка — всё работает

**Дата:** 25 июня 2026, 14:00 MSK  
**Окружение:** Windows, Python 3.14.3, pytest 9.1.1  
**Последний коммит:** `chore: drop Python 3.11 support — requires-python >=3.12, CI matrix updated`

## Результаты тестов

| Показатель | Значение |
|---|---|
| Всего тестов | 355 |
| Прошло | ✅ 355 |
| Упало | ❌ 0 |
| Время | 11.87 сек |

## Покрытие кода (Coverage)

| Файл | Строк | Не покрыто | Покрытие |
|---|---|---|---|
| downloader.py | 405 | 8 | 98% |
| executor.py | 40 | 12 | 70% |
| grader.py | 650 | 218 | 66% |
| microbench_runner.py | 69 | 5 | 93% |
| normalizers.py | 14 | 2 | 86% |
| oauth_flow.py | 25 | 0 | **100%** |
| parsers.py | 20 | 0 | **100%** |
| stepik_client.py | 181 | 3 | 98% |
| storage.py | 18 | 0 | **100%** |
| **TOTAL** | **1422** | **248** | **83%** |

## Тестовые модули

- `test_analyzer.py` — 49 тестов ✅
- `test_downloader.py` — 16 тестов ✅
- `test_downloader_extra.py` — 40 тестов ✅
- `test_executor.py` — 18 тестов ✅
- `test_grader_core.py` — 14 тестов ✅
- `test_grader_extra.py` — 14 тестов ✅
- `test_integration_repos.py` — 11 тестов ✅
- `test_loader.py` — 26 тестов ✅
- `test_menu_modes.py` — 10 тестов ✅
- `test_microbench.py` — 12 тестов ✅
- `test_microbench_grader.py` — 11 тестов ✅
- `test_microbench_runner_module.py` — 8 тестов ✅
- `test_normalizers.py` — 17 тестов ✅
- `test_oauth_flow.py` — 25 тестов ✅
- `test_parsers.py` — 21 тест ✅
- `test_slugify.py` — 7 тестов ✅
- `test_stepik_client.py` — 9 тестов ✅
- `test_stepik_client_extra.py` — 20 тестов ✅
- `test_storage.py` — 14 тестов ✅
- `test_testblock.py` — 13 тестов ✅

## Поддерживаемые версии Python

| Версия | Статус |
|---|---|
| Python 3.12 | ✅ стабильная (CI) |
| Python 3.13 | ✅ стабильная (CI) |
| Python 3.14 | 🧪 experimental (CI, `continue-on-error`) |
| Python 3.11 | ❌ не поддерживается (`delete_on_close` недоступен) |

## Архитектурные изменения

- **fix #12** — `storage.load_json_file` рефакторинг
- **fix #13** — sha256 верификация
- **fix #14** — `delete_on_close=False` в `microbench_runner.py` (требует Python 3.12+)
- **chore** — `requires-python = ">=3.12"`, CI матрица: 3.12 / 3.13 / 3.14-experimental

## Статус

> 🟢 **Проект полностью работоспособен.** Все 355 тестов прошли успешно.  
> Общее покрытие: **83%**. Минимальная версия Python: **3.12**.
