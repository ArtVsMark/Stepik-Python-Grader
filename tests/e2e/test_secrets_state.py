"""Загрузчик называет проблему с `secrets.json`, а не молчит (issue #1213).

Раньше отсутствие файла давало общий отказ: развилка «настроить впервые / у
меня есть secrets.json» — и всё. Ни имени файла, ни пути, по которому искали,
ни причины. Пользователь выбирал наугад, хотя сервер всё это знал: статус
различает состояния, а путь лежит в ответе с самого начала.

Проверяется в браузере, потому что дефект жил именно там: серверная часть была
на месте, а ветки `no_secrets` в статике не было вовсе. Тест на API это бы не
поймал — он и не ловил.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PANEL = "#auth-panel"
_TIMEOUT_MS = 15000


def _open_downloader(page: Any, server: str) -> str:
    """Открыть раздел загрузчика и вернуть текст панели доступа."""
    page.goto(server + "/")
    page.click('.sidebar-item[data-section="downloader"]')
    page.wait_for_selector("#view-downloader:not([hidden])", timeout=_TIMEOUT_MS)
    page.wait_for_selector(f"{_PANEL} .auth-problem, {_PANEL} .auth-ok", timeout=_TIMEOUT_MS)
    return str(page.inner_text(_PANEL))


class TestPanelNamesTheProblem:
    def test_missing_file_is_named_with_its_path(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """Главный случай: файла нет — сказать, какого и где искали."""
        text = _open_downloader(page, e2e_server)

        assert "secrets.json" in text
        assert "не найден" in text
        assert str(tmp_path / "secrets.json") in text, "путь поиска не показан"

    def test_broken_json_is_a_different_message(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """«Файла нет» и «файл есть, но не читается» чинятся по-разному."""
        (tmp_path / "secrets.json").write_text("{ это не json", encoding="utf-8")

        text = _open_downloader(page, e2e_server)

        assert "не найден" not in text
        assert "прочитать" in text

    def test_incomplete_credentials_say_which_fields(
        self, page: Any, e2e_server: str, tmp_path: Path
    ) -> None:
        """Третий случай: JSON разобран, но кредов неполно — названы поля."""
        (tmp_path / "secrets.json").write_text(json.dumps({"client_id": "a"}), encoding="utf-8")

        text = _open_downloader(page, e2e_server)

        assert "client_secret" in text

    def test_the_way_out_is_still_offered(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """Объяснение встаёт НАД развилкой, а не вместо неё — выход остаётся."""
        _open_downloader(page, e2e_server)

        assert page.locator("#auth-choice-wizard").is_visible()
        assert page.locator("#auth-choice-existing").is_visible()

    def test_panel_never_shows_the_secret(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """Граница из SECURITY.md: на экране состояние, а не содержимое файла.

        Прецедент уже был — `?path=secrets.json` отдавал `client_secret` и
        токен. Панель показывает путь и причину; всё, что внутри файла, там
        появиться не должно ни при каком состоянии.
        """
        (tmp_path / "secrets.json").write_text(
            json.dumps({"client_id": "a", "client_secret": "секрет-в-файле"}),
            encoding="utf-8",
        )

        text = _open_downloader(page, e2e_server)

        assert "секрет-в-файле" not in text

    def test_english_locale_says_the_same(self, page: Any, e2e_server: str, tmp_path: Path) -> None:
        """Подсказка не теряется при смене языка — она из локали, не из кода."""
        _open_downloader(page, e2e_server)
        # Именно кнопка EN: обработчик игнорирует клик по уже активному языку,
        # и `.lang-btn` без уточнения попадает в RU — тест «проходил» бы, ничего
        # не переключив.
        page.click('.lang-btn[data-lang="en"]')
        # Ждём ПОЯВЛЕНИЯ нужного текста, а не исчезновения старого. Отрицательное
        # условие выполняется на любом промежуточном состоянии — и выполнялось:
        # смена языка перезапрашивает статус, панель на секунду показывает
        # «Checking access to Stepik…», ожидание заканчивалось там, и тест падал
        # на этой заглушке. Локально фетч успевал, в CI — нет.
        page.wait_for_function(
            "() => document.querySelector('#auth-panel').innerText.includes('not found')",
            timeout=_TIMEOUT_MS,
        )

        text = str(page.inner_text(_PANEL))

        assert "not found" in text
        assert str(tmp_path / "secrets.json") in text
