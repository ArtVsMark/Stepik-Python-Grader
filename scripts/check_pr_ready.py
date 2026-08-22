#!/usr/bin/env python3
"""scripts/check_pr_ready.py — можно ли мержить этот PR (issue #997).

Инцидент, из которого вырос скрипт: PR смержили, когда 9 проверок из 14 были
``queued``/``in_progress``. Проверка была наивной — «нет ``failure`` и нет
``pending`` → мержим», — а сразу после ``git push`` GitHub ещё не успевает
создать check-runs и REST отдаёт **пустой список**. На пустоте условие «нет
красных, нет ожидающих» выполняется идеально.

Отсюда три правила, которые здесь и закодированы:

1. **Пустой или неполный список проверок — это «CI не стартовал», а не «зелено».**
   Поэтому смотрим не только check-runs, но и прогоны Actions для того же
   коммита: пока прогон в ``queued``/``in_progress``, часть джобов ещё не
   существует, и судить по ним нельзя.
2. **Набор проверок сверяется с эталоном** — именами с последнего завершённого
   прогона на ``main``. Отсутствующее имя означает «джоб не создан», и это
   ровно тот случай, который прошлую проверку обманул.
3. **Только REST.** ``gh pr view``/``gh pr checks`` ходят через GraphQL, и
   поллинг в цикле выжигает квоту 5000/час до нуля — посреди работы команды
   ``gh`` просто перестают отвечать. Интервал опроса — не чаще раза в 45-60 с.
   Сами запросы уходят через общий транспорт ``scripts/gh_rest.py`` (issue
   #1242): один модуль на весь конвейер — значит и токен, и распознавание
   исчерпанной квоты, и условные запросы по ``ETag`` одинаковы везде.
4. **Мерж внахлёст запрещён механикой, а не памятью.** В очереди одной
   concurrency-группы GitHub держит ровно один ОЖИДАЮЩИЙ прогон, и каждый новый
   пуш вытесняет предыдущий pending — тот получает ``cancelled``, не начавшись.
   Замер 19.08.2026: шесть мержей подряд дали шесть отменённых прогонов и ни
   одного выполненного, то есть шесть состояний ``main`` уехали без единой
   проверки. Поэтому пока на ``main`` идёт прогон — вердикт «ждать», а не
   «готов». И если последний прогон ``main`` красный, следующий PR не мержится
   тоже: иначе красный ``main`` копит изменения, и разбирать придётся смесь.

   Merge queue закрыла бы это штатно, но она **нам недоступна**: условие — не
   публичность репозитория, а тип владельца (организация либо Enterprise
   Cloud), и у личного аккаунта опции нет вовсе. Поэтому порядок держится
   вариантом B (issue #1235): ветка обновляется из ``main``, ``auto-merge``
   мержит её по зелёному, следующий PR берётся после завершения прогона.

5. **Устаревшая ветка — не «готова»**. PR, отставший от ``main``, проверен на
   состоянии, которого больше нет: его зелёный отвечает про вчера. С включённой
   защитой «Require branches to be up to date» GitHub и сам не даст смержить,
   но гейт обязан говорить это раньше и внятно — иначе ожидание зелёного уходит
   впустую.

Запуск::

    python scripts/check_pr_ready.py 1100          # вердикт по PR
    python scripts/check_pr_ready.py 1100 --json   # то же машинно

Код возврата 0 — можно мержить; 1 — нельзя (причина в выводе).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys
from collections.abc import Callable
from typing import Any

# Сосед по каталогу ``scripts/``, а не пакет: при запуске файлом Python сам
# кладёт его каталог в ``sys.path``, но тесты грузят скрипт по пути через
# ``spec_from_file_location`` — там этого не происходит, и импорт надо
# подстраховать явно.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_attribution
import gh_rest

__all__ = [
    "Verdict",
    "attribution_blockers",
    "bot_author_blockers",
    "branch_is_stale",
    "check_names",
    "default_fetch",
    "evaluate",
    "job_names",
    "latest_by_name",
    "main",
    "main_branch_blockers",
    "metadata_blockers",
    "pending_runs",
    "pull_labels",
    "queue_blockers",
    "workflows_running_on_pull_requests",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_REPO = gh_rest.DEFAULT_REPO

# issue #1231: очередь, из-за которой прогоны main отменялись, принадлежит
# именно этому workflow — его и спрашиваем про занятость ветки.
_CI_WORKFLOW = "ci.yml"

# Заключения, которые не считаются провалом: пропущенный джоб — это условие в
# workflow, а не отказ, и требовать от него `success` значит никогда не мержить.
_OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

Fetch = Callable[[str], Any]


@dataclasses.dataclass(frozen=True)
class Verdict:
    """Готовность PR: вердикт, причины «нет» и сводка по проверкам."""

    ready: bool
    reasons: list[str]
    total_checks: int
    completed: int
    missing: list[str]
    #: То, о чём стоит знать, но что мержу не мешает (issue #1350). Отдельное
    #: поле, а не строка в `reasons`: предупреждение, попавшее в блокеры,
    #: останавливает конвейер там, где чинить может быть нечего, — и его
    #: начинают обходить вместе с настоящими причинами.
    warnings: list[str] = dataclasses.field(default_factory=list)


def default_fetch(path: str) -> Any:
    """GET по REST через общий транспорт ``gh_rest`` (issue #1242).

    Своей реализации запроса здесь больше нет: токен, условные запросы по
    ``ETag`` и распознавание исчерпанной квоты одинаковы для всего конвейера,
    и держать их в двух местах значит однажды починить только одно.

    Ошибки транспорта переводятся в ``RuntimeError`` — тип, который ``main``
    ловил и раньше: вердикт гейта от смены транспорта не меняется. Исчерпанная
    квота при этом остаётся узнаваемой в тексте: «ждать сброса», а не «REST-
    запрос не удался».

    Raises:
        RuntimeError: GitHub недоступен, отказал или квота исчерпана.
    """
    try:
        return gh_rest.request("GET", path).data
    except gh_rest.RateLimited as exc:
        raise RuntimeError(exc.describe()) from exc
    except gh_rest.GitHubError as exc:
        raise RuntimeError(f"REST-запрос не удался ({path}): {exc}") from exc


def check_names(check_runs: dict[str, Any]) -> set[str]:
    """Имена проверок из ответа ``/commits/{sha}/check-runs``."""
    runs = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
    return {str(run.get("name", "")) for run in runs if run.get("name")}


def latest_by_name(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить по одной, самой свежей записи на каждое имя проверки/прогона.

    ``ci.yml`` держит concurrency-группу и гасит устаревшие прогоны, а
    перезапуск (снятие черновика, повторный пуш) создаёт вторую запись с тем же
    именем на том же коммите. В ответе REST обе лежат рядом, и без этого отбора
    отменённый предшественник держал бы PR красным вечно — то есть гейт врал бы
    ровно там, где должен помогать.
    """
    freshest: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name", ""))
        current = freshest.get(name)
        if current is None or _started(item) >= _started(current):
            freshest[name] = item
    return list(freshest.values())


def _started(item: dict[str, Any]) -> tuple[str, int]:
    """Ключ свежести: время старта, при равенстве — идентификатор."""
    return str(item.get("started_at") or item.get("run_started_at") or ""), int(item.get("id") or 0)


def pending_runs(workflow_runs: dict[str, Any]) -> list[str]:
    """Прогоны Actions, которые ещё не завершились (по ним джобы не созданы)."""
    runs = workflow_runs.get("workflow_runs", []) if isinstance(workflow_runs, dict) else []
    return [
        f"{run.get('name', 'workflow')} ({run.get('status')})"
        for run in latest_by_name(runs)
        if run.get("status") != "completed"
    ]


def branch_is_stale(comparison: dict[str, Any]) -> list[str]:
    """Отстала ли ветка PR от базовой (issue #1235, вариант B).

    ``behind_by`` из ``/compare/{base}...{head}`` — сколько коммитов базовой
    ветки в ветку PR не приехало. Больше нуля означает, что зелёный прогон
    отвечает про состояние, которого в ``main`` уже нет.

    Пустой или неразобранный ответ блокировкой не считается: гейт, краснеющий
    на отсутствии данных, обходят.
    """
    behind = comparison.get("behind_by") if isinstance(comparison, dict) else None
    if not isinstance(behind, int) or behind <= 0:
        return []
    return [
        f"ветка отстала от main на {behind} коммит(ов) — обновить "
        "(gh pr update-branch), иначе зелёный отвечает про прошлое состояние"
    ]


def main_branch_blockers(main_runs: dict[str, Any]) -> list[str]:
    """Что на ``main`` мешает мержить прямо сейчас (issue #1231).

    Два случая, и оба про очередь, а не про сам PR:

    - **на ``main`` идёт прогон** — мерж внахлёст вытеснит его ожидающего
      преемника из очереди, и очередное состояние ``main`` останется без
      проверок. Полная матрица идёт 12-15 минут, значит мерж возможен примерно
      раз в четверть часа: осознанная плата за то, что каждое состояние
      проверено на всех трёх ОС;
    - **последний завершённый прогон ``main`` красный** — пока он не починен,
      следующий PR только добавит изменений в смесь, которую придётся разбирать.

    Пустой ответ (прогонов нет вовсе, API недоступен) блокировкой НЕ считается:
    гейт, который краснеет на отсутствии данных, обходят, а он должен помогать.
    """
    runs = main_runs.get("workflow_runs", []) if isinstance(main_runs, dict) else []
    active = [
        f"{run.get('name', 'workflow')} ({run.get('status')})"
        for run in runs
        if run.get("status") != "completed"
    ]
    if active:
        return [
            "на main идёт прогон (" + ", ".join(sorted(active)) + ") — "
            "ждать: мерж внахлёст вытеснит из очереди следующий прогон"
        ]
    completed = [run for run in runs if run.get("status") == "completed"]
    if not completed:
        return []
    newest = max(completed, key=_started)
    if newest.get("conclusion") not in _OK_CONCLUSIONS:
        return [
            f"последний прогон main красный (conclusion={newest.get('conclusion')}) — "
            "чинить его, а не копить поверх изменения"
        ]
    return []


def bot_author_blockers(pull: dict[str, Any]) -> list[str]:
    """Причины отказа, связанные с авторством PR (issue #1280).

    Squash-мерж атрибутирует итоговый коммит **автору pull request**, а не
    автору коммитов ветки. Проверено на PR #1277: коммит писал
    ``Claude <noreply@anthropic.com>``, а в ``main`` он значится за человеком,
    потому что PR открыл человек. Обратное так же верно: PR, открытый ботом,
    запишет в ``main`` автором бота — и участие человека из истории исчезнет
    насовсем, потому что переписать защищённую ветку нечем.

    Поэтому такой PR не мержится, а пересоздаётся от человека: дешевле, чем
    навсегда потерянное авторство.
    """
    raw = pull.get("user")
    author: dict[str, Any] = raw if isinstance(raw, dict) else {}
    login = str(author.get("login", "?"))
    if author.get("type") == "Bot" or login.endswith("[bot]"):
        return [
            f"PR открыт ботом ({login}): после squash автором коммита в main станет он, "
            "а не человек. Пересоздайте PR от своего имени — переписать историю "
            "защищённой ветки потом нельзя"
        ]
    return []


# issue #1329: метки конвейера ставит и снимает автоматика — они про поведение
# очереди, а не про содержание работы, и требовать их от автора незачем.
_PIPELINE_LABELS = frozenset({"merge-when-green", "hold", "needs-rebase", "blocker"})

# Тип работы: та же шкала, что у issue (CLAUDE.md § Метки при заведении issue).
_TYPE_LABELS = frozenset(
    {"bug", "enhancement", "documentation", "tech-debt", "security", "refactor"}
)

# Явное освобождение от связи с задачей: не каждый PR закрывает issue, и
# правило не должно порождать пустышки ради формальности. Отличие явного от
# забытого в том, что его видно.
_NO_ISSUE_RE = re.compile(r"^\s*Без issue:\s*\S", re.IGNORECASE | re.MULTILINE)
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#\d+", re.IGNORECASE)

#: Номер задачи из объявленной связи — та же формулировка, что у `_CLOSES_RE`,
#: но с захватом самого номера.
_CLOSES_NUMBER_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE
)

