"""viewmodels.py — построение JSON-ответов веб-интерфейса из ядра грейдера.

Архитектурный слой: Application/UI (web-адаптер). Переиспользует
``core/grader_core`` (``run_tests``/``run_benchmark``), ``core/test_loader``
(``find_all_solution_files``/``resolve_test_dir``),
``core/microbench_runner`` (``apply_relative_ranking``/``apply_reference_ranking``,
issue #397 — ранжирование живёт в core, не дублируется) и
``core/reporter.fmt_time`` — логика грейдинга и форматирования не дублируется.
``web → core`` ациклично.
"""

from __future__ import annotations

import functools
import pathlib
import re
import threading
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stepik_grader.config import CONFIG
from stepik_grader.core import history, history_recording, lint
from stepik_grader.core.cache import hash_solution
from stepik_grader.core.error_glossary import resolve_error_hint
from stepik_grader.core.grader_core import (
    MUCH_SLOWER_THRESHOLD,
    SIMILAR_THRESHOLD,
    run_benchmark,
    run_microbench_mode,
    run_tests,
)
from stepik_grader.core.microbench_runner import (
    apply_reference_ranking,
    apply_relative_ranking,
)
from stepik_grader.core.mtime_cache import MtimeCache
from stepik_grader.core.reporter import fmt_time
from stepik_grader.core.test_loader import (
    collect_grouped_files,
    find_all_solution_files,
    is_solution_file,
    load_test_cases,
    resolve_test_dir,
)
from stepik_grader.glossary.detector import MissingConceptDetector
from stepik_grader.glossary.json_provider import (
    GlossaryError,
    JsonGlossaryProvider,
    append_missing_entries,
)
from stepik_grader.web.i18n import DEFAULT_LANG, message_fields, render_message

if TYPE_CHECKING:
    from stepik_grader.core.result import CaseResult

# Вердикты-"ошибки" (в отличие от AC) — ErrorCard-поля (severity/stderr/
# suggestions/...) заполняются только для них (issue #125, web-current.md §
# «Модель error cards»).
_FAILURE_VERDICTS = frozenset({"WA", "RE", "TLE"})

__all__ = [
    "estimate_run_count",
    "grade_benchmark",
    "grade_microbench",
    "grade_path",
    "list_solutions",
    "read_source",
    "save_solution",
]

_TASK_NUM_RE = re.compile(r"^(task\d*)_(\d+)\.py$")


