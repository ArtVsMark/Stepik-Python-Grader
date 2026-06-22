"""conftest.py — корневой конфиг pytest.

Обеспечивает корректный импорт модулей проекта (at_first, executor,
microbench_runner) без ручных sys.path манипуляций в тестах.
pytest автоматически добавляет директорию conftest.py в sys.path.
"""
