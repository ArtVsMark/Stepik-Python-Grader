#!/usr/bin/env python3
"""scripts/check_attribution.py — атрибуция проверяется до мержа (issue #1343).

**Что случилось.** Изменение уехало в ``main`` с трейлером соавторства,
подставленным платформой: при squash-мерже GitHub добавил
``Co-authored-by: Claude <noreply@anthropic.com>`` вместо согласованной строки
``Claude Opus 5 <noreply@anthropic.com>``. Автор при этом человек, как и требует
``CLAUDE.md`` § Формат коммитов; сломалось только соавторство — и один и тот же
соавтор оказался в истории под двумя именами.

**Откуда берётся вторая строка.** Squash не переносит коммит, а составляет
новый: автором становится автор pull request, а прежних авторов коммитов ветки
платформа дописывает трейлерами ``Co-authored-by``. Берёт она их из полей
``author``/``committer`` коммитов — то есть из **git-идентичности окна**, в
котором работали. У облачного контейнера она ``Claude <noreply@anthropic.com>``,
и это не та строка, о которой договаривались.

Отсюда главное свойство этой проверки: смотреть надо **не на трейлеры в теле**
(их платформа допишет сама), а на авторов коммитов ветки — именно они станут
трейлерами итогового коммита. Это известно до слияния, а после — необратимо:
``main`` защищена, force-push запрещён.

**Почему гейт, а не памятка.** Неверная атрибуция ничего не ломает и никого не
будит: сборка зелёная, код верный. Обнаруживается глазами и случайно — обычно
когда таких коммитов накопилось несколько.

Запуск::

    python scripts/check_attribution.py --audit-main   # сколько уже испорчено
    python scripts/check_attribution.py --check-branch # авторы коммитов ветки
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tomllib

__all__ = [
    "AGREED_SETTINGS",
    "Identity",
    "agreed_identities",
    "audit_history",
    "branch_identities",
    "is_agent",
    "main",
    "mismatched",
    "owner_identity",
    "parse_identity",
    "trailer_block",
    "trailer_identities",
]

_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Согласованные строки авторства живут здесь и правятся здесь же — харнесс
#: подставляет их сам, а гейт читает оттуда же, чтобы список был один.
AGREED_SETTINGS = _ROOT / ".claude" / "settings.json"

_IDENTITY_RE = re.compile(r"^\s*(?P<name>[^<]+?)\s*<(?P<email>[^>]+)>\s*$")
_TRAILER_RE = re.compile(r"^\s*co-authored-by:\s*(?P<value>.+?)\s*$", re.IGNORECASE)

#: Разделители `git log --format`: пригодны для машинного разбора и не
#: встречаются в тексте сообщений.
_FIELD = "\x1f"
_RECORD = "\x1e"


class Identity:
    """Пара «имя + почта», сравниваемая без учёта регистра почты."""

    __slots__ = ("email", "name")

    def __init__(self, name: str, email: str) -> None:
        self.name = name.strip()
        self.email = email.strip().lower()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Identity):
            return NotImplemented
        return self.name == other.name and self.email == other.email

    def __hash__(self) -> int:
        return hash((self.name, self.email))

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"

    def __repr__(self) -> str:  # pragma: no cover — диагностика в отладке
        return f"Identity({self.name!r}, {self.email!r})"


def parse_identity(raw: str) -> Identity | None:
    """Разобрать строку ``Имя <почта>``; мусор — ``None``, а не исключение.

    Строки приходят из сообщений коммитов, то есть из рук человека: половина
    отклонений — лишние пробелы и отсутствующие угловые скобки. Падать на них
    нельзя, иначе гейт превращается в источник ложных отказов.
    """
    match = _IDENTITY_RE.match(raw)
    if match is None:
        return None
    return Identity(match.group("name"), match.group("email"))


def agreed_identities(settings: pathlib.Path = AGREED_SETTINGS) -> set[Identity]:
    """Согласованные строки соавторства из ``.claude/settings.json``.

    Читаются из того же ключа ``attribution.commit``, который харнесс
    подставляет в коммиты: два источника означали бы один устаревший.
    """
    try:
        raw = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    block = raw.get("attribution") if isinstance(raw, dict) else None
    commit = block.get("commit") if isinstance(block, dict) else None
    if not isinstance(commit, str):
        return set()
    found: set[Identity] = set()
    for line in commit.splitlines():
        match = _TRAILER_RE.match(line)
        value = match.group("value") if match else line
        identity = parse_identity(value)
        if identity is not None:
            found.add(identity)
    return found


def owner_identity(pyproject: pathlib.Path | None = None) -> Identity | None:
    """Владелец проекта из ``[project].authors`` — он всегда согласован.

    Имя берётся из ``pyproject.toml``, а не из git-идентичности окна: у
    локальной машины, облачного контейнера и CI она разная (то же основание,
    что у проверки «автор участвует в коммитах» в ``preflight.py``).
    """
    path = pyproject if pyproject is not None else _ROOT / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    authors = data.get("project", {}).get("authors") or []
    for author in authors:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name", "")).strip()
        email = str(author.get("email", "")).strip()
        if name and email:
            return Identity(name, email)
    return None


#: Строка вида ``Ключ: значение`` — из таких целиком состоит хвостовой блок.
_TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")


def trailer_block(message: str) -> list[str]:
    """Хвостовой блок сообщения: последний абзац из строк ``Ключ: значение``.

    Правило 156 каталога. Трейлер — это НЕ «строка, начинающаяся с имени
    трейлера»: разбор по такому образцу принимает за директиву прозаическое
    упоминание, и тем чаще, чем подробнее написано сообщение. У нас подробные
    сообщения — норма, а `CLAUDE.md` содержит образец строки соавторства
    дословно, то есть попасть в тело коммита ему ничего не мешает.

    Опаснее направление, в котором ошибался `preflight.py`: там проза
    **удовлетворяла** проверку «владелец участвует в коммите», то есть гейт
    зеленел на коммите без настоящего трейлера.

    Блоком считается последний абзац сообщения, если КАЖДАЯ его строка — пара
    ``Ключ: значение``. Иначе трейлеров нет вовсе.
    """
    paragraphs = [block for block in message.strip().split("\n\n") if block.strip()]
    if not paragraphs:
        return []
    lines = [line for line in paragraphs[-1].splitlines() if line.strip()]
    if not lines or not all(_TRAILER_LINE_RE.match(line.strip()) for line in lines):
        return []
    return lines


def trailer_identities(message: str) -> set[Identity]:
    """Личности из трейлеров ``Co-authored-by`` — из хвостового блока."""
    found: set[Identity] = set()
    for line in trailer_block(message):
        match = _TRAILER_RE.match(line)
        if match is None:
            continue
        identity = parse_identity(match.group("value"))
        if identity is not None:
            found.add(identity)
    return found


def is_agent(identity: Identity) -> bool:
    """Похожа ли подпись на агентскую — Claude в любом написании.

    Нужно, чтобы отделить **наш** дефект от чужого вклада. Внешний
    контрибьютор в согласованный список не входит и входить не должен: его
    соавторство законно, и требовать от него нашей строки — то же самое, что
    требовать русского текста в PR (см. ``CLAUDE.md`` § Язык артефактов).
    А вот агент, подписавшийся не тем именем, — ровно тот случай, ради
    которого гейт и написан.
    """
    haystack = f"{identity.name} {identity.email}".lower()
    return "claude" in haystack or "anthropic" in haystack


def mismatched(
    identities: set[Identity],
    *,
    agreed: set[Identity] | None = None,
    owner: Identity | None = None,
    agents_only: bool = False,
) -> list[Identity]:
    """Личности, которых нет в согласованном списке — отсортированные.

    Сверяется пара целиком, а не почта: расхождение, из-за которого всё
    затевалось, было именно в **имени** при совпадающей почте
    (``Claude`` против ``Claude Opus 5``).

    ``agents_only`` оставляет только агентские подписи — режим ревизии
    истории, где чужие имена принадлежат внешним участникам и дефектом не
    являются. На своей ветке сверка строгая: там внешним взяться неоткуда.
    """
    allowed = set(agreed if agreed is not None else agreed_identities())
    known_owner = owner if owner is not None else owner_identity()
    if known_owner is not None:
        allowed.add(known_owner)
    unknown = identities - allowed
    if agents_only:
        unknown = {identity for identity in unknown if is_agent(identity)}
    return sorted(unknown, key=str)


def _git(*args: str, cwd: pathlib.Path | None = None) -> str:
    """``git`` с текстовым выводом; отказ — пустая строка, а не исключение."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd or _ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return ""
    return done.stdout if done.returncode == 0 else ""


