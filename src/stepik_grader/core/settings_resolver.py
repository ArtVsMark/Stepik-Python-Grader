"""settings_resolver.py — настройки прогона из user-state поверх pyproject (issue #1136).

Архитектурный слой: Application / Configuration.

Вкладка «Дополнительно» предъявляет пользователю настройки, которые до неё
правились только в ``pyproject.toml`` — файле, которого у поставившего пакет
через ``pipx`` **нет вовсе** (``SET-3-03``). Поэтому выбор живёт в
``.grader_settings.json`` (ADR-0012), а этот модуль отвечает на два вопроса,
которых раньше никто не задавал:

* **что применить** — какие значения из user-state лечь поверх конфига проекта
  (:func:`apply_user_run_settings`);
* **откуда взялось текущее** — «по умолчанию» / «из ``pyproject.toml``» /
  «изменено вами» (:func:`describe_setting`).

Второе — не украшение. Персистентная настройка липкая: выбранное однажды
действует, пока не сброшено, и через месяц её автор не помнит, что менял.
Показ происхождения и кнопка сброса — то, чем ADR-0012 расплачивается за
удобство файла настроек.

Применяется через существующий ``config.override_config``, то есть встаёт в
уже действующую лестницу приоритета: **явный флаг → user-state →
``pyproject.toml`` → дефолт**. Флаги применяются ПОСЛЕ и перекрывают — иначе
разовое ``--timeout 30`` проигрывало бы сохранённому значению, а это
противоположность тому, зачем флаг существует.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Literal

from stepik_grader import config
from stepik_grader.core.user_settings import default_settings_path, load_settings, save_fields

__all__ = [
    "ADVANCED_GROUPS",
    "ADVANCED_SETTINGS",
    "USER_TUNABLE_SETTINGS",
    "AdvancedSetting",
    "SettingView",
    "advanced_settings",
    "apply_user_run_settings",
    "coerce_value",
    "describe_setting",
    "reset_setting",
    "set_user_run_setting",
    "setting_spec",
]

Origin = Literal["default", "pyproject", "user"]

# issue #1136: какие настройки пользователю ВООБЩЕ можно менять из интерфейса.
# Список закрытый и повторяет разбор в постановке: вердикт (сравнение, лимиты),
# «Подучить» (N/T/K), поведение прогона, квоты песочницы и конфайнмент путей.
#
# Намеренно НЕ включены `encoding`, `glossary_store`, `glossary_missing_queue`,
# `history_db_path` и пороги микробенча: это тюнинг, а не выбор, и ему место в
# `pyproject.toml`. Закрытый список важнее удобства — иначе файл настроек
# незаметно превращается во второй конфиг проекта, только без ревью и истории.
USER_TUNABLE_SETTINGS: frozenset[str] = frozenset(
    {
        # Вердикт
        "compare_mode",
        "timeout_seconds",
        "max_memory_mb",
        # Обучение — «Подучить»
        "insights_window_n",
        "insights_active_threshold_t",
        "insights_clean_streak_k",
        # Поведение прогона
        "use_cache",
        "record_stats",
        "record_history",
        "job_workers",
        "max_active_runs",
        # AI-подсказки
        "ai_base_url",
        "ai_model",
        "ai_max_hints",
        "ai_grounding_k",
        # Песочница
        "sandbox_max_cpu_seconds",
        "sandbox_max_processes",
        "sandbox_max_output_bytes",
    }
)


@dataclasses.dataclass(frozen=True)
class SettingView:
    """Что показать рядом с контролом настройки (issue #1136).

    ``value`` — действующее значение, ``origin`` — откуда оно взялось,
    ``default`` — значение по умолчанию, ``inherited`` — что останется, если
    пользователь нажмёт «сбросить» (значение проекта или дефолт).
    """

    name: str
    value: object
    origin: Origin
    default: object
    inherited: object


def _known_names() -> set[str]:
    return {f.name for f in dataclasses.fields(config.GraderConfig)}


def _user_values(root: Path | None = None) -> dict[str, object]:
    """Настройки прогона из ``.grader_settings.json`` (только допустимые)."""
    settings = load_settings(default_settings_path(root or config.workspace_root()))
    known = _known_names() & USER_TUNABLE_SETTINGS
    return {name: value for name, value in settings.run_settings.items() if name in known}


def apply_user_run_settings(root: Path | None = None) -> list[str]:
    """Наложить пользовательские настройки прогона на конфиг; вернуть отброшенные.

    Негодные значения (испорченный файл, настройка из чужой версии) молча
    отбрасываются самим ``config.validate_values`` — тем же кодом, что проверяет
    ``pyproject.toml``. Имена отброшенных возвращаются, чтобы вызывающая сторона
    могла о них сказать: настройка, которая «не сработала» без единого слова,
    неотличима от неработающей функции.

    Returns:
        Имена настроек, которые не прошли проверку и применены не были.
    """
    values = _user_values(root)
    if not values:
        return []
    rejected = sorted({problem.split(":", 1)[0] for problem in config.validate_values(values)})
    usable = {name: value for name, value in values.items() if name not in rejected}
    if usable:
        config.override_config(**usable)
    return rejected


def describe_setting(name: str, root: Path | None = None) -> SettingView:
    """Действующее значение настройки и его происхождение (issue #1136).

    Raises:
        ValueError: имя не относится к настройкам, доступным пользователю —
            молча вернуть «по умолчанию» на опечатку значило бы показать в
            интерфейсе настройку, которой нет.
    """
    if name not in USER_TUNABLE_SETTINGS or name not in _known_names():
        raise ValueError(f"настройка недоступна пользователю: {name}")
    defaults = config.GraderConfig()
    default_value = getattr(defaults, name)
    # Значение проекта читается ОТДЕЛЬНЫМ чтением файла, а не из активного
    # конфига: активный уже содержит наложенный user-state, и по нему
    # «изменено вами» неотличимо от «из pyproject.toml».
    project_value = getattr(config.load_config(), name)
    user_values = _user_values(root)

    if name in user_values:
        return SettingView(
            name=name,
            value=user_values[name],
            origin="user",
            default=default_value,
            inherited=project_value,
        )
    origin: Origin = "pyproject" if project_value != default_value else "default"
    return SettingView(
        name=name,
        value=project_value,
        origin=origin,
        default=default_value,
        inherited=project_value,
    )


def set_user_run_setting(name: str, value: object, root: Path | None = None) -> None:
    """Сохранить пользовательское значение настройки прогона (issue #1136).

    Raises:
        ValueError: имя недоступно пользователю или значение не проходит ту же
            проверку, что и ``pyproject.toml``. Отказ, а не тихое сохранение:
            иначе интерфейс показывал бы выбранное, а прогон шёл бы по старому.
    """
    if name not in USER_TUNABLE_SETTINGS or name not in _known_names():
        raise ValueError(f"настройка недоступна пользователю: {name}")
    problems = config.validate_values({name: value})
    if problems:
        raise ValueError(problems[0])
    path = default_settings_path(root or config.workspace_root())
    values = dict(load_settings(path).run_settings)
    values[name] = value
    save_fields(path, run_settings=values)


def reset_setting(name: str, root: Path | None = None) -> None:
    """Убрать пользовательское значение — вернуть унаследованное (issue #1136).

    Ключ удаляется, а не переписывается значением проекта: «не задано» обязано
    оставаться отсутствием ключа, иначе следующая правка ``pyproject.toml``
    перестала бы действовать — её перекрыл бы застывший снимок сегодняшнего дня
    (тот же инвариант, что у команды лаунчера, ADR-0012).
    """
    path = default_settings_path(root or config.workspace_root())
    values = dict(load_settings(path).run_settings)
    # Нечего сбрасывать — не ошибка: «сбросить» жмут и на унаследованном
    # значении, и повторно, и результат обязан быть один и тот же.
    values.pop(name, None)
    save_fields(path, run_settings=values)


@dataclasses.dataclass(frozen=True)
class AdvancedSetting:
    """Как показать настройку во вкладке «Дополнительно» (issue #1136).

    ``kind`` — тип контрола (``bool``/``int``/``float``/``choice``/``text``),
    ``group`` — блок вкладки, ``unsafe`` — требует явного подтверждения перед
    тем, как станет доступной, ``nullable`` — пустое поле означает осмысленное
    «не ограничивать / не задано», а не ошибку ввода.

    Описание живёт в ядре, а не в окне: по нему строятся контролы, и ровно оно
    отвечает на вопрос «какие настройки вообще предъявляем». Список в GUI
    означал бы, что проверить его состав можно только глазами и только там, где
    есть дисплей.
    """

    name: str
    kind: str
    group: str
    choices: tuple[str, ...] = ()
    unsafe: bool = False
    nullable: bool = False


# Порядок = порядок показа. Группы и состав — из постановки #1136: вердикт,
# обучение, поведение прогона, AI, небезопасное. Тюнинг (encoding, пороги
# микробенча, пути глоссария) сюда не входит и остаётся в pyproject.toml.
#
# `record_history` намеренно отсутствует, хотя и разрешён пользователю: он
# уже стоит на главной вкладке лаунчера как настройка приватности (issue
# #1131). Два контрола на одно значение расходятся ровно тогда, когда человек
# правит один и смотрит на другой.
ADVANCED_SETTINGS: tuple[AdvancedSetting, ...] = (
    AdvancedSetting("compare_mode", "choice", "verdict", choices=("stepik", "strict")),
    AdvancedSetting("timeout_seconds", "float", "verdict"),
    AdvancedSetting("max_memory_mb", "int", "verdict", nullable=True),
    AdvancedSetting("insights_window_n", "int", "learning"),
    AdvancedSetting("insights_active_threshold_t", "int", "learning"),
    AdvancedSetting("insights_clean_streak_k", "int", "learning"),
    AdvancedSetting("use_cache", "bool", "run"),
    AdvancedSetting("record_stats", "bool", "run"),
    AdvancedSetting("job_workers", "int", "run"),
    AdvancedSetting("max_active_runs", "int", "run"),
    AdvancedSetting("ai_base_url", "text", "ai", nullable=True),
    AdvancedSetting("ai_model", "text", "ai", nullable=True),
    AdvancedSetting("ai_max_hints", "int", "ai"),
    AdvancedSetting("ai_grounding_k", "int", "ai"),
    # Квоты песочницы — за подтверждением: они меняют то, что именно
    # ограничивает изоляция, а «подкрутил и забыл» здесь стоит дороже, чем в
    # любом другом блоке вкладки.
    AdvancedSetting("sandbox_max_cpu_seconds", "float", "unsafe", unsafe=True),
    AdvancedSetting("sandbox_max_processes", "int", "unsafe", unsafe=True),
    AdvancedSetting("sandbox_max_output_bytes", "int", "unsafe", unsafe=True),
)

ADVANCED_GROUPS: tuple[str, ...] = ("verdict", "learning", "run", "ai", "unsafe")


def advanced_settings(group: str | None = None) -> tuple[AdvancedSetting, ...]:
    """Описания контролов вкладки; ``group`` — только один блок (issue #1136)."""
    if group is None:
        return ADVANCED_SETTINGS
    return tuple(item for item in ADVANCED_SETTINGS if item.group == group)


def setting_spec(name: str) -> AdvancedSetting:
    """Описание контрола по имени настройки (issue #1136).

    Raises:
        ValueError: настройки нет во вкладке.
    """
    for item in ADVANCED_SETTINGS:
        if item.name == name:
            return item
    raise ValueError(f"настройка недоступна пользователю: {name}")


def coerce_value(name: str, raw: object) -> object:
    """Привести ввод контрола к типу настройки (issue #1136).

    Поле ввода отдаёт строку, а ``config.validate_values`` проверяет тип —
    без приведения ``"12"`` было бы отвергнуто как строка там, где ждут число.
    Пустая строка у ``nullable``-настройки означает ``None`` («без лимита», «не
    задано»), а не ноль: ноль эти поля не принимают вовсе.

    Raises:
        ValueError: имя не из вкладки или ввод не разобрался как число —
            отказ здесь, а не молчаливая подстановка дефолта, иначе опечатка
            в поле выглядела бы как принятое значение.
    """
    spec = setting_spec(name)
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text:
        if spec.nullable:
            return None
        raise ValueError(f"{name}: пустое значение недопустимо")
    if spec.kind == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{name}: ожидается целое число, получено {text!r}") from exc
    if spec.kind == "float":
        try:
            return float(text.replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{name}: ожидается число, получено {text!r}") from exc
    if spec.kind == "bool":
        return text.lower() in {"1", "true", "yes", "да", "вкл"}
    return text
