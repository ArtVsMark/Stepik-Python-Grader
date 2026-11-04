"""Guard for .github/workflows/release.yml — dist собирается ОДИН раз (issue #559).

Раньше `release` и `pypi-publish` вызывали `python -m build` каждый — двойная
сборка (риск рассинхрона wheel/sdist между двумя запусками). Теперь один job
`build` собирает dist и отдаёт его артефактом обоим потребителям (GitHub Release
+ PyPI) через upload/download-artifact. Проверка текстовая (stdlib-only, без
PyYAML), как остальные guardrail-тесты проекта.
"""

import pathlib

_RELEASE = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return _RELEASE.read_text(encoding="utf-8")


def _noncomment_text() -> str:
    """release.yml без YAML-комментариев: историческая проза может упоминать
    `python -m build`, но guard считает реальные шаги, а не комментарии."""
    return "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("#"))


def test_dist_built_exactly_once() -> None:
    # Ровно одна сборка на весь workflow — иначе снова двойной `python -m build`.
    assert _noncomment_text().count("python -m build") == 1


def test_dist_uploaded_once_and_downloaded_by_both_consumers() -> None:
    text = _noncomment_text()
    # Ссылки — без версии: с issue #808 actions запинены по SHA, и Dependabot
    # меняет его еженедельно. Guard сторожит структуру workflow, а не версии.
    # build-job загружает артефакт...
    assert text.count("actions/upload-artifact@") == 1
    # ...и оба потребителя (github-release + pypi-publish) его скачивают.
    assert text.count("actions/download-artifact@") == 2


def test_publish_jobs_fan_out_from_build() -> None:
    text = _noncomment_text()
    # Оба публикующих job'а зависят от `build` (независимы друг от друга: провал
    # PyPI не мешает GitHub Release и наоборот).
    assert text.count("needs: [build, verify]") == 2
    # Публикация всё ещё присутствует.
    assert "softprops/action-gh-release@" in text
    assert "pypa/gh-action-pypi-publish@" in text


# ---------------------------------------------------------------------------
# issue #809: гейт перед публикацией
#
# Версия в PyPI неперезаписываема: тег, собранный из непроверенного коммита,
# откатить нельзя (только yank). Guard держит сам факт наличия проверок —
# если job `verify` вырежут или отвяжут от публикации, тест упадёт.
# ---------------------------------------------------------------------------


def test_verify_job_runs_the_same_gate_as_ci() -> None:
    """Гейт релиза повторяет набор проверок матрицы CI, а не сокращённый набор."""
    text = _noncomment_text()
    for command in (
        "ruff check .",
        "ruff format --check .",
        "mypy src/stepik_grader scripts",
        "python scripts/check_version_consistency.py",
        "pytest -q",
    ):
        assert command in text, command


def test_tag_outside_main_does_not_publish() -> None:
    """Тег с произвольной ветки останавливается проверкой предка, а не уезжает в PyPI."""
    text = _noncomment_text()
    assert "git merge-base --is-ancestor" in text
    assert "origin/main" in text


def _job(name: str) -> str:
    """Текст одного job'а release.yml: от его заголовка до следующего job'а."""
    lines = _noncomment_text().splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("  ")
            and not lines[i].startswith("   ")
            and lines[i].rstrip().endswith(":")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_verify_writes_nothing_into_the_tree_before_tests() -> None:
    """`verify` не кладёт файлы в дерево репозитория: следом идёт `pytest`.

    Гейт документации внутри набора читает каждый `.md` дерева как поясняющий
    документ. `release-notes.md`, записанный в корень, — журнал работ с
    номерами задач, и выпуск v1.12.0 упал на нём дважды; на PR и на `main`
    этого файла нет, поэтому падение было видно только на теге.
    """
    verify = _job("verify")
    outs = [line.strip() for line in verify.splitlines() if "--out " in line]

    assert outs, "в verify нет извлечения заметок — проверка смотрит не туда"
    assert all("$RUNNER_TEMP/" in line for line in outs), outs
