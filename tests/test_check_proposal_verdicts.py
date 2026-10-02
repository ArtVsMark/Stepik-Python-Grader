"""Тесты scripts/check_proposal_verdicts.py — что каталог ответил на наши правила.

Правило 080 держалось словами, и причиной в своде стояло «проверить запись в
чужой репозиторий нечем». Канал при этом уже был двусторонним: предложение
уезжает файлом, вердикт приезжает файлом, и оба читаются без токена и прав.

Предмет проверки односторонний и потому проверяемый: **предложение с вынесенным
вердиктом предложением быть перестало**. Оставленное, оно выглядит ждущим
ответа, хотя ответ получен.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPTS = pathlib.Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS / "check_proposal_verdicts.py"


@pytest.fixture
def checker() -> ModuleType:
    """Свежий модуль на каждый тест."""
    spec = importlib.util.spec_from_file_location("_check_proposal_verdicts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ours(root: pathlib.Path, slugs: list[str]) -> pathlib.Path:
    target = root / ".rules" / "proposals.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "1.0", "proposals": [{"slug": slug} for slug in slugs]}
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return root


def _theirs(root: pathlib.Path, verdicts: dict[str, dict[str, str]]) -> pathlib.Path:
    target = root / ".rules" / "proposals.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema": "1.0", "verdicts": verdicts}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_an_admitted_proposal_is_reported(checker: ModuleType, tmp_path: pathlib.Path) -> None:
    """Принятое предложение названо вместе с присвоенным номером."""
    ours = _ours(tmp_path / "проект", ["наше-правило"])
    theirs = _theirs(
        tmp_path / "каталог",
        {f"{checker.PROJECT}:наше-правило": {"status": "admitted", "rule": "157", "why": "…"}},
    )

    assert checker.main(["--catalogue", str(theirs), "--root", str(ours)]) == 1


def test_a_pending_proposal_is_silence(checker: ModuleType, tmp_path: pathlib.Path) -> None:
    """Вердикта нет — предложение ждёт, и это не находка."""
    ours = _ours(tmp_path / "проект", ["ещё-не-решено"])
    theirs = _theirs(tmp_path / "каталог", {})

    assert checker.main(["--catalogue", str(theirs), "--root", str(ours)]) == 0


def test_a_verdict_for_another_project_is_not_ours(
    checker: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Слаг не уникален между проектами — ключ обязан нести репозиторий."""
    ours = _ours(tmp_path / "проект", ["общий-слаг"])
    theirs = _theirs(
        tmp_path / "каталог", {"ArtVsMark/Сосед:общий-слаг": {"status": "admitted", "rule": "9"}}
    )

    assert checker.main(["--catalogue", str(theirs), "--root", str(ours)]) == 0


def test_every_proposal_is_examined_not_just_the_first(
    checker: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Вердикт набора выносится после последнего случая (правило 159).

    Ранний выход на первом же решённом предложении оставил бы идущие следом
    непроверенными, а список — прочитанным до конца перебора.
    """
    ours = _ours(tmp_path / "проект", ["первое", "второе"])
    theirs = _theirs(
        tmp_path / "каталог",
        {
            f"{checker.PROJECT}:первое": {"status": "admitted", "rule": "157"},
            f"{checker.PROJECT}:второе": {"status": "rejected", "why": "решение иное"},
        },
    )

    settled = checker.settled_proposals(
        json.loads((ours / ".rules" / "proposals.json").read_text(encoding="utf-8")),
        json.loads((theirs / ".rules" / "proposals.json").read_text(encoding="utf-8")),
    )

    assert [slug for slug, _, _ in settled] == ["первое", "второе"], settled


def test_an_unreadable_side_names_which_one(checker: ModuleType, tmp_path: pathlib.Path) -> None:
    """Третий исход называет ПРЕДМЕТ отказа, а не только причину (правило 158).

    Скрипт читает два файла в разных репозиториях: без адреса «файл не
    разбирается» не отвечает на единственный нужный вопрос — чей это отказ.
    """
    ours = _ours(tmp_path / "проект", ["наше"])

    code = checker.main(["--catalogue", str(tmp_path / "нет-клона"), "--root", str(ours)])

    assert code == checker.EXIT_BROKEN
