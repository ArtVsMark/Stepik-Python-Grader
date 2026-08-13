"""Tests for scripts/check_locale_guardrails.py — locale catalog guardrails
(issue #264).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_docs_guardrails.py.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_locale_guardrails.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_locale_guardrails", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main нарушений быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_locale_guardrails.py` завершается 0."""
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# issue #787 (CIG-02): нулевой вход = ошибка, а не успех
#
# Guard, которому нечего проверять, обязан падать: пустой результат неотличим
# от «всё чисто», и переезд каталога обнулил бы защиту молча.
# ---------------------------------------------------------------------------


def test_zero_source_files_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Каталог пакета переехал → guard падает, а не рапортует «0 файлов, всё ок»."""
    module = _load_module()
    monkeypatch.setattr(module, "_PKG_DIR", tmp_path / "no-such-package")

    errors: list[str] = []
    module.check_ru_covers_referenced_ids(errors)
    assert errors, "нулевой вход прошёл молча"
    assert "проверять нечего" in errors[0]


def test_zero_referenced_ids_is_an_error(monkeypatch, tmp_path: Path) -> None:
    """Файлы есть, а вызовов каталога нет — проверка ослепла, это тоже ошибка."""
    module = _load_module()
    pkg_dir = tmp_path / "stepik_grader"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(module, "_PKG_DIR", pkg_dir)

    errors: list[str] = []
    module.check_ru_covers_referenced_ids(errors)
    assert errors, "файл без единого message_id прошёл как успех"


def test_source_files_walks_subpackages(monkeypatch, tmp_path: Path) -> None:
    """Обход рекурсивный: файл в подпакете виден (прежний glob его пропускал)."""
    module = _load_module()
    pkg_dir = tmp_path / "stepik_grader"
    (pkg_dir / "web" / "nested").mkdir(parents=True)
    (pkg_dir / "web" / "nested" / "deep.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(module, "_PKG_DIR", pkg_dir)

    assert [p.name for p in module.source_files()] == ["deep.py"]


def test_collect_referenced_message_ids_finds_render_message_and_message_fields(
    tmp_path: Path,
) -> None:
    module = _load_module()
    src = tmp_path / "sample.py"
    src.write_text(
        "from stepik_grader.web.i18n import message_fields, render_message\n"
        "\n"
        "def f(lang):\n"
        "    a = message_fields('some_key', lang, path='x')\n"
        "    b = render_message('other_key', lang)\n"
        "    return a, b\n",
        encoding="utf-8",
    )
    assert module.collect_referenced_message_ids(src) == {"some_key", "other_key"}


def test_collect_referenced_message_ids_ignores_dynamic_first_arg(tmp_path: Path) -> None:
    """Не строковый литерал первым аргументом — не считается (см. docstring функции)."""
    module = _load_module()
    src = tmp_path / "sample.py"
    src.write_text(
        "def f(key, lang):\n    return render_message(key, lang)\n",
        encoding="utf-8",
    )
    assert module.collect_referenced_message_ids(src) == set()


def test_ru_missing_referenced_key_is_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    pkg_dir = tmp_path / "stepik_grader"
    # issue #787: обход стал рекурсивным — кладём файл в ПОДпакет, чтобы тест
    # заодно сторожил это: прежний нерекурсивный glob такой вызов не увидел бы.
    (pkg_dir / "web").mkdir(parents=True)
    (pkg_dir / "web" / "mod.py").write_text(
        "render_message('missing_in_ru', 'ru')\n", encoding="utf-8"
    )
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps({}), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(module, "_PKG_DIR", pkg_dir)
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_ru_covers_referenced_ids(errors)
    assert any("missing_in_ru" in e for e in errors), errors


def test_en_ru_key_mismatch_is_flagged(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps({"a": "1", "b": "2"}), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps({"a": "1"}), encoding="utf-8")
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_en_ru_key_parity(errors)
    assert any("b" in e for e in errors), errors


def test_matching_keys_pass(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps({"a": "1"}), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps({"a": "hi"}), encoding="utf-8")
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_en_ru_key_parity(errors)
    assert errors == []


def test_load_locale_keys_missing_file_returns_empty_set(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_LOCALES_DIR", tmp_path)
    assert module.load_locale_keys("xx") == set()


# ---------------------------------------------------------------------------
# issue #821 (DESC-03): в английской локали не должно быть кириллицы
#
# Паритет ключей этого не ловит: ключ есть в обоих файлах, а перевода нет.
# ---------------------------------------------------------------------------


def test_cyrillic_in_en_locale_is_flagged(monkeypatch, tmp_path: Path) -> None:
    """Русский текст внутри en.json — нарушение, с указанием ключа."""
    module = _load_module()
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(
        json.dumps({"greeting": "Привет"}, ensure_ascii=False), encoding="utf-8"
    )
    (locales_dir / "en.json").write_text(
        json.dumps({"greeting": "Hello, раздел «Подучить»"}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_en_locale_has_no_cyrillic(errors)
    assert any("greeting" in e for e in errors), errors


def test_clean_en_locale_passes(monkeypatch, tmp_path: Path) -> None:
    """Полностью английская локаль проверку проходит."""
    module = _load_module()
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(
        json.dumps({"greeting": "Привет"}, ensure_ascii=False), encoding="utf-8"
    )
    (locales_dir / "en.json").write_text(json.dumps({"greeting": "Hello"}), encoding="utf-8")
    monkeypatch.setattr(module, "_LOCALES_DIR", locales_dir)

    errors: list[str] = []
    module.check_en_locale_has_no_cyrillic(errors)
    assert errors == []


# ---------------------------------------------------------------------------
# issue #1005 (INS-5-02, LINK-1-05, READER-1-04): ссылки ведут наружу
#
# Строки звали читать `docs/use/installation.md` и `README` — то есть файлы,
# которых у поставившего через pip/pipx на диске нет вовсе. Подсказка в никуда
# хуже её отсутствия: пользователь ищет несуществующее и решает, что сломан он.
# ---------------------------------------------------------------------------


def _locales(tmp_path: Path, ru: dict[str, str], en: dict[str, str]) -> Path:
    """Подготовить пару локалей во временной папке и вернуть её путь."""
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "ru.json").write_text(json.dumps(ru, ensure_ascii=False), encoding="utf-8")
    (locales_dir / "en.json").write_text(json.dumps(en, ensure_ascii=False), encoding="utf-8")
    return locales_dir


def test_repo_relative_doc_path_is_caught(monkeypatch, tmp_path: Path) -> None:
    """Тот самый дефект: сообщение зовёт в docs/, которых при pipx нет."""
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_LOCALES_DIR",
        _locales(
            tmp_path,
            {"auth": "Подробнее об OAuth: docs/use/installation.md"},
            {"auth": "More about OAuth: docs/use/installation.md"},
        ),
    )

    errors: list[str] = []
    module.check_locales_link_outside_the_repo(errors)

    assert any("ru.json:auth" in e and "en.json:auth" in e for e in errors), errors


def test_bare_readme_mention_is_caught(monkeypatch, tmp_path: Path) -> None:
    """«см. README» — тот же тупик: файла у пользователя пакета нет."""
    module = _load_module()
    monkeypatch.setattr(
        module, "_LOCALES_DIR", _locales(tmp_path, {"k": "см. README"}, {"k": "see README"})
    )

    errors: list[str] = []
    module.check_locales_link_outside_the_repo(errors)

    assert any("README" in e for e in errors), errors


def test_absolute_url_to_the_same_document_passes(monkeypatch, tmp_path: Path) -> None:
    """Тот же путь внутри абсолютного URL — это и есть решение, не нарушение."""
    module = _load_module()
    url = "https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/use/installation.md"
    monkeypatch.setattr(
        module,
        "_LOCALES_DIR",
        _locales(tmp_path, {"auth": f"OAuth: {url}"}, {"auth": f"OAuth: {url}"}),
    )

    errors: list[str] = []
    module.check_locales_link_outside_the_repo(errors)

    assert errors == []


def test_generated_file_name_is_not_a_repo_path(monkeypatch, tmp_path: Path) -> None:
    """`grader-progress.md` продукт создаёт сам — запрет его не касается."""
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_LOCALES_DIR",
        _locales(
            tmp_path,
            {"exp": "Экспорт в grader-progress.md рядом с задачей"},
            {"exp": "Export to grader-progress.md next to the task"},
        ),
    )

    errors: list[str] = []
    module.check_locales_link_outside_the_repo(errors)

    assert errors == []


# ---------------------------------------------------------------------------
# issue #1005 (ED-2-06): один стиль кавычек в английской локали
# ---------------------------------------------------------------------------


def test_guillemets_in_en_locale_are_caught(monkeypatch, tmp_path: Path) -> None:
    """Русские «ёлочки» в en.json — то, что проверка на кириллицу пропускает."""
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_LOCALES_DIR",
        _locales(tmp_path, {"k": "Раздел «Подучить»"}, {"k": "The «Learn» section"}),
    )

    errors: list[str] = []
    module.check_en_locale_uses_one_quote_style(errors)

    assert any("k" in e for e in errors), errors


def test_english_quotes_pass(monkeypatch, tmp_path: Path) -> None:
    """Английские “лапки” — принятый стиль, претензий нет."""
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_LOCALES_DIR",
        _locales(tmp_path, {"k": "Раздел «Подучить»"}, {"k": "The “Learn” section"}),
    )

    errors: list[str] = []
    module.check_en_locale_uses_one_quote_style(errors)

    assert errors == []
