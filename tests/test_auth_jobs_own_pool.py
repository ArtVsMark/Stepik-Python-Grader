"""Авторизация не занимает воркеров проверки (issue #971).

Находка RUN-3-03. Джобы авторизации клались в тот же ``ThreadPoolExecutor``,
что и проверки решений, а пул по умолчанию на два воркера. ``perform_browser_auth``
блокируется до 120 секунд — ждёт, пока человек нажмёт кнопку в браузере, — то
есть два параллельных ``POST /api/auth/start`` занимали пул целиком. Кнопка
«Проверить» переставала отвечать, хотя грейдер всё это время простаивал.

Отмена при этом отвечала 200 и не делала ничего: ``wait_for_auth_code``
прерывания не поддерживал. Студент, начавший авторизацию и передумавший,
оставался без работающей проверки до истечения таймаута.

Три границы, которые тут проверяются: свой пул под авторизацию, отказ плодить
вторую (переиспользуем идущую), прерываемое ожидание кода.
"""

from __future__ import annotations

import threading
import time

import pytest

from stepik_grader.core import stepik_client
from stepik_grader.core.stepik_client import wait_for_auth_code
from stepik_grader.web import runs


def _cancelled_error() -> type[BaseException]:
    """Тип «ожидание отменено» — берётся в рантайме, а не импортом модуля.

    До фикса класса не существовало, и импорт наверху ронял бы сбор всего
    файла: тесты про пулы краснели бы по чужой причине, ничего не доказывая
    про себя. Так каждый падает на своём.
    """
    return stepik_client.OAuthCancelled


@pytest.fixture(autouse=True)
def clean_registry():
    """Реестр job'ов — module-level; каждый тест начинает с чистого."""
    with runs._JOBS_LOCK:
        runs._JOBS.clear()
    yield
    with runs._JOBS_LOCK:
        for job in runs._JOBS.values():
            job.cancel_event.set()
        runs._JOBS.clear()


class TestSeparatePools:
    """Блокирующая авторизация и проверки решений не делят воркеров."""

    def test_pools_are_distinct(self) -> None:
        assert runs._get_auth_executor() is not runs._get_executor()

    def test_auth_pool_is_single_threaded(self) -> None:
        """Параллельных браузерных flow не бывает: браузер и порт колбэка одни."""
        assert runs._get_auth_executor()._max_workers == 1

    def test_shutdown_closes_both(self) -> None:
        """Иначе Ctrl+C оставлял бы висеть поток, ради которого всё и делалось."""
        auth_pool = runs._get_auth_executor()
        runs._get_executor()

        runs.shutdown_jobs()

        assert runs._auth_executor is None
        assert runs._executor is None
        assert auth_pool._shutdown


class TestSecondAuthReusesTheFirst:
    """Вторая авторизация не встаёт в очередь и не плодит вторую попытку."""

    def test_same_job_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_auth(*args: object, **kwargs: object) -> dict[str, str]:
            started.set()
            release.wait(timeout=5)
            return {"authorized": "yes"}

        monkeypatch.setattr(runs, "_run_auth_job", lambda job, params, lang: slow_auth())
        params = {
            "secrets_path": "secrets.json",
            "client_id": "a",
            "client_secret": "b",
            "redirect_uri": "http://127.0.0.1:5000/",
        }

        first = runs.submit_job("auth", None, params)
        assert started.wait(timeout=5)
        second = runs.submit_job("auth", None, params)
        release.set()

        assert second.id == first.id

    def test_finished_auth_does_not_block_a_new_one(self) -> None:
        """Переиспользуется только ИДУЩАЯ: завершённая ничему не мешает."""
        done = runs.Job("old", "auth")
        done.status = "done"
        with runs._JOBS_LOCK:
            runs._JOBS[done.id] = done
            assert runs._active_auth_job_locked() is None

    def test_running_auth_is_found(self) -> None:
        active = runs.Job("live", "auth")
        active.status = "running"
        with runs._JOBS_LOCK:
            runs._JOBS[active.id] = active
            assert runs._active_auth_job_locked() is active

    def test_other_kinds_are_not_reused(self) -> None:
        """Проверки решений идут параллельно — их переиспользовать нельзя."""
        grading = runs.Job("grading", "tests")
        grading.status = "running"
        with runs._JOBS_LOCK:
            runs._JOBS[grading.id] = grading
            assert runs._active_auth_job_locked() is None


class TestWaitingIsInterruptible:
    """Отмена действует, а не отвечает 200 и ничего не делает."""

    def test_cancel_before_start_raises_immediately(self) -> None:
        """Уже отменённое ожидание не должно занимать поток ни одного тика."""
        cancelled = threading.Event()
        cancelled.set()

        started = time.monotonic()
        with pytest.raises(_cancelled_error()):
            wait_for_auth_code("127.0.0.1", 0, "/", "state", timeout=120, cancel_event=cancelled)

        assert time.monotonic() - started < 5  # а не 120 секунд таймаута

    def test_cancel_during_wait_stops_it(self) -> None:
        """Отмена приходит, когда ожидание уже идёт, — самый частый случай."""
        cancel = threading.Event()
        outcome: list[object] = []

        def waiter() -> None:
            try:
                wait_for_auth_code("127.0.0.1", 0, "/", "state", timeout=120, cancel_event=cancel)
            except BaseException as exc:  # переносим в поток теста как есть
                outcome.append(exc)

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        time.sleep(0.3)
        cancel.set()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert outcome and isinstance(outcome[0], _cancelled_error())

    def test_without_event_behaviour_is_unchanged(self) -> None:
        """Старый вызов без события обязан работать как прежде."""
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            wait_for_auth_code("127.0.0.1", 0, "/", "state", timeout=1)

        assert time.monotonic() - started >= 1


class TestCancelledIsNotAnError:
    """Отменённая авторизация финализируется как отмена, а не как поломка."""

    def test_cancelled_status_and_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stepik_grader.web import auth_adapter

        def cancelled(*args: object, **kwargs: object) -> dict[str, str]:
            raise _cancelled_error()("отменено")

        monkeypatch.setattr(auth_adapter, "perform_browser_auth", cancelled)
        job = runs.Job("j", "auth")

        runs._run_auth_job(
            job,
            {
                "secrets_path": "secrets.json",
                "client_id": "a",
                "client_secret": "b",
                "redirect_uri": "http://127.0.0.1:5000/",
            },
            "ru",
        )

        assert job.status == "cancelled"
        assert job.message_fields is not None
        assert job.message_fields["message_id"] == "run_cancelled"

    def test_real_failure_is_still_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Границу видно: настоящий сбой по-прежнему `error`, а не `cancelled`."""
        from stepik_grader.web import auth_adapter

        def boom(*args: object, **kwargs: object) -> dict[str, str]:
            raise RuntimeError("порт занят")

        monkeypatch.setattr(auth_adapter, "perform_browser_auth", boom)
        job = runs.Job("j", "auth")

        runs._run_auth_job(
            job,
            {
                "secrets_path": "secrets.json",
                "client_id": "a",
                "client_secret": "b",
                "redirect_uri": "http://127.0.0.1:5000/",
            },
            "ru",
        )

        assert job.status == "error"
