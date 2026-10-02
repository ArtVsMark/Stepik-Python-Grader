"""Guard двух workflow'ов Claude — кто может запускать агента (issue #807, #1272).

Workflow отдаёт шагу OAuth-токен владельца, а триггеры `issues`/`issue_comment`
доступны любому аккаунту GitHub. Пока условием было одно лишь упоминание
«@claude», посторонний мог запускать оплачиваемого агента, а текст его issue
уходил в промпт без ограничения инструментов.

Второй файл — `claude-code-review.yml`, авто-ревью на PR. Там вопрос обратный:
не «кого не пустить», а «кого пустить». Ревью отказывается работать для событий
от бота, а обновление ветки из `main` обязательно и приходит именно от него —
поэтому в workflow разрешён один известный бот, и гейт ниже держит этот список
именным: `*` пустил бы в ревью любое приложение с доступом к репозиторию.

Проверка текстовая (stdlib-only, без PyYAML), как остальные guardrail-тесты
проекта: `tests/test_release_workflow.py` устроен так же.
"""

from __future__ import annotations

import pathlib

_WORKFLOWS = pathlib.Path(__file__).parent.parent / ".github" / "workflows"
_CLAUDE = _WORKFLOWS / "claude.yml"
_REVIEW = _WORKFLOWS / "claude-code-review.yml"

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


def _review_noncomment_text() -> str:
    """`claude-code-review.yml` без комментариев — по той же причине, что выше."""
    lines = _REVIEW.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_review_allows_exactly_one_named_bot() -> None:
    """Бот разрешён поимённо, а не «все» (issue #1272).

    Строка нужна: событие `synchronize` шлёт тот, кто толкнул в ветку, а
    обновление ветки из `main` обязательно («Require branches to be up to
    date»), поэтому без неё ревью отказывалось работать на каждом PR и
    проверяло состояние ДО обновления.

    Но `*` пустил бы в ревью любое приложение с доступом к репозиторию, а
    удаление строки вернуло бы красную проверку на каждый PR — гейт держит
    ровно середину: список есть и в нём один известный бот.
    """
    text = _review_noncomment_text()
    allowed = [line for line in text.splitlines() if "allowed_bots:" in line]

    assert len(allowed) == 1, "ожидается ровно одна строка allowed_bots"
    value = allowed[0].split(":", 1)[1].strip().strip("'\"")
    assert value == "claude", f"разрешён не тот набор ботов: {value!r}"


def test_review_skips_forked_pull_requests() -> None:
    """Форковый PR по-прежнему пропускается — периметр не расширен.

    Это второй крепёж того же места: разрешив бота, легко не заметить, что
    вместе с ним открылась дверь коду постороннего автора. Условие `if`
    остаётся единственным, что эту дверь держит.
    """
    text = _review_noncomment_text()

    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    assert "pull_request_target" not in text, "триггер обязан остаться pull_request"


def test_review_workflow_stays_read_only() -> None:
    """Права ревью — на чтение: разрешение бота не должно тянуть за собой запись."""
    text = _review_noncomment_text()

    assert "contents: read" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
