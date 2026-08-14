"""core/user_settings.py — персистентные пользовательские настройки CLI (issue #430).

Архитектурный слой: Application / Configuration (leaf-модуль).

Отдельный от ``config.py`` слой. ``config.GraderConfig`` — ``frozen=True`` и
читается ТОЛЬКО из секции ``[tool.stepik-grader]`` в ``pyproject.toml`` (конфиг
проекта, который pipx-ученик не редактирует). Этот модуль хранит настройки,
переключаемые прямо из интерактивного меню (например тумблер записи истории,
issue #430), в файле ``.grader_settings.json`` в рабочей директории — рядом с
``.grader_history.db``/``.grader_stats.jsonl``, к которым эти настройки и
относятся.

Приоритет для меню (issue #430): user-state (этот файл) → ``pyproject.toml``
(``CONFIG.record_history``) → дефолт ``False``. ``record_history is None``
означает «пользователь не переопределял» — тогда меню наследует ``CONFIG``.

Атомарную запись настроек делегирует общему top-level ``atomic_io.atomic_write_json``
(issue #551) — единственный проектный импорт (сам ``atomic_io`` — stdlib-leaf);
прежний статус «ноль проектных импортов» сменён на это одно ребро.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from stepik_grader.atomic_io import atomic_write_json

__all__ = [
    "SETTINGS_FILE_NAME",
    "LaunchChoice",
    "UserSettings",
    "default_settings_path",
    "load_settings",
    "save_fields",
    "save_settings",
    "settings_field_names",
]

SETTINGS_FILE_NAME = ".grader_settings.json"


@dataclass(frozen=True)
class LaunchChoice:
    """Выбор в окне лаунчера, переживающий закрытие окна (issue #1133).

    Окно каждый раз стартовало с нуля: порт 8000, изоляция выключена, папка
    вычислялась заново. Для инструмента, который открывают ежедневно, это
    значит, что пользователь, работающий с ``--sandbox``, включает его каждый
    раз — и однажды забудет. Тихая потеря настройки безопасности.

    ``None`` в поле — «не выбирал», и это не то же самое, что «выбрал значение,
    совпавшее с дефолтом»: запечённый выбор перекрыл бы правку
    ``pyproject.toml`` флагом с тем же значением (ADR-0012). Поэтому поля
    трёхзначные, а восстановление подставляет только заданное.
    """

    sandbox: bool | None = None
    record_history: bool | None = None
    lang: str | None = None
    port: int | None = None
    workdir: Path | None = None

    def to_json(self) -> dict[str, object]:
        """Представление для ``.grader_settings.json`` (только заданные поля)."""
        payload: dict[str, object] = {}
        for field in dataclass_fields(self):
            value = getattr(self, field.name)
            if value is None:
                continue
            payload[field.name] = str(value) if isinstance(value, Path) else value
        return payload

    @classmethod
    def from_json(cls, raw: object) -> LaunchChoice | None:
        """Разобрать значение из файла; мусор и чужие типы → ``None`` полей.

        Файл правится сторонними редакторами и другой версией грейдера, поэтому
        каждое поле проверяется по типу отдельно: одна испорченная строка не
        должна обнулять остальной выбор (тот же принцип, что у
        :func:`load_settings`).
        """
        if not isinstance(raw, dict):
            return None
        port = raw.get("port")
        workdir = raw.get("workdir")
        return cls(
            sandbox=_as_bool(raw.get("sandbox")),
            record_history=_as_bool(raw.get("record_history")),
            lang=_as_str(raw.get("lang")),
            # bool — подкласс int: без явной отсечки `True` стал бы портом 1.
            port=port if isinstance(port, int) and not isinstance(port, bool) else None,
            workdir=Path(workdir) if isinstance(workdir, str) and workdir else None,
        )


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_launch(value: object) -> LaunchChoice | None:
    return LaunchChoice.from_json(value)


# issue #1133 (LNCH-3-05): единственный список полей файла настроек. Раньше их
# перечисляли руками в четырёх местах (dataclass, чтение, `save_fields`,
# `save_settings`), и поле, забытое в одном из них, МОЛЧА не сохранялось —
# ровно тот дефект, которым рисковали профили лаунчера, первое непримитивное
# поле. Теперь читатели живут здесь, а совпадение с полями `UserSettings`
# сверяет тест (`test_user_settings.py`), а не внимательность автора правки.
_FIELD_READERS: dict[str, Callable[[object], object]] = {
    "record_history": _as_bool,
    "ai_hint_consent": _as_bool,
    "ai_hint_consent_endpoint": _as_str,
    "onboarding_seen": _as_bool,
    "last_launch": _as_launch,
}


def settings_field_names() -> frozenset[str]:
    """Имена полей, которые этот код умеет читать и писать (issue #1133)."""
    return frozenset(_FIELD_READERS)


def _to_json(value: object) -> object:
    """Значение поля в JSON-представлении (``LaunchChoice`` — вложенным объектом)."""
    if isinstance(value, LaunchChoice):
        return value.to_json()
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass
class UserSettings:
    """Пользовательские настройки, переключаемые из меню/web (issue #430).

    ``record_history``: ``None`` — не переопределено (наследовать
    ``CONFIG.record_history``); ``True``/``False`` — явный выбор пользователя,
    сохранённый между запусками.

    ``ai_hint_consent`` (issue #543): однократное явное согласие пользователя на
    отправку кода/ввода-вывода AI-провайдеру (web ``POST /api/v1/hint``). ``None``
    — не давалось (запрос в сеть не уйдёт, эндпоинт вернёт ``consent_required``);
    ``True`` — дано и запомнено между запусками. Приватность: без согласия ничего
    не отправляется наружу.

    ``ai_hint_consent_endpoint`` (issue #812): получатель, которому согласие
    было дано, в виде ``scheme://host[:port]``. Согласие было глобальным:
    сказав «да» локальному ollama, пользователь тем же «да» разрешал отправку
    на любой адрес, который позже окажется в конфиге. Информированным такое
    согласие не назвать — получатель неизвестен. Не совпало с текущим
    ``ai_base_url`` → спрашиваем заново.

    ``onboarding_seen`` (issue #660): показан ли пользователю стартовый экран-
    онбординг веб-интерфейса. ``None``/``False`` — ещё не закрыт (веб покажет
    модалку при первом заходе); ``True`` — закрыт, больше не всплывает
    автоматически (открыть заново можно кнопкой в topbar). Машинный факт «первый
    запуск на этой рабочей директории», а не per-браузер, поэтому живёт здесь, а
    не в ``localStorage`` (в отличие от однократного history-notice, issue #565).

    ``last_launch`` (issue #1133): последний выбор в окне лаунчера — изоляция,
    история, язык, порт, рабочая папка. ``None`` — окно ещё не запускали. Первое
    непримитивное поле файла настроек, поэтому список полей выведен в
    ``_FIELD_READERS`` (см. комментарий там): при ручном перечислении новое поле
    молча не сохранилось бы.
    """

    record_history: bool | None = None
    ai_hint_consent: bool | None = None
    ai_hint_consent_endpoint: str | None = None
    onboarding_seen: bool | None = None
    last_launch: LaunchChoice | None = None


def default_settings_path(root: Path | None = None) -> Path:
    """Путь к файлу настроек внутри корня настроек (issue #430, #984).

    Мирит семантику с ``history_recording.default_history_db_path()``:
    настройка живёт там же, где база истории, которой она управляет.

    ``root`` — корень настроек прогона (``config.workspace_root()``): CLI
    передаёт его явно, чтобы читать тот же файл, что и веб, который якорится на
    ``--root``. Прежде CLI жёстко брал ``cwd``, и один запуск имел два разных
    корня настроек. Параметр, а не импорт ``config``: модуль остаётся
    leaf'ом с единственным ребром на ``atomic_io``, а знание о ``--root``
    живёт в слое, который его и разбирает. ``None`` — прежнее поведение (cwd).
    """
    return (root or Path.cwd()) / SETTINGS_FILE_NAME


def load_settings(path: Path) -> UserSettings:
    """Прочитать настройки из ``path`` (best-effort).

    Отсутствие файла, битый JSON или неверный тип поля → дефолтные
    ``UserSettings`` (никогда не роняет CLI). Неизвестные ключи игнорируются —
    формат forward-compatible.
    """
    try:
        # issue #792 (FST-01): читаем БАЙТЫ и декодируем с заменой. Прежний
        # read_text ловился только `except OSError`, а UnicodeDecodeError —
        # подкласс ValueError: один посторонний байт в файле (правка сторонним
        # редактором, обрыв синхронизации) ронял интерактивное меню целиком,
        # хотя докстринг обещает «никогда не роняет CLI». Битый текст всё равно
        # не разберётся как JSON — ниже вернутся дефолтные настройки.
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return UserSettings()
    try:
        data = json.loads(raw)
    except ValueError:
        return UserSettings()
    if not isinstance(data, dict):
        return UserSettings()
    # issue #1133: поля берутся из общего списка читателей, а не перечисляются
    # здесь ещё раз — иначе новое поле читалось бы только там, где о нём вспомнили.
    # Значения приходят из JSON, то есть проверяются в рантайме читателями, а не
    # статически: `Any` здесь честнее, чем `object` с игнором на распаковке.
    values: dict[str, Any] = {name: read(data.get(name)) for name, read in _FIELD_READERS.items()}
    return UserSettings(**values)


def _read_raw(path: Path) -> dict[str, object]:
    """Сырое содержимое файла настроек как dict (битое/отсутствующее → пустой).

    Отдельно от ``load_settings``: тому нужен типизированный ``UserSettings``, а
    записи — все ключи файла, включая незнакомые этой версии. Иначе обновление
    одного флага стирало бы поля, которых код ещё не знает.
    """
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_fields(path: Path, **fields: object) -> None:
    """Записать ТОЛЬКО названные поля поверх текущего файла (issue #997).

    ``None`` в значении — явное стирание ключа, а не «не задано»: так
    отзывается согласие на AI-подсказки. Неупомянутые поля остаются как есть,
    включая незнакомые этой версии.

    Зачем вместо ``save_settings``: тот пишет весь объект, а объект у
    интерактивного меню — снапшот, снятый при запуске. Пока меню открыто, файл
    мог изменить веб или соседний процесс, и переключение тумблера истории
    возвращало на диск всё остальное состояние часовой давности — включая
    отозванное согласие на отправку кода AI-провайдеру (``CNC-5-01``,
    ``CNC-5-04``, ``SET-2-02``).

    Гонку двух одновременных писателей это не решает (read-modify-write не
    атомарен), но окно сузилось с «вся сессия меню» до времени одной записи.
    """
    unknown = set(fields) - settings_field_names()
    if unknown:
        raise ValueError(f"неизвестные поля настроек: {sorted(unknown)}")
    data = _read_raw(path)
    for name, value in fields.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = _to_json(value)
    atomic_write_json(path, data, fsync=False)


def save_settings(settings: UserSettings, path: Path) -> None:
    """Записать настройки в ``path`` атомарно через общий ``atomic_write_json``.

    Пишутся только явно заданные (не-``None``) поля, чтобы файл не фиксировал
    «наследуемые из CONFIG» значения, и **поверх текущего содержимого файла**:
    ключи, которых нет в объекте (записанные другим каналом или другой версией),
    сохраняются. Стереть ключ этой функцией нельзя — для этого
    :func:`save_fields` с ``None``.

    Когда известно, какие именно поля меняются, берите :func:`save_fields`: он
    не тащит на диск остальной снапшот объекта. ``atomic_write_json`` (issue #551) сменил
    прежний фиксированный ``.tmp`` (его делили параллельные писатели — гонка) на
    уникальный ``mkstemp``; ``fsync=False`` — настройки редки и не критичны,
    достаточно атомарности замены.
    """
    payload: dict[str, object] = _read_raw(path)
    if settings.record_history is not None:
        payload["record_history"] = settings.record_history
    if settings.ai_hint_consent is not None:
        payload["ai_hint_consent"] = settings.ai_hint_consent
    if settings.ai_hint_consent_endpoint is not None:
        payload["ai_hint_consent_endpoint"] = settings.ai_hint_consent_endpoint
    if settings.onboarding_seen is not None:
        payload["onboarding_seen"] = settings.onboarding_seen
    atomic_write_json(path, payload, fsync=False)
