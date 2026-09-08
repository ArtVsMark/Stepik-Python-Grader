"""Тесты scripts/generate_facts.py — факты проекта машиночитаемо, для соседей.

Витрина берёт наши числа у себя: клонирует репозиторий целиком ради двух
подсчётов по ``tests/``, разбирает наш ``ci.yml`` регулярным выражением и
оценивает число проверок медианой по семи последним PR. Знание о нашем
устройстве живёт при этом в чужом репозитории — перенесём каталог, и у соседа
молча изменится число, а не сломается сборка.

Здесь проверяется обратный приём: считает издатель, читает потребитель. И
отдельно — что «не измеряли» не притворяется нулём.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "generate_facts.py"


@pytest.fixture
def facts() -> ModuleType:
    """Свежий модуль на каждый тест."""
    spec = importlib.util.spec_from_file_location("_generate_facts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(root: pathlib.Path, *, matrix: str = "") -> pathlib.Path:
    """Дерево проекта: несколько тестов и матрица прогона."""
    tests = root / "tests"
    (tests / "e2e").mkdir(parents=True)
    (tests / "test_один.py").write_text(
        "def test_a():\n    pass\n\nasync def test_b():\n    pass\n", encoding="utf-8"
    )
    (tests / "e2e" / "test_два.py").write_text("def test_c():\n    pass\n", encoding="utf-8")
    (tests / "conftest.py").write_text("def test_не_модуль():\n    pass\n", encoding="utf-8")
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        matrix
        or (
            '        os: ["ubuntu-latest"]\n'
            '        python-version: ["3.12", "3.13"]\n'
            "        include:\n"
            '          - {os: "ubuntu-latest", python-version: "3.14", experimental: true}\n'
        ),
        encoding="utf-8",
    )
    return root


def test_test_functions_are_counted_across_the_whole_tree(
    facts: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Считаются и вложенные, и async — но conftest модулем не является."""
    root = _project(tmp_path)

    assert facts.count_test_functions(root) == 4
    assert facts.count_test_modules(root) == 2


def test_matrix_versions_are_split_by_experimental(
    facts: ModuleType, tmp_path: pathlib.Path
) -> None:
    """Экспериментальных нет в правилах ветки — единственный источник матрица."""
    root = _project(tmp_path)

    assert facts.python_versions(root) == {
        "supported": ["3.12", "3.13"],
        "experimental": ["3.14"],
        "os": ["ubuntu-latest"],
    }


def test_an_unmeasured_key_is_absent_not_zero(
    facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Спросить площадку не удалось — ключа нет вовсе.

    Ноль читался бы как «проверок на PR не создаётся», то есть точной ложью.
    Тот же приём, что у `portable` в контракте каталога правил: ключа нет —
    значит не отвечали.
    """
    root = _project(tmp_path)
    monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)

    built = facts.build_facts(root)

    assert "checks_per_pr" not in built


def test_the_schema_says_what_it_versions(
    facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Номер обязан называть, ЧЕГО он версия.

    Номера разного назначения, названные одним словом, разъезжаются по чужим
    полям: сосед по каталогу правил записал версию формата выгрузки в поле
    версии ответа потребителя, и обе стороны остались формально валидными.

    Обращение к площадке подменено: предмет теста — поле, а не измерение. Без
    подмены тест молча ходил в сеть, и urllib заводил кэш в домашнем каталоге —
    сторож изоляции поймал это на свежем раннере, где каталога ещё не было.
    Локально не падало: там он давно существует, а ловится СОЗДАНИЕ.
    """
    root = _project(tmp_path)
    monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)

    built = facts.build_facts(root)

    assert built["schema"] == facts.SCHEMA
    assert "ФОРМАТА" in str(built["_"]), "файл не говорит, чего его номер версия"


