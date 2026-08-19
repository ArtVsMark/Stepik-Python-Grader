@echo off
rem launcher.cmd — запуск окна-лаунчера из клона репозитория (issue #1185).
rem
rem Нужен тем, у кого есть клон: после `pipx install` то же окно открывает
rem команда `stepik-grader-gui` из любого каталога, и этот файл не нужен.
rem
rem Запускает pythonw.exe, а не python.exe: иначе рядом с окном лаунчера висит
rem консоль, ради избавления от которой всё и делалось.
setlocal
set "HERE=%~dp0"

if exist "%HERE%.venv\Scripts\pythonw.exe" (
    start "" "%HERE%.venv\Scripts\pythonw.exe" -m stepik_grader.launcher %*
    goto :eof
)

echo Виртуальное окружение не найдено: %HERE%.venv 1>&2
echo Создайте его и поставьте пакет: 1>&2
echo     python -m venv .venv ^&^& .venv\Scripts\pip install -e . 1>&2
exit /b 1
