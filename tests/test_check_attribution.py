"""Тесты scripts/check_attribution.py — атрибуция сверяется до мержа (issue #1343).

Дефект, ради которого всё написано: изменение уехало в `main` с трейлером
`Co-authored-by: Claude <noreply@anthropic.com>` вместо согласованного
`Claude Opus 5 <noreply@anthropic.com>`. Подставила его платформа при squash,
взяв git-идентичность окна, — и один соавтор оказался в истории под двумя
именами. Переписать нечем: `main` защищена.

Поэтому проверяется именно **то, что станет итоговым коммитом**: авторы
коммитов ветки, а не трейлеры в теле PR (их платформа допишет сама).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS / "check_attribution.py"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_check_attribution", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def attribution() -> ModuleType:
    """Свежий модуль на каждый тест."""
    return _load_module()


# ---------------------------------------------------------------------------
# Разбор строк: приходят из рук человека, падать на них нельзя
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "name", "email"),
    [
        ("Claude Opus 5 <noreply@anthropic.com>", "Claude Opus 5", "noreply@anthropic.com"),
        ("  Имя Фамилия  <a@b.c>  ", "Имя Фамилия", "a@b.c"),
        ("Claude <NoReply@Anthropic.COM>", "Claude", "noreply@anthropic.com"),
    ],
)
def test_identity_is_parsed_tolerantly(
    attribution: ModuleType, raw: str, name: str, email: str
) -> None:
    """Лишние пробелы и регистр почты не мешают — иначе гейт даёт ложные отказы."""
    identity = attribution.parse_identity(raw)
    assert identity is not None
    assert identity.name == name
    assert identity.email == email


@pytest.mark.parametrize("raw", ["без скобок", "", "<only@email>", "Имя <без закрывающей"])
def test_garbage_is_none_not_an_exception(attribution: ModuleType, raw: str) -> None:
    """Мусор — `None`: строка пришла из сообщения коммита, а не из схемы."""
    assert attribution.parse_identity(raw) is None


def test_identity_compares_name_and_email_together(attribution: ModuleType) -> None:
    """Расхождение было в ИМЕНИ при совпадающей почте — сверять только почту нельзя."""
    short = attribution.Identity("Claude", "noreply@anthropic.com")
    full = attribution.Identity("Claude Opus 5", "noreply@anthropic.com")
    assert short != full
    assert len({short, full}) == 2


# ---------------------------------------------------------------------------
# Согласованный список читается из одного места
# ---------------------------------------------------------------------------


def test_agreed_list_comes_from_settings(attribution: ModuleType, tmp_path: Path) -> None:
    """Список берётся из того же ключа, который харнесс подставляет в коммиты."""
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "attribution": {
                    "commit": (
                        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
                        "Co-Authored-By: Кто-то Ещё <someone@example.com>"
                    )
                }
            }
        ),
        encoding="utf-8",
    )

    agreed = attribution.agreed_identities(settings)

    assert attribution.Identity("Claude Opus 5", "noreply@anthropic.com") in agreed
    assert attribution.Identity("Кто-то Ещё", "someone@example.com") in agreed


def test_missing_settings_is_empty_not_a_crash(attribution: ModuleType, tmp_path: Path) -> None:
    """Файла нет — пустой список; решение «сверять не с чем» принимает вызывающий."""
    assert attribution.agreed_identities(tmp_path / "нет.json") == set()


def test_owner_comes_from_pyproject_not_git(attribution: ModuleType, tmp_path: Path) -> None:
    """Владелец — из pyproject: git-идентичность у окна, контейнера и CI разная."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nauthors = [{name = "Артём", email = "a@b.c"}]\n', encoding="utf-8"
    )

    owner = attribution.owner_identity(pyproject)

    assert owner is not None
    assert owner.name == "Артём"


# ---------------------------------------------------------------------------
# Главное: несогласованная подпись видна
# ---------------------------------------------------------------------------


def test_wrong_agent_name_is_caught(attribution: ModuleType) -> None:
    """Тот самый случай: почта совпадает, имя — нет."""
    agreed = {attribution.Identity("Claude Opus 5", "noreply@anthropic.com")}
    found = {attribution.Identity("Claude", "noreply@anthropic.com")}

    wrong = attribution.mismatched(found, agreed=agreed, owner=None)

    assert [str(identity) for identity in wrong] == ["Claude <noreply@anthropic.com>"]


def test_agreed_identity_passes(attribution: ModuleType) -> None:
    """Согласованная подпись расхождением не считается."""
    agreed = {attribution.Identity("Claude Opus 5", "noreply@anthropic.com")}
    assert attribution.mismatched(set(agreed), agreed=agreed, owner=None) == []


def test_owner_is_always_agreed(attribution: ModuleType) -> None:
    """Владелец в списке не перечисляется — он берётся из pyproject."""
    owner = attribution.Identity("Артём", "a@b.c")
    assert attribution.mismatched({owner}, agreed=set(), owner=owner) == []


def test_external_contributor_is_not_our_defect(attribution: ModuleType) -> None:
    """Внешний соавтор законен: требовать от него нашей строки — то же, что русский текст.

    Без этого разделения ревизия истории считала бы чужой вклад поломкой, и
    число «сколько испорчено» перестало бы что-либо значить.
    """
    outsider = attribution.Identity("mercael91", "mercael91@users.noreply.github.com")
    agent = attribution.Identity("Claude", "noreply@anthropic.com")

    strict = attribution.mismatched({outsider, agent}, agreed=set(), owner=None)
    agents_only = attribution.mismatched(
        {outsider, agent}, agreed=set(), owner=None, agents_only=True
    )

    assert outsider in strict, "на своей ветке сверка строгая"
    assert agents_only == [agent], "в ревизии истории чужие имена — не наш дефект"


@pytest.mark.parametrize(
    ("name", "email", "expected"),
    [
        ("Claude", "noreply@anthropic.com", True),
        ("claude[bot]", "209825114+claude[bot]@users.noreply.github.com", True),
        ("Кто-то", "someone@anthropic.com", True),
        ("mercael91", "mercael91@users.noreply.github.com", False),
    ],
)
def test_agent_signature_is_recognised(
    attribution: ModuleType, name: str, email: str, expected: bool
) -> None:
    """Агентская подпись узнаётся в любом написании — под ней и пряталось расхождение."""
    assert attribution.is_agent(attribution.Identity(name, email)) is expected


def test_trailers_are_read_case_insensitively(attribution: ModuleType) -> None:
    """`Co-authored-by` пишут по-разному — платформа одним регистром, харнесс другим."""
    message = (
        "fix: что-то\n\n"
        "Co-authored-by: Claude <noreply@anthropic.com>\n"
        "Co-Authored-By: Артём <a@b.c>\n"
        "Claude-Session: https://example.invalid\n"
    )

    found = attribution.trailer_identities(message)

    assert attribution.Identity("Claude", "noreply@anthropic.com") in found
    assert attribution.Identity("Артём", "a@b.c") in found
    assert len(found) == 2, "не-трейлерные строки в список не попадают"
