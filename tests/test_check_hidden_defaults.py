"""Тесты гейта скрытых умолчаний (issue #1417, правила каталога 165 и 176).

Гейт проверяется тем, что он обязан **отвергнуть** (правило 140), и подделка
воспроизводит **вероятный** случай, а не удобный: имя на языке, на котором
ведётся проект. Первая проба такого гейта у соседа была сделана файлом с
латинским именем — он находился, и проверка выглядела рабочей.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "check_hidden_defaults.py"
_ROOT = pathlib.Path(__file__).parent.parent


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_hidden_defaults", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()


def _findings(source: str, kind: str) -> list[str]:
    """Находки указанного рода на переданном исходнике."""
    tree = ast.parse(source)
    path = _ROOT / "scripts" / "образец.py"
    if kind == "encoding":
        return _MODULE.encoding_findings(path, tree)
    return _MODULE.git_list_findings(path, tree)


# --- состояние репозитория ------------------------------------------------------


def test_repository_is_clean() -> None:
    """Приёмка: в дереве нет умолчаний, взятых из окружения."""
    assert _MODULE.main([]) == 0


def test_coverage_is_named_not_implied(capsys: pytest.CaptureFixture[str]) -> None:
    """Правило 165, вторая половина: охват называется числом.

    «Чисто» без числа означает и «ничего не нашли», и «ничего не смотрели», и
    различить их читателю нечем.
    """
    _MODULE.main([])

    out = capsys.readouterr().out
    assert "разобрано исходников" in out
    assert any(character.isdigit() for character in out)


def test_scan_covers_tests_too() -> None:
    """Подделка, читающая чужой вывод не в той кодировке, врёт так же, как код."""
    scanned = {path.relative_to(_ROOT).parts[0] for path in _MODULE.scanned_files()}

    assert scanned == {"src", "scripts", "tests"}


# --- правило 176: кодировка ------------------------------------------------------


@pytest.mark.parametrize(
    "keyword",
    ["text=True", "universal_newlines=True", 'errors="replace"'],
)
def test_text_mode_without_encoding_is_rejected(keyword: str) -> None:
    """Любой из трёх ключей включает текстовый режим — и все три без кодировки.

    ``errors="replace"`` опаснее прочих: он выглядит предусмотрительностью, а
    кодировку при этом оставляет локальной.
    """
    source = f'import subprocess\n\nsubprocess.run(["git", "log"], {keyword})\n'

    assert _findings(source, "encoding"), keyword


def test_explicit_encoding_passes() -> None:
    """Кодировка задана — находки нет."""
    source = 'import subprocess\n\nsubprocess.run(["git", "log"], text=True, encoding="utf-8")\n'

    assert _findings(source, "encoding") == []


def test_binary_mode_is_not_a_finding() -> None:
    """Без текстового режима предмета нет: байты декодирует вызывающий сам."""
    source = 'import subprocess\n\nsubprocess.run(["git", "log"], capture_output=True)\n'

    assert _findings(source, "encoding") == []


def test_all_subprocess_entry_points_are_covered() -> None:
    """Проверяются все вызовы с текстовым режимом, а не один ``run``."""
    for name in _MODULE.SUBPROCESS_CALLS:
        source = f'import subprocess\n\nsubprocess.{name}(["git", "log"], text=True)\n'

        assert _findings(source, "encoding"), name


# --- правило 165: списки путей ---------------------------------------------------


@pytest.mark.parametrize("command", ["ls-files", "--name-only", "--porcelain"])
def test_git_path_list_without_nul_is_rejected(command: str) -> None:
    """Список путей без ``-z`` отвергается — независимо от подкоманды."""
    source = f'subprocess.run(["git", "diff", "{command}"], text=True, encoding="utf-8")\n'

    assert _findings(source, "git"), command


def test_a_string_list_that_is_not_a_git_call_is_ignored() -> None:
    """Предмет — вызов именно git, а не всякий список строк.

    Без этого условия гейт краснел на собственном `parametrize`, где имена
    подкоманд стоят как данные теста.
    """
    source = 'pytest.mark.parametrize("c", ["ls-files", "--name-only"])\n'

    assert _findings(source, "git") == []


def test_nul_separated_list_passes() -> None:
    """``-z`` на месте — находки нет."""
    source = 'subprocess.run(["git", "ls-files", "-z"], text=True, encoding="utf-8")\n'

    assert _findings(source, "git") == []


def test_git_text_output_is_not_a_path_list() -> None:
    """``git log`` отдаёт текст, а не пути: правило его не касается."""
    source = 'subprocess.run(["git", "log", "--oneline"], text=True, encoding="utf-8")\n'

    assert _findings(source, "git") == []


def test_a_wrapper_that_adds_nul_is_not_a_finding() -> None:
    """Гейт не краснеет на собственной починке.

    Обёртка добавляет ``-z`` внутри себя, поэтому в месте вызова его нет.
    Признак берётся из дерева, а не из соглашения об именах.
    """
    source = (
        'def git_paths(git, *args):\n    return git(*args, "-z").split("\\0")\n\n'
        'git_paths(_git, "ls-files")\n'
    )

    assert _findings(source, "git") == []


def test_wrapper_detection_needs_the_nul_flag() -> None:
    """Обёртка без ``-z`` обёрткой не считается — иначе исключение бесплатно."""
    source = (
        "def git_paths(git, *args):\n    return git(*args).splitlines()\n\n"
        'git_paths(_git, "ls-files")\n'
    )

    assert _findings(source, "git")


# --- живая проба: тот самый случай, ради которого правило заведено ----------------


def test_worktree_fingerprint_sees_a_cyrillic_filename(tmp_path: pathlib.Path) -> None:
    """Отпечаток замечает правку в файле с кириллическим именем.

    До починки не замечал: git отдаёт такое имя экранированным, ``Path`` из него
    не разрешается, чтение падает ``OSError`` и записывается как ``<missing>`` —
    то есть pre-push хук принимал состояние, которого не проверял. Проект
    ведётся по-русски, значит слепая зона пришлась на самый вероятный случай.
    """
    spec = importlib.util.spec_from_file_location(
        "_preflight_for_fingerprint", _ROOT / "scripts" / "preflight.py"
    )
    assert spec is not None and spec.loader is not None
    preflight = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = preflight
    spec.loader.exec_module(preflight)

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args],
            cwd=tmp_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip("\n\r\0")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    target = tmp_path / "утечка.py"
    target.write_text("# токен\n", encoding="utf-8")

    before = preflight.worktree_fingerprint(tmp_path, git)
    target.write_text("# совсем другое содержимое\nimport os\n", encoding="utf-8")
    after = preflight.worktree_fingerprint(tmp_path, git)

    assert before != after, (
        "отпечаток не заметил правки файла с кириллическим именем — "
        "хук принял бы пуш состояния, которого гейт не проверял"
    )
    seen, missing = preflight.fingerprint_coverage(tmp_path, git)
    assert seen >= 1 and missing == 0, (seen, missing)


def test_a_finding_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ветка отказа прогоняется, а не только объявлена.

    Прогон одного пути подтверждает, что механизм запускается, и ничего больше:
    ветка, которую никто не видел работающей, обычно и оказывается сломанной.
    """
    bad = tmp_path / "образец.py"
    bad.write_text(
        'import subprocess\n\nsubprocess.run(["git", "log"], text=True)\n', encoding="utf-8"
    )
    monkeypatch.setattr(_MODULE, "scanned_files", lambda: [bad])
    monkeypatch.setattr(_MODULE, "_ROOT", tmp_path)

    assert _MODULE.main([]) == 1
    assert "текстовый режим без encoding=" in capsys.readouterr().out