def branch_identities(base: str = "origin/main", head: str = "HEAD") -> set[Identity]:
    """Кем подписаны коммиты ветки — то, что станет трейлерами после squash.

    Проверять надо именно это: трейлеры в теле PR платформа при squash
    дописывает сама, из авторов коммитов. Значит расхождение видно до слияния —
    а после слияния уже необратимо.
    """
    log = _git("log", f"--format=%an{_FIELD}%ae{_RECORD}", f"{base}..{head}")
    found: set[Identity] = set()
    for record in log.split(_RECORD):
        if _FIELD not in record:
            continue
        name, email = record.strip().split(_FIELD, 1)
        if name and email:
            found.add(Identity(name, email))
    return found


def audit_history(ref: str = "origin/main", limit: int = 0) -> list[tuple[str, Identity]]:
    """Коммиты ``ref``, несущие несогласованную **агентскую** подпись.

    Чинить задним числом нельзя — ``main`` защищена. Но знать масштаб надо:
    иначе через полгода никто не вспомнит, где граница между «до правила» и
    «после».

    Внешние соавторы в счёт не идут: их строки законны, и попадание в этот
    список сделало бы число бессмысленным (см. :func:`is_agent`).
    """
    args = ["log", f"--format=%h{_FIELD}%B{_RECORD}"]
    if limit:
        args.append(f"-{limit}")
    args.append(ref)
    log = _git(*args)
    agreed = agreed_identities()
    owner = owner_identity()
    spoiled: list[tuple[str, Identity]] = []
    for record in log.split(_RECORD):
        if _FIELD not in record:
            continue
        sha, message = record.strip().split(_FIELD, 1)
        wrong = mismatched(
            trailer_identities(message), agreed=agreed, owner=owner, agents_only=True
        )
        spoiled.extend((sha, identity) for identity in wrong)
    return spoiled


