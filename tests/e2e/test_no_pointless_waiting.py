"""Ожидание с концом: опрос и загрузка отвечают, а не тянут время (issue #922).

Две находки одной природы — «ответ пришёл, но его не посмотрели» и «ответ не
пришёл, и ждать его некому»:

* ``FE-1-01`` — цикл опроса AI-подсказки читал тело, не глядя на статус. Прогон
  живёт в памяти сервера, и после перезапуска ``--serve`` опрос получает 404 с
  телом ``{"kind": "error", …}``. Поля ``status`` там нет, поэтому ни «done», ни
  «error» не срабатывали, и цикл честно доходил до дедлайна: семьдесят пять
  запросов и тридцать секунд ожидания вместо ответа.
* ``FE-1-05`` — каталог ``ui.json`` грузился без таймаута под top-level await.
  Выполнение всех импортирующих ``core.js`` модулей ждёт эту загрузку, поэтому
  зависший (а не упавший) запрос останавливал приложение целиком — навсегда, и
  выйти можно было только перезагрузкой страницы.

Оба воспроизводятся только в браузере: первый — счётом запросов, второй —
маршрутом, который не отвечает вовсе. Не входит в обычный ``pytest tests/`` —
см. ``tests/e2e/conftest.py``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

_TIMEOUT_MS = 15_000

#: Дедлайн цикла опроса — 30 с. Ответ обязан прийти НАМНОГО раньше: проверяем
#: не «быстро», а «не дотянул до дедлайна», иначе тест мерил бы скорость
#: раннера, а не наличие выхода из цикла.
_MUCH_LESS_THAN_DEADLINE_S = 15.0


def _ask_for_a_hint(page: Any) -> tuple[str, float]:
    """Позвать AI-подсказку напрямую и вернуть ``(текст панели, секунды)``.

    Через экспортированную функцию, а не через кнопку в интерфейсе: до кнопки
    нужно довести упавший прогон и диалог согласия, и тест начал бы падать от
    любого изменения этого пути. Проверяется здесь цикл опроса, а не дорога
    к нему.
    """
    started = time.monotonic()
    text = page.evaluate(
        """async () => {
            const core = await import("/static/core.js");
            const box = document.createElement("div");
            box.id = "probe-ai-hint";
            document.body.appendChild(box);
            await core.explainFailureWithAi({ traceback: "boom" }, box);
            return box.textContent;
        }"""
    )
    return text, time.monotonic() - started


def test_a_lost_run_ends_the_poll_instead_of_waiting_out_the_deadline(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """404 на опросе — это ответ «прогона нет», а не повод ждать полминуты."""
    polls: list[str] = []

    page.goto(e2e_server + "/")
    # Согласие уже дано: диалог — отдельный путь, и здесь он только мешал бы.
    page.evaluate("() => localStorage.setItem('grader_ai_consent', '1')")

    page.route(
        "**/api/v1/hint*",
        lambda route: route.fulfill(
            status=202,
            content_type="application/json",
            body='{"run_id": "ушёл-вместе-с-перезапуском"}',
        ),
    )

    def _gone(route: Any) -> None:
        polls.append(route.request.url)
        route.fulfill(
            status=404,
            content_type="application/json",
            body='{"kind": "error", "message": "run not found"}',
        )

    page.route("**/api/v1/runs/*", _gone)

    text, elapsed = _ask_for_a_hint(page)

    # Главное: цикл вышел, а не досидел до дедлайна.
    assert elapsed < _MUCH_LESS_THAN_DEADLINE_S, f"опрос тянул {elapsed:.1f} с"
    # И вышел по первому же 404, а не после десятков одинаковых ответов.
    assert len(polls) <= 2, f"опросов после 404: {len(polls)}"
    # Пользователю при этом сказано, что подсказки не будет.
    assert text.strip()


def test_a_server_error_on_the_poll_is_still_retried(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Граница с другой стороны: 5xx — причина повторить, а не сдаться.

    Сервер жив, но ответить сейчас не смог; следующий запрос может удаться, и
    отказ по первому 500 отнимал бы подсказку там, где она была доступна.
    """
    polls: list[str] = []

    page.goto(e2e_server + "/")
    page.evaluate("() => localStorage.setItem('grader_ai_consent', '1')")
    page.route(
        "**/api/v1/hint*",
        lambda route: route.fulfill(
            status=202,
            content_type="application/json",
            body='{"run_id": "живой"}',
        ),
    )

    def _flaky(route: Any) -> None:
        polls.append(route.request.url)
        if len(polls) <= 2:
            route.fulfill(status=503, content_type="application/json", body="{}")
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status": "done", "result": {"hint": "подсказка", "configured": true}}',
        )

    page.route("**/api/v1/runs/*", _flaky)

    text, elapsed = _ask_for_a_hint(page)

    assert len(polls) >= 3, "после 5xx опрос обязан повториться"
    assert "подсказка" in text
    assert elapsed < _MUCH_LESS_THAN_DEADLINE_S


def test_a_hanging_catalog_does_not_hold_the_whole_app(
    page: Any, e2e_server: str, tmp_path: Path
) -> None:
    """Каталог не отвечает — интерфейс всё равно поднимается, и не молча.

    Маршрут ВИСИТ, а не падает: именно в этом дефект. Сетевая ошибка приходит
    быстро и всегда ловилась; ответ, который не приходит вовсе, останавливал
    top-level await, а с ним и выполнение всех импортирующих ``core.js``
    модулей — то есть приложение целиком, без выхода кроме перезагрузки.

    Обработчик маршрута ничего не делает и сразу возвращается: запрос остаётся
    висеть, а клиент Playwright не блокируется. Ждать внутри обработчика
    нельзя — синхронный API останавливает на это время и сам тест, и тогда
    проверка мерила бы собственную блокировку, а не поведение страницы (так
    первая версия этого теста зеленела и на несломанном, и на сломанном коде).

    Признак поднявшегося приложения — видимые маркеры ⟦key⟧: боковое меню есть
    в статическом HTML и появилось бы даже при мёртвом JS.
    """
    hung: list[str] = []

    page.route("**/static/locales/ui.json", lambda route: hung.append(route.request.url))

    page.goto(e2e_server + "/", timeout=_TIMEOUT_MS)
    page.wait_for_function(
        "() => /⟦[a-z0-9_.]+⟧/i.test(document.body.innerText)",
        timeout=_TIMEOUT_MS,
    )

    assert hung, "перехват каталога не сработал — тест проверял бы не то"
