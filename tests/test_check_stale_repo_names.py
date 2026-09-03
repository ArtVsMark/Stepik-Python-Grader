"""Тесты переписи имён репозиториев (issue #1421, правило 172).

Предмет — миграция, у которой **нет способа сделать старый путь нерабочим**:
переименованный репозиторий отвечает по прежнему имени редиректом площадки.
Обычные средства бесполезны все разом — прогон зелёный, ссылка живая, гейт
целостности доволен, — поэтому сигнал заменяет перепись.

Гейт проверяется тем, что обязан отвергнуть (правило 140), и отдельно тем, чего
отвергать не должен: ложная находка в переписи стоит дороже пропущенной —
перепись, краснеющую на выдуманном, отключают целиком.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_stale_repo_names.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_stale_repo_names", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()
_OWNER = _MODULE.OWNER

# Имена подделок — ASCII: GitHub допускает в имени репозитория только буквы,
# цифры, точку, дефис и подчёркивание. Первая редакция этих тестов брала имя
# кириллицей, и они падали не на дефекте, а на невозможном значении — подделка,
# отвечающая не то, что отвечает площадка (правило 170).


def _tree(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    """Разложить дерево из ``путь → текст``."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# --- сбор имён из дерева ---------------------------------------------------------


def test_a_link_target_is_a_name(tmp_path: pathlib.Path) -> None:
    """Имя из ссылки опознаётся вместе с продолжением пути."""
    root = _tree(
        tmp_path, {"README.md": f"см. https://github.com/{_OWNER}/Neighbour-Repo/blob/main/x.md"}
    )

    assert "Neighbour-Repo" in _MODULE.mentions(root)


def test_a_bare_pair_is_a_name(tmp_path: pathlib.Path) -> None:
    """Голая пара «владелец/имя» — тоже адрес: так пишется ``uses:``."""
    root = _tree(tmp_path, {".github/workflows/x.yml": f"      uses: {_OWNER}/Neighbour-Repo@v1\n"})

    assert "Neighbour-Repo" in _MODULE.mentions(root)


def test_a_branch_reference_is_not_a_repository(tmp_path: pathlib.Path) -> None:
    """Имя ВЕТКИ за владельцем репозиторием не является.

    Живой случай: плейсхолдер в шаблоне обращения — «Merge pull request #741
    from <владелец>/<ветка>/<хвост>». Первая редакция переписи считала «ветку»
    именем репозитория и шла спрашивать о ней у площадки.
    """
    root = _tree(
        tmp_path,
        {".github/x.yml": f"placeholder: Merge pull request #741 from {_OWNER}/docs/sweep-700\n"},
    )

    assert "docs" not in _MODULE.mentions(root)


def test_history_is_not_rewritten(tmp_path: pathlib.Path) -> None:
    """Старый след в журнале и архиве — история, а не действующий адрес.

    Прошлое не правят задним числом (правило 114), и молчаливое включение этих
    файлов в перепись означало бы требование переписать журнал.
    """
    root = _tree(
        tmp_path,
        {
            "CHANGELOG.md": f"было {_OWNER}/Old-Name\n",
            "changelog.d/x.internal.md": f"было {_OWNER}/Old-Name\n",
            "docs/archive/old.md": f"было {_OWNER}/Old-Name\n",
        },
    )

    assert _MODULE.mentions(root) == {}


def test_an_active_document_is_counted(tmp_path: pathlib.Path) -> None:
    """Действующий документ в перепись входит — в отличие от архива."""
    root = _tree(tmp_path, {"docs/dev/x.md": f"{_OWNER}/Neighbour-Repo\n"})

    assert "Neighbour-Repo" in _MODULE.mentions(root)


# --- что перепись обязана отвергнуть ---------------------------------------------


def test_a_renamed_repository_is_a_finding() -> None:
    """Площадка зовёт репозиторий иначе — находка с обоими именами."""
    found = _MODULE.stale_names({"Old-Name": ["README.md"]}, {"Old-Name": "New-Name"})

    assert len(found) == 1
    assert "Old-Name" in found[0] and "New-Name" in found[0]


def test_a_canonical_name_is_silent() -> None:
    """Имя совпало с каноном — находки нет."""
    assert (
        _MODULE.stale_names({"Neighbour-Repo": ["README.md"]}, {"Neighbour-Repo": "Neighbour-Repo"})
        == []
    )


def test_an_unasked_name_is_not_a_finding() -> None:
    """Канон не получен — молчим: незнание устаревания не доказывает.

    В облачном окне доступ есть не ко всем репозиториям владельца, и красное на
    этом было бы ложным.
    """
    assert _MODULE.stale_names({"Neighbour-Repo": ["README.md"]}, {}) == []


# --- поведение скрипта -----------------------------------------------------------


def test_incompleteness_is_always_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Сколько имён спросить не удалось — говорится всегда, а не только при полном отказе.

    Перепись, о которой не сказано, скольких мест она не коснулась, и есть то
    состояние, ради которого правило заведено: «все прогоны зелёные, и никто не
    может назвать число мест».
    """
    monkeypatch.setattr(_MODULE, "mentions", lambda root=None: {"a": ["x.md"], "b": ["y.md"]})

    def _answer(_method: str, path: str, **_kwargs: object) -> object:
        if path.endswith("/a"):
            raise _MODULE.gh_rest.GitHubError("403")
        return type("R", (), {"data": {"full_name": f"{_OWNER}/b"}})()

    monkeypatch.setattr(_MODULE.gh_rest, "request", _answer)

    assert _MODULE.main([]) == 0
    out = capsys.readouterr().out
    assert "спросить не удалось — 1" in out
    assert "не спрошено — a" in out


def test_nothing_asked_is_the_third_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ни одного канона — «проверка не отработала» (код 2), а не «чисто»."""
    monkeypatch.setattr(_MODULE, "mentions", lambda root=None: {"a": ["x.md"]})

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise _MODULE.gh_rest.GitHubError("403")

    monkeypatch.setattr(_MODULE.gh_rest, "request", _refuse)

    assert _MODULE.main([]) == 2
    assert "не отработала" in capsys.readouterr().out


def test_the_live_tree_names_no_impossible_repository() -> None:
    """Приёмка: перепись живого дерева не выдумывает имён.

    Каждое собранное имя обязано быть похожим на имя репозитория, иначе
    следующий прогон пойдёт спрашивать площадку о ветке или о куске пути.
    """
    for name in _MODULE.mentions():
        assert "/" not in name and name.strip() == name, name
        assert not name.startswith("."), name
