"""未定義の用語参照を、フルパースに依存せず一括検出する."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import get_close_matches

from tanbun.feature.parsing.issue import ParseIssue, SourceRange
from tanbun.feature.parsing.primitive.mark import protect_escaped_braces
from tanbun.feature.parsing.tree2net.lineparse import DEF_SEPS, parse_line

RELATION_SOURCE = r"(?:<->|->|<-|ex\.|xe\.|ref\.|~=|by\.|where\.)"
RELATION_PATTERN = re.compile(rf"^{RELATION_SOURCE}\s*")
TERM_REFERENCE_PATTERN = re.compile(r"\{([^{}]+)\}")
QUOTERM_REFERENCE_PATTERN = re.compile(
    rf"^\s*(?:{RELATION_SOURCE}\s*)?`([^`]+)`\s*$",
)
FORMULA_PATTERN = re.compile(r"\$[^$]*\$")


@dataclass(frozen=True)
class TermReference:
    """用語参照とソース上の位置."""

    name: str
    source_range: SourceRange
    is_quoterm: bool


def find_unresolved_reference_issues(text: str) -> tuple[ParseIssue, ...]:
    """文書内で定義されていない参照を全て返す."""
    definitions = _defined_names(text)
    unresolved = [
        reference
        for reference in _references(text)
        if reference.name not in definitions
    ]
    counts = Counter(reference.name for reference in unresolved)
    return tuple(
        _to_issue(reference, counts[reference.name], definitions)
        for reference in unresolved
    )


def _defined_names(text: str) -> set[str]:
    definitions = set()
    for raw_line in text.splitlines():
        content = raw_line.lstrip()
        relation = RELATION_PATTERN.match(content)
        if relation is not None:
            content = content[relation.end() :]
        if not _is_definition_line(content):
            continue
        try:
            alias, names, _ = parse_line(content)
        except ValueError:
            continue
        definitions.update(_normalize_name(name) for name in names)
        if alias:
            definitions.add(alias.strip())
    return {name for name in definitions if name}


def _references(text: str) -> tuple[TermReference, ...]:
    protected = protect_escaped_braces(text)
    references = []
    for line_number, line in enumerate(protected.splitlines()):
        searchable = FORMULA_PATTERN.sub(
            lambda match: " " * len(match.group()),
            line,
        )
        for match in TERM_REFERENCE_PATTERN.finditer(searchable):
            name = match.group(1).strip()
            start, end = match.span(0)
            references.append(
                TermReference(
                    name=name,
                    source_range=SourceRange(line_number, start, end),
                    is_quoterm=False,
                ),
            )
        quoterm = QUOTERM_REFERENCE_PATTERN.match(searchable)
        if quoterm is not None:
            start, end = quoterm.span(1)
            references.append(
                TermReference(
                    name=quoterm.group(1).strip(),
                    source_range=SourceRange(line_number, start, end),
                    is_quoterm=True,
                ),
            )
    return tuple(references)


def _to_issue(
    reference: TermReference,
    count: int,
    definitions: set[str],
) -> ParseIssue:
    label = "引用用語" if reference.is_quoterm else "用語"
    frequency = f" ({count}箇所で参照)" if count > 1 else ""
    candidates = get_close_matches(
        reference.name,
        sorted(definitions),
        n=3,
        cutoff=0.4,
    )
    suggestion = f"「{reference.name}: ...」と定義"
    if candidates:
        suggestion += f"。定義済みの候補: {', '.join(candidates)}"
    return ParseIssue(
        code=("undefined-quoterm" if reference.is_quoterm else "undefined-term"),
        message=f"{label}「{reference.name}」が定義されていません{frequency}。",
        suggestion=suggestion,
        source_range=reference.source_range,
    )


def _is_definition_line(line: str) -> bool:
    if not line or line.startswith(("#", "@", "!", "`", "+++", "when.")):
        return False
    return any(separator in line for separator in DEF_SEPS) or "|" in line


def _normalize_name(name: str) -> str:
    """複合用語はパーサーのlookup keyと同じ形にする."""
    return name.replace("{", "").replace("}", "").strip()
