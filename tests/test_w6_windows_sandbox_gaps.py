"""issue #648 (эпик #620, волна W6): escape-PoC пределов Windows Job Objects backend.

Windows-часть аудита «реальная герметичность трёх sandbox-backend'ов». Job
Objects (`core/sandbox/_windows.py`) изолируют РЕСУРСЫ (память/CPU/число
процессов/kill-on-close/таймаут/размер вывода — это уже покрыто положительными
сценариями в ``test_sandbox_runner.py::TestWindowsSandboxRunner``) и пересобирают
окружение (секреты грейдера не пробрасываются). Но два пробела MVP заявлены
прямо в докстринге ``_windows.py`` (стр. «Два сознательных пробела MVP») и в
``SECURITY.md``:

* **нет сетевой изоляции** (SEC-CORE-04 — AppContainer не реализован);
* **нет ФС-изоляции** — только ``cwd``-контейнмент, абсолютные пути НЕ
  блокируются (SEC-CORE-03-аналог: чтение секретов/запись наружу).

Тесты ниже — **характеризующие**: они ассертят ТЕКУЩЕЕ (небезопасное)
поведение, превращая «пробел, описанный в докстринге» в исполняемый контракт.
Это НЕ утверждение, что так правильно, — это фиксация факта, чтобы:

1. пробел нельзя было потерять из виду (в отличие от прозаической оговорки);
2. **закрытие пробела было замечено**: как только на Windows появится ФС/сеть-
   изоляция, соответствующий тест здесь ПОКРАСНЕЕТ. Красный тест в этом файле =
   «дыру закрыли» → нужно переписать его на блокировку (как
   ``_assert_network_blocked`` / ``_assert_write_outside_run_dir_blocked`` для
   Linux/macOS в ``test_sandbox_runner.py``) и снять оговорку в докстринге
   ``_windows.py`` и в ``SECURITY.md``.

Методология аудита (эпик #620): дельта покрывается точечно; Linux (bwrap) и
macOS (sandbox-exec) части #648 — отдельно, здесь не трогаются (нужны живые ОС).

Сетевой PoC СОЗНАТЕЛЬНО бьёт в локальный listener (127.0.0.1), поднятый самим
тестом, а не во внешний хост: это детерминированно и работает оффлайн (на
раннере без интернета), и это более строгий PoC — Linux netns не пустил бы
дочерний процесс даже к loopback-сокету хоста, а Windows Job Object пускает.
"""

from __future__ import annotations

import pathlib
import socket
import sys
import threading

import pytest

from stepik_grader.core.runner import RunSpec

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects backend only")


def _runner():
    """Сконструировать боевой Windows-backend (Job Object API-проба внутри)."""
    from stepik_grader.core.sandbox._windows import create_backend

    return create_backend()


def _write_script(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "sol.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_network_egress_not_isolated(tmp_path: pathlib.Path) -> None:
    """ПРОБЕЛ (SEC-CORE-04): sandboxed-код открывает TCP-соединение наружу.

    Escape подтверждён локально: дочерний процесс достукивается до listener'а,
    поднятого этим тестом на 127.0.0.1. Изолированная песочница (Linux netns)
    этого бы не позволила. Красный тест = сетевая изоляция появилась.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    accepted = threading.Event()

    def _accept() -> None:
        try:
            conn, _ = srv.accept()
            accepted.set()
            conn.close()
        except OSError:
            pass

    accepter = threading.Thread(target=_accept, name="w6-accept", daemon=True)
    accepter.start()
    try:
        script = _write_script(
            tmp_path,
            "import socket\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.settimeout(5)\n"
            f"s.connect(('127.0.0.1', {port}))\n"
            "s.sendall(b'exfil')\n"
            "print('CONNECTED')\n"
            "s.close()\n",
        )
        outcome = _runner().run(RunSpec(path=script, stdin=None, timeout=10.0))
    finally:
        srv.close()

    accepter.join(timeout=2.0)
    # Побег: соединение установлено (эксфильтрация возможна). Если сеть станет
    # изолированной — connect бросит, 'CONNECTED' не напечатается → красный тест.
    assert outcome.returncode == 0, outcome.stderr.decode("utf-8", errors="replace")
    assert b"CONNECTED" in outcome.stdout
    assert accepted.is_set()


def test_absolute_path_write_not_blocked(tmp_path: pathlib.Path) -> None:
    """ПРОБЕЛ (ФС): запись по абсолютному пути ВНЕ run_dir не блокируется.

    Только ``cwd``-контейнмент: относительные пути идут в per-run tmp, но
    абсолютный путь пишется куда угодно, куда есть права у пользователя.
    Контраст с Linux/macOS, где ``_assert_write_outside_run_dir_blocked``
    требует ``not target.exists()``. Красный тест = ФС-изоляция появилась.
    """
    target = tmp_path / "escape_write.txt"
    script = _write_script(
        tmp_path,
        f"open({str(target)!r}, 'w', encoding='utf-8').write('pwned-by-sandbox')\n",
    )
    outcome = _runner().run(RunSpec(path=script, stdin=None, timeout=5.0))

    assert outcome.returncode == 0, outcome.stderr.decode("utf-8", errors="replace")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "pwned-by-sandbox"


def test_absolute_path_read_exfiltrates_secret(tmp_path: pathlib.Path) -> None:
    """ПРОБЕЛ (SEC-CORE-03-аналог): чтение произвольного файла + эксфильтрация.

    sandboxed-код читает «секрет» вне run_dir по абсолютному пути и печатает
    его в stdout — то есть данные утекают наружу через результат прогона.
    Красный тест = ФС-изоляция чтения появилась.
    """
    secret = "S3CRET-windows-exfil-42"
    secret_file = tmp_path / "secrets.txt"
    secret_file.write_text(secret, encoding="utf-8")

    script = _write_script(
        tmp_path,
        f"print(open({str(secret_file)!r}, encoding='utf-8').read())\n",
    )
    outcome = _runner().run(RunSpec(path=script, stdin=None, timeout=5.0))

    assert outcome.returncode == 0, outcome.stderr.decode("utf-8", errors="replace")
    assert secret in outcome.stdout.decode("utf-8", errors="replace")
