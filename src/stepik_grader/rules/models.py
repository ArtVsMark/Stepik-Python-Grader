"""models.py — модель карточки правила PEP 8 (issue #345, эпик #342).

Архитектурный слой: Domain (leaf — только stdlib, не импортирует project-код).

``RuleCard`` — карточка правила стиля (код pycodestyle/pyflakes вроде ``E501``/
``F401``), на которую ссылается найденное в коде ученика нарушение (раздел
«Правила/PEP», `docs/audit-2026-07.md` § 9.4). Карточка учит «духу» PEP 8:
объясняет **почему** правило существует, а не только «что нельзя».

Намеренно **не** обобщается в единый «RefCard»-слой с ``GlossaryCard`` (решение
§ 11 аудита, правило трёх): у правил своя специфика (severity, PEP-ссылка,
bad/good примеры), а преждевременная абстракция дороже дублирования двух
похожих моделей. Симметричные ``from_dict``/``to_dict`` — как у ``GlossaryCard``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["RuleCard", "RuleSeverity", "RuleStatus"]

# Тяжесть нарушения — по духу pycodestyle: E### → error/warning, W### →
# warning, соглашения оформления — convention. На вердикт проверки НЕ влияет
# (issue #346), только сортировка/подсветка в UI.
RuleSeverity = Literal["convention", "warning", "error"]
# Жизненный цикл карточки — как у глоссария, но без exported (правила наружу
# не выгружаются): draft (наполняется) → ready (готова к показу).
RuleStatus = Literal["draft", "ready"]

_SEVERITIES: frozenset[str] = frozenset({"convention", "warning", "error"})
_STATUSES: frozenset[str] = frozenset({"draft", "ready"})


def _as_str_list(value: Any) -> list[str]:
    """Привести значение поля JSON к списку строк (терпимо к None/строке)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    raise ValueError(f"Ожидался список строк, получено: {value!r}")


@dataclass
class RuleCard:
    """Карточка правила PEP 8 / pycodestyle / pyflakes.

    Обязательные поля — ``id`` (код правила) и ``title``; остальное опционально.
    ``example_bad``/``example_good`` — короткие фрагменты «до/после», ``body`` —
    Markdown-объяснение «почему и как исправить».
    """

    id: str  # код правила, напр. "E501", "F401"
    title: str  # краткое название, напр. "Строка длиннее лимита"
    summary: str = ""  # RU-однострочник (что нарушено)
    body: str = ""  # расширенное объяснение (Markdown): почему и как исправить
    pep_url: str = ""  # ссылка на релевантный раздел peps.python.org
    docs_url: str = ""  # доп. ссылка (ruff/pycodestyle), опционально
    severity: RuleSeverity = "warning"
    status: RuleStatus = "ready"
    tags: list[str] = field(default_factory=list)
    example_bad: str = ""  # фрагмент кода-нарушения
    example_good: str = ""  # исправленный фрагмент

    @property
    def search_terms(self) -> list[str]:
        """Строки, по которым карточка находится поиском (lower-case)."""
        return [t.lower() for t in (self.id, self.title, *self.tags) if t]

    def matches(self, query: str) -> bool:
        """True, если ``query`` (подстрока, без регистра) есть в search_terms."""
        needle = query.strip().lower()
        return bool(needle) and any(needle in term for term in self.search_terms)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleCard:
        """Собрать карточку из JSON-объекта с валидацией обязательных полей.

        Raises:
            ValueError: если нет ``id``/``title`` или ``severity``/``status``
                имеют недопустимое значение.
        """
        rule_id = str(data.get("id", "")).strip()
        title = str(data.get("title", "")).strip()
        if not rule_id:
            raise ValueError("Карточка правила без обязательного поля 'id'")
        if not title:
            raise ValueError(f"Карточка '{rule_id}' без обязательного поля 'title'")

        severity = str(data.get("severity", "warning"))
        if severity not in _SEVERITIES:
            raise ValueError(
                f"Карточка '{rule_id}': недопустимый severity={severity!r} "
                f"(ожидалось одно из {sorted(_SEVERITIES)})"
            )
        status = str(data.get("status", "ready"))
        if status not in _STATUSES:
            raise ValueError(
                f"Карточка '{rule_id}': недопустимый status={status!r} "
                f"(ожидалось одно из {sorted(_STATUSES)})"
            )

        return cls(
            id=rule_id,
            title=title,
            summary=str(data.get("summary", "")),
            body=str(data.get("body", "")),
            pep_url=str(data.get("pep_url", "")),
            docs_url=str(data.get("docs_url", "")),
            severity=severity,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            tags=_as_str_list(data.get("tags")),
            example_bad=str(data.get("example_bad", "")),
            example_good=str(data.get("example_good", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать карточку в JSON-совместимый dict (стабильный порядок)."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "body": self.body,
            "pep_url": self.pep_url,
            "docs_url": self.docs_url,
            "severity": self.severity,
            "status": self.status,
            "tags": list(self.tags),
            "example_bad": self.example_bad,
            "example_good": self.example_good,
        }