#: Явное освобождение для частичной работы: «Часть #1341 — пять пластов из
#: пятнадцати». Снимает предупреждение по этому номеру, потому что закрывать
#: задачу целиком нельзя, а связь при этом объявлена честно.
_PARTIAL_RE = re.compile(r"^\s*Часть\s+#(\d+)\s*[—–-]\s*\S", re.MULTILINE)

#: Номер в тексте фрагмента `changelog.d`: формат требует его в скобках —
#: «что изменилось (#1234)».
_FRAGMENT_NUMBER_RE = re.compile(r"\(#(\d+)\)")

#: Каталог фрагментов: только его файлы участвуют в сверке.
_CHANGELOG_DIR = "changelog.d/"


def pull_labels(pull: dict[str, Any]) -> set[str]:
    """Имена меток PR из ответа REST."""
    return {
        str(label.get("name", ""))
        for label in (pull.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    }


def metadata_blockers(pull: dict[str, Any]) -> list[str]:
    """Чего PR не хватает по правилу разметки (issue #1329).

    Правило про метки было записано только для issue, и на PR их не было:
    замер по двенадцати открытым — навигационные метки у четырёх, связь с
    задачей у четырёх. Машина метила PR исправнее человека.

    Вредит это трижды. PR без ``Closes #N`` не закрывает задачу при мерже —
    трекер начинает врать. Приоритет очереди наследуется по той же связи
    (issue #1326), и без неё брать его неоткуда. И зона работы (``area/*``)
    видна до чтения диффа только по метке.

    PR из форка — предупреждение, а не отказ: внешний контрибьютор не обязан
    знать про наши метки, их проставит мейнтейнер при разборе. Здесь такой PR
    просто пропускается: гейт про мерж, а не про воспитание.
    """
    raw_head = pull.get("head")
    head: dict[str, Any] = raw_head if isinstance(raw_head, dict) else {}
    raw_repo = head.get("repo")
    repo: dict[str, Any] = raw_repo if isinstance(raw_repo, dict) else {}
    if repo.get("fork"):
        return []

    reasons: list[str] = []
    labels = pull_labels(pull) - _PIPELINE_LABELS
    if not any(label.startswith("area/") for label in labels):
        reasons.append("нет метки area/* — по ней видно зону работы до чтения диффа")
    if not (labels & _TYPE_LABELS):
        kinds = ", ".join(sorted(_TYPE_LABELS))
        reasons.append(f"нет метки типа работы (одна из: {kinds})")
    body = str(pull.get("body") or "")
    if not _CLOSES_RE.search(body) and not _NO_ISSUE_RE.search(body):
        reasons.append(
            "в теле нет ни «Closes #N», ни строки «Без issue: <причина>» — "
            "без связи задача не закроется при мерже, а приоритет очереди брать неоткуда"
        )
    return reasons


def link_completeness_warnings(
    pull: dict[str, Any],
    files: list[dict[str, Any]],
    *,
    number: int | None = None,
) -> list[str]:
    """Задачи, сделанные этим PR, но не объявленные в его теле (issue #1350).

    Наличие связи гейт требовал и раньше (issue #1329), а **полноту** — никто.
    Сборный PR проходил, объявив одну задачу из пяти: остальные оставались
    открытыми, хотя сделаны, и узнать об этом можно было только чтением диффа.
    Живой прецедент — PR #1345: шесть фрагментов ``changelog.d`` называли пять
    задач, в теле стояла одна.

    Сверять есть что без единого лишнего запроса и без эвристик: номер задачи
    уже лежит в тексте фрагмента, потому что формат требует его в скобках. Это
    разность двух множеств — номера из добавленных фрагментов минус номера из
    ``Closes``/``Fixes``/``Resolves``.

    **Предупреждение, а не отказ** (правило 051): частичное закрытие бывает
    законным, и номер во фрагменте может оказаться номером самого PR. Первое
    снимается строкой ``Часть #N — <что именно>``, второе — отбрасыванием
    собственного номера.
    """
    declared = {int(match) for match in _CLOSES_NUMBER_RE.findall(str(pull.get("body") or ""))}
    excused = {int(match) for match in _PARTIAL_RE.findall(str(pull.get("body") or ""))}

    mentioned: set[int] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "")
        if not filename.startswith(_CHANGELOG_DIR):
            continue
        if item.get("status") == "removed":
            # Сборка при релизе удаляет фрагменты — их номера ничего не
            # объявляют об этой работе.
            continue
        patch = str(item.get("patch") or "")
        added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+"))
        mentioned.update(int(match) for match in _FRAGMENT_NUMBER_RE.findall(added))

    if number is not None:
        # Формат фрагмента допускает номер самого PR — он не про задачу.
        mentioned.discard(number)

    unannounced = sorted(mentioned - declared - excused)
    if not unannounced:
        return []

    listed = ", ".join(f"#{item}" for item in unannounced)
    in_body = ", ".join(f"#{item}" for item in sorted(declared)) if declared else "ничего"
    return [
        f"во фрагментах changelog.d названы {listed} — в теле объявлено {in_body}. "
        "При мерже эти задачи не закроются, и трекер разойдётся с фактом. "
        "Добавьте «Closes #N» либо «Часть #N — <что именно сделано>», "
        "если задача закрыта не целиком"
    ]


