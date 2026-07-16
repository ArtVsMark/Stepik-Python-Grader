"""stepik_reference.py — импорт закреплённых решений Stepik (issue #55).

Архитектурный слой: Application (оркестрация Stepik-специфики). НЕ часть
grading-core: импортированное решение — вторичный reference-competitor для
режимов 2–4, а не источник первичной проверки.

Цепочка (эмпирически подтверждена на lesson/571244/step/3):
    meta.json (step_id/lesson_id/step_position)
      → fetch_step_data → discussion_threads
      → thread с thread=="solutions"
      → discussion-proxy → discussions_most_liked
      → comments + expand=submission → reply.code
      → task{N}_{100+}.py

Имя файла: числовой слот с зарезервированным высоким диапазоном (старт 100),
совместимый с ``core/test_loader._SOLUTION_FILE_RE`` — режимы 2–4 подхватывают
его как обычное решение. Высокий номер = соглашение-маркер «reference».
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from stepik_grader.core.diag_log import get_logger
from stepik_grader.core.oauth_flow import create_user_session, load_secrets_dict
from stepik_grader.core.step_content import extract_submission_code, pick_solutions_thread
from stepik_grader.core.stepik_client import (
    fetch_comments_with_submissions,
    fetch_discussion_proxy,
    fetch_discussion_threads,
    fetch_step_data,
)
from stepik_grader.core.storage import load_json_file, save_json_file

__all__ = [
    "DEFAULT_MAX_TOP",
    "DEFAULT_MIN_LIKES",
    "REFERENCE_SLOT_START",
    "ReferenceSolution",
    "import_references_from_task_dir",
    "next_free_reference_slot",
    "reference_slot_filename",
    "select_reference_solutions",
]

_log = get_logger("stepik_reference")

REFERENCE_SLOT_START = 100  # reference-файлы стартуют с task{N}_100.py
DEFAULT_MAX_TOP = 5  # топовых сверх эталона
DEFAULT_MIN_LIKES = 1  # нулёвые решения не берём в top


@dataclass
class ReferenceSolution:
    """Отобранное reference-решение из ветки solutions."""

    comment_id: int
    likes: int
    is_pinned: bool
    code: str


def _normalize_code(code: str) -> str:
    """Нормализует код для дедупа (одинаковые решения не тащим дважды)."""
    return "\n".join(line.rstrip() for line in code.strip().splitlines())


def select_reference_solutions(
    comments: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    *,
    max_top: int = DEFAULT_MAX_TOP,
    min_likes: int = DEFAULT_MIN_LIKES,
) -> list[ReferenceSolution]:
    """Отбирает эталон + топовые решения из comments/submissions.

    Возвращает список, где первый элемент — эталон (закреплённое ``is_pinned``;
    если закреплённого нет — лучшее по лайкам), затем до ``max_top`` топовых по
    убыванию лайков. Эталон берётся всегда (если код есть), топовые фильтруются
    по ``likes >= min_likes``. Дубликаты по коду схлопываются. Пустой список —
    если ни у одного решения не извлёкся код.
    """
    subs_by_id = {s.get("id"): s for s in submissions}

    candidates: list[ReferenceSolution] = []
    for comment in comments:
        sub = subs_by_id.get(comment.get("submission"))
        if sub is None:
            continue
        code = extract_submission_code(sub)
        if not code or not code.strip():
            continue
        candidates.append(
            ReferenceSolution(
                comment_id=int(comment.get("id", 0)),
                likes=int(comment.get("epic_count") or 0),
                is_pinned=bool(comment.get("is_pinned")),
                code=code,
            )
        )

    candidates.sort(key=lambda r: r.likes, reverse=True)

    # дедуп по коду, лучшие (по лайкам) остаются
    seen: set[str] = set()
    deduped: list[ReferenceSolution] = []
    for ref in candidates:
        key = _normalize_code(ref.code)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)

    if not deduped:
        return []

    # эталон → слот 100: закреплённое, иначе лучшее по лайкам
    reference = next((r for r in deduped if r.is_pinned), deduped[0])
    top = [r for r in deduped if r is not reference and r.likes >= min_likes][:max_top]
    return [reference, *top]


def reference_slot_filename(step_position: int, slot: int) -> str:
    """Имя reference-файла ``task{step_position}_{slot}.py`` (совместимо с 2–4)."""
    return f"task{step_position}_{slot}.py"


def next_free_reference_slot(
    directory: Path,
    step_position: int,
    start: int = REFERENCE_SLOT_START,
) -> int:
    """Возвращает первый свободный слот ≥ ``start`` в директории задачи."""
    slot = start
    while (directory / reference_slot_filename(step_position, slot)).exists():
        slot += 1
    return slot


def import_references_from_task_dir(
    task_dir: Path,
    *,
    secrets_path: Path = Path("secrets.json"),
    max_top: int = DEFAULT_MAX_TOP,
    min_likes: int = DEFAULT_MIN_LIKES,
    session: requests.Session | None = None,
) -> list[Path]:
    """Импортирует reference-решения из ветки solutions в директорию задачи.

    Читает ``task_dir/meta.json`` (сохранён downloader'ом), проходит цепочку до
    закреплённого решения, сохраняет эталон + топовые как ``task{N}_{100+}.py``
    и дописывает привязку в meta.json. Возвращает пути сохранённых файлов.

    ``session`` — готовая авторизованная сессия (dependency injection): web-слой
    передаёт non-browser сессию (``try_create_session_without_browser``), чтобы
    сервер не блокировался на браузерном OAuth. ``None`` (CLI) → создаётся через
    ``create_user_session`` (может открыть браузер при протухшем refresh).

    Бросает понятные ошибки на каждом обрыве цепочки (нет meta.json, ветка
    решений не открыта/отсутствует, решений нет, код не извлёкся).
    """
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"meta.json не найден в {task_dir} — сначала скачайте задачу (downloader)"
        )
    meta = load_json_file(meta_path)  # битый JSON → JSONDecodeError
    lesson_id = meta.get("lesson_id")
    step_position = meta.get("step_position")
    if not lesson_id or not step_position:
        raise ValueError("meta.json неполный: нет lesson_id/step_position")

    if session is None:
        secrets = load_secrets_dict(secrets_path)
        session = create_user_session(secrets, secrets_path)

    step = fetch_step_data(session, int(lesson_id), int(step_position))
    # NB: поле is_solutions_unlocked отражает UI-состояние пользователя (открыл
    # ли он кнопку «решения» после N попыток), а НЕ доступность данных через
    # API — проверено эмпирически: при False ветка solutions всё равно
    # отдаёт решения. Поэтому не гейтим по нему; реальные обрывы (нет ветки /
    # нет решений / код не извлёкся) ловятся ниже понятными ошибками.
    threads = fetch_discussion_threads(session, step.get("discussion_threads") or [])
    solutions_thread = pick_solutions_thread(threads)
    if solutions_thread is None:
        raise ValueError("У задачи нет ветки решений (solutions thread)")

    proxy = fetch_discussion_proxy(session, str(solutions_thread["discussion_proxy"]))
    # запас на дедуп/фильтр по лайкам; most_liked уже отсортирован Stepik
    candidate_ids = (proxy.get("discussions_most_liked") or [])[: max(20, max_top * 3)]
    if not candidate_ids:
        raise ValueError("В ветке решений нет решений")

    comments, submissions = fetch_comments_with_submissions(session, candidate_ids)
    selected = select_reference_solutions(
        comments, submissions, max_top=max_top, min_likes=min_likes
    )
    if not selected:
        raise ValueError("Не удалось извлечь код ни одного решения из ветки")

    saved: list[Path] = []
    refs_meta: list[dict[str, Any]] = []
    for ref in selected:
        slot = next_free_reference_slot(task_dir, int(step_position))
        file_path = task_dir / reference_slot_filename(int(step_position), slot)
        code = ref.code if ref.code.endswith("\n") else ref.code + "\n"
        file_path.write_text(code, encoding="utf-8")
        saved.append(file_path)
        refs_meta.append(
            {
                "file": file_path.name,
                "comment_id": ref.comment_id,
                "likes": ref.likes,
                "is_pinned": ref.is_pinned,
            }
        )
        _log.debug(
            "reference сохранён: %s (pinned=%s likes=%d)", file_path.name, ref.is_pinned, ref.likes
        )

    meta["stepik_references"] = refs_meta
    save_json_file(meta_path, meta)
    return saved
