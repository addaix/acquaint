"""Facts: the small exact things agents keep asking for, as a typed file on the record they belong to.

An optional ``facts.yaml`` in any entity folder holds one current value per field, each
with the disclosure label acquaint already uses, its source and its dates:

.. code-block:: yaml

    schema: acquaint/facts/v1
    facts:
      name.full: {value: "Ada Lovelace", label: clear, source: "self: \\"I write it Ada Lovelace\\"", recorded: 2026-11-09}
      titles.current:
        value: "Analyst"
        label: clear
        source: https://example.org/about
        recorded: 2026-11-09
        valid_from: 2023-01-01
        review_by: 2027-05-09
        status: current            # current | proposed | conflicting
        supersedes: titles.current@2025-02-01
    to_confirm: ["inferred time zone"]   # inferred items awaiting the operator

Field names follow JSContact (RFC 9553) where one exists (``name.full``, ``titles.*``,
``speakToAs.pronouns``, ...), so vCard or schema.org exports are mappings later. ``label``
is one of :data:`acquaint.trust.LABELS`. A ``source`` follows the rules of a preference
line (:func:`acquaint.records.source_problem`): a date alone is not a source.

What reads them: :func:`facts_problems` (``lint``), :func:`brief_lines` (the brief's
"Facts" block: ``clear`` facts that are current, never ``amber`` or ``red``) and
:func:`fact_value` (``who <id> -f name.full``, ``clear`` only: a fact with a stricter label
is refused, since ``who`` knows nothing about its reader). A fact that fails its own lint, is
duplicated, is not ``current`` or is outside its ``valid_from``/``valid_to`` is never shown.

>>> facts_problems("schema: acquaint/facts/v1\\nfacts:\\n  name.full: {value: Ada, label: clear, source: operator, recorded: 2026-11-09}\\n", today="2026-11-10")
[]
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import yaml

from acquaint.records import load_yaml, source_problem
from acquaint.store import Entity
from acquaint.trust import LABELS, is_iso_date

__all__ = [
    "FACTS_FILE",
    "OPEN_LABELS",
    "SCHEMA",
    "STATUSES",
    "brief_lines",
    "fact_value",
    "facts",
    "facts_problems",
]

FACTS_FILE = "facts.yaml"
SCHEMA = "acquaint/facts/v1"
STATUSES = ("current", "proposed", "conflicting")
#: Labels a fact may be read at without knowing who reads it.
OPEN_LABELS = ("clear",)
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(\.[A-Za-z][A-Za-z0-9]*)*")
_DATE_FIELDS = ("recorded", "valid_from", "valid_to", "review_by")
_FACT_KEYS = frozenset({"value", "label", "source", "supersedes", "status", *_DATE_FIELDS})
_TOP_KEYS = frozenset({"schema", "facts", "to_confirm"})
_SCALARS = (str, int, float, bool)


def _duplicate_names(text: str) -> set[str]:
    """Names that appear twice under ``facts:``: YAML keeps the last, which hides which one was meant."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return set()
    if not isinstance(root, yaml.MappingNode):
        return set()
    for key, value in root.value:
        if getattr(key, "value", None) == "facts" and isinstance(value, yaml.MappingNode):
            names = [getattr(k, "value", None) for k, _ in value.value]
            return {n for n in names if names.count(n) > 1}
    return set()


def _value_problem(value: Any) -> str | None:
    items = value if isinstance(value, list) else [value]
    if value in (None, "", []) or not all(isinstance(i, _SCALARS) and str(i).strip() for i in items):
        return "value must be text, a number, or a list of them"
    return None


def _fact_problems(name: str, fact: Any) -> list[tuple[str, str, str]]:
    """``(severity, rule, message)`` for one fact; a message names the field, never echoes what was written in it."""
    where = f"fact {name}"
    if not _NAME_RE.fullmatch(name):
        return [("error", "facts-shape", "a field name is dotted words like titles.current")]
    if not isinstance(fact, Mapping):
        return [("error", "facts-shape", f"{where} must be a mapping with value, label, source, recorded")]
    found: list[tuple[str, str, str]] = []
    for key in sorted(set(map(str, fact)) - _FACT_KEYS):
        found.append(("error", "facts-shape", f"{where}: unknown key {key!r} (a typo hides a field from the checks)" if _NAME_RE.fullmatch(key) else f"{where}: unknown key"))
    if problem := _value_problem(fact.get("value")):
        found.append(("error", "facts-shape", f"{where}: {problem}"))
    if fact.get("label") not in LABELS:
        found.append(("error", "facts-label", f"{where}: label must be one of {', '.join(LABELS)}"))
    source = fact.get("source")
    if not isinstance(source, str) or "]" in source or "\n" in source or source_problem(f"[source: {source}]"):
        found.append(("error", "facts-unsourced", f"{where} has no usable source (a date alone is not one)"))
    for field in _DATE_FIELDS:
        if fact.get(field) is not None and not is_iso_date(str(fact[field])):
            found.append(("error", "facts-date", f"{where}: {field} must be YYYY-MM-DD"))
    if fact.get("recorded") is None:
        found.append(("error", "facts-date", f"{where} has no recorded date"))
    if fact.get("status") is not None and fact["status"] not in STATUSES:
        found.append(("error", "facts-shape", f"{where}: status must be one of {', '.join(STATUSES)}"))
    return found


