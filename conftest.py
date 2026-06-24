"""conftest.py — корневой конфиг pytest.

Обеспечивает корректный импорт модулей проекта (downloader, executor,
microbench_runner) без ручных sys.path манипуляций в тестах.
pytest автоматически добавляет директорию conftest.py в sys.path.
"""

# test.py — основной модуль grader'а (не тестовый файл).
# Без этой директивы pytest пытается собрать из него TestCase-датакласс
# и выдаёт PytestCollectionWarning.
collect_ignore = ["test.py"]
