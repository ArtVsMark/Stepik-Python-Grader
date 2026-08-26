"""usage_export.py — журнал прогонов в объявленном формате для соседних инструментов.

Грейдер уже ведёт локальный журнал прогонов (``core/stats.py``, ``.grader_stats.jsonl``,
opt-in ``--stats``), но наружу он отдавался только человеку — сводкой на экран.
Соседнему инструменту (``claude-code-usage``) нужен тот же материал **записями**, а
не сводкой, и в формате, на который можно опереться.

Три решения, которые здесь важнее кода:

1. **Ничего нового не собирается.** Экспорт — это чтение уже накопленного журнала
   и переименование полей в объявленную схему. Ни одного нового измерения,
   ни одного нового источника: иначе «экспорт» стал бы телеметрией, заведённой
   боком, и раздел SECURITY.md пришлось бы переписывать про сбор, а не про формат.
2. **Схема версионирована и живёт в поле каждой записи** (:data:`USAGE_SCHEMA`).
   Потребитель читает файл построчно и должен уметь отличить сегодняшний формат
   от завтрашнего, не выясняя версию из имени файла или документации.
3. **Сети нет и не появится.** Экспорт пишет в файл или в стандартный вывод —
   всё. Отправку наружу, если она кому-то нужна, делает тот, кто читает файл, и
   делает осознанно.

Формат — JSON Lines, как и сам журнал: запись независима от соседей, обрыв на
середине теряет одну строку, а не файл. Битые строки исходного журнала
**пропускаются молча**, ровно как в :func:`stepik_grader.core.stats.read_summary`:
экспорт не тот случай, где стоит падать из-за одной покалеченной записи, — но
число пропущенных возвращается, чтобы «пусто» и «всё побилось» не выглядели
одинаково.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from stepik_grader.core import stats

__all__ = [
    "USAGE_SCHEMA",
    "ExportResult",
    "collect_events",
    "render_jsonl",
    "write_export",
]

#: Имя и версия формата. Меняется вместе с несовместимой правкой полей —
#: потребитель сверяет его в каждой строке.
USAGE_SCHEMA = "stepik-grader/usage/1"

#: Поля, которые уезжают в экспорт. Список закрытый и это главное свойство
#: модуля: добавление строки сюда — расширение того, что покидает журнал, и
#: обязано проходить через ревью, а не через `entry.update(...)`.
_FIELDS = ("ts", "mode", "os", "verdicts", "total_time", "isolation")


class ExportResult:
    """Что получилось: сколько записей отдано и сколько пропущено.

    Attributes:
        events: события в схеме :data:`USAGE_SCHEMA`, старые сверху.
        skipped: строки журнала, которые не разобрались или не несли режима.
    """

    __slots__ = ("events", "skipped")

    def __init__(self, events: list[dict[str, Any]], skipped: int) -> None:
        self.events = events
        self.skipped = skipped

    def __repr__(self) -> str:  # pragma: no cover — отладочное представление
        return f"ExportResult(events={len(self.events)}, skipped={self.skipped})"


def collect_events(
    *,
    stats_path: pathlib.Path | None = None,
    since: float | None = None,
) -> ExportResult:
    """Прочитать журнал прогонов и привести записи к схеме экспорта.

    Args:
        stats_path: путь к журналу; по умолчанию — штатный.
        since: отдавать только записи не старше этой отметки времени (epoch).

    Returns:
        События и число пропущенных строк. Отсутствующий журнал — пустой
        результат, а не ошибка: статистика opt-in, и «выключено» законно.
    """
    path = stats.stats_path() if stats_path is None else stats_path
    events: list[dict[str, Any]] = []
    skipped = 0

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ExportResult([], 0)
    except OSError:
        # Нечитаемый журнал — не повод падать: тот же best-effort, что во всём
        # модуле статистики. Но и делать вид, что журнал пуст, нельзя.
        return ExportResult([], 1)

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("mode"), int):
            skipped += 1
            continue
        timestamp = entry.get("ts")
        if since is not None and (not isinstance(timestamp, int | float) or timestamp < since):
            continue

        event: dict[str, Any] = {"schema": USAGE_SCHEMA}
        for field in _FIELDS:
            if field in entry:
                event[field] = entry[field]
        events.append(event)

    events.sort(key=lambda item: item.get("ts") or 0)
    return ExportResult(events, skipped)


def render_jsonl(events: list[dict[str, Any]]) -> str:
    """Собрать JSON Lines: одна запись — одна строка, всегда с завершающим \\n."""
    if not events:
        return ""
    return (
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events) + "\n"
    )


def write_export(
    destination: pathlib.Path,
    *,
    stats_path: pathlib.Path | None = None,
    since: float | None = None,
) -> ExportResult:
    """Записать экспорт в файл, создав родительские каталоги.

    Args:
        destination: файл назначения; каталог создаётся при необходимости.
        stats_path: путь к журналу прогонов.
        since: нижняя граница по времени.

    Returns:
        Тот же результат, что у :func:`collect_events`.

    Raises:
        OSError: записать не удалось. Здесь ошибка НЕ проглатывается: команду
            позвали ради файла, и молчаливый успех без файла — худший исход.
    """
    result = collect_events(stats_path=stats_path, since=since)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_jsonl(result.events), encoding="utf-8")
    return result