def attribution_blockers(commits: list[dict[str, Any]]) -> list[str]:
    """Кем подпишется итоговый коммит после squash (issue #1343).

    Squash не переносит коммит, а составляет новый: автором становится автор
    pull request, а прежних авторов коммитов ветки платформа дописывает
    трейлерами ``Co-authored-by``. Берёт она их из git-идентичности окна, в
    котором работали, — и у облачного контейнера это ``Claude``, а не
    согласованное ``Claude Opus 5``. Так один соавтор и оказался в истории под
    двумя именами.

    Значит проверять надо не тело PR (трейлеры там платформа допишет сама), а
    авторов коммитов ветки: они известны ДО слияния, а после слияния уже
    необратимы — ``main`` защищена, force-push запрещён.

    Внешние соавторы под правило не подпадают (:func:`check_attribution.is_agent`):
    их строки законны, как и английский язык в их PR.
    """
    identities: set[check_attribution.Identity] = set()
    for item in commits:
        if not isinstance(item, dict):
            continue
        raw_commit = item.get("commit")
        commit: dict[str, Any] = raw_commit if isinstance(raw_commit, dict) else {}
        raw_author = commit.get("author")
        author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
        name = str(author.get("name", "")).strip()
        email = str(author.get("email", "")).strip()
        if name and email:
            identities.add(check_attribution.Identity(name, email))
        identities.update(check_attribution.trailer_identities(str(commit.get("message", ""))))

    wrong = check_attribution.mismatched(identities, agents_only=True)
    if not wrong:
        return []
    listed = ", ".join(str(identity) for identity in wrong)
    return [
        f"подпись не из согласованного списка: {listed} — после squash платформа "
        "впишет её в main трейлером, и переписать это будет нечем "
        "(python scripts/check_attribution.py --check-branch)"
    ]


