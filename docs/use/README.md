# Как пользоваться грейдером

Всё, что нужно, чтобы поставить инструмент и проверять свои решения. Код
грейдера здесь не разбирается — это в [`../dev/`](../dev/README.md).

| Хочу… | Документ |
|---|---|
| Установить (pipx или из исходников), настроить OAuth к Stepik, продиагностировать окружение | [installation.md](installation.md) |
| Installation guide in English | [installation.en.md](installation.en.md) |
| Working with the grader in English | [grader-workflow.en.md](grader-workflow.en.md) |
| Запустить проверку: режимы 1–4, интерактивное меню, CLI-флаги, скачивание задачи со Stepik, интеграция с VS Code / PyCharm | [grader-workflow.md](grader-workflow.md) |
| Открыть веб-интерфейс: `stepik-grader --serve` или окно-лаунчер `stepik-grader-gui` | [grader-workflow.md § Веб-интерфейс](grader-workflow.md#веб-интерфейс---serve) |
| Разобраться в разделах веб-интерфейса: «Проверка решений», «Загрузчик задач», «Глоссарий», «Правила (PEP)», «Подучить», «Песочница», раскладка окна, клавиатура и тёмная тема | [web-interface.md](web-interface.md) |
| Настроить под себя: `[tool.stepik-grader]`, таймауты, три формата тест-кейсов, вердикты, ограничения и безопасность | [configuration.md](configuration.md) |
| Поменять настройки прогона без правки файлов — вкладка «Дополнительно» в окне лаунчера | [configuration.md § Настройки прогона из окна лаунчера](configuration.md#настройки-прогона-из-окна-лаунчера) |
| Понять, чем этот форк отличается от первоисточника, и как менялись релизы | [versions.md](versions.md) |

**Первый раз здесь?** Ставь через `pipx install stepik-python-grader`, дальше —
[«Первый пример за 2 минуты»](grader-workflow.md#первый-пример-за-2-минуты): он
проходится без Stepik и без OAuth, на одном файле решения и одной паре тестовых
файлов.

**Что важно знать про безопасность.** По умолчанию решения запускаются **без**
OS-изоляции: есть таймаут и best-effort лимит памяти на POSIX, но не изоляция
файловой системы и сети. Изоляция включается флагом `--sandbox`. Без него
запускай только доверенный код — свой или скачанный со Stepik как есть. Разбор —
[configuration.md § Ограничения и безопасность](configuration.md#ограничения-и-безопасность).

---

Не нашлось ответа? Вопросы и идеи —
[Discussions](https://github.com/ArtVsMark/Stepik-Python-Grader/discussions),
баг-репорты — [Issues](https://github.com/ArtVsMark/Stepik-Python-Grader/issues).
