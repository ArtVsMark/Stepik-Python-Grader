"""Тесты scripts/generate_rules_index.py — указатель правил (issue #1342).

Указатель генерируется, а не ведётся: список, который поддерживают руками,
отстаёт с первого же нового правила — молча, как уже было с числами в витрине и
со списками открытых задач. Поэтому проверяется не «красиво ли», а три
свойства: правило со следом сюда попадает само, без следа — не попадает вовсе,
а след в никуда роняет генератор.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).parent.parent / "scripts"
_SCRIPT = _SCRIPTS / "generate_rules_index.py"


def _load_module() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("_generate_rules_index", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Регистрация до exec_module: `dataclasses` ищет модуль класса в sys.modules,
    # и без неё разбор `@dataclass` падает на пустом `__module__`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generator() -> ModuleType:
    """Свежий модуль на каждый тест."""
    return _load_module()


def _rule(trace: str, *, title: str = "Правило", mechanism: str = "") -> str:
    """Файл правила каталога: заголовок, инцидент, след и опционально механизм."""
    parts = [f"# {title}", "", "## Инцидент", "", "Что-то сломалось.", "", "## След", "", trace]
    if mechanism:
        parts += ["", "## Механизм", "", mechanism]
    return "\n".join(parts) + "\n"


def _catalogue(tmp_path: Path, files: dict[str, str]) -> Path:
    """Разложить файлы правил так, как лежит настоящий каталог."""
    rules_dir = tmp_path / "rules" / "ru"
    rules_dir.mkdir(parents=True)
    for name, text in files.items():
        (rules_dir / name).write_text(text, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Признак принятия — наличие следа
# ---------------------------------------------------------------------------


def test_rule_with_a_trace_here_is_included(generator: ModuleType, tmp_path: Path) -> None:
    """Правило со ссылкой на этот проект попадает в указатель само."""
    catalogue = _catalogue(
        tmp_path, {"001-что-то.md": _rule("ArtVsMark/Stepik-Python-Grader#1265, #1233")}
    )

    rules = generator.collect_rules(catalogue)

    assert len(rules) == 1
    assert rules[0].issues == (1233, 1265), "номера собраны оба, включая краткую форму"


def test_rule_about_another_project_is_skipped(generator: ModuleType, tmp_path: Path) -> None:
    """След на чужой репозиторий — правило есть, но действует не здесь."""
    catalogue = _catalogue(tmp_path, {"005-чужое.md": _rule("ArtVsMark/ArtVsMark#7, #8")})

    assert generator.collect_rules(catalogue) == []


def test_trace_to_a_file_counts_as_well(generator: ModuleType, tmp_path: Path) -> None:
    """След бывает не только issue: ссылка на файл этого репозитория — тоже след."""
    catalogue = _catalogue(tmp_path, {"011-файл.md": _rule("`docs/agent/preflight.md`")})

    rules = generator.collect_rules(catalogue)

    assert len(rules) == 1
    assert rules[0].paths == ("docs/agent/preflight.md",)


# ---------------------------------------------------------------------------
# Гейт, не нашедший предмета, обязан падать
# ---------------------------------------------------------------------------


def test_trace_into_nowhere_is_a_failure(generator: ModuleType, tmp_path: Path) -> None:
    """Файла из следа больше нет — отказ, а не предупреждение.

    Это сигнал, что предмет правила изменился, а правило осталось. Зелёный
    генератор на таком входе означал бы, что указатель описывает несуществующее.
    """
    catalogue = _catalogue(tmp_path, {"012-мимо.md": _rule("`scripts/удалённый_скрипт.py`")})

    with pytest.raises(ValueError, match="в никуда"):
        generator.collect_rules(catalogue)


def test_empty_catalogue_is_a_failure(generator: ModuleType, tmp_path: Path) -> None:
    """Правил не нашлось — отказ: гейт не зеленеет на пустом входе."""
    with pytest.raises(FileNotFoundError, match="строить не из чего"):
        generator.collect_rules(tmp_path)


def test_missing_catalogue_names_the_clone_command(generator: ModuleType, tmp_path: Path) -> None:
    """Отказ называет, что делать: каталог лежит в другом репозитории."""
    with pytest.raises(FileNotFoundError, match="git clone"):
        generator.collect_rules(tmp_path / "нет-такого")


# ---------------------------------------------------------------------------
# Колонка «чем держится» — главная, и она не догадывается
# ---------------------------------------------------------------------------


def test_mechanism_comes_from_its_own_section(generator: ModuleType, tmp_path: Path) -> None:
    catalogue = _catalogue(
        tmp_path,
        {
            "020-гейт.md": _rule(
                "ArtVsMark/Stepik-Python-Grader#1", mechanism="Проверяет гейт preflight.py."
            ),
            "021-шаг.md": _rule(
                "ArtVsMark/Stepik-Python-Grader#2", mechanism="Строка в чек-лист перед PR."
            ),
        },
    )

    rules = {rule.slug: rule.mechanism for rule in generator.collect_rules(catalogue)}

    assert rules["020-гейт"] == "гейт"
    assert rules["021-шаг"] == "шаг процесса"


def test_gate_word_in_the_incident_does_not_count(generator: ModuleType, tmp_path: Path) -> None:
    """Слово «гейт» в описании инцидента не делает правило обеспеченным.

    Иначе метрика «не обеспечено ничем» врала бы в приятную сторону — а она
    существует ровно затем, чтобы показывать необеспеченные.
    """
    text = _rule("ArtVsMark/Stepik-Python-Grader#3").replace(
        "Что-то сломалось.", "Гейт в CI тогда ещё не падал, и preflight молчал."
    )
    catalogue = _catalogue(tmp_path, {"022-соблазн.md": text})

    assert generator.collect_rules(catalogue)[0].mechanism == "не объявлено"


# ---------------------------------------------------------------------------
# Итоговый текст: число необеспеченных видно, а не растворено
# ---------------------------------------------------------------------------


def test_index_shows_the_unmechanised_count(generator: ModuleType, tmp_path: Path) -> None:
    catalogue = _catalogue(
        tmp_path,
        {
            "030-раз.md": _rule("ArtVsMark/Stepik-Python-Grader#1"),
            "031-два.md": _rule("ArtVsMark/Stepik-Python-Grader#2"),
            "032-три.md": _rule("ArtVsMark/Stepik-Python-Grader#3", mechanism="Гейт в CI."),
        },
    )

    text = generator.render_index(generator.collect_rules(catalogue))

    assert "**Не объявлено: 2.**" in text
    assert "Всего правил, действующих здесь: **3**." in text


def test_index_warns_against_hand_editing(generator: ModuleType, tmp_path: Path) -> None:
    """Шапка говорит, что файл генерируется, — иначе его начнут править руками."""
    catalogue = _catalogue(tmp_path, {"040-раз.md": _rule("ArtVsMark/Stepik-Python-Grader#1")})

    text = generator.render_index(generator.collect_rules(catalogue))

    assert text.startswith(generator.GENERATED_HEADER)


def test_check_mode_reports_drift(generator: ModuleType, tmp_path: Path) -> None:
    """`--check` отвечает отказом, когда указатель разошёлся с каталогом."""
    catalogue = _catalogue(tmp_path, {"050-раз.md": _rule("ArtVsMark/Stepik-Python-Grader#1")})
    output = tmp_path / "README.md"
    output.write_text("устаревший текст\n", encoding="utf-8")

    code = generator.main(
        ["--catalogue", str(catalogue), "--output", str(output), "--check"],
    )

    assert code == 1


def test_check_mode_passes_on_a_fresh_index(generator: ModuleType, tmp_path: Path) -> None:
    catalogue = _catalogue(tmp_path, {"051-раз.md": _rule("ArtVsMark/Stepik-Python-Grader#1")})
    output = tmp_path / "README.md"

    assert generator.main(["--catalogue", str(catalogue), "--output", str(output)]) == 0
    assert generator.main(["--catalogue", str(catalogue), "--output", str(output), "--check"]) == 0
