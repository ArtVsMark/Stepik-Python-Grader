"""TLS-транспорт `gh_rest` против настоящего сервера (issue #1259).

Соседний `test_gh_rest.py` подменяет `urlopen` и проверяет решения модуля. Здесь
проверяется то, чего подменой не проверить: **как ведёт себя реальная проверка
сертификата**. Поднимается локальный HTTPS-сервер со своим CA, и запрос уходит
через тот же `_default_opener`, которым ходит весь конвейер.

Ради чего это нужно именно так. Прежняя редакция модуля при сбое TLS
**сама** повторяла запрос с ослабленной проверкой — молча, решением кода, а не
окружения. Это против инварианта проекта: невыполнимая гарантия — громкий
отказ, а не автоматический обход (ср. недоступный backend песочницы, где ответ
— `parser.error`, а не тихий `LocalRunner`). Тест на моках такое различие
показывает лишь настолько, насколько его показывает сам мок; настоящий сервер
не оставляет места договорённостям.

Различаются два непохожих отказа:

* **недоверенный сертификат** — лечится `SSL_CERT_FILE`, то есть окружением;
* **строгая проверка формы CA** (`VERIFY_X509_STRICT`, включён по умолчанию с
  Python 3.13) — `SSL_CERT_FILE` не лечится вовсе: бандл уже нужный, не проходит
  его оформление. Ровно этот случай ловится в облачной сессии с корпоративным
  прокси, и ровно на нём стоял тихий откат.

Сертификаты выпекает `openssl`: `cryptography` в зависимостях нет и не будет
(три runtime-зависимости — осознанная граница), а хранить готовые ключи в репо
значит завести бомбу с часовым механизмом на дате истечения. Нет `openssl` —
тесты пропускаются, а не притворяются пройденными.
"""

from __future__ import annotations

import contextlib
import dataclasses
import http.server
import json
import pathlib
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "gh_rest.py"

_NO_OPENSSL = shutil.which("openssl") is None
_STRICT_BY_DEFAULT = bool(ssl.create_default_context().verify_flags & ssl.VERIFY_X509_STRICT)

pytestmark = pytest.mark.skipif(_NO_OPENSSL, reason="нужен openssl для выпечки тестового CA")


@dataclasses.dataclass(frozen=True)
class _Pki:
    """Свой удостоверяющий центр и выписанный им сертификат сервера."""

    ca_cert: pathlib.Path
    server_cert: pathlib.Path
    server_key: pathlib.Path


def _openssl(*args: str) -> None:
    """Вызвать `openssl`, показав его вывод, если он упал."""
    done = subprocess.run(["openssl", *args], capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:  # pragma: no cover - диагностика окружения
        raise AssertionError(f"openssl {' '.join(args)}\n{done.stdout}\n{done.stderr}")


def _make_pki(root: pathlib.Path, *, ca_key_usage: bool) -> _Pki:
    """Выпечь CA и сертификат для `127.0.0.1`, подписанный этим CA.

    Args:
        root: куда сложить ключи и сертификаты.
        ca_key_usage: выдавать ли CA расширение `keyUsage`. Без него
            `VERIFY_X509_STRICT` отвергает цепочку с
            «CA cert does not include key usage extension» — это и есть
            поведение корпоративного прокси, из-за которого появился откат.
    """
    ca_key, ca_cert = root / "ca.key", root / "ca.crt"
    server_key, server_cert = root / "server.key", root / "server.crt"
    csr, ext = root / "server.csr", root / "server.ext"

    ca_extensions = [
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "subjectKeyIdentifier=hash",
    ]  # fmt: skip
    if ca_key_usage:
        ca_extensions += ["-addext", "keyUsage=critical,keyCertSign,cRLSign"]
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
        "-subj", "/CN=Stepik-Grader Test CA",
        "-keyout", str(ca_key), "-out", str(ca_cert), *ca_extensions,
    )  # fmt: skip

    _openssl(
        "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=127.0.0.1",
        "-keyout", str(server_key), "-out", str(csr),
    )  # fmt: skip
    ext.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=IP:127.0.0.1\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="utf-8",
    )
    _openssl(
        "x509", "-req", "-in", str(csr), "-days", "3650",
        "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial",
        "-extfile", str(ext), "-out", str(server_cert),
    )  # fmt: skip

    return _Pki(ca_cert=ca_cert, server_cert=server_cert, server_key=server_key)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Отвечает единственным JSON — содержимое здесь не проверяется."""

    def do_GET(self) -> None:
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Молча: иначе каждый рукопожатный сбой сыпется в вывод прогона."""


@contextlib.contextmanager
def _serving(pki: _Pki) -> Iterator[str]:
    """Локальный HTTPS-сервер на случайном порту; отдаёт свой URL."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki.server_cert, pki.server_key)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture(scope="module")
def module() -> ModuleType:
    """Модуль скрипта; сеть настоящая, но только до локального сервера."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_gh_rest_tls", _SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture(scope="module")
