"""Тесты scripts/generate_rules_index.py — указатель правил (issue #1342).

Указатель генерируется, а не ведётся: список, который поддерживают руками,
отстаёт с первого же нового правила — молча, как уже было с числами в витрине и
со списками открытых задач. Поэтому проверяется не «красиво ли», а три
свойства: правило со следом сюда попадает само, без следа — не попадает вовсе,
а след в никуда роняет генератор.
"""

from __future__ import annotations

import importlib.util
import json
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


def _bindings(root: Path, rules: dict[str, dict[str, str]]) -> None:
    """Ответ проекта каталогу — источник поля «чем держится» (issue #1351)."""
    target = root / ".rules" / "bindings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "1.0", "project": "x/y", "catalogue": "https://e", "rules": rules}
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_mechanism_comes_from_the_project_answer(generator: ModuleType, tmp_path: Path) -> None:
    """Уровень берётся из `.rules/bindings.json`, а не из раздела каталога.

    Поле принадлежит потребителю: одно правило в проекте с полным конвейером
    держится гейтом, в витрине — шагом сборки, в статическом сайте ничем.
    Пока источником был каталог, у 88 правил из 89 читалось «не объявлено» —
    поле пустовало не потому, что гейтов нет, а потому что заведено не в том
    репозитории.
    """
    catalogue = _catalogue(
        tmp_path / "cat",
        {
            "020-гейт.md": _rule("ArtVsMark/Stepik-Python-Grader#1"),
            "021-шаг.md": _rule("ArtVsMark/Stepik-Python-Grader#2"),
        },
    )
    root = tmp_path / "repo"
    root.mkdir()
    _bindings(
        root,
        {
            "020": {"status": "active", "mechanism": "gate", "where": "scripts/x.py"},
            "021": {"status": "active", "mechanism": "document", "where": "CONTRIBUTING.md"},
        },
    )

    rules = {r.slug: r.mechanism for r in generator.collect_rules(catalogue, repo_root=root)}

    assert rules["020-гейт"] == "гейт"
    assert rules["021-шаг"] == "документ"


def test_without_an_answer_everything_is_undeclared(generator: ModuleType, tmp_path: Path) -> None:
    """Нет ответа — «не объявлено»: приятная ошибка здесь хуже отсутствия метрики."""
    catalogue = _catalogue(
        tmp_path / "cat",
        {"020-гейт.md": _rule("ArtVsMark/Stepik-Python-Grader#1", mechanism="Гейт в CI.")},
    )
    root = tmp_path / "repo"
    root.mkdir()

    rules = {r.slug: r.mechanism for r in generator.collect_rules(catalogue, repo_root=root)}

    assert rules["020-гейт"] == "не объявлено", (
        "раздел «Механизм» каталога больше не источник: он говорит о чужом проекте"
    )


def test_answered_not_here_is_not_counted_as_undeclared(
    generator: ModuleType, tmp_path: Path
) -> None:
    """«Решено, что не про нас» и «руки не дошли» — разные состояния.

    У обоих след ведёт сюда, но `not-applicable` и `rejected` несут причину по
    контракту каталога, то есть решение принято. Пока такой ответ показывался
    как «не объявлено», он стоял в метрике «очередь на автоматизацию», которая
    объявлена обязанной уменьшаться, — и уменьшить её было нечем: строить гейт
    для предмета, которого здесь нет, не из чего (правило 154).
    """
    catalogue = _catalogue(
        tmp_path / "cat",
        {
            "020-гейт.md": _rule("ArtVsMark/Stepik-Python-Grader#1"),
            "079-срок.md": _rule("ArtVsMark/Stepik-Python-Grader#2"),
            "080-отклонено.md": _rule("ArtVsMark/Stepik-Python-Grader#3"),
        },
    )
    root = tmp_path / "repo"
    root.mkdir()
    _bindings(
        root,
        {
            "020": {"status": "active", "mechanism": "gate", "where": "scripts/x.py"},
            "079": {"status": "not-applicable", "why": "сроков от постановки здесь нет"},
            "080": {"status": "rejected", "why": "решение иное и записано"},
        },
    )

    rules = generator.collect_rules(catalogue, repo_root=root)

    assert {rule.slug for rule in rules} == {"020-гейт"}, (
        "отвеченное «здесь не действует» перечисляется среди действующих"
    )
    assert all(rule.mechanism != "не объявлено" for rule in rules), (
        "ответ с причиной попал в очередь на автоматизацию, которую нечем закрыть"
    )