# --- правило 180: вызов разрешается по импортам файла, а не по звену имени -------


class TestCallIsResolvedThroughImports:
    """Предмет — как ``subprocess`` назван В ЭТОМ файле (правило 180 каталога).

    Прежний разбор смотрел на последнее звено имени, и обе половины ошибки
    подтверждены пробой: псевдоним прятал вызов от проверки, а свой метод с тем
    же именем объявлялся нарушением. Второе хуже — гейт, краснеющий на верном
    коде, снимают первой же правкой.
    """

    def test_an_aliased_function_is_still_found(self) -> None:
        """``from subprocess import run as r`` — псевдоним не прячет вызов."""
        source = "from subprocess import run as r\nr(['git', 'log'], text=True)\n"

        assert _findings(source, "encoding")

    def test_an_aliased_module_is_still_found(self) -> None:
        """``import subprocess as sp`` — то же для псевдонима модуля."""
        source = "import subprocess as sp\nsp.run(['git'], text=True)\n"

        assert _findings(source, "encoding")

    def test_a_plain_import_of_the_function_is_found(self) -> None:
        """``from subprocess import run`` — вызов без точки тоже предмет."""
        source = "from subprocess import run\nrun(['git'], text=True)\n"

        assert _findings(source, "encoding")

    def test_someone_elses_run_is_not_a_subprocess_call(self) -> None:
        """Свой метод ``run`` с теми же ключами нарушением не является.

        Совпадение звена не доказывает ничего: ``text=`` бывает и у чужого API.
        """
        source = (
            "class Job:\n    def run(self, cmd, text=False): ...\n\nJob().run(['x'], text=True)\n"
        )

        assert _findings(source, "encoding") == []

    def test_an_attribute_call_on_a_foreign_module_is_ignored(self) -> None:
        """``other.run(...)`` — модуль не тот, предмета нет."""
        source = "import other\nother.run(['x'], text=True)\n"

        assert _findings(source, "encoding") == []

    def test_the_names_are_taken_from_the_imports(self) -> None:
        """Guard-the-guard: разбор импортов возвращает именно ввезённые имена."""
        tree = ast.parse("import subprocess as sp\nfrom subprocess import run as r, check_output\n")

        modules, functions = _MODULE.subprocess_names(tree)

        assert modules == {"sp"}
        assert functions == {"r", "check_output"}
