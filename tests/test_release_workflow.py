"""Guard for .github/workflows/release.yml — dist собирается ОДИН раз (issue #559).

Раньше `release` и `pypi-publish` вызывали `python -m build` каждый — двойная
сборка (риск рассинхрона wheel/sdist между двумя запусками). Теперь один job
`build` собирает dist и отдаёт его артефактом обоим потребителям (GitHub Release
+ PyPI) через upload/download-artifact. Проверка текстовая (stdlib-only, без
PyYAML), как остальные guardrail-тесты проекта.
"""

from __future__ import annotations

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
    # build-job загружает артефакт...
    assert text.count("actions/upload-artifact@v4") == 1
    # ...и оба потребителя (github-release + pypi-publish) его скачивают.
    assert text.count("actions/download-artifact@v4") == 2


def test_publish_jobs_fan_out_from_build() -> None:
    text = _noncomment_text()
    # Оба публикующих job'а зависят от `build` (независимы друг от друга: провал
    # PyPI не мешает GitHub Release и наоборот).
    assert text.count("needs: [build, verify]") == 2
    # Публикация всё ещё присутствует.
    assert "softprops/action-gh-release@v2" in text
    assert "pypa/gh-action-pypi-publish@release/v1" in text


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