def test_answered_not_here_still_needs_a_live_trail(generator: ModuleType, tmp_path: Path) -> None:
    """Пропуск не глушит отказ «след ведёт в никуда».

    Иначе ответом `not-applicable` можно было бы спрятать правило, чей предмет
    исчез, — а это ровно тот сигнал, ради которого генератор падает.
    """
    catalogue = _catalogue(tmp_path / "cat", {"079-срок.md": _rule("`scripts/нет_такого.py`")})
    root = tmp_path / "repo"
    root.mkdir()
    _bindings(root, {"079": {"status": "not-applicable", "why": "предмета здесь нет"}})

    with pytest.raises(ValueError, match="в никуда"):
        generator.collect_rules(catalogue, repo_root=root)


def test_gate_word_in_the_incident_does_not_count(generator: ModuleType, tmp_path: Path) -> None:
    """Слово «гейт» в описании инцидента не делает правило обеспеченным.

    Иначе метрика «не обеспечено ничем» врала бы в приятную сторону — а она
    существует ровно затем, чтобы показывать необеспеченные.
    """
    text = _rule("ArtVsMark/Stepik-Python-Grader#3").replace(
        "Что-то сломалось.", "Гейт в CI тогда ещё не падал, и preflight молчал."
    )
    catalogue = _catalogue(tmp_path / "cat", {"022-соблазн.md": text})
    # Ответ проекта берётся из пустого дерева намеренно: предмет теста —
    # слово «гейт» в прозе каталога, а не то, что этот номер значит у нас.
    # Без этого тест краснел бы от чужой правки `.rules/bindings.json`.
    root = tmp_path / "repo"
    root.mkdir()

    rules = generator.collect_rules(catalogue, repo_root=root)

    assert rules[0].mechanism == "не объявлено"


# ---------------------------------------------------------------------------
# Итоговый текст: число необеспеченных видно, а не растворено
# ---------------------------------------------------------------------------


def test_index_shows_the_unmechanised_count(generator: ModuleType, tmp_path: Path) -> None:
    catalogue = _catalogue(
        tmp_path / "cat",
        {
            "030-раз.md": _rule("ArtVsMark/Stepik-Python-Grader#1"),
            "031-два.md": _rule("ArtVsMark/Stepik-Python-Grader#2"),
            "032-три.md": _rule("ArtVsMark/Stepik-Python-Grader#3", mechanism="Гейт в CI."),
        },
    )

    root = tmp_path / "repo"
    root.mkdir()
    _bindings(root, {"032": {"status": "active", "mechanism": "gate", "where": "scripts/x.py"}})

    text = generator.render_index(generator.collect_rules(catalogue, repo_root=root))

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


class TestOwnershipIsHandedOverInsideAParagraph:
    """След правила, родившегося здесь, кончается словами «а у каталога — вот чем».

    Владельца абзаца задаёт первый названный репозиторий, и для такого следа это
    мы. Но путь после передачи владения принадлежит уже не нам, и считать его
    своим значит искать в дереве файл, которого тут быть не должно.

    Прецедент — правило 181: оно вышло отсюда, поэтому абзац наш, а последняя
    фраза про каталог. Указатель не находил `scripts/check_exclusive.py` и
    отказывался пересобираться ЦЕЛИКОМ — гейт, краснеющий на верном ответе.
    """

    def test_a_path_after_the_handover_is_not_ours(self, generator: ModuleType) -> None:
        """Путь после «У каталога —» в свои не берётся."""
        trace = (
            "`ArtVsMark/Stepik-Python-Grader` — `scripts/preflight.py`, строки 845–846; "
            "сверено по HEAD. У каталога — `scripts/check_exclusive.py`."
        )

        paths = generator._our_paths(trace)

        assert "scripts/preflight.py" in paths
        assert "scripts/check_exclusive.py" not in paths

    def test_our_own_paths_before_the_handover_survive(self, generator: ModuleType) -> None:
        """Передача владения не должна съедать то, что стоит до неё."""
        trace = (
            "`ArtVsMark/Stepik-Python-Grader` — `scripts/check_sources_of_truth.py` и "
            "`tests/test_check_sources_of_truth.py`. У каталога — `scripts/check_exclusive.py`."
        )

        paths = generator._our_paths(trace)

        assert "scripts/check_sources_of_truth.py" in paths
        assert "tests/test_check_sources_of_truth.py" in paths
        assert "scripts/check_exclusive.py" not in paths

    def test_a_paragraph_without_a_handover_is_unchanged(self, generator: ModuleType) -> None:
        """Нет передачи — поведение прежнее, все пути наши."""
        trace = (
            "`ArtVsMark/Stepik-Python-Grader` — `scripts/preflight.py`, `docs/agent/preflight.md`."
        )

        paths = generator._our_paths(trace)

        assert set(paths) == {"scripts/preflight.py", "docs/agent/preflight.md"}
