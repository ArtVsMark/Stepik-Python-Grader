"""Tests for scripts/generate_good_first_issues_badge.py — пул задач для новичка (issue #835).

README и CONTRIBUTING зовут новичка «взять issue с меткой `good first issue`», а
открытых задач с этой меткой было ноль — все шесть закрыты. Узнавал об этом
только сам новичок, уже кликнув по ссылке; владелец пустого пула не видел.

Сеть в тестах не используется: `fetch_open_count` принимает `opener`.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "generate_good_first_issues_badge.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_gfi_badge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _opener(total: int):
    def _open(request: object) -> _Response:
        return _Response(json.dumps({"total_count": total}).encode("utf-8"))

    return _open


@pytest.mark.parametrize(
    ("count", "color"),
    [(0, "red"), (1, "yellow"), (4, "yellow"), (5, "brightgreen"), (12, "brightgreen")],
    ids=["empty", "one", "thin", "healthy-edge", "healthy"],
)
def test_color_signals_pool_health(count: int, color: str) -> None:
    """Цвет — сигнал ВЛАДЕЛЬЦУ: пустой пул обязан быть виден как красный."""
    payload = _load().badge_payload(count)
    assert payload["color"] == color
    assert payload["message"] == str(count)
    assert payload["schemaVersion"] == 1


def test_payload_matches_shields_endpoint_schema() -> None:
    """Формат — тот же, что у соседних бейджей: иначе shields отдаст «invalid»."""
    payload = _load().badge_payload(6)
    assert set(payload) == {"schemaVersion", "label", "message", "color"}


def test_fetch_counts_open_issues_only() -> None:
    module = _load()
    assert module.fetch_open_count(opener=_opener(7)) == 7


def test_fetch_query_asks_for_open_issues_with_the_label() -> None:
    """Запрос обязан фильтровать по `is:open` — закрытые задачи пул не наполняют."""
    module = _load()
    seen: list[str] = []

    def _open(request):
        seen.append(request.full_url)
        return _Response(json.dumps({"total_count": 3}).encode("utf-8"))

    module.fetch_open_count(opener=_open)
    assert "is%3Aopen" in seen[0]
    assert "is%3Aissue" in seen[0]
    assert "good+first+issue" in seen[0] or "good%20first%20issue" in seen[0]


def test_writes_badge_file(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load()
    monkeypatch.setattr(module, "fetch_open_count", lambda *a, **kw: 6)
    out = tmp_path / "badges" / "good-first-issues.json"

    assert module.main(["--out", str(out)]) == 0

    assert json.loads(out.read_text(encoding="utf-8"))["message"] == "6"
    assert "6" in capsys.readouterr().out


def test_empty_pool_warns_but_does_not_fail(tmp_path: Path, monkeypatch, capsys) -> None:
    """Пустой пул — состояние трекера, а не дефект кода.

    Падение здесь блокировало бы посторонние PR из-за того, что владелец не успел
    завести задачу. Сигнал — предупреждение и красный бейдж.
    """
    module = _load()
    monkeypatch.setattr(module, "fetch_open_count", lambda *a, **kw: 0)
    out = tmp_path / "b.json"

    assert module.main(["--out", str(out)]) == 0

    assert "::warning::" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["color"] == "red"


def test_thin_pool_warns_too(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load()
    monkeypatch.setattr(module, "fetch_open_count", lambda *a, **kw: 2)
    assert module.main(["--out", str(tmp_path / "b.json")]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_network_failure_keeps_the_previous_badge(tmp_path: Path, monkeypatch) -> None:
    """Сбой сети не должен записывать выдуманное число поверх честного.

    Тот же принцип, что у coverage-бейджа при деградации: лучше устаревшее
    значение, чем неверное.
    """
    import urllib.error

    module = _load()

    def _boom(*a: object, **kw: object) -> int:
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(module, "fetch_open_count", _boom)
    out = tmp_path / "b.json"
    out.write_text('{"message": "6"}', encoding="utf-8")

    assert module.main(["--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["message"] == "6"


def test_repo_badge_file_is_present_and_valid() -> None:
    """Файл бейджа есть в репозитории — иначе README смотрит в 404.

    Ровно этим отличился `coverage-combined.json`, которого не было до первого
    прогона job'а: бейдж в README висел битым.
    """
    payload = json.loads(
        (Path(__file__).parent.parent / ".github" / "badges" / "good-first-issues.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["schemaVersion"] == 1
    assert payload["message"].isdigit()