def facts_problems(text: str, *, today: str) -> list[tuple[str, str, str]]:
    """``(severity, rule, message)`` for one ``facts.yaml``: shape, label, source, dates, duplicates, a passed ``review_by``."""
    data, errors = load_yaml(text)
    if errors:
        return [("error", "unparseable", e) for e in errors]
    if not isinstance(data, Mapping):
        return [("error", "facts-shape", "facts.yaml must be a mapping with a `facts:` key")]
    found: list[tuple[str, str, str]] = []
    if data.get("schema") != SCHEMA:
        found.append(("error", "facts-shape", f"schema must be {SCHEMA}"))
    if unknown := set(map(str, data)) - _TOP_KEYS:
        found.append(("error", "facts-shape", f"unknown top-level key(s): {', '.join(sorted(unknown))}"))
    listed = data.get("facts")
    if not isinstance(listed, Mapping):
        return [*found, ("error", "facts-shape", "`facts:` must map field names to facts")]
    if "to_confirm" in data and not (
        isinstance(data["to_confirm"], list) and all(isinstance(x, str) for x in data["to_confirm"])
    ):
        found.append(("error", "facts-shape", "to_confirm must be a list of text items"))
    for name in sorted(_duplicate_names(text)):
        found.append(("error", "facts-duplicate", f"fact {name} is listed twice; keep one, and move the old value under `supersedes` or delete it"))
    for name, fact in listed.items():
        found += _fact_problems(str(name), fact)
        review = fact.get("review_by") if isinstance(fact, Mapping) else None
        if review is not None and is_iso_date(str(review)) and str(review) < today:
            found.append(("warning", "facts-review", f"fact {name} was due for review on {review}"))
    return found


def facts(entity: Entity) -> dict[str, dict]:
    """The entity's facts by name; ``{}`` when there is no file or it does not parse (lint reports that)."""
    text = entity.text(FACTS_FILE)
    data, errors = load_yaml(text)
    found = data.get("facts") if isinstance(data, Mapping) and not errors else None
    return {
        str(name): dict(fact)
        for name, fact in (found.items() if isinstance(found, Mapping) else [])
        if isinstance(fact, Mapping)
    }


def _readable(entity: Entity, today: str) -> dict[str, dict]:
    """The facts that may be shown as exact: ``clear``, free of lint errors, not a duplicate, ``current``, and inside their validity dates."""
    duplicated = _duplicate_names(entity.text(FACTS_FILE))
    found = {}
    for name, fact in facts(entity).items():
        if name in duplicated or any(sev == "error" for sev, _, _ in _fact_problems(name, fact)):
            continue
        inside = (not fact.get("valid_from") or str(fact["valid_from"]) <= today) and (
            not fact.get("valid_to") or str(fact["valid_to"]) >= today
        )
        if fact.get("label") in OPEN_LABELS and (fact.get("status") or "current") == "current" and inside:
            found[name] = fact
    return found


def _render(value: Any) -> str:
    items = value if isinstance(value, list) else [value]
    return " ".join(", ".join(map(str, items)).split())


def brief_lines(entity: Entity, *, max_chars: int, today: str | None = None) -> list[str]:
    """``- name: value`` for each readable fact, within ``max_chars`` (the overflow note included); what does not fit is counted, not cut mid-line."""
    readable = _readable(entity, today or date.today().isoformat())
    # the person's own name first: it is what a reader most needs if the budget bites
    ordered = sorted(readable.items(), key=lambda item: (not item[0].startswith("name."), item[0]))
    lines = [f"- {name}: {_render(fact['value'])}" for name, fact in ordered]
    note = lambda n: f"- (+{n} more: `acquaint who {entity.slug} -f <name>`)"  # noqa: E731
    kept, used = [], 0
    for index, line in enumerate(lines):
        left = len(lines) - index - 1
        reserve = len(note(left)) + 1 if left else 0
        if used + len(line) + 1 + reserve > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    dropped = len(lines) - len(kept)
    return [*kept, note(dropped)] if dropped else kept


def fact_value(entity: Entity, name: str, *, today: str | None = None) -> tuple[Any, str | None]:
    """``(value, refusal)`` of a fact by dotted name (any case): the value when it is readable, else ``None`` and why not (``None, None`` when there is no such fact)."""
    every = facts(entity)
    key = next((k for k in every if k.lower() == name.lower()), None)
    if key is None:
        return None, None
    readable = _readable(entity, today or date.today().isoformat())
    if key not in readable:
        return None, f"{entity.slug} has a fact of that name, but it is not readable without knowing the reader (or it is not current or valid)"
    return readable[key].get("value"), None