def _rel(path: pathlib.Path, base: pathlib.Path) -> str:
    """Путь относительно base (для компактного отображения), с fallback."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return path.name


def _resolve_solutions(
    path: pathlib.Path, *, lang: str = DEFAULT_LANG
) -> tuple[str, pathlib.Path, list[pathlib.Path]] | dict[str, Any]:
    """Вернуть (kind, base, solutions) для файла/папки или error-dict.

    kind — "file" | "dir". Общий вход для обоих режимов грейдинга.
    """
    p = path.expanduser()
    if p.is_file():
        return "file", p.parent, [p]
    if p.is_dir():
        solutions = find_all_solution_files(p)
        if not solutions:
            return {
                "kind": "error",
                **message_fields("solutions_not_found", lang, path=str(path)),
                "rows": [],
            }
        return "dir", p, solutions
    return {"kind": "error", **message_fields("path_not_found", lang, path=str(path)), "rows": []}


def _resolve_group_test_dir(folder_path: pathlib.Path) -> pathlib.Path:
    """Тесты для ГРУППЫ решений (папки), issue #187 — микробенч.

    Дубликат ветки ``is_dir=True`` из ``cli.__init__._resolve_test_dir_from_input``,
    не импортируется из ``cli``: ``web``/``cli`` — независимые адаптеры над
    ``core`` (см. docs/architecture.md), импорт создал бы нежелательное ребро
    DAG ``web → cli``. Логика (`<dir>/tests/` → Format-3 `input.txt`+`output.txt`
    → сама папка как fallback) достаточно проста, чтобы держать копию.
    """
    p = folder_path
    if (p / "tests").is_dir():
        return p / "tests"
    if (p / "input.txt").exists() and (p / "output.txt").exists():
        return p
    return p


def estimate_run_count(solutions: list[pathlib.Path], *, kind: str, repeats: int = 1) -> int:
    """Оценить, сколько раз ``run_single_test()`` будет вызван для ``solutions``
    (issue #262) — дешёвый предрасчёт total для async job-прогресса
    (``web/runs.py``), только файловый ввод-вывод (``load_test_cases()``),
    без запуска subprocess.

    ``kind="bench"`` — умножает число кейсов каждого решения на ``repeats``
    (соответствует гранулярности тика ``run_benchmark`` — раз на повтор);
    ``kind="tests"`` (issue #297, корректность режима 1) — то же, что bench с
    ``repeats=1``: раз на тест-кейс (``run_tests`` тикает так же);
    ``kind="microbench"`` — единица, так как ``run_microbench_mode`` тикает
    раз на решение целиком (грубее, см. её докстринг), не на кейс.
    Решения без резолвящегося ``test_dir`` пропускаются (0 запусков — та же
    ветка, что даёт ``{"error": ..., "runs": 0}`` в ``run_benchmark``).
    """
    if kind == "microbench":
        return len(solutions)
    total = 0
    for sol in solutions:
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not test_dir.is_dir():
            continue
        total += len(load_test_cases(test_dir)) * max(1, repeats)
    return total


def _wa_suggestion(actual: str, expected: str, *, lang: str = DEFAULT_LANG) -> str | None:
    """Курированная (не AI) эвристика для WA — одна подсказка по форме вывода.

    Приоритет: сначала не-UTF-8 (более специфичная и частая причина «странного»
    diff), затем хвостовые пробелы/CRLF.
    """
    # issue #301: '�' (U+FFFD REPLACEMENT CHARACTER, тот самый «�») в
    # actual означает, что решение писало в stdout не-UTF-8 байты, а runner
    # декодировал их с заменами (errors="replace" — сознательно, не меняется).
    # Diff с «�» сам по себе причину не объясняет — даём явную подсказку.
    if "�" in actual:
        return render_message("output_invalid_utf8", lang)
    if actual and expected and actual != expected and actual.rstrip() == expected.rstrip():
        return render_message("wa_whitespace_suggestion", lang)
    return None


def _error_card_actions(
    *, verdict: str, stdin: str, actual: str, glossary_ids: list[str]
) -> list[str]:
    """MVP-набор action cards для кейса (issue #125) — только 5 реализованных id.

    Никогда не возвращает ``create_test``/``compare_solutions`` — они вне
    скоупа #125 (design-only, см. docs/web-design.md § Action cards, отложенные).
    """
    actions = ["run_again"]
    if stdin:
        actions.append("copy_input")
    if actual:
        actions.append("copy_output")
    if verdict in _FAILURE_VERDICTS:
        actions.append("explain_error")
    if glossary_ids:
        actions.append("open_glossary")
    return actions


# issue #404: search-термины store'а кешируются по mtime. Раньше каждый RE-кейс
# через _queue_missing_concept заново читал и разбирал всю базу (~1.3 МБ) ради
# набора известных терминов; теперь — один разбор на неизменный файл, инвалидация
# по mtime (тот же MtimeCache, что у glossary_adapter #339 и rules #345).
# Детектор строит собственный нормализованный набор и лишь читает known (см.
# detector._normalize_known), поэтому общий set безопасно шарить между потоками.
_TERMS_CACHE: MtimeCache[set[str]] = MtimeCache()


def _known_glossary_terms() -> set[str]:
    """Search-термины настроенной локальной базы (пусто, если store не задан).

    Кешируется по mtime store'а (issue #404): отсутствующий store → пустое
    множество; битый store (``GlossaryError``) → тоже пустое (best-effort, как
    и прежде) и НЕ кешируется как ошибка (``on_error`` у MtimeCache).
    """
    store = CONFIG.glossary_store
    if not store:
        return set()
    p = pathlib.Path(store)
    terms = _TERMS_CACHE.get_or_load(
        f"store:{p}",
        [p],
        lambda: JsonGlossaryProvider.load(p).known_terms(),
        on_error=GlossaryError,
    )
    return terms if terms is not None else set()


def _queue_missing_concept(
    error_text: str, *, source: str, missing_queue_path: pathlib.Path
) -> None:
    """Best-effort J7: RE с неизвестным исключением → очередь пополнения глоссария.

    Best-effort и defensive (issue #125, как и опциональный кэш #56): плохой/
    незаписываемый путь к очереди не должен ронять грейдинг.
    """
    try:
        entry = MissingConceptDetector().detect_from_error(
            error_text, known=_known_glossary_terms(), source=source, verdict="RE"
        )
        if entry is not None:
            append_missing_entries(missing_queue_path, [entry])
    except (GlossaryError, OSError):
        pass


def _case_view(
    index: int,
    case: CaseResult,
    *,
    stdin: str = "",
    source: str = "",
    missing_queue_path: pathlib.Path | None = None,
    lang: str = DEFAULT_LANG,
) -> dict[str, Any]:
    """Представление одного тест-кейса для UI — ErrorCard для WA/RE/TLE (issue #125)."""
    error = case.get("error", "")
    verdict = case.get("verdict") or ("RE" if error else "?")
    passed = bool(case.get("passed"))
    actual = "\n".join(case.get("output") or [])

    # issue #72/#356: карточка ошибки — тип исключения, пояснение, ссылка на
    # глоссарий; из общей базы (bundled JSON → компактная карта fallback), тот
    # же резолвер, что у CLI-репортера.
    entry = resolve_error_hint(error) if error else None
    glossary_ids = [entry.anchor] if entry is not None and verdict == "RE" else []

    view: dict[str, Any] = {
        "n": index,
        "case_n": index,
        "verdict": verdict,
        "time": round(case.get("time", 0.0), 4),
        "error": error,
        # diff показываем только для непрошедших — иначе пусто.
        "diff": "" if passed else case.get("diff", ""),
        "stdin": stdin,
        "actual": actual,
        "actions": _error_card_actions(
            verdict=verdict, stdin=stdin, actual=actual, glossary_ids=glossary_ids
        ),
    }
    if entry is not None:
        view["glossary"] = {
            "exception": entry.exception,
            "hint": entry.hint,
            "url": entry.url,
        }

    if verdict in _FAILURE_VERDICTS:
        view["severity"] = "warning" if verdict == "TLE" else "error"
        suggestions: list[str] = []
        if verdict == "RE" and entry is not None:
            suggestions = [entry.hint]
        elif verdict == "TLE":
            suggestions = [render_message("tle_suggestion", lang)]
        elif verdict == "WA":
            expected = "\n".join(case.get("expected") or [])
            hint = _wa_suggestion(actual, expected, lang=lang)
            if hint:
                suggestions = [hint]
        view["suggestions"] = suggestions

    if verdict == "WA":
        view["expected"] = "\n".join(case.get("expected") or [])
    if verdict in ("RE", "TLE"):
        view["stderr"] = error
        view["exit_code"] = case.get("exit_code")
    if verdict == "TLE":
        view["timeout_s"] = CONFIG.timeout_seconds
    if verdict == "RE":
        view["glossary_ids"] = glossary_ids
        if not glossary_ids:
            # Неизвестное исключение — нет карточки в локальной базе (J7).
            default_queue_path = pathlib.Path(CONFIG.glossary_missing_queue)
            _queue_missing_concept(
                error,
                source=source,
                missing_queue_path=missing_queue_path or default_queue_path,
            )

    return view


def list_solutions(path: pathlib.Path, *, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Найти файлы-решения в папке — пикер режима 1 «Один файл» (issue #125-fix).

    Без грейдинга: только листинг, чтобы UI дал выбрать один конкретный файл
    перед запуском. Возвращает {"kind": "dir", "base", "files": [полные пути]}
    либо {"kind": "error", "message", "message_id", "message_params", "files": []}.
    """
    p = path.expanduser()
    if not p.is_dir():
        return {
            "kind": "error",
            **message_fields("folder_not_found", lang, path=str(path)),
            "files": [],
        }
    solutions = find_all_solution_files(p)
    if not solutions:
        return {
            "kind": "error",
            **message_fields("solutions_not_found", lang, path=str(path)),
            "files": [],
        }
    return {"kind": "dir", "base": str(p), "files": [str(s) for s in solutions]}


def read_source(path: pathlib.Path, *, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Прочитать исходник файла-решения — показ кода в режиме 1 (issue #125-fix).

    Чтение произвольного локального пути не расширяет threat model:
    ``grade_path``/``grade_benchmark`` уже исполняют произвольные локальные
    ``.py``-файлы в subprocess без sandbox (см. CLAUDE.md) — чтение текста
    строго безопаснее уже существующей возможности.
    """
    p = path.expanduser()
    try:
        source = p.read_text(encoding=CONFIG.encoding)
        mtime = p.stat().st_mtime
    except (OSError, UnicodeDecodeError) as exc:
        # issue #423: не-UTF8 файл (cp1251/бинарь) даёт UnicodeDecodeError
        # (подкласс ValueError, не OSError) — вернуть JSON-ошибку, а не ронять
        # обработчик 500/RemoteDisconnected (кириллица на Windows — типовой случай).
        return {"kind": "error", **message_fields("file_read_failed", lang, error=str(exc))}
    # ``mtime`` — baseline для optimistic locking режима 1 (issue #297): фронтенд
    # запоминает его при загрузке файла и присылает обратно в save_solution как
    # ``expected_mtime``, чтобы поймать «файл изменился на диске с момента
    # загрузки» (например, другое окно грейдера сохранило туда же).
    return {"kind": "file", "path": str(p), "source": source, "mtime": mtime}


def _next_solution_filename(folder: pathlib.Path) -> str:
    """Следующее свободное имя файла-решения в прямых детях ``folder``.

    Не рекурсивно — режим 1 сохраняет ровно в показанную пользователем
    папку, без попытки угадать вложенную подпапку (issue #125-fix). Маска
    та же, что у ``is_solution_file``/downloader (``task<опц.цифры>_<M>.py``):
    если в папке уже есть ``task4_1.py``/``task4_2.py`` — расширяет ту же
    серию (``task4_3.py``), а не начинает ``task_1.py`` с нуля.
    """
    matches = [
        _TASK_NUM_RE.match(f.name)
        for f in folder.iterdir()
        if f.is_file() and is_solution_file(f.name)
    ]
    numbered = [m for m in matches if m]
    if not numbered:
        return "task_1.py"
    prefix = Counter(m.group(1) for m in numbered).most_common(1)[0][0]
    max_n = max(int(m.group(2)) for m in numbered if m.group(1) == prefix)
    return f"{prefix}_{max_n + 1}.py"


def save_solution(
    folder: pathlib.Path,
    path: pathlib.Path | None,
    code: str,
    *,
    lang: str = DEFAULT_LANG,
    expected_mtime: float | None = None,
) -> dict[str, Any]:
    """Сохранить код решения на диск — явная кнопка «Сохранить» режима 1.

    ``path`` — существующий выбранный файл (перезаписывается) или
    ``None``/пусто — создаётся новый файл в ``folder`` по маске
    (``_next_solution_filename``). Возвращает ``{"ok": True, "path": ...,
    "mtime": ...}`` или ``{"ok": False, "message": ...}`` — тот же контракт,
    что у ``download_task`` (issue #186); любой ``OSError`` при записи
    (несуществующая подпапка, права доступа) — graceful, не пробрасывается.

    ``expected_mtime`` (issue #297, optimistic locking, minimum viable) — если
    задан и ``path`` указывает на СУЩЕСТВУЮЩИЙ файл, чей фактический ``mtime``
    расходится с ожидаемым (файл изменился на диске с момента загрузки —
    напр. другое окно грейдера сохранило туда же), запись НЕ выполняется:
    возвращается ``{"ok": False, "conflict": True, ...}``. Фронтенд решает,
    перезаписывать ли (повторный вызов без ``expected_mtime``). Для нового
    файла (``path is None``) проверка не применяется — там нечего затирать.
    """
    folder_p = folder.expanduser()
    if not folder_p.is_dir():
        return {"ok": False, **message_fields("folder_not_found", lang, path=str(folder))}
    target = path.expanduser() if path else folder_p / _next_solution_filename(folder_p)
    if expected_mtime is not None and path is not None and target.is_file():
        actual_mtime = target.stat().st_mtime
        # Допуск 1 мс: mtime проходит через JSON float round-trip, а точность
        # st_mtime разнится по ФС (NTFS ~100нс, некоторые FS — секунды) —
        # точное равенство было бы ложно-конфликтным.
        if abs(actual_mtime - expected_mtime) > 1e-3:
            return {
                "ok": False,
                "conflict": True,
                **message_fields("file_changed_on_disk", lang, path=str(target)),
            }
    try:
        target.write_text(code, encoding=CONFIG.encoding)
        mtime = target.stat().st_mtime
    except OSError as exc:
        return {"ok": False, **message_fields("file_save_failed", lang, error=str(exc))}
    return {"ok": True, "path": str(target), "mtime": mtime}


# issue #395: оверрайд флага истории для web. ``CONFIG`` — frozen dataclass, а
# ``--serve`` должен включать историю по умолчанию; ``run_server`` ставит сюда
# True/False. None → падаем на ``CONFIG.record_history`` (тесты, зовущие grade_*
# напрямую, поведения не меняют).
_web_record_history: bool | None = None


def set_web_record_history(enabled: bool | None) -> None:
    """Переопределить флаг записи истории для web-слоя (issue #395).

    ``run_server`` вызывает с ``True`` (история по умолчанию для ``--serve``,
    приватная локальная БД) или ``False`` (``--no-history``). ``None`` —
    вернуться к ``CONFIG.record_history``.
    """
    global _web_record_history
    _web_record_history = enabled


def _history_enabled() -> bool:
    """Активна ли запись истории для web: оверрайд, иначе ``CONFIG`` (issue #395)."""
    return CONFIG.record_history if _web_record_history is None else _web_record_history


@functools.lru_cache(maxsize=1)
def _ruff_available_cached() -> bool:
    """Доступен ли ruff (extra ``[lint]``), кэш на процесс (issue #403).

    ``lint.ruff_available()`` запускает subprocess — кэшируем, чтобы не платить
    его на каждый web-грейд. Для типичного пользователя без extra ``[lint]``
    — один ``False`` за жизнь процесса, lint в историю не пишется.
    """
    return lint.ruff_available()


def _web_lint_records(solutions: list[pathlib.Path]) -> list[history.LintRecord]:
    """Собрать lint по решениям для истории (issue #403, best-effort).

    Гоняет ruff только если он установлен (opt-in extra ``[lint]``) — иначе
    пусто, без латентности. Любой сбой ruff → пустой список: линт информационный,
    не должен ронять грейдинг/запись.
    """
    if not _ruff_available_cached():
        return []
    try:
        violations = [v for sol in solutions for v in lint.run_lint(sol)]
    except (lint.LintUnavailable, OSError):
        return []
    return history_recording.lint_records_from_violations(violations)


def _record_history_if_enabled(
    mode: int,
    case_records: list[history.CaseRecord],
    *,
    task_key: str,
    duration_s: float,
    solution_name: str | None = None,
    solution_hash: str | None = None,
    lint_records: list[history.LintRecord] | None = None,
) -> None:
    """Записать web-прогон в историю под гейтом записи (issue #395).

    ``source="web"`` — так браузерная аудитория (та, «кому консоль барьер»)
    наполняет ``.grader_history.db``, и раздел «Подучить» перестаёт быть
    структурно пустым. Best-effort: сам ``record_run`` проглатывает ошибки БД
    (см. ``core/history``), поэтому запись никогда не роняет грейдинг.
    """
    if not _history_enabled():
        return
    history.record_run(
        mode,
        case_records,
        db_path=history_recording.default_history_db_path(),
        source="web",
        task_key=task_key,
        solution_name=solution_name,
        solution_hash=solution_hash,
        duration_s=duration_s,
        lint=lint_records or None,
    )


def grade_path(
    path: pathlib.Path,
    *,
    missing_queue_path: pathlib.Path | None = None,
    lang: str = DEFAULT_LANG,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Прогрейдить файл/папку на корректность (режим 1/2).

    Возвращает JSON-совместимый dict: kind ("file"|"dir"|"error"), mode="tests",
    base, rows (по одному решению) либо message при ошибке.

    ``missing_queue_path`` — путь к очереди пополнения глоссария (J7); None →
    ``CONFIG.glossary_missing_queue`` (issue #125). Параметр в основном для
    тестов — production-вызовы полагаются на дефолт из конфига. ``lang`` —
    локаль для ``message``/``message_id`` при ошибке (issue #264), default ru.

    ``progress_callback``/``cancel_event`` (issue #297, оба по умолчанию None —
    синхронный ``/api/grade`` их не передаёт, поведение не меняется) —
    прокидываются в каждый ``run_tests()``; ``cancel_event`` также
    проверяется перед каждым решением (симметрично ``grade_benchmark``).
    Используются, когда режим 1 идёт через async job (``web/runs.py``,
    ``kind="tests"``) с кодом в теле — прогресс тикает раз на тест-кейс.
    """
    resolved = _resolve_solutions(path, lang=lang)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

    rows: list[dict[str, Any]] = []
    graded: list[tuple[pathlib.Path, dict[str, Any]]] = []  # issue #395: для истории
    for sol in solutions:
        if cancel_event is not None and cancel_event.is_set():
            break
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not test_dir.is_dir():
            rows.append({"file": _rel(sol, base), "status": "NO TESTS", "passed": 0, "total": 0})
            continue
        res = run_tests(
            sol, test_dir, progress_callback=progress_callback, cancel_event=cancel_event
        )
        if res["total"] > 0:
            graded.append((sol, res))
        # total == 0 (tests/ существует, но не содержит распознаваемых кейсов)
        # — это не провал решения, а отсутствие проверки; тот же исход, что и
        # для отсутствующей tests/ выше (issue #299 — раньше эта ветка давала
        # вводящий в заблуждение "FAIL 0/0").
        if res["total"] == 0:
            status = "NO TESTS"
        else:
            status = "OK" if res["passed"] == res["total"] else "FAIL"
        # issue #397: run_tests кладёт "stdin" в каждый кейс — больше не
        # перечитываем тест-кейсы и не зипуем по позиции. Снята и хрупкость
        # #422: усечённый на отмене префикс cases самодостаточен (у каждого
        # уже есть свой stdin), zip(strict=True) с рассинхроном не нужен.
        rows.append(
            {
                "file": _rel(sol, base),
                "status": status,
                "passed": res["passed"],
                "total": res["total"],
                "total_time": round(res["total_time"], 4),
                "avg_time": round(res["avg_time"], 4),
                "memory_mb": round(res["peak_memory_mb"], 2),
                "cases": [
                    _case_view(
                        i,
                        c,
                        stdin=c.get("stdin", ""),
                        source=_rel(sol, base),
                        missing_queue_path=missing_queue_path,
                        lang=lang,
                    )
                    for i, c in enumerate(res["cases"], 1)
                ],
            }
        )

    # issue #395/#403: наполнить историю (+ lint), кроме случая отмены. Гейт
    # _history_enabled() здесь — чтобы не гонять ruff/сборку записей впустую.
    cancelled = cancel_event is not None and cancel_event.is_set()
    if not cancelled and graded and _history_enabled():
        if kind == "file":
            sol, res = graded[0]
            _record_history_if_enabled(
                1,
                history_recording.cases_from_test_results(res["cases"]),
                task_key=_rel(sol.parent, pathlib.Path.cwd()),
                solution_name=sol.name,
                solution_hash=hash_solution(sol),
                duration_s=res["total_time"],
                lint_records=_web_lint_records([sol]) or None,
            )
        else:
            all_cases = [c for _, r in graded for c in r["cases"]]
            total_time = sum(r["total_time"] for _, r in graded)
            _record_history_if_enabled(
                2,
                history_recording.cases_from_test_results(all_cases),
                task_key=_rel(base, pathlib.Path.cwd()),
                duration_s=total_time,
                lint_records=_web_lint_records([s for s, _ in graded]) or None,
            )
    return {"kind": kind, "mode": "tests", "base": str(base), "rows": rows}


def grade_benchmark(
    path: pathlib.Path,
    *,
    repeats: int = 15,
    reference: str | None = None,
    lang: str = DEFAULT_LANG,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Бенчмаркнуть файл/папку (режим 3) и ранжировать по медиане.

    Строки отсортированы от быстрого к медленному; вердикт SIMILAR/SLOWER/
    MUCH_SLOWER — относительно самого быстрого (как в CLI mode 3). Ошибочные
    решения идут в конец.

    ``reference`` — путь или имя файла одного из найденных решений (режим
    «Сравнение», редизайн под маску эпика #123): если указан и резолвится
    среди ``solutions`` без ошибки прогона, ранжирование считается
    относительно НЕГО (вердикты REFERENCE/FASTER/SIMILAR/SLOWER/MUCH_SLOWER),
    а его исходник возвращается в ``reference_source``/``reference_file``.
    Не резолвится (опечатка/чужой файл) — тихий fallback на обычное
    ранжирование относительно самого быстрого решения, как если бы
    ``reference`` не передавали. ``lang`` — локаль сообщения об ошибке
    (issue #264), default ru.

    ``progress_callback``/``cancel_event`` (issue #262, оба по умолчанию
    None — синхронный вызов из ``/api/grade`` их не передаёт, поведение не
    меняется) — прокидываются в каждый ``run_benchmark()`` для папки с
    несколькими решениями: один и тот же callback тикает через ВСЕ решения
    группы, что и даёт плоский job-level прогресс без отдельного механизма
    агрегации здесь. Также вызывается из ``web/runs.py`` (async job).
    """
    resolved = _resolve_solutions(path, lang=lang)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

    results: dict[pathlib.Path, dict[str, Any]] = {}
    for sol in solutions:
        if cancel_event is not None and cancel_event.is_set():
            break
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not test_dir.is_dir():
            results[sol] = {"error": render_message("tests_not_found_short", lang), "runs": 0}
        else:
            results[sol] = run_benchmark(
                sol,
                test_dir,
                repeats=max(1, repeats),
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )

    ref_path = None
    if reference:
        ref_name = pathlib.Path(reference).name
        ref_path = next(
            (
                s
                for s in solutions
                if (str(s) == reference or s.name == ref_name) and not results[s].get("error")
            ),
            None,
        )

    if ref_path is not None:
        apply_reference_ranking(
            results,
            ref_path,
            similar_threshold=SIMILAR_THRESHOLD,
            much_slower_threshold=MUCH_SLOWER_THRESHOLD,
        )
    else:
        apply_relative_ranking(
            results,
            similar_threshold=SIMILAR_THRESHOLD,
            much_slower_threshold=MUCH_SLOWER_THRESHOLD,
        )

    ok = {s: d for s, d in results.items() if not d.get("error")}
    rows: list[dict[str, Any]] = []
    for sol in sorted(ok, key=lambda s: ok[s]["median"]):
        d = ok[sol]
        rows.append(
            {
                "file": _rel(sol, base),
                "runs": d["runs"],
                "min": fmt_time(d["min"]),
                "median": fmt_time(d["median"]),
                # issue #370: полный набор колонок как в CLI-репортере (mean/max/
                # std dev) — бэкенд уже считает их в run_benchmark.
                "mean": fmt_time(d["mean"]),
                "max": fmt_time(d["max"]),
                "stdev": fmt_time(d["stdev"]),
                "relative": round(d.get("relative", 1.0) * 100, 1),
                "verdict": d.get("verdict", "SIMILAR"),
                "memory_mb": round(d["peak_memory_mb"], 2),
            }
        )
    for sol, d in results.items():
        if d.get("error"):
            rows.append({"file": _rel(sol, base), "verdict": "ERR", "error": d["error"]})

    # issue #395: наполнить историю (mode 3), кроме случая отмены.
    cancelled = cancel_event is not None and cancel_event.is_set()
    if not cancelled and results and _history_enabled():
        total_time = sum(d.get("mean", 0.0) * d.get("runs", 0) for d in results.values())
        _record_history_if_enabled(
            3,
            history_recording.cases_from_bench_results(results),
            task_key=_rel(base, pathlib.Path.cwd()),
            duration_s=total_time,
        )

    response: dict[str, Any] = {"kind": kind, "mode": "bench", "base": str(base), "rows": rows}
    if ref_path is not None:
        try:
            response["reference_source"] = ref_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # issue #423: не-UTF8 reference-файл не должен ронять bench-ответ.
            response["reference_source"] = None
        response["reference_file"] = _rel(ref_path, base)
    return response


def grade_microbench(
    path: pathlib.Path,
    *,
    number: int = 1000,
    lang: str = DEFAULT_LANG,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Микро-бенчмарк (режим 4, timeit) файла/папки — issue #187.

    В отличие от ``grade_benchmark``, ``run_microbench_mode`` принимает ОДИН
    общий ``test_dir`` для всех решений сразу и сама вызывает
    ``apply_relative_ranking`` внутри (сравнивает решения одной группы между
    собой) — поэтому решения группируются по подпапке (как CLI-режим 4,
    ``core/test_loader.collect_grouped_files``), а не грейдятся по одному
    (иначе ``relative``/``verdict`` выродились бы в тривиальные 1.0/SIMILAR).

    Несколько групп решений (разные подпапки) в одной корневой папке — MVP:
    обрабатывается только первая (по имени), остальные перечисляются в
    ``other_groups`` для явного предупреждения (полноценная секционная
    поддержка — вне скоупа этого issue). ``lang`` — локаль сообщения об
    ошибке (issue #264), default ru.

    ``progress_callback``/``cancel_event`` (issue #262, оба по умолчанию
    None) — прокидываются в ``run_microbench_mode()`` как есть; прогресс тут
    грубее (тик на решение, не на кейс/повтор), см. докстринг
    ``run_microbench_mode``.
    """
    resolved = _resolve_solutions(path, lang=lang)
    if isinstance(resolved, dict):
        return resolved
    kind, base, solutions = resolved

    if kind == "file":
        sol = solutions[0]
        test_dir = resolve_test_dir(sol)
        if test_dir is None or not test_dir.is_dir():
            return {
                "kind": "error",
                **message_fields("tests_not_found_for", lang, path=str(path)),
                "rows": [],
            }
        other_groups: list[str] = []
        results = run_microbench_mode(
            [sol],
            test_dir,
            number=number,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
    else:
        grouped = collect_grouped_files(base)
        group_names = sorted(grouped)
        folder = group_names[0]
        other_groups = group_names[1:]
        folder_abs = base if folder == "." else base / folder
        test_dir = _resolve_group_test_dir(folder_abs)
        if not test_dir.is_dir():
            return {
                "kind": "error",
                **message_fields("tests_not_found_for", lang, path=str(folder_abs)),
                "rows": [],
            }
        group_label = folder if folder != "." else base.name
        results = run_microbench_mode(
            sorted(grouped[folder]),
            test_dir,
            number=number,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    if not results:
        return {
            "kind": "error",
            **message_fields("test_cases_not_found_in", lang, path=str(test_dir)),
            "rows": [],
        }

    ok = {s: d for s, d in results.items() if not d.get("error")}
    rows: list[dict[str, Any]] = []
    for sol in sorted(ok, key=lambda s: ok[s]["median"]):
        d = ok[sol]
        rows.append(
            {
                "file": _rel(sol, base),
                "runs": d["runs"],
                "min_us": round(d["min"] * 1e6, 3),
                "median_us": round(d["median"] * 1e6, 3),
                "mean_us": round(d["mean"] * 1e6, 3),
                "max_us": round(d["max"] * 1e6, 3),
                "stdev_us": round(d["stdev"] * 1e6, 3),
                "relative": round(d.get("relative", 1.0) * 100, 1),
                "verdict": d.get("verdict", "SIMILAR"),
                "memory_mb": round(d["peak_memory_mb"], 2),
            }
        )
    for sol, d in results.items():
        if d.get("error"):
            rows.append({"file": _rel(sol, base), "verdict": "ERR", "error": d["error"]})

    # issue #395: наполнить историю (mode 4), кроме случая отмены.
    cancelled = cancel_event is not None and cancel_event.is_set()
    if not cancelled and results and _history_enabled():
        total_time = sum(d.get("mean", 0.0) * d.get("runs", 0) for d in results.values())
        _record_history_if_enabled(
            4,
            history_recording.cases_from_bench_results(results),
            task_key=_rel(base, pathlib.Path.cwd()),
            duration_s=total_time,
        )

    response: dict[str, Any] = {"kind": kind, "mode": "microbench", "base": str(base), "rows": rows}
    if kind == "dir":
        response["group"] = group_label
        if other_groups:
            response["other_groups"] = other_groups
    return response