def trusted(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    return _make_pki(tmp_path_factory.mktemp("pki-trusted"), ca_key_usage=True)


@pytest.fixture(scope="module")
def stranger(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    """Второй, ни с чем не связанный CA — им проверяется настоящее недоверие."""
    return _make_pki(tmp_path_factory.mktemp("pki-stranger"), ca_key_usage=True)


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> _Pki:
    """CA без `keyUsage` — то, чем подписывает перехватывающий прокси."""
    return _make_pki(tmp_path_factory.mktemp("pki-legacy"), ca_key_usage=False)


def _fetch(module: ModuleType, url: str) -> tuple[int, str]:
    with module._default_opener(urllib.request.Request(url)) as response:
        return int(response.status), response.read().decode("utf-8")


class TestTrustComesFromTheEnvironment:
    """Доверенный набор задаёт окружение — модуль его не переопределяет."""

    def test_correct_ssl_cert_file_passes(
        self, module: ModuleType, trusted: _Pki, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Штатный путь: CA указан переменной — соединение проходит."""
        monkeypatch.setenv("SSL_CERT_FILE", str(trusted.ca_cert))
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)

        with _serving(trusted) as url:
            assert _fetch(module, url) == (200, '{"ok": true}')

    def test_untrusted_certificate_is_refused(
        self, module: ModuleType, trusted: _Pki, stranger: _Pki, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Чужой CA — отказ, и это главное свойство всей правки."""
        monkeypatch.setenv("SSL_CERT_FILE", str(stranger.ca_cert))
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)

        with _serving(trusted) as url, pytest.raises(module.TlsVerificationError) as caught:
            _fetch(module, url)

        assert "SSL_CERT_FILE" in str(caught.value), "ошибка не подсказывает штатное лечение"

    def test_relaxation_does_not_buy_trust(
        self, module: ModuleType, trusted: _Pki, stranger: _Pki, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Даже с явным opt-in чужой сертификат остаётся чужим.

        Ослабляется проверка ФОРМЫ CA, а не доверие: иначе переменная была бы
        выключателем TLS, чего issue #1259 прямо запрещает.
        """
        monkeypatch.setenv("SSL_CERT_FILE", str(stranger.ca_cert))
        monkeypatch.setenv(module.ENV_RELAXED_CA, "1")

        with _serving(trusted) as url, pytest.raises(module.TlsVerificationError):
            _fetch(module, url)


@pytest.mark.skipif(
    not _STRICT_BY_DEFAULT,
    reason="VERIFY_X509_STRICT выключен по умолчанию (Python до 3.13) — отказ не воспроизводится",
)
class TestStrictFormIsRefusedUnlessAskedOtherwise:
    """Тот самый отказ, ради которого существовал тихий откат."""

    def test_legacy_ca_fails_without_the_opt_in(
        self, module: ModuleType, legacy: _Pki, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Модуль не выкручивается сам — он объясняет и останавливается."""
        monkeypatch.setenv("SSL_CERT_FILE", str(legacy.ca_cert))
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)

        with _serving(legacy) as url, pytest.raises(module.TlsVerificationError) as caught:
            _fetch(module, url)

        text = str(caught.value)
        assert "VERIFY_X509_STRICT" in text, "не названа настоящая причина"
        assert module.ENV_RELAXED_CA in text, "не назван осознанный выход"

    def test_opt_in_lets_it_through_and_says_so(
        self,
        module: ModuleType,
        legacy: _Pki,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """С переменной запрос проходит — и каждый раз предупреждает об этом."""
        monkeypatch.setenv("SSL_CERT_FILE", str(legacy.ca_cert))
        monkeypatch.setenv(module.ENV_RELAXED_CA, "1")

        with _serving(legacy) as url:
            assert _fetch(module, url)[0] == 200
            assert _fetch(module, url)[0] == 200

        warnings = capsys.readouterr().err
        assert warnings.count(module.ENV_RELAXED_CA) == 2, "предупреждение не на каждый запрос"

    @pytest.mark.parametrize("junk", ["", "0", "true", "yes", "да", " 1 x"])
    def test_only_the_exact_value_switches_it(
        self, module: ModuleType, legacy: _Pki, monkeypatch: pytest.MonkeyPatch, junk: str
    ) -> None:
        """Опечатка в переключателе безопасности означает «выключено»."""
        monkeypatch.setenv("SSL_CERT_FILE", str(legacy.ca_cert))
        monkeypatch.setenv(module.ENV_RELAXED_CA, junk)

        with _serving(legacy) as url, pytest.raises(module.TlsVerificationError):
            _fetch(module, url)


class TestOrdinaryFailuresAreNotDressedAsTls:
    def test_connection_refused_stays_a_url_error(
        self, module: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Порт закрыт — это не сбой проверки сертификата, и путать нельзя."""
        monkeypatch.delenv(module.ENV_RELAXED_CA, raising=False)

        with pytest.raises(urllib.error.URLError) as caught:
            _fetch(module, "https://127.0.0.1:1/")

        assert not isinstance(caught.value, module.TlsVerificationError)
