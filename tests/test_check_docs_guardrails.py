"""Tests for scripts/check_docs_guardrails.py — docs guardrails (issue #173).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_version_consistency.py.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_docs_guardrails.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_docs_guardrails", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_passes_on_current_repo() -> None:
    """На актуальном main нарушений быть не должно — main() возвращает 0."""
    assert _load_module().main() == 0


def test_cli_exits_zero() -> None:
    """`python scripts/check_docs_guardrails.py` завершается 0."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_readme_over_budget_is_flagged(monkeypatch) -> None:
    """README больше лимита → ошибка бюджета."""
    module = _load_module()
    errors: list[str] = []
    overflow = "\n".join(str(i) for i in range(module.README_LINE_BUDGET + 5))
    monkeypatch.setattr(module.Path, "read_text", lambda self, encoding="utf-8": overflow)
    module.check_readme_budget(errors)
    assert any("exceed the budget" in e for e in errors), errors


def test_readme_within_budget_passes(monkeypatch) -> None:
    """README в пределах лимита → без ошибок."""
    module = _load_module()
    errors: list[str] = []
    ok = "\n".join(str(i) for i in range(module.README_LINE_BUDGET - 1))
    monkeypatch.setattr(module.Path, "read_text", lambda self, encoding="utf-8": ok)
    module.check_readme_budget(errors)
    assert errors == []


def test_github_slug_keeps_underscore_and_cyrillic() -> None:
    """Slug сохраняет подчёркивание/кириллицу, выкидывает точки/бэктики/эм-дэш."""
    module = _load_module()
    assert module.github_slug("`stepik_config.json` — корневая папка задач") == (
        "stepik_configjson--корневая-папка-задач"
    )
    assert module.github_slug("Ограничения и безопасность") == "ограничения-и-безопасность"