def test_the_file_is_written_as_utf8_json(
    facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Потребитель читает JSON, а не текст: кириллица не должна экранироваться."""
    root = _project(tmp_path)
    monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)
    out = tmp_path / "out" / "facts.json"

    assert facts.main(["--out", str(out), "--root", str(root)]) == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["tests"] == {"functions": 4, "modules": 2}
    assert "\\u" not in out.read_text(encoding="utf-8")


class TestOperatingSystemsComeFromTheMatrix:
    """Список ОС выводится из матрицы, а не переписан рядом (issue #1448).

    Витрина показывала «3 OS», добывая число **регулярным выражением по именам
    наших джобов**: знание о нашем формате имён жило в её коде. Переименуй мы
    комбинацию — у соседа молча изменилось бы число, и не упало бы ничего.
    Ровно тот класс связанности, который убрали для тестов, версий и проверок.
    """

    def test_the_os_list_is_taken_from_the_matrix(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Три ОС в матрице — три в фактах, в том же порядке."""
        root = _project(
            tmp_path,
            matrix=(
                '        os: ["ubuntu-latest", "windows-latest", "macos-latest"]\n'
                '        python-version: ["3.12"]\n'
            ),
        )

        found = facts.python_versions(root)

        assert found["os"] == ["ubuntu-latest", "windows-latest", "macos-latest"]

    def test_a_changed_matrix_changes_the_answer(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Убрали ОС из матрицы — ответ изменился.

        Guard-the-guard против константы: список, переписанный рядом, ответил бы
        то же самое на любой матрице.
        """
        root = _project(
            tmp_path,
            matrix='        os: ["ubuntu-latest"]\n        python-version: ["3.12"]\n',
        )

        assert facts.python_versions(root)["os"] == ["ubuntu-latest"]

    def test_a_matrix_without_os_gives_an_empty_list(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Ключа в матрице нет — пусто, а не выдумано.

        Пустой список честнее догадки: «не измеряли» витрина отличит сама.
        """
        root = _project(tmp_path, matrix='        python-version: ["3.12"]\n')

        assert facts.python_versions(root)["os"] == []

    def test_the_schema_field_says_what_it_versions(
        self, facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Рядом со ``schema`` стоит ``schema_of`` — чего эта версия (правило 164).

        В экосистеме четыре разных ``schema``, и витрина уже обожглась: держала
        в своём ответе чужой номер, файл при этом оставался валиден.
        """
        root = _project(tmp_path)
        monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)

        built = facts.build_facts(root)

        assert "schema_of" in built
        assert "ЭТОГО файла" in str(built["schema_of"])


def _bindings(root: pathlib.Path, rules: dict[str, dict[str, str]]) -> pathlib.Path:
    """Ответ потребителя каталогу: правила со статусом и механизмом."""
    path = root / ".rules" / "bindings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "1.1", "rules": rules}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _coverage_xml(path: pathlib.Path, line_rate: str) -> pathlib.Path:
    """Минимальный Cobertura-отчёт: корню достаточно ``line-rate``."""
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{line_rate}"></coverage>',
        encoding="utf-8",
    )
    return path


class TestRuleBindings:
    """Доли «чем держится правило» — третий блок карточки проекта (issue #1505)."""

    def test_active_rules_are_counted_by_mechanism(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        _bindings(
            tmp_path,
            {
                "001": {"status": "active", "mechanism": "gate"},
                "002": {"status": "active", "mechanism": "gate"},
                "003": {"status": "active", "mechanism": "document"},
            },
        )

        assert facts.count_rule_bindings(tmp_path) == {
            "total": 3,
            "gate": 2,
            "document": 1,
        }

    def test_an_inactive_rule_is_counted_by_status(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """У отклонённого механизма нет по определению — вопрос без ответа.

        Дефис в статусе становится подчёркиванием: ключ читают как имя поля.
        """
        _bindings(
            tmp_path,
            {
                "001": {"status": "active", "mechanism": "gate"},
                "002": {"status": "not-applicable", "why": "предмета нет"},
                "003": {"status": "rejected", "why": "не наш случай"},
            },
        )

        counts = facts.count_rule_bindings(tmp_path)

        assert counts["not_applicable"] == 1
        assert counts["rejected"] == 1
        assert "not-applicable" not in counts

    def test_an_unknown_mechanism_is_counted_too(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Словарь механизмов ведёт каталог, и он растёт.

        Жёсткий список ключей в коде отстал бы молча: новая доля просто не
        попала бы в файл, а сумма долей продолжила бы сходиться с ``total``
        только потому, что о пропущенной никто не знает.
        """
        _bindings(
            tmp_path,
            {
                "001": {"status": "active", "mechanism": "gate"},
                "002": {"status": "active", "mechanism": "code"},
            },
        )

        assert facts.count_rule_bindings(tmp_path)["code"] == 1

    def test_an_active_rule_without_a_mechanism_is_held_by_nothing(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Действующее правило без механизма — принятое на словах."""
        _bindings(tmp_path, {"001": {"status": "active"}})

        assert facts.count_rule_bindings(tmp_path) == {"total": 1, "none": 1}

    def test_the_parts_add_up_to_the_total(self, facts: ModuleType, tmp_path: pathlib.Path) -> None:
        """Каждое правило попадает ровно в одну долю — иначе картина врёт."""
        _bindings(
            tmp_path,
            {
                "001": {"status": "active", "mechanism": "gate"},
                "002": {"status": "active", "mechanism": "pipeline"},
                "003": {"status": "not-applicable"},
                "004": {"status": "active"},
            },
        )

        counts = facts.count_rule_bindings(tmp_path)

        assert sum(value for key, value in counts.items() if key != "total") == counts["total"]

    def test_a_missing_file_is_not_zero(self, facts: ModuleType, tmp_path: pathlib.Path) -> None:
        """Нет файла — ключа не будет вовсе, а не ``total: 0``."""
        assert facts.count_rule_bindings(tmp_path) is None

    def test_the_real_bindings_are_counted(self, facts: ModuleType) -> None:
        """Guard-the-guard: собственный файл проекта читается и сходится."""
        counts = facts.count_rule_bindings(pathlib.Path(__file__).parent.parent)

        assert counts is not None
        assert counts["total"] > 0
        assert sum(value for key, value in counts.items() if key != "total") == counts["total"]


class TestCoveragePercent:
    """Число покрытия соседям — сравнивают его, а не смотрят (issue #1505)."""

    def test_the_percent_comes_from_the_report(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        assert facts.coverage_percent(_coverage_xml(tmp_path / "coverage.xml", "0.9237")) == 92.4

    def test_an_unreadable_report_is_not_zero(
        self, facts: ModuleType, tmp_path: pathlib.Path
    ) -> None:
        """Ноль читался бы как «ничего не покрыто» — точная ложь."""
        assert facts.coverage_percent(tmp_path / "нет-такого.xml") is None

    def test_the_key_is_absent_unless_the_report_is_offered(
        self, facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """Отчёт есть всегда, в том числе после деградации.

        Поэтому решает не наличие файла, а вызывающая сторона: она одна знает,
        полны ли данные. Не передали — ключа нет.
        """
        root = _project(tmp_path)
        monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)

        assert "coverage_percent" not in facts.build_facts(root)

    def test_the_offered_report_reaches_the_facts(
        self, facts: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        root = _project(tmp_path)
        monkeypatch.setattr(facts, "_checks_per_pr", lambda _root: None)

        built = facts.build_facts(
            root, coverage_xml=_coverage_xml(tmp_path / "coverage.xml", "0.883")
        )

        assert built["coverage_percent"] == 88.3
