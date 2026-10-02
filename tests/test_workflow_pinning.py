"""Guard: все `uses:` в workflow запинены по commit SHA (issue #808).

Плавающий тег (`@v1`, `@v4`) или ветка (`@release/v1`) означает, что следующий
запуск CI выполнит любой код, который окажется по этому ref в момент старта —
включая job'ы с `id-token: write` и правом коммитить в `main`. Это классический
supply-chain-вектор (случай tj-actions/changed-files), и защита от него —
единственная: полный SHA.

Проверка текстовая (stdlib-only, без PyYAML), как остальные guardrail-тесты
проекта. Обновление SHA — работа Dependabot (`.github/dependabot.yml`), поэтому
тест не фиксирует конкретные версии, только форму ссылки.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

# `uses: owner/repo@ref` — ref до пробела/комментария. Локальные (`./...`) и
# docker-ссылки сюда не попадают: у них нет формы `owner/repo@ref`.
_USES = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@(\S+)")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _workflow_files() -> list[pathlib.Path]:
    return sorted(_WORKFLOWS.glob("*.yml"))


def test_workflows_exist() -> None:
    """Сам guard бесполезен, если каталог переехал и матчить стало нечего."""
    assert _workflow_files(), f"нет ни одного workflow в {_WORKFLOWS}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit_sha(path: pathlib.Path) -> None:
    """Каждое использование стороннего action ссылается на полный commit SHA."""
    floating = [
        f"{action}@{ref}"
        for action, ref in _USES.findall(path.read_text(encoding="utf-8"))
        if not _SHA.match(ref)
    ]
    assert not floating, f"{path.name}: не запинено по SHA — {floating}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_pinned_sha_carries_a_readable_version(path: pathlib.Path) -> None:
    """Рядом с SHA стоит комментарий с версией — иначе строку невозможно читать."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not _USES.search(line):
            continue
        assert "#" in line.split("@", 1)[1], (
            f"{path.name}: SHA без комментария-версии: {line.strip()}"
        )


def test_dependabot_watches_github_actions() -> None:
    """Пиннинг без автообновления протухает: Dependabot должен следить за actions."""
    dependabot = _ROOT / ".github" / "dependabot.yml"
    assert dependabot.exists(), "нет .github/dependabot.yml — запиненные SHA застынут"
    assert 'package-ecosystem: "github-actions"' in dependabot.read_text(encoding="utf-8")