def evaluate(
    pull: dict[str, Any],
    workflow_runs: dict[str, Any],
    check_runs: dict[str, Any],
    expected: set[str],
    *,
    main_blockers: list[str] | None = None,
    commits: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
    number: int | None = None,
) -> Verdict:
    """Собрать вердикт из состояния PR, прогонов Actions и check-runs.

    ``main_blockers`` (issue #1231) — причины, лежащие вне PR: занятая или
    красная ``main``. Приходят готовыми из ``main_branch_blockers``.
    """
    reasons: list[str] = list(main_blockers or [])

    if pull.get("state") != "open":
        reasons.append(f"PR не открыт (state={pull.get('state')})")
    if pull.get("draft"):
        reasons.append("PR — черновик")
    reasons.extend(bot_author_blockers(pull))
    reasons.extend(metadata_blockers(pull))
    reasons.extend(attribution_blockers(commits or []))
    warnings = link_completeness_warnings(pull, files or [], number=number)
    mergeable_state = pull.get("mergeable_state")
    if mergeable_state not in {"clean", "unstable", "has_hooks"}:
        reasons.append(f"ветка не готова к мержу (mergeable_state={mergeable_state})")
    if pull.get("mergeable") is False:
        reasons.append("GitHub сообщает о конфликте с базовой веткой")

    raw_runs = workflow_runs.get("workflow_runs", []) if isinstance(workflow_runs, dict) else []
    runs = latest_by_name(raw_runs)
    if not runs:
        reasons.append("для этого коммита нет ни одного прогона Actions — CI ещё не стартовал")
    still_running = pending_runs(workflow_runs)
    if still_running:
        reasons.append("прогоны не завершены: " + ", ".join(sorted(still_running)))
    failed_runs = [
        str(run.get("name", "workflow"))
        for run in runs
        if run.get("status") == "completed" and run.get("conclusion") not in _OK_CONCLUSIONS
    ]
    if failed_runs:
        reasons.append("красные прогоны: " + ", ".join(sorted(failed_runs)))

    raw_checks = check_runs.get("check_runs", []) if isinstance(check_runs, dict) else []
    listed = latest_by_name(raw_checks)
    if not listed:
        reasons.append("список проверок пуст — это «не стартовало», а не «зелено»")
    unfinished = [
        f"{run.get('name')} ({run.get('status')})"
        for run in listed
        if run.get("status") != "completed"
    ]
    if unfinished:
        reasons.append("проверки не завершены: " + ", ".join(sorted(unfinished)))
    red = [
        str(run.get("name"))
        for run in listed
        if run.get("status") == "completed" and run.get("conclusion") not in _OK_CONCLUSIONS
    ]
    if red:
        reasons.append("красные проверки: " + ", ".join(sorted(red)))

    missing = sorted(expected - check_names(check_runs))
    if missing:
        reasons.append("не создано проверок из эталонного набора: " + ", ".join(missing))

    completed = sum(1 for run in listed if run.get("status") == "completed")
    return Verdict(
        ready=not reasons,
        reasons=reasons,
        total_checks=len(listed),
        completed=completed,
        missing=missing,
        warnings=warnings,
    )


