"""core/submission.py — общий путь отправки решения на Stepik (issue #1175).

Архитектурный слой: Application-service над ``core/stepik_client`` (сеть) и
``core/history`` (локальная база). Зависит только от модулей ниже по DAG, новых
циклов не создаёт.

Зачем отдельный модуль, а не пара строк в веб-адаптере. Вердикты платформы
приезжали к нам однобоко: при **скачивании** попытки раскладывались рядом с
задачей (``core/submission_archive``), а результат **живой отправки** не
сохранялся нигде — веб показывал его в интерфейсе, и на этом след обрывался.
Пока сбор живёт в адаптере, он собирает данные ровно одной поверхности:
подключат CLI или оркестратор прогона — и те пройдут мимо. Здесь же отправка и
запись — один вызов, поэтому любой будущий потребитель получает сверку
вердиктов бесплатно, а ``stepik_client`` остаётся тем, чем был: сетевым
клиентом без знания о локальной базе.

Почему модуль не решает за пользователя, писать ли историю. Запись включается
явным согласием (ADR-0002, #134), и действующее значение знает только
вызывающая сторона: у веба поверх конфига лежит оверрайд ``--serve
--no-history``. Поэтому путь к базе передаётся параметром, а ``None`` означает
«не писать» — гейт остаётся там, где о нём есть правда, а общей здесь остаётся
сама логика записи.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from stepik_grader.core import history, stepik_client

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    import requests

__all__ = ["submit_and_record"]

# Вердикты, которые вообще имеет смысл хранить: ответ платформы о решении.
# Список один на проект — ``history.PLATFORM_VERDICTS``, чтобы «что считается
# вердиктом» и «что пишется» не разъехались по двум файлам.
_RECORDED_VERDICTS = history.PLATFORM_VERDICTS


def submit_and_record(
    session: requests.Session,
    step_id: int,
    code: str,
    *,
    task_dir: Path | None = None,
    base_dir: Path | None = None,
    history_db: Path | None = None,
    language: str | None = None,
    timeout: float = 60.0,
    cancel_event: threading.Event | None = None,
) -> stepik_client.SubmissionResult:
    """Отправить решение и записать вердикт платформы в историю (issue #1175).

    Обёртка над ``stepik_client.submit_and_wait``: сначала полный поток
    отправки (attempt → submission → poll), затем — запись вердикта, если
    ``history_db`` задан.

    ``task_dir``/``base_dir`` нужны только для ключа задачи (``task_key_for``):
    без них вердикт платформы всё равно запишется, но привязать его к нашему
    прогону будет нечем — сравнение вердиктов останется пустым.

    В историю попадают только терминальные вердикты платформы
    (``correct``/``wrong``). ``evaluation`` (проверка ещё идёт: poll не
    дождался) и ``error`` (сбой отправки) — это не ответ о решении, и записывать
    их значило бы засорять сверку строками, которые ни с чем не сравниваются.

    Best-effort: сбой записи не влияет на возвращаемый результат — решение уже
    ушло на платформу, и падать после этого не на чем.
    """
    result = stepik_client.submit_and_wait(
        session, step_id, code, language=language, timeout=timeout, cancel_event=cancel_event
    )
    if history_db is not None and result.status in _RECORDED_VERDICTS:
        task_key = ""
        if task_dir is not None:
            task_key = history.task_key_for(task_dir, base_dir or task_dir)
        history.record_stepik_submission(
            step_id,
            result.status,
            db_path=history_db,
            task_key=task_key,
            submission_id=result.submission_id,
            hint=result.hint or None,
        )
    return result
