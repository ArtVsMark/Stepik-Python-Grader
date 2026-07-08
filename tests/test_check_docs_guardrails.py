"""Tests for scripts/check_docs_guardrails.py — docs guardrails (issue #173).

Скрипт лежит в scripts/ (не на sys.path) — грузим его как модуль по пути, тем же
приёмом, что и test_check_version_consistency.py.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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
    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True)
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