def test_broken_file_link_is_flagged(tmp_path, monkeypatch) -> None:
    """Ссылка на несуществующий файл → ошибка."""
    module = _load_module()
    (tmp_path / "a.md").write_text("[gone](missing.md)\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    monkeypatch.setattr(module, "collect_markdown_files", lambda: [tmp_path / "a.md"])
    errors: list[str] = []
    module.check_markdown_links(errors)
    assert any("broken link" in e for e in errors), errors


def test_broken_anchor_is_flagged(tmp_path, monkeypatch) -> None:
    """Ссылка на несуществующий якорь в существующем файле → ошибка."""
    module = _load_module()
    (tmp_path / "a.md").write_text("[x](b.md#nope)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Real Heading\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    monkeypatch.setattr(
        module, "collect_markdown_files", lambda: [tmp_path / "a.md", tmp_path / "b.md"]
    )
    errors: list[str] = []
    module.check_markdown_links(errors)
    assert any("broken anchor" in e for e in errors), errors


def test_valid_anchor_and_external_link_pass(tmp_path, monkeypatch) -> None:
    """Валидный якорь и внешняя ссылка не порождают ошибок."""
    module = _load_module()
    (tmp_path / "a.md").write_text(
        "[ok](b.md#real-heading)\n[web](https://example.com)\n", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text("# Real Heading\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    monkeypatch.setattr(
        module, "collect_markdown_files", lambda: [tmp_path / "a.md", tmp_path / "b.md"]
    )
    errors: list[str] = []
    module.check_markdown_links(errors)
    assert errors == []


def test_anchor_in_code_fence_is_ignored(tmp_path, monkeypatch) -> None:
    """Заголовок-подобная строка внутри ``` не создаёт якорь."""
    module = _load_module()
    slugs = module._heading_slugs
    (tmp_path / "b.md").write_text("```\n# not a heading\n```\n# Real\n", encoding="utf-8")
    result = slugs(tmp_path / "b.md")
    assert result == {"real"}


def test_docs_index_complete_on_current_repo() -> None:
    """На актуальном main каждый docs/*.md упомянут в docs/README.md."""
    module = _load_module()
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert errors == []


def test_unindexed_docs_file_is_flagged(tmp_path, monkeypatch) -> None:
    """Файл docs/*.md, не упомянутый в docs/README.md, → ошибка (issue #300)."""
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("[installation](installation.md)\n", encoding="utf-8")
    (docs / "installation.md").write_text("# Installation\n", encoding="utf-8")
    (docs / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert any("orphan.md" in e for e in errors), errors


def test_name_masked_by_longer_name_is_flagged(tmp_path, monkeypatch) -> None:
    """issue #788 приёмка: имя-хвост другого имени больше не «покрывает» файл.

    Подстрочная проверка засчитывала `api.md` как проиндексированный, если в
    индексе упомянут `web-api.md`. Такая пара в репозитории реальная —
    `audit-2026-07-15.md` целиком входит в `issue-audit-2026-07-15.md`, — то
    есть выпадение документа из навигации guard бы не заметил, ровно тот
    регресс, ради которого он написан.
    """
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("[web api](web-api.md)\n", encoding="utf-8")
    (docs / "web-api.md").write_text("# Web API\n", encoding="utf-8")
    (docs / "api.md").write_text("# API\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert any("'api.md'" in e for e in errors), errors
    assert not any("'web-api.md'" in e for e in errors), errors


def test_mention_without_link_is_flagged_with_hint(tmp_path, monkeypatch) -> None:
    """Упоминание в бэктиках — не индексация: из навигации по нему не перейти."""
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("Смотри `setup.md` где-то рядом.\n", encoding="utf-8")
    (docs / "setup.md").write_text("# Setup\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert len(errors) == 1
    assert "not as a link" in errors[0], errors  # подсказка отличает случай от «нет вовсе»


def test_link_forms_accepted(tmp_path, monkeypatch) -> None:
    """Формы ссылок, встречающиеся в индексах: с `./`, с якорем, в угловых скобках."""
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(
        "[a](./a.md) · [b](b.md#section) · [c](<c.md>)\n", encoding="utf-8"
    )
    for name in ("a.md", "b.md", "c.md"):
        (docs / name).write_text("# Doc\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert errors == []


def test_subdir_file_checked_against_its_own_index(tmp_path, monkeypatch) -> None:
    """Рекурсия #562: docs/dev/adr/*.md проверяется против adr/README.md (не против
    docs/README.md); упомянутый в своём индексе файл → без ошибок."""
    module = _load_module()
    docs = tmp_path / "docs"
    (docs / "adr").mkdir(parents=True)
    (docs / "README.md").write_text("[ADR index](adr/README.md)\n", encoding="utf-8")
    (docs / "adr" / "README.md").write_text(
        "# ADR index\n\n[0001](0001-decision.md)\n", encoding="utf-8"
    )
    (docs / "adr" / "0001-decision.md").write_text("# Decision\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert errors == []


def test_unindexed_subdir_file_is_flagged(tmp_path, monkeypatch) -> None:
    """Рекурсия #562: файл в подкаталоге с собственным README, не упомянутый в
    этом README, → ошибка (полнота индекса на КАЖДОМ уровне)."""
    module = _load_module()
    docs = tmp_path / "docs"
    (docs / "archive").mkdir(parents=True)
    (docs / "README.md").write_text("[archive](archive/README.md)\n", encoding="utf-8")
    (docs / "archive" / "README.md").write_text("# Archive index\n", encoding="utf-8")
    (docs / "archive" / "old-audit.md").write_text("# Old\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert any("old-audit.md" in e for e in errors), errors


def test_subdir_without_readme_not_recursed(tmp_path, monkeypatch) -> None:
    """Подкаталог БЕЗ собственного README отдельно не индексируется — его файлы
    каталогизируются родителем (role-*.md-приложения к сводному аудиту, #562)."""
    module = _load_module()
    docs = tmp_path / "docs"
    (docs / "archive" / "appendix").mkdir(parents=True)
    (docs / "README.md").write_text("[archive](archive/README.md)\n", encoding="utf-8")
    (docs / "archive" / "README.md").write_text("# Archive\n", encoding="utf-8")
    (docs / "archive" / "appendix" / "role-x.md").write_text("# Role\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_index_completeness(errors)
    assert errors == []  # appendix/ без README → рекурсия его не трогает


def test_changelog_within_version_budget_passes(tmp_path, monkeypatch) -> None:
    """[Unreleased] + 3 версии + до-версионные записи → без ошибок (issue #373)."""
    module = _load_module()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        "## [1.8.0] - 2026-07-14\n\n## [1.7.0] - 2026-07-12\n\n## [1.6.0] - 2026-07-08\n\n"
        "## [unreleased] / 2026-06-25 — до-версионная запись из архива\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_changelog_version_budget(errors)
    assert errors == []


def test_changelog_over_version_budget_is_flagged(tmp_path, monkeypatch) -> None:
    """Четыре версионных релиза в живом CHANGELOG.md → ошибка (issue #373)."""
    module = _load_module()
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        "## [1.8.0] - 2026-07-14\n\n## [1.7.0] - 2026-07-12\n\n"
        "## [1.6.0] - 2026-07-08\n\n## [1.5.0] - 2026-07-06\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_changelog_version_budget(errors)
    assert any("versioned releases exceed the budget" in e for e in errors), errors


def test_changelog_version_budget_on_current_repo() -> None:
    """На актуальном main живой CHANGELOG.md в пределах лимита версий (issue #373)."""
    module = _load_module()
    errors: list[str] = []
    module.check_changelog_version_budget(errors)
    assert errors == []


def _make_directions(root: Path) -> Path:
    """Собрать минимальный docs/ с четырьмя направлениями и их индексами."""
    docs = root / "docs"
    for direction in ("use", "dev", "agent", "audit", "archive"):
        (docs / direction).mkdir(parents=True)
        (docs / direction / "README.md").write_text(f"# {direction}\n", encoding="utf-8")
    (docs / "README.md").write_text(
        "\n".join(f"[{d}]({d}/README.md)" for d in ("use", "dev", "agent", "archive")),
        encoding="utf-8",
    )
    return docs


def test_directions_layout_passes(tmp_path, monkeypatch) -> None:
    """Корень docs/ с одним README.md и четырьмя индексами направлений → без ошибок."""
    module = _load_module()
    _make_directions(tmp_path)
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_directions(errors)
    assert errors == []


def test_stray_doc_in_docs_root_is_flagged(tmp_path, monkeypatch) -> None:
    """Новый .md прямо в корне docs/ → ошибка: он не попал ни в одно направление."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "server-mode.md").write_text("# Server mode\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_directions(errors)
    assert any("server-mode.md" in e and "docs/ root" in e for e in errors), errors


def test_missing_direction_index_is_flagged(tmp_path, monkeypatch) -> None:
    """Направление без своего README.md-индекса → ошибка."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "dev" / "README.md").unlink()
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_docs_directions(errors)
    assert any("docs/dev/" in e for e in errors), errors


def test_directions_on_current_repo() -> None:
    """На актуальном main раскладка по направлениям соблюдена."""
    module = _load_module()
    errors: list[str] = []
    module.check_docs_directions(errors)
    assert errors == []


def test_issue_tail_in_explanatory_doc_is_flagged(tmp_path, monkeypatch) -> None:
    """Ссылка на issue в объясняющем документе → ошибка политики."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "use" / "configuration.md").write_text(
        "# Конфигурация\n\nКлюч `timeout` добавлен в issue #123.\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_issue_tail_policy(errors)
    assert any("configuration.md" in e and "#123" in e for e in errors), errors


def test_issue_tail_allowed_in_changelog_and_archive(tmp_path, monkeypatch) -> None:
    """CHANGELOG.md и docs/archive/ — логи: номера там уместны и не флагаются."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n- fix (#123)\n", encoding="utf-8")
    (docs / "archive" / "history.md").write_text("# История\n\n#124, #125\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_issue_tail_policy(errors)
    assert errors == [], errors


def test_issue_tail_budget_in_design_zone(tmp_path, monkeypatch) -> None:
    """docs/dev/design/ держит номера-требования, но не сверх бюджета."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "dev" / "design").mkdir()
    (docs / "dev" / "design" / "server-mode.md").write_text(
        "# Server mode\n\n" + " ".join(f"#{200 + i}" for i in range(40)), encoding="utf-8"
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_issue_tail_policy(errors)
    assert any("docs/dev/design/" in e and "budget" in e for e in errors), errors


def test_internal_anchor_is_not_an_issue_tail(tmp_path, monkeypatch) -> None:
    """`#заголовок` — внутренний якорь, а не ссылка на задачу: не флагается."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "use" / "grader-workflow.md").write_text(
        "# Работа\n\nСм. [раздел](#режимы-работы) и `#!/usr/bin/env python`.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_issue_tail_policy(errors)
    assert errors == [], errors


def test_issue_tail_policy_on_current_repo() -> None:
    """На актуальном main политика соблюдена."""
    module = _load_module()
    errors: list[str] = []
    module.check_issue_tail_policy(errors)
    assert errors == []


# --- UI-strings issue policy (issue #820) ------------------------------------


def _make_ui_tree(tmp_path: Path, options_py: str, locale: dict[str, str]) -> None:
    """Минимальный срез репозитория с cli/options.py и core/locales/ru.json."""
    import json

    pkg = tmp_path / "src" / "stepik_grader"
    (pkg / "cli").mkdir(parents=True)
    (pkg / "core" / "locales").mkdir(parents=True)
    (pkg / "cli" / "options.py").write_text(options_py, encoding="utf-8")
    (pkg / "core" / "locales" / "ru.json").write_text(
        json.dumps(locale, ensure_ascii=False), encoding="utf-8"
    )


def test_ui_help_with_issue_tail_is_flagged(tmp_path, monkeypatch) -> None:
    """`help=` с хвостом «Issue #51» — нарушение: пользователь читает справку."""
    module = _load_module()
    _make_ui_tree(
        tmp_path,
        'parser.add_argument("--lang", help="Язык меню. Issue #51 D-01.")\n',
        {"ok": "Файл не найден"},
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_ui_issue_tail_policy(errors)
    assert any("options.py" in e and "#51" in e for e in errors), errors


def test_ui_locale_value_with_issue_tail_is_flagged(tmp_path, monkeypatch) -> None:
    """Номер задачи в тексте локали — то же нарушение, что и в справке."""
    module = _load_module()
    _make_ui_tree(
        tmp_path,
        'parser.add_argument("--lang", help="Язык меню.")\n',
        {"path_not_found": "Путь не найден (issue #261)"},
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_ui_issue_tail_policy(errors)
    assert any("ru.json" in e and "#261" in e for e in errors), errors


def test_ui_strings_read_implicitly_concatenated_help(tmp_path, monkeypatch) -> None:
    """Многострочный `help=(...)` читается целиком, а не первым куском."""
    module = _load_module()
    _make_ui_tree(
        tmp_path,
        'parser.add_argument("--watch", help=("Перезапускать при изменении. " "Issue #54."))\n',
        {"ok": "Готово"},
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    strings = module.collect_ui_strings()
    assert strings["src/stepik_grader/cli/options.py"] == [
        "Перезапускать при изменении. Issue #54."
    ]


def test_ui_clean_strings_pass(tmp_path, monkeypatch) -> None:
    """Справка и локаль без номеров задач — без ошибок."""
    module = _load_module()
    _make_ui_tree(
        tmp_path,
        'parser.add_argument("--lang", help="Язык меню и сообщений (по умолчанию ru).")\n',
        {"path_not_found": "Путь не найден"},
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_ui_issue_tail_policy(errors)
    assert errors == []


def test_ui_issue_tail_policy_on_current_repo() -> None:
    """На актуальном main справка и локали держат ноль."""
    module = _load_module()
    errors: list[str] = []
    module.check_ui_issue_tail_policy(errors)
    assert errors == []


def test_ui_guard_fails_when_options_py_moved(tmp_path, monkeypatch) -> None:
    """Пропавший вход — ошибка, а не тихий пропуск проверки (issue #988).

    Прежде оба источника брались «если найдутся», поэтому переезд `options.py`
    обнулял гейт: он печатал «0 строк проверено» тем же тоном, что и «всё
    чисто». Guard, зеленеющий на пустом входе, неотличим от отсутствующего.
    """
    module = _load_module()
    _make_ui_tree(tmp_path, 'parser.add_argument("--lang", help="Язык меню.")\n', {"ok": "Готово"})
    (tmp_path / "src" / "stepik_grader" / "cli" / "options.py").unlink()
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []

    module.check_ui_issue_tail_policy(errors)

    assert any("options.py" in error for error in errors), errors


def test_ui_guard_fails_when_locales_are_gone(tmp_path, monkeypatch) -> None:
    """Каталог локалей опустел — политика по сообщениям не проверена, это ошибка."""
    module = _load_module()
    _make_ui_tree(tmp_path, 'parser.add_argument("--lang", help="Язык меню.")\n', {"ok": "Готово"})
    for locale in (tmp_path / "src" / "stepik_grader" / "core" / "locales").glob("*.json"):
        locale.unlink()
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []

    module.check_ui_issue_tail_policy(errors)

    assert any("locales" in error for error in errors), errors


# --- Подписи-пути у ссылок (issue #827) --------------------------------------


def test_stale_path_caption_is_flagged(tmp_path, monkeypatch) -> None:
    """Подпись ведёт на путь, которого нет: цель рабочая, а текст врёт."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "use" / "configuration.md").write_text("# Конфигурация\n", encoding="utf-8")
    (tmp_path / "SECURITY.md").write_text(
        "См. [docs/configuration.md § Безопасность](docs/use/configuration.md).\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_link_captions(errors)
    assert any("docs/configuration.md" in e for e in errors), errors


def test_shortened_caption_is_allowed(tmp_path, monkeypatch) -> None:
    """Сокращённая подпись — «хвост» реального пути — не нарушение."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "use" / "web-interface.md").write_text("# Веб\n", encoding="utf-8")
    (docs / "dev" / "web-contracts.md").write_text(
        "См. [use/web-interface.md § Разделы](../use/web-interface.md).\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_link_captions(errors)
    assert errors == []


def test_plain_text_caption_is_not_checked(tmp_path, monkeypatch) -> None:
    """Обычная человеческая подпись под правило не подпадает."""
    module = _load_module()
    docs = _make_directions(tmp_path)
    (docs / "use" / "configuration.md").write_text("# Конфигурация\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "См. [конфигурацию](docs/use/configuration.md).\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_link_captions(errors)
    assert errors == []


def test_link_captions_on_current_repo() -> None:
    """На актуальном main подписи-пути совпадают с целями."""
    module = _load_module()
    errors: list[str] = []
    module.check_link_captions(errors)
    assert errors == []


# --- Метрики числом в витринах (issue #829) ----------------------------------


def test_hardcoded_metric_in_readme_is_flagged(tmp_path, monkeypatch) -> None:
    """«2100+ tests» в прозе — нарушение: живой источник числа это бейдж."""
    module = _load_module()
    (tmp_path / "README.md").write_text(
        "- ✅ **2100+ automated tests** (pytest), CI on 3 OSes.\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_showcase_metrics(errors)
    assert any("README.md" in e for e in errors), errors


def test_russian_metric_is_flagged_too(tmp_path, monkeypatch) -> None:
    """Правило одинаково для обеих витрин и обоих языков."""
    module = _load_module()
    (tmp_path / "README.en.md").write_text("Глоссарий: 1349 карточек.\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_showcase_metrics(errors)
    assert any("README.en.md" in e for e in errors), errors


def test_badge_line_is_not_flagged(tmp_path, monkeypatch) -> None:
    """Сам бейдж — источник истины, а не хардкод."""
    module = _load_module()
    (tmp_path / "README.md").write_text(
        "[![Coverage](https://img.shields.io/endpoint?url=...4200+tests)](ci)\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []
    module.check_showcase_metrics(errors)
    assert errors == []


def test_showcase_metrics_on_current_repo() -> None:
    """На актуальном main обе витрины держат метрики только в бейджах."""
    module = _load_module()
    errors: list[str] = []
    module.check_showcase_metrics(errors)
    assert errors == []


# ---------------------------------------------------------------------------
# issue #832 — readme пакета обязан ссылаться абсолютно, а свои же абсолютные
# ссылки не должны выпадать из гейтов ссылок и подписей.
# ---------------------------------------------------------------------------


def test_pypi_readme_check_passes_on_repo() -> None:
    errors: list[str] = []
    _load_module().check_pypi_readme_is_absolute(errors)
    assert errors == []


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (
            "https://github.com/ArtVsMark/Stepik-Python-Grader/blob/main/docs/README.md",
            "docs/README.md",
        ),
        (
            "https://raw.githubusercontent.com/ArtVsMark/Stepik-Python-Grader/main/docs/assets/a.gif",
            "docs/assets/a.gif",
        ),
        ("https://example.com/docs/README.md", None),
        ("docs/README.md", None),
        ("https://github.com/other/repo/blob/main/docs/README.md", None),
    ],
    ids=["blob", "raw", "foreign-host", "already-relative", "foreign-repo"],
)
def test_self_repo_links_resolve_back_to_paths(link: str, expected: str | None) -> None:
    """Без разворота обратно README выпал бы из проверок целей и подписей."""
    assert _load_module()._as_repo_path(link) == expected


def test_relative_link_in_pypi_readme_is_reported(tmp_path, monkeypatch) -> None:
    """Гейт ловит возврат относительного пути — иначе страница снова сломается."""
    (tmp_path / "pyproject.toml").write_text('readme = "R.md"\n', encoding="utf-8")
    (tmp_path / "R.md").write_text(
        "[док](docs/README.md)\n![скрин](docs/assets/a.png)\n[внешняя](https://example.com)\n",
        encoding="utf-8",
    )
    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", tmp_path)

    errors: list[str] = []
    module.check_pypi_readme_is_absolute(errors)

    assert len(errors) == 1
    assert "docs/README.md" in errors[0]
    assert "docs/assets/a.png" in errors[0]
    assert "example.com" not in errors[0]  # внешние ссылки не в претензии


def test_anchor_only_link_is_not_reported_as_relative(tmp_path, monkeypatch) -> None:
    """`#якорь` — не путь: на PyPI он никуда не резолвится и ломаться нечему."""
    (tmp_path / "pyproject.toml").write_text('readme = "R.md"\n', encoding="utf-8")
    (tmp_path / "R.md").write_text("[к разделу](#установка)\n", encoding="utf-8")
    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", tmp_path)

    errors: list[str] = []
    module.check_pypi_readme_is_absolute(errors)
    assert errors == []


def test_violations_are_printable_on_a_single_byte_console(tmp_path) -> None:
    """Гейт печатает НАРУШЕНИЕ даже под cp1252, а не падает вместо него.

    Реальная поломка (#832): новая success-строка на кириллице уронила три
    Windows-job'а ``UnicodeEncodeError`` — гейт вернул 1, подменив настоящую
    причину отказа своей собственной. Success-строки с тех пор английские,
    поэтому проверяем то, что осталось русским и печатается на Windows в самый
    неподходящий момент: текст самого нарушения.
    """
    (tmp_path / "pyproject.toml").write_text('readme = "R.md"\n', encoding="utf-8")
    (tmp_path / "R.md").write_text("[док](docs/README.md)\n", encoding="utf-8")
    snippet = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('g', r'{_SCRIPT}')\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['g'] = m\n"
        "spec.loader.exec_module(m)\n"
        "m._force_utf8_stdout()\n"
        f"m._ROOT = m.Path(r'{tmp_path}')\n"
        "errors = []\n"
        "m.check_pypi_readme_is_absolute(errors)\n"
        "[print(f'  - {e}') for e in errors]\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
    )
    assert "UnicodeEncodeError" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    assert "docs/README.md" in result.stdout


def test_nested_badge_links_are_checked_for_relative_targets(tmp_path, monkeypatch) -> None:
    """Цель внутри бейдж-ссылки тоже обязана быть абсолютной (issue #832/#835).

    Реальный промах: `[![Glossary](img)](docs/dev/glossary.md)` — подпись сама
    содержит картинку, и шаблон `[^\\]]*` такую ссылку не видел. Две относительные
    цели уцелели в README при переводе на абсолютные, а гейт рапортовал «чисто».
    """
    (tmp_path / "pyproject.toml").write_text('readme = "R.md"\n', encoding="utf-8")
    (tmp_path / "R.md").write_text(
        "[![Glossary](https://img.shields.io/x.json)](docs/dev/glossary.md)\n", encoding="utf-8"
    )
    module = _load_module()
    monkeypatch.setattr(module, "_ROOT", tmp_path)

    errors: list[str] = []
    module.check_pypi_readme_is_absolute(errors)

    assert len(errors) == 1
    assert "docs/dev/glossary.md" in errors[0]


def test_conflict_markers_are_flagged(tmp_path, monkeypatch) -> None:
    """Незаконченное слияние в документации — нарушение (#1164).

    Такое уже уезжало в main и жило там несколько PR: ссылки резолвились,
    бюджет строк соблюдался, индексы были полны — а раздел про лаунчер читатель
    видел дважды, вперемешку с «HEAD» и «origin/main».
    """
    module = _load_module()
    opening, middle, closing = "<" * 7, "=" * 7, ">" * 7
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        f"Абзац\n{opening} HEAD\nмоя версия\n{middle}\nчужая версия\n{closing} origin/main\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []

    module.check_no_conflict_markers(errors)

    assert len(errors) == 1
    assert "docs/guide.md" in errors[0].replace(os.sep, "/")
    assert "строка 2" in errors[0]


def test_setext_heading_underline_is_not_a_conflict(tmp_path, monkeypatch) -> None:
    """Подчёркивание заголовка «=======» — валидный Markdown, а не конфликт.

    Поэтому средний маркер сам по себе ничего не значит: ловим только открытие
    и закрытие, у которых после знаков идёт имя ревизии.
    """
    module = _load_module()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("Заголовок\n=======\n\nТекст\n", encoding="utf-8")
    monkeypatch.setattr(module, "_ROOT", tmp_path)
    errors: list[str] = []

    module.check_no_conflict_markers(errors)

    assert errors == []


def test_conflict_marker_check_passes_on_repo() -> None:
    """На актуальном репозитории маркеров нет."""
    errors: list[str] = []

    _load_module().check_no_conflict_markers(errors)

    assert errors == []
