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
is refused by name, since ``who`` knows nothing about its reader).

>>> facts_problems("schema: acquaint/facts/v1\\nfacts:\\n  name.full: {value: Ada, label: clear, source: operator, recorded: 2026-11-09}\\n", today="2026-11-10")
[]
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from acquaint.records import load_yaml, source_problem
from acquaint.store import Entity
from acquaint.trust import LABELS

__all__ = [
    "FACTS_FILE",
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


def _iso_date(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return False
    return len(str(value)) == 10


def facts(entity: Entity) -> dict[str, dict]:
    """The entity's facts by name; ``{}`` when there is no file or it does not parse (lint reports that)."""
    data, errors = load_yaml(entity.text(FACTS_FILE))
    found = data.get("facts") if isinstance(data, Mapping) and not errors else None
    return {
        str(name): dict(fact)
        for name, fact in (found.items() if isinstance(found, Mapping) else [])
        if isinstance(fact, Mapping)
    }


def facts_problems(text: str, *, today: str) -> list[tuple[str, str, str]]:
    """``(severity, rule, message)`` for one ``facts.yaml``: shape, label, source, dates, a passed ``review_by``."""
    data, errors = load_yaml(text)
    if errors:
        return [("error", "unparseable", e) for e in errors]
    if not isinstance(data, Mapping):
        return [("error", "facts-shape", "facts.yaml must be a mapping with a `facts:` key")]
    found: list[tuple[str, str, str]] = []
    if data.get("schema") != SCHEMA:
        found.append(("error", "facts-shape", f"schema must be {SCHEMA}"))
    listed = data.get("facts")
    if not isinstance(listed, Mapping):
        return [*found, ("error", "facts-shape", "`facts:` must map field names to facts")]
    if "to_confirm" in data and not (
        isinstance(data["to_confirm"], list) and all(isinstance(x, str) for x in data["to_confirm"])
    ):
        found.append(("error", "facts-shape", "to_confirm must be a list of text items"))
    for name, fact in listed.items():
        where = f"fact {name!s}"
        if not _NAME_RE.fullmatch(str(name)):
            found.append(("error", "facts-shape", f"{where}: a field name is dotted words like titles.current"))
        if not isinstance(fact, Mapping):
            found.append(("error", "facts-shape", f"{where} must be a mapping with value, label, source, recorded"))
            continue
        if fact.get("value") in (None, "", []):
            found.append(("error", "facts-shape", f"{where} has no value"))
        if fact.get("label") not in LABELS:
            found.append(("error", "facts-label", f"{where}: label {fact.get('label')!r} is not one of {', '.join(LABELS)}"))
        source = fact.get("source")
        if not source or source_problem(f"[source: {source}]"):
            found.append(("error", "facts-unsourced", f"{where} has no usable source (a date alone is not one)"))
        for field in _DATE_FIELDS:
            if fact.get(field) is not None and not _iso_date(fact[field]):
                found.append(("error", "facts-date", f"{where}: {field} must be YYYY-MM-DD, not {fact[field]!r}"))
        if fact.get("recorded") is None:
            found.append(("error", "facts-date", f"{where} has no recorded date"))
        if fact.get("status") is not None and fact["status"] not in STATUSES:
            found.append(("error", "facts-shape", f"{where}: status {fact['status']!r} is not one of {', '.join(STATUSES)}"))
        review = fact.get("review_by")
        if review is not None and _iso_date(review) and str(review) < today:
            found.append(("warning", "facts-review", f"{where} was due for review on {review}"))
    return found


def _shown(fact: Mapping) -> bool:
    return fact.get("label") in OPEN_LABELS and str(fact.get("status") or "current") == "current"


def brief_lines(entity: Entity, *, max_chars: int) -> list[str]:
    """``- name: value`` for each current ``clear`` fact, within ``max_chars``; what does not fit is counted, not cut mid-line."""
    lines: list[str] = []
    used = 0
    rest = 0
    for name, fact in sorted(facts(entity).items()):
        if not _shown(fact):
            continue
        line = f"- {name}: {' '.join(str(fact.get('value')).split())}"
        if used + len(line) + 1 > max_chars:
            rest += 1
            continue
        lines.append(line)
        used += len(line) + 1
    if rest:
        lines.append(f"- (+{rest} more: `acquaint who {entity.slug} -f <name>`)")
    return lines


def fact_value(entity: Entity, name: str) -> tuple[Any, str | None]:
    """``(value, refusal)`` of a fact by dotted name: the value when it is a current ``clear`` fact, else ``None`` and why not (or ``None, None`` when there is no such fact)."""
    fact = facts(entity).get(name)
    if fact is None:
        return None, None
    if not _shown(fact):
        label, status = fact.get("label"), fact.get("status") or "current"
        why = f"status {status}" if status != "current" else f"label {label}"
        return None, f"{entity.slug} has {name}, but it is {why}: not readable without knowing the reader"
    return fact.get("value"), None