def job_names(jobs: dict[str, Any]) -> set[str]:
    """Имена джобов из ответа ``/actions/runs/{id}/jobs``."""
    listed = jobs.get("jobs", []) if isinstance(jobs, dict) else []
    return {str(job.get("name", "")) for job in listed if job.get("name")}


def workflows_running_on_pull_requests(workflows_dir: pathlib.Path) -> set[str]:
    """Имена workflow'ов (`name:`), у которых есть триггер ``pull_request``.

    Читается локально, а не через API: список триггеров живёт в репозитории и
    меняется вместе с ним. Нужно, чтобы в эталон не попадали прогоны, которых
    на PR не бывает по определению — иначе гейт краснеет всегда, а гейт,
    который краснеет всегда, обходят.
    """
    names: set[str] = set()
    for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        body, _, _ = text.partition("\njobs:")
        if not re.search(r"^\s{2}pull_request:", body, re.MULTILINE):
            continue
        title = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        if title:
            names.add(title.group(1).strip().strip("'\""))
    return names


def _expected_names(fetch: Fetch, repo: str, workflows_dir: pathlib.Path) -> set[str]:
    """Эталонный набор — джобы тех прогонов ``main``, что бывают и на PR.

    Эталон берётся из живого состояния, а не из константы в коде: список джобов
    меняется вместе с ``ci.yml``, а зашитое число устаревает молча. Прогоны,
    которые на PR не запускаются (у их workflow нет триггера ``pull_request``),
    из эталона исключаются — иначе недостающим числится то, чего быть и не
    должно.
    """
    allowed = workflows_running_on_pull_requests(workflows_dir)
    try:
        sha = str(fetch(f"repos/{repo}/commits/main").get("sha", ""))
        if not sha:
            return set()
        runs = fetch(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100")
        names: set[str] = set()
        for run in runs.get("workflow_runs", []):
            if allowed and str(run.get("name", "")) not in allowed:
                continue
            jobs = fetch(f"repos/{repo}/actions/runs/{run.get('id')}/jobs?per_page=100")
            names |= job_names(jobs)
        return names
    except RuntimeError:
        return set()


def _force_utf8_stdio() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли (issue #1108).

    Вердикт готовности русский и со стрелками ``→``; в консоли cp1251 такой
    вывод падал ``UnicodeEncodeError``, и вместо ответа «готов / не готов»
    гейт печатал собственный трейсбек — на нём же спотыкался и ``--help``.

    Собственная копия приёма, а не общий ``stepik_grader.stdio_encoding``:
    скрипт принципиально не импортирует пакет — он работает и там, где пакет не
    установлен. No-op на потоках без ``reconfigure`` (перехваченных pytest).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def queue_blockers(report: gh_rest.QueueReport, number: int) -> list[str]:
    """Причины ждать из-за очереди мержа (issue #1282).

    Обновляется и мержится только голова очереди: массовое обновление
    гарантирует, что обновлённые PR устареют снова следующим же мержем, и число
    холостых прогонов растёт квадратично от длины очереди. Поэтому «зелёный, но
    второй в очереди» — это «ждать», а не «готов».

    Пустой список, если PR первый или его в очереди нет вовсе: причины «нет» в
    последнем случае уже назвал локальный разбор проверок.
    """
    ahead = report.ahead_of(number)
    if not ahead:
        return []
    names = ", ".join(f"#{item}" for item in ahead)
    return [
        f"в очереди впереди: {names} — из main обновляется только её голова, "
        "остальные стоят неподвижно (python scripts/gh_rest.py queue)"
    ]


def main(argv: list[str] | None = None, *, fetch: Fetch | None = None) -> int:
    """Напечатать вердикт готовности PR; 0 — можно мержить."""
    # Раньше любой печати, включая справку argparse: описание флагов русское.
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pull", type=int, help="номер pull request")
    parser.add_argument("--repo", default=_REPO, help="owner/repo (по умолчанию текущий проект)")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    call = fetch or default_fetch
    try:
        pull = call(f"repos/{args.repo}/pulls/{args.pull}")
        sha = str(pull.get("head", {}).get("sha", ""))
        workflow_runs = call(f"repos/{args.repo}/actions/runs?head_sha={sha}&per_page=100")
        check_runs = call(f"repos/{args.repo}/commits/{sha}/check-runs?per_page=100")
        # issue #1231: состояние очереди на main. Отдельный дешёвый REST-запрос
        # — фильтр по workflow и ветке делает сам GitHub.
        main_runs = call(
            f"repos/{args.repo}/actions/workflows/{_CI_WORKFLOW}/runs"
            "?branch=main&event=push&per_page=10"
        )
        # issue #1235: отставание ветки от базовой. `mergeable_state` показывает
        # `behind` только при включённой защите «Require branches to be up to
        # date»; спрашиваем напрямую, чтобы гейт говорил это и до её включения.
        base = str(pull.get("base", {}).get("ref", "main"))
        comparison = call(f"repos/{args.repo}/compare/{base}...{sha}")
        # issue #1343: кем подпишется итоговый коммит. Отдельный дешёвый REST-
        # запрос: авторы коммитов ветки — это и есть будущие трейлеры squash.
        commits = call(f"repos/{args.repo}/pulls/{args.pull}/commits?per_page=100")
        # issue #1350: файлы PR нужны ради номеров задач в добавленных
        # фрагментах changelog.d. Отдельный REST-запрос (1 из 5000), тогда как
        # тот же вопрос через GraphQL стоил бы ~300 points.
        files = call(f"repos/{args.repo}/pulls/{args.pull}/files?per_page=100")
    except RuntimeError as exc:
        print(f"Не удалось опросить GitHub: {exc}", file=sys.stderr)
        return 1

    expected = _expected_names(call, args.repo, _ROOT / ".github" / "workflows")
    verdict = evaluate(
        pull,
        workflow_runs,
        check_runs,
        expected,
        main_blockers=main_branch_blockers(main_runs) + branch_is_stale(comparison),
        commits=commits if isinstance(commits, list) else [],
        files=files if isinstance(files, list) else [],
        number=args.pull,
    )

    # issue #1282: очередь спрашивается ТОЛЬКО у PR, готового по своим
    # проверкам. Пока он и так не готов, его место в очереди ничего не решает, а
    # запрос стоил бы по обращению на каждый открытый PR.
    if verdict.ready:
        try:
            waiting = queue_blockers(gh_rest.merge_queue(args.repo), args.pull)
        except gh_rest.GitHubError as exc:
            # Очередь — уточнение поверх вердикта, а не его основание. Молчать о
            # сбое нельзя, но и ронять готовый PR из-за недоступного списка тоже:
            # гейт, который краснеет на отсутствии данных, обходят.
            print(
                f"Очередь опросить не удалось ({exc}) — вердикт без учёта очереди.", file=sys.stderr
            )
            waiting = []
        if waiting:
            verdict = dataclasses.replace(verdict, ready=False, reasons=verdict.reasons + waiting)

    if args.json:
        print(json.dumps(dataclasses.asdict(verdict), ensure_ascii=False))
        return 0 if verdict.ready else 1

    print(f"PR #{args.pull}: проверок {verdict.completed}/{verdict.total_checks} завершено")
    # Предупреждения печатаются и у готового PR: они не мешают мержу, но узнать
    # о них надо ДО него — после слияния задача уже не закроется сама.
    for warning in verdict.warnings:
        print(f"  ⚠ {warning}")
    if verdict.ready:
        print("Готов к мержу: все проверки созданы, завершены и зелёные.")
        return 0
    print("Мержить нельзя:")
    for reason in verdict.reasons:
        print(f"  — {reason}")
    print("\nОпрашивать не чаще раза в 45-60 с: частый поллинг выжигает квоту GitHub.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
