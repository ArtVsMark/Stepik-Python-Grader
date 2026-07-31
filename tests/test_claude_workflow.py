"""Guard для `.github/workflows/claude.yml` — кто может запускать агента (issue #807).

Workflow отдаёт шагу OAuth-токен владельца, а триггеры `issues`/`issue_comment`
доступны любому аккаунту GitHub. Пока условием было одно лишь упоминание
«@claude», посторонний мог запускать оплачиваемого агента, а текст его issue
уходил в промпт без ограничения инструментов.

Проверка текстовая (stdlib-only, без PyYAML), как остальные guardrail-тесты
проекта: `tests/test_release_workflow.py` устроен так же.
"""

from __future__ import annotations

import pathlib

_CLAUDE = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "claude.yml"

# Роли, которые GitHub проставляет сам по фактическим правам в репозитории;
# содержимым issue их не подделать.
_TRUSTED = '["OWNER", "MEMBER", "COLLABORATOR"]'


def _text() -> str:
    return _CLAUDE.read_text(encoding="utf-8")


def _noncomment_text() -> str:
    """Файл без YAML-комментариев: guard считает реальные условия, а не прозу."""
    return "\n".join(line for line in _text().splitlines() if not line.lstrip().startswith("#"))


def test_every_trigger_is_gated_by_author_association() -> None:
    """Каждая из четырёх веток условия требует доверенного автора, а не только «@claude».

    Считаем по числу проверок: упоминаний `@claude` в условии четыре (issue_comment,
    pull_request_review_comment, pull_request_review, issues — у последней две
    проверки текста, тело и заголовок), и на каждую ветку приходится своя
    проверка author_association.
    """
    text = _noncomment_text()
    assert text.count(f"contains(fromJSON('{_TRUSTED}')") == 4


def test_association_checked_on_the_right_event_field() -> None:
    """Ассоциация берётся из объекта своего события, а не из чужого."""
    text = _noncomment_text()
    for field in (
        "github.event.comment.author_association",
        "github.event.review.author_association",
        "github.event.issue.author_association",
    ):
        assert field in text, field


def test_agent_tools_are_restricted() -> None:
    """Инструменты агента ограничены явным списком — без него доступен любой Bash."""
    text = _noncomment_text()
    assert "--allowed-tools" in text
    # Ничего пишущего в репозиторий и ничего сетевого в списке быть не должно.
    allowed_line = next(line for line in text.splitlines() if "--allowed-tools" in line)
    for forbidden in ("Write", "Edit", "WebFetch", "gh pr create", "gh pr merge", "git push"):
        assert forbidden not in allowed_line, forbidden


def test_workflow_stays_read_only() -> None:
    """Права job'а остаются на чтение: запись в репозиторий агенту недоступна."""
    text = _noncomment_text()
    assert "contents: read" in text
    assert "contents: write" not in text
