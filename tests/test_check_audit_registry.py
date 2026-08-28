"""Реестр закрытых находок не отстаёт от `main`.

Правило «находка открыта, пока её ID не в реестре» существовало без механизма, и
реестр отстал на 152 записи: PR закрывали находки, не дописывая строку. Тесты
проверяют разбор — то место, где ошибка стоит дороже всего: ложное «закрыта»
хоронит живой дефект, ложное «открыта» переоткрывает сделанное.

В сеть не ходит ни один тест: предмет здесь логика разбора, а не GitHub.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).parent.parent


def _load() -> Any:
    """Загрузить скрипт как модуль: `scripts/` не пакет."""
    path = _ROOT / "scripts" / "check_audit_registry.py"
    spec = importlib.util.spec_from_file_location("check_audit_registry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_audit_registry", module)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _verdict(body: str, fid: str) -> str:
    match = next(m for m in guard.FINDING_ID.finditer(body) if m.group(1) == fid)
    return guard.mention_verdict(body, match)


def test_parse_document_splits_ids_registry_and_rejected() -> None:
    text = (
        "| ID | file:line | Находка | Итог | ✓ |\n"
        "| RUN-1-01 | `a.py:1` | что-то молчит | medium | ✅ |\n"
        "| PY-3-07 | `b.py:2` | вывод обратный | low | ✅ |\n"
        "\n"
        "| RUN-1-01 | что-то молчит | #1200 |\n"
        "\n"
        "| PY-3-07 | вывод обратный | REFUTED | репро недостижим |\n"
    )

    all_ids, registry, rejected = guard.parse_document(text)

    assert all_ids == {"RUN-1-01", "PY-3-07"}
    assert registry == {"RUN-1-01": 1200}
    assert rejected == {"PY-3-07"}


def test_registry_is_found_by_row_shape_not_by_heading() -> None:
    """Переименование раздела не должно отключать проверку молча."""
    text = "## Совсем другой заголовок\n\n| ADD-1-01 | вызов вне try | #900 |\n"

    _, registry, _ = guard.parse_document(text)

    assert registry == {"ADD-1-01": 900}


def test_paragraph_context_survives_dots_in_paths() -> None:
    """Точка в `SECURITY.md` рвала окно так, что «остаются» терялось."""
    body = (
        "**Границы.** Проверено на Linux/bwrap. Из подэпика #986 остаются: "
        "SBX-4-01 (сломанный bwrap), SEC-2-02 (монтируется весь venv вопреки "
        "SECURITY.md) и SEC-2-03 (Windows, внуки переживают обрыв)."
    )

    assert _verdict(body, "SEC-2-03") == "remains"
    assert _verdict(body, "SBX-4-01") == "remains"


def test_heading_about_leftovers_outweighs_row_shape() -> None:
    """Под «Что осталось» лежит тот же список «ID — что не так», что и под «Что сделано»."""
    body = (
        "## Что осталось в файле\n\n"
        "`MTX-5-01` — одни и те же ожидания дают разный вердикт в форматах 1 и 3."
    )

    assert _verdict(body, "MTX-5-01") == "remains"


def test_partial_closure_is_not_a_closure() -> None:
    """«(часть про URL)» — половина находки; реестр держит только закрытые целиком."""
    body = "Закрывает 5 находок из 12: **INS-5-02**, **READER-1-04** (часть про URL), **ED-2-06**."

    assert _verdict(body, "READER-1-04") == "remains"
    assert _verdict(body, "INS-5-02") == "closes"


def test_finding_as_subject_is_a_closure() -> None:
    """«- **ID** — что было»: «остаётся» в абзаце относится к другому."""
    body = (
        "- **INS-3-04** — `python -m stepik_grader.ide` был тихим no-op с кодом 0. "
        "Модуль остаётся библиотекой, но прямой запуск теперь печатает в stderr."
    )

    assert _verdict(body, "INS-3-04") == "closes"


def test_paperwork_pull_requests_do_not_close_anything() -> None:
    """`docs(audit)` ведёт сам документ и перечисляет ID десятками."""
    pulls = [
        {
            "number": 1366,
            "title": "docs(audit): реестр закрытых находок сверен с main",
            "body": "Закрывает пробел учёта. В реестр не попали BRW-1-03 и REV-7-04.",
        }
    ]

    assert guard.closing_mentions(pulls, {"BRW-1-03", "REV-7-04"}) == {}


def test_later_pull_request_wins() -> None:
    """Сначала «остаётся», потом фикс: побеждает поздний."""
    pulls = [
        {"number": 1252, "title": "fix(guards): аудит", "body": "Что остаётся: `DES-1-04` — цвет."},
        {
            "number": 1276,
            "title": "fix(web): контраст",
            "body": "Находка `DES-1-04` закрыта здесь.",
        },
    ]

    assert guard.closing_mentions(pulls, {"DES-1-04"}) == {"DES-1-04": 1276}


def test_known_set_limits_the_search() -> None:
    """ID, уже стоящий в реестре, второй раз не предлагается."""
    pulls = [{"number": 10, "title": "fix: что-то", "body": "Закрывает `RUN-1-01`."}]

    assert guard.closing_mentions(pulls, set()) == {}


def test_live_audit_registry_is_in_sync() -> None:
    """Сам документ аудита: разбор его реестра не должен ломаться на форме строк."""
    for document in guard.audit_documents():
        all_ids, registry, rejected = guard.parse_document(document.read_text(encoding="utf-8"))

        assert registry, f"{document.name}: реестр не распознан — проверка стала бы пустой"
        assert set(registry) <= all_ids, f"{document.name}: в реестре ID, которых нет в таблицах"
        # Находка-дубликат законно стоит в обоих списках: закрыта тем же PR, что и
        # оригинал, и помечена дубликатом (`RUN-5-03` — дубликат `RUN-4-03`).
        assert rejected <= all_ids, f"{document.name}: отклонён ID, которого нет в таблицах"


def test_unreachable_github_is_the_third_outcome(monkeypatch: Any) -> None:
    """«Трекер не прочитан» и «реестр не отстал» — разные исходы (правило 039).

    Ветка прогоняется, а не только пишется: непрогнанная ветка отказа обычно и
    оказывается сломанной — её никто не видел работающей.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise guard.gh_rest.GitHubError("403: прав нет")

    monkeypatch.setattr(guard.gh_rest, "request", refuse)

    assert guard.main(["--repo", "owner/repo"]) == 2
