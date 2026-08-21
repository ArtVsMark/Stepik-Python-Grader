"""Тесты scripts/check_secret_dumps.py — реестр точек дампа (issue #1301, #982).

Guard-the-guard: на реальном репозитории проверка зелёная, а синтетический
пакет с новой точкой записи делает её красной. Скрипт лежит в ``scripts/``
(не на ``sys.path``) — грузим по пути, тем же приёмом, что
``test_check_web_imports.py``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_secret_dumps.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_secret_dumps", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard() -> ModuleType:
    """Свежий экземпляр модуля проверки на каждый тест (реестр правится по месту)."""
    return _load_module()


def _package(tmp_path: Path, name: str, source: str) -> Path:
    """Синтетический «пакет» из одного модуля."""
    package = tmp_path / "pkg"
    package.mkdir(exist_ok=True)
    (package / name).write_text(source, encoding="utf-8")
    return package


# ---------------------------------------------------------------------------
# Реальный репозиторий
# ---------------------------------------------------------------------------


def test_passes_on_current_repo(guard: ModuleType) -> None:
    """На актуальном состоянии пакета нарушений нет — main() возвращает 0."""
    assert guard.main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_secret_dumps.py` завершается кодом 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cache_dump_is_registered_as_redacting(guard: ModuleType) -> None:
    """Файловый кэш ответов API числится точкой, которая сверяется с redact.

    Регрессия к предусловию #1301: кэш — единственное место, где ответ Stepik
    ложится на диск целиком, и именно поэтому он обязан оставаться под
    редакцией, а не «пока что содержит только безобидные эндпоинты».
    """
    assert guard.KNOWN_DUMPS["core/stepik_client.py::_cached_api_get"] == guard.REDACTED


# ---------------------------------------------------------------------------
# Предмет проверки: что считается точкой дампа
# ---------------------------------------------------------------------------


def test_new_dump_site_in_network_module_is_flagged(
    guard: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Новая запись на диск в сетевом модуле отклоняется, пока не разобрана."""
    package = _package(
        tmp_path,
        "fetcher.py",
        "import requests\n\n\ndef dump(path, session):\n"
        '    path.write_text(session.get("http://x").text)\n',
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)
    monkeypatch.setattr(guard, "KNOWN_DUMPS", {})

    assert guard.main() == 1
    assert "fetcher.py::dump" in capsys.readouterr().out


def test_module_without_network_imports_is_ignored(
    guard: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись в модуле, до которого сеть не доходит, точкой дампа не является."""
    package = _package(
        tmp_path,
        "report.py",
        "import json\n\n\ndef save(path, data):\n    path.write_text(json.dumps(data))\n",
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)

    assert guard.collect_dump_sites(package) == []


def test_socket_write_is_not_a_dump_site(
    guard: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`self.wfile.write(...)` — HTTP-ответ OAuth-колбэка, а не файл на диске."""
    package = _package(
        tmp_path,
        "callback.py",
        "import requests\n\n\ndef handle(self):\n    self.wfile.write(b'ok')\n",
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)

    assert guard.collect_dump_sites(package) == []


def test_registered_site_passes(
    guard: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Та же точка с причиной в реестре проверку проходит."""
    package = _package(
        tmp_path,
        "fetcher.py",
        "import requests\n\n\ndef dump(path, session):\n    path.write_text('данные')\n",
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)
    monkeypatch.setattr(guard, "KNOWN_DUMPS", {"fetcher.py::dump": "только отобранные поля"})

    assert guard.main() == 0


# ---------------------------------------------------------------------------
# Реестр не расходится с кодом ни в одну сторону
# ---------------------------------------------------------------------------


def test_redacted_mark_requires_a_redact_call(
    guard: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пометка «редактирует» без вызова redact — расхождение, а не формальность."""
    package = _package(
        tmp_path,
        "fetcher.py",
        "import requests\n\n\ndef dump(path, data):\n    path.write_text(data)\n",
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)
    monkeypatch.setattr(guard, "KNOWN_DUMPS", {"fetcher.py::dump": guard.REDACTED})

    assert guard.main() == 1
    assert "redact в ней не вызывается" in capsys.readouterr().out


def test_redact_call_satisfies_the_mark(
    guard: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """С вызовом redact та же точка проходит."""
    package = _package(
        tmp_path,
        "fetcher.py",
        "import requests\n\nfrom stepik_grader.core.diag_log import redact\n\n\n"
        "def dump(path, data):\n    path.write_text(redact(data))\n",
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)
    monkeypatch.setattr(guard, "KNOWN_DUMPS", {"fetcher.py::dump": guard.REDACTED})

    assert guard.main() == 0


def test_dead_registry_entry_is_flagged(
    guard: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Запись без соответствующей точки удаляется: протухший реестр ничего не утверждает."""
    package = _package(
        tmp_path,
        "fetcher.py",
        "import requests\n\n\ndef dump(path):\n    path.write_text('x')\n",
    )
    monkeypatch.setattr(guard, "_PACKAGE", package)
    monkeypatch.setattr(
        guard,
        "KNOWN_DUMPS",
        {"fetcher.py::dump": "ок", "fetcher.py::переехало": "функции больше нет"},
    )

    assert guard.main() == 1
    assert "мёртвая запись реестра" in capsys.readouterr().out


def test_zero_sites_is_an_error(
    guard: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пакет переехал → проверка падает, а не рапортует «всё чисто» (issue #787)."""
    package = tmp_path / "пусто"
    package.mkdir()
    monkeypatch.setattr(guard, "_PACKAGE", package)

    assert guard.main() == 1
    assert "Ни одной точки записи не найдено" in capsys.readouterr().out