def _force_utf8_stdout() -> None:
    """Печатать UTF-8 независимо от кодовой страницы консоли."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    """0 — согласовано; 1 — найдено расхождение."""
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="python scripts/check_attribution.py",
        description="Сверить авторство с согласованными строками до мержа.",
    )
    parser.add_argument(
        "--check-branch",
        action="store_true",
        help="авторы коммитов ветки — они станут трейлерами итогового коммита",
    )
    parser.add_argument("--audit-main", action="store_true", help="сколько уже испорчено в main")
    parser.add_argument("--base", default="origin/main", help="база сравнения для --check-branch")
    parser.add_argument("--ref", default="origin/main", help="ветка ревизии для --audit-main")
    parser.add_argument("--limit", type=int, default=0, help="сколько коммитов смотреть (0 — все)")
    args = parser.parse_args(argv)

    if not args.check_branch and not args.audit_main:
        args.check_branch = True

    agreed = agreed_identities()
    owner = owner_identity()
    if not agreed and owner is None:
        print("согласованных строк авторства не нашлось — сверять не с чем")
        return 1

    failed = False
    if args.check_branch:
        wrong = mismatched(branch_identities(args.base), agreed=agreed, owner=owner)
        if wrong:
            failed = True
            print("Авторы коммитов ветки не входят в согласованный список:")
            for identity in wrong:
                print(f"  — {identity}")
            print(
                "\nПосле squash платформа впишет их трейлерами в main, и переписать это "
                "будет нечем. Согласованные строки:"
            )
            for identity in sorted(agreed, key=str):
                print(f"  — {identity}")
            print(
                '\nЧинится идентичностью окна ДО коммита: git config user.name "…" '
                'и git config user.email "…"'
            )
        else:
            print("авторы коммитов ветки согласованы")

    if args.audit_main:
        spoiled = audit_history(args.ref, args.limit)
        if spoiled:
            print(f"\nВ {args.ref} с несогласованным трейлером: {len(spoiled)} коммит(ов)")
            for sha, identity in spoiled[:20]:
                print(f"  {sha}  {identity}")
            if len(spoiled) > 20:
                print(f"  … и ещё {len(spoiled) - 20}")
            print("Чинить задним числом нечем — это отправная точка, а не задача.")
        else:
            print(f"\nв {args.ref} несогласованных трейлеров нет")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
